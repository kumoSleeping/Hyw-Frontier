from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio
import json
import unittest
from unittest.mock import patch

from hyw_frontier.agent import SearchAgent
from hyw_frontier.jina import JinaClient
from hyw_frontier.request_log import RequestLog, log_summaries
from hyw_frontier.runtime import build_context, DEFAULT_MODEL
from hyw_frontier.telemetry import redact, stage
from hyw_frontier.tools import ToolRuntime


class TelemetryTests(unittest.TestCase):
    def test_disk_logs_remove_image_payloads_secrets_and_repeated_deltas(self):
        with TemporaryDirectory() as directory:
            log = RequestLog(Path(directory), {'message': 'log check', 'entry': 'test'})
            image = {'type': 'image', 'mimeType': 'image/jpeg', 'data': 'PRIVATE_IMAGE_BYTES'}
            log.write({'type': 'tool_end', 'round': 1, 'id': 'a', 'name': 'crop', 'ok': True, 'duration_ms': 4,
                       'result': {'content': [image, {'type': 'text', 'text': json.dumps({'attachment': image})}]},
                       'api_key': 'SECRET_KEY', 'url': 'https://user:password@example.com/page?token=SECRET_TOKEN'})
            log.write({'type': 'thinking_delta', 'delta': 'PRIVATE_THOUGHT'})
            log.write({'type': 'thinking_end', 'round': 1, 'content': 'PRIVATE_THOUGHT'})
            log.write({'type': 'thinking_replace', 'round': 1, 'content': 'PRIVATE_THOUGHT'})
            log.write({'type': 'model_response', 'round': 1, 'response': {'content': [
                {'type': 'thinking', 'thinking': 'PRIVATE_THOUGHT', 'thinkingSignature': 'SECRET_SIGNATURE'}]}})
            log.write({'type': 'done', 'elapsed_ms': 10})
            log.close()
            saved = log.path.read_text()
            for secret in ('PRIVATE_IMAGE_BYTES', 'PRIVATE_THOUGHT', 'SECRET_KEY', 'SECRET_TOKEN', 'SECRET_SIGNATURE', 'user:password'):
                self.assertNotIn(secret, saved)
            self.assertIn('https://example.com/page', saved)
            self.assertIn('base64_chars_omitted', saved)
            self.assertEqual(redact(redact(image)), redact(image))

    def test_failed_and_cancelled_stages_are_timed_without_exception_contents(self):
        events = []
        for error, status in ((ValueError('secret'), 'error'), (asyncio.CancelledError(), 'cancelled')):
            with self.assertRaises(type(error)):
                with stage(events.append, 'operation'):
                    raise error
            self.assertEqual(events[-1]['status'], status)
            self.assertEqual(events[-1]['stage_id'], events[-2]['stage_id'])
            self.assertGreaterEqual(events[-1]['duration_ms'], 0)
            self.assertNotIn('secret', json.dumps(events))

    def test_summaries_pair_parallel_stages_and_tool_duration(self):
        with TemporaryDirectory() as directory:
            log = RequestLog(Path(directory), {'message': 'parallel'})
            for ident in ('a', 'b'):
                log.write({'type': 'stage_start', 'stage_id': ident, 'stage': 'download', 'elapsed_ms': 1})
            for ident, duration in (('b', 10), ('a', 20)):
                log.write({'type': 'stage_end', 'stage_id': ident, 'stage': 'download', 'duration_ms': duration,
                           'elapsed_ms': duration + 1, 'status': 'ok'})
            log.write({'type': 'tool_start', 'round': 1, 'id': 'tool', 'name': 'reader', 'elapsed_ms': 1})
            log.write({'type': 'tool_end', 'round': 1, 'id': 'tool', 'name': 'reader', 'elapsed_ms': 23,
                       'duration_ms': 20, 'ok': False, 'code': 'timeout'})
            log.write({'type': 'error', 'elapsed_ms': 24})
            log.close()
            result = log_summaries(Path(directory))[0]
            self.assertEqual([s['duration_ms'] for s in result['stages']], [20, 10])
            self.assertEqual(result['tool_calls'][0]['duration_ms'], 20)
            self.assertEqual(result['tool_calls'][0]['code'], 'timeout')

    def test_round_model_and_tool_times_exclude_media_work(self):
        class Backend:
            home = Path('/unused')
            count = 0
            def call(self, request, **kwargs):
                self.count += 1
                content = ([{'type': 'toolCall', 'id': 'read', 'name': 'jina_read_url',
                             'arguments': {'url': 'https://example.com'}}] if self.count == 1 else
                           [{'type': 'text', 'text': 'done'}])
                return {'role': 'assistant', 'content': content, 'stopReason': 'toolUse' if self.count == 1 else 'stop'}
        backend = Backend(); events = []
        with ToolRuntime(JinaClient(backend.home, transport=lambda *_: {'data': {'content': 'page'}}), search_provider='jina') as tools:
            agent = SearchAgent(backend, tools, max_rounds=4, prefetch_icons=False, on_event=events.append)
            try:
                with patch('hyw_frontier.media.ImagePipeline.prepare', return_value={
                        'download_ms': 20000, 'processing_ms': 10000, 'new_images': 0}):
                    agent.run('deepseek', DEFAULT_MODEL, build_context('question', 'prompt'))
                timing = next(e for e in events if e['type'] == 'round_timing' and e['round'] == 1 and e['complete'])
                self.assertEqual(timing['timing_version'], 2)
                self.assertEqual(timing['media_download_ms'], 20000)
                self.assertEqual(timing['image_processing_ms'], 10000)
                self.assertLess(timing['model_ms'], 10000)
                self.assertLess(timing['tool_ms'], 20000)
                self.assertGreaterEqual(next(e for e in events if e['type'] == 'tool_end')['duration_ms'], 0)
            finally:
                agent.release()
