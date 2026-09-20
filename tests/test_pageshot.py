from io import BytesIO
import json
import unittest
from unittest.mock import Mock

from PIL import Image

from hyw_frontier.pageshot import PageshotStore
from hyw_frontier.media import ImagePipeline
from hyw_frontier.media_refs import is_display_url


class FakeJina:
    def pageshot_url(self, item):
        return {
            "url": item["url"],
            "image_url": "https://storage.googleapis.com/example/pageshot.png?sig=1",
        }


class PageshotTests(unittest.TestCase):
    def setUp(self):
        with Image.new("RGB", (800, 2400), "white") as image:
            for y in range(0, 2400, 200):
                image.paste((40 + (y // 200) * 10, 80, 120), (0, y, 800, min(2400, y + 100)))
            with BytesIO() as output:
                image.save(output, "PNG")
                self.raw = output.getvalue()
        self.assets = {}
        self.store = PageshotStore(FakeJina(), fetch_image=lambda url: self.raw)
        self.store.set_asset_sink(self.assets.__setitem__)
        self.addCleanup(self.store.close)

    def test_capture_returns_full_page_attachment_and_crop_registers_final_asset(self):
        events = []
        result, attachments = self.store.run({"url": "https://example.com/article"}, on_event=events.append)
        ended = [e for e in events if e['type'] == 'stage_end']
        self.assertEqual([e['stage'] for e in ended], ['pageshot_generate', 'pageshot_download', 'pageshot_compression'])
        self.assertTrue(all(e['status'] == 'ok' and e['duration_ms'] >= 0 for e in ended))
        self.assertEqual(ended[1]['download_bytes'], len(self.raw))
        self.assertTrue(result["ok"])
        self.assertTrue(result["full_page"])
        self.assertEqual((result["width"], result["height"]), (800, 2400))
        self.assertEqual(attachments[0]["type"], "text")
        self.assertEqual(attachments[1]["type"], "image")
        self.assertEqual(attachments[1]["mimeType"], "image/jpeg")

        cropped, blocks = self.store.run({
            "pageshot_id": result["pageshot_id"],
            "bbox": [0, 200, 800, 1000],
        })
        self.assertTrue(cropped["ok"])
        self.assertTrue(cropped["display_ready"])
        self.assertIn(cropped["display_url"], self.assets)
        self.assertLessEqual(len(self.assets[cropped["display_url"]]), 256 * 1024)
        self.assertEqual(blocks[1]["type"], "image")
        self.assertEqual(blocks[1]["mimeType"], "image/jpeg")
        self.assertTrue(is_display_url(cropped["display_url"]))
        row = cropped['media_images'][0]
        self.assertEqual(row['source_url'], 'https://example.com/article')
        self.assertEqual(row['display_url'], json.loads(blocks[0]['text'])['display_url'])

        history = [{'role': 'toolResult', 'toolName': 'jina_pageshot', 'content': [
            {'type': 'text', 'text': json.dumps(cropped)}, *blocks]}]
        replay = ImagePipeline(history)
        self.addCleanup(replay.close)
        self.assertEqual(replay.assets, self.assets)

    def test_exhausted_budget_skips_download_and_crop(self):
        pipeline = ImagePipeline(max_images=1)
        self.addCleanup(pipeline.close)
        self.store.set_image_budget(pipeline.reserve_tool_image)
        result, _ = self.store.run({'url': 'https://example.com/article'})
        self.assertTrue(result['ok'])
        self.store.fetch_image = Mock(side_effect=AssertionError('must not download'))
        for args in ({'url': 'https://example.com/other'},
                     {'pageshot_id': result['pageshot_id'], 'bbox': [0, 0, 10, 10]}):
            denied, blocks = self.store.run(args)
            self.assertEqual(denied['code'], 'image_budget_exhausted')
            self.assertEqual(blocks, [])
        self.assertFalse(self.assets)

    def test_crop_coordinates_are_validated_against_attachment_dimensions(self):
        result, _ = self.store.run({"url": "https://example.com/article"})
        cropped, blocks = self.store.run({
            "pageshot_id": result["pageshot_id"],
            "bbox": [0, 0, result["width"] + 1, result["height"]],
        })
        self.assertFalse(cropped["ok"])
        self.assertEqual(cropped["code"], "invalid_bbox")
        self.assertEqual(blocks, [])


if __name__ == "__main__":
    unittest.main()
