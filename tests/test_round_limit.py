from pathlib import Path
import unittest

from hyw_frontier.agent import SearchAgent
from hyw_frontier.jina import JinaClient
from hyw_frontier.prompt_files import read_prompt
from hyw_frontier.runtime import build_context
from hyw_frontier.tools import ToolRuntime


class RoundLimitTests(unittest.TestCase):
    def test_warning_is_injected_two_rounds_before_limit_once_without_polluting_history(self):
        for limit in (1, 2, 3, 5, 30):
            with self.subTest(limit=limit):
                requests, events = [], []

                class Backend:
                    home = Path('/unused')
                    def call(self, request, **kwargs):
                        requests.append(request['context']['systemPrompt'])
                        index = len(requests)
                        blocks = ([{'type': 'text', 'text': 'done'}] if index == limit else
                                  [{'type': 'toolCall', 'id': str(index), 'name': 'jina_read_url',
                                    'arguments': {'url': 'https://example.com'}}])
                        return {'role': 'assistant', 'content': blocks,
                                'stopReason': 'stop' if index == limit else 'toolUse'}

                backend = Backend()
                client = JinaClient(backend.home, transport=lambda *_: {'data': {'content': 'page'}})
                with ToolRuntime(client, search_provider='jina') as tools:
                    agent = SearchAgent(backend, tools, max_rounds=limit, prefetch_icons=False, on_event=events.append)
                    try:
                        context = build_context('question', 'main prompt')
                        agent.run('deepseek', 'deepseek-flash', context)
                        warning = read_prompt('round_limit.md')
                        threshold = max(1, limit - 2)
                        self.assertEqual([i for i, p in enumerate(requests, 1) if warning in p], list(range(threshold, limit + 1)))
                        self.assertTrue(all(p.count(warning) <= 1 for p in requests))
                        notices = [e for e in events if e['type'] == 'round_limit_warning']
                        self.assertEqual(len(notices), 1)
                        self.assertEqual(notices[0]['round'], threshold)
                        self.assertEqual(context['systemPrompt'], 'main prompt')
                        self.assertFalse(any(warning in str(m) for m in agent.context['messages']))
                    finally:
                        agent.release()


if __name__ == '__main__':
    unittest.main()
