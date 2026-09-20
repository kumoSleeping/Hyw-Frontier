"""Image references survive complete agent turns and share a bounded budget."""
from io import BytesIO
from contextlib import closing
import json
from pathlib import Path
from threading import Event
import unittest

from PIL import Image

from hyw_frontier.agent import SearchAgent
from hyw_frontier.jina import JinaClient
from hyw_frontier.rendering import CardRenderer
from hyw_frontier.runtime import build_context
from hyw_frontier.tools import ToolRuntime


class ScreenshotBackend:
    home = Path('/unused')

    def __init__(self):
        self.step = 0
        self.reference = None

    def call(self, request, **kwargs):
        self.step += 1
        latest = request['context']['messages'][-1]
        data = json.loads(latest['content'][0]['text']) if latest['role'] == 'toolResult' else {}
        if self.step == 1:
            block = {'type': 'toolCall', 'id': 'capture', 'name': 'jina_pageshot',
                     'arguments': {'url': 'https://example.com/article'}}
        elif self.step == 2 and data.get('ok'):
            block = {'type': 'toolCall', 'id': 'crop', 'name': 'jina_pageshot',
                     'arguments': {'pageshot_id': data['pageshot_id'], 'bbox': [0, 0, 100, 100]}}
        else:
            self.reference = data.get('display_url', self.reference)
            body = f'![页面局部]({self.reference})' if self.reference else '图片额度不足，依据已有资料回答。'
            block = {'type': 'text', 'text': f'<final_response>\n# 页面\n\n{body}\n</final_response>'}
        return {'role': 'assistant', 'content': [block],
                'stopReason': 'toolUse' if block['type'] == 'toolCall' else 'stop'}


class ImageFlowTests(unittest.TestCase):
    def test_budget_and_crop_reuse_across_complete_turns(self):
        with Image.new('RGB', (200, 400), 'orange') as image, BytesIO() as output:
            image.save(output, 'PNG')
            raw = output.getvalue()
        for budget in (0, 1, 2):
            with self.subTest(budget=budget):
                backend, events = ScreenshotBackend(), []
                client = JinaClient(backend.home, transport=lambda *_: {})
                client.pageshot_url = lambda item: {**item, 'image_url': 'https://example.com/shot.png'}
                with ToolRuntime(client, search_provider='jina') as tools:
                    tools.pageshots.fetch_image = lambda _: raw
                    agent = SearchAgent(backend, tools, max_rounds=4, max_tool_images=budget,
                                        prefetch_icons=False, on_event=events.append)
                    try:
                        answer = agent.run('deepseek', 'deepseek-flash', build_context('截图并展示局部', 'prompt'))
                        history = agent.context['messages']
                        image_count = sum(b['type'] == 'image' for m in history if m['role'] == 'toolResult'
                                          for b in m['content'])
                        self.assertEqual(image_count, budget)
                        assets = dict(agent.image_assets)
                        # Per-task full shots are released before rendering; crops remain in assets.
                        self.assertFalse(tools.pageshots._shots)
                        for event in events:
                            if event['type'] == 'tool_end':
                                for block in event['result']['content']:
                                    if block['type'] == 'image':
                                        self.assertNotIn('data', block)
                    finally:
                        agent.release()
                # A fresh runtime mirrors the server's next user turn.
                with ToolRuntime(JinaClient(backend.home, transport=lambda *_: {}), search_provider='jina') as tools:
                    following = SearchAgent(backend, tools, max_rounds=4, max_tool_images=budget, prefetch_icons=False)
                    try:
                        following.run('deepseek', 'deepseek-flash', build_context('再次展示', 'prompt', history))
                        self.assertEqual(following.image_assets, assets)
                        if budget == 2:
                            with closing(CardRenderer()) as renderer:
                                result = renderer.render(answer['content'][0]['text'], metadata={}, cancel=Event(),
                                                         image_assets=following.image_assets, max_tool_images=budget)
                            self.assertEqual(result.diagnostics[0]['selected_images'], 1)
                            self.assertTrue(result.png)
                    finally:
                        following.release()


if __name__ == '__main__':
    unittest.main()
