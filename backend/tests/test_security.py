import asyncio
import io
import json
import subprocess
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import main
from transcriber import Transcriber, TranscriptionError


class FakeUpload:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    async def read(self, _size):
        return next(self._chunks, b"")


class FakeWebSocket:
    def __init__(self, headers, messages=()):
        self.headers = headers
        self.closed = None
        self.accepted = False
        self.sent = []
        self._messages = iter(messages)

    async def close(self, code, reason):
        self.closed = (code, reason)

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(text)

    async def receive(self):
        return next(self._messages)


class SecurityControlsTests(unittest.TestCase):
    def setUp(self):
        main._auth_token = "test-token"

    def test_loopback_host_and_origin_boundary(self):
        self.assertTrue(main._loopback_hostname("127.0.0.1:8080"))
        self.assertTrue(main._allowed_origin("http://localhost:8080"))
        self.assertFalse(main._loopback_hostname("attacker.example"))
        self.assertFalse(main._allowed_origin("https://attacker.example"))

    def test_bearer_auth_rejects_missing_and_wrong_tokens(self):
        with self.assertRaises(HTTPException) as missing:
            main.verify_auth_token(None)
        self.assertEqual(missing.exception.status_code, 401)
        with self.assertRaises(HTTPException) as wrong:
            main.verify_auth_token("Bearer wrong")
        self.assertEqual(wrong.exception.status_code, 403)
        self.assertEqual(main.verify_auth_token("Bearer test-token"), "test-token")

    def test_config_never_returns_the_token(self):
        response = asyncio.run(main.get_config("test-token"))
        self.assertNotIn(b"auth_token", response.body)

    def test_new_auth_token_and_directory_are_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            token_path = data_dir / "auth_token"
            with patch.object(main, "DATA_DIR", data_dir), patch.object(main, "AUTH_TOKEN_PATH", token_path):
                main._store_auth_token("test-token")
            self.assertEqual(token_path.read_text(), "test-token")
            self.assertEqual(stat.S_IMODE(data_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(token_path.stat().st_mode), 0o600)

    def test_remote_actions_are_clipboard_only(self):
        self.assertTrue(main._is_safe_remote_action({"name": "copy", "type": "clipboard", "config": {}}))
        for action_type in ("terminal_command", "open_app", "http_request"):
            self.assertFalse(main._is_safe_remote_action({"name": "unsafe", "type": action_type, "config": {}}))

    def test_sensitive_http_routes_require_auth(self):
        protected = {"/api/actions", "/api/settings", "/api/config", "/api/history", "/api/transcribe"}
        for route in main.app.routes:
            if getattr(route, "path", None) not in protected:
                continue
            dependencies = [dependency.call for dependency in route.dependant.dependencies]
            self.assertIn(main.verify_auth_token, dependencies, route.path)

    def test_oversized_upload_is_rejected_before_transcription(self):
        destination = io.BytesIO()
        upload = FakeUpload([b"1234", b"5"])
        with patch.object(main, "MAX_UPLOAD_BYTES", 4):
            with self.assertRaises(HTTPException) as oversized:
                asyncio.run(main._copy_bounded_upload(upload, destination))
        self.assertEqual(oversized.exception.status_code, 413)

    def test_websocket_rejects_missing_auth_before_accept(self):
        websocket = FakeWebSocket({"host": "127.0.0.1:8080"})
        asyncio.run(main.websocket_endpoint(websocket))
        self.assertFalse(websocket.accepted)
        self.assertEqual(websocket.closed[0], 1008)

    def test_websocket_ignores_malformed_frames_and_stays_connected(self):
        messages = [
            {"text": "not json"},
            {"text": "[1, 2, 3]"},
            {"text": json.dumps({"type": "ping"})},
        ]
        websocket = FakeWebSocket(
            {"host": "127.0.0.1:8080", "authorization": "Bearer test-token"},
            messages=messages,
        )
        asyncio.run(main.websocket_endpoint(websocket))
        self.assertTrue(websocket.accepted)
        self.assertIsNone(websocket.closed)
        self.assertIn(json.dumps({"type": "pong"}), websocket.sent)

    def test_transcript_content_is_not_logged(self):
        secret = "private spoken content"
        websocket = FakeWebSocket(
            {"host": "127.0.0.1:8080", "authorization": "Bearer test-token"},
            messages=[{"text": json.dumps({"type": "transcription", "text": secret})}],
        )
        with patch.object(main.logger, "info") as info:
            asyncio.run(main.websocket_endpoint(websocket))
        self.assertNotIn(secret, str(info.call_args_list))

    def test_custom_transcriber_stderr_is_not_returned(self):
        with patch.dict("os.environ", {"TRANSCRIBE_COMMAND": "transcribe"}):
            transcriber = Transcriber()
        failure = subprocess.CalledProcessError(1, ["transcribe"], stderr="private audio transcript")
        with patch("transcriber.subprocess.run", side_effect=failure):
            with self.assertRaises(TranscriptionError) as raised:
                transcriber.transcribe_file(Path("/tmp/audio.wav"))
        self.assertNotIn("private audio transcript", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
