"""日记快速记录（Ctrl+M）：追加语义 + 极简窗口（一个字都没有）。"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import diary, paths  # noqa: E402
from app.appconfig import AppConfig  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.models import today_str  # noqa: E402
from app.services import LifeManagerService  # noqa: E402
from app.storage import Database  # noqa: E402
from app.widgets.diary_capture import DiaryCaptureDialog  # noqa: E402
from tests._support import make_temp_dir, remove_temp_dir  # noqa: E402


class DiaryAppendTest(unittest.TestCase):
    """追加语义：快速记录不能覆盖已经写好的日记。"""

    def setUp(self) -> None:
        self.tmp = make_temp_dir()
        self.path = self.tmp / "diary.md"

    def tearDown(self) -> None:
        remove_temp_dir(self.tmp)

    def test_append_keeps_existing_content(self) -> None:
        diary.write_day("2026-10-15", "今天写了一段。", self.path)
        diary.append_to_day("2026-10-15", "后来又补一句。", self.path)
        body = diary.read_day("2026-10-15", self.path)
        self.assertIn("今天写了一段。", body)
        self.assertIn("后来又补一句。", body)
        self.assertLess(body.index("今天写了一段"), body.index("后来又补一句"))

    def test_append_to_empty_day_creates_entry(self) -> None:
        diary.append_to_day("2026-10-16", "第一句。", self.path)
        self.assertEqual(diary.read_day("2026-10-16", self.path), "第一句。")

    def test_two_appends_on_same_day_keep_both(self) -> None:
        diary.append_to_day("2026-10-17", "上午这句", self.path)
        diary.append_to_day("2026-10-17", "下午这句", self.path)
        body = diary.read_day("2026-10-17", self.path)
        self.assertIn("上午这句", body)
        self.assertIn("下午这句", body)

    def test_append_blank_does_nothing(self) -> None:
        diary.write_day("2026-10-18", "原有内容", self.path)
        diary.append_to_day("2026-10-18", "   ", self.path)
        self.assertEqual(diary.read_day("2026-10-18", self.path), "原有内容")
        diary.append_to_day("2026-10-19", "", self.path)
        self.assertEqual(diary.days_with_entries(self.path), ["2026-10-18"])


class DiaryCaptureDialogTest(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.tmp = make_temp_dir()
        self._saved = {
            "BACKUP_DIR": paths.BACKUP_DIR,
            "EXPORT_DIR": paths.EXPORT_DIR,
            "DIARY_PATH": paths.DIARY_PATH,
            "NOTES_DIR": paths.NOTES_DIR,
        }
        paths.BACKUP_DIR = self.tmp / "backups"
        paths.EXPORT_DIR = self.tmp / "exports"
        paths.DIARY_PATH = self.tmp / "diary.md"
        paths.NOTES_DIR = self.tmp / "notes"
        self.db = Database(self.tmp / "ui.sqlite3")
        self.db.initialize()
        self.service = LifeManagerService(self.db)
        self.config = AppConfig(self.tmp / "config.json")

    def tearDown(self) -> None:
        self.db.close()
        for key, value in self._saved.items():
            setattr(paths, key, value)
        remove_temp_dir(self.tmp)

    def test_dialog_has_no_text_at_all(self) -> None:
        """极简窗：输入框里连占位文字都没有。"""
        dialog = DiaryCaptureDialog(None)
        self.assertEqual(dialog.input.placeholderText(), "")
        self.assertEqual(dialog.input.text(), "")
        # 窗口里唯一的控件就是这个输入框（没有任何说明文字）
        self.assertEqual(dialog.findChildren(type(dialog.input)), [dialog.input])

    def test_enter_appends_to_today(self) -> None:
        dialog = DiaryCaptureDialog(None)
        dialog.input.setText("随手记一句")
        dialog.input.returnPressed.emit()
        self.assertEqual(dialog.saved_text, "随手记一句")
        self.assertEqual(diary.read_day(today_str()), "随手记一句")

    def test_blank_enter_saves_nothing(self) -> None:
        dialog = DiaryCaptureDialog(None)
        dialog.input.setText("   ")
        dialog.input.returnPressed.emit()
        self.assertEqual(dialog.saved_text, "")
        self.assertEqual(diary.read_day(today_str()), "")

    def test_ctrl_m_shortcut_registered(self) -> None:
        window = MainWindow(self.service, self.config)
        try:
            self.assertEqual(
                window.diary_capture_shortcut.key(), QKeySequence("Ctrl+M")
            )
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_ctrl_m_really_opens_the_panel(self) -> None:
        """端到端：真的按一下 Ctrl+M，那个窗要弹出来（用定时器关掉，避免阻塞）。"""
        from PySide6.QtCore import QTimer, Qt
        from PySide6.QtTest import QTest

        window = MainWindow(self.service, self.config)
        opened: list[object] = []

        def close_active() -> None:
            active = QApplication.activeModalWidget()
            opened.append(active)
            if active is not None:
                active.reject()

        try:
            window.show()
            self.app.processEvents()
            QTimer.singleShot(150, close_active)
            QTest.keyClick(
                window, Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier
            )
            self.assertTrue(opened, "按下 Ctrl+M 应该有窗口弹出来")
            self.assertIsInstance(opened[0], DiaryCaptureDialog)
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
