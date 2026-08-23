#!/usr/bin/env python3
"""
Jellyfin Edge Cache - Prefetch Worker v18.3

Session-aware reference worker.

Features:
- Detect playback session changes using stream prefix + Jellyfin hash.
- Reset prefetch state when a new transcode session appears.
- Warm up new sessions.
- Live prefetch while transcoding.
- Drain generated HLS segments after transcoding completes.
- Hold completed cache while playback remains active.

This file is a reference implementation. Review paths and permissions before production use.
"""

import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

STATUS_URL = "http://127.0.0.1:8098/status"
BASE_URL = "http://127.0.0.1:8098"
INTERVAL = 5
WORKERS = 4
WINDOW = 300
WARMUP = 100
STOP_AGE_MS = 300000
LOG = "/var/log/jellyfin-prefetch-worker.log"

context = None
done = set()
highest_prefetched = -1
mode = "IDLE"


def log(message):
    with open(LOG, "a") as f:
        f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")


def status():
    with urllib.request.urlopen(STATUS_URL, timeout=5) as r:
        return json.loads(r.read())


def fetch(url, segment):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            while r.read(1024 * 1024):
                pass
        return segment, True
    except Exception:
        return segment, False


while True:
    try:
        info = status()
        player = info.get("player", {})
        nas = info.get("nas", {})

        if not player.get("tracked"):
            time.sleep(INTERVAL)
            continue

        if int(player.get("track_age_ms") or 0) > STOP_AGE_MS:
            log("SESSION TIMEOUT")
            time.sleep(INTERVAL)
            continue

        prefix = player.get("prefix") or ""
        current = int(player.get("current") or -1)
        jfhash = nas.get("hash")
        ext = nas.get("extension") or "ts"

        if not prefix or current < 0 or not jfhash:
            time.sleep(INTERVAL)
            continue

        new_context = (prefix, jfhash, ext)
        if new_context != context:
            log("SESSION/HASH CHANGE")
            log("OLD=" + str(context))
            log("NEW=" + str(new_context))
            context = new_context
            done.clear()
            highest_prefetched = -1
            mode = "WARMUP"

        if nas.get("active") and int(nas.get("active_jobs") or 0):
            limit = int(nas.get("safe_prefetch_max") or 0)
            mode = "LIVE"
        else:
            limit = int(nas.get("last_generated") or 0)
            mode = "DRAIN"

        window = WARMUP if mode == "WARMUP" else WINDOW
        end = min(current + window, limit)

        jobs = []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for segment in range(current + 1, end + 1):
                if segment not in done:
                    url = f"{BASE_URL}{prefix}{segment}.{ext}?jfhash={jfhash}"
                    jobs.append(pool.submit(fetch, url, segment))

            success = 0
            for job in as_completed(jobs):
                segment, ok = job.result()
                if ok:
                    done.add(segment)
                    highest_prefetched = max(highest_prefetched, segment)
                    success += 1
                    log(f"prefetched {segment}")

        if mode == "DRAIN" and highest_prefetched >= limit:
            log(f"CACHE HOLD complete highest={highest_prefetched}")
            mode = "HOLD"

        log(f"{mode} player={current} target={limit} success={success}")

    except Exception as e:
        log("ERROR " + str(e))

    time.sleep(INTERVAL)
