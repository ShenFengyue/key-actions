# -*- coding: utf-8 -*-
"""Typora 式 Markdown 即时预览编辑控件。

思路（单栏混合编辑）：
- 真相是一份无损的「分段列表」：按空行把源码切成 block / sep 交替的段，
  围栏代码块里的空行不切。所有段文本用 "\\n" 拼回去与原文完全一致。
- 光标所在的那一段显示源码（可直接编辑），其余 block 段实时渲染成富文本，
  sep 段就是空行。
- 文档里每个 QTextBlock 用 userState 记下自己属于哪一段，光标移动时据此
  判断「是不是离开了当前编辑段」；离开了就把当前段的最新文本收割回分段
  列表，重新切段、重建文档，再按全局字符偏移把光标放回去。
- 打字本身不触发重建（只在同一段内移动光标），所以输入是流畅的。

实现上的关键保证：先把整份源码用 insertText 铺成纯文本文档（此时文档文本
== 源码，逐段记下精确字符区间），再**从后往前**把非编辑段用 insertHtml
替换成渲染结果——倒序替换不会影响前面段的区间，边界零误差。

跨段的结构性破坏（全选后打字、跨段粘贴等）无法无损收割时，发 structureBroken
信号，由笔记页回退到「源码」模式兜底，绝不悄悄丢内容。
"""

from __future__ import annotations

import re

from PySide6.QtCore import Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextEdit

from .. import theme
from . import markdown

_FENCE = re.compile(r"^\s*```([\w+#-]*)\s*$")
_PREFIX = re.compile(r"^\s*(?:#{1,3}\s+|[-*+]\s+|\d+[.)]\s+|>\s?)")


def split_segments(text: str) -> list[dict]:
    """把源码切成 block / sep 交替的段；围栏内的空行不算分隔。"""
    if text == "":
        return [{"kind": "block", "text": ""}]
    segments: list[dict] = []
    buf: list[str] = []
    kind: str | None = None
    in_fence = False

    def flush() -> None:
        nonlocal buf
        if buf:
            segments.append({"kind": kind, "text": "\n".join(buf)})
            buf = []

    for line in text.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
        is_blank = not line.strip() and not in_fence
        want = "sep" if is_blank else "block"
        if kind is not None and want != kind:
            flush()
        kind = want
        buf.append(line)
    flush()
    return segments


def join_segments(segments: list[dict]) -> str:
    return "\n".join(seg["text"] for seg in segments)


