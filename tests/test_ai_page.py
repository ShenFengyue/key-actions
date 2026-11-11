# -*- coding: utf-8 -*-
"""AI 页的「自然语言记录」：卡片只在确认后写库。"""

from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402  必须在设置平台后导入

from app import ai, ai_records, paths, theme  # noqa: E402
from app.models import NodeType  # noqa: E402
from app.pages.ai import AIPage, RecordCard  # noqa: E402
from app.services import LifeManagerService  # noqa: E402
from app.storage import Database  # noqa: E402
from tests._support import make_temp_dir, remove_temp_dir  # noqa: E402


class RecordCardTest(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        theme.apply_theme("light")
        self.tmp = make_temp_dir()
        self._saved = {
            "BACKUP_DIR": paths.BACKUP_DIR,
            "EXPORT_DIR": paths.EXPORT_DIR,
            "DIARY_PATH": paths.DIARY_PATH,
        }
        paths.BACKUP_DIR = self.tmp / "backups"
        paths.EXPORT_DIR = self.tmp / "exports"
        paths.DIARY_PATH = self.tmp / "diary.md"
        self.db = Database(self.tmp / "ai.sqlite3")
        self.db.initialize()
        self.service = LifeManagerService(self.db)

        self.area = self.service.create_node(NodeType.AREA, "语言", None)
        self.process = self.service.create_node(NodeType.PROCESS, "英语单词", self.area.id)

        self.page = AIPage(self.service, ai_config=ai.AIConfig(self.tmp / "ai.json"))
        self.page.current_id = self.service.create_ai_conversation("记录测试")
        self.service.append_ai_message(self.page.current_id, "user", "记一下今天背了 40 分钟单词")

    def tearDown(self) -> None:
        self.page.deleteLater()
        for key, value in self._saved.items():
            setattr(paths, key, value)
        remove_temp_dir(self.tmp)

    # ------------------------------------------------------------ 工具
    def _reply_with(self, target: str, day: str = "今天", note: str = "背了 40 分钟") -> None:
        payload = {"records": [{"target": target, "date": day, "note": note}]}
        self.page._on_reply(f"好的，帮你记一下。\n{ai_records.MARKER}{json.dumps(payload, ensure_ascii=False)}")

    def _card(self) -> RecordCard:
        cards = self.page.findChildren(RecordCard)
        self.assertTrue(cards, "没有出现待写入卡片")
        return cards[0]

    # ------------------------------------------------------------ 用例
    def test_reply_shows_card_and_writes_nothing(self) -> None:
        self._reply_with("英语单词")
        self.assertEqual(self.service.list_activities(), [], "没点确认就写库了")
        self.assertIsNotNone(self._card())
        stored = [m["content"] for m in self.service.ai_messages(self.page.current_id)]
        self.assertNotIn(ai_records.MARKER, "".join(stored), "协议行被存进了历史")

    def test_protocol_line_is_not_displayed(self) -> None:
        from app.pages.ai import MarkdownView

        self._reply_with("英语单词")
        for view in self.page.findChildren(MarkdownView):
            self.assertNotIn(ai_records.MARKER, view.text(), "协议行显示在了气泡里")

    def test_confirming_writes_one_activity(self) -> None:
        self._reply_with("英语单词")
        self._card().write_button.click()
        activities = self.service.list_activities()
        self.assertEqual(len(activities), 1)
        self.assertEqual(activities[0].node_id, self.process.id)
        self.assertIn("40 分钟", activities[0].note)
        self.assertEqual(activities[0].activity_date, ai_records.normalize_day("今天"))

    def test_card_cannot_write_twice(self) -> None:
        self._reply_with("英语单词")
        card = self._card()
        card.write_button.click()
        card.write_button.click()  # 按钮已经被换掉，再点也写不出第二条
        self.assertEqual(len(self.service.list_activities()), 1)

    def test_unmatched_target_must_be_chosen_manually(self) -> None:
        self._reply_with("完全不存在的目标名")
        card = self._card()
        combo = card._rows[0][0]
        self.assertIsNone(combo.current_node_id())
        card.write_button.click()
        self.assertEqual(self.service.list_activities(), [])
        self.assertFalse(card.error.isHidden(), "没选节点就应该提示")

    def test_dismiss_writes_nothing(self) -> None:
        self._reply_with("英语单词")
        card = self._card()
        card.skip_button.click()
        self.assertEqual(self.service.list_activities(), [])
        self.assertEqual(self.page._pending, {})

    def test_after_write_pending_is_cleared(self) -> None:
        self._reply_with("英语单词")
        self._card().write_button.click()
        self.assertEqual(self.page._pending, {})


class PayloadTest(unittest.TestCase):
    def test_system_hint_goes_into_payload(self) -> None:
        tmp = make_temp_dir()
        try:
            config = ai.AIConfig(tmp / "ai.json")
            payload = ai.build_payload(
                config, [{"role": "user", "content": "hi"}], system_hint="格式约定"
            )
        finally:
            remove_temp_dir(tmp)
        roles = [message["role"] for message in payload["messages"]]
        self.assertIn("system", roles)
        contents = " ".join(message["content"] for message in payload["messages"])
        self.assertIn("格式约定", contents)
        self.assertIn("hi", contents)


if __name__ == "__main__":
    unittest.main()
