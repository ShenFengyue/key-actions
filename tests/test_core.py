"""核心规则测试：对应产品验收标准的数据层部分。"""

from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import date
from pathlib import Path

from app import heatmap, paths
from app.models import EventType, NodeType, Status
from app.services import (
    ImportValidationError,
    LifeManagerService,
    ValidationError,
    describe_event,
)
from app.storage import SCHEMA_VERSION, Database, DataCorruptionError
from tests._support import make_temp_dir, remove_temp_dir

EXPECTED_TREE = {
    "领域 1": {"进程 1-1": [], "进程 1-2": ["线程 1-2-1"], "进程 1-3": [], "进程 1-4": []},
    "领域 2": {"进程 2-1": ["线程 2-1-1", "线程 2-1-2"], "真实领域 2": []},
    "领域 3": {"进程 3-1": ["线程 3-1-1"], "进程 3-2": [], "进程 3-3": ["线程 3-3-1", "线程 3-3-2"]},
}


class ServiceTestCase(unittest.TestCase):
    """提供隔离的临时数据库与临时备份/导出目录。"""

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
        self.db = Database(self.tmp / "test.sqlite3")
        self.db.initialize()
        self.service = LifeManagerService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        for key, value in self._saved.items():
            setattr(paths, key, value)
        remove_temp_dir(self.tmp)


