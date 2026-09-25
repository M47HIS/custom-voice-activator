#!/usr/bin/env bash
# start.sh — convenience launcher for the Docker-first Voice Module.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "========================================="
echo " Voice Module — Docker local app"
echo "========================================="
echo ""

exec "$SCRIPT_DIR/script/build_and_run.sh" "$@"
