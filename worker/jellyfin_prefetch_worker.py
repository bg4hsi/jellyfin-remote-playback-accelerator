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
from dataclasses import dataclass
from typing import Any, Optional


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
    poll_seconds: int = env_int("POLL_SECONDS", 5)
    stale_seconds: int = env_int("PLAYER_STALE_SECONDS", 45)
    timeout_seconds: int = env_int("HTTP_TIMEOUT_SECONDS", 20)


def get_json(url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "jellyfin-edge-cache/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"status endpoint returned HTTP {response.status}")
        return json.load(response)


def validate_player(payload: dict[str, Any], stale_seconds: int) -> Optional[dict[str, Any]]:
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
    if not isinstance(age, (int, float)) or age > stale_seconds * 1000:
        return None
    return {"prefix": prefix, "extension": extension, "current": current}


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
    origin_limit = (
        origin["safe_prefetch_max"] if origin["active"] else origin["last_generated"]
    )
    end = min(player["current"] + window, origin_limit)
    return range(start, end + 1) if end >= start else range(0)


def prefetch_one(settings: Settings, player: dict[str, Any], job_hash: str, segment: int) -> str:
    path = f'{player["prefix"]}{segment}.{player["extension"]}'
    query = urllib.parse.urlencode({"jfhash": job_hash})
    url = f"{settings.prefetch_base_url}{path}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "jellyfin-edge-cache/1"})
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
            response.read()
            return "ok" if response.status in {200, 206} else f"http_{response.status}"
    except urllib.error.HTTPError as exc:
        return f"http_{exc.code}"
    except (urllib.error.URLError, TimeoutError):
        return "network_error"


def run_cycle(settings: Settings) -> tuple[str, int]:
    player = validate_player(get_json(settings.player_status_url, settings.timeout_seconds), settings.stale_seconds)
    if player is None:
        return "no_active_player", 0
    origin = validate_origin(get_json(settings.origin_status_url, settings.timeout_seconds))
    if origin is None or origin["extension"] != player["extension"]:
        return "no_matching_origin_job", 0
    segments = list(target_range(player, origin, settings.window))
    if not segments:
        return "nothing_to_prefetch", 0

    successes = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=settings.workers) as executor:
        futures = [
            executor.submit(prefetch_one, settings, player, origin["hash"], segment)
            for segment in segments
        ]
        for segment, future in zip(segments, futures):
            result = future.result()
            if result == "ok":
                successes += 1
            elif result not in {"http_404", "http_409"}:
                logging.warning("segment=%s result=%s", segment, result)

    mode = "live" if origin["active"] else "drain"
    logging.info(
        "mode=%s player=%s window=%s-%s success=%s",
        mode,
        player["current"],
        segments[0],
        segments[-1],
        successes,
    )
    return mode, successes


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = Settings()
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.info("worker started")
    while not stopping:
        try:
            reason, _ = run_cycle(settings)
            if reason not in {"live", "drain", "nothing_to_prefetch"}:
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
