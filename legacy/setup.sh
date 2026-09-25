#!/usr/bin/env bash
# setup.sh — Check local prerequisites for the Docker-first Voice Module.
set -euo pipefail

echo "========================================="
echo " Voice Module — Setup Check"
echo "========================================="
echo ""

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker was not found."
    echo "Install OrbStack or Docker Desktop, then run this again."
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker/OrbStack is installed but not running."
    exit 1
fi

echo "Docker is ready."
echo ""
echo "Start the app:"
echo "  ./script/build_and_run.sh"
echo ""
echo "Then open:"
echo "  http://127.0.0.1:8080"
