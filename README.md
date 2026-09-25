# Voice AI

Voice AI is an open-source macOS menu-bar app for local dictation and voice
control. Dictation copies text to the clipboard; a second shortcut lets you
control apps by voice. Speech recognition and desktop decisions run locally.

## What it does

- Native menu-bar app with a compact status popover
- Two customizable global keyboard shortcuts
- Hold-to-talk and start/stop recording modes
- Local Voxtral transcription through Apple MLX
- Dictation copies to the clipboard; voice control uses Accessibility actions
- Local Laya decisions over observed controls; an optional local Bonsai service
  handles complex requests when memory allows
- Accessibility observation with local OCR fallback when an app exposes little text
- Confirmation for clicks except Photo Booth capture, plus recognized risky requests
- Short-lived recording, success, and error overlays
- Optional custom transcription command
- Optional authenticated local Docker backend for status coordination

## Requirements

- macOS 13 or later on Apple silicon for voice control
- Xcode 15 or a compatible Swift toolchain
- Python 3.11 or later
- Microphone and Accessibility permission for `Voice AI.app`
- Screen Recording permission for OCR fallback in apps with sparse Accessibility trees
- Several gigabytes of free space for the local model on first use

## Install

Clone the repository, then run:

```bash
git clone https://github.com/M47HIS/custom-voice-activator.git
cd custom-voice-activator
./script/build_and_run.sh --install
open "/Applications/Voice AI.app"
```

The installer builds the Swift app, creates a private Python environment under
`~/Library/Application Support/VoiceModule/`, installs the worker dependencies,
and copies the signed app to `/Applications`. It prefers Homebrew's pinned
`python@3.11` over whatever `python3` is on `PATH`, so later Homebrew upgrades
do not break the long-running worker.

New installs use the `io.github.M47HIS.VoiceAI` bundle identifier. To preserve
the existing app identity when replacing an older Voice AI build, set
`VOICE_AI_BUNDLE_ID` to the identifier shown in that installed app's
`Contents/Info.plist` before running the installer. macOS may still ask you to
grant permissions again.

The first dictation downloads Voxtral model files. Voice control also needs a
local [Laya multilingual MLX checkpoint](https://huggingface.co/aac6fef/laya-multilingual-mlx),
which is distributed separately under Apache-2.0. Download it outside this
repository, then set its path in `~/.config/voice-module/config.json`:

```bash
"$HOME/Library/Application Support/VoiceModule/venv/bin/huggingface-cli" download \
  aac6fef/laya-multilingual-mlx \
  --local-dir "$HOME/.local/share/voice-ai/models/laya-multilingual-mlx"
```

That directory is Voice AI's default Laya path. You can use another local
checkpoint by setting `laya_model_path` in the config. Click the menu-bar
waveform icon to open Settings or check status.

## Shortcut and output

The default shortcut is `⌘⇧Space`:

1. Hold the shortcut.
2. Speak.
3. Release it.
4. Paste the copied transcript wherever you want.

To change the shortcut, click the menu-bar icon, open Settings, click the
shortcut field, and type a new combination. Saving re-registers it immediately.

Voice AI's dictation mode copies to the clipboard. Voice control never writes
the clipboard. The default voice-control shortcut is `⌃⇧Space`; press it again
to stop listening. Simple commands can be combined with “and then.” Risky
actions wait for a spoken “confirm” or the Confirm button in the menu.

## Configuration

User configuration lives at `~/.config/voice-module/config.json`:

```json
{
  "hotkey": "cmd+shift+space",
  "control_hotkey": "ctrl+shift+space",
  "control_silence_db": -38,
  "mode": "hold",
  "action": "clipboard",
  "transcribe_command": "",
  "language": "en",
  "engine": "voxtral",
  "laya_model_path": "~/.local/share/voice-ai/models/laya-multilingual-mlx"
}
```

Existing `paste_focused` configurations are migrated to `clipboard` when the
worker starts.

The local model path is optional when you use the default directory above.
Model weights, user configuration, recordings, logs, and credentials are not
part of this repository or its MIT license.

### Custom transcription command

Set `transcribe_command` to a command that accepts an audio file and prints the
transcript to standard output. `{file}` is replaced with the recording path:

```json
{
  "transcribe_command": "whisper {file} --model base.en --output_format txt"
}
```

You can also set `TRANSCRIBE_COMMAND` in the app's launch environment.

## Development

```bash
# Swift tests
swift test --package-path macos/VoiceActivator

# Python syntax checks
PYTHONPYCACHEPREFIX=/private/tmp/voice-module-pycache \
  python3 -m py_compile client/voice_client.py client/control_decider.py backend/main.py backend/transcriber.py

PYTHONPATH=client python3 -m unittest client/test_control_decider.py

# Worker security checks (after installation)
PYTHONPATH=client "$HOME/Library/Application Support/VoiceModule/venv/bin/python3" \
  -m unittest client/test_voice_client_security.py

# Full local verification
./script/build_and_run.sh --verify
```

The main components are:

```text
macos/VoiceActivator/   Native menu-bar app and settings UI
client/voice_client.py  Local transcription worker
client/control_decider.py  Local Laya decisions and bounded Bonsai fallback
backend/                Optional local FastAPI backend
legacy/                 Archived browser-first prototype
script/                 Build, install, and verification commands
```

## Optional backend

The app does not require Docker. For local backend experiments only:

```bash
export VOICE_MODULE_AUTH_TOKEN="$(openssl rand -hex 32)"
docker compose up -d
```

Use the same token in the Python client's local `auth_token` setting or its
`VOICE_MODULE_AUTH_TOKEN` environment variable. The backend never returns the
token from an API and accepts sensitive REST/WebSocket traffic only from an
authenticated loopback client. The generated token is stored in the Docker
volume at `/data/auth_token` with user-only permissions.

Only the clipboard action is accepted from backend configuration. Terminal,
AppleScript, and webhook actions are intentionally outside the remote backend
trust boundary.

The service binds to `127.0.0.1:8080`. Do not expose it to a public network.

## Privacy and permissions

- Voice AI requests Microphone and Accessibility access. Screen Recording is
  needed only when an app exposes too little text through Accessibility; OCR
  runs on your Mac.
- Recordings are temporary and removed after normal transcription. A crash may
  leave a temporary recording behind. Dictation text enters the macOS clipboard.
- Voice control sends the spoken request and observed control labels or OCR
  text to local Laya or Bonsai. Text-field values are withheld from the model;
  labels and OCR text may still contain sensitive information. Web search and
  site-opening commands send the requested query or address to the browser
  destination. A user-configured transcription command may have its own
  network behavior.
- The optional backend keeps recent transcripts in memory for authenticated
  local clients. It does not need to run for the native app.
- Voice control asks for confirmation before clicks except Photo Booth capture,
  model-generated website or text actions, and recognized risky requests.
  Check the target before confirming; this safeguard cannot classify every app action.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidance. Please report
security issues according to [SECURITY.md](SECURITY.md), not in a public issue.

## License

MIT. See [LICENSE](LICENSE).
