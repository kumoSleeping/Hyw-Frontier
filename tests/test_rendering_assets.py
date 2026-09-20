"""Rendering uses only authored, reviewed pictures; failures remain failures."""
from io import BytesIO
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

from PIL import Image
from md2png.fonts import FontSet
from md2png.model import Limits, RenderError as EngineError

from hyw_frontier.api import answer
from hyw_frontier.pillow_card import adapt_answer, parse_protocol, referenced_assets
from hyw_frontier.rendering import CardRenderer, RenderError, engine_error_code, png_size
from hyw_frontier.media_refs import display_url


def jpeg(size=(96, 80), color='orange'):
    with Image.new('RGB', size, color) as image, BytesIO() as output:
        image.save(output, 'JPEG')
        return output.getvalue()


class AssetSelectionTests(unittest.TestCase):
    def test_ast_selection_preserves_urls_bytes_and_ignores_inline_code_and_links(self):
        selected = 'https://example.com/selected.png?x=1&y=2'
        nested = 'https://example.com/nested.png'
        inline = 'https://example.com/inline.png'
        code = 'https://example.com/code.png'
        text = (f'<final_response>\n# 图\n\n![选中]({selected})\n\n'
                f'> ![嵌套]({nested})\n\n正文 ![行内]({inline})\n\n'
                f'```md\n![代码]({code})\n```\n\n[仅链接]({code})\n</final_response>')
        pool = {selected: b'a', nested: b'b', inline: b'c', code: b'd'}
        doc = adapt_answer(parse_protocol(text), {}, Limits(), reading=True, image_urls=set(pool))
        images, icons = referenced_assets(doc, pool, {'https://example.com': b'icon', 'https://unused.test': b'unused'})
        self.assertEqual(images, {selected: b'a', nested: b'b'})
        self.assertEqual(icons, {'https://example.com': b'icon'})
        self.assertEqual(len(pool), 4)

    def test_pageshot_crop_display_url_can_select_registered_asset(self):
        display_url = 'https://example.com/article#hyw-pageshot-crop=abc123'
        pool = {display_url: b'crop-bytes'}
        text = f'<final_response>\n# 截图\n\n![页面局部]({display_url})\n</final_response>'
        doc = adapt_answer(parse_protocol(text), {}, Limits(), reading=True, image_urls=set(pool))
        images, _ = referenced_assets(doc, pool, {})
        self.assertEqual(images, pool)

    def test_internal_images_use_only_registered_bytes_and_are_not_source_links(self):
        known = display_url('one', b'a')
        unknown = display_url('two', b'b')
        text = (f'<final_response>\n# 图\n\n![已审阅]({known})\n\n![未知]({unknown})\n\n'
                f'[内部地址]({known})\n\n[来源](https://example.com/article)\n</final_response>')
        doc = adapt_answer(parse_protocol(text), {}, Limits(), reading=True, image_urls={known})
        images, _ = referenced_assets(doc, {known: b'a'}, {})
        self.assertEqual(images, {known: b'a'})
        self.assertEqual([r.url for r in doc.references], ['https://example.com/article'])

    def test_specific_safe_error_codes(self):
        cases = {'Asset count budget exceeded': 'render_asset_limit',
                 'Combined decoded asset pixel budget exceeded': 'render_asset_pixels',
                 'Canvas pixel/height budget exceeded': 'render_canvas_limit',
                 'Unbreakable content exceeds canvas width budget': 'render_width_limit',
                 'Combined answer node budget exceeded': 'render_structure_limit',
                 'Formula character budget exceeded': 'render_formula_limit',
                 'Markdown exceeds 100000 characters': 'render_text_limit',
                 'Rendering deadline exceeded': 'render_timeout'}
        for message, code in cases.items():
            with self.subTest(code=code):
                error = RenderError(engine_error_code(EngineError(message)))
                self.assertEqual(error.code, code)
                self.assertEqual(error.diagnostics['code'], code)


class RenderAssetIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = CardRenderer()
        cls.fonts = FontSet.bundled()
        cls.raw = jpeg()

    @classmethod
    def tearDownClass(cls):
        cls.renderer.close()

    def render(self, text, pool):
        return self.renderer.render(text, metadata={}, cancel=Event(), font_set=self.fonts, image_assets=pool)

    def test_79_and_600_candidates_render_identically_to_the_two_selected_images(self):
        text = '<final_response>\n# 候选图片回归\n\n正文完整保留。\n\n![一](https://example.com/0.jpg)\n\n![二](https://example.com/1.jpg)\n</final_response>'
        pool = {f'https://example.com/{i}.jpg': self.raw for i in range(600)}
        expected = self.render(text, dict(list(pool.items())[:2]))
        for count in (79, 600):
            with self.subTest(count=count):
                result = self.render(text, dict(list(pool.items())[:count]))
                self.assertEqual(result.png, expected.png)
                self.assertEqual(result.links, expected.links)
                self.assertEqual(result.diagnostics[0]['selected_images'], 2)
                self.assertEqual(result.diagnostics[0]['available_images'], count)
                self.assertGreater(png_size(result.png)[1], 0)
        self.assertEqual(len(pool), 600)

    def test_pageshot_crop_fragment_url_renders_as_final_reply_image(self):
        display_url = 'https://example.com/article#hyw-pageshot-crop=abc123'
        text = f'<final_response>\n# 截图\n\n![页面局部]({display_url})\n</final_response>'
        result = self.render(text, {display_url: self.raw})
        self.assertEqual(result.diagnostics[0]['selected_images'], 1)
        self.assertTrue(result.png)

    def test_internal_display_reference_renders_without_exposing_a_source_link(self):
        reference = display_url('https://example.com/original.jpg', self.raw)
        text = f'<final_response>\n# 配图\n\n![图片]({reference})\n</final_response>'
        result = self.render(text, {reference: self.raw})
        self.assertEqual(result.diagnostics[0]['selected_images'], 1)
        self.assertTrue(result.png)
        self.assertFalse(result.links)

    def test_no_selected_images_and_unused_invalid_assets_do_not_block_text_card(self):
        text = '<final_response>\n# 文字卡片\n\n完整正文。\n</final_response>'
        result = self.render(text, {f'https://example.com/{i}.jpg': b'unused-invalid' for i in range(79)})
        self.assertEqual(result.diagnostics[0]['selected_images'], 0)
        self.assertTrue(result.png)

    def test_genuine_asset_limit_is_reported_and_next_request_still_works(self):
        pool = {f'https://example.com/{i}.jpg': self.raw for i in range(65)}
        text = '<final_response>\n# 多图\n\n' + '\n\n'.join(f'![图]({url})' for url in pool) + '\n</final_response>'
        with self.assertRaises(RenderError) as raised:
            self.render(text, pool)
        self.assertEqual(raised.exception.code, 'render_asset_limit')
        self.assertTrue(self.render('<final_response># 下一次\n\n正常内容</final_response>', {}).png)

    def test_genuine_canvas_limit_preserves_specific_error(self):
        text = '<final_response>\n# 长图\n\n' + '\n\n'.join(f'第{i}段，完整保留正文。' for i in range(400)) + '\n</final_response>'
        with self.assertRaises(RenderError) as raised:
            self.render(text, {})
        self.assertEqual(raised.exception.code, 'render_canvas_limit')

    def test_genuine_decoded_pixel_limit_is_distinct_from_canvas_size(self):
        raw = jpeg((1280, 1280))
        pool = {f'https://example.com/pixels-{i}.jpg': raw for i in range(8)}
        text = '<final_response>\n# 配图\n\n' + '\n\n'.join(f'![图]({url})' for url in pool) + '\n</final_response>'
        with self.assertRaises(RenderError) as raised:
            self.render(text, pool)
        self.assertEqual(raised.exception.code, 'render_asset_pixels')

    def test_cancelled_render_is_not_retried(self):
        cancel = Event(); cancel.set()
        with self.assertRaises(RenderError) as raised:
            self.renderer.render('<final_response>内容</final_response>', metadata={}, cancel=cancel)
        self.assertEqual(raised.exception.code, 'cancelled')


class NoTextFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_reports_render_error_without_sending_text_or_retrying_model(self):
        class Backend:
            home = Path('/unused')
            calls = 0
            def call(self, request):
                self.calls += 1
                return {'role': 'assistant', 'stopReason': 'stop', 'content': [
                    {'type': 'text', 'text': '<final_response>\n# 原答案\n\n不能改为群聊文字。</final_response>'}]}
        backend = Backend()
        events, sent = [], []
        with patch.object(CardRenderer, 'render', side_effect=RenderError('render_canvas_limit')):
            with self.assertRaises(RenderError) as raised:
                await answer('问题', backend=backend, send=sent.append, on_event=events.append,
                             system_prompt='直接回答', search_provider='jina')
        self.assertEqual(raised.exception.code, 'render_canvas_limit')
        self.assertEqual(sent, [])
        self.assertEqual(backend.calls, 1)
        self.assertTrue(any(e['type'] == 'render_error' and e['code'] == 'render_canvas_limit' for e in events))
        self.assertFalse(any(e['type'] == 'render_end' for e in events))


if __name__ == '__main__':
    unittest.main()