class MarkdownLiveEdit(QTextEdit):
    """光标所在块显示源码，其余块即时渲染的 Markdown 编辑器。"""

    textEdited = Signal()        # 用户改动了内容（供「未保存」提示）
    structureBroken = Signal()   # 跨段结构被破坏，请回退源码模式

    def __init__(self, body_size: int = 15, parent=None) -> None:
        super().__init__(parent)
        self._body_size = int(body_size)
        self._segments: list[dict] = [{"kind": "block", "text": ""}]
        self._active = 0
        self._rebuilding = False
        self._dirty = False
        self._last_remap: dict[int, int] = {}
        self.setAcceptRichText(False)
        self.textChanged.connect(self._on_text_changed)
        self.cursorPositionChanged.connect(self._on_cursor_moved)

    # ------------------------------------------------------------ 对外接口
    def set_text(self, text: str) -> None:
        """载入一份源码（视为加载，不算修改）。"""
        self._segments = split_segments(text or "")
        self._active = next(
            (i for i, s in enumerate(self._segments) if s["kind"] == "block"), 0)
        self._dirty = False
        self._rebuild(place=(self._active, 0))

    def source_text(self) -> str:
        """当前完整源码：把编辑段的最新文本收割回来再拼。"""
        harvested, merged_sep = self._harvest_active()
        if harvested is not None:
            self._segments[self._active]["text"] = harvested
        offset = self._global_offset()
        if self._reconcile_seps(merged_sep):
            # 空行数对不上（退格 / 删除吃掉了空行）：重建一次让标记归位
            self._active, local = self._locate(self._segments, offset)
            self._rebuild(place=(self._active, local))
        return join_segments(self._segments)

    def toPlainText(self) -> str:  # noqa: N802 - 与 Qt 同名，返回的必须是源码
        return self.source_text()

    def is_modified(self) -> bool:
        return self._dirty

    def clear_modified(self) -> None:
        self._dirty = False

    def set_body_size(self, px: int) -> None:
        self._body_size = int(px)
        self._rebuild(place=(self._active, self._local_offset()))

    # ------------------------------------------------------------ 收割
    def _block_list(self) -> list:
        out = []
        block = self.document().firstBlock()
        while block.isValid():
            out.append(block)
            block = block.next()
        return out

    def _harvest_active(self) -> tuple[str | None, int | None]:
        """把编辑段（含用户分裂出的 -1 块）的当前文本读回来。

        返回 (文本, 被并入的空行段下标)；文本为 None 表示结构被破坏。
        """
        blocks = self._block_list()
        tags = [b.userState() for b in blocks]
        start = next((i for i, t in enumerate(tags) if t == self._active), None)
        merged_sep = None
        if start is None:
            # 退格把编辑段并进了相邻空行：空行段里出现非空文本就是它
            sep_idx = {i for i, s in enumerate(self._segments) if s["kind"] == "sep"}
            start = next((i for i, t in enumerate(tags)
                          if t in sep_idx and blocks[i].text().strip()), None)
            if start is None:
                return None, None
            merged_sep = tags[start]
        lines: list[str] = []
        i = start
        while i < len(blocks) and tags[i] in (self._active, -1, tags[start]):
            lines.append(blocks[i].text())
            i += 1
        return "\n".join(lines), merged_sep

    def _reconcile_seps(self, merged_sep: int | None) -> bool:
        """按文档里实际的空行块数校正 sep 段（退格 / 删除会吃掉空行）。

        返回是否发生了改动；改动时同步重映射 _active。
        """
        tags = [b.userState() for b in self._block_list()]
        changed = False
        new_segments: list[dict] = []
        remap: dict[int, int] = {}
        for i, seg in enumerate(self._segments):
            if seg["kind"] == "sep":
                actual = tags.count(i) - (1 if merged_sep == i else 0)
                expected = seg["text"].count("\n") + 1
                if actual < expected:
                    changed = True
                    if actual <= 0:
                        continue  # 空行被吃光，整段去掉
                    seg = {"kind": "sep", "text": "\n" * (actual - 1)}
            remap[i] = len(new_segments)
            new_segments.append(seg)
        if changed:
            self._segments = new_segments
            self._active = remap.get(self._active, 0)
        self._last_remap = remap
        return changed

    def _absorb_split(self) -> bool:
        """回车分裂出的 -1 块若紧挨编辑段，就地收编，不重建（保证输入流畅）。"""
        blocks = self._block_list()
        tags = [b.userState() for b in blocks]
        cursor_block = self.textCursor().block()
        start = next((i for i, t in enumerate(tags) if t == self._active), None)
        if start is None:
            return False
        lo, hi = start, start
        while lo > 0 and tags[lo - 1] in (self._active, -1):
            lo -= 1
        while hi + 1 < len(blocks) and tags[hi + 1] in (self._active, -1):
            hi += 1
        cur = next((i for i, b in enumerate(blocks) if b == cursor_block), None)
        if cur is None or not (lo <= cur <= hi):
            return False
        for i in range(lo, hi + 1):
            if tags[i] == -1:
                blocks[i].setUserState(self._active)
        return True

    def _structure_ok(self) -> bool:
        """每个非编辑段都还得有自己标记的块，且顺序不乱。"""
        tags = [b.userState() for b in self._block_list()]
        seen = [t for t in tags if t >= 0]
        if seen != sorted(seen):
            return False
        present = set(seen)
        for i, seg in enumerate(self._segments):
            if i != self._active and seg["kind"] == "block" and i not in present:
                return False
        return True

    # ------------------------------------------------------------ 光标
    def _local_offset(self) -> int:
        """光标在当前编辑段内的字符偏移。"""
        cursor = self.textCursor()
        offset = 0
        for block in self._block_list():
            if block.userState() != self._active:
                continue
            if block == cursor.block():
                return offset + cursor.positionInBlock()
            offset += block.length()  # 含块尾换行
        return offset

    def _global_offset(self) -> int:
        """光标在整份源码里的字符偏移（段文本 + 段间换行）。"""
        cursor = self.textCursor()
        tag = cursor.block().userState()
        if tag >= 0 and tag != self._active:
            # 键盘移进渲染块：落在该段开头
            return sum(len(s["text"]) + 1 for s in self._segments[:tag])
        local = self._local_offset()
        total = 0
        for i, seg in enumerate(self._segments):
            if i == self._active:
                return total + min(local, len(seg["text"]))
            total += len(seg["text"]) + 1
        return total

    def _on_text_changed(self) -> None:
        if self._rebuilding:
            return
        self._dirty = True
        self.textEdited.emit()
        if not self._structure_ok():
            self.structureBroken.emit()

    def _on_cursor_moved(self) -> None:
        if self._rebuilding or getattr(self, "_in_mouse_release", False):
            return
        idx = self.textCursor().block().userState()
        if idx == self._active:
            return
        if idx == -1:
            if self._absorb_split():
                return
            self.structureBroken.emit()
            return
        if not self._structure_ok():
            self.structureBroken.emit()
            return
        self._activate(idx)

    def _activate(self, idx: int, offset: int | None = None) -> None:
        """收割当前编辑段 → 校正空行 → 重新切段 → 重建 → 按全局偏移放光标。

        offset 为 None 表示「落在目标段开头」（键盘 / 程序切换）；
        鼠标点击会传入换算好的源码偏移。
        """
        harvested, merged_sep = self._harvest_active()
        if harvested is None:
            self.structureBroken.emit()
            return
        self._segments[self._active]["text"] = harvested

        # 落在空行段上：激活相邻的 block 段（优先前一个），光标放它末尾
        if 0 <= idx < len(self._segments) and self._segments[idx]["kind"] == "sep":
            idx = next((i for i in range(idx - 1, -1, -1)
                        if self._segments[i]["kind"] == "block"), None)
            if idx is None:
                idx = next((i for i, s in enumerate(self._segments)
                            if s["kind"] == "block"), 0)
            offset = sum(len(s["text"]) + 1 for s in self._segments[:idx]) \
                + len(self._segments[idx]["text"])

        self._reconcile_seps(merged_sep)
        idx = self._last_remap.get(idx, idx)
        if offset is None:
            offset = sum(len(s["text"]) + 1 for s in self._segments[:idx])
        full = join_segments(self._segments)
        self._segments = split_segments(full)
        self._active, local = self._locate(self._segments, offset)
        # 偏移恰好落在空行段上时，贴到相邻 block 段
        if self._segments[self._active]["kind"] == "sep":
            prev = next((i for i in range(self._active - 1, -1, -1)
                         if self._segments[i]["kind"] == "block"), None)
            if prev is not None:
                self._active = prev
                local = len(self._segments[prev]["text"])
            else:
                nxt = next((i for i in range(self._active + 1, len(self._segments))
                            if self._segments[i]["kind"] == "block"), self._active)
                self._active, local = nxt, 0
        self._rebuild(place=(self._active, local))

    @staticmethod
    def _locate(segments: list[dict], offset: int) -> tuple[int, int]:
        """全局偏移 → (段下标, 段内偏移)。"""
        total = 0
        for i, seg in enumerate(segments):
            length = len(seg["text"])
            if offset <= total + length:
                return i, offset - total
            total += length + 1
        last = len(segments) - 1
        return last, len(segments[last]["text"])

    # ------------------------------------------------------------ 重建
    def _rebuild(self, place: tuple[int, int] | None = None) -> None:
        self._rebuilding = True
        try:
            self.clear()
            cursor = self.textCursor()
            ranges: list[tuple[int, int]] = []
            for i, seg in enumerate(self._segments):
                start = cursor.position()
                cursor.insertText(seg["text"])
                ranges.append((start, cursor.position()))
                self._tag_range(cursor, start, i)
                if i < len(self._segments) - 1:
                    cursor.insertBlock()
            # 倒序把非编辑的 block 段替换成渲染结果
            for i in range(len(self._segments) - 1, -1, -1):
                seg = self._segments[i]
                if seg["kind"] != "block" or i == self._active:
                    continue
                start, end = ranges[i]
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                cursor.insertHtml(self._render(seg["text"]))
                self._tag_after_html(cursor, start, i)
            if place is not None:
                self._place_cursor(*place)
        finally:
            self._rebuilding = False

    def _tag_range(self, cursor: QTextCursor, start: int, idx: int) -> None:
        """把 [start, 当前光标] 覆盖的块都标记为 idx。"""
        block = self.document().firstBlock()
        while block.isValid():
            if start <= block.position() <= cursor.position():
                block.setUserState(idx)
            block = block.next()

    def _tag_after_html(self, cursor: QTextCursor, start: int, idx: int) -> None:
        block = self.document().firstBlock()
        while block.isValid() and block.position() < start:
            block = block.next()
        end_block = cursor.block()
        while block.isValid():
            block.setUserState(idx)
            if block == end_block:
                break
            block = block.next()

    def _place_cursor(self, idx: int, local: int) -> None:
        cursor = self.textCursor()
        remaining = local
        for block in self._block_list():
            if block.userState() != idx:
                continue
            content = block.length() - 1
            if remaining <= content:
                cursor.setPosition(block.position() + remaining)
                self.setTextCursor(cursor)
                return
            remaining -= content + 1
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

    def _render(self, text: str) -> str:
        style = {"text": theme.TEXT, "muted": theme.TEXT_MUTED,
                 "code_bg": theme.SURFACE_ALT, "border": theme.BORDER}
        return markdown.to_html(text, style, body_size=self._body_size)

    # ------------------------------------------------------------ 点击映射
    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 点击期间先拦住 cursorPositionChanged，松开后统一按映射偏移激活，避免重建两次
        self._in_mouse_release = True
        try:
            super().mouseReleaseEvent(event)
        finally:
            self._in_mouse_release = False
        if self._rebuilding:
            return
        cursor = self.textCursor()
        idx = cursor.block().userState()
        if idx < 0 or idx == self._active or idx >= len(self._segments):
            return
        seg = self._segments[idx]
        if seg["kind"] != "block":
            return
        local = self._map_click(seg["text"], cursor)
        if local is None:
            return
        if not self._structure_ok():
            self.structureBroken.emit()
            return
        offset = sum(len(s["text"]) + 1 for s in self._segments[:idx]) + local
        self._activate(idx, offset)

    def _map_click(self, seg_text: str, cursor: QTextCursor) -> int | None:
        lines = seg_text.split("\n")
        pos = cursor.positionInBlock()
        block_text = cursor.block().text()
        if len(lines) == 1:
            prefix = _PREFIX.match(lines[0])
            return min(len(seg_text), (prefix.end() if prefix else 0) + pos)
        # 围栏代码：渲染的第 n 块对应源码第 n+1 行（跳过开头 ```）
        if _FENCE.match(lines[0]):
            index = self._block_index_in_segment(cursor)
            raw_line = min(index + 1, len(lines) - 1)
            base = sum(len(l) + 1 for l in lines[:raw_line])
            return min(len(seg_text), base + pos)
        # 列表：一块一行，按行前缀补偿
        if all(_PREFIX.match(l) for l in lines if l.strip()):
            index = self._block_index_in_segment(cursor)
            raw_line = min(index, len(lines) - 1)
            base = sum(len(l) + 1 for l in lines[:raw_line])
            prefix = _PREFIX.match(lines[raw_line])
            return min(len(seg_text), base + (prefix.end() if prefix else 0) + pos)
        # 其余（多行段落 / 引用）：按长度比例近似
        if not block_text:
            return 0
        return min(len(seg_text), round(pos / len(block_text) * len(seg_text)))

    def _block_index_in_segment(self, cursor: QTextCursor) -> int:
        idx = cursor.block().userState()
        index = 0
        for block in self._block_list():
            if block == cursor.block():
                return index
            if block.userState() == idx:
                index += 1
        return index