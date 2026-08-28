#!/usr/bin/env python3
"""Prune consumed Jellyfin HLS cache entries when disk space is low."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


VERSION = "2.0.0"
CACHE_DIR = os.getenv(
    "JELLYFIN_CACHE_DIR", "/var/cache/nginx/jellyfin-edge-cache"
)
STATUS_URL = os.getenv(
    "JELLYFIN_STATUS_URL", "http://127.0.0.1:8098/__prefetch_status"
)
LOG_PATH = os.getenv(
    "JELLYFIN_CACHE_CLEANER_LOG", "/var/log/jellyfin-cache-cleaner.log"
)
LOCK_PATH = os.getenv(
    "JELLYFIN_CACHE_CLEANER_LOCK", "/run/jellyfin-cache-cleaner.lock"
)
TRIGGER_FREE_GIB = float(os.getenv("JELLYFIN_CACHE_TRIGGER_FREE_GIB", "3"))
MAX_TRACK_AGE_MS = int(os.getenv("JELLYFIN_CACHE_MAX_TRACK_AGE_MS", "60000"))
HEADER_BYTES = 16384

PREFIX_RE = re.compile(r"^/videos/[^/]+/hls[^/]*/main/$")
KEY_RE = re.compile(
    rb"KEY: hls\|(?P<prefix>/videos/[^/]+/hls[^/]*/main/)"
    rb"(?P<segment>[0-9]+)\.(?P<extension>ts|m4s)\|[^\r\n]*"
)


@dataclass(frozen=True)
class Player:
    prefix: str
    current: int
    track_age_ms: int


@dataclass(frozen=True)
class CacheKey:
    prefix: str
    segment: int
    extension: str


def log(message: str) -> None:
    line = datetime.now().strftime("%Y-%m-%d %H:%M:%S ") + message
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        print(f"warning: cannot write log: {exc}", file=sys.stderr)


def disk_free_bytes(path: str) -> int:
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def load_player() -> Optional[Player]:
    request = urllib.request.Request(
        STATUS_URL, headers={"User-Agent": f"jellyfin-cache-cleaner/{VERSION}"}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.load(response)
    player = payload.get("player") or {}
    if not player.get("tracked"):
        return None
    prefix = player.get("prefix")
    current = player.get("current")
    age = player.get("track_age_ms")
    if not isinstance(prefix, str) or PREFIX_RE.fullmatch(prefix) is None:
        return None
    # A resumed session can briefly report segment 0 before its real position.
    if not isinstance(current, int) or current <= 0:
        return None
    if not isinstance(age, (int, float)) or age < 0 or age > MAX_TRACK_AGE_MS:
        return None
    return Player(prefix=prefix, current=current, track_age_ms=int(age))


def parse_cache_key(path: str) -> Optional[CacheKey]:
    try:
        with open(path, "rb") as handle:
            header = handle.read(HEADER_BYTES)
    except OSError:
        return None
    match = KEY_RE.search(header)
    if match is None:
        return None
    return CacheKey(
        prefix=match.group("prefix").decode("utf-8", "replace"),
        segment=int(match.group("segment")),
        extension=match.group("extension").decode("ascii"),
    )


def should_remove(key: CacheKey, player: Player) -> bool:
    return key.prefix != player.prefix or key.segment < player.current


def prune(player: Player, dry_run: bool) -> tuple[int, int, int, int]:
    removed_files = 0
    removed_bytes = 0
    kept_files = 0
    unknown_files = 0

    for root, _dirs, files in os.walk(CACHE_DIR):
        for name in files:
            path = os.path.join(root, name)
            key = parse_cache_key(path)
            if key is None:
                unknown_files += 1
                continue
            if not should_remove(key, player):
                kept_files += 1
                continue
            try:
                size = os.path.getsize(path)
                if not dry_run:
                    os.unlink(path)
            except FileNotFoundError:
                continue
            except OSError as exc:
                log(f"DELETE_FAILED path={path} error={exc}")
                continue
            removed_files += 1
            removed_bytes += size

    return removed_files, removed_bytes, kept_files, unknown_files


def reset_nginx_cache_index() -> None:
    subprocess.run(["nginx", "-t"], check=True)
    try:
        subprocess.run(["systemctl", "restart", "nginx"], check=True)
    except subprocess.CalledProcessError:
        subprocess.run(["systemctl", "start", "nginx"], check=False)
        raise
    subprocess.run(["systemctl", "is-active", "--quiet", "nginx"], check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()

    if args.version:
        print(VERSION)
        return 0

    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    with open(LOCK_PATH, "w", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("SKIP another cleaner run is active")
            return 0

        free_before = disk_free_bytes(CACHE_DIR)
        trigger_bytes = int(TRIGGER_FREE_GIB * 1024**3)
        if not args.force and free_before >= trigger_bytes:
            log(
                f"SPACE_OK free_gib={free_before / 1024**3:.2f} "
                f"trigger_gib={TRIGGER_FREE_GIB:.2f}"
            )
            return 0

        try:
            player = load_player()
        except Exception as exc:
            log(f"SKIP status_error={type(exc).__name__}:{exc}")
            return 0
        if player is None:
            log("SKIP no_fresh_active_player; cache preserved")
            return 0

        removed_files, removed_bytes, kept_files, unknown_files = prune(
            player, args.dry_run
        )
        action = "DRY_RUN" if args.dry_run else "PRUNE"
        log(
            f"{action} current={player.current} prefix={player.prefix} "
            f"removed_files={removed_files} removed_gib={removed_bytes / 1024**3:.2f} "
            f"kept_files={kept_files} unknown_files={unknown_files} "
            f"free_before_gib={free_before / 1024**3:.2f}"
        )

        if not args.dry_run and removed_files:
            reset_nginx_cache_index()
            free_after = disk_free_bytes(CACHE_DIR)
            log(f"NGINX_RESTARTED free_after_gib={free_after / 1024**3:.2f}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
