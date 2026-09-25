#!/usr/bin/env python3
"""
Voice Module for OpenCode — macOS always-on voice-to-text hotkey trigger (legacy).

This is the legacy standalone module using faster-whisper for transcription.
For the recommended setup, use client/voice_client.py which uses Voxtral
(Apple MLX / Apple Neural Engine) for faster, more accurate transcription.

Note: This legacy module does not support backend settings synchronization.
Use client/voice_client.py for the full-featured client that syncs hotkey,
mode, and action settings from the Voice Module backend dashboard.

Legacy usage:
    python3 voice_module.py
    python3 voice_module.py --model tiny.en --mode toggle

Requires macOS Accessibility + Microphone permissions (see README.md).
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

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
    from faster_whisper import WhisperModel  # noqa: F401
except ImportError:
    _MISSING.append("faster-whisper")

if _MISSING:
    print(f"ERROR: Missing packages: {', '.join(_MISSING)}")
    print("Run: pip3 install sounddevice pynput faster-whisper")
    print("Or:  bash setup.sh")
    sys.exit(1)

# ── Configuration ──────────────────────────────────────────────────────────────

DEFAULT_CONFIG: dict[str, any] = {
    "hotkey": "cmd+shift+space",
    "mode": "hold",               # "hold" (push-to-talk) or "toggle"
    "whisper_model": "tiny.en",   # tiny.en, base.en, small.en, etc.
    "whisper_device": "auto",     # "auto" / "cpu" / "cuda"
    "whisper_compute": "auto",    # "auto" → int8 on ARM, else "default"
    "sample_rate": 16000,
    "language": "en",
    "beep": True,
    "min_duration": 0.3,          # seconds — skip if recording shorter
    "debug": False,
}

CONFIG_DIR = Path.home() / ".config" / "voice-module"
CONFIG_PATH = CONFIG_DIR / "config.json"


def load_config(cli_overrides: dict | None = None) -> dict:
    """Load config from JSON file, creating defaults if missing. CLI overrides win."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, PermissionError):
            print(f"Warning: could not parse {CONFIG_PATH}, using defaults.")
            cfg = {}
    else:
        # First run — create config file with defaults
        print(f"Creating default config at {CONFIG_PATH}")
        save_config(DEFAULT_CONFIG)
        cfg = {}

    merged = {**DEFAULT_CONFIG, **cfg}
    if cli_overrides:
        merged.update({k: v for k, v in cli_overrides.items() if v is not None})
    return merged


