"""Typora 式即时预览控件：无损往返 / 渲染 / 段切换 / 结构兜底（离屏）。"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.widgets.markdown_live import (  # noqa: E402
    MarkdownLiveEdit,
    join_segments,
    split_segments,
)

SAMPLE = (
    "# 本周周报\n\n## 做完的\n\n- 能量分配逻辑改成从空余扣\n- 笔记页面上线\n\n"
    "> 一次只推进一件事。\n\n下周计划：把 `Ctrl+N` 的匹配再调一调。\n\n"
    "```python\nx = 1\n\ny = 2\n```\n\n结尾一段。"
)


class SplitTest(unittest.TestCase):
    def test_round_trip_lossless(self) -> None:
        for text in (SAMPLE, "", "\n", "\n\n", "a\n\n\nb", "\n\na\n\n",
                     "```\n\n\n```\n\n后记", "单段无空行"):
            self.assertEqual(join_segments(split_segments(text)), text, repr(text))

    def test_blank_inside_fence_not_split(self) -> None:
        segs = split_segments("```\na\n\nb\n```")
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["kind"], "block")

    def test_alternating_kinds(self) -> None:
        segs = split_segments("a\n\nb")
        self.assertEqual([s["kind"] for s in segs], ["block", "sep", "block"])


class LiveEditTest(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.edit = MarkdownLiveEdit(body_size=15)

    def tearDown(self) -> None:
        self.edit.deleteLater()
        self.app.processEvents()

    def _blocks(self) -> list:
        out = []
        block = self.edit.document().firstBlock()
        while block.isValid():
            out.append(block)
            block = block.next()
        return out

    def test_load_round_trip(self) -> None:
        self.edit.set_text(SAMPLE)
        self.assertEqual(self.edit.source_text(), SAMPLE)
        self.assertEqual(self.edit.toPlainText(), SAMPLE)
        self.assertFalse(self.edit.is_modified())

    def test_other_blocks_rendered_active_raw(self) -> None:
        self.edit.set_text("# 标题\n\n正文一段")
        texts = [b.text() for b in self._blocks()]
        # 第一段是编辑段（显示源码），第二段被渲染（去掉 # 前缀的正文不在其中）
        self.assertIn("# 标题", texts)
        self.assertIn("正文一段", texts)
        # 渲染段的标题不该带 # 号
        self.edit._activate(2, len("# 标题\n\n"))
        texts = [b.text() for b in self._blocks()]
        self.assertIn("标题", texts)
        self.assertNotIn("# 标题", texts)
        self.assertIn("正文一段", texts)  # 现在它是编辑段，显示源码
        self.assertEqual(self.edit.source_text(), "# 标题\n\n正文一段")

    def test_typing_in_active_block(self) -> None:
        self.edit.set_text("abc\n\n第二段")
        cursor = self.edit.textCursor()
        cursor.setPosition(3)
        self.edit.setTextCursor(cursor)
        cursor.insertText("XYZ")
        self.assertTrue(self.edit.is_modified())
        self.assertEqual(self.edit.source_text(), "abcXYZ\n\n第二段")

    def test_switch_block_keeps_edits(self) -> None:
        self.edit.set_text("第一\n\n第二")
        cursor = self.edit.textCursor()
        cursor.setPosition(2)
        self.edit.setTextCursor(cursor)
        cursor.insertText("改")
        # 光标移到第二段（渲染块）→ 触发切换
        second_start = next(b.position() for b in self._blocks()
                            if b.userState() == 2)
        cursor.setPosition(second_start)
        self.edit.setTextCursor(cursor)
        self.assertEqual(self.edit.source_text(), "第一改\n\n第二")
        self.assertEqual(self.edit._active, 2)

    def test_enter_split_stays_lossless(self) -> None:
        self.edit.set_text("一行内容\n\n第二段")
        cursor = self.edit.textCursor()
        cursor.setPosition(2)
        self.edit.setTextCursor(cursor)
        cursor.insertBlock()  # 模拟回车
        self.assertEqual(self.edit.source_text(), "一行\n内容\n\n第二段")

    def test_blank_line_enter_resplits(self) -> None:
        self.edit.set_text("甲\n\n乙")
        cursor = self.edit.textCursor()
        cursor.setPosition(1)  # 「甲」末尾
        self.edit.setTextCursor(cursor)
        cursor.insertBlock()  # 单个回车 = 软换行，还在同一段
        cursor.insertText("丙")
        self.assertEqual(self.edit.source_text(), "甲\n丙\n\n乙")
        # 再按两次回车造出空行，才会切成新段
        cursor.insertBlock()
        cursor.insertBlock()
        cursor.insertText("丁")
        self.assertEqual(self.edit.source_text(), "甲\n丙\n\n丁\n\n乙")

    def test_backspace_merge_into_blank_recovers(self) -> None:
        self.edit.set_text("甲\n\n乙")
        # 激活「乙」，光标放它开头，退格并入上面的空行
        self.edit._activate(2, len("甲\n\n"))
        cursor = self.edit.textCursor()
        cursor.setPosition(cursor.block().position())
        self.edit.setTextCursor(cursor)
        cursor.deletePreviousChar()
        self.assertEqual(self.edit.source_text(), "甲\n乙")

    def test_structure_broken_signal(self) -> None:
        self.edit.set_text("# 标题\n\n正文")
        fired = []
        self.edit.structureBroken.connect(lambda: fired.append(True))
        cursor = self.edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.End,
                            QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText("全删了重写")  # 跨段全选替换 → 结构破坏
        self.app.processEvents()
        self.assertTrue(fired)

    def test_fence_block_round_trip_after_switch(self) -> None:
        text = "前记\n\n```\na\n\nb\n```\n\n后记"
        self.edit.set_text(text)
        self.edit._activate(4, len(text))  # 激活最后一段
        self.assertEqual(self.edit.source_text(), text)


if __name__ == "__main__":
    unittest.main()