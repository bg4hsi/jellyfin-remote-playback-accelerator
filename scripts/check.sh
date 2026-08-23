#!/bin/sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root_dir"

python_bin=${PYTHON_BIN:-python3}
cache_dir=${PYTHONPYCACHEPREFIX:-$root_dir/.pycache-check}
PYTHONPYCACHEPREFIX="$cache_dir" "$python_bin" -m py_compile worker/jellyfin_prefetch_worker.py home/jellyfin_prefetch_origin.py
PYTHONPYCACHEPREFIX="$cache_dir" "$python_bin" -m unittest discover -s tests -v
./scripts/scan-secrets.sh
