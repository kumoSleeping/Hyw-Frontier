from pathlib import Path
import unittest

from hyw_frontier.jina import JinaClient


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


if __name__ == '__main__':
    unittest.main()
