"""Exercise both render functions with fake HTTP responses; no provider calls."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote
from test_audio_response import audio_response

class RenderTests(unittest.TestCase):
    def render(self, module, responses):
        tree = ast.parse((Path(__file__).parent / 'modules' / module / 'voices.py').read_text(encoding='utf-8'))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'render_audio')
        request = SimpleNamespace(post=Mock(side_effect=responses), RequestException=ConnectionError)
        usage = Mock()
        namespace = dict(voice_casting=SimpleNamespace(style_for=lambda _: 0.4), requests=request, _headers=lambda *args: {}, BASE='https://example.invalid', MODEL='test', STYLE_BY_ENERGY={}, VoiceError=RuntimeError, quote=quote, _note_characters=usage, timestamp_audio=audio_response.timestamp_audio, plain_audio=audio_response.plain_audio, mp3_seconds=lambda _: 2, _mp3_seconds=lambda _: 2)
        exec(compile(ast.Module(body=[function], type_ignores=[]), module, 'exec'), namespace)
        return namespace['render_audio'], request.post, usage

    def test_render_paths(self):
        for module in ('radio_promo', 'fan_radio'):
            for alignment in (None, {'character_end_times_seconds': ['invalid']}):
                with self.subTest(module=module, alignment=alignment):
                    response = Mock(status_code=200, json=Mock(return_value={'audio_base64': 'YXVkaW8=', 'alignment': alignment}))
                    render, post, usage = self.render(module, [response])
                    self.assertEqual(render('voice', 'Hello')['audio'], b'audio')
                    self.assertEqual(post.call_count, 1)
                    usage.assert_called_once()

    def test_failures_do_not_rerender(self):
        for module in ('radio_promo', 'fan_radio'):
            for response in (Mock(status_code=200, json=Mock(return_value={'audio_base64': ''})), Mock(status_code=429), Mock(status_code=401), ConnectionError('timeout')):
                with self.subTest(module=module, response=response):
                    render, post, usage = self.render(module, [response])
                    with self.assertRaises(RuntimeError):
                        render('voice', 'Hello')
                    self.assertEqual(post.call_count, 1)

    def test_unsupported_endpoint_fallback(self):
        for module in ('radio_promo', 'fan_radio'):
            with self.subTest(module=module):
                render, post, usage = self.render(module, [Mock(status_code=404), Mock(status_code=200, content=b'audio', headers={'Content-Type': 'audio/mpeg'})])
                self.assertEqual(render('voice', 'Hello')['audio'], b'audio')
                self.assertEqual(post.call_count, 2)
                usage.assert_called_once()

if __name__ == '__main__':
    unittest.main()