class TestInitialClassification(ServiceTestCase):
    def test_tree_matches_specification(self) -> None:
        """验收 1：初始分类与用户提供的树完全一致。"""
        actual: dict[str, dict[str, list[str]]] = {}
        for area in self.service.areas():
            processes: dict[str, list[str]] = {}
            for process in self.service.processes(area.id):
                processes[process.name] = [t.name for t in self.service.threads(process.id)]
            actual[area.name] = processes
        self.assertEqual(actual, EXPECTED_TREE)

    def test_every_node_has_unique_id(self) -> None:
        ids = [node.id for node in self.service.all_nodes()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_seed_associations(self) -> None:
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        targets = {p.name for p in self.service.processes_for_thread(english.id)}
        self.assertEqual(targets, {"进程 1-4", "真实领域 2"})


class TestHeatmapRules(ServiceTestCase):
    """验收 2/3/4/6：热力图按活动次数分档，关联不重复计数。"""

    def setUp(self) -> None:
        super().setUp()
        self.life = self.service.node_by_name("领域 3", NodeType.AREA)
        self.work = self.service.node_by_name("领域 1", NodeType.AREA)
        self.explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        self.english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.startup = self.service.node_by_name("进程 1-4", NodeType.PROCESS)

    def test_activity_lands_in_process_and_area_only(self) -> None:
        self.service.record_activity(self.english.id, "背单词 30 分钟", "2026-10-01")
        self.assertEqual(heatmap.counts_for_process(self.db.conn, self.explore.id), {"2026-10-01": 1})
        self.assertEqual(heatmap.counts_for_area(self.db.conn, self.life.id), {"2026-10-01": 1})
        # 关联了「进程 1-4」，但活动不能进入进程 1-4的热力图。
        self.assertEqual(heatmap.counts_for_process(self.db.conn, self.startup.id), {})
        self.assertEqual(heatmap.counts_for_area(self.db.conn, self.work.id), {})

    def test_each_record_counted_once_per_heatmap(self) -> None:
        for _ in range(3):
            self.service.record_activity(self.english.id, "练习", "2026-10-02")
        self.assertEqual(heatmap.counts_for_process(self.db.conn, self.explore.id)["2026-10-02"], 3)
        self.assertEqual(heatmap.counts_for_area(self.db.conn, self.life.id)["2026-10-02"], 3)

    def test_levels_follow_fixed_thresholds(self) -> None:
        self.assertEqual([heatmap.level_for(n) for n in (0, 1, 2, 3, 4, 9)], [0, 1, 2, 3, 4, 4])
        self.assertEqual(heatmap.color_for(5), "#216e39")
        self.assertEqual(heatmap.color_for(0), "#ebedf0")

    def test_thread_heatmap_counts_only_activities_recorded_on_it(self) -> None:
        """Thread 热力图：只统计「当时就记在这个线程上」的活动。"""
        self.assertTrue(hasattr(heatmap, "counts_for_thread"))
        area = self.service.areas()[0]
        process = self.service.processes(area.id)[0]
        thread = self.service.create_node(NodeType.THREAD, "线程-口径测试", process.id)
        self.service.record_activity(thread.id, "记在线程上", "2026-10-01")
        self.service.record_activity(process.id, "只记在进程上", "2026-10-01")

        self.assertEqual(
            heatmap.counts_for_thread(self.db.conn, thread.id), {"2026-10-01": 1}
        )
        # 进程那张含它下面线程的活动（记在进程上那一条 + 线程那一条）
        self.assertEqual(
            heatmap.counts_for_process(self.db.conn, process.id), {"2026-10-01": 2}
        )

    def test_calendar_grid_shape(self) -> None:
        grid = heatmap.build_calendar(date(2026, 10, 15))
        self.assertEqual(len(grid.columns), heatmap.DEFAULT_WEEKS)
        self.assertTrue(all(len(column) == 7 for column in grid.columns))
        today_row = (date(2026, 10, 15).weekday() + 1) % 7
        self.assertEqual(grid.columns[-1][today_row], "2026-10-15")
        # 未来的日期不生成方格
        self.assertIsNone(grid.columns[-1][today_row + 1])
        self.assertTrue(grid.month_labels)


class TestOwnershipHistory(ServiceTestCase):
    """验收 7/8/9/14：移动保留历史、生成事件、旧快照不变。"""

    def setUp(self) -> None:
        super().setUp()
        self.english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        self.startup = self.service.node_by_name("进程 1-4", NodeType.PROCESS)
        self.life = self.service.node_by_name("领域 3", NodeType.AREA)
        self.work = self.service.node_by_name("领域 1", NodeType.AREA)

    def test_thread_move_keeps_old_activities_in_old_heatmap(self) -> None:
        self.service.record_activity(self.english.id, "10 月 1 日学习", "2026-10-01")
        self.service.record_activity(self.english.id, "10 月 14 日学习", "2026-10-14")
        self.service.move_node(self.english.id, self.startup.id)
        self.service.record_activity(self.english.id, "10 月 15 日学习", "2026-10-15")

        self.assertEqual(
            heatmap.counts_for_process(self.db.conn, self.explore.id),
            {"2026-10-01": 1, "2026-10-14": 1},
        )
        self.assertEqual(heatmap.counts_for_process(self.db.conn, self.startup.id), {"2026-10-15": 1})
        self.assertEqual(
            heatmap.counts_for_area(self.db.conn, self.life.id),
            {"2026-10-01": 1, "2026-10-14": 1},
        )
        self.assertEqual(heatmap.counts_for_area(self.db.conn, self.work.id), {"2026-10-15": 1})

    def test_move_does_not_rewrite_snapshots(self) -> None:
        activity = self.service.record_activity(self.english.id, "学习", "2026-10-01")
        self.service.move_node(self.english.id, self.startup.id)
        reloaded = self.service.require_activity(activity.id)
        self.assertEqual(reloaded.path_text, "领域 3 → 进程 3-3 → 线程 3-3-2")
        self.assertEqual(reloaded.area_id, self.life.id)
        self.assertEqual(reloaded.process_id, self.explore.id)
        self.assertEqual(self.service.path_text(self.english.id), "领域 1 → 进程 1-4 → 线程 3-3-2")

    def test_move_creates_readable_event(self) -> None:
        self.service.move_node(self.english.id, self.startup.id)
        events = self.service.events_for_node(self.english.id)
        move_events = [event for event in events if event.event_type is EventType.MOVE]
        self.assertEqual(len(move_events), 1)
        self.assertEqual(move_events[0].before["path"], ["领域 3", "进程 3-3", "线程 3-3-2"])
        self.assertEqual(move_events[0].after["path"], ["领域 1", "进程 1-4", "线程 3-3-2"])

    def test_illegal_moves_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.move_node(self.english.id, self.life.id)  # Thread 不能挂到 Area 下
        with self.assertRaises(ValidationError):
            self.service.move_node(self.explore.id, self.startup.id)  # Process 不能挂到 Process 下
        with self.assertRaises(ValidationError):
            self.service.move_node(self.life.id, self.work.id)  # Area 不能移动

    def test_process_move_across_areas(self) -> None:
        """验收 9：Process 自身热力图保持完整，Area 按历史归属分别保留。"""
        travel = self.service.node_by_name("进程 3-2", NodeType.PROCESS)
        self.service.record_activity(travel.id, "在旅途中", "2026-09-01")
        new_area = self.service.create_node(NodeType.AREA, "新领域", None)
        self.service.move_node(travel.id, new_area.id)
        self.service.record_activity(travel.id, "新归属下的进程 3-2", "2026-10-15")

        self.assertEqual(
            heatmap.counts_for_process(self.db.conn, travel.id),
            {"2026-09-01": 1, "2026-10-15": 1},
        )
        self.assertEqual(heatmap.counts_for_area(self.db.conn, self.life.id), {"2026-09-01": 1})
        self.assertEqual(heatmap.counts_for_area(self.db.conn, new_area.id), {"2026-10-15": 1})
        self.assertEqual(self.service.path_text(travel.id), "新领域 → 进程 3-2")

    def test_rename_keeps_old_snapshot_names(self) -> None:
        activity = self.service.record_activity(self.english.id, "学习", "2026-10-01")
        self.service.rename_node(self.explore.id, "进程 3-32")
        reloaded = self.service.require_activity(activity.id)
        self.assertEqual(reloaded.path_text, "领域 3 → 进程 3-3 → 线程 3-3-2")
        self.assertEqual(self.service.path_text(self.english.id), "领域 3 → 进程 3-32 → 线程 3-3-2")

    def test_archive_keeps_history(self) -> None:
        activity = self.service.record_activity(self.english.id, "学习", "2026-10-01")
        self.service.set_pinned(self.english.id, True)
        self.service.set_status(self.english.id, Status.ARCHIVED)
        node = self.service.require_node(self.english.id)
        self.assertTrue(node.is_archived)
        self.assertFalse(node.pinned)
        self.assertIsNotNone(node.archived_at)
        self.assertEqual(self.service.require_activity(activity.id).note, "学习")
        self.assertEqual(heatmap.counts_for_process(self.db.conn, self.explore.id), {"2026-10-01": 1})

    def test_focus_limit(self) -> None:
        nodes = [
            self.service.node_by_name(name, NodeType.PROCESS)
            for name in ("进程 1-1", "进程 1-2", "进程 1-3")
        ]
        for node in nodes:
            self.service.set_pinned(node.id, True)
        with self.assertRaises(ValidationError):
            self.service.set_pinned(self.startup.id, True)
        self.service.set_pinned(nodes[0].id, False)
        self.service.set_pinned(self.startup.id, True)
        self.assertEqual(len(self.service.focused_nodes()), 3)


class TestAssociations(ServiceTestCase):
    """验收 5/6：线程可关联多个进程，关联不影响热力图。"""

    def test_thread_can_associate_many_processes(self) -> None:
        coding = self.service.node_by_name("线程 3-1-1", NodeType.THREAD)
        value = self.service.node_by_name("进程 1-3", NodeType.PROCESS)
        startup = self.service.node_by_name("进程 1-4", NodeType.PROCESS)
        self.assertEqual(
            {p.name for p in self.service.processes_for_thread(coding.id)}, {"进程 1-3", "进程 1-4"}
        )
        self.assertEqual(
            {t.name for t in self.service.threads_for_process(startup.id)}, {"线程 3-1-1", "线程 3-3-2"}
        )
        self.assertEqual(
            {t.name for t in self.service.threads_for_process(value.id)}, {"线程 3-1-1"}
        )

    def test_associate_and_dissociate_history(self) -> None:
        coding = self.service.node_by_name("线程 3-1-1", NodeType.THREAD)
        hobby = self.service.node_by_name("进程 3-1", NodeType.PROCESS)
        with self.assertRaises(ValidationError):
            self.service.associate(coding.id, hobby.id)  # 已是主要归属
        with self.assertRaises(ValidationError):
            self.service.associate(coding.id, self.service.node_by_name("线程 2-1-2", NodeType.THREAD).id)

        startup = self.service.node_by_name("进程 1-4", NodeType.PROCESS)
        self.service.dissociate(coding.id, startup.id)
        self.assertNotIn("进程 1-4", {p.name for p in self.service.processes_for_thread(coding.id)})
        self.assertTrue(any(a.ended_at for a in self.service.association_history(coding.id)))
        self.assertTrue(
            any(e.event_type is EventType.DISSOCIATE for e in self.service.events_for_node(coding.id))
        )


class TestPersistenceAndData(ServiceTestCase):
    """验收 10/11/13/14/15：持久化、数据位置、备份导入导出、离线。"""

    def test_reopen_keeps_everything(self) -> None:
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "持久化测试", "2026-10-05")
        self.service.move_node(english.id, self.service.node_by_name("进程 1-4", NodeType.PROCESS).id)
        self.service.set_next_action(english.id, "每天 30 分钟")
        self.db.close()

        reopened = Database(self.db.path)
        reopened.initialize()
        service = LifeManagerService(reopened)
        english = service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.assertEqual(service.path_text(english.id), "领域 1 → 进程 1-4 → 线程 3-3-2")
        self.assertEqual(english.next_action, "每天 30 分钟")
        self.assertEqual(len(service.activities_on("2026-10-05")), 1)
        self.assertTrue(any(e.event_type is EventType.MOVE for e in service.events_for_node(english.id)))
        self.assertEqual(
            {p.name for p in service.processes_for_thread(english.id)}, {"进程 1-4", "真实领域 2"}
        )
        reopened.close()

    def test_backup_and_restore(self) -> None:
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "备份前的活动", "2026-10-06")
        backup = self.service.backup("test")
        self.assertTrue(backup.exists())
        self.assertTrue(paths.is_inside_project(backup))
        ok, message = self.service.verify_backup(backup)
        self.assertTrue(ok, message)
        # 备份必须是单文件：不留 WAL 边车文件
        self.assertFalse(backup.with_name(backup.name + "-wal").exists())
        self.assertFalse(backup.with_name(backup.name + "-shm").exists())

        self.service.record_activity(english.id, "备份后的活动", "2026-10-07")
        safety = self.service.restore_backup(backup)
        self.assertTrue(safety.exists())
        self.assertEqual(len(self.service.activities_on("2026-10-06")), 1)
        self.assertEqual(len(self.service.activities_on("2026-10-07")), 0)

    def test_restore_rejects_invalid_file(self) -> None:
        broken = self.tmp / "broken.sqlite3"
        broken.write_text("not a database", encoding="utf-8")
        with self.assertRaises(ValidationError):
            self.service.restore_backup(broken)
        self.assertEqual(len(self.service.all_nodes()), 18)

    def test_backup_and_restore_cover_the_diary(self) -> None:
        """日记是独立的 Markdown 文件，备份时一起存，恢复时一起回退。"""
        from app import diary as diary_store

        diary_store.write_day("2026-10-15", "备份时的日记", paths.DIARY_PATH)
        backup = self.service.backup("with-diary")
        sibling = backup.with_name(backup.stem + ".diary.md")
        self.assertTrue(sibling.exists(), "备份没有带上日记文件")
        self.assertIn("备份时的日记", sibling.read_text(encoding="utf-8"))

        diary_store.write_day("2026-10-15", "备份之后改过的内容", paths.DIARY_PATH)
        safety = self.service.restore_backup(backup)

        self.assertEqual(
            diary_store.read_day("2026-10-15", paths.DIARY_PATH), "备份时的日记"
        )
        # 恢复前的日记也另存了一份
        self.assertTrue(safety.with_name(safety.stem + ".diary.md").exists())

    def test_export_and_import_json(self) -> None:
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "导出前", "2026-10-08")
        exported = self.service.export_json(self.tmp / "export.json")
        self.assertTrue(exported.exists())

        self.service.record_activity(english.id, "导出后新增", "2026-10-09")
        safety = self.service.import_json(exported)
        self.assertTrue(safety.exists())
        self.assertEqual(len(self.service.activities_on("2026-10-08")), 1)
        self.assertEqual(len(self.service.activities_on("2026-10-09")), 0)
        self.assertTrue(self.service.events_for_node(english.id))

    def test_import_invalid_file_keeps_data(self) -> None:
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "保留我", "2026-10-08")
        bad = self.tmp / "bad.json"
        bad.write_text(json.dumps({"format": "something-else", "nodes": []}), encoding="utf-8")
        with self.assertRaises(ImportValidationError):
            self.service.import_json(bad)
        self.assertEqual(len(self.service.activities_on("2026-10-08")), 1)

        bad2 = self.tmp / "bad2.json"
        bad2.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(ImportValidationError):
            self.service.import_json(bad2)
        self.assertEqual(len(self.service.all_nodes()), 18)

    def test_integrity_report_is_clean(self) -> None:
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "检查", "2026-10-10")
        report = self.db.integrity_report()
        self.assertEqual(report["sqlite"], "ok")
        self.assertEqual([f for f in report["findings"] if f["level"] == "error"], [])
        self.assertEqual(report["stats"]["areas"], 3)
        self.assertEqual(report["stats"]["processes"], 9)
        self.assertEqual(report["stats"]["threads"], 6)

    def test_all_paths_live_inside_project(self) -> None:
        """验收 11：所有数据、日志、备份都位于项目文件夹内。"""
        for path in (paths.DATA_DIR, paths.DATABASE_PATH, paths.BACKUP_DIR,
                     paths.EXPORT_DIR, paths.LOG_DIR):
            self.assertTrue(paths.is_inside_project(path), f"{path} 不在项目文件夹内")
        forbidden = ("AppData", "Documents", "ProgramData")
        for path in (paths.DATA_DIR, paths.BACKUP_DIR, paths.EXPORT_DIR, paths.LOG_DIR):
            self.assertFalse(any(part in str(path) for part in forbidden))

    def test_source_has_no_network_dependencies(self) -> None:
        """只有 app/ai.py（AI 客户端）允许联网，其它模块一律不许。"""
        banned = ("requests", "httpx", "aiohttp", "urllib.request", "http.client",
                  "socket", "ftplib", "smtplib", "webbrowser", "openai", "anthropic")
        offenders: list[str] = []
        for file in (paths.ROOT / "app").rglob("*.py"):
            # AI 功能是唯一的联网出口（见 docs/AI-功能草案.md）
            if file == paths.ROOT / "app" / "ai.py":
                continue
            text = file.read_text(encoding="utf-8")
            for name in banned:
                if f"import {name}" in text:
                    offenders.append(f"{file.name}: {name}")
        self.assertEqual(offenders, [])


