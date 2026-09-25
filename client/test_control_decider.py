import io
import json
import unittest
from unittest.mock import patch

import control_decider


class ControlDeciderTests(unittest.TestCase):
    def test_bonsai_decision_is_marked_as_model_generated(self):
        content = json.dumps({"kind": "open_url", "value": "https://example.com", "model_generated": False})
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
        with patch.object(control_decider, "free_memory_percent", return_value=70), \
             patch.object(control_decider.urllib.request, "urlopen", return_value=response):
            action = control_decider._bonsai("open something complex", [])
        self.assertTrue(action["model_generated"])

    def test_laya_model_path_can_be_configured_without_a_private_checkout(self):
        with patch.dict("os.environ", {"VOICE_AI_LAYA_MODEL": "~/voice-ai-test-model"}):
            self.assertEqual(control_decider._model_path().name, "voice-ai-test-model")

    def test_reel_commands_and_memory_gate(self):
        # The narrow classifier sometimes confuses domain names and "note".
        intents = iter([
            "new_document", "new_document", "none", "open_app",
            "none", "open_app", "open_app", "none", "none",
        ])
        def choose(_state, name, _criteria, _instructions):
            return "0" if name == "target" else next(intents)
        with patch.object(control_decider, "_choice", side_effect=choose):
            scene = [{"app": "Photo Booth"}, {"id": 0, "role": "AXButton", "title": "Take Photo"}]
            commands = [
                "create a new note", "put Hello in the new note", "Great",
                "open Arc", "search Google for Norbert Wiener", "open X.com",
                "open Photo Booth", "take a picture", "Cool, awesome, thank you",
            ]
            kinds = [control_decider.decide(command, scene)["kind"] for command in commands]
        self.assertEqual(kinds, [
            "new_document", "type_text", "none", "open_app",
            "open_url", "open_url", "open_app", "click", "none",
        ])
        with patch.object(control_decider, "_choice", return_value="complex"):
            with patch.object(control_decider, "free_memory_percent", return_value=10):
                with self.assertRaisesRegex(RuntimeError, "free memory"):
                    control_decider.decide("organize these windows", [])


if __name__ == "__main__":
    unittest.main()
