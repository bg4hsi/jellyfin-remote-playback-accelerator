#!/usr/bin/env python3
"""Poll player/origin state and warm the VPS Nginx HLS cache."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional


VERSION = "18.4.0"


def env_int(name: str, default: int, minimum: int = 1) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class Settings:
    player_status_url: str = os.getenv(
        "PLAYER_STATUS_URL", "http://127.0.0.1:8098/__prefetch_status"
    )
    origin_status_url: str = os.getenv(
        "ORIGIN_STATUS_URL", "http://127.0.0.1:18097/status"
    )
    prefetch_base_url: str = os.getenv(
        "PREFETCH_BASE_URL", "http://127.0.0.1:8098"
    ).rstrip("/")
    window: int = env_int("PREFETCH_WINDOW", 300)
    workers: int = env_int("PREFETCH_WORKERS", 4)
    drain_batch: int = env_int("DRAIN_BATCH", 32)
    verify_batch: int = env_int("CACHE_VERIFY_BATCH", 8)
    verify_retries: int = env_int("CACHE_VERIFY_RETRIES", 3)
    poll_seconds: int = env_int("POLL_SECONDS", 5)
    stale_seconds: int = env_int("PLAYER_STALE_SECONDS", 45)
    timeout_seconds: int = env_int("HTTP_TIMEOUT_SECONDS", 20)


@dataclass
class WorkerState:
    context: Optional[tuple[str, str, str]] = None
    done: set[int] = field(default_factory=set)
    drain_cursor: Optional[int] = None
    mode: str = "idle"


def get_json(url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"User-Agent": f"jellyfin-edge-cache/{VERSION}"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"status endpoint returned HTTP {response.status}")
        return json.load(response)


def validate_player(
    payload: dict[str, Any], stale_seconds: int, allow_stale: bool = False
) -> Optional[dict[str, Any]]:
    player = payload.get("player") or {}
    if not player.get("tracked"):
        return None
    prefix = player.get("prefix")
    extension = player.get("extension")
    current = player.get("current")
    age = player.get("track_age_ms")
    if not isinstance(prefix, str) or not prefix.startswith("/videos/"):
        return None
    if extension not in {"ts", "m4s"}:
        return None
    if not isinstance(current, int) or current < 0:
        return None
    if not isinstance(age, (int, float)):
        return None
    if not allow_stale and age > stale_seconds * 1000:
        return None
    return {
        "prefix": prefix,
        "extension": extension,
        "current": current,
        "track_age_ms": age,
    }


def validate_origin(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    job = payload.get("job") or {}
    job_hash = job.get("hash")
    extension = job.get("extension")
    last_generated = job.get("last_generated")
    safe_max = job.get("safe_prefetch_max")
    active = bool(job.get("active"))
    if not isinstance(job_hash, str) or not (16 <= len(job_hash) <= 64):
        return None
    if extension not in {"ts", "m4s"}:
        return None
    if not isinstance(last_generated, int) or last_generated < 0:
        return None
    if not isinstance(safe_max, int) or safe_max < 0:
        return None
    return {
        "hash": job_hash,
        "extension": extension,
        "last_generated": last_generated,
        "safe_prefetch_max": safe_max,
        "active": active,
    }


def target_range(player: dict[str, Any], origin: dict[str, Any], window: int) -> range:
    start = player["current"] + 1
    if origin["active"]:
        end = min(player["current"] + window, origin["safe_prefetch_max"])
    else:
        end = origin["last_generated"]
    return range(start, end + 1) if end >= start else range(0)


def segment_url(
    settings: Settings, player: dict[str, Any], job_hash: str, segment: int
) -> str:
    path = f'{player["prefix"]}{segment}.{player["extension"]}'
    query = urllib.parse.urlencode({"jfhash": job_hash})
    return f"{settings.prefetch_base_url}{path}?{query}"


def response_cache_status(response: Any) -> str:
    return str(response.headers.get("X-Cache-Status", "")).upper()


def probe_cached(
    settings: Settings, player: dict[str, Any], job_hash: str, segment: int
) -> bool:
    request = urllib.request.Request(
        segment_url(settings, player, job_hash, segment),
        method="HEAD",
        headers={"User-Agent": f"jellyfin-edge-cache/{VERSION}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
            return response.status in {200, 206} and response_cache_status(response) == "HIT"
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False


def prefetch_one(
    settings: Settings, player: dict[str, Any], job_hash: str, segment: int
) -> str:
    request = urllib.request.Request(
        segment_url(settings, player, job_hash, segment),
        headers={"User-Agent": f"jellyfin-edge-cache/{VERSION}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
            response.read()
            if response.status not in {200, 206}:
                return f"http_{response.status}"
            if response_cache_status(response) == "HIT":
                return "ok"
    except urllib.error.HTTPError as exc:
        return f"http_{exc.code}"
    except (urllib.error.URLError, TimeoutError):
        return "network_error"

    # HTTP 200 only proves the origin replied. Confirm that Nginx committed the
    # object and can now serve the exact same cache key as HIT.
    for attempt in range(settings.verify_retries):
        if probe_cached(settings, player, job_hash, segment):
            return "ok"
        if attempt + 1 < settings.verify_retries:
            time.sleep(0.1 * (attempt + 1))
    return "cache_unverified"


def sync_context(state: WorkerState, player: dict[str, Any], origin: dict[str, Any]) -> bool:
    context = (player["prefix"], origin["hash"], player["extension"])
    if context == state.context:
        return False
    logging.info("session changed old=%s new=%s", state.context, context)
    state.context = context
    state.done.clear()
    state.drain_cursor = None
    state.mode = "warmup"
    return True


def plan_segments(
    settings: Settings,
    state: WorkerState,
    player: dict[str, Any],
    origin: dict[str, Any],
) -> list[int]:
    current = player["current"]
    target = (
        origin["safe_prefetch_max"] if origin["active"] else origin["last_generated"]
    )
    state.done = {segment for segment in state.done if segment > current}

    if origin["active"]:
        state.drain_cursor = None
        end = min(current + settings.window, target)
        return [
            segment
            for segment in range(current + 1, end + 1)
            if segment not in state.done
        ]

    selected: list[int] = []
    selected_set: set[int] = set()

    # Always repair the playback-critical window before extending the drain.
    urgent_end = min(current + settings.window, target)
    for segment in range(current + 1, urgent_end + 1):
        if segment not in state.done:
            selected.append(segment)
            selected_set.add(segment)
            if len(selected) >= settings.drain_batch:
                return selected

    if state.drain_cursor is None:
        state.drain_cursor = current + 1
    state.drain_cursor = max(state.drain_cursor, current + 1)

    cursor = state.drain_cursor
    while cursor <= target and len(selected) < settings.drain_batch:
        if cursor not in state.done and cursor not in selected_set:
            selected.append(cursor)
            selected_set.add(cursor)
        cursor += 1
    return selected


def verify_near_player(
    settings: Settings,
    state: WorkerState,
    player: dict[str, Any],
    origin: dict[str, Any],
) -> int:
    target = (
        origin["safe_prefetch_max"] if origin["active"] else origin["last_generated"]
    )
    verify_end = min(player["current"] + settings.verify_batch, target)
    candidates = [
        segment
        for segment in range(player["current"] + 1, verify_end + 1)
        if segment in state.done
    ]
    missing = 0
    for segment in candidates:
        if not probe_cached(settings, player, origin["hash"], segment):
            state.done.discard(segment)
            missing += 1
    return missing


def advance_drain_cursor(state: WorkerState, current: int, target: int) -> None:
    if state.drain_cursor is None:
        state.drain_cursor = current + 1
    state.drain_cursor = max(state.drain_cursor, current + 1)
    while state.drain_cursor <= target and state.drain_cursor in state.done:
        state.drain_cursor += 1


def run_cycle(settings: Settings, state: Optional[WorkerState] = None) -> tuple[str, int]:
    state = state or WorkerState()
    origin = validate_origin(get_json(settings.origin_status_url, settings.timeout_seconds))
    if origin is None:
        return "no_matching_origin_job", 0

    player = validate_player(
        get_json(settings.player_status_url, settings.timeout_seconds),
        settings.stale_seconds,
        allow_stale=not origin["active"],
    )
    if player is None or origin["extension"] != player["extension"]:
        return "no_active_player", 0

    sync_context(state, player, origin)
    invalidated = verify_near_player(settings, state, player, origin)
    if invalidated:
        logging.warning("cache verification invalidated=%s", invalidated)

    segments = plan_segments(settings, state, player, origin)
    target = (
        origin["safe_prefetch_max"] if origin["active"] else origin["last_generated"]
    )
    if not segments:
        if origin["active"]:
            state.mode = "live"
            return "nothing_to_prefetch", 0
        advance_drain_cursor(state, player["current"], target)
        state.mode = "hold" if state.drain_cursor > target else "drain"
        return state.mode, 0

    successes = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=settings.workers) as executor:
        futures = {
            executor.submit(
                prefetch_one, settings, player, origin["hash"], segment
            ): segment
            for segment in segments
        }
        for future in concurrent.futures.as_completed(futures):
            segment = futures[future]
            result = future.result()
            if result == "ok":
                state.done.add(segment)
                successes += 1
            elif result not in {"http_404", "http_409"}:
                logging.warning("segment=%s result=%s", segment, result)

    mode = "live" if origin["active"] else "drain"
    state.mode = mode
    if mode == "drain":
        advance_drain_cursor(state, player["current"], target)
    logging.info(
        "mode=%s player=%s batch=%s-%s success=%s target=%s cursor=%s",
        mode,
        player["current"],
        segments[0],
        segments[-1],
        successes,
        target,
        state.drain_cursor,
    )
    return mode, successes


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = Settings()
    state = WorkerState()
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.info("worker started version=%s", VERSION)
    while not stopping:
        try:
            reason, _ = run_cycle(settings, state)
            if reason not in {"live", "drain", "hold", "nothing_to_prefetch"}:
                logging.debug("idle reason=%s", reason)
        except Exception:
            logging.exception("prefetch cycle failed")
        for _ in range(settings.poll_seconds * 10):
            if stopping:
                break
            time.sleep(0.1)
    logging.info("worker stopped")


if __name__ == "__main__":
    main()
