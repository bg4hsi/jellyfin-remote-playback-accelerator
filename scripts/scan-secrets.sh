#!/bin/sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root_dir"

pattern='(BEGIN (OPENSSH|RSA|EC|DSA) PRIVATE KEY|api[_-]?key[[:space:]]*[:=][[:space:]]*[^$<{[:space:]]|token[[:space:]]*[:=][[:space:]]*[^$<{[:space:]]|([0-9]{1,3}\.){3}[0-9]{1,3})'

if command -v rg >/dev/null 2>&1; then
    matches=$(rg -n -i --hidden --glob '!.git/**' --glob '!scripts/scan-secrets.sh' "$pattern" . || true)
else
    matches=$(grep -RInE "$pattern" . --exclude=scan-secrets.sh --exclude-dir=.git || true)
fi

filtered=$(printf '%s\n' "$matches" | grep -Ev '(127\.0\.0\.1|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)' || true)
if [ -n "$filtered" ]; then
    printf '%s\n' "发现可能的秘密或非示例 IP：" "$filtered" >&2
    exit 1
fi
printf '%s\n' "脱敏扫描通过"
