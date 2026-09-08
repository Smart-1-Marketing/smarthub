import base64
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('audio_response', Path(__file__).parent / 'hub/audio_response.py')
audio_response = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio_response)

class AudioResponseTests(unittest.TestCase):
    def response(self, audio=b'example audio', alignment=None):
        return Mock(json=Mock(return_value={'audio_base64': base64.b64encode(audio).decode(), 'alignment': alignment}))

    def test_valid_timing(self):
        result = audio_response.timestamp_audio(self.response(alignment={'character_end_times_seconds': [1.25]}), lambda _: 2, RuntimeError)
        self.assertEqual(result['seconds'], 1.25)
        self.assertTrue(result['measured'])

    def test_bad_timing_preserves_audio(self):
        for alignment in (None, [], 'bad', {'character_end_times_seconds': ['bad']}, {'character_end_times_seconds': [float('nan')]}, {'character_end_times_seconds': [-1]}):
            with self.subTest(alignment=alignment):
                result = audio_response.timestamp_audio(self.response(alignment=alignment), lambda _: 2, RuntimeError)
                self.assertEqual(result, {'audio': b'example audio', 'seconds': 2, 'measured': False})

    def test_invalid_audio_is_rejected(self):
        for payload in (None, [], {}, {'audio_base64': ''}, {'audio_base64': '!!!'}, {'audio_base64': None}):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                audio_response.timestamp_audio(Mock(json=Mock(return_value=payload)), lambda _: 2, RuntimeError)

    def test_plain_response_validation(self):
        for content, mime in ((b'', 'audio/mpeg'), (b'{}', 'application/json'), (b'error', 'text/html')):
            with self.subTest(mime=mime), self.assertRaises(RuntimeError):
                audio_response.plain_audio(Mock(content=content, headers={'Content-Type': mime}), lambda _: 2, RuntimeError)
        result = audio_response.plain_audio(Mock(content=b'audio', headers={'Content-Type': 'audio/mpeg'}), lambda _: 2, RuntimeError)
        self.assertEqual(result['audio'], b'audio')

if __name__ == '__main__':
    unittest.main()
