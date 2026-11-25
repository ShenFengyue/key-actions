"""休息日：快速记录输入「休息」→ 当天在总热力图上变粉。

覆盖：服务层的标记/取消/查询、结构升级 v5→v6、导出导入、
快速记录窗口的交互，以及热力图控件的休息日提示。
"""

from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from app import paths  # noqa: E402
from app.models import NodeType, today_str  # noqa: E402
from app.services import ImportValidationError, LifeManagerService, ValidationError  # noqa: E402
from app.storage import SCHEMA_VERSION, Database  # noqa: E402
from app.widgets.heatmap_calendar import HeatmapCalendar, HeatmapLegend  # noqa: E402
from app.widgets.quick_capture import QuickCaptureDialog  # noqa: E402
from tests._support import make_temp_dir, remove_temp_dir  # noqa: E402


class _RestCase(unittest.TestCase):
    """隔离临时目录 + 独立数据库的公共底座。"""

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
        }
        paths.BACKUP_DIR = self.tmp / "backups"
        paths.EXPORT_DIR = self.tmp / "exports"
        paths.DIARY_PATH = self.tmp / "diary.md"
        self.db = Database(self.tmp / "rest.sqlite3")
        self.db.initialize()
        self.service = LifeManagerService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        for key, value in self._saved.items():
            setattr(paths, key, value)
        remove_temp_dir(self.tmp)


class RestDayServiceTest(_RestCase):
    def test_mark_and_clear_today(self) -> None:
        day = today_str()
        self.assertFalse(self.service.is_rest_day(day))
        self.assertEqual(self.service.mark_rest_day(), day)
        self.assertTrue(self.service.is_rest_day(day))
        # 重复标记没有副作用
        self.service.mark_rest_day(day)
        self.assertEqual(self.service.rest_days(), {day})
        self.assertTrue(self.service.clear_rest_day(day))
        self.assertFalse(self.service.is_rest_day(day))
        # 再取消一次：返回 False，不报错
        self.assertFalse(self.service.clear_rest_day(day))

    def test_rest_day_is_not_an_activity(self) -> None:
        self.service.mark_rest_day()
        self.assertEqual(self.service.list_activities(), [])

    def test_range_query(self) -> None:
        for day in ("2026-01-01", "2026-01-05", "2026-02-01"):
            self.service.mark_rest_day(day)
        self.assertEqual(
            self.service.rest_days("2026-01-01", "2026-01-31"),
            {"2026-01-01", "2026-01-05"},
        )
        self.assertEqual(self.service.rest_days(date_from="2026-02-01"), {"2026-02-01"})
        self.assertEqual(self.service.rest_days(date_to="2025-12-31"), set())

    def test_invalid_date_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.mark_rest_day("2026-13-99")
        with self.assertRaises(ValidationError):
            self.service.clear_rest_day("不是日期")

    def test_migration_v5_to_v6_keeps_data(self) -> None:
        area = self.service.areas()[0]
        process = self.service.processes(area.id)[0]
        self.service.record_activity(process.id, "升级前记的")
        self.service.mark_rest_day("2026-01-01")
        # 模拟旧库：删掉休息日表、把结构版本拨回 5
        with self.db.transaction() as tx:
            tx.execute("DROP TABLE rest_days")
            tx.execute("UPDATE meta SET value = '5' WHERE key = 'schema_version'")
        self.db.close()
        self.db = Database(self.tmp / "rest.sqlite3")
        self.db.initialize()
        self.service = LifeManagerService(self.db)
        self.assertEqual(self.db.get_meta("schema_version"), str(SCHEMA_VERSION))
        self.assertEqual(len(self.service.list_activities()), 1)
        self.assertEqual(self.service.rest_days(), set())  # 旧表没了，休息日为空
        self.service.mark_rest_day()  # 新表可以正常写入
        self.assertTrue(self.service.is_rest_day())
        # 升级前自动备份
        backups = list(paths.BACKUP_DIR.glob("pre-migration-*.sqlite3"))
        self.assertTrue(backups)

    def test_export_import_roundtrip(self) -> None:
        self.service.mark_rest_day("2026-01-01")
        self.service.mark_rest_day()
        exported = self.service.export_json(self.tmp / "export.json")
        payload = json.loads(exported.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["rest_days"]), 2)
        self.service.import_json(exported)
        self.assertEqual(
            self.service.rest_days(), {"2026-01-01", today_str()}
        )

    def test_import_legacy_file_without_rest_days(self) -> None:
        self.service.mark_rest_day()
        exported = self.service.export_json(self.tmp / "legacy.json")
        payload = json.loads(exported.read_text(encoding="utf-8"))
        del payload["rest_days"]  # 旧版导出没有这个字段
        legacy = self.tmp / "legacy2.json"
        legacy.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.service.import_json(legacy)
        self.assertEqual(self.service.rest_days(), set())

    def test_import_rejects_bad_rest_days(self) -> None:
        exported = self.service.export_json(self.tmp / "bad.json")
        payload = json.loads(exported.read_text(encoding="utf-8"))
        payload["rest_days"] = [{"day": "不是日期"}]
        bad = self.tmp / "bad2.json"
        bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ImportValidationError):
            self.service.import_json(bad)