def save_config(cfg: dict) -> None:
    """Write config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


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

# Populate F1-F20
for _n in range(1, 21):
    _SPECIAL_KEYS[f"f{_n}"] = getattr(pynput_keyboard.Key, f"f{_n}")


def parse_hotkey(hotkey_str: str) -> tuple[set[pynput_keyboard.Key], pynput_keyboard.Key]:
    """
    Parse a hotkey string like 'cmd+shift+space' into (modifiers, trigger_key).
    Raises ValueError on failure.
    """
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
        raise ValueError(
            f"No trigger key found in '{hotkey_str}'. "
            f"Format: modifier+modifier+key (e.g., cmd+shift+space)."
        )
    if not modifiers:
        raise ValueError(
            f"No modifiers in '{hotkey_str}'. At least one modifier required "
            f"(cmd, ctrl, alt, shift)."
        )

    return modifiers, key


def _normalize_key(key) -> pynput_keyboard.Key | pynput_keyboard.KeyCode:
    """Map left/right modifier variants to their base Key."""
    mapping = {
        pynput_keyboard.Key.cmd_r: pynput_keyboard.Key.cmd,
        pynput_keyboard.Key.ctrl_r: pynput_keyboard.Key.ctrl,
        pynput_keyboard.Key.alt_r: pynput_keyboard.Key.alt,
        pynput_keyboard.Key.shift_r: pynput_keyboard.Key.shift,
    }
    return mapping.get(key, key)


# ── Audio Recorder ────────────────────────────────────────────────────────────

class AudioRecorder:
    """Captures mono float32 audio from the default mic at a given sample rate."""

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._buffer: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._recording = False
        self._lock = threading.Lock()

    def _callback(self, indata: np.ndarray, frames: int, time_info, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        with self._lock:
            if self._recording:
                self._buffer.append(indata.copy())

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

    def stop(self) -> np.ndarray:
        with self._lock:
            self._recording = False

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        with self._lock:
            audio = np.concatenate(self._buffer) if self._buffer else np.array([], dtype=np.float32)
            self._buffer.clear()
            return audio

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording


# ── Transcriber ───────────────────────────────────────────────────────────────

class Transcriber:
    """Local Whisper transcription via faster-whisper (CTranslate2)."""

    def __init__(self, model_name: str = "tiny.en", device: str = "auto", compute: str = "auto"):
        if device == "auto":
            device = "cpu"
        if compute == "auto":
            compute = "int8"  # fast path for Apple Silicon; falls back gracefully

        self.model_name = model_name
        print(f"Loading Whisper model '{model_name}' (device={device}, compute={compute})...")
        print("(first run downloads the model — this may take a moment)")

        self._model = WhisperModel(model_name, device=device, compute_type=compute)
        print("Model ready.")

    def transcribe(self, audio: np.ndarray, language: str = "en") -> str:
        """Return transcribed text from a float32 mono audio array."""
        if audio.size == 0:
            return ""

        print("Transcribing...")
        segments, _info = self._model.transcribe(
            audio,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )

        parts = [seg.text.strip() for seg in segments]
        result = " ".join(parts).strip()
        if result:
            print(f"  → \"{result}\"")
        return result


# ── Terminal Launcher ─────────────────────────────────────────────────────────

def launch_opencode(text: str) -> None:
    """Copy text to clipboard, open Terminal.app, run opencode, paste text."""
    if not text.strip():
        print("No text to send — skipping Terminal launch.")
        return

    subprocess.run(["pbcopy"], input=text.encode(), check=True)
    print("Text copied to clipboard.")

    applescript = '''
    tell application "Terminal"
        activate
        do script "opencode"
    end tell
    delay 0.3
    tell application "System Events"
        tell process "Terminal"
            keystroke "v" using command down
            delay 0.1
            keystroke return
        end tell
    end tell
    '''

    try:
        subprocess.run(["osascript", "-e", applescript], check=True)
        print("Launched Terminal with opencode + transcribed text.")
    except subprocess.CalledProcessError as e:
        print(f"Error launching Terminal: {e}")
        print("Text is on clipboard — you can paste it manually (Cmd+V).")


# ── Helpers ───────────────────────────────────────────────────────────────────

def beep() -> None:
    """Play a system beep."""
    subprocess.run(["osascript", "-e", "beep"], capture_output=True)


def notify(title: str, text: str = "") -> None:
    """Show a macOS notification."""
    script = 'on run argv\n  display notification (item 2 of argv) with title (item 1 of argv)\nend run'
    subprocess.run(["osascript", "-e", script, title, text], capture_output=True)


# ── Main Voice Module ─────────────────────────────────────────────────────────

class VoiceModule:
    """Orchestrates hotkey listening, audio recording, transcription, and terminal launch."""

    def __init__(self, config: dict):
        self.cfg = config
        self.recorder = AudioRecorder(sample_rate=config["sample_rate"])
        self.transcriber: Transcriber | None = None
        self._mods: set[pynput_keyboard.Key] = set()           # required modifier set
        self._trigger: pynput_keyboard.Key | None = None        # the non-modifier key
        self._held: set[pynput_keyboard.Key] = set()            # currently held (normalized)
        self._trigger_held = False
        self._running = False
        self._listener: pynput_keyboard.Listener | None = None
        self._model_load_thread: threading.Thread | None = None

    # ── hotkey state ──────────────────────────────────────────────────────

    def _hotkey_active(self) -> bool:
        """True when all required modifiers AND the trigger key are held."""
        if not self._trigger_held:
            return False
        return self._mods.issubset(self._held)

    # ── pynput callbacks ──────────────────────────────────────────────────

    def _on_press(self, key):
        if not self._running:
            return
        norm = _normalize_key(key)
        self._held.add(norm)

        # Track trigger key separately
        is_trigger = (norm == _normalize_key(self._trigger))
        is_trigger |= (key == self._trigger)  # exact match for KeyCode

        was_trigger_held = self._trigger_held

        if is_trigger:
            self._trigger_held = True

        if self._hotkey_active():
            if not self.recorder.is_recording:
                self._start_recording()
            elif self.cfg["mode"] == "toggle" and not was_trigger_held:
                # Only toggle on fresh press (not key repeat while held)
                self._stop_recording()

    def _on_release(self, key):
        if not self._running:
            return
        norm = _normalize_key(key)
        self._held.discard(norm)

        is_trigger = (norm == _normalize_key(self._trigger))
        is_trigger |= (key == self._trigger)

        if is_trigger:
            self._trigger_held = False

        # In hold mode: stop when hotkey is no longer active
        if self.cfg["mode"] == "hold":
            if self.recorder.is_recording and not self._hotkey_active():
                self._stop_recording()

    # ── recording lifecycle ───────────────────────────────────────────────

    def _start_recording(self):
        try:
            self.recorder.start()
            if self.cfg["beep"]:
                threading.Thread(target=beep, daemon=True).start()
            notify("Voice Module", "Recording... (release hotkey to stop)")
            print("🎤  Recording started — release hotkey to transcribe.")
        except Exception as e:
            print(f"Recording error: {e}", file=sys.stderr)
            notify("Voice Module Error", str(e))

    def _stop_recording(self):
        print("🎤  Recording stopped.")
        audio = self.recorder.stop()

        duration = len(audio) / self.cfg["sample_rate"]
        if duration < self.cfg["min_duration"]:
            print(f"Recording too short ({duration:.1f}s < {self.cfg['min_duration']}s) — skipped.")
            notify("Voice Module", "Recording too short — skipped.")
            return

        print(f"Captured {duration:.1f}s of audio.")

        # Lazy-load model if not loaded yet
        if self.transcriber is None:
            try:
                self._load_model()
            except Exception as e:
                print(f"Model load error: {e}", file=sys.stderr)
                notify("Voice Module Error", f"Model load failed: {e}")
                return

        try:
            notify("Voice Module", "Transcribing…")
            text = self.transcriber.transcribe(audio, language=self.cfg["language"])
        except Exception as e:
            print(f"Transcription error: {e}", file=sys.stderr)
            notify("Voice Module Error", f"Transcription failed: {e}")
            return

        if text:
            print(f"✅  \"{text}\"")
            notify("Voice Module", "Transcription ready")
            launch_opencode(text)
        else:
            print("No speech detected.")
            notify("Voice Module", "No speech detected.")

    def _load_model(self):
        """Load the Whisper model (can be called from background thread)."""
        self.transcriber = Transcriber(
            model_name=self.cfg["whisper_model"],
            device=self.cfg["whisper_device"],
            compute=self.cfg["whisper_compute"],
        )

    # ── run / shutdown ────────────────────────────────────────────────────

    def run(self):
        # Parse hotkey
        try:
            self._mods, self._trigger = parse_hotkey(self.cfg["hotkey"])
        except ValueError as e:
            print(f"Invalid hotkey: {e}", file=sys.stderr)
            sys.exit(1)

        mod_names = [str(m).replace("Key.", "") for m in self._mods]
        trigger_name = str(self._trigger).replace("Key.", "")
        print(f"Hotkey: {'+'.join(mod_names)}+{trigger_name}")
        print(f"Mode:   {self.cfg['mode']}")
        print(f"Model:  {self.cfg['whisper_model']}")
        print(f"Mic:    {self._mic_name()}")
        print()

        # Preload model in background
        self._model_load_thread = threading.Thread(target=self._load_model, daemon=True)
        self._model_load_thread.start()

        # Signal handlers for clean shutdown
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        print("Voice Module ready. Hold hotkey to record, release to transcribe.")
        print("Press Ctrl+C to stop.\n")

        self._running = True

        self._listener = pynput_keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()

        # Wait for listener thread
        while self._running and self._listener.is_alive():
            self._listener.join(timeout=0.5)

        self.shutdown()

    def _on_signal(self, signum, frame):
        print("\nShutting down...")
        self._running = False
        # Let the main loop exit cleanly — don't sys.exit() here.

    def shutdown(self):
        self._running = False
        if self.recorder.is_recording:
            self.recorder.stop()
        if self._listener and self._listener.is_alive():
            self._listener.stop()
        print("Voice Module stopped.")

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
        description="Voice Module for OpenCode — macOS voice-to-text hotkey trigger."
    )
    parser.add_argument("--hotkey", help="Override hotkey (e.g., cmd+shift+space)")
    parser.add_argument("--mode", choices=["hold", "toggle"],
                        help="'hold' (push-to-talk) or 'toggle'")
    parser.add_argument("--model", help="Whisper model (tiny.en, base.en, small.en, etc.)")
    parser.add_argument("--language", help="Language code (default: en)")
    parser.add_argument("--debug", action="store_true", help="Verbose output")
    parser.add_argument("--list-devices", action="store_true",
                        help="List audio devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        print("Audio input devices:")
        try:
            devices = sd.query_devices()
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    print(f"  [{i}] {d['name']}")
        except Exception as e:
            print(f"Could not query audio devices: {e}", file=sys.stderr)
            sys.exit(1)
        return

    overrides = {
        "hotkey": args.hotkey,
        "mode": args.mode,
        "whisper_model": args.model,
        "language": args.language,
        "debug": args.debug,
    }
    config = load_config({k: v for k, v in overrides.items() if v is not None})

    module = VoiceModule(config)
    module.run()


if __name__ == "__main__":
    main()
