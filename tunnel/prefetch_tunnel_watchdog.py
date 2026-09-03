#!/usr/bin/env python3
"""OpenWrt-only passive backpressure watchdog for the prefetch SSH tunnel.

Never controls playback, DSM, Drive, Xray, nginx, or cache files. No SSH keys,
remote commands, public health endpoint, or extra forwarding grants are needed.
"""
from __future__ import annotations

import argparse
import dataclasses
import fcntl
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time

VERSION = "1.0.0"
SERVICE = "jellyfin-prefetch-tunnel"
RESTART_COMMAND = [f"/etc/init.d/{SERVICE}", "restart"]
STATE_DIR = "/tmp/jellyfin-prefetch-watchdog"


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


@dataclasses.dataclass(frozen=True)
class Config:
    nas_status_url: str
    proxy_port: int = 10022
    interval_seconds: int = 30
    failure_threshold: int = 3
    min_pending_bytes: int = 262144
    min_drain_bytes_per_second: int = 524288
    cooldown_seconds: int = 600
    max_restarts_per_hour: int = 2
    nas_timeout_seconds: int = 3

    def __post_init__(self):
        if not self.nas_status_url.startswith("http://"):
            raise ValueError("nas_status_url must be the local NAS HTTP status URL")
        bounds = {"proxy_port": (1, 65535), "interval_seconds": (10, 300),
                  "failure_threshold": (3, 20), "min_pending_bytes": (65536, 16777216),
                  "min_drain_bytes_per_second": (1024, 10485760),
                  "cooldown_seconds": (600, 86400), "max_restarts_per_hour": (1, 3),
                  "nas_timeout_seconds": (1, 10)}
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in [{low}, {high}]")


@dataclasses.dataclass
class Socket:
    local: str
    remote: str
    recv_q: int
    send_q: int
    owners: list[tuple[str, int]]
    stats: str = ""


def parse_ss(output: str) -> list[Socket]:
    result = []
    for line in output.splitlines():
        match = re.match(r"^ESTAB\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s*(.*)$", line)
        if match:
            owners = [(name, int(pid)) for name, pid in re.findall(
                r'\("([^"]+)",pid=(\d+)', match[5])]
            result.append(Socket(match[3], match[4], int(match[1]), int(match[2]), owners))
        elif line[:1].isspace() and result:
            result[-1].stats += " " + line.strip()
    return result


def counter(stats: str, name: str) -> int:
    found = re.search(r"(?:^|\s)" + re.escape(name) + r":(\d+)(?:\s|$)", stats)
    if not found:
        raise ValueError(f"missing TCP counter {name}")
    return int(found[1])


def command_output(args: list[str]) -> str:
    return subprocess.check_output(args, text=True, timeout=5, stderr=subprocess.DEVNULL)