class QuickCaptureRestTest(_RestCase):
    def _dialog(self) -> QuickCaptureDialog:
        return QuickCaptureDialog(self.service, None)

    def test_type_rest_marks_today(self) -> None:
        dialog = self._dialog()
        dialog.input.setText("休息")
        self.assertTrue(dialog._rest_mode)
        self.assertIn("休息日", dialog.result_list.item(0).text())
        dialog.input.returnPressed.emit()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.rest_day, today_str())
        self.assertFalse(dialog.rest_cleared)
        self.assertIsNone(dialog.recorded)
        self.assertTrue(self.service.is_rest_day())
        self.assertEqual(self.service.list_activities(), [])

    def test_type_rest_again_clears_today(self) -> None:
        self.service.mark_rest_day()
        dialog = self._dialog()
        dialog.input.setText("休息")
        self.assertIn("取消", dialog.result_list.item(0).text())
        dialog.input.returnPressed.emit()
        self.assertTrue(dialog.rest_cleared)
        self.assertFalse(self.service.is_rest_day())

    def test_rest_option_coexists_with_node_matches(self) -> None:
        area = self.service.areas()[0]
        process = self.service.create_node(NodeType.PROCESS, "休息与恢复", area.id)
        dialog = self._dialog()
        dialog.input.setText("休息")
        # 第一行是休息日选项，第二行是名字命中的节点
        self.assertIn("休息日", dialog.result_list.item(0).text())
        self.assertIn(process.name, dialog.result_list.item(1).text())
        # 下移一行再回车：走正常的选节点流程，不碰休息日
        dialog._move_selection(1)
        dialog.input.returnPressed.emit()
        self.assertEqual(dialog.stage, "note")
        self.assertIsNone(dialog.rest_day)
        self.assertFalse(self.service.is_rest_day())

    def test_partial_keyword_does_not_trigger_rest(self) -> None:
        dialog = self._dialog()
        dialog.input.setText("休息啊")
        self.assertFalse(dialog._rest_mode)
        dialog.input.setText(" 休息 ")
        self.assertTrue(dialog._rest_mode)

    def test_escape_writes_nothing(self) -> None:
        dialog = self._dialog()
        dialog.input.setText("休息")
        dialog.reject()
        self.assertIsNone(dialog.rest_day)
        self.assertFalse(self.service.is_rest_day())


class HeatmapRestTest(_RestCase):
    def test_tooltip_marks_rest_day(self) -> None:
        calendar = HeatmapCalendar(weeks=4)
        day = today_str()
        calendar.set_rest_days({day})
        self.assertIn("休息日", calendar._tooltip_for(day))
        # 休息日当天也有活动：两条信息都在
        calendar.set_data({day: 2}, {day: ["背单词", "精听"]})
        tooltip = calendar._tooltip_for(day)
        self.assertIn("休息日", tooltip)
        self.assertIn("2 次活动", tooltip)
        # 清空后恢复普通提示
        calendar.set_rest_days(None)
        self.assertNotIn("休息日", calendar._tooltip_for(day))

    def test_hot_legend_has_rest_row(self) -> None:
        hot = HeatmapLegend(hot=True)
        plain = HeatmapLegend(hot=False)
        self.assertGreater(hot.height(), plain.height())


if __name__ == "__main__":
    unittest.main()