import json
from io import BytesIO
from threading import Barrier, Event, Lock
import unittest
from unittest.mock import patch

from PIL import Image

from hyw_frontier.media import ImagePipeline


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
        with patch('hyw_frontier.media.download') as fetch:
            history.prepare([reader_result(20, start=1)], Event(), lambda _: None)
            fetch.assert_not_called()

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