class TestAIChat(ServiceTestCase):
    """AI 对话：本地历史、请求体隐私、配置与发送日志。"""

    def test_conversation_history_roundtrip(self) -> None:
        conversation = self.service.create_ai_conversation()
        self.service.append_ai_message(conversation, "user", "你好")
        self.service.append_ai_message(conversation, "assistant", "在")

        conversations = self.service.ai_conversations()
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["title"], "你好")
        self.assertEqual(conversations[0]["message_count"], 2)
        self.assertEqual(
            [m["role"] for m in self.service.ai_messages(conversation)],
            ["user", "assistant"],
        )

        self.service.append_ai_message(conversation, "user", "再问一句")
        self.assertGreaterEqual(
            self.service.ai_conversations()[0]["updated_at"], conversations[0]["updated_at"]
        )

        self.service.delete_ai_conversation(conversation)
        self.assertEqual(self.service.ai_conversations(), [])
        self.assertEqual(self.service.ai_messages(conversation), [])

    def test_request_body_contains_only_the_conversation(self) -> None:
        from app import ai

        config = ai.AIConfig(self.tmp / "ai.json")
        config.set("system_prompt", "人设提示词")
        config.set("model", "deepseek-chat")
        payload = ai.build_payload(
            config,
            [
                {"role": "user", "content": "这句话会发出去"},
                {"role": "assistant", "content": "好"},
            ],
        )
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "人设提示词"})
        self.assertEqual(
            [m["content"] for m in payload["messages"][1:]], ["这句话会发出去", "好"]
        )

        text = json.dumps(payload, ensure_ascii=False)
        for forbidden in (paths.DATABASE_PATH.name, "activities", "backup", "diary"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("api_key", text)

    def test_config_is_off_by_default_and_stored_under_data(self) -> None:
        from app import ai

        config = ai.AIConfig(self.tmp / "ai.json")
        self.assertFalse(config.ready, "默认必须是关闭的")
        config.set("api_key", "sk-test")
        config.set("enabled", True)
        config.save()

        again = ai.AIConfig(self.tmp / "ai.json")
        self.assertTrue(again.ready)
        self.assertEqual(again.get("api_key"), "sk-test")
        self.assertIn("data", str(ai.AI_CONFIG_PATH))

    def test_endpoint_builder(self) -> None:
        from app import ai

        self.assertEqual(
            ai.endpoint("https://api.deepseek.com"),
            "https://api.deepseek.com/chat/completions",
        )
        self.assertEqual(ai.endpoint("https://x/v1/"), "https://x/v1/chat/completions")
        self.assertEqual(
            ai.endpoint("https://x/chat/completions"), "https://x/chat/completions"
        )

    def test_send_log_never_contains_the_key_or_the_text(self) -> None:
        from app import ai

        original = ai.AI_LOG_PATH
        ai.AI_LOG_PATH = self.tmp / "logs" / "ai.log"
        try:
            ai._log(True, "deepseek-chat", 123, "reply=4c")
            text = ai.AI_LOG_PATH.read_text(encoding="utf-8")
        finally:
            ai.AI_LOG_PATH = original
        self.assertIn("bytes=123", text)
        self.assertNotIn("sk-", text)


class TestEventDeletion(ServiceTestCase):
    """历史事件可以删除：只删事件本身，活动与热力图不受影响。"""

    def setUp(self) -> None:
        super().setUp()
        self.english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        self.startup = self.service.node_by_name("进程 1-4", NodeType.PROCESS)

    def test_delete_event_keeps_activities_and_heatmaps(self) -> None:
        activity = self.service.record_activity(self.english.id, "学习", "2026-10-01")
        self.service.move_node(self.english.id, self.startup.id)
        move_event = next(
            event
            for event in self.service.events_for_node(self.english.id)
            if event.event_type is EventType.MOVE
        )
        before_count = self.service.event_count(self.english.id)

        self.service.delete_event(move_event.id)

        self.assertEqual(self.service.event_count(self.english.id), before_count - 1)
        self.assertNotIn(
            move_event.id, [event.id for event in self.service.events_for_node(self.english.id)]
        )
        # 活动记录与它发生时的归属快照完全不受影响
        self.assertEqual(self.service.require_activity(activity.id).path_text, "领域 3 → 进程 3-3 → 线程 3-3-2")
        self.assertEqual(heatmap.counts_for_process(self.db.conn, self.explore.id), {"2026-10-01": 1})
        self.assertEqual(heatmap.counts_for_area(self.db.conn, self.service.node_by_name("领域 3", NodeType.AREA).id), {"2026-10-01": 1})
        self.assertEqual(self.db.integrity_report()["findings"][0]["level"], "ok")

    def test_delete_unknown_event_is_rejected(self) -> None:
        self.assertEqual(self.service.delete_events([]), 0)
        with self.assertRaises(ValidationError):
            self.service.delete_events(["不存在的-id"])
        self.assertGreater(self.service.event_count(self.english.id), 0)

    def test_revision_bumps_on_every_write(self) -> None:
        start = self.service.revision
        self.service.record_activity(self.english.id, "x", "2026-10-02")
        self.service.set_status(self.english.id, Status.PAUSED)
        self.service.set_next_action(self.english.id, "继续")
        self.assertGreater(self.service.revision, start + 2)
        self.service.delete_events(
            [event.id for event in self.service.events_for_node(self.english.id, limit=1)]
        )
        self.assertGreater(self.service.revision, start + 3)


class TestNodeDeletion(ServiceTestCase):
    """删除节点 = 移入已删除清单：数据保留、热力图不变、可恢复。"""

    def setUp(self) -> None:
        super().setUp()
        self.life = self.service.node_by_name("领域 3", NodeType.AREA)
        self.travel = self.service.node_by_name("进程 3-2", NodeType.PROCESS)
        self.explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        self.english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)

    def test_delete_process_keeps_activity_and_area_heatmap(self) -> None:
        activity = self.service.record_activity(self.travel.id, "在路上", "2026-10-03")
        area_before = heatmap.counts_for_area(self.db.conn, self.life.id)

        self.service.delete_node(self.travel.id)

        self.assertNotIn("进程 3-2", [p.name for p in self.service.processes(self.life.id)])
        entries = self.service.deleted_entries()
        self.assertEqual([entry["node"].name for entry in entries], ["进程 3-2"])
        self.assertTrue(entries[0]["restorable"])
        self.assertEqual(entries[0]["subtree_activity_count"], 1)
        # 数据与热力图完全不受影响
        self.assertEqual(heatmap.counts_for_process(self.db.conn, self.travel.id), {"2026-10-03": 1})
        self.assertEqual(heatmap.counts_for_area(self.db.conn, self.life.id), area_before)
        self.assertEqual(self.service.require_activity(activity.id).note, "在路上")
        self.assertIn(activity.id, [item.id for item in self.service.list_activities()])

    def test_delete_process_takes_descendants_and_restores_them(self) -> None:
        sub = self.service.node_by_name("进程 1-2", NodeType.PROCESS)
        thread = self.service.node_by_name("线程 1-2-1", NodeType.THREAD)
        self.service.record_activity(thread.id, "整理词表", "2026-10-04")

        removed = self.service.delete_node(sub.id)
        self.assertEqual(removed, 2)
        work = self.service.node_by_name("领域 1", NodeType.AREA)
        self.assertNotIn("进程 1-2", [p.name for p in self.service.processes(work.id)])
        entry = self.service.deleted_entries()[0]
        self.assertEqual(entry["batch_size"], 2)
        self.assertEqual([node.name for node in entry["descendants"]], ["线程 1-2-1"])
        self.assertEqual(self.service.deleted_entry_count(), 1)

        restored = self.service.restore_node(sub.id)
        self.assertEqual(restored, 2)
        self.assertEqual(self.service.deleted_entry_count(), 0)
        parent = self.service.node_by_name("领域 1", NodeType.AREA)
        self.assertIn("进程 1-2", [p.name for p in self.service.processes(parent.id)])
        self.assertEqual(
            [t.name for t in self.service.threads(self.service.node_by_name("进程 1-2", NodeType.PROCESS).id)],
            ["线程 1-2-1"],
        )
        # 记录一直都在
        self.assertEqual(len(self.service.list_activities(node_id=thread.id)), 1)

    def test_delete_area_keeps_snapshot_heatmaps(self) -> None:
        self.service.record_activity(self.english.id, "学习", "2026-10-05")
        life_activities = heatmap.counts_for_area(self.db.conn, self.life.id)

        removed = self.service.delete_node(self.life.id)
        self.assertEqual(removed, 7)  # 领域 3 + 3 个进程（进程 3-1/进程 3-2/进程 3-3）+ 3 条线程
        self.assertNotIn("领域 3", [a.name for a in self.service.areas()])
        self.assertEqual(heatmap.counts_for_area(self.db.conn, self.life.id), life_activities)

        self.service.restore_node(self.life.id)
        self.assertIn("领域 3", [a.name for a in self.service.areas()])
        self.assertEqual(len(self.service.all_nodes()), 18)

    def test_restore_requires_parent_to_be_restored_first(self) -> None:
        self.service.delete_node(self.english.id)
        self.service.delete_node(self.explore.id)
        self.assertEqual(self.service.deleted_entry_count(), 2)
        with self.assertRaises(ValidationError):
            self.service.restore_node(self.english.id)
        self.service.restore_node(self.explore.id)
        self.service.restore_node(self.english.id)
        self.assertEqual(self.service.deleted_entry_count(), 0)
        self.assertEqual(len(self.service.all_nodes()), 18)

    def test_deleted_node_cannot_be_recorded_or_pinned(self) -> None:
        self.service.set_pinned(self.travel.id, True)
        self.service.delete_node(self.travel.id)
        self.assertNotIn(self.travel.id, [node.id for node in self.service.focused_nodes()])
        with self.assertRaises(ValidationError):
            self.service.record_activity(self.travel.id, "不该成功", "2026-10-06")
        with self.assertRaises(ValidationError):
            self.service.set_pinned(self.travel.id, True)
        with self.assertRaises(ValidationError):
            self.service.delete_node(self.travel.id)

    def test_delete_and_restore_are_recorded_in_history(self) -> None:
        self.service.delete_node(self.english.id)
        kinds = [event.event_type for event in self.service.events_for_node(self.english.id)]
        self.assertIn(EventType.DELETE, kinds)
        self.service.restore_node(self.english.id)
        kinds = [event.event_type for event in self.service.events_for_node(self.english.id)]
        self.assertIn(EventType.RESTORE, kinds)

    def test_schema_upgrade_from_v1_keeps_data(self) -> None:
        """老数据库（v1 无删除列）升级到 v2：数据保留、自动备份、结构补齐。"""
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "升级前", "2026-10-07")
        self.db.close()

        legacy = sqlite3.connect(self.db.path)
        legacy.execute("ALTER TABLE nodes DROP COLUMN deleted_at")
        legacy.execute("ALTER TABLE nodes DROP COLUMN deleted_group")
        legacy.execute("ALTER TABLE nodes DROP COLUMN description")
        legacy.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
        legacy.commit()
        legacy.close()

        upgraded = Database(self.db.path)
        upgraded.initialize()
        service = LifeManagerService(upgraded)
        columns = {
            row["name"] for row in upgraded.conn.execute("PRAGMA table_info(nodes)").fetchall()
        }
        self.assertIn("deleted_at", columns)
        self.assertIn("deleted_group", columns)
        self.assertIn("description", columns)
        self.assertEqual(upgraded.get_meta("schema_version"), str(SCHEMA_VERSION))
        self.assertEqual(len(service.list_activities(node_id=english.id)), 1)
        self.assertEqual(len(service.all_nodes()), 18)
        self.assertTrue(list((self.tmp / "backups").glob("pre-migration-*.sqlite3")))
        upgraded.close()

    def test_heatmap_source_is_untouched_by_deletion(self) -> None:
        """删除不会让任何热力图少一次：Activity 快照与统计口径都不变。"""
        self.service.record_activity(self.english.id, "A", "2026-10-08")
        before_process = heatmap.counts_for_process(self.db.conn, self.explore.id)
        before_area = heatmap.counts_for_area(self.db.conn, self.life.id)
        self.service.delete_node(self.english.id)
        self.assertEqual(heatmap.counts_for_process(self.db.conn, self.explore.id), before_process)
        self.assertEqual(heatmap.counts_for_area(self.db.conn, self.life.id), before_area)


