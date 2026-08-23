#!/usr/bin/env python3
"""Loopback-only, read-only view of Jellyfin's generated transcode segments."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


HASH_RE = re.compile(r"^[0-9a-f]{16,64}$")
SEGMENT_RE = re.compile(r"^/segment/([0-9a-f]{16,64})/([0-9]+)\.([a-z0-9]{2,5})$")


@dataclass(frozen=True)
class Config:
    transcode_dir: Path = Path(os.getenv("TRANSCODE_DIR", "/var/lib/jellyfin/transcodes"))
    host: str = os.getenv("ORIGIN_LISTEN_HOST", "127.0.0.1")
    port: int = int(os.getenv("ORIGIN_LISTEN_PORT", "18097"))
    active_seconds: int = int(os.getenv("ACTIVE_SECONDS", "15"))
    safety_segments: int = int(os.getenv("SAFETY_SEGMENTS", "2"))
    extensions: tuple[str, ...] = tuple(
        item.strip() for item in os.getenv("ALLOWED_EXTENSIONS", "ts").split(",") if item.strip()
    )


CONFIG = Config()


def find_latest_job(config: Config = CONFIG) -> Optional[dict[str, object]]:
    candidates: list[tuple[float, str, str, list[int]]] = []
    if not config.transcode_dir.is_dir():
        return None
    for manifest in config.transcode_dir.glob("*.m3u8"):
        job_hash = manifest.stem
        if not HASH_RE.fullmatch(job_hash):
            continue
        for extension in config.extensions:
            numbers: list[int] = []
            pattern = re.compile(rf"^{re.escape(job_hash)}([0-9]+)\.{re.escape(extension)}$")
            for path in config.transcode_dir.glob(f"{job_hash}*.{extension}"):
                match = pattern.fullmatch(path.name)
                if match:
                    numbers.append(int(match.group(1)))
            if numbers:
                newest = max([manifest.stat().st_mtime] + [
                    (config.transcode_dir / f"{job_hash}{number}.{extension}").stat().st_mtime
                    for number in numbers[-3:]
                ])
                candidates.append((newest, job_hash, extension, sorted(numbers)))
    if not candidates:
        return None
    newest, job_hash, extension, numbers = max(candidates, key=lambda item: item[0])
    active = time.time() - newest <= config.active_seconds
    last_generated = numbers[-1]
    safe_max = max(numbers[0], last_generated - config.safety_segments) if active else last_generated
    return {
        "active": active,
        "hash": job_hash,
        "extension": extension,
        "first_generated": numbers[0],
        "last_generated": last_generated,
        "safe_prefetch_max": safe_max,
    }


def segment_path(job_hash: str, segment: int, extension: str, config: Config = CONFIG) -> Optional[Path]:
    if not HASH_RE.fullmatch(job_hash) or extension not in config.extensions or segment < 0:
        return None
    path = config.transcode_dir / f"{job_hash}{segment}.{extension}"
    try:
        path.resolve().relative_to(config.transcode_dir.resolve())
    except ValueError:
        return None
    return path


class Handler(BaseHTTPRequestHandler):
    server_version = "JellyfinPrefetchOrigin/1"

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/status":
            self.send_json(200, {"job": find_latest_job()})
            return
        match = SEGMENT_RE.fullmatch(path)
        if not match:
            self.send_json(404, {"error": "not_found"})
            return
        job_hash, segment_text, extension = match.groups()
        current = find_latest_job()
        if not current or current["hash"] != job_hash:
            self.send_json(409, {"error": "stale_job"})
            return
        target = segment_path(job_hash, int(segment_text), extension)
        if target is None or not target.is_file():
            self.send_json(404, {"error": "segment_not_ready"})
            return
        size = target.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "public, max-age=86400, immutable")
        self.end_headers()
        if self.command != "HEAD":
            with target.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    self.wfile.write(chunk)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    if CONFIG.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("ORIGIN_LISTEN_HOST must be loopback")
    server = ThreadingHTTPServer((CONFIG.host, CONFIG.port), Handler)
    print(f"origin helper listening on {CONFIG.host}:{CONFIG.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
