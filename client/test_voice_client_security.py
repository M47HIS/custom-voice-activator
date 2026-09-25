import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import voice_client


class VoiceClientSecurityTests(unittest.TestCase):
    def test_existing_config_becomes_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"auth_token": "test-token"}))
            path.chmod(0o644)
            with patch.object(voice_client, "CONFIG_PATH", path):
                config = voice_client.load_config()
            self.assertEqual(config["auth_token"], "test-token")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_new_config_and_directory_are_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory) / "config"
            config_path = config_dir / "config.json"
            with patch.object(voice_client, "CONFIG_DIR", config_dir), \
                 patch.object(voice_client, "CONFIG_PATH", config_path):
                voice_client.save_config({"auth_token": "test-token"})
            self.assertEqual(stat.S_IMODE(config_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)

    def test_notification_text_is_an_argument_not_script_source(self):
        text = '\\"\nreturn 42\n--'
        with patch.object(voice_client.subprocess, "run") as run:
            voice_client.notify("Voice AI", text)
        args = run.call_args.args[0]
        self.assertEqual(args[-2:], ["Voice AI", text])
        self.assertNotIn(text, args[2])

    def test_custom_command_stderr_stays_out_of_errors_and_logs(self):
        client = SimpleNamespace(_custom_command="transcribe {file}")
        result = SimpleNamespace(returncode=1, stderr="private audio transcript")
        with patch.object(voice_client.subprocess, "run", return_value=result), \
             patch.object(voice_client.log, "error") as error:
            with self.assertRaisesRegex(RuntimeError, "Transcribe command failed") as raised:
                voice_client.VoiceClient._transcribe_with_command(client, path="/tmp/audio.wav")
        self.assertNotIn("private audio transcript", str(raised.exception) + str(error.call_args_list))


if __name__ == "__main__":
    unittest.main()
