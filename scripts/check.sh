#!/bin/sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root_dir"

python_bin=${PYTHON_BIN:-python3}
cache_dir=${PYTHONPYCACHEPREFIX:-$root_dir/.pycache-check}
PYTHONPYCACHEPREFIX="$cache_dir" "$python_bin" -m py_compile worker/jellyfin_prefetch_worker.py home/jellyfin_prefetch_origin.py scripts/jellyfin_cache_cleaner.py tunnel/prefetch_tunnel_watchdog.py
sh -n tunnel/openwrt-prefetch-watchdog.init
sh -n tunnel/install-openwrt-prefetch-watchdog.sh
PYTHONPYCACHEPREFIX="$cache_dir" "$python_bin" -m unittest discover -s tests -v
./scripts/scan-secrets.sh
