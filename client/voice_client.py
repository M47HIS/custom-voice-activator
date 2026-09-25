#!/usr/bin/env python3
"""
Voice Module Client — macOS native hotkey + audio capture + Voxtral transcription.

Captures audio via hotkey, transcribes locally using Voxtral (Apple MLX),
then executes configured actions. Connects to the lightweight Docker backend
via WebSocket for coordination, status reporting, and action configuration.

Voxtral runs on Apple Neural Engine — no cloud, no Docker GPU needed.

Usage:
    python3 client/voice_client.py
    python3 client/voice_client.py --hotkey "ctrl+alt+o" --action clipboard
    python3 client/voice_client.py --backend ws://localhost:8080/ws
"""

import argparse
import atexit
import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import tempfile
import time
import wave
from pathlib import Path

# Homebrew may replace the Python stdlib while this long-running worker is
# alive. Hugging Face imports _strptime lazily during model loading, so keep it
# resident from startup instead of relying on a path that may later disappear.
import _strptime  # noqa: F401

import numpy as np

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voice-client")

# ── Dependency checks ──────────────────────────────────────────────────────────
_MISSING: list[str] = []

try:
    import sounddevice as sd  # noqa: F401
except ImportError:
    _MISSING.append("sounddevice")

try:
    from pynput import keyboard as pynput_keyboard  # noqa: F401
except ImportError:
    _MISSING.append("pynput")

try:
    import websocket  # noqa: F401
except ImportError:
    _MISSING.append("websocket-client")

try:
    import requests  # noqa: F401
except ImportError:
    _MISSING.append("requests")

_voxtral_available = True
try:
    from mlx_audio.stt.utils import load as mlx_load
except Exception as e:
    _voxtral_available = False
    log.warning(f"mlx-audio unavailable. Voxtral transcription unavailable: {e}")
    log.warning("Install with: pip install mlx-audio")
    log.warning("Will fall back to backend transcription if available.")

if _MISSING:
    print(f"ERROR: Missing packages: {', '.join(_MISSING)}")
    print("Run: pip3 install -r client/requirements.txt")
    sys.exit(1)

# ── Configuration ──────────────────────────────────────────────────────────────

DEFAULT_CONFIG: dict = {
    "hotkey": "cmd+shift+space",
    "mode": "hold",
    "sample_rate": 16000,
    "language": "en",
    "beep": True,
    "min_duration": 0.3,
    "debug": False,
    "backend_url": "ws://localhost:8080/ws",
    "backend_http": "http://localhost:8080",
    "action": "clipboard",
    "engine": "voxtral",   # voxtral (local MLX) or backend (faster-whisper fallback)
    "auth_token": "",      # optional backend token, provisioned locally
    "transcribe_command": "",  # custom transcription command (overrides voxtral)
}

CONFIG_DIR = Path.home() / ".config" / "voice-module"
CONFIG_PATH = CONFIG_DIR / "config.json"
APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "VoiceModule"
PID_PATH = APP_SUPPORT_DIR / "voice_client.pid"

# Backward compat: existing config keys
CONFIG_MAP = {
    "hotkey": "hotkey",
    "mode": "mode",
    "sample_rate": "sample_rate",
    "language": "language",
    "beep": "beep",
    "min_duration": "min_duration",
    "debug": "debug",
}


def load_config(cli_overrides: dict | None = None) -> dict:
    """Load config from JSON file, creating defaults if missing."""
    if CONFIG_PATH.exists():
        try:
            os.chmod(CONFIG_PATH, 0o600)
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            log.warning(f"Could not parse {CONFIG_PATH}, using defaults.")
            cfg = {}
    else:
        log.info(f"Creating default config at {CONFIG_PATH}")
        merged = {**DEFAULT_CONFIG}
        save_config(merged)
        cfg = {}

    # Map old config keys to new ones (backward compat)
    mapped = {}
    for old_key, new_key in CONFIG_MAP.items():
        if old_key in cfg:
            mapped[new_key] = cfg[old_key]

    # Merge: defaults < file config < CLI overrides
    merged = {**DEFAULT_CONFIG, **cfg, **mapped}
    if cli_overrides:
        merged.update({k: v for k, v in cli_overrides.items() if v is not None})
    if merged.get("action") == "paste_focused":
        log.info("Migrating legacy paste_focused action to clipboard.")
        merged["action"] = "clipboard"
    return merged


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.ftruncate(fd, 0)
        with os.fdopen(fd, "w") as output:
            fd = -1
            json.dump(cfg, output, indent=2)
    finally:
        if fd >= 0:
            os.close(fd)


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def claim_pid_file() -> None:
    """Publish this process so the menu-bar app does not spawn duplicates."""
    if PID_PATH.exists():
        try:
            existing = int(PID_PATH.read_text().strip())
        except (OSError, ValueError):
            existing = 0
        if existing and existing != os.getpid() and _pid_is_running(existing):
            log.error(f"Voice client already running (pid={existing}).")
            sys.exit(1)

    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(f"{os.getpid()}\n")