class TestActivityEditing(ServiceTestCase):
    """活动记录可以改备注与日期，也可以彻底删除。"""

    def setUp(self) -> None:
        super().setUp()
        self.english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)

    def test_update_note_and_date_moves_heatmap_cell(self) -> None:
        activity = self.service.record_activity(self.english.id, "写错了", "2026-10-09")
        self.assertEqual(heatmap.counts_for_process(self.db.conn, self.explore.id), {"2026-10-09": 1})

        self.service.update_activity(activity.id, note="改正后的备注", activity_date="2026-10-10")

        updated = self.service.require_activity(activity.id)
        self.assertEqual(updated.note, "改正后的备注")
        self.assertEqual(updated.activity_date, "2026-10-10")
        # 归属快照不可变
        self.assertEqual(updated.path_text, "领域 3 → 进程 3-3 → 线程 3-3-2")
        self.assertEqual(heatmap.counts_for_process(self.db.conn, self.explore.id), {"2026-10-10": 1})

    def test_update_rejects_bad_date_and_keeps_record(self) -> None:
        activity = self.service.record_activity(self.english.id, "原始", "2026-10-11")
        with self.assertRaises(ValidationError):
            self.service.update_activity(activity.id, activity_date="10/11/2026")
        self.assertEqual(self.service.require_activity(activity.id).activity_date, "2026-10-11")

    def test_delete_activity_removes_it_from_every_heatmap(self) -> None:
        activity = self.service.record_activity(self.english.id, "误点", "2026-10-12")
        self.assertEqual(self.service.delete_activity(activity.id), None)
        self.assertEqual(self.service.list_activities(), [])
        self.assertEqual(heatmap.counts_for_process(self.db.conn, self.explore.id), {})
        self.assertEqual(heatmap.counts_for_area(self.db.conn, self.service.node_by_name("领域 3", NodeType.AREA).id), {})
        with self.assertRaises(ValidationError):
            self.service.delete_activity(activity.id)


