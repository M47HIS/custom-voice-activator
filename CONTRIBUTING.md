# Contributing to Voice AI

Thanks for helping improve Voice AI. Keep changes small, local-first, and
consistent with the native macOS interaction model.

## Development setup

Requirements:

- macOS 13 or later on Apple silicon
- Xcode 15 or a compatible Swift toolchain
- Python 3.11 or later

```bash
git clone https://github.com/M47HIS/custom-voice-activator.git
cd custom-voice-activator
python3 -m venv .venv
.venv/bin/pip install -r client/requirements.txt
swift test --package-path macos/VoiceActivator
```

Run the development build with:

```bash
./script/build_and_run.sh --install
```

## Project boundaries

- `macos/VoiceActivator/` is the primary product surface.
- `client/voice_client.py` is the bundled local transcription worker.
- `backend/` and Docker are optional integrations.
- `legacy/` is archived and should not drive the current architecture.
- Keep the backend bound to `127.0.0.1`.
- Keep audio and transcription local by default.
- Dictation uses the clipboard; voice control uses observed Accessibility
  controls and bounded local actions. Require confirmation for risky actions.

## Before opening a pull request

Run:

```bash
swift test --package-path macos/VoiceActivator
PYTHONPYCACHEPREFIX=/private/tmp/voice-module-pycache \
  python3 -m py_compile client/voice_client.py client/control_decider.py backend/main.py backend/transcriber.py
PYTHONPATH=client python3 -m unittest client/test_control_decider.py
PYTHONPATH=client "$HOME/Library/Application Support/VoiceModule/venv/bin/python3" \
  -m unittest client/test_voice_client_security.py
./script/build_and_run.sh --verify
```

Then confirm:

- User-facing behavior is documented.
- New settings survive relaunch.
- Errors are visible but transient.
- No credentials, model files, audio recordings, local config, or logs are
  included.
- The menu-bar app still works without Docker.

## Pull requests

Create a focused branch from `main`, explain the user-visible result, and list
the verification you ran. Screenshots are welcome for UI changes, but do not
include personal content or transcripts.

Use clear commit messages such as:

```text
feat: add shortcut capture feedback
fix: clear transient worker errors
docs: clarify local installation
```

## Security

Do not open a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md).
