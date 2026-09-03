#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "must run as root" >&2
    exit 1
fi

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
program="$source_dir/prefetch_tunnel_watchdog.py"
init_file="$source_dir/openwrt-prefetch-watchdog.init"
nas_url=${NAS_STATUS_URL:-}
config=/etc/jellyfin-prefetch-watchdog.json

test -r "$program"
test -r "$init_file"
command -v python3 >/dev/null
command -v ss >/dev/null
command -v ubus >/dev/null

if [ ! -e "$config" ]; then
    case "$nas_url" in
        http://*:18097/prefetch) ;;
        *) echo "first install requires NAS_STATUS_URL=http://NAS-LAN-IP:18097/prefetch" >&2; exit 1 ;;
    esac
    umask 077
    config_tmp=$(mktemp /tmp/jellyfin-prefetch-watchdog-config.XXXXXX)
    cat >"$config_tmp" <<EOF
{
  "nas_status_url": "$nas_url",
  "proxy_port": 10022,
  "interval_seconds": 30,
  "failure_threshold": 3,
  "min_pending_bytes": 262144,
  "min_drain_bytes_per_second": 524288,
  "cooldown_seconds": 600,
  "max_restarts_per_hour": 2,
  "nas_timeout_seconds": 3
}
EOF
else
    config_tmp=$config
fi

# Validate the exact candidate code and configuration before changing services.
python3 "$program" --config "$config_tmp" --once

timestamp=$(date +%Y%m%d-%H%M%S)
backup=/root/jellyfin-prefetch-watchdog-backup-$timestamp
made_backup=0
for path in /usr/bin/jellyfin-prefetch-watchdog.py \
            /etc/init.d/jellyfin-prefetch-watchdog \
            "$config"; do
    if [ -e "$path" ]; then
        if [ "$made_backup" -eq 0 ]; then
            mkdir -p "$backup"
            made_backup=1
        fi
        cp -p "$path" "$backup/$(basename "$path")"
    fi
done

cp "$program" /usr/bin/jellyfin-prefetch-watchdog.py.new
chmod 0755 /usr/bin/jellyfin-prefetch-watchdog.py.new
mv /usr/bin/jellyfin-prefetch-watchdog.py.new /usr/bin/jellyfin-prefetch-watchdog.py

cp "$init_file" /etc/init.d/jellyfin-prefetch-watchdog.new
chmod 0755 /etc/init.d/jellyfin-prefetch-watchdog.new
mv /etc/init.d/jellyfin-prefetch-watchdog.new /etc/init.d/jellyfin-prefetch-watchdog

if [ "$config_tmp" != "$config" ]; then
    cp "$config_tmp" "$config.new"
    chmod 0600 "$config.new"
    mv "$config.new" "$config"
    rm -f "$config_tmp"
fi

/etc/init.d/jellyfin-prefetch-watchdog enable
/etc/init.d/jellyfin-prefetch-watchdog restart
sleep 1
/etc/init.d/jellyfin-prefetch-watchdog status
echo "installed jellyfin-prefetch-watchdog 1.0.0"
if [ "$made_backup" -eq 1 ]; then
    echo "backup: $backup"
fi