class TestProcessDescription(ServiceTestCase):
    """进程注释：一句小字，显示在进程热力图标题下面。"""

    def setUp(self) -> None:
        super().setUp()
        self.explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)

    def test_counts_all_aggregates_each_record_once(self) -> None:
        """总热力图：汇总所有领域与进程，每条记录只算一次。"""
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        daily = self.service.node_by_name("进程 1-1", NodeType.PROCESS)
        self.service.record_activity(english.id, "A", "2026-10-01")
        self.service.record_activity(english.id, "B", "2026-10-01")
        self.service.record_activity(daily.id, "C", "2026-10-01")
        self.service.record_activity(daily.id, "D", "2026-10-02")

        counts = heatmap.counts_all(self.db.conn)
        self.assertEqual(counts, {"2026-10-01": 3, "2026-10-02": 1})

        # 各领域相加 == 总数（不因同时属于进程与领域而重复）
        per_day_area = sum(
            heatmap.counts_for_area(self.db.conn, area.id).get("2026-10-01", 0)
            for area in self.service.areas()
        )
        per_day_process = sum(
            heatmap.counts_for_process(self.db.conn, process.id).get("2026-10-01", 0)
            for process in self.service.processes()
        )
        self.assertEqual((per_day_area, per_day_process), (3, 3))

        # 支持日期窗口
        self.assertEqual(
            heatmap.counts_all(self.db.conn, "2026-10-02"), {"2026-10-02": 1}
        )

    def test_set_and_clear_description(self) -> None:
        self.assertEqual(self.service.require_node(self.explore.id).description, "")
        self.service.set_description(self.explore.id, "  精听加跟读，先保连续 4 周  ")
        self.assertEqual(
            self.service.require_node(self.explore.id).description, "精听加跟读，先保连续 4 周"
        )
        # 注释变化也写进历史事件，便于追溯
        events = self.service.events_for_node(self.explore.id)
        description_events = [e for e in events if e.event_type is EventType.DESCRIPTION]
        self.assertEqual(len(description_events), 1)
        self.assertIn("注释：", describe_event(description_events[0]))

        self.service.set_description(self.explore.id, "")
        self.assertEqual(self.service.require_node(self.explore.id).description, "")
        self.assertEqual(self.service.event_count(self.explore.id), len(events) + 1)

    def test_description_survives_export_and_import(self) -> None:
        self.service.set_description(self.explore.id, "旅途中的学习记录")
        exported = self.service.export_json(self.tmp / "with-note.json")
        self.service.set_description(self.explore.id, "被改掉了")

        self.service.import_json(exported)

        self.assertEqual(
            self.service.require_node(self.explore.id).description, "旅途中的学习记录"
        )
        self.assertEqual(len(self.service.all_nodes()), 18)

    def test_import_json_without_description_field_still_works(self) -> None:
        """旧版导出的 JSON 没有 description 字段，导入后按空注释处理。"""
        exported = self.service.export_json(self.tmp / "legacy.json")
        payload = json.loads(exported.read_text(encoding="utf-8"))
        for node in payload["nodes"]:
            node.pop("description", None)
        legacy = self.tmp / "legacy-no-description.json"
        legacy.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        self.service.import_json(legacy)

        self.assertEqual(self.service.require_node(self.explore.id).description, "")
        self.assertEqual(len(self.service.all_nodes()), 18)

    def test_description_is_separate_from_next_action(self) -> None:
        self.service.set_description(self.explore.id, "说明文字")
        self.service.set_next_action(self.explore.id, "下一步文字")
        node = self.service.require_node(self.explore.id)
        self.assertEqual((node.description, node.next_action), ("说明文字", "下一步文字"))


