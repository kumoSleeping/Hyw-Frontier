from email.message import Message
from io import BytesIO
import json
from pathlib import Path
import unittest
from urllib.error import HTTPError

from hyw_frontier.jina import JinaClient, request_pageshot_url


class ReaderTests(unittest.TestCase):
    def test_reader_engine_default_omits_override_and_both_use_markdown(self):
        for mode in ('default', 'browser'):
            seen = []
            def transport(endpoint, body, headers):
                seen.append(headers)
                return {'data': {'content': '# markdown'}}
            client = JinaClient(Path('/unused'), transport=transport, reader_engine=mode)
            self.addCleanup(client.close)
            client.read_url({'url': 'https://example.com'})
            self.assertEqual(seen[0].get('X-Engine'), 'browser' if mode == 'browser' else None)
            self.assertEqual(seen[0]['X-Respond-With'], 'markdown')

    def test_pageshot_uses_return_format_header_and_returns_redirect_url(self):
        class Opener:
            request = None

            def open(self, request, timeout):
                self.request = request
                headers = Message()
                headers['Location'] = 'https://storage.googleapis.com/example/pageshot.png?sig=1'
                raise HTTPError(request.full_url, 302, 'Found', headers, BytesIO(b''))

        opener = Opener()
        result = request_pageshot_url('https://example.com/page?x=1', opener=opener)
        headers = {key.lower(): value for key, value in opener.request.header_items()}
        self.assertEqual(headers['x-return-format'], 'pageshot')
        self.assertEqual(headers['x-engine'], 'browser')
        self.assertEqual(json.loads(opener.request.data), {'url': 'https://example.com/page?x=1'})
        self.assertEqual(result, 'https://storage.googleapis.com/example/pageshot.png?sig=1')


if __name__ == '__main__':
    unittest.main()
