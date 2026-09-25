#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-build}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SWIFT_DIR="$ROOT_DIR/macos/VoiceActivator"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
APP_URL="http://127.0.0.1:8080"
APP_NAME="Voice AI.app"
APP_BUNDLE="/Applications/$APP_NAME"
BUILD_APP="$SWIFT_DIR/.build/release/$APP_NAME"
APP_SUPPORT="$HOME/Library/Application Support/VoiceModule"
VENV_PYTHON="$APP_SUPPORT/venv/bin/python3"
BUNDLE_ID="${VOICE_AI_BUNDLE_ID:-io.github.M47HIS.VoiceAI}"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

compose() {
    docker compose -f "$COMPOSE_FILE" "$@"
}

ensure_docker() {
    command -v docker >/dev/null 2>&1 || fail "docker was not found on PATH."
    docker info >/dev/null 2>&1 || fail "Docker/OrbStack is not running."
}

build_swift() {
    echo "==> Building Voice AI.app..."
    (cd "$SWIFT_DIR" && swift build -c release) || fail "Swift build failed."
    echo "[PASS] Swift build"
}

package_app() {
    [[ "$BUNDLE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || fail "Invalid app bundle identifier."
    local contents="$BUILD_APP/Contents"
    rm -rf "$BUILD_APP"
    mkdir -p "$contents/MacOS" "$contents/Resources/client"
    cp "$SWIFT_DIR/.build/release/VoiceAI" "$contents/MacOS/VoiceAI"
    cp "$ROOT_DIR/client/voice_client.py" "$contents/Resources/client/voice_client.py"
    cp "$ROOT_DIR/client/control_decider.py" "$contents/Resources/client/control_decider.py"
    cp "$ROOT_DIR/client/requirements.txt" "$contents/Resources/client/requirements.txt"
    cp "$SWIFT_DIR/Assets/VoiceAI.icns" "$contents/Resources/VoiceAI.icns"
    cat > "$contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleDisplayName</key><string>Voice AI</string>
  <key>CFBundleExecutable</key><string>VoiceAI</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleName</key><string>Voice AI</string>
  <key>CFBundleIconFile</key><string>VoiceAI.icns</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Voice AI records audio for dictation and voice control.</string>
</dict></plist>
EOF
    xattr -cr "$BUILD_APP"
    codesign --force --deep --sign - "$BUILD_APP"
}

install_native() {
    build_swift
    package_app
    mkdir -p "$APP_SUPPORT"
    # Prefer the pinned Homebrew python@3.11 over whatever python3 is on PATH:
    # a Homebrew upgrade can swap the stdlib under the long-running worker.
    local host_python=""
    for candidate in \
        /opt/homebrew/opt/python@3.11/libexec/bin/python3 \
        /usr/local/opt/python@3.11/libexec/bin/python3 \
        "$(command -v python3 || true)"; do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            host_python="$candidate"
            break
        fi
    done
    [ -n "$host_python" ] || fail "python3 was not found."
    echo "Using Python: $host_python"
    if [ ! -x "$VENV_PYTHON" ]; then
        "$host_python" -m venv "$APP_SUPPORT/venv"
    fi
    if "$VENV_PYTHON" -c 'import importlib.util; names = ("numpy", "sounddevice", "pynput", "websocket", "requests", "mlx_audio", "laya_mlx"); raise SystemExit(any(importlib.util.find_spec(name) is None for name in names))'; then
        echo "[PASS] Existing worker dependencies"
    else
        "$VENV_PYTHON" -m pip install -r "$ROOT_DIR/client/requirements.txt"
    fi
    local old_bundle="/Applications/VoiceActivator.app"
    local backup_dir
    backup_dir="$(mktemp -d /private/tmp/voice-ai-install.XXXXXX)" || fail "Could not create install backup."
    local old_backup="$backup_dir/VoiceActivator.app"
    local new_backup="$backup_dir/Voice AI.app"
    if [ -d "$old_bundle" ]; then
        mv "$old_bundle" "$old_backup" || fail "Could not back up the old app."
    fi
    if [ -d "$APP_BUNDLE" ]; then
        if ! mv "$APP_BUNDLE" "$new_backup"; then
            if [ -d "$old_backup" ]; then mv "$old_backup" "$old_bundle"; fi
            fail "Could not back up the current Voice AI app."
        fi
    fi
    if ! cp -R "$BUILD_APP" "$APP_BUNDLE" ||
       ! xattr -cr "$APP_BUNDLE" ||
       ! codesign --force --deep --sign - "$APP_BUNDLE" ||
       ! codesign --verify --deep --strict "$APP_BUNDLE"; then
        rm -rf "$APP_BUNDLE"
        if [ -d "$old_backup" ]; then mv "$old_backup" "$old_bundle"; fi
        if [ -d "$new_backup" ]; then mv "$new_backup" "$APP_BUNDLE"; fi
        fail "Could not install Voice AI."
    fi
    rm -rf "$backup_dir"
    echo "Installed: $APP_BUNDLE"
    echo "The first transcription downloads the local Voxtral model."
    echo "Open the app, then grant Microphone permission when prompted."
}

verify() {
    local failures=0
    echo "==> Verifying Voice Module"

    # Swift binary exists
    if [ -f "$SWIFT_DIR/.build/release/VoiceAI" ]; then
        echo "[PASS] Voice AI binary"
    else
        echo "[FAIL] Voice AI binary not found"
        failures=$((failures + 1))
    fi

    # Python worker compiles
    if PYTHONPYCACHEPREFIX=/private/tmp/voice-module-pycache \
        python3 -m py_compile "$ROOT_DIR/client/voice_client.py" "$ROOT_DIR/client/control_decider.py" 2>/dev/null; then
        echo "[PASS] Python worker and control decisions compile"
    else
        echo "[FAIL] Python worker does not compile"
        failures=$((failures + 1))
    fi

    # Backend compiles (if present)
    if [ -f "$ROOT_DIR/backend/main.py" ]; then
        if PYTHONPYCACHEPREFIX=/private/tmp/voice-module-pycache \
            python3 -m py_compile "$ROOT_DIR/backend/main.py" 2>/dev/null; then
            echo "[PASS] Backend compiles"
        else
            echo "[FAIL] Backend does not compile"
            failures=$((failures + 1))
        fi
    fi

    # Optional: Docker backend (if running)
    if curl -fsS "$APP_URL/api/status" 2>/dev/null | grep -q '"state"'; then
        echo "[PASS] Docker backend (optional)"
    else
        echo "[SKIP] Docker backend not running (optional)"
    fi

    if [ "$failures" -gt 0 ]; then
        fail "$failures verification check(s) failed."
    fi
    echo "==> Verification passed."
}

case "$MODE" in
    build)
        build_swift
        echo "Voice AI built: $SWIFT_DIR/.build/release/VoiceAI"
        ;;
    run)
        build_swift
        echo "Starting Voice AI..."
        "$SWIFT_DIR/.build/release/VoiceAI" &
        echo "Voice AI running (PID=$!)."
        ;;
    --verify|verify)
        build_swift
        verify
        ;;
    --backend|backend)
        ensure_docker
        compose up -d --build
        echo "Docker backend starting: $APP_URL"
        ;;
    --install|install)
        install_native
        ;;
    --stop|stop)
        pkill -f VoiceAI 2>/dev/null || true
        if command -v docker >/dev/null 2>&1; then
            compose down 2>/dev/null || true
        fi
        echo "Stopped."
        ;;
    *)
        echo "usage: $0 [build|run|--verify|--backend|--install|--stop]" >&2
        exit 2
        ;;
esac
