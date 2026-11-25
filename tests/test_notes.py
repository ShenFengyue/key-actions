"""笔记：文件夹 / 文件的存储规则与页面冒烟（离屏）。"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402  必须在设置平台后导入

from app import notes as store  # noqa: E402
from app import paths  # noqa: E402
from tests._support import make_temp_dir, remove_temp_dir  # noqa: E402


class _NotesCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_temp_dir()
        self._saved = paths.NOTES_DIR
        paths.NOTES_DIR = self.tmp / "notes"

    def tearDown(self) -> None:
        paths.NOTES_DIR = self._saved
        remove_temp_dir(self.tmp)


class NotesStoreTest(_NotesCase):
    def test_create_folder_and_note(self) -> None:
        folder = store.create_folder("", "工作")
        self.assertEqual(folder, "工作")
        rel = store.create_note(folder, "周报")
        self.assertEqual(rel, "工作/周报.md")
        self.assertTrue(store.exists(rel))
        # 名字自带 .md 不重复加
        rel2 = store.create_note(folder, "计划.md")
        self.assertEqual(rel2, "工作/计划.md")

    def test_nested_folders(self) -> None:
        store.create_folder("", "学习")
        sub = store.create_folder("学习", "英语")
        self.assertEqual(sub, "学习/英语")
        rel = store.create_note(sub, "单词")
        self.assertEqual(rel, "学习/英语/单词.md")
        self.assertEqual(store.folders(), ["学习", "学习/英语"])

    def test_tree_folders_first_sorted(self) -> None:
        store.create_note("", "b笔记")
        store.create_note("", "a笔记")
        store.create_folder("", "z文件夹")
        store.create_folder("", "a文件夹")
        tree = store.tree()
        self.assertEqual([e["name"] for e in tree],
                         ["a文件夹", "z文件夹", "a笔记.md", "b笔记.md"])

    def test_read_write(self) -> None:
        rel = store.create_note("", "随想")
        self.assertEqual(store.read_note(rel), "")
        store.write_note(rel, "# 标题\n\n正文")
        self.assertEqual(store.read_note(rel), "# 标题\n\n正文")

    def test_rename_note_keeps_extension(self) -> None:
        rel = store.create_note("", "旧名")
        new_rel = store.rename(rel, "新名")
        self.assertEqual(new_rel, "新名.md")
        self.assertFalse(store.exists(rel))
        self.assertTrue(store.exists(new_rel))

    def test_rename_folder(self) -> None:
        store.create_folder("", "旧文件夹")
        rel = store.create_note("旧文件夹", "内页")
        new_folder = store.rename("旧文件夹", "新文件夹")
        self.assertEqual(new_folder, "新文件夹")
        self.assertTrue(store.exists("新文件夹/内页.md"))

    def test_move_note(self) -> None:
        store.create_folder("", "目标")
        rel = store.create_note("", "漂流")
        new_rel = store.move_note(rel, "目标")
        self.assertEqual(new_rel, "目标/漂流.md")
        # 移回根目录
        back = store.move_note(new_rel, "")
        self.assertEqual(back, "漂流.md")

    def test_move_collision_rejected(self) -> None:
        store.create_folder("", "目标")
        store.create_note("目标", "同名")
        rel = store.create_note("", "同名")
        with self.assertRaises(ValueError):
            store.move_note(rel, "目标")
        self.assertTrue(store.exists(rel))

    def test_delete_note_and_folder(self) -> None:
        store.create_folder("", "空文件夹")
        store.delete_folder("空文件夹")
        self.assertFalse(store.exists("空文件夹"))
        rel = store.create_note("", "待删")
        store.delete_note(rel)
        self.assertFalse(store.exists(rel))

    def test_delete_nonempty_folder_rejected(self) -> None:
        store.create_folder("", "有内容")
        store.create_note("有内容", "一篇")
        with self.assertRaises(ValueError):
            store.delete_folder("有内容")
        self.assertTrue(store.exists("有内容"))

    def test_duplicate_names_rejected(self) -> None:
        store.create_folder("", "重复")
        with self.assertRaises(ValueError):
            store.create_folder("", "重复")
        store.create_note("", "重复")
        with self.assertRaises(ValueError):
            store.create_note("", "重复.md")

    def test_illegal_names_rejected(self) -> None:
        for bad in ("", "   ", "a/b", "a\\b", "a:b", 'a"b', "a<b", "..", "x."):
            with self.assertRaises(ValueError, msg=bad):
                store.check_name(bad)

    def test_path_traversal_rejected(self) -> None:
        store.create_note("", "正常")
        for bad in ("../外面", "a/../../外面", ".."):
            with self.assertRaises(ValueError, msg=bad):
                store.read_note(bad)
        with self.assertRaises(ValueError):
            store.create_note("../", "越界")


class NotesPageSmokeTest(_NotesCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _page(self):
        from app.pages.notes import NotesPage

        return NotesPage()

    @staticmethod
    def _type(page, text: str) -> None:
        """在即时预览控件里模拟真实打字。"""
        cursor = page.live.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        page.live.setTextCursor(cursor)
        cursor.insertText(text)

    def test_page_builds_and_lists_tree(self) -> None:
        store.create_folder("", "工作")
        store.create_note("工作", "周报")
        store.write_note("工作/周报.md", "# 本周\n\n- 做完了能量分配")
        page = self._page()
        self.assertEqual(page.tree.topLevelItemCount(), 1)
        folder = page.tree.topLevelItem(0)
        self.assertEqual(folder.text(0), "工作")
        self.assertEqual(folder.childCount(), 1)
        page.deleteLater()

    def test_select_edit_and_autosave(self) -> None:
        rel = store.create_note("", "随想")
        page = self._page()
        page._select_rel(rel)
        self.assertEqual(page._current_rel, rel)
        self._type(page, "# 新内容")
        self.assertTrue(page._dirty)
        page.flush()
        self.assertEqual(store.read_note(rel), "# 新内容")
        self.assertFalse(page._dirty)
        page.deleteLater()

    def test_switch_note_saves_previous(self) -> None:
        first = store.create_note("", "第一篇")
        second = store.create_note("", "第二篇")
        page = self._page()
        page._select_rel(first)
        self._type(page, "第一篇的内容")
        page._select_rel(second)
        self.assertEqual(store.read_note(first), "第一篇的内容")
        self.assertEqual(page._current_rel, second)
        page.deleteLater()

    def test_live_preview_renders_other_blocks(self) -> None:
        rel = store.create_note("", "渲染")
        store.write_note(rel, "# 标题\n\n**加粗**")
        page = self._page()
        page._select_rel(rel)
        texts = []
        block = page.live.document().firstBlock()
        while block.isValid():
            texts.append(block.text())
            block = block.next()
        # 光标在第一段（源码），第二段已渲染：加粗不带 ** 号
        self.assertIn("# 标题", texts)
        self.assertIn("加粗", texts)
        self.assertNotIn("**加粗**", texts)
        # 源码模式兜底：内容无损带过去
        page.source_button.setChecked(True)
        self.assertEqual(page.editor.toPlainText(), "# 标题\n\n**加粗**")
        page.source_button.setChecked(False)
        self.assertEqual(page.live.source_text(), "# 标题\n\n**加粗**")
        page.deleteLater()

    def test_refresh_keeps_unsaved_text(self) -> None:
        rel = store.create_note("", "草稿")
        page = self._page()
        page._select_rel(rel)
        self._type(page, "写了一半")
        page.refresh()
        self.assertEqual(page._current_text(), "写了一半")
        self.assertTrue(page._dirty)
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()