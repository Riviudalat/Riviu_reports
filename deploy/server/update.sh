#!/usr/bin/env bash
# Update the Riviu Reports server: pull, rebuild, restart, wait for /healthz.
# Usage (from anywhere): deploy/server/update.sh [branch]   (default: the current branch)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose -f "$REPO_DIR/deploy/server/compose.yaml")
HEALTH_URL="http://127.0.0.1:18110/healthz"

cd "$REPO_DIR"
if [[ $# -gt 0 ]]; then
    git fetch origin "$1"
    git checkout "$1"
fi
git pull --ff-only

"${COMPOSE[@]}" build --pull
"${COMPOSE[@]}" up -d

echo "Chờ ứng dụng sẵn sàng..."
for _ in $(seq 1 60); do
    if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
        echo "OK: $(git log -1 --format='%h %s')"
        exit 0
    fi
    sleep 2
done

echo "Lỗi: $HEALTH_URL không phản hồi sau 120 giây. Nhật ký gần nhất:" >&2
"${COMPOSE[@]}" logs --tail=80 riviu >&2
exit 1
