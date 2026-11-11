# -*- coding: utf-8 -*-
"""Markdown → Qt 富文本的转换规则（纯函数，不依赖 Qt）。"""

from __future__ import annotations

import unittest

from app.widgets import markdown

STYLE = {
    "text": "#1a1a1a",
    "muted": "#6b7280",
    "code_bg": "#f1f3f5",
    "border": "#d9dde1",
}


def render(text: str) -> str:
    return markdown.to_html(text, STYLE)


class MarkdownRenderTest(unittest.TestCase):
    def test_heading_becomes_larger_bold_block(self) -> None:
        html = render("## 本周三件事")
        self.assertIn("font-size:15px", html)
        self.assertIn("font-weight:600", html)
        self.assertIn("本周三件事", html)
        self.assertNotIn("##", html)

    def test_unordered_and_ordered_lists(self) -> None:
        html = render("- 一\n- 二\n\n1. 甲\n2. 乙")
        self.assertIn("<ul", html)
        self.assertIn("<ol", html)
        for word in ("一", "二", "甲", "乙"):
            self.assertIn(word, html)
        self.assertNotIn("- 一", html)

    def test_code_block_keeps_monospace_and_background(self) -> None:
        html = render("```python\nprint('hello world')\n```")
        self.assertIn("background-color:#f1f3f5", html)
        self.assertIn("Consolas", html)
        self.assertIn("print('hello world')", html)
        self.assertNotIn("```", html)

    def test_inline_code_is_protected_from_emphasis(self) -> None:
        """`a*b*c` 里的星号不能被当成斜体标记吃掉。"""
        html = render("`a*b*c`")
        self.assertIn("a*b*c", html)
        self.assertNotIn("<i>", html)

    def test_blockquote_is_marked_and_muted(self) -> None:
        html = render("> 慢一点没关系")
        self.assertIn("▍", html)
        self.assertIn("#6b7280", html)
        self.assertIn("慢一点没关系", html)
        self.assertNotIn("&gt;", html)

    def test_paragraphs_are_separated(self) -> None:
        html = render("第一段\n\n第二段")
        self.assertEqual(html.count("<p "), 2)

    def test_cjk_lines_join_without_space(self) -> None:
        html = render("这一句被拆成\n两行")
        self.assertIn("这一句被拆成两行", html)

    def test_unknown_syntax_is_shown_as_plain_text(self) -> None:
        """认识不了的语法不能把内容吞掉。"""
        html = render("| a | b |\n| --- | --- |")
        self.assertIn("a", html)
        self.assertIn("b", html)

    def test_html_is_escaped(self) -> None:
        html = render("<script>alert(1)</script>")
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_link_shows_label_only(self) -> None:
        html = render("见[文档](https://example.com/x)")
        self.assertIn("文档", html)
        self.assertNotIn("example.com", html)

    def test_empty_text_renders_empty(self) -> None:
        self.assertEqual(render(""), "")
        self.assertEqual(render("   \n\n  "), "")


if __name__ == "__main__":
    unittest.main()