def emit(payload: dict):
    message = json.dumps(payload, sort_keys=True)
    print(message, flush=True)
    try:
        subprocess.run(["logger", "-t", "jellyfin-prefetch-watchdog", message],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass


def prefetch_pid() -> int:
    services = json.loads(command_output(["ubus", "call", "service", "list",
                                         json.dumps({"name": SERVICE})]))
    running = [instance for instance in services.get(SERVICE, {}).get("instances", {}).values()
               if instance.get("running") is True and type(instance.get("pid")) is int]
    if len(running) != 1:
        raise ValueError("prefetch service must have exactly one running instance")
    pid = running[0]["pid"]
    args = read_bytes(f"/proc/{pid}/cmdline").decode().split("\0")
    if os.path.basename(args[0]) not in {"ssh", "dbclient"}:
        raise ValueError("prefetch instance is not yet an SSH client")
    forwards = [args[index + 1] for index, value in enumerate(args[:-1]) if value == "-R"]
    # Refuse to restart a service whose client also forwards another business.
    if len(forwards) != 1 or re.fullmatch(r"(?:localhost:|127\.0\.0\.1:)?18097:[^:]+:18097", forwards[0]) is None:
        raise ValueError("prefetch service must forward only port 18097")
    return pid


@dataclasses.dataclass(frozen=True)
class Sample:
    identity: tuple[int, str, int]
    at: float
    drained_bytes: int
    pending_bytes: int


def sample_from_ss(output: str, pid: int, proxy_port: int, now: float) -> Sample:
    sockets = parse_ss(output)
    proxy = f"127.0.0.1:{proxy_port}"
    clients = [sock for sock in sockets if sock.remote == proxy
               and sock.local.startswith("127.0.0.1:")
               and any(owner_pid == pid for _, owner_pid in sock.owners)]
    if len(clients) != 1:
        raise ValueError("cannot uniquely identify prefetch SSH-to-proxy socket")
    client = clients[0]
    peers = [sock for sock in sockets if sock.local == client.remote and sock.remote == client.local]
    if len(peers) != 1 or len(peers[0].owners) != 1 or peers[0].owners[0][0] != "xray":
        raise ValueError("cannot uniquely identify the Xray peer socket")
    peer = peers[0]
    received = counter(peer.stats, "bytes_received")
    if received < peer.recv_q:
        raise ValueError("inconsistent TCP counters")
    # Data actually consumed by the proxy, not merely ACKed into its socket.
    return Sample((pid, client.local, peer.owners[0][1]), now,
                  received - peer.recv_q, peer.recv_q + client.send_q)


def collect(config: Config) -> Sample:
    pid = prefetch_pid()
    output = command_output(["ss", "-H", "-t", "-i", "-n", "-p",
                             f"sport = :{config.proxy_port} or dport = :{config.proxy_port}"])
    sample = sample_from_ss(output, pid, config.proxy_port, time.monotonic())
    if prefetch_pid() != pid:
        raise ValueError("prefetch PID changed during sampling")
    return sample


class Detector:
    def __init__(self, config: Config):
        self.config = config
        self.reset()

    def reset(self):
        self.previous = None
        self.failures = 0

    def observe(self, sample: Sample) -> tuple[str, float | None]:
        old = self.previous
        self.previous = sample
        if old is None or old.identity != sample.identity:
            self.failures = 0
            return "baseline", None
        elapsed = sample.at - old.at
        delta = sample.drained_bytes - old.drained_bytes
        if elapsed < self.config.interval_seconds * 0.8 or elapsed > self.config.interval_seconds * 2 or delta < 0:
            self.failures = 0
            return "invalid_interval", None
        rate = delta / elapsed
        if min(old.pending_bytes, sample.pending_bytes) < self.config.min_pending_bytes:
            self.failures = 0
            return "no_backlog", rate
        if rate >= self.config.min_drain_bytes_per_second:
            self.failures = 0
            return "healthy_flow", rate
        self.failures += 1
        return ("stalled" if self.failures >= self.config.failure_threshold else "suspect"), rate


def nas_healthy(config: Config) -> bool:
    try:
        body = subprocess.check_output(
            ["curl", "--silent", "--show-error", "--fail", "--noproxy", "*",
             "--max-time", str(config.nas_timeout_seconds), "--max-filesize", "16384",
             config.nas_status_url], timeout=config.nas_timeout_seconds + 2,
            stderr=subprocess.DEVNULL)
        payload = json.loads(body)
        return isinstance(payload, dict) and type(payload.get("active")) is bool
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


class Budget:
    def __init__(self, path: str, boot_id: str):
        self.path = os.fspath(path)
        self.boot_id = boot_id
        self.events = []
        if os.path.exists(self.path):
            state = json.loads(read_text(self.path))
            events = state["events"]
            if not isinstance(events, list) or any(type(item) not in (int, float) or not 0 <= item < float("inf") for item in events):
                raise ValueError("invalid restart history; refusing automatic recovery")
            if state["boot_id"] == boot_id:
                self.events = sorted(events)

    def blocked(self, now: float, config: Config) -> str | None:
        if self.events and now - self.events[-1] < config.cooldown_seconds:
            return "cooldown"
        if sum(now - event < 3600 for event in self.events) >= config.max_restarts_per_hour:
            return "hourly_limit"
        return None

    def reserve(self, now: float):
        events = [event for event in self.events if now - event < 3600] + [now]
        state_dir = os.path.dirname(self.path)
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix="history-", dir=state_dir)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump({"boot_id": self.boot_id, "events": events}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self.events = events


def recover(config: Config, sample: Sample, budget: Budget, dry_run: bool) -> str:
    now = time.time()
    blocked = budget.blocked(now, config)
    if blocked:
        return blocked
    if not nas_healthy(config):
        return "nas_unhealthy_skip"
    if prefetch_pid() != sample.identity[0]:
        return "pid_changed_skip"
    if dry_run:
        return "would_restart"
    # Record attempts BEFORE invoking init.d, even if restart subsequently fails.
    # A failed history write aborts recovery, preventing uncontrolled retries.
    budget.reserve(now)
    result = subprocess.run(RESTART_COMMAND, capture_output=True, text=True, timeout=20)
    return "restart_requested" if result.returncode == 0 else "restart_failed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/jellyfin-prefetch-watchdog.json")
    parser.add_argument("--once", action="store_true", help="read-only snapshot; never restarts")
    parser.add_argument("--dry-run", action="store_true", help="observe without restarting")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args()
    config = Config(**json.loads(read_text(args.config)))
    if args.once:
        print(json.dumps({"version": VERSION, "sample": dataclasses.asdict(collect(config)),
                          "nas_healthy": nas_healthy(config)}, sort_keys=True))
        return
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    with open(os.path.join(STATE_DIR, "lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        budget = Budget(os.path.join(STATE_DIR, "history.json"),
                        read_text("/proc/sys/kernel/random/boot_id").strip())
        detector = Detector(config)
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        last_state = None
        last_log = 0
        emit({"event": "start", "version": VERSION, "service": SERVICE,
              "dry_run": args.dry_run})
        while not stop.is_set():
            details = {}
            try:
                sample = collect(config)
                state, rate = detector.observe(sample)
                details = {"pid": sample.identity[0], "pending_bytes": sample.pending_bytes,
                           "drain_Bps": None if rate is None else round(rate), "failures": detector.failures}
                if state == "stalled":
                    state = recover(config, sample, budget, args.dry_run)
                    detector.reset()
            except Exception as exc:
                # Missing/ambiguous telemetry is not evidence for restarting.
                detector.reset()
                state = "measurement_or_recovery_error"
                details = {"error": type(exc).__name__, "message": str(exc)[:200]}
            now = time.monotonic()
            if state != last_state or now - last_log >= 300:
                emit({"event": state, **details})
                last_state, last_log = state, now
            stop.wait(config.interval_seconds)


if __name__ == "__main__":
    main()
