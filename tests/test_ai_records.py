# -*- coding: utf-8 -*-
"""AI 回复里的记录意图：抠出来、换算日期、坏数据不崩。"""

from __future__ import annotations

import unittest
from datetime import date

from app import ai_records

STYLE_TODAY = date(2026, 11, 11)


def with_marker(payload: str, body: str = "好的，记下了。") -> str:
    return f"{body}\n{ai_records.MARKER}{payload}"


class ExtractTest(unittest.TestCase):
    def test_marker_line_is_removed_and_parsed(self) -> None:
        reply = with_marker(
            '{"records":[{"target":"英语单词","date":"今天","note":"背了 40 分钟"}]}'
        )
        clean, intents = ai_records.extract(reply, STYLE_TODAY)
        self.assertEqual(clean, "好的，记下了。")
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].target, "英语单词")
        self.assertEqual(intents[0].day, "2026-11-11")
        self.assertEqual(intents[0].note, "背了 40 分钟")

    def test_multiple_records(self) -> None:
        payload = (
            '{"records":['
            '{"target":"跑步","date":"昨天","note":"5 公里"},'
            '{"target":"读书","date":"2026-11-03","note":""}]}'
        )
        _clean, intents = ai_records.extract(with_marker(payload), STYLE_TODAY)
        self.assertEqual([intent.day for intent in intents], ["2026-11-10", "2026-11-03"])

    def test_plain_list_payload_is_accepted(self) -> None:
        _clean, intents = ai_records.extract(
            with_marker('[{"target":"背单词","note":"20 分钟"}]'), STYLE_TODAY
        )
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].day, "2026-11-11")

    def test_fenced_json_is_also_recognised(self) -> None:
        reply = (
            "记好了。\n```json\n"
            '{"records":[{"target":"跑步","date":"前天","note":"3 公里"}]}\n```'
        )
        clean, intents = ai_records.extract(reply, STYLE_TODAY)
        self.assertNotIn("```", clean)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].day, "2026-11-09")

    def test_broken_json_does_not_raise(self) -> None:
        reply = with_marker('{"records":[{"target":') + "\n正文继续。"
        clean, intents = ai_records.extract(reply, STYLE_TODAY)
        self.assertEqual(intents, [])
        self.assertIn("正文继续", clean)

    def test_entry_without_target_is_dropped(self) -> None:
        _clean, intents = ai_records.extract(
            with_marker('{"records":[{"target":"","date":"今天"}]}'), STYLE_TODAY
        )
        self.assertEqual(intents, [])

    def test_normal_reply_is_untouched(self) -> None:
        reply = "## 本周三件事\n\n1. 把调度器灰度推完\n"
        clean, intents = ai_records.extract(reply, STYLE_TODAY)
        self.assertEqual(clean, reply.strip("\n"))
        self.assertEqual(intents, [])


class NormalizeDayTest(unittest.TestCase):
    def test_relative_words(self) -> None:
        self.assertEqual(ai_records.normalize_day("今天", STYLE_TODAY), "2026-11-11")
        self.assertEqual(ai_records.normalize_day("昨天", STYLE_TODAY), "2026-11-10")
        self.assertEqual(ai_records.normalize_day("前天", STYLE_TODAY), "2026-11-09")

    def test_empty_defaults_to_today(self) -> None:
        self.assertEqual(ai_records.normalize_day("", STYLE_TODAY), "2026-11-11")

    def test_iso_date_is_kept(self) -> None:
        self.assertEqual(ai_records.normalize_day("2026-11-03", STYLE_TODAY), "2026-11-03")

    def test_month_day_rolls_back_to_last_year(self) -> None:
        """11 月说「12-30」，指的是去年。"""
        self.assertEqual(ai_records.normalize_day("12-30", STYLE_TODAY), "2025-12-30")
        self.assertEqual(ai_records.normalize_day("01-05", STYLE_TODAY), "2026-01-05")

    def test_garbage_falls_back_to_today(self) -> None:
        self.assertEqual(ai_records.normalize_day("某个时候", STYLE_TODAY), "2026-11-11")
        self.assertEqual(ai_records.normalize_day("2026-13-40", STYLE_TODAY), "2026-11-11")


class ProtocolHintTest(unittest.TestCase):
    def test_hint_tells_model_the_marker(self) -> None:
        hint = ai_records.protocol_hint()
        self.assertIn(ai_records.MARKER, hint)
        self.assertIn("target", hint)


if __name__ == "__main__":
    unittest.main()