class TestAutoBackup(ServiceTestCase):
    """每天第一次启动自动备份一次（含日记），且绝不自动删除旧备份。"""

    def test_creates_one_backup_per_day(self) -> None:
        from app.models import today_str

        today = today_str()
        first = self.service.auto_backup_if_needed()
        self.assertIsNotNone(first)
        self.assertTrue(first.exists())
        self.assertTrue(first.name.startswith(f"auto-{today}-"), first.name)

        # 同一天再启动一次：不再新增
        self.assertIsNone(self.service.auto_backup_if_needed())
        todays = list((self.tmp / "backups").glob(f"auto-{today}-*.sqlite3"))
        self.assertEqual(len(todays), 1)

    def test_old_backup_does_not_block_today(self) -> None:
        """只有旧日期的自动备份时，今天仍然要新建一份，且旧文件保持不动。"""
        (self.tmp / "backups").mkdir(parents=True, exist_ok=True)
        old = self.tmp / "backups" / "auto-2020-01-01-000000.sqlite3"
        old.write_bytes(b"old")

        created = self.service.auto_backup_if_needed()

        self.assertIsNotNone(created)
        self.assertNotEqual(created, old)
        self.assertEqual(len(list((self.tmp / "backups").glob("auto-*.sqlite3"))), 2)
        self.assertEqual(old.read_bytes(), b"old")

    def test_auto_backup_includes_the_diary(self) -> None:
        from app import diary as diary_store

        diary_store.write_day("2026-10-15", "自动备份要带上这一天的日记")
        created = self.service.auto_backup_if_needed()
        self.assertIsNotNone(created)
        sibling = created.with_name(created.stem + ".diary.md")
        self.assertTrue(sibling.exists(), "自动备份没有带上日记")
        self.assertIn("自动备份要带上这一天的日记", sibling.read_text(encoding="utf-8"))

        # 日记被清空后，可以从这份自动备份里找回
        diary_store.write_day("2026-10-15", "")
        self.assertEqual(diary_store.read_day("2026-10-15"), "")
        self.assertIn("自动备份要带上这一天的日记", sibling.read_text(encoding="utf-8"))

    def test_auto_backup_never_deletes_older_backups(self) -> None:
        """应用不做自动清理：手工备份与旧备份都必须留着。"""
        manual = self.service.backup("manual")
        (self.tmp / "backups" / "auto-2020-01-01-000000.sqlite3").write_bytes(b"old")
        self.service.auto_backup_if_needed()
        backups = list((self.tmp / "backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 3)
        self.assertIn(manual, backups)

    def test_latest_auto_backup_pointer(self) -> None:
        self.assertIsNone(self.service.latest_auto_backup())
        (self.tmp / "backups").mkdir(parents=True, exist_ok=True)
        (self.tmp / "backups" / "auto-2020-01-01-000000.sqlite3").write_bytes(b"old")
        newest = self.service.auto_backup_if_needed()
        self.assertEqual(self.service.latest_auto_backup(), newest)


class TestDataCorruptionHandling(ServiceTestCase):
    def test_corrupt_database_raises_instead_of_wiping(self) -> None:
        broken_path = self.tmp / "corrupt.sqlite3"
        broken_path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 512 + b"garbage" * 64)
        db = Database(broken_path)
        with self.assertRaises(DataCorruptionError):
            db.initialize()
        self.assertTrue(broken_path.exists())
        self.assertGreater(broken_path.stat().st_size, 0)


class TestChildOrdering(ServiceTestCase):
    """同级顺序：只改显示顺序，不动归属、不影响其它层级。"""

    def test_reorder_processes_inside_an_area(self) -> None:
        area = self.service.areas()[0]
        before = [node.id for node in self.service.processes(area.id)]
        self.assertGreaterEqual(len(before), 3)
        target = [before[2], before[0], before[1]]
        self.service.apply_child_order(area.id, target)
        after = [node.id for node in self.service.processes(area.id)]
        self.assertEqual(after, target + before[3:])

    def test_reorder_is_limited_to_that_level(self) -> None:
        first, second = self.service.areas()[:2]
        before_first = [node.id for node in self.service.processes(first.id)]
        before_second = [node.id for node in self.service.processes(second.id)]
        self.service.apply_child_order(first.id, list(reversed(before_first)))
        self.assertEqual(
            [node.id for node in self.service.processes(first.id)],
            list(reversed(before_first)),
        )
        self.assertEqual(
            [node.id for node in self.service.processes(second.id)], before_second
        )

    def test_reorder_top_level_areas(self) -> None:
        areas = [node.id for node in self.service.areas()]
        self.assertGreaterEqual(len(areas), 3)
        target = list(reversed(areas))
        self.service.apply_child_order(None, target)
        self.assertEqual([node.id for node in self.service.areas()], target)

    def test_unknown_and_foreign_ids_are_ignored(self) -> None:
        first, second = self.service.areas()[:2]
        before = [node.id for node in self.service.processes(first.id)]
        foreign = [node.id for node in self.service.processes(second.id)]
        self.service.apply_child_order(first.id, ["不存在的节点"] + foreign)
        self.assertEqual(
            [node.id for node in self.service.processes(first.id)], before
        )
        self.assertEqual(
            [node.id for node in self.service.processes(second.id)], foreign
        )

    def test_threads_can_be_reordered_inside_a_process(self) -> None:
        process = None
        for area in self.service.areas():
            for candidate in self.service.processes(area.id):
                if len(self.service.threads(candidate.id)) >= 2:
                    process = candidate
                    break
            if process is not None:
                break
        self.assertIsNotNone(process, "初始分类里应有一个带两条线程的进程")
        threads = [node.id for node in self.service.threads(process.id)]
        flipped = list(reversed(threads))
        self.service.apply_child_order(process.id, flipped)
        self.assertEqual([node.id for node in self.service.threads(process.id)], flipped)


class TestActivityExport(ServiceTestCase):
    """把某个节点的全部活动记录导出成 CSV。"""

    def setUp(self) -> None:
        super().setUp()
        self.area = self.service.areas()[0]
        self.process = self.service.processes(self.area.id)[0]
        self.thread = None
        for candidate in self.service.processes(self.area.id):
            threads = self.service.threads(candidate.id)
            if threads:
                self.process = candidate
                self.thread = threads[0]
                break
        self.assertIsNotNone(self.thread, "初始分类里应有一个带线程的进程")

    def test_export_area_covers_children_and_sorts_by_date(self) -> None:
        self.service.record_activity(self.thread.id, "线程那一条", "2026-10-05")
        self.service.record_activity(self.process.id, "进程那一条", "2026-10-01")

        target = self.tmp / "area.csv"
        path, count = self.service.export_activities_csv(self.area.id, target)

        self.assertEqual(path, target)
        self.assertEqual(count, 2)
        lines = target.read_text(encoding="utf-8-sig").strip().splitlines()
        self.assertEqual(len(lines), 3, "应该是表头 + 两条记录")
        self.assertIn("日期", lines[0])
        self.assertIn("2026-10-01", lines[1])
        self.assertIn("进程那一条", lines[1])
        self.assertIn("2026-10-05", lines[2])
        self.assertIn("线程那一条", lines[2])

    def test_export_thread_only_includes_its_own_records(self) -> None:
        self.service.record_activity(self.thread.id, "属于线程", "2026-10-06")
        self.service.record_activity(self.process.id, "属于进程", "2026-10-06")

        target = self.tmp / "thread.csv"
        _path, count = self.service.export_activities_csv(self.thread.id, target)
        self.assertEqual(count, 1)
        text = target.read_text(encoding="utf-8-sig")
        self.assertIn("属于线程", text)
        self.assertNotIn("属于进程", text)

    def test_export_empty_node_writes_header_only(self) -> None:
        target = self.tmp / "empty.csv"
        _path, count = self.service.export_activities_csv(self.process.id, target)
        self.assertEqual(count, 0)
        lines = target.read_text(encoding="utf-8-sig").strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("备注", lines[0])

    def test_default_file_name_mentions_the_node(self) -> None:
        path, _count = self.service.export_activities_csv(self.process.id)
        self.assertEqual(path.parent, paths.EXPORT_DIR)
        self.assertIn(self.process.name, path.name)
        self.assertTrue(path.name.endswith(".csv"))


if __name__ == "__main__":
    unittest.main()
