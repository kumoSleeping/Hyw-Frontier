"""CJK emphasis must reach the answer AST without damaging literal syntax."""
import unittest

from markdown_it import MarkdownIt

from hyw_frontier.pillow_card import adapt_answer, parse_protocol
from md2png.cjk_emphasis import cjk_emphasis_plugin
from md2png.model import Limits
from md2png.parser import parse


def spans(blocks):
    for block in blocks:
        yield from block.inlines
        yield from spans(block.children)


class EmphasisRenderingTests(unittest.TestCase):
    def test_screenshot_title_and_trailing_chinese(self):
        text = '- **《IF 01 - MINAMO SHIRASE -》**等外传'
        result = list(spans(parse(text).children))
        self.assertEqual(''.join(s.text for s in result), '《IF 01 - MINAMO SHIRASE -》等外传')
        self.assertEqual(''.join(s.text for s in result if s.style.bold),
                         '《IF 01 - MINAMO SHIRASE -》')
        self.assertFalse(result[-1].style.bold)

    def test_cjk_punctuation_and_mixed_scripts(self):
        for inside in ('《书名》', '“结论”', '「作品」', '结论。', '(English)',
                       '中文！', 'カナ。', '한글.', '𠀀。', '，\ufe00'):
            for marker in ('*', '**', '***'):
                with self.subTest(inside=inside, marker=marker):
                    result = list(spans(parse(f'前{marker}{inside}{marker}后').children))
                    self.assertEqual(''.join(s.text for s in result), f'前{inside}后')
                    self.assertEqual(''.join(s.text for s in result
                                            if s.style.bold or s.style.italic), inside)
                    emphasized = [s for s in result if s.text and (s.style.bold or s.style.italic)]
                    self.assertTrue(all(s.style.bold == (len(marker) >= 2) for s in emphasized))
                    self.assertTrue(all(s.style.italic == (len(marker) != 2) for s in emphasized))

    def test_nested_emphasis_links_and_math(self):
        result = list(spans(parse('前**《外层*斜体*与[链接](https://example.com)》**后 $x^2$').children))
        self.assertEqual(''.join(s.text for s in result if s.style.bold), '《外层斜体与链接》')
        self.assertTrue(next(s for s in result if s.text == '斜体').style.italic)
        self.assertEqual(next(s for s in result if s.text == '链接').style.link, 'https://example.com')
        self.assertEqual([s.text for s in result if s.kind == 'math'], ['x^2'])

    def test_containers_and_answer_adapter(self):
        text = '**《结论》**接正文'
        for template in ('# {}', '- {}', '> {}', '| 列 |\n| --- |\n| {} |',
                         '```summary\n{}\n```', '[{}](https://example.com)'):
            with self.subTest(template=template):
                result = list(spans(parse(template.format(text)).children))
                self.assertEqual(''.join(s.text for s in result if s.style.bold), '《结论》')
        answer = (f'<final_response>\n# {text}\n\n<summary>{text}</summary>\n\n'
                  f'- {text}\n</final_response>')
        doc = adapt_answer(parse_protocol(answer), {}, Limits(), reading=True)
        self.assertEqual(doc.title, '《结论》接正文')
        self.assertEqual(''.join(s.text for s in doc.title_inlines if s.style.bold), '《结论》')
        result = list(spans(b for section in doc.sections for b in section))
        self.assertEqual(''.join(s.text for s in result if s.style.bold), '《结论》《结论》')

    def test_literal_code_escapes_and_unclosed_markers(self):
        for source, expected in ((r'\*\*《书名》\*\*等', '**《书名》**等'),
                                 ('`**《书名》**等`', '**《书名》**等'),
                                 ('**《书名》等', '**《书名》等')):
            with self.subTest(source=source):
                result = list(spans(parse(source).children))
                self.assertEqual(''.join(s.text for s in result), expected)
                self.assertFalse(any(s.style.bold or s.style.italic for s in result))
        self.assertEqual(parse('```text\n**《书名》**等\n```').children[0].text, '**《书名》**等')

    def test_standard_boundaries_and_parser_isolation(self):
        standard = MarkdownIt('commonmark').enable('strikethrough')
        patched = MarkdownIt('commonmark').enable('strikethrough').use(cjk_emphasis_plugin)
        for source in ('**normal** text', 'a**"word"**b', 'a_b_c', 'a__b__c',
                       '前__《书名》__后', '前~~《书名》~~后', '***a** b*',
                       '** 内容 **', '前** 《书名》 **后', '前**\u3000内容\u3000**后',
                       '前**\n内容\n**后', 'a**😀**b', '**a😀**b', 'a**©️**b',
                       '---', '* * *', '**'):
            with self.subTest(source=source):
                self.assertEqual(patched.render(source), standard.render(source))
        self.assertNotEqual(patched.render('**《书名》**等'), standard.render('**《书名》**等'))


if __name__ == '__main__':
    unittest.main()
