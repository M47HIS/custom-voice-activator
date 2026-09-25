# Legacy Files

These files are from the pre-v1 architecture (standalone Python app and Docker
browser UI). They are kept for reference but are not part of the current
VoiceActivator.app product.

- `browser-ui/` — static HTML/CSS/JS for the Docker-served browser push-to-talk UI
- `voice_module.py` — standalone Python voice-to-text script (pre-Docker, pre-Swift)
- `setup.sh` — legacy setup script
- `start.sh` — legacy CLI launcher
- `requirements.txt` — legacy standalone dependencies

The current product is the native macOS menu-bar app in `macos/VoiceActivator/`
with the Python worker in `client/voice_client.py`.