def clear_pid_file() -> None:
    try:
        if PID_PATH.exists() and PID_PATH.read_text().strip() == str(os.getpid()):
            PID_PATH.unlink()
    except OSError:
        pass


# ── Hotkey parsing ────────────────────────────────────────────────────────────

_MODIFIER_MAP: dict[str, pynput_keyboard.Key] = {
    "cmd": pynput_keyboard.Key.cmd,
    "command": pynput_keyboard.Key.cmd,
    "ctrl": pynput_keyboard.Key.ctrl,
    "control": pynput_keyboard.Key.ctrl,
    "alt": pynput_keyboard.Key.alt,
    "option": pynput_keyboard.Key.alt,
    "shift": pynput_keyboard.Key.shift,
}

_SPECIAL_KEYS: dict[str, pynput_keyboard.Key] = {
    "space": pynput_keyboard.Key.space,
    "tab": pynput_keyboard.Key.tab,
    "enter": pynput_keyboard.Key.enter,
    "return": pynput_keyboard.Key.enter,
    "esc": pynput_keyboard.Key.esc,
    "escape": pynput_keyboard.Key.esc,
    "backspace": pynput_keyboard.Key.backspace,
    "delete": pynput_keyboard.Key.delete,
    "up": pynput_keyboard.Key.up,
    "down": pynput_keyboard.Key.down,
    "left": pynput_keyboard.Key.left,
    "right": pynput_keyboard.Key.right,
    "home": pynput_keyboard.Key.home,
    "end": pynput_keyboard.Key.end,
    "page_up": pynput_keyboard.Key.page_up,
    "page_down": pynput_keyboard.Key.page_down,
}

for _n in range(1, 21):
    _SPECIAL_KEYS[f"f{_n}"] = getattr(pynput_keyboard.Key, f"f{_n}")


def parse_hotkey(hotkey_str: str) -> tuple[set[pynput_keyboard.Key], pynput_keyboard.Key]:
    """Parse 'cmd+shift+space' → (modifiers, trigger)."""
    parts = [p.strip().lower() for p in hotkey_str.split("+")]
    modifiers: set[pynput_keyboard.Key] = set()
    key: pynput_keyboard.Key | None = None

    for part in parts:
        if part in _MODIFIER_MAP:
            modifiers.add(_MODIFIER_MAP[part])
        elif part in _SPECIAL_KEYS:
            key = _SPECIAL_KEYS[part]
        elif len(part) == 1:
            key = pynput_keyboard.KeyCode.from_char(part)
        else:
            raise ValueError(f"Unknown key: '{part}'")

    if key is None:
        raise ValueError(f"No trigger key found in '{hotkey_str}'.")
    if not modifiers:
        raise ValueError(f"No modifiers in '{hotkey_str}'.")
    return modifiers, key


def _normalize_key(key) -> pynput_keyboard.Key | pynput_keyboard.KeyCode:
    mapping = {
        pynput_keyboard.Key.cmd_r: pynput_keyboard.Key.cmd,
        pynput_keyboard.Key.ctrl_r: pynput_keyboard.Key.ctrl,
        pynput_keyboard.Key.alt_r: pynput_keyboard.Key.alt,
        pynput_keyboard.Key.shift_r: pynput_keyboard.Key.shift,
    }
    return mapping.get(key, key)


# ── Audio Recorder ────────────────────────────────────────────────────────────

class AudioRecorder:
    """Captures mono int16 audio from the default mic at a given sample rate."""

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._buffer: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._recording = False
        self._lock = threading.Lock()

    def _callback(self, indata: np.ndarray, frames: int, time_info, status):
        if status:
            log.warning(f"Audio status: {status}")
        with self._lock:
            if self._recording:
                # Convert float32 [-1,1] to int16 and store as bytes
                int_data = (indata[:, 0].clip(-1, 1) * 32767).astype(np.int16)
                self._buffer.append(int_data.tobytes())

    def start(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._buffer.clear()
            self._recording = True

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype=np.float32,
            callback=self._callback,
        )
        try:
            self._stream.start()
        except Exception:
            self._stream.close()
            self._stream = None
            with self._lock:
                self._recording = False
                self._buffer.clear()
            raise

    def stop(self) -> list[bytes]:
        with self._lock:
            self._recording = False

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        with self._lock:
            chunks = list(self._buffer)
            self._buffer.clear()
            return chunks

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording


# ── Voxtral Transcriber ───────────────────────────────────────────────────────

VOXTRAL_MODEL_ID = "mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit"


