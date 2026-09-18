"""Currency labels must survive Markdown parsing alongside real formulas."""
import unittest

from hyw_frontier.pillow_card import adapt_answer, parse_protocol
from md2png.model import Limits
from md2png.parser import parse


def spans(blocks):
    for block in blocks:
        yield from block.inlines
        yield from spans(block.children)


class CurrencyRenderingTests(unittest.TestCase):
    def test_screenshot_currency_quote_is_literal(self):
        text = '中文版写为「智利比索($)–智利、人民币(¥)–中国、哥伦比亚比索($) – 哥伦比亚」，与截图红框位置完全吻合。'
        result = list(spans(parse(text).children))
        self.assertTrue(all(span.kind == 'text' for span in result))
        self.assertEqual(''.join(span.text for span in result), text)

    def test_currency_labels_in_markdown_containers(self):
        for label in ('($)', '（$）', '( $ )', '（ $ ）'):
            for template in ('# {0} / {0}', '- {0} / {0}', '> {0} / {0}',
                             '**{0} / {0}**', '[{0} / {0}](https://example.com)',
                             '| 币种 |\n| --- |\n| {0} / {0} |'):
                with self.subTest(label=label, template=template):
                    result = list(spans(parse(template.format(label)).children))
                    self.assertFalse(any(span.kind == 'math' for span in result))
                    self.assertEqual(''.join(s.text for s in result).count(label), 2)

    def test_currency_and_formulas_can_coexist(self):
        text = r'智利($)，公式 $x^2$，哥伦比亚（$），公式 $\frac{1}{2}$ 和 \(y+1\)；($z$)。'
        result = list(spans(parse(text).children))
        self.assertEqual([s.text for s in result if s.kind == 'math'],
                         ['x^2', r'\frac{1}{2}', 'y+1', 'z'])
        literal = ''.join(s.text for s in result if s.kind == 'text')
        self.assertIn('智利($)', literal)
        self.assertIn('哥伦比亚（$）', literal)

    def test_currency_cannot_close_a_stray_dollar(self):
        text = '$unclosed 智利($)，哥伦比亚($)。'
        result = list(spans(parse(text).children))
        self.assertTrue(all(s.kind == 'text' for s in result))
        self.assertEqual(''.join(s.text for s in result), text)

    def test_code_escaping_prices_and_display_math(self):
        text = '`CLP ($) - Chile`、`COP ($) - Colombia`、\\$5 和 \\$10；$5 与 $10。'
        result = list(spans(parse(text).children))
        self.assertFalse(any(s.kind == 'math' for s in result))
        self.assertEqual([s.text for s in result if s.style.code],
                         ['CLP ($) - Chile', 'COP ($) - Colombia'])
        self.assertEqual(parse('```text\n($) / ($)\n```').children[0].text, '($) / ($)')
        block = parse('$$\nx^2 + y^2 = z^2\n$$').children[0]
        self.assertEqual((block.kind, block.text), ('math', 'x^2 + y^2 = z^2'))

    def test_answer_adapter_preserves_currency_in_title_summary_and_body(self):
        answer = ('<final_response>\n# 币种 ($) / ($)\n\n'
                  '<summary>智利($)，哥伦比亚($)。</summary>\n\n'
                  '- 智利比索($)–智利、人民币(¥)–中国、哥伦比亚比索($)–哥伦比亚\n'
                  '</final_response>')
        doc = adapt_answer(parse_protocol(answer), {}, Limits(), reading=True)
        self.assertEqual(doc.title, '币种 ($) / ($)')
        result = list(spans(block for section in doc.sections for block in section))
        self.assertFalse(any(s.kind == 'math' for s in result))
        self.assertEqual(''.join(s.text for s in result).count('$'), 4)


if __name__ == '__main__':
    unittest.main()
