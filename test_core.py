import json
import unittest
import urllib.error
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
from core import DictationError, ENDPOINT, KeyGesture, ModifierGesture, MAX_BYTES, make_config, transcribe


class Reply(BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())


class Transport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return Reply(self.response)


class DictationTests(unittest.TestCase):
    def test_modifier_tap_hold_and_toggle(self):
        g = ModifierGesture()
        self.assertEqual(g.update(True, False, 0, "idle"), "start")
        self.assertIsNone(g.update(False, False, .1, "recording"))
        self.assertIsNone(g.update(True, False, 1, "recording"))
        self.assertEqual(g.update(False, False, 1.1, "recording"), "stop")
        self.assertEqual(g.update(True, False, 2, "idle"), "start")
        self.assertEqual(g.update(False, False, 2.8, "recording"), "stop")

    def test_modifier_chords_cancel_only_their_own_recording(self):
        g = ModifierGesture()
        self.assertEqual(g.update(True, False, 0, "idle"), "start")
        self.assertEqual(g.update(True, True, .1, "recording"), "cancel")
        self.assertIsNone(g.update(False, False, .2, "idle"))
        self.assertIsNone(g.update(True, False, 1, "recording"))
        self.assertIsNone(g.update(True, True, 1.1, "recording"))
        self.assertIsNone(g.update(False, False, 1.2, "recording"))
        self.assertIsNone(g.update(True, True, 2, "idle"))
        self.assertIsNone(g.update(False, False, 2.1, "idle"))
        self.assertIsNone(g.update(True, False, 3, "transcribing"))
        self.assertIsNone(g.update(False, False, 3.1, "idle"))

    def test_exact_endpoint_auth_pcm_and_config_first(self):
        pcm = bytes(range(256)) * 40
        transport = Transport({"text": "um hello", "llm_response": "Hello."})
        result = transcribe(pcm, "test-secret", {"keyterms": "Ada,Ada,Debian"}, transport)
        self.assertEqual(result.text, "Hello.")
        req, timeout = transport.calls[0]
        self.assertEqual(req.full_url, ENDPOINT)
        self.assertEqual(req.get_header("Authorization"), "test-secret")
        self.assertEqual(timeout, 90)
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: " + req.get_header("Content-type").encode() + b"\r\n\r\n" + req.data)
        parts = list(message.iter_parts())
        self.assertEqual([p.get_param("name", header="content-disposition") for p in parts], ["config", "audio"])
        config = json.loads(parts[0].get_payload(decode=True))
        self.assertEqual(config, {"sample_rate": 16000, "channels": 1,
                                  "language_codes": ["en"], "keyterms_prompt": ["Ada", "Debian"]})
        self.assertEqual(parts[1].get_payload(decode=True), pcm)
        self.assertEqual(parts[1].get_content_type(), "audio/pcm")

    def test_rewrite_failure_keeps_verbatim(self):
        for rewrite in [None, "", "   "]:
            result = transcribe(b"\0\0" * 4000, "key", {}, Transport(
                {"text": "Original words.", "llm_response": rewrite, "llm_error": "timeout"}))
            self.assertEqual(result.text, "Original words.")
            self.assertTrue(result.rewrite_failed)

    def test_verbatim_mode(self):
        result = transcribe(b"\0\0" * 4000, "key", {"polished": False}, Transport(
            {"text": "um hello", "llm_response": "Hello."}))
        self.assertEqual(result.text, "um hello")

    def test_limits_prevent_network_request(self):
        transport = Transport({})
        for audio in [b"\0" * 100, b"\0" * (MAX_BYTES + 2), b"\0" * 5001]:
            with self.assertRaises(DictationError):
                transcribe(audio, "key", {}, transport)
        self.assertEqual(transport.calls, [])
        with self.assertRaises(DictationError):
            make_config({"keyterms": ",".join(str(n) for n in range(101))})
        with self.assertRaises(DictationError):
            make_config({"instruction": "é" * 1025})

    def test_auth_and_rate_limit_errors(self):
        for code, phrase in [(404, "API key"), (429, "limit")]:
            error = urllib.error.HTTPError(ENDPOINT, code, "failure", {}, BytesIO(b'{}'))
            with self.assertRaisesRegex(DictationError, phrase):
                transcribe(b"\0\0" * 4000, "test-api-secret", {}, Transport(error))

    def test_invalid_response_is_not_pasted(self):
        with self.assertRaisesRegex(DictationError, "invalid transcript"):
            transcribe(b"\0\0" * 4000, "key", {}, Transport({"llm_response": "invented"}))

    def test_tap_hold_and_repeat(self):
        gate = KeyGesture()
        self.assertEqual(gate.press(0, "idle"), "start")
        self.assertIsNone(gate.press(.1, "recording"))
        self.assertIsNone(gate.release(.2, "recording"))
        self.assertEqual(gate.press(1, "recording"), "stop")
        self.assertIsNone(gate.release(1.1, "idle"))
        self.assertEqual(gate.press(2, "idle"), "start")
        self.assertEqual(gate.release(2.6, "recording"), "stop")
        self.assertIsNone(gate.press(3, "transcribing"))
        self.assertIsNone(gate.release(3.7, "transcribing"))


if __name__ == "__main__":
    unittest.main()
