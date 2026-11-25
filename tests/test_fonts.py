"""字体大小：全局 / 笔记字号的配置、样式表缩放与设置页接线（离屏）。"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402  必须在设置平台后导入

from app import ai, appconfig, paths, theme  # noqa: E402
from app.appconfig import AppConfig  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.services import LifeManagerService  # noqa: E402
from app.storage import Database  # noqa: E402
from app.widgets import markdown  # noqa: E402
from tests._support import make_temp_dir, remove_temp_dir  # noqa: E402

_STYLE = {"text": "#111111", "muted": "#666666", "code_bg": "#eeeeee", "border": "#dddddd"}


class FontScaleTest(unittest.TestCase):
    """不碰界面，只验证字号推算本身。"""

    def setUp(self) -> None:
        self._saved = theme.FONT_SIZE

    def tearDown(self) -> None:
        theme.FONT_SIZE = self._saved

    def test_defaults_present(self) -> None:
        self.assertEqual(appconfig.DEFAULTS["font_size"], 14)
        self.assertEqual(appconfig.DEFAULTS["notes_font_size"], 15)

    def test_apply_clamps_and_keeps_on_bad_input(self) -> None:
        self.assertEqual(theme.apply_font_size(8), theme.FONT_SIZE_RANGE[0])
        self.assertEqual(theme.apply_font_size(99), theme.FONT_SIZE_RANGE[1])
        self.assertEqual(theme.apply_font_size("16"), 16)
        self.assertEqual(theme.apply_font_size(None), 16)
        self.assertEqual(theme.apply_font_size("abc"), 16)

    def test_stylesheet_scales_with_font_size(self) -> None:
        theme.apply_font_size(14)
        self.assertIn("font-size: 14px", theme.stylesheet())
        theme.apply_font_size(18)
        sheet = theme.stylesheet()
        self.assertIn("font-size: 18px", sheet)   # 正文
        self.assertIn("font-size: 25px", sheet)   # 页标题 18+7
        self.assertIn("font-size: 16px", sheet)   # 提示 18-2
        self.assertNotIn("font-size: 13px", sheet)  # 旧的写死字号不该再出现

    def test_app_font_scales(self) -> None:
        theme.apply_font_size(16)
        self.assertEqual(theme.app_font().pointSize(), 12)
        theme.apply_font_size(12)
        self.assertEqual(theme.app_font().pointSize(), 9)

    def test_markdown_body_size(self) -> None:
        html = markdown.to_html("## 标题\n\n```\nx\n```", _STYLE, body_size=18)
        self.assertIn("font-size:20px", html)  # 二级标题 18+2
        self.assertIn("font-size:17px", html)  # 代码块 18-1
        default = markdown.to_html("## 标题", _STYLE)
        self.assertIn("font-size:15px", default)  # 不传保持 AI 页默认


class FontSettingsTest(unittest.TestCase):
    """设置页两个字号框 → 配置落盘 → 全局字号变化触发界面重建。"""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.tmp = make_temp_dir()
        self._saved = {name: getattr(paths, name)
                       for name in ("BACKUP_DIR", "EXPORT_DIR", "DIARY_PATH", "NOTES_DIR")}
        paths.BACKUP_DIR = self.tmp / "backups"
        paths.EXPORT_DIR = self.tmp / "exports"
        paths.DIARY_PATH = self.tmp / "diary.md"
        paths.NOTES_DIR = self.tmp / "notes"
        self._saved_font = theme.FONT_SIZE
        self.db = Database(self.tmp / "fonts.sqlite3")
        self.db.initialize()
        self.service = LifeManagerService(self.db)
        self.config = AppConfig(self.tmp / "config.json")
        self.window = MainWindow(self.service, self.config)
        # AI 配置也指到临时目录，保存设置时不碰真实 data/
        self.settings_page = self.window.pages["settings"]
        self.settings_page.ai_config = ai.AIConfig(self.tmp / "ai.json")

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.db.close()
        theme.FONT_SIZE = self._saved_font
        for name, value in self._saved.items():
            setattr(paths, name, value)
        remove_temp_dir(self.tmp)

    def test_spinboxes_show_config_values(self) -> None:
        settings = self.settings_page
        self.assertEqual(settings.global_font.value(), 14)
        self.assertEqual(settings.notes_font.value(), 15)
        self.assertEqual(settings.global_font.minimum(), theme.FONT_SIZE_RANGE[0])
        self.assertEqual(settings.global_font.maximum(), theme.FONT_SIZE_RANGE[1])

    def test_save_font_change_requests_restart(self) -> None:
        settings = self.settings_page
        settings.global_font.setValue(17)
        settings.notes_font.setValue(20)
        settings.save()
        self.assertEqual(self.config.get("font_size"), 17)
        self.assertEqual(self.config.get("notes_font_size"), 20)
        self.assertTrue(self.window.restart_requested)
        # 配置真的写进了磁盘，重建后能读回来
        self.assertEqual(AppConfig(self.config.path).get("font_size"), 17)

    def test_save_without_font_change_keeps_window(self) -> None:
        self.settings_page.save()
        self.assertFalse(self.window.restart_requested)

    def test_notes_page_uses_notes_font_size(self) -> None:
        self.config.set("notes_font_size", 20)
        from app.pages.notes import NotesPage

        page = NotesPage(self.config)
        try:
            self.assertEqual(page._notes_size, 20)
            self.assertIn("font-size: 20px", page.editor.styleSheet())
        finally:
            page.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()