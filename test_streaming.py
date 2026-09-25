import json
import threading
import unittest
from io import BytesIO
from core import LiveTranscriber, StreamCancelled, multipart, make_config


class StreamTransport:
    def __init__(self):
        self.received = bytearray()
        self.audio_arrived = threading.Event()
        self.request = None

    def open(self, request, timeout):
        self.request = request
        for chunk in request.data:
            self.received.extend(chunk)
            if chunk == b'\x12\x34' * 1600:
                self.audio_arrived.set()
        return BytesIO(json.dumps({'text':'Streaming works.', 'llm_response':'Streaming works.'}).encode())


class StreamingTests(unittest.TestCase):
    def test_audio_uploads_before_stop_and_is_byte_exact(self):
        transport = StreamTransport()
        live = LiveTranscriber('test-key', {}, transport)
        chunk = b'\x12\x34' * 1600
        live.write(chunk)
        live.write(chunk)
        self.assertTrue(transport.audio_arrived.wait(2), 'No audio uploaded while recording')
        self.assertFalse(live.done.is_set(), 'Request closed before recording stopped')
        live.write(chunk)
        result = live.finish()
        self.assertEqual(result.text, 'Streaming works.')
        boundary = transport.request.get_header('Content-type').split('boundary=')[1]
        self.assertEqual(bytes(transport.received), multipart(chunk*3, make_config({}), boundary))

    def test_cancelling_does_not_finish_a_normal_transcription(self):
        transport = StreamTransport()
        live = LiveTranscriber('test-key', {}, transport)
        live.write(b'\x12\x34' * 1600)
        live.write(b'\x12\x34' * 1600)
        self.assertTrue(transport.audio_arrived.wait(2))
        live.cancel()
        self.assertTrue(live.done.wait(2))
        self.assertIsInstance(live.error, StreamCancelled)
        self.assertIsNone(live.result)

    def test_short_modifier_chord_never_opens_network_request(self):
        transport = StreamTransport()
        live = LiveTranscriber('test-key', {}, transport)
        live.write(b'\0' * 1280)
        live.cancel()
        self.assertIsNone(transport.request)


if __name__ == '__main__':
    unittest.main()
