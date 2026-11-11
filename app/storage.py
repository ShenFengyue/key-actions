"""SQLite 存储层：表结构、迁移、初始分类数据、事务与完整性检查。

SQLite 是唯一正式数据源。所有写入都通过事务完成。
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import paths
from .models import NodeType, PathStep, Status, now_stamp

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS nodes (
        id          TEXT PRIMARY KEY,
        type        TEXT NOT NULL CHECK (type IN ('area', 'process', 'thread')),
        name        TEXT NOT NULL,
        parent_id   TEXT REFERENCES nodes(id) ON DELETE RESTRICT,
        position    INTEGER NOT NULL DEFAULT 0,
        status      TEXT NOT NULL,
        pinned      INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
        next_action TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        archived_at   TEXT,
        deleted_at    TEXT,
        deleted_group TEXT,
        description   TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS associations (
        id         TEXT PRIMARY KEY,
        thread_id  TEXT NOT NULL REFERENCES nodes(id) ON DELETE RESTRICT,
        process_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        ended_at   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS activities (
        id            TEXT PRIMARY KEY,
        node_id       TEXT NOT NULL REFERENCES nodes(id) ON DELETE RESTRICT,
        node_type     TEXT NOT NULL CHECK (node_type IN ('process', 'thread')),
        activity_date TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        note          TEXT NOT NULL DEFAULT '',
        area_id       TEXT,
        process_id    TEXT,
        thread_id     TEXT,
        path_snapshot TEXT NOT NULL DEFAULT '[]'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id           TEXT PRIMARY KEY,
        node_id      TEXT NOT NULL,
        event_type   TEXT NOT NULL,
        before_value TEXT,
        after_value  TEXT,
        created_at   TEXT NOT NULL,
        note         TEXT NOT NULL DEFAULT ''
    )
    """,
    # 左栏标语：每改一次插入一条，旧的原样留着，等于自带历史。
    """
    CREATE TABLE IF NOT EXISTS slogans (
        id         TEXT PRIMARY KEY,
        text       TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    # AI 对话历史：只存对话本身，不存任何日记 / 活动 / 备份内容。
    """
    CREATE TABLE IF NOT EXISTS ai_conversations (
        id         TEXT PRIMARY KEY,
        title      TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_messages (
        id              TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content         TEXT NOT NULL,
        created_at      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ai_messages_conv ON ai_messages(conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type)",
    "CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(activity_date)",
    "CREATE INDEX IF NOT EXISTS idx_activities_area ON activities(area_id, activity_date)",
    "CREATE INDEX IF NOT EXISTS idx_activities_process ON activities(process_id, activity_date)",
    "CREATE INDEX IF NOT EXISTS idx_activities_thread ON activities(thread_id, activity_date)",
    "CREATE INDEX IF NOT EXISTS idx_activities_node ON activities(node_id, activity_date)",
    "CREATE INDEX IF NOT EXISTS idx_events_node ON events(node_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_assoc_thread ON associations(thread_id, ended_at)",
    "CREATE INDEX IF NOT EXISTS idx_assoc_process ON associations(process_id, ended_at)",
)

# 增量变更：升级前会自动创建本地备份；每条语句只执行一次。
MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: (
        "ALTER TABLE nodes ADD COLUMN deleted_at TEXT",
        "ALTER TABLE nodes ADD COLUMN deleted_group TEXT",
    ),
    3: ("ALTER TABLE nodes ADD COLUMN description TEXT NOT NULL DEFAULT ''",),
    4: (
        """
        CREATE TABLE IF NOT EXISTS slogans (
            id         TEXT PRIMARY KEY,
            text       TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
    ),
    5: (
        """
        CREATE TABLE IF NOT EXISTS ai_conversations (
            id         TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_messages (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content         TEXT NOT NULL,
            created_at      TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_ai_messages_conv ON ai_messages(conversation_id, created_at)",
    ),
}

# 初始分类：必须与用户提供的树完全一致，不擅自重命名或重新归类。
SEED_TREE: tuple[dict[str, Any], ...] = (
    {
        "name": "领域 1",
        "children": (
            {"name": "进程 1-1", "children": ()},
            {"name": "进程 1-2", "children": ({"name": "线程 1-2-1"},)},
            {"name": "进程 1-3", "children": ()},
            {"name": "进程 1-4", "children": ()},
        ),
    },
    {
        "name": "领域 2",
        "children": (
            {
                "name": "进程 2-1",
                "children": ({"name": "线程 2-1-1"}, {"name": "线程 2-1-2"}),
            },
            {"name": "真实领域 2", "children": ()},
        ),
    },
    {
        "name": "领域 3",
        "children": (
            {"name": "进程 3-1", "children": ({"name": "线程 3-1-1"},)},
            {"name": "进程 3-2", "children": ()},
            {
                "name": "进程 3-3",
                "children": ({"name": "线程 3-3-1"}, {"name": "线程 3-3-2"}),
            },
        ),
    },
)

# 关联关系示例（产品文档 4.2 节给出的例子）。
SEED_ASSOCIATIONS: tuple[tuple[str, str], ...] = (
    ("线程 3-3-2", "进程 1-4"),
    ("线程 3-3-2", "真实领域 2"),
    ("线程 3-1-1", "进程 1-3"),
    ("线程 3-1-1", "进程 1-4"),
)


class DataCorruptionError(RuntimeError):
    """数据库文件无法正常读取时抛出。绝不静默清空或覆盖数据。"""


def new_id() -> str:
    return uuid.uuid4().hex


class Database:
    """SQLite 连接的薄封装，负责表结构、迁移与事务。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else paths.DATABASE_PATH
        self._conn: sqlite3.Connection | None = None

    # ---------------------------------------------------------------- 连接
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("数据库尚未连接，请先调用 connect()")
        return self._conn

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(self.path, timeout=15.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute("PRAGMA busy_timeout = 15000")
            # 立即触发一次读取，尽早暴露损坏的数据库文件。
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError as exc:
            try:
                conn.close()
            except (sqlite3.Error, UnboundLocalError):
                pass
            raise DataCorruptionError(
                f"数据库文件无法打开或已损坏：{self.path}\n{exc}"
            ) from exc
        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.execute("PRAGMA optimize")
            except sqlite3.DatabaseError:  # pragma: no cover
                pass
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """安全事务：任一步骤失败则整体回滚。"""
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    # ------------------------------------------------------------ 初始化
    def initialize(self, seed: bool = True) -> None:
        """建立或升级表结构，并在空库时写入初始分类。"""
        conn = self.connect()
        existing = self._existing_tables(conn)
        current_version = self._schema_version(conn) if "meta" in existing else 0

        if existing and current_version and current_version < SCHEMA_VERSION:
            # 更新数据结构前先创建本地备份。
            backup_path = self._pre_migration_backup()
            logger.info("结构升级前已创建备份：%s", backup_path)

        with self.transaction() as tx:
            for statement in SCHEMA_STATEMENTS:
                tx.execute(statement)
            # 结构升级：新库因为建表语句已包含新列而跳过列冲突语句。
            for version in range(current_version + 1, SCHEMA_VERSION + 1):
                for statement in MIGRATIONS.get(version, ()):
                    try:
                        tx.execute(statement)
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" not in str(exc).lower():
                            raise
                    logger.info("应用结构升级 v%d：%s", version, statement)
            tx.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
            tx.execute(
                "INSERT INTO meta(key, value) VALUES('created_at', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (now_stamp(),),
            )

        if seed and self.node_count() == 0 and not self._meta_flag("seeded"):
            self._seed()

    @staticmethod
    def _existing_tables(conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {row["name"] for row in rows}

    @staticmethod
    def _schema_version(conn: sqlite3.Connection) -> int:
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.DatabaseError:
            return 0
        return int(row["value"]) if row else 0

    def _meta_flag(self, key: str) -> bool:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return bool(row and row["value"] == "1")

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _pre_migration_backup(self) -> Path:
        destinations = paths.BACKUP_DIR
        destinations.mkdir(parents=True, exist_ok=True)
        target = destinations / f"pre-migration-{now_stamp().replace(':', '').replace(' ', '-')}.sqlite3"
        if self.path.exists():
            try:
                source = sqlite3.connect(self.path)
                destination = sqlite3.connect(target)
                with destination:
                    source.backup(destination)
                source.close()
                destination.close()
            except sqlite3.DatabaseError:
                shutil.copy2(self.path, target)
        return target

    # ---------------------------------------------------------- 初始数据
    def node_count(self) -> int:
        row = self.conn.execute("SELECT count(*) AS n FROM nodes").fetchone()
        return int(row["n"])

    def _seed(self) -> None:
        """写入初始分类（Area → Process → Thread）与关联示例。"""
        stamp = now_stamp()
        created: dict[str, str] = {}
        with self.transaction() as tx:
            for area_index, area in enumerate(SEED_TREE):
                area_id = new_id()
                tx.execute(
                    "INSERT INTO nodes(id, type, name, parent_id, position, status, pinned,"
                    " next_action, created_at, updated_at) VALUES(?,?,?,?,?,?,0,'',?,?)",
                    (
                        area_id,
                        NodeType.AREA.value,
                        area["name"],
                        None,
                        area_index,
                        Status.DOING.value,
                        stamp,
                        stamp,
                    ),
                )
                created[area["name"]] = area_id
                self._insert_event(
                    tx,
                    node_id=area_id,
                    event_type="create",
                    before=None,
                    after={"name": area["name"], "type": NodeType.AREA.value, "path": [area["name"]]},
                    note="初始分类",
                    created_at=stamp,
                )
                for process_index, process in enumerate(area["children"]):
                    process_id = new_id()
                    tx.execute(
                        "INSERT INTO nodes(id, type, name, parent_id, position, status, pinned,"
                        " next_action, created_at, updated_at) VALUES(?,?,?,?,?,?,0,'',?,?)",
                        (
                            process_id,
                            NodeType.PROCESS.value,
                            process["name"],
                            area_id,
                            process_index,
                            Status.DOING.value,
                            stamp,
                            stamp,
                        ),
                    )
                    created[process["name"]] = process_id
                    self._insert_event(
                        tx,
                        node_id=process_id,
                        event_type="create",
                        before=None,
                        after={
                            "name": process["name"],
                            "type": NodeType.PROCESS.value,
                            "path": [area["name"], process["name"]],
                        },
                        note="初始分类",
                        created_at=stamp,
                    )
                    for thread_index, thread in enumerate(process.get("children", ())):
                        thread_id = new_id()
                        tx.execute(
                            "INSERT INTO nodes(id, type, name, parent_id, position, status, pinned,"
                            " next_action, created_at, updated_at) VALUES(?,?,?,?,?,?,0,'',?,?)",
                            (
                                thread_id,
                                NodeType.THREAD.value,
                                thread["name"],
                                process_id,
                                thread_index,
                                Status.DOING.value,
                                stamp,
                                stamp,
                            ),
                        )
                        created[thread["name"]] = thread_id
                        self._insert_event(
                            tx,
                            node_id=thread_id,
                            event_type="create",
                            before=None,
                            after={
                                "name": thread["name"],
                                "type": NodeType.THREAD.value,
                                "path": [area["name"], process["name"], thread["name"]],
                            },
                            note="初始分类",
                            created_at=stamp,
                        )

            for thread_name, process_name in SEED_ASSOCIATIONS:
                thread_id = created.get(thread_name)
                process_id = created.get(process_name)
                if not thread_id or not process_id:
                    continue
                tx.execute(
                    "INSERT INTO associations(id, thread_id, process_id, created_at, ended_at)"
                    " VALUES(?,?,?,?,NULL)",
                    (new_id(), thread_id, process_id, stamp),
                )
                self._insert_event(
                    tx,
                    node_id=thread_id,
                    event_type="associate",
                    before=None,
                    after={"thread_id": thread_id, "process_id": process_id,
                           "thread_name": thread_name, "process_name": process_name},
                    note="关联示例",
                    created_at=stamp,
                )
            tx.execute(
                "INSERT INTO meta(key, value) VALUES('seeded', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = '1'"
            )
        logger.info("已写入初始分类：%d 个节点", self.node_count())

    @staticmethod
    def _insert_event(
        tx: sqlite3.Connection,
        *,
        node_id: str,
        event_type: str,
        before: Any,
        after: Any,
        created_at: str,
        note: str = "",
    ) -> str:
        event_id = new_id()
        tx.execute(
            "INSERT INTO events(id, node_id, event_type, before_value, after_value,"
            " created_at, note) VALUES(?,?,?,?,?,?,?)",
            (
                event_id,
                node_id,
                event_type,
                None if before is None else json.dumps(before, ensure_ascii=False),
                None if after is None else json.dumps(after, ensure_ascii=False),
                created_at,
                note,
            ),
        )
        return event_id

    # ------------------------------------------------------------ 完整性
    def counts(self) -> dict[str, Any]:
        """只做计数统计，供界面每次刷新使用（不做完整检查，开销很小）。"""
        return {
            "areas": self._count("nodes", "type = 'area' AND deleted_at IS NULL"),
            "processes": self._count("nodes", "type = 'process' AND deleted_at IS NULL"),
            "threads": self._count("nodes", "type = 'thread' AND deleted_at IS NULL"),
            "deleted_nodes": self._count("nodes", "deleted_at IS NOT NULL"),
            "activities": self._count("activities"),
            "events": self._count("events"),
            "active_associations": self._count("associations", "ended_at IS NULL"),
            "ended_associations": self._count("associations", "ended_at IS NOT NULL"),
            "schema_version": int(self.get_meta("schema_version") or 0),
            "checked_at": now_stamp(),
        }

    def integrity_report(self) -> dict[str, Any]:
        """数据完整性检查：结构、外键、快照与归属一致性。"""
        findings: list[dict[str, str]] = []

        check = self.conn.execute("PRAGMA integrity_check").fetchone()
        sqlite_result = check[0] if check else "unknown"
        if sqlite_result != "ok":
            findings.append({"level": "error", "message": f"SQLite 结构检查未通过：{sqlite_result}"})

        for row in self.conn.execute("PRAGMA foreign_key_check").fetchall():
            findings.append(
                {
                    "level": "error",
                    "message": f"外键损坏：表 {row[0]} 第 {row[1]} 行 → 表 {row[2]}",
                }
            )

        stats = self.counts()

        orphan = self.conn.execute(
            "SELECT count(*) AS n FROM nodes n WHERE n.parent_id IS NULL AND n.type <> 'area'"
        ).fetchone()["n"]
        if orphan:
            findings.append({"level": "error", "message": f"{orphan} 个 Process/Thread 缺少上级归属"})

        bad_parent = self.conn.execute(
            """
            SELECT count(*) AS n FROM nodes child
            JOIN nodes parent ON parent.id = child.parent_id
            WHERE (child.type = 'process' AND parent.type <> 'area')
               OR (child.type = 'thread'  AND parent.type <> 'process')
            """
        ).fetchone()["n"]
        if bad_parent:
            findings.append({"level": "error", "message": f"{bad_parent} 个节点的上级类型不合法"})

        orphan_deleted = self.conn.execute(
            """
            SELECT count(*) AS n FROM nodes child
            JOIN nodes parent ON parent.id = child.parent_id
            WHERE child.deleted_at IS NOT NULL AND parent.deleted_at IS NULL
            """
        ).fetchone()["n"]
        if orphan_deleted:
            findings.append(
                {
                    "level": "warning",
                    "message": (
                        f"{orphan_deleted} 个已删除节点的上级仍在使用中"
                        "（恢复该节点时会一起回到树里）"
                    ),
                }
            )

        bad_snapshot = 0
        for row in self.conn.execute(
            "SELECT id, node_type, area_id, process_id, thread_id, path_snapshot FROM activities"
        ).fetchall():
            steps: list[PathStep] = []
            try:
                steps = [PathStep.from_dict(item) for item in json.loads(row["path_snapshot"] or "[]")]
            except (json.JSONDecodeError, TypeError, KeyError, ValueError):
                bad_snapshot += 1
                continue
            if row["node_type"] == NodeType.PROCESS.value:
                if not row["area_id"] or not row["process_id"] or row["thread_id"]:
                    bad_snapshot += 1
            else:
                if not row["area_id"] or not row["process_id"] or not row["thread_id"]:
                    bad_snapshot += 1
            if not steps or steps[-1].name == "":
                bad_snapshot += 1
        if bad_snapshot:
            findings.append({"level": "error", "message": f"{bad_snapshot} 条活动记录缺少完好的归属快照"})

        bad_assoc = self.conn.execute(
            """
            SELECT count(*) AS n FROM associations a
            JOIN nodes t ON t.id = a.thread_id
            JOIN nodes p ON p.id = a.process_id
            WHERE t.type <> 'thread' OR p.type <> 'process'
            """
        ).fetchone()["n"]
        if bad_assoc:
            findings.append({"level": "error", "message": f"{bad_assoc} 条关联关系的节点类型不合法"})

        pinned = self.conn.execute("SELECT count(*) AS n FROM nodes WHERE pinned = 1").fetchone()["n"]
        if pinned > 3:
            findings.append({"level": "warning", "message": f"置顶节点数为 {pinned}，超过 3 个上限"})

        duplicates = self.conn.execute(
            "SELECT count(*) AS n FROM (SELECT id FROM nodes GROUP BY id HAVING count(*) > 1)"
        ).fetchone()["n"]
        if duplicates:
            findings.append({"level": "error", "message": f"{duplicates} 个节点 ID 重复"})

        if not findings:
            findings.append({"level": "ok", "message": "未发现数据问题，节点、活动、关联与历史事件一致。"})
        return {"stats": stats, "findings": findings, "sqlite": sqlite_result}

    def _count(self, table: str, where: str = "") -> int:
        sql = f"SELECT count(*) AS n FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(self.conn.execute(sql).fetchone()["n"])
