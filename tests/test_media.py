import json
import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier, Event, Lock
import unittest
from unittest.mock import patch

from PIL import Image

from hyw_frontier.media import ImagePipeline, compress
from hyw_frontier.media_refs import is_display_url


def reader_result(count, start=0):
    content = '\n'.join(f'![Image {i}](https://example.com/{i}.png)' for i in range(start, start + count))
    return {'toolName': 'jina_read_url', 'content': [{'type': 'text', 'text': json.dumps({
        'results': [{'ok': True, 'url': 'https://example.com/page', 'title': 'Gallery', 'content': content}]
    })}]}


class MediaBatchTests(unittest.TestCase):
    def setUp(self):
        with Image.new('RGB', (100, 80), 'orange') as image, BytesIO() as output:
            image.save(output, 'PNG')
            self.raw = output.getvalue()
        self.pipeline = ImagePipeline()
        self.addCleanup(self.pipeline.close)

    def test_all_45_images_attached_with_at_most_20_simultaneous_downloads(self):
        active = peak = 0
        lock = Lock()
        first_batch = Barrier(20)

        def download(candidate, cancel, timeout):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                index = int(candidate.url.rsplit('/', 1)[1].split('.')[0])
                if index < 20:
                    first_batch.wait(timeout=5)
                return candidate, self.raw, 'downloaded', 1
            finally:
                with lock:
                    active -= 1

        self.pipeline.max_reader_images = 45
        result = reader_result(45)
        events = []
        with patch('hyw_frontier.media.download', side_effect=download) as fetch:
            timing = self.pipeline.prepare([result], Event(), events.append)
            self.assertEqual(fetch.call_count, 45)
        self.assertEqual(peak, 20)
        self.assertEqual(timing['new_images'], 45)
        self.assertEqual(len(self.pipeline.assets), 45)
        rows = json.loads(result['content'][0]['text'])['media_images']
        self.assertEqual(len(rows), 45)
        self.assertTrue(all(row['status'] == 'ready' for row in rows))
        for row, marker, block in zip(rows, result['content'][1::2], result['content'][2::2]):
            self.assertEqual(json.loads(marker['text'])['url'], row['url'])
            self.assertEqual(json.loads(marker['text'])['display_url'], row['display_url'])
            self.assertTrue(is_display_url(row['display_url']))
            self.assertEqual(self.pipeline.assets[row['display_url']], base64.b64decode(block['data']))
            self.assertEqual(block['type'], 'image')
        self.assertIsNone(events[-1]['round_limit'])

    def test_failure_does_not_skip_later_batches_and_total_budget_still_applies(self):
        self.pipeline.max_reader_images = 1000
        with patch('hyw_frontier.media.download', side_effect=lambda c, cancel, timeout:
                   (c, None, 'download_timeout', 1)) as fetch:
            result = reader_result(605)
            self.pipeline.prepare([result], Event(), lambda _: None)
            self.assertEqual(fetch.call_count, 600)
            self.assertEqual(len(json.loads(result['content'][0]['text'])['media_images']), 600)
            self.pipeline.prepare([reader_result(1, start=700)], Event(), lambda _: None)
            self.assertEqual(fetch.call_count, 600)
        self.assertIn('604.png', result['content'][0]['text'])

    def test_small_image_rejected_but_next_batch_retained_and_history_reused(self):
        with Image.new('RGB', (63, 80), 'orange') as image, BytesIO() as output:
            image.save(output, 'PNG')
            small = output.getvalue()
        result = reader_result(21)
        with patch('hyw_frontier.media.download', side_effect=lambda c, cancel, timeout:
                   (c, small if c.url.endswith('/0.png') else self.raw, 'downloaded', 1)):
            timing = self.pipeline.prepare([result], Event(), lambda _: None)
        self.assertEqual(timing['new_images'], 20)
        rows = json.loads(result['content'][0]['text'])['media_images']
        self.assertEqual(rows[0]['status'], 'compression_rejected')
        self.assertEqual(rows[-1]['status'], 'ready')
        result['role'] = 'toolResult'
        history = ImagePipeline([result])
        self.addCleanup(history.close)
        self.assertEqual(history.assets, self.pipeline.assets)
        with patch('hyw_frontier.media.download') as fetch:
            history.prepare([reader_result(20, start=1)], Event(), lambda _: None)
            fetch.assert_not_called()

    def test_parallel_pageshots_share_budget_with_reader_downloads(self):
        pipeline = ImagePipeline(max_images=3)
        self.addCleanup(pipeline.close)
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(lambda _: pipeline.reserve_tool_image(), range(8))), 3)
        with patch('hyw_frontier.media.download') as fetch:
            pipeline.prepare([reader_result(2)], Event(), lambda _: None)
            fetch.assert_not_called()

    def test_reader_attempts_leave_only_remaining_slots_for_pageshots(self):
        pipeline = ImagePipeline(max_images=3)
        self.addCleanup(pipeline.close)
        with patch('hyw_frontier.media.download', side_effect=lambda c, cancel, timeout:
                   (c, self.raw, 'downloaded', 1)):
            pipeline.prepare([reader_result(2)], Event(), lambda _: None)
        self.assertTrue(pipeline.reserve_tool_image())
        self.assertFalse(pipeline.reserve_tool_image())

    def test_legacy_reader_and_crop_history_preserves_exact_urls(self):
        original = 'https://example.com/image.jpg'
        crop = 'https://example.com/article#hyw-pageshot-crop=abc123'
        raw, _ = compress(self.raw)
        encoded = base64.b64encode(raw).decode()
        history = [{'role': 'toolResult', 'toolName': name, 'content': [
            {'type': 'text', 'text': json.dumps(data)},
            {'type': 'image', 'mimeType': 'image/jpeg', 'data': encoded}]} for name, data in (
                ('jina_read_url', {'media_images': [{'status': 'ready', 'url': original}]}),
                ('jina_pageshot', {'ok': True, 'display_url': crop}))]
        pipeline = ImagePipeline(history)
        self.addCleanup(pipeline.close)
        self.assertEqual(pipeline.assets, {original: raw, crop: raw})

    def test_cancellation_stops_before_next_batch(self):
        cancel = Event()

        def download(candidate, cancel, timeout):
            cancel.set()
            return candidate, None, 'cancelled', 0

        with patch('hyw_frontier.media.download', side_effect=download) as fetch:
            self.pipeline.prepare([reader_result(45)], cancel, lambda _: None)
        self.assertEqual(fetch.call_count, 20)
        self.assertFalse(self.pipeline.assets)


if __name__ == '__main__':
    unittest.main()
