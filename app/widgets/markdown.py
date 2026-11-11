# -*- coding: utf-8 -*-
"""把 AI 回复里常见的 Markdown 子集转成 Qt 富文本 HTML。

只做「显示得清楚」这一件事：标题、列表、代码块、引用块、段落，
以及行内的粗体、斜体、代码、链接。

刻意不使用 Qt 自带的 markdown→toHtml：它会把代码块的底色、引用块的竖线
和段间距都丢掉，正文挤成一坨。这里输出的是 Qt 富文本引擎能吃下的 HTML
（`<p>` 带 margin / background-color / font-family），转换过程不依赖 Qt，
可以直接用单元测试断言。
"""

from __future__ import annotations

import html as _html
import re

BODY_SIZE = 13
H1_SIZE = 16
H2_SIZE = 15
H3_SIZE = 14
MONO_FAMILY = "Consolas, 'Cascadia Mono', monospace"

_FENCE = re.compile(r"^\s*```([\w+#-]*)\s*$")
_HEADING = re.compile(r"^\s*(#{1,3})\s+(.*)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_UNORDERED = re.compile(r"^\s*[-*+]\s+(.*)$")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

_CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def _escape(text: str) -> str:
    return _html.escape(text, quote=False)


def _inline(text: str, style: dict[str, str]) -> str:
    """处理行内标记：先转义，再按 代码 → 链接 → 粗体 → 斜体 的顺序替换。

    行内代码先换成占位符，避免里面的 `*` 被当成粗体/斜体。
    """
    escaped = _escape(text)
    slots: list[str] = []

    def keep(fragment: str) -> str:
        slots.append(fragment)
        return f"\x00{len(slots) - 1}\x00"

    def code(match: re.Match[str]) -> str:
        return keep(
            f'<span style="font-family:{MONO_FAMILY}; '
            f'background-color:{style["code_bg"]};">{match.group(1)}</span>'
        )

    escaped = _INLINE_CODE.sub(code, escaped)
    escaped = _LINK.sub(lambda m: m.group(1), escaped)
    escaped = _BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", escaped)
    escaped = _ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", escaped)
    for index, fragment in enumerate(slots):
        escaped = escaped.replace(f"\x00{index}\x00", fragment)
    return escaped


def _join(lines: list[str], style: dict[str, str]) -> str:
    """多行合并成一段：中文之间不插空格，英文单词之间插一个空格。"""
    out = ""
    for line in lines:
        piece = _inline(line.strip(), style)
        if not piece:
            continue
        if out and not (_CJK.search(out[-1:]) and _CJK.search(piece[:1])):
            out += " "
        out += piece
    return out


def to_html(text: str, style: dict[str, str]) -> str:
    """Markdown 子集 → Qt 富文本片段。认识不了的语法按纯文本显示，不丢内容。"""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parts: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []
    item_tag = "ul"
    index = 0

    def flush_paragraph() -> None:
        body = _join(paragraph, style)
        paragraph.clear()
        if body:
            parts.append(f'<p style="margin:0 0 9px 0;">{body}</p>')

    def flush_items() -> None:
        if not items:
            return
        block = "".join(f'<li style="margin:0 0 4px 0;">{item}</li>' for item in items)
        parts.append(
            f'<{item_tag} style="margin:0 0 9px 0; padding-left:20px;">{block}</{item_tag}>'
        )
        items.clear()

    def flush_all() -> None:
        flush_paragraph()
        flush_items()

    while index < len(lines):
        line = lines[index]

        fence = _FENCE.match(line)
        if fence:
            flush_all()
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not _FENCE.match(lines[index]):
                code_lines.append(lines[index])
                index += 1
            index += 1  # 跳过收尾的 ```
            body = "<br/>".join(_escape(item) for item in code_lines) or "&nbsp;"
            parts.append(
                '<p style="margin:0 0 9px 0; padding:7px 9px; '
                f"background-color:{style['code_bg']}; font-family:{MONO_FAMILY}; "
                f'font-size:{BODY_SIZE - 1}px;">{body}</p>'
            )
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_all()
            level = len(heading.group(1))
            size = {1: H1_SIZE, 2: H2_SIZE, 3: H3_SIZE}[level]
            top = 0 if not parts else 10
            parts.append(
                f'<p style="margin:{top}px 0 6px 0; font-size:{size}px; '
                f'font-weight:600; color:{style["text"]};">'
                f"{_inline(heading.group(2), style)}</p>"
            )
            index += 1
            continue

        if line.lstrip().startswith(">"):
            flush_all()
            quote: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quote.append(lines[index].lstrip().lstrip(">").strip())
                index += 1
            body = _join(quote, style)
            # Qt 富文本不支持 <p> 上的 border-left / padding，所以用「竖线字符 + 灰色文字」表达引用
            parts.append(
                '<p style="margin:0 0 9px 0;">'
                f'<span style="color:{style["muted"]};">\u258d&nbsp;{body}</span></p>'
            )
            continue

        ordered = _ORDERED.match(line)
        unordered = _UNORDERED.match(line)
        if ordered or unordered:
            flush_paragraph()
            wanted = "ol" if ordered else "ul"
            if items and wanted != item_tag:
                flush_items()
            item_tag = wanted
            items.append(_inline((ordered or unordered).group(1), style))
            index += 1
            continue

        if not line.strip():
            flush_paragraph()
            index += 1
            continue

        paragraph.append(line)
        index += 1

    flush_all()
    return "".join(parts)