class VoxtralTranscriber:
    """Local transcription using Voxtral via Apple MLX (Apple Neural Engine).

    Lazy-loads the model on first use. Converts raw PCM int16 chunks
    to a temporary WAV file, then calls model.generate().
    """

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._model = None
        self._loaded = False
        self._load_error: str | None = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """Check if mlx-audio is importable."""
        return _voxtral_available

    def _ensure_loaded(self):
        """Lazy-load the Voxtral model thread-safely."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                log.info(f"Loading Voxtral model: {VOXTRAL_MODEL_ID}")
                log.info("(first run downloads the model — this may take a moment)")
                self._model = mlx_load(VOXTRAL_MODEL_ID)
                self._loaded = True
                log.info("Voxtral model loaded successfully.")
            except Exception as e:
                self._load_error = str(e)
                log.error(f"Voxtral model load failed: {e}")
                raise

    def transcribe(self, audio_bytes: bytes, language: str = "en") -> str:
        """Transcribe raw PCM int16 mono audio bytes.

        Returns the transcribed text string, or empty string if nothing detected.
        """
        if not audio_bytes:
            log.warning("Empty audio buffer — nothing to transcribe.")
            return ""

        self._ensure_loaded()

        # Convert int16 bytes → numpy for duration calculation
        samples = np.frombuffer(audio_bytes, dtype=np.int16)

        duration = len(samples) / self.sample_rate
        log.info(f"Transcribing {duration:.1f}s with Voxtral...")

        # Write to temp WAV file
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="voxtral_")
            os.close(fd)
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(self.sample_rate)
                wf.writeframes(samples.tobytes())

            result = self._model.generate(tmp_path)
            text = result.text.strip()

            if text:
                log.info("Voxtral transcribed %d characters", len(text))
            else:
                log.info("No speech detected by Voxtral.")

            return text

        except Exception as e:
            log.error(f"Voxtral transcription failed: {e}")
            raise
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


# ── WebSocket Backend Client (Lightweight Coordination) ───────────────────────

class BackendClient:
    """Connects to the Voice Module backend via WebSocket for coordination.

    The backend no longer handles transcription — it serves as a status relay,
    action config provider, and history logger. Transcription happens locally
    in the client via Voxtral.
    """

    def __init__(self, ws_url: str, http_url: str, auth_token: str = ""):
        self.ws_url = ws_url
        self.http_url = http_url
        self.auth_token = auth_token
        self.ws: websocket.WebSocketApp | None = None
        self._connected = False
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._ws_thread: threading.Thread | None = None

    def connect(self) -> bool:
        """Connect to the backend WebSocket. Returns True if connected."""
        self._connected = False
        self._ready.clear()

        def on_open(ws):
            log.info("Connected to backend WebSocket")
            with self._lock:
                self._connected = True
            self._ready.set()
            # Register as client
            ws.send(json.dumps({"type": "hello", "role": "client"}))

        def on_message(ws, message):
            try:
                data = json.loads(message)
                msg_type = data.get("type")
                if msg_type == "welcome":
                    log.info(f"Backend welcome: engine={data.get('engine')}")
                elif msg_type == "status":
                    log.debug(f"Server state: {data.get('state')}")
                elif msg_type == "pong":
                    pass
            except json.JSONDecodeError:
                log.debug("Non-JSON WS message")

        def on_error(ws, error):
            log.error(f"WebSocket error: {error}")
            with self._lock:
                self._connected = False
            self._ready.set()

        def on_close(ws, close_status_code, close_msg):
            log.info(f"WebSocket closed: {close_status_code}")
            with self._lock:
                self._connected = False
            self._ready.set()

        self.ws = websocket.WebSocketApp(
            self.ws_url,
            header=[f"Authorization: Bearer {self.auth_token}"],
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        self._ws_thread = threading.Thread(
            target=lambda: self.ws.run_forever(ping_interval=30, ping_timeout=10),
            daemon=True,
        )
        self._ws_thread.start()

        # Wait for connection
        if not self._ready.wait(timeout=10):
            log.error("Timed out connecting to backend")
            return False

        time.sleep(0.3)
        return self._connected

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected and self.ws is not None

    def send_message(self, data: dict) -> None:
        """Send a JSON message via WebSocket."""
        if self.is_connected():
            with self._lock:
                if self.ws:
                    self.ws.send(json.dumps(data))

    def send_status(self, state: str) -> None:
        """Send a status update to the backend."""
        self.send_message({"type": "status", "state": state})

    def send_transcription(self, text: str) -> None:
        """Send a transcription result to the backend for history/logging."""
        self.send_message({
            "type": "transcription",
            "text": text,
            "is_final": True,
        })

    def check_backend(self) -> bool:
        """Check if the backend HTTP server is reachable."""
        try:
            resp = requests.get(f"{self.http_url}/api/status", timeout=3)
            return resp.status_code == 200
        except requests.RequestException:
            return False


# ── Action Runner ─────────────────────────────────────────────────────────────

class ActionRunner:
    """Executes configured actions with transcribed text."""

    BUILTIN_ACTIONS: list[dict] = [
        {
            "name": "clipboard",
            "description": "Copy transcribed text to clipboard",
            "type": "clipboard",
            "config": {},
        },
    ]

    def __init__(self, http_url: str, auth_token: str = ""):
        self.http_url = http_url
        self._actions: list[dict] = []
        self.auth_token = auth_token

    @property
    def _auth_headers(self) -> dict:
        """Headers to include in authenticated mutation requests."""
        if self.auth_token:
            return {"Authorization": f"Bearer {self.auth_token}"}
        return {}

    def fetch_actions(self) -> list[dict]:
        """Fetch actions from the backend API."""
        try:
            resp = requests.get(
                f"{self.http_url}/api/actions",
                headers=self._auth_headers,
                timeout=5,
            )
            if resp.status_code == 200:
                self._actions = [
                    action for action in resp.json()
                    if isinstance(action, dict) and action.get("type") == "clipboard"
                ]
                return self._actions
        except requests.RequestException as e:
            log.warning(f"Could not fetch actions from backend: {e}")
        return self._actions

    def get_action(self, name: str) -> dict | None:
        """Get a specific action by name, falling back to built-in actions."""
        for action in self.BUILTIN_ACTIONS:
            if action["name"] == name:
                return action
        for action in self._actions:
            if action.get("name") == name and action.get("type") == "clipboard":
                return action
        return None

    def execute(self, action: dict, text: str) -> bool:
        """Execute an action with the transcribed text. Returns True on success."""
        action_type = action.get("type", "")

        log.info(f"Executing action '{action.get('name')}' (type={action_type})")

        try:
            if action_type == "clipboard":
                return self._copy_clipboard(text)
            else:
                log.error(f"Unknown action type: {action_type}")
                return False
        except Exception as e:
            log.error(f"Action execution failed: {e}")
            return False

    def _copy_clipboard(self, text: str) -> bool:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        log.info("Text copied to clipboard")
        notify("Voice Module", "Text copied to clipboard")
        return True

# ── Helpers ───────────────────────────────────────────────────────────────────

def beep() -> None:
    subprocess.run(["osascript", "-e", "beep"], capture_output=True)


def notify(title: str, text: str = "") -> None:
    script = 'on run argv\n  display notification (item 2 of argv) with title (item 1 of argv)\nend run'
    subprocess.run(["osascript", "-e", script, title, text], capture_output=True)


# ── Voice Client ──────────────────────────────────────────────────────────────

class VoiceClient:
    """macOS client: hotkey → mic → Voxtral transcription → WebSocket → action."""

    def __init__(self, config: dict, cli_overrides: dict | None = None):
        self.cfg = config
        self._cli_overrides = cli_overrides or {}
        self.recorder = AudioRecorder(sample_rate=config["sample_rate"])
        self.backend = BackendClient(
            config["backend_url"],
            config["backend_http"],
            auth_token=config.get("auth_token", ""),
        )
        self.runner = ActionRunner(config["backend_http"], auth_token=config.get("auth_token", ""))

        # Voxtral transcriber (lazy-loaded)
        self._transcriber: VoxtralTranscriber | None = None
        self._use_voxtral = (
            config.get("engine", "voxtral") == "voxtral" and _voxtral_available
        )
        self._custom_command = config.get("transcribe_command", "") or os.getenv("TRANSCRIBE_COMMAND", "").strip()

        self._mods: set[pynput_keyboard.Key] = set()
        self._trigger: pynput_keyboard.Key | None = None
        self._held: set[pynput_keyboard.Key] = set()
        self._trigger_held = False
        self._running = False
        self._listener: pynput_keyboard.Listener | None = None
        self._action: dict | None = None
        self._worker_mode = False

    # ── hotkey state ──────────────────────────────────────────────────────

    def _hotkey_active(self) -> bool:
        if not self._trigger_held:
            return False
        return self._mods.issubset(self._held)

    # ── pynput callbacks ──────────────────────────────────────────────────

    def _on_press(self, key):
        if not self._running:
            return
        norm = _normalize_key(key)
        self._held.add(norm)

        is_trigger = (norm == _normalize_key(self._trigger)) or (key == self._trigger)

        was_trigger_held = self._trigger_held

        if is_trigger:
            self._trigger_held = True

        if self._hotkey_active():
            if not self.recorder.is_recording:
                self._start_recording()
            elif self.cfg["mode"] == "toggle" and not was_trigger_held:
                # Only toggle on fresh press (not macOS key repeat while held)
                self._stop_recording()

    def _on_release(self, key):
        if not self._running:
            return
        norm = _normalize_key(key)
        self._held.discard(norm)

        is_trigger = (norm == _normalize_key(self._trigger)) or (key == self._trigger)
        if is_trigger:
            self._trigger_held = False

        if self.cfg["mode"] == "hold":
            if self.recorder.is_recording and not self._hotkey_active():
                self._stop_recording()

    # ── recording lifecycle ───────────────────────────────────────────────

    def _start_recording(self):
        try:
            self.recorder.start()
            if self.cfg["beep"]:
                threading.Thread(target=beep, daemon=True).start()
            if self.backend.is_connected():
                self.backend.send_status("listening")
            self._emit_worker_event({"type": "status", "state": "listening"})
            notify("Voice Module", "Recording... (release to transcribe)")
            log.info("Recording started")
        except Exception as e:
            log.error(f"Recording start error: {e}")
            self._emit_worker_event({"type": "error", "message": str(e)})
            notify("Voice Module Error", str(e))

    def _stop_recording(self):
        log.info("Recording stopped")
        audio_chunks = self.recorder.stop()

        duration = sum(len(c) for c in audio_chunks) / 2 / self.cfg["sample_rate"]
        if duration < self.cfg["min_duration"]:
            log.info(f"Recording too short ({duration:.1f}s) — skipped.")
            notify("Voice Module", "Recording too short — skipped.")
            if self.backend.is_connected():
                self.backend.send_status("idle")
            self._emit_worker_event({"type": "status", "state": "idle"})
            return

        log.info(f"Captured ~{duration:.1f}s, transcribing...")
        notify("Voice Module", "Transcribing...")
        self._emit_worker_event({"type": "status", "state": "transcribing"})

        if self.backend.is_connected():
            self.backend.send_status("transcribing")

        text: str | None = None

        if self._custom_command:
            # ── Custom command transcription ──
            try:
                raw = b"".join(audio_chunks)
                text = self._transcribe_with_command(raw)
                if self.backend.is_connected():
                    if text:
                        self.backend.send_transcription(text)
                    else:
                        self.backend.send_message({
                            "type": "transcription",
                            "text": "",
                            "is_final": True,
                            "empty": True,
                        })
            except Exception as e:
                log.error(f"Custom command transcription failed: {e}")
                notify("Voice Module Error", f"Transcription failed: {e}")
                if self.backend.is_connected():
                    self.backend.send_status("idle")
                self._emit_worker_event({"type": "status", "state": "idle"})
                self._emit_worker_event({"type": "error", "message": f"Transcription failed: {e}"})
                return
        elif self._use_voxtral:
            # ── Voxtral local transcription ──
            try:
                if self._transcriber is None:
                    self._transcriber = VoxtralTranscriber(
                        sample_rate=self.cfg["sample_rate"]
                    )
                raw = b"".join(audio_chunks)
                text = self._transcriber.transcribe(raw, language=self.cfg["language"])

                # Send result to backend for history/logging
                if self.backend.is_connected():
                    if text:
                        self.backend.send_transcription(text)
                    else:
                        self.backend.send_message({
                            "type": "transcription",
                            "text": "",
                            "is_final": True,
                            "empty": True,
                        })
            except Exception as e:
                log.error(f"Voxtral transcription failed: {e}")
                notify("Voice Module Error", f"Transcription failed: {e}")
                if self.backend.is_connected():
                    self.backend.send_status("idle")
                self._emit_worker_event({"type": "status", "state": "idle"})
                self._emit_worker_event({"type": "error", "message": f"Transcription failed: {e}"})
                return
        else:
            # ── Backend fallback (legacy faster-whisper) ──
            log.warning("Voxtral not available — using backend transcription fallback.")
            log.warning("Install mlx-audio for local transcription: pip install mlx-audio")
            text = self._transcribe_via_backend(audio_chunks)

        if self.backend.is_connected():
            self.backend.send_status("idle")
        self._emit_worker_event({"type": "status", "state": "idle"})

        if text is None:
            log.error("Transcription failed")
            self._emit_worker_event({"type": "error", "message": "Transcription failed"})
            notify("Voice Module Error", "Transcription failed")
            return

        if text:
            log.info("Transcribed %d characters", len(text))
            self._emit_worker_event({"type": "transcribed", "text": text})
            notify("Voice Module", "Transcription ready")

            # Execute the configured action
            action_name = self.cfg.get("action", "clipboard")
            action = self.runner.get_action(action_name)

            if action is None:
                log.warning(f"Action '{action_name}' not found on backend. Using fallback clipboard.")
                ok = self.runner._copy_clipboard(text)
            else:
                ok = self.runner.execute(action, text)
            self._emit_worker_event({"type": "action_done", "action": action_name, "ok": bool(ok)})
        else:
            log.info("No speech detected")
            self._emit_worker_event({"type": "transcribed", "text": ""})
            notify("Voice Module", "No speech detected.")

    def _transcribe_with_command(self, raw_bytes: bytes | None = None, path: str | None = None) -> str:
        """Run a custom transcription command. Accepts raw bytes or a file path."""
        tmp_path = None
        try:
            if path:
                file_path = path
            else:
                fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="cmd_")
                os.close(fd)
                with wave.open(tmp_path, "wb") as wf:
                    samples = np.frombuffer(raw_bytes, dtype=np.int16)
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self.cfg["sample_rate"])
                    wf.writeframes(samples.tobytes())
                file_path = tmp_path

            cmd = self._custom_command
            if "{file}" in cmd:
                cmd = cmd.replace("{file}", file_path)
            else:
                cmd = f"{cmd} {file_path}"

            parts = shlex.split(cmd)
            result = subprocess.run(parts, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                log.error("Transcribe command failed (rc=%d).", result.returncode)
                raise RuntimeError("Transcribe command failed.")
            return result.stdout.strip()
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _transcribe_via_backend(self, audio_chunks: list[bytes]) -> str | None:
        """Backend audio transcription is not supported by the headless API."""
        log.error("Backend transcription is unavailable in the current headless backend.")
        log.error("Install/fix mlx-audio for local Voxtral transcription.")
        notify("Voice Module Error", "Backend transcription is unavailable. Fix mlx-audio.")
        return None

    def _ensure_auth_token(self) -> bool:
        """Load a locally provisioned backend token without network bootstrap."""
        token = (
            self.cfg.get("auth_token", "").strip()
            or os.getenv("VOICE_MODULE_AUTH_TOKEN", "").strip()
        )
        if not token:
            log.warning("No backend auth token configured; optional backend features are disabled.")
            return False
        self.cfg["auth_token"] = token
        self.backend.auth_token = token
        self.runner.auth_token = token
        return True

    # ── run / shutdown ────────────────────────────────────────────────────

    def _fetch_backend_settings(self):
        """Fetch settings from backend and merge, respecting CLI overrides.

        Priority: CLI overrides > backend settings > local config file > defaults.
        """
        try:
            resp = requests.get(
                f"{self.cfg['backend_http']}/api/settings",
                headers=self.runner._auth_headers,
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                for key in ("hotkey", "mode"):
                    if key in data and key not in self._cli_overrides:
                        old = self.cfg.get(key)
                        self.cfg[key] = data[key]
                        log.info(f"[settings] {key}: {old!r} → {data[key]!r} (from backend)")
                    elif key in data and key in self._cli_overrides:
                        log.info(
                            f"[settings] {key}: backend value {data[key]!r} ignored "
                            f"(CLI override: {self._cli_overrides[key]!r})"
                        )
                    elif key not in data:
                        source = "CLI" if key in self._cli_overrides else "local config"
                        log.info(
                            f"[settings] {key}: not in backend response, "
                            f"using {self.cfg.get(key)!r} (from {source})"
                        )
            else:
                log.warning(f"Backend returned {resp.status_code} for settings")
        except requests.RequestException as e:
            log.info(f"Backend settings unavailable — using local config ({e})")
        except Exception as e:
            log.warning(f"Unexpected error fetching backend settings: {e}")

    def _prepare_runtime(self, interactive: bool) -> bool:
        log.info(f"Hotkey from config: {self.cfg['hotkey']}")
        log.info(f"Engine: {'Voxtral (local MLX)' if self._use_voxtral else 'Backend fallback'}")
        if self._custom_command:
            log.info("Custom transcribe command configured")
        log.info(f"Backend: {self.cfg['backend_url']}")
        log.info(f"Action: {self.cfg.get('action', 'clipboard')}")
        log.info(f"Mic:    {self._mic_name()}")

        backend_available = True

        if not self.backend.check_backend():
            if self._worker_mode:
                log.warning("Backend is not running — worker will operate without it.")
                backend_available = False
            else:
                log.warning("Backend is not running!")
                log.warning("Start it with: docker compose up -d")
                log.warning("Or: cd backend && uvicorn main:app --host 127.0.0.1 --port 8080")
                if not interactive:
                    self._emit_worker_event({"type": "error", "message": "Backend is not running"})
                    return False
                ans = input("Continue without backend? Action config will be unavailable. [y/N] ")
                if ans.lower() != "y":
                    log.info("Exiting. Start the backend and try again.")
                    return False

        if backend_available and not self._ensure_auth_token():
            backend_available = False

        if backend_available and not self.backend.connect():
            if self._worker_mode:
                log.warning("Failed to connect to backend WebSocket — worker will operate without it.")
                backend_available = False
            else:
                log.error("Failed to connect to backend WebSocket.")
                if not interactive:
                    self._emit_worker_event({"type": "error", "message": "Failed to connect to backend WebSocket"})
                    return False
                ans = input("Continue anyway? [y/N] ")
                if ans.lower() != "y":
                    return False

        if backend_available:
            self.runner.fetch_actions()
            log.info(f"Loaded {len(self.runner._actions)} actions from backend")
            self._fetch_backend_settings()
        else:
            log.info("Backend unavailable — using built-in actions only.")

        return True

    def run(self):
        if not self._prepare_runtime(interactive=True):
            sys.exit(1)

        # Parse hotkey (after potential backend override)
        try:
            self._mods, self._trigger = parse_hotkey(self.cfg["hotkey"])
        except ValueError as e:
            log.error(f"Invalid hotkey: {e}")
            sys.exit(1)

        mod_names = [str(m).replace("Key.", "") for m in self._mods]
        trigger_name = str(self._trigger).replace("Key.", "")
        log.info(f"Active hotkey: {'+'.join(mod_names)}+{trigger_name}")
        log.info(f"Active mode: {self.cfg['mode']}")

        # Signal handlers
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        log.info("Voice Module ready. Hold hotkey to record, release to transcribe.")
        log.info("Press Ctrl+C to stop.\n")

        self._running = True
        self._listener = pynput_keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()

        while self._running and self._listener.is_alive():
            self._listener.join(timeout=0.5)

        self.shutdown()

    def run_worker(self):
        self._worker_mode = True
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        if not self._prepare_runtime(interactive=False):
            sys.exit(1)

        self._running = True
        self._emit_worker_event({"type": "ready"})
        log.info("Voice Module worker ready.")

        while self._running:
            line = sys.stdin.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError as e:
                self._emit_worker_event({"type": "error", "message": f"Invalid JSON command: {e}"})
                continue

            command = message.get("type")
            if command == "start_recording":
                if not self.recorder.is_recording:
                    self._start_recording()
            elif command == "stop_recording":
                if self.recorder.is_recording:
                    self._stop_recording()
            elif command == "toggle_recording":
                if self.recorder.is_recording:
                    self._stop_recording()
                else:
                    self._start_recording()
            elif command == "shutdown":
                self._running = False
            elif command == "transcribe_file":
                path = message.get("path", "")
                log.info(f"Received transcribe_file: {path}")
                self._handle_transcribe_file(path, mode=message.get("mode", "dictation"))
            elif command == "decide_control":
                try:
                    from control_decider import decide
                    action = decide(message.get("text", ""), message.get("scene", []))
                    self._emit_worker_event({"type": "control_decision", "action": action})
                except Exception as e:
                    log.error("Control decision failed: %s", type(e).__name__)
                    self._emit_worker_event({"type": "error", "message": f"Voice control unavailable: {e}"})
            else:
                self._emit_worker_event({"type": "error", "message": f"Unknown command: {command}"})

        self.shutdown()

    def _emit_worker_event(self, event: dict) -> None:
        if not self._worker_mode:
            return
        print(json.dumps(event, ensure_ascii=False), flush=True)

    def _handle_transcribe_file(self, path: str, mode: str = "dictation") -> None:
        """Transcribe a WAV file recorded by the Swift audio recorder."""
        if not path or not os.path.exists(path):
            log.error(f"File not found: {path}")
            self._emit_worker_event({"type": "error", "message": "Recording file was not found", "mode": mode})
            return

        try:
            with wave.open(path, "rb") as wf:
                nchannels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()
                raw_bytes = wf.readframes(wf.getnframes())
            log.info(
                f"WAV: {nchannels}ch, {sampwidth * 8}-bit, "
                f"{framerate}Hz, {len(raw_bytes)} bytes"
            )
        except Exception as e:
            log.error(f"Failed to read WAV file: {e}")
            self._emit_worker_event({"type": "error", "message": "Could not read recording", "mode": mode})
            return

        duration = len(raw_bytes) / (sampwidth * max(framerate, 1) * max(nchannels, 1))
        if duration < self.cfg["min_duration"]:
            log.info(f"Recording too short ({duration:.1f}s) — skipped.")
            self._emit_worker_event({"type": "status", "state": "idle"})
            if mode == "control":
                self._emit_worker_event({"type": "transcribed", "text": "", "mode": mode})
            self._cleanup_wav(path)
            return

        log.info(f"Duration: {duration:.1f}s, transcribing...")
        self._emit_worker_event({"type": "status", "state": "transcribing"})

        text: str | None = None
        if self._custom_command:
            try:
                text = self._transcribe_with_command(path=path)
            except Exception as e:
                log.error("Custom command transcription failed")
                self._emit_worker_event({"type": "error", "message": "Custom transcription failed", "mode": mode})
                self._emit_worker_event({"type": "status", "state": "idle"})
                self._cleanup_wav(path)
                return
        elif self._use_voxtral:
            try:
                if self._transcriber is None:
                    if mode == "control":
                        from control_decider import free_memory_percent
                        if free_memory_percent() < 25:
                            raise RuntimeError("Not enough free memory to load Voxtral safely")
                    self._emit_worker_event({"type": "model_loading"})
                    self._transcriber = VoxtralTranscriber(
                        sample_rate=self.cfg["sample_rate"]
                    )
                text = self._transcriber.transcribe(raw_bytes, language=self.cfg["language"])
            except Exception as e:
                log.error(f"Transcription failed: {e}")
                self._emit_worker_event({"type": "error", "message": f"Transcription failed: {e}", "mode": mode})
                self._emit_worker_event({"type": "status", "state": "idle"})
                self._cleanup_wav(path)
                return
        else:
            log.error("Voxtral not available")
            self._emit_worker_event({"type": "error", "message": "Voxtral not available", "mode": mode})
            self._emit_worker_event({"type": "status", "state": "idle"})
            self._cleanup_wav(path)
            return

        self._emit_worker_event({"type": "status", "state": "idle"})

        if text is None:
            self._emit_worker_event({"type": "error", "message": "Transcription failed", "mode": mode})
            self._cleanup_wav(path)
            return

        if text:
            log.info("Transcribed %d characters", len(text))
            self._emit_worker_event({"type": "transcribed", "text": text, "mode": mode})

            if mode == "dictation":
                action_name = self.cfg.get("action", "clipboard")
                action = self.runner.get_action(action_name)
                if action is None:
                    log.warning(
                        f"Action '{action_name}' not found. Using fallback clipboard."
                    )
                    ok = self.runner._copy_clipboard(text)
                else:
                    ok = self.runner.execute(action, text)
                self._emit_worker_event(
                    {"type": "action_done", "action": action_name, "ok": bool(ok)}
                )
        else:
            log.info("No speech detected")
            self._emit_worker_event({"type": "transcribed", "text": "", "mode": mode})

        self._cleanup_wav(path)
        log.info("transcribe_file complete")

    @staticmethod
    def _cleanup_wav(path: str) -> None:
        try:
            os.remove(path)
            log.info(f"Cleaned up temp WAV: {path}")
        except OSError:
            pass

    def _on_signal(self, signum, frame):
        log.info("\nShutting down...")
        self._running = False
        # Let the main loop exit cleanly — don't sys.exit() here.

    def shutdown(self):
        self._running = False
        if self.recorder.is_recording:
            self.recorder.stop()
        if self._listener and self._listener.is_alive():
            self._listener.stop()
        log.info("Voice Module client stopped.")

    @staticmethod
    def _mic_name() -> str:
        try:
            dev = sd.query_devices(kind="input")
            return dev.get("name", "unknown")
        except Exception:
            return "unknown"


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Voice Module Client — macOS voice-to-text with Voxtral (Apple MLX)."
    )
    parser.add_argument("--hotkey", help="Override hotkey (e.g., cmd+shift+space)")
    parser.add_argument("--mode", choices=["hold", "toggle"],
                        help="'hold' (push-to-talk) or 'toggle'")
    parser.add_argument("--backend", help="WebSocket URL (default: ws://localhost:8080/ws)")
    parser.add_argument("--backend-http", help="HTTP URL (default: http://localhost:8080)")
    parser.add_argument("--action", help="Action name to execute on transcription")
    parser.add_argument("--engine", choices=["voxtral", "backend"],
                        help="Transcription engine (default: voxtral)")
    parser.add_argument("--debug", action="store_true", help="Verbose output")
    parser.add_argument("--list-devices", action="store_true",
                        help="List audio devices and exit")
    parser.add_argument("--worker", action="store_true",
                        help="Run as a JSON-command worker supervised by the macOS app")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.list_devices:
        print("Audio input devices:")
        try:
            devices = sd.query_devices()
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    print(f"  [{i}] {d['name']}")
        except Exception as e:
            log.error(f"Could not query audio devices: {e}")
            sys.exit(1)
        return

    overrides = {
        "hotkey": args.hotkey,
        "mode": args.mode,
        "backend_url": args.backend,
        "backend_http": args.backend_http,
        "action": args.action,
        "engine": args.engine,
        "debug": args.debug,
    }
    cli_overrides = {k: v for k, v in overrides.items() if v is not None}
    config = load_config(cli_overrides)

    claim_pid_file()
    atexit.register(clear_pid_file)

    client = VoiceClient(config, cli_overrides)
    try:
        if args.worker:
            client.run_worker()
        else:
            client.run()
    finally:
        clear_pid_file()


if __name__ == "__main__":
    main()
