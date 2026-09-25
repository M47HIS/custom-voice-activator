#!/usr/bin/env bash
# Compatibility wrapper. The project-level script stages and launches the
# SwiftPM GUI target as a real .app bundle.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

exec "$REPO_ROOT/script/build_and_run.sh" "$@"
