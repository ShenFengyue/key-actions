"""业务逻辑层：节点、归属、关联、活动记录、历史事件、备份与导入导出。

规则二（归属变更必须保留历史）在这里落地：
    - 移动节点只改动 nodes.parent_id，并追加历史事件；
    - 活动表的活动快照永不被移动操作改写；
    - 所有多步写入都在同一个 SQLite 事务中完成，任一步失败即整体回滚。
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Sequence

from . import paths
from .models import (
    Activity,
    Association,
    Event,
    EventType,
    Node,
    NodeType,
    PathStep,
    Slogan,
    Status,
    today_str,
    now_stamp,
)
from .storage import Database, new_id

logger = logging.getLogger(__name__)

# 导出格式标识：历史导出件里写的都是 life-manager-json，改名后保持原值，
# 否则旧导出会被判成「不是本应用导出的文件」。
JSON_FORMAT = "life-manager-json"
JSON_FORMAT_VERSION = 1
FOCUS_LIMIT = 3
SLOGAN_MAX_LENGTH = 60


class ValidationError(RuntimeError):
    """用户操作不合法（例如把 Thread 挂到 Area 下）。"""


class ImportValidationError(RuntimeError):
    """导入文件结构不合法。导入失败时保留现有数据。"""


class LifeManagerService:
    """应用的全部业务操作入口。"""

    def __init__(self, db: Database) -> None:
        self.db = db
        # 每次写入自增，界面据此判断哪些页面需要重绘（避免无谓刷新与闪烁）。
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    def _touch(self) -> None:
        self._revision += 1

    # =========================================================== 读取节点
    def get_node(self, node_id: str) -> Node | None:
        row = self.db.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return Node.from_row(row) if row else None

    def require_node(self, node_id: str) -> Node:
        node = self.get_node(node_id)
        if node is None:
            raise ValidationError(f"节点不存在：{node_id}")
        return node

    def node_by_name(self, name: str, node_type: NodeType | None = None) -> Node | None:
        sql = "SELECT * FROM nodes WHERE name = ?"
        params: list[Any] = [name]
        if node_type is not None:
            sql += " AND type = ?"
            params.append(node_type.value)
        row = self.db.conn.execute(sql, params).fetchone()
        return Node.from_row(row) if row else None

    def all_nodes(
        self, include_archived: bool = True, include_deleted: bool = False
    ) -> list[Node]:
        sql = "SELECT * FROM nodes"
        conditions: list[str] = []
        if not include_deleted:
            conditions.append("deleted_at IS NULL")
        if not include_archived:
            conditions.append("status <> '已归档'")
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY type, position, name"
        return [Node.from_row(row) for row in self.db.conn.execute(sql).fetchall()]

    def children(
        self,
        parent_id: str | None,
        node_type: NodeType | None = None,
        include_deleted: bool = False,
    ) -> list[Node]:
        if parent_id is None:
            sql = "SELECT * FROM nodes WHERE parent_id IS NULL"
            params: list[Any] = []
        else:
            sql = "SELECT * FROM nodes WHERE parent_id = ?"
            params = [parent_id]
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        if node_type is not None:
            sql += " AND type = ?"
            params.append(node_type.value)
        sql += " ORDER BY position, name"
        return [Node.from_row(row) for row in self.db.conn.execute(sql, params).fetchall()]

    def areas(self, include_archived: bool = True) -> list[Node]:
        nodes = self.children(None, NodeType.AREA)
        if include_archived:
            return nodes
        return [node for node in nodes if not node.is_archived]

    def processes(self, area_id: str | None = None) -> list[Node]:
        if area_id is None:
            rows = self.db.conn.execute(
                "SELECT * FROM nodes WHERE type = 'process' AND deleted_at IS NULL"
                " ORDER BY position, name"
            ).fetchall()
            return [Node.from_row(row) for row in rows]
        return self.children(area_id, NodeType.PROCESS)

    def threads(self, process_id: str | None = None) -> list[Node]:
        if process_id is None:
            rows = self.db.conn.execute(
                "SELECT * FROM nodes WHERE type = 'thread' AND deleted_at IS NULL"
                " ORDER BY position, name"
            ).fetchall()
            return [Node.from_row(row) for row in rows]
        return self.children(process_id, NodeType.THREAD)

    def path_of(self, node_id: str) -> list[PathStep]:
        """返回节点的当前归属路径（Area → Process → Thread）。"""
        steps: list[PathStep] = []
        current = self.get_node(node_id)
        guard = 0
        while current is not None and guard < 10:
            steps.append(PathStep(id=current.id, name=current.name, type=current.type))
            current = self.get_node(current.parent_id) if current.parent_id else None
            guard += 1
        steps.reverse()
        return steps

    def path_text(self, node_id: str) -> str:
        steps = self.path_of(node_id)
        return " → ".join(step.name for step in steps) if steps else "（无归属）"

    def ancestors(self, node_id: str) -> dict[str, str | None]:
        """返回节点所属的 area_id / process_id（用于活动快照）。"""
        node = self.require_node(node_id)
        if node.type is NodeType.PROCESS:
            return {"area_id": node.parent_id, "process_id": node.id}
        if node.type is NodeType.THREAD:
            process = self.get_node(node.parent_id) if node.parent_id else None
            return {
                "area_id": process.parent_id if process else None,
                "process_id": process.id if process else None,
            }
        return {"area_id": node.id, "process_id": None}

    def focused_nodes(self) -> list[Node]:
        rows = self.db.conn.execute(
            "SELECT * FROM nodes WHERE pinned = 1 AND type <> 'area' AND deleted_at IS NULL"
            " ORDER BY updated_at DESC LIMIT ?",
            (FOCUS_LIMIT,),
        ).fetchall()
        return [Node.from_row(row) for row in rows]

    # ============================================================ 左栏标语
    def current_slogan(self) -> Slogan | None:
        """当前标语（最新一条）；从来没设置过则为 None。"""
        row = self.db.conn.execute(
            "SELECT * FROM slogans ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return Slogan.from_row(row) if row else None

    def slogan_history(self) -> list[Slogan]:
        """全部标语记录，最新的排在最前（第一条就是当前显示的）。"""
        rows = self.db.conn.execute(
            "SELECT * FROM slogans ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
        return [Slogan.from_row(row) for row in rows]

    def set_slogan(self, text: str) -> Slogan:
        """改标语：插入一条新记录，旧记录原样留着 —— 历史就是这么来的。

        内容与当前完全相同就不重复记录；传空字符串表示清空标语。
        """
        cleaned = (text or "").strip()
        if len(cleaned) > SLOGAN_MAX_LENGTH:
            raise ValidationError(f"标语最多 {SLOGAN_MAX_LENGTH} 个字。")
        current = self.current_slogan()
        if current is not None and current.text == cleaned:
            return current
        slogan_id = new_id()
        stamp = now_stamp()
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO slogans(id, text, created_at) VALUES(?,?,?)",
                (slogan_id, cleaned, stamp),
            )
        logger.info("更新标语：%s", cleaned or "（已清空）")
        self._touch()
        return Slogan(id=slogan_id, text=cleaned, created_at=stamp)

    def stats(self) -> dict[str, int]:
        return self.db.counts()

    # =========================================================== 节点写操作
    def create_node(self, node_type: NodeType, name: str, parent_id: str | None) -> Node:
        name = (name or "").strip()
        if not name:
            raise ValidationError("节点名称不能为空。")
        if node_type is NodeType.AREA:
            if parent_id not in (None, ""):
                raise ValidationError("Area 是顶层节点，不能有上级。")
            parent_id = None
        else:
            if not parent_id:
                raise ValidationError(f"新建{node_type.label}时必须指定上级归属。")
            parent = self.require_node(parent_id)
            if parent.type is not node_type.parent_type:
                raise ValidationError(
                    f"{node_type.label}只能归属到{node_type.parent_type.label}下。"
                )

        position = self._next_position(parent_id)
        stamp = now_stamp()
        node_id = new_id()
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO nodes(id, type, name, parent_id, position, status, pinned,"
                " next_action, created_at, updated_at) VALUES(?,?,?,?,?,?,0,'',?,?)",
                (node_id, node_type.value, name, parent_id, position,
                 Status.DOING.value, stamp, stamp),
            )
            self.db._insert_event(
                tx,
                node_id=node_id,
                event_type=EventType.CREATE.value,
                before=None,
                after={
                    "name": name,
                    "type": node_type.value,
                    "path": [step.name for step in self.path_of(node_id)],
                },
                created_at=stamp,
                note="新建节点",
            )
        logger.info("新建节点：%s（%s）", name, node_type.label)
        self._touch()
        return self.require_node(node_id)

    def _next_position(self, parent_id: str | None) -> int:
        if parent_id is None:
            row = self.db.conn.execute(
                "SELECT max(position) AS p FROM nodes WHERE parent_id IS NULL"
            ).fetchone()
        else:
            row = self.db.conn.execute(
                "SELECT max(position) AS p FROM nodes WHERE parent_id = ?", (parent_id,)
            ).fetchone()
        return int(row["p"] + 1) if row and row["p"] is not None else 0

    def apply_child_order(self, parent_id: str | None, ordered_ids: list[str]) -> None:
        """按给定顺序重排同一父级下的节点（只改显示顺序）。

        记录地图与热力图都按同一个 `position` 排序，所以它们会一起变。
        这里**只动 position**：不改归属、不写历史事件（调序不是归属变更），
        也不动 updated_at（否则会影响「当前重点」的排序）。

        传进来的 id 里不属于该父级的会被忽略；没出现在列表里的兄弟节点
        （例如折叠过滤掉的已归档节点）保持原来的位置不动。
        """
        known = {node.id for node in self.children(parent_id)}
        ordered = [node_id for node_id in ordered_ids if node_id in known]
        if not ordered:
            return
        with self.db.transaction() as tx:
            for index, node_id in enumerate(ordered):
                tx.execute(
                    "UPDATE nodes SET position = ? WHERE id = ?", (index, node_id)
                )
        logger.info("调整同级顺序：%s", "、".join(ordered))
        self._touch()

    def rename_node(self, node_id: str, new_name: str) -> None:
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValidationError("节点名称不能为空。")
        node = self.require_node(node_id)
        if node.name == new_name:
            return
        legacy_path = [step.name for step in self.path_of(node_id)]
        stamp = now_stamp()
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE nodes SET name = ?, updated_at = ? WHERE id = ?",
                (new_name, stamp, node_id),
            )
            new_path = list(legacy_path)
            new_path[-1] = new_name
            self.db._insert_event(
                tx,
                node_id=node_id,
                event_type=EventType.RENAME.value,
                before={"name": node.name, "path": legacy_path},
                after={"name": new_name, "path": new_path},
                created_at=stamp,
                note="重命名",
            )
        logger.info("重命名：%s → %s", node.name, new_name)
        self._touch()

    def move_node(self, node_id: str, new_parent_id: str) -> None:
        """移动节点（Thread → 其他 Process，Process → 其他 Area）。

        必须在一次事务内完成：更新当前归属、写入变更事件、保证旧活动快照不变。
        """
        node = self.require_node(node_id)
        if node.type is NodeType.AREA:
            raise ValidationError("Area 是顶层节点，无法移动。")
        if not new_parent_id:
            raise ValidationError("必须指定新的上级归属。")
        if new_parent_id == node.parent_id:
            return
        new_parent = self.require_node(new_parent_id)
        if new_parent.type is not node.type.parent_type:
            raise ValidationError(
                f"{node.type.label}只能移动到{node.type.parent_type.label}下。"
            )

        before_path = [step.name for step in self.path_of(node_id)]
        stamp = now_stamp()
        position = self._next_position(new_parent_id)
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE nodes SET parent_id = ?, position = ?, updated_at = ? WHERE id = ?",
                (new_parent_id, position, stamp, node_id),
            )
            new_path = [step.name for step in self.path_of(node_id)]
            self.db._insert_event(
                tx,
                node_id=node_id,
                event_type=EventType.MOVE.value,
                before={
                    "parent_id": node.parent_id,
                    "path": before_path,
                },
                after={
                    "parent_id": new_parent_id,
                    "path": new_path,
                },
                created_at=stamp,
                note=f"{node.type.label}归属变更",
            )
        logger.info("移动节点：%s（%s → %s）", node.name, " → ".join(before_path), " → ".join(new_path))
        self._touch()

    def set_status(self, node_id: str, status: Status) -> None:
        node = self.require_node(node_id)
        if node.status is status:
            return
        stamp = now_stamp()
        archived_at = stamp if status is Status.ARCHIVED else None
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE nodes SET status = ?, archived_at = ?, updated_at = ? WHERE id = ?",
                (status.value, archived_at, stamp, node_id),
            )
            if status is Status.ARCHIVED:
                # 归档是软归档：取消置顶，但不删除任何活动与历史。
                tx.execute("UPDATE nodes SET pinned = 0 WHERE id = ?", (node_id,))
            self.db._insert_event(
                tx,
                node_id=node_id,
                event_type=EventType.STATUS.value,
                before={"status": node.status.value},
                after={"status": status.value},
                created_at=stamp,
                note="状态变更",
            )
        logger.info("状态变更：%s → %s", node.name, status.value)
        self._touch()

    def set_description(self, node_id: str, text: str) -> None:
        """写一句简短注释（目前用于进程热力图标题下的小字）。"""
        node = self.require_node(node_id)
        text = (text or "").strip()
        if node.description == text:
            return
        stamp = now_stamp()
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE nodes SET description = ?, updated_at = ? WHERE id = ?",
                (text, stamp, node_id),
            )
            self.db._insert_event(
                tx,
                node_id=node_id,
                event_type=EventType.DESCRIPTION.value,
                before={"description": node.description},
                after={"description": text},
                created_at=stamp,
                note="注释更新",
            )
        logger.info("更新注释：%s（%r → %r）", node.name, node.description, text)
        self._touch()

    def set_next_action(self, node_id: str, text: str) -> None:
        node = self.require_node(node_id)
        text = (text or "").strip()
        if node.next_action == text:
            return
        if node.type is NodeType.AREA:
            raise ValidationError("Area 不支持设置下一步行动。")
        stamp = now_stamp()
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE nodes SET next_action = ?, updated_at = ? WHERE id = ?",
                (text, stamp, node_id),
            )
            self.db._insert_event(
                tx,
                node_id=node_id,
                event_type=EventType.NEXT_ACTION.value,
                before={"next_action": node.next_action},
                after={"next_action": text},
                created_at=stamp,
                note="下一步行动更新",
            )
        self._touch()

    def set_pinned(self, node_id: str, pinned: bool) -> None:
        node = self.require_node(node_id)
        if node.type is NodeType.AREA:
            raise ValidationError("Area 不能置顶为重点。")
        if node.is_deleted:
            raise ValidationError("已删除的节点不能置顶，请先恢复它。")
        if node.pinned == pinned:
            return
        if pinned and len(self.focused_nodes()) >= FOCUS_LIMIT:
            raise ValidationError(f"当前重点最多 {FOCUS_LIMIT} 个，请先取消一个已置顶节点。")
        stamp = now_stamp()
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE nodes SET pinned = ?, updated_at = ? WHERE id = ?",
                (1 if pinned else 0, stamp, node_id),
            )
            self.db._insert_event(
                tx,
                node_id=node_id,
                event_type=EventType.PIN.value,
                before={"pinned": node.pinned},
                after={"pinned": pinned},
                created_at=stamp,
                note="置顶为重点" if pinned else "取消置顶",
            )
        self._touch()

    # =========================================================== 删除与恢复
    def subtree(self, node_id: str, include_deleted: bool = True) -> list[Node]:
        """返回节点自身及其全部后代（深度优先）。"""
        root = self.require_node(node_id)
        result = [root] if (include_deleted or not root.is_deleted) else []
        stack = [node_id]
        while stack:
            current = stack.pop()
            for child in self.children(current, include_deleted=include_deleted):
                result.append(child)
                stack.append(child.id)
        return result

    def delete_node(self, node_id: str) -> int:
        """把节点及其后代移入「已删除」清单。

        数据一个都不删：活动记录、归属快照、热力图统计与历史事件全部保留，
        只是节点不再出现在树、列表、选择器与热力图分组里。
        """
        node = self.require_node(node_id)
        if node.is_deleted:
            raise ValidationError("该节点已经在「已删除」清单里。")
        targets = self.subtree(node_id, include_deleted=True)
        active = [item for item in targets if not item.is_deleted]
        stamp = now_stamp()
        with self.db.transaction() as tx:
            for item in active:
                before_path = [step.name for step in self.path_of(item.id)]
                tx.execute(
                    "UPDATE nodes SET deleted_at = ?, deleted_group = ?, pinned = 0,"
                    " updated_at = ? WHERE id = ?",
                    (stamp, node.id, stamp, item.id),
                )
                self.db._insert_event(
                    tx,
                    node_id=item.id,
                    event_type=EventType.DELETE.value,
                    before={"path": before_path, "status": item.status.value},
                    after=None,
                    created_at=stamp,
                    note="移入已删除清单（数据保留）",
                )
        logger.warning(
            "移入已删除清单：%s（%s），共 %d 个节点；活动与历史全部保留",
            node.name,
            node.type.label,
            len(active),
        )
        self._touch()
        return len(active)

    def deleted_entries(self) -> list[dict[str, Any]]:
        """「已删除」清单：按删除批次（批次根节点）列出，可查看与恢复。"""
        rows = self.db.conn.execute(
            "SELECT * FROM nodes WHERE deleted_at IS NOT NULL AND deleted_group = id"
            " ORDER BY deleted_at DESC"
        ).fetchall()
        entries: list[dict[str, Any]] = []
        for row in rows:
            node = Node.from_row(row)
            batch = [
                item
                for item in self.all_nodes(include_deleted=True)
                if item.deleted_group == node.id and item.is_deleted
            ]
            descendants = [item for item in batch if item.id != node.id]
            entries.append(
                {
                    "node": node,
                    "deleted_at": node.deleted_at,
                    "path": [step.name for step in self.path_of(node.id)],
                    "batch_size": len(batch),
                    "descendants": descendants,
                    "activity_count": self.activity_count_for_node(node.id),
                    "subtree_activity_count": sum(
                        self.activity_count_for_node(item.id) for item in batch
                    ),
                    "restorable": self._restore_blockers(node) == [],
                    "blockers": self._restore_blockers(node),
                }
            )
        return entries

    def deleted_entry_count(self) -> int:
        """「已删除」清单里有多少个删除批次（用于界面提示）。"""
        row = self.db.conn.execute(
            "SELECT count(*) AS n FROM nodes WHERE deleted_at IS NOT NULL AND deleted_group = id"
        ).fetchone()
        return int(row["n"])

    def deleted_children(
        self, parent_id: str | None, node_type: NodeType | None = None
    ) -> list[Node]:
        """指定上级下已删除的子节点（用于热力图上的标注）。"""
        return [
            node
            for node in self.children(parent_id, node_type, include_deleted=True)
            if node.is_deleted
        ]

    def _restore_blockers(self, node: Node) -> list[str]:
        """恢复的前置条件：上级不能还处在删除状态。"""
        blockers: list[str] = []
        parent = self.get_node(node.parent_id) if node.parent_id else None
        while parent is not None:
            if parent.is_deleted:
                blockers.append(parent.name)
            parent = self.get_node(parent.parent_id) if parent.parent_id else None
        return blockers

    def restore_node(self, node_id: str) -> int:
        """把一个删除批次整体恢复到树里原来的位置。"""
        node = self.require_node(node_id)
        if not node.is_deleted:
            raise ValidationError("该节点不在「已删除」清单里。")
        blockers = self._restore_blockers(node)
        if blockers:
            raise ValidationError(
                "它的上级「" + "、".join(blockers) + "」还在已删除清单里，请先恢复上级。"
            )
        group = node.deleted_group or node.id
        targets = [
            item
            for item in self.all_nodes(include_deleted=True)
            if item.deleted_group == group and item.is_deleted
        ]
        stamp = now_stamp()
        with self.db.transaction() as tx:
            for item in targets:
                tx.execute(
                    "UPDATE nodes SET deleted_at = NULL, deleted_group = NULL, updated_at = ?"
                    " WHERE id = ?",
                    (stamp, item.id),
                )
                self.db._insert_event(
                    tx,
                    node_id=item.id,
                    event_type=EventType.RESTORE.value,
                    before=None,
                    after={"path": [step.name for step in self.path_of(item.id)]},
                    created_at=stamp,
                    note="从已删除清单恢复",
                )
        logger.warning("已恢复节点：%s，共 %d 个节点回到树里", node.name, len(targets))
        self._touch()
        return len(targets)

    # =========================================================== 关联关系
    def active_associations(self) -> list[Association]:
        rows = self.db.conn.execute(
            "SELECT * FROM associations WHERE ended_at IS NULL ORDER BY created_at"
        ).fetchall()
        return [Association.from_row(row) for row in rows]

    def processes_for_thread(self, thread_id: str) -> list[Node]:
        rows = self.db.conn.execute(
            "SELECT n.* FROM associations a JOIN nodes n ON n.id = a.process_id"
            " WHERE a.thread_id = ? AND a.ended_at IS NULL ORDER BY n.name",
            (thread_id,),
        ).fetchall()
        return [Node.from_row(row) for row in rows]

    def threads_for_process(self, process_id: str) -> list[Node]:
        rows = self.db.conn.execute(
            "SELECT n.* FROM associations a JOIN nodes n ON n.id = a.thread_id"
            " WHERE a.process_id = ? AND a.ended_at IS NULL ORDER BY n.name",
            (process_id,),
        ).fetchall()
        return [Node.from_row(row) for row in rows]

    def association_history(self, thread_id: str) -> list[Association]:
        rows = self.db.conn.execute(
            "SELECT * FROM associations WHERE thread_id = ? ORDER BY created_at",
            (thread_id,),
        ).fetchall()
        return [Association.from_row(row) for row in rows]

    def associate(self, thread_id: str, process_id: str) -> None:
        thread = self.require_node(thread_id)
        process = self.require_node(process_id)
        if thread.type is not NodeType.THREAD or process.type is not NodeType.PROCESS:
            raise ValidationError("关联只能建立在 Thread 与 Process 之间。")
        if thread.parent_id == process.id:
            raise ValidationError("该 Process 已是这条线程的主要归属，无需重复关联。")
        existing = self.db.conn.execute(
            "SELECT id FROM associations WHERE thread_id = ? AND process_id = ?"
            " AND ended_at IS NULL",
            (thread_id, process_id),
        ).fetchone()
        if existing:
            raise ValidationError("这条关联已经存在。")
        stamp = now_stamp()
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO associations(id, thread_id, process_id, created_at, ended_at)"
                " VALUES(?,?,?,?,NULL)",
                (new_id(), thread_id, process_id, stamp),
            )
            self.db._insert_event(
                tx,
                node_id=thread_id,
                event_type=EventType.ASSOCIATE.value,
                before=None,
                after={
                    "thread_id": thread_id,
                    "process_id": process_id,
                    "thread_name": thread.name,
                    "process_name": process.name,
                },
                created_at=stamp,
                note="建立关联",
            )
        logger.info("建立关联：%s ←→ %s", thread.name, process.name)
        self._touch()

    def dissociate(self, thread_id: str, process_id: str) -> None:
        row = self.db.conn.execute(
            "SELECT id FROM associations WHERE thread_id = ? AND process_id = ?"
            " AND ended_at IS NULL",
            (thread_id, process_id),
        ).fetchone()
        if row is None:
            raise ValidationError("这条关联不存在或已解除。")
        thread = self.require_node(thread_id)
        process = self.require_node(process_id)
        stamp = now_stamp()
        with self.db.transaction() as tx:
            tx.execute("UPDATE associations SET ended_at = ? WHERE id = ?", (stamp, row["id"]))
            self.db._insert_event(
                tx,
                node_id=thread_id,
                event_type=EventType.DISSOCIATE.value,
                before={
                    "thread_id": thread_id,
                    "process_id": process_id,
                    "thread_name": thread.name,
                    "process_name": process.name,
                },
                after=None,
                created_at=stamp,
                note="解除关联",
            )
        logger.info("解除关联：%s ←→ %s", thread.name, process.name)
        self._touch()

    # =========================================================== 活动记录
    def record_activity(
        self, node_id: str, note: str = "", activity_date: str | None = None
    ) -> Activity:
        """记录一次活动，并写入发生当时的归属快照。"""
        node = self.require_node(node_id)
        if node.type is NodeType.AREA:
            raise ValidationError("活动只能记录在 Process 或 Thread 上。")
        if node.is_deleted:
            raise ValidationError("已删除的节点不能记录活动，请先恢复它。")
        day = (activity_date or today_str()).strip()
        if not _is_valid_date(day):
            raise ValidationError("活动日期格式应为 YYYY-MM-DD。")

        scope = self.ancestors(node_id)
        steps = self.path_of(node_id)
        activity_id = new_id()
        stamp = now_stamp()
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO activities(id, node_id, node_type, activity_date, created_at,"
                " note, area_id, process_id, thread_id, path_snapshot)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    activity_id,
                    node_id,
                    node.type.value,
                    day,
                    stamp,
                    (note or "").strip(),
                    scope["area_id"],
                    scope["process_id"],
                    node.id if node.type is NodeType.THREAD else None,
                    json.dumps([step.to_dict() for step in steps], ensure_ascii=False),
                ),
            )
        logger.info("记录活动：%s（%s）", node.name, day)
        self._touch()
        return self.require_activity(activity_id)

    def update_activity(
        self,
        activity_id: str,
        *,
        note: str | None = None,
        activity_date: str | None = None,
    ) -> Activity:
        """修改活动的备注或日期。

        归属快照不可修改：它记录的是活动发生时的事实，只能通过新的活动去表达新归属。
        """
        activity = self.require_activity(activity_id)
        new_note = activity.note if note is None else (note or "").strip()
        new_date = activity.activity_date if activity_date is None else (activity_date or "").strip()
        if not _is_valid_date(new_date):
            raise ValidationError("活动日期格式应为 YYYY-MM-DD。")
        if new_note == activity.note and new_date == activity.activity_date:
            return activity
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE activities SET note = ?, activity_date = ? WHERE id = ?",
                (new_note, new_date, activity_id),
            )
        logger.info(
            "修改活动记录：%s（备注 %r→%r，日期 %s→%s）",
            activity_id,
            activity.note,
            new_note,
            activity.activity_date,
            new_date,
        )
        self._touch()
        return self.require_activity(activity_id)

    def delete_activities(self, activity_ids: Sequence[str]) -> int:
        """彻底删除活动记录（不留存、不可恢复），热力图随之重新统计。"""
        unique_ids = [activity_id for activity_id in dict.fromkeys(activity_ids) if activity_id]
        if not unique_ids:
            return 0
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self.db.conn.execute(
            f"SELECT id, activity_date, path_snapshot FROM activities WHERE id IN ({placeholders})",
            unique_ids,
        ).fetchall()
        if not rows:
            raise ValidationError("选中的活动记录不存在或已被删除。")
        with self.db.transaction() as tx:
            tx.execute(f"DELETE FROM activities WHERE id IN ({placeholders})", unique_ids)
        for row in rows:
            logger.warning(
                "删除活动记录：%s（日期 %s，快照 %s）",
                row["id"],
                row["activity_date"],
                row["path_snapshot"],
            )
        self._touch()
        return len(rows)

    def delete_activity(self, activity_id: str) -> None:
        self.delete_activities([activity_id])

    def require_activity(self, activity_id: str) -> Activity:
        row = self.db.conn.execute(
            "SELECT * FROM activities WHERE id = ?", (activity_id,)
        ).fetchone()
        if row is None:
            raise ValidationError(f"活动记录不存在：{activity_id}")
        return Activity.from_row(row)

    def list_activities(
        self,
        *,
        area_id: str | None = None,
        process_id: str | None = None,
        thread_id: str | None = None,
        node_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        keyword: str | None = None,
        limit: int | None = None,
    ) -> list[Activity]:
        sql = "SELECT * FROM activities WHERE 1 = 1"
        params: list[Any] = []
        if area_id:
            sql += " AND area_id = ?"
            params.append(area_id)
        if process_id:
            sql += " AND process_id = ?"
            params.append(process_id)
        if thread_id:
            sql += " AND thread_id = ?"
            params.append(thread_id)
        if node_id:
            sql += " AND node_id = ?"
            params.append(node_id)
        if date_from:
            sql += " AND activity_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND activity_date <= ?"
            params.append(date_to)
        if keyword:
            sql += " AND (note LIKE ? OR path_snapshot LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        sql += " ORDER BY activity_date DESC, created_at DESC, rowid DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [Activity.from_row(row) for row in self.db.conn.execute(sql, params).fetchall()]

    def activities_on(self, day: str) -> list[Activity]:
        return self.list_activities(date_from=day, date_to=day)

    def activity_count_for_node(self, node_id: str) -> int:
        row = self.db.conn.execute(
            "SELECT count(*) AS n FROM activities WHERE node_id = ?", (node_id,)
        ).fetchone()
        return int(row["n"])

    def activity_summary(self, node_id: str) -> tuple[int, str]:
        """返回（该节点活动条数, 最近一条活动的创建时间），用于界面刷新判断。"""
        row = self.db.conn.execute(
            "SELECT count(*) AS n, max(created_at) AS latest FROM activities WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        return int(row["n"]), (row["latest"] or "")

    # =========================================================== 历史事件
    def events_for_node(self, node_id: str, limit: int = 200) -> list[Event]:
        rows = self.db.conn.execute(
            "SELECT * FROM events WHERE node_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (node_id, limit),
        ).fetchall()
        return [Event.from_row(row) for row in rows]

    def event_count(self, node_id: str | None = None) -> int:
        if node_id is None:
            return int(self.db.conn.execute("SELECT count(*) AS n FROM events").fetchone()["n"])
        row = self.db.conn.execute(
            "SELECT count(*) AS n FROM events WHERE node_id = ?", (node_id,)
        ).fetchone()
        return int(row["n"])

    def delete_events(self, event_ids: Sequence[str]) -> int:
        """删除选中的历史事件。

        只删除历史事件本身：活动记录、归属快照与热力图统计都不受影响。
        删除动作会写入运行日志，便于追溯。
        """
        unique_ids = [event_id for event_id in dict.fromkeys(event_ids) if event_id]
        if not unique_ids:
            return 0
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self.db.conn.execute(
            f"SELECT id, node_id, event_type, created_at FROM events WHERE id IN ({placeholders})",
            unique_ids,
        ).fetchall()
        if not rows:
            raise ValidationError("选中的历史事件不存在或已被删除。")
        with self.db.transaction() as tx:
            tx.execute(f"DELETE FROM events WHERE id IN ({placeholders})", unique_ids)
        for row in rows:
            logger.warning(
                "删除历史事件：%s（节点 %s，类型 %s，事件时间 %s）",
                row["id"],
                row["node_id"],
                row["event_type"],
                row["created_at"],
            )
        self._touch()
        return len(rows)

    def delete_event(self, event_id: str) -> None:
        self.delete_events([event_id])

    # ============================================================ AI 对话历史
    def create_ai_conversation(self, title: str = "新对话") -> str:
        """新建一个空对话，返回 id。历史只存在本地数据库里。"""
        conversation_id = new_id()
        stamp = now_stamp()
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO ai_conversations(id, title, created_at, updated_at)"
                " VALUES(?,?,?,?)",
                (conversation_id, (title or "新对话").strip() or "新对话", stamp, stamp),
            )
        return conversation_id

    def ai_conversations(self) -> list[dict[str, Any]]:
        """对话列表，最近更新的在前。"""
        rows = self.db.conn.execute(
            "SELECT c.id, c.title, c.created_at, c.updated_at,"
            " (SELECT count(*) FROM ai_messages m WHERE m.conversation_id = c.id) AS n"
            " FROM ai_conversations c ORDER BY c.updated_at DESC, c.rowid DESC"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "message_count": int(row["n"]),
            }
            for row in rows
        ]

    def ai_messages(self, conversation_id: str) -> list[dict[str, str]]:
        rows = self.db.conn.execute(
            "SELECT id, role, content, created_at FROM ai_messages"
            " WHERE conversation_id = ? ORDER BY created_at, rowid",
            (conversation_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def append_ai_message(self, conversation_id: str, role: str, content: str) -> str:
        """追加一条对话消息（user / assistant）。"""
        if role not in ("user", "assistant"):
            raise ValidationError("对话角色只能是 user 或 assistant。")
        text = (content or "").strip()
        if not text:
            raise ValidationError("消息不能为空。")
        message_id = new_id()
        stamp = now_stamp()
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO ai_messages(id, conversation_id, role, content, created_at)"
                " VALUES(?,?,?,?,?)",
                (message_id, conversation_id, role, text, stamp),
            )
            tx.execute(
                "UPDATE ai_conversations SET updated_at = ? WHERE id = ?",
                (stamp, conversation_id),
            )
            if role == "user":
                title = text
                for token in ("**", "__", "`", "#", ">", "*", "_"):
                    title = title.replace(token, "")
                title = " ".join(title.split())
                tx.execute(
                    "UPDATE ai_conversations SET title = ?"
                    " WHERE id = ? AND title = '新对话'",
                    (title[:24] + ("…" if len(title) > 24 else ""), conversation_id),
                )
        return message_id

    def delete_ai_conversation(self, conversation_id: str) -> None:
        with self.db.transaction() as tx:
            tx.execute("DELETE FROM ai_messages WHERE conversation_id = ?", (conversation_id,))
            tx.execute("DELETE FROM ai_conversations WHERE id = ?", (conversation_id,))

    # ======================================================= 备份与导入导出
    def backup(self, label: str = "manual") -> Path:
        """用 SQLite 备份 API 生成一致性快照，保存在项目内的 data/backups。"""
        paths.ensure_directories()
        stamp = now_stamp().replace(":", "").replace(" ", "-")
        target = paths.BACKUP_DIR / f"{label}-{stamp}.sqlite3"
        destination = sqlite3.connect(target)
        try:
            with destination:
                self.db.conn.backup(destination)
            # 备份文件保持单文件：把 WAL 内容合并回去，不留 -wal / -shm 边车文件。
            destination.execute("PRAGMA journal_mode = DELETE")
        finally:
            destination.close()
        # 日记是独立的 Markdown 文件，备份时一起存一份（<备份名>.diary.md）
        if paths.DIARY_PATH.exists():
            shutil.copy2(paths.DIARY_PATH, target.with_name(target.stem + ".diary.md"))
        logger.info("创建备份：%s", target)
        return target

    def list_backups(self) -> list[Path]:
        if not paths.BACKUP_DIR.exists():
            return []
        return sorted(paths.BACKUP_DIR.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)

    def auto_backup_if_needed(self, day: str | None = None) -> Path | None:
        """每天第一次启动时自动备份一次（数据库 + 日记）。

        - 同一天只做一份：文件名形如 auto-2026-10-15-052030.sqlite3，含日记的同名 .diary.md；
        - **不会自动删除任何旧备份**（不做自动清理），需要清理时由用户手动处理；
        - 不会因为失败而影响启动：调用方负责捕获异常。
        """
        today = day or today_str()
        existing = sorted(paths.BACKUP_DIR.glob(f"auto-{today}-*.sqlite3"))
        if existing:
            logger.info("今天已经有自动备份，跳过：%s", existing[-1].name)
            return None
        # 文件名形如 auto-2026-10-15-052030.sqlite3（日期已含在时间戳里）
        target = self.backup(label="auto")
        logger.info("已创建今天的自动备份：%s", target.name)
        return target

    def latest_auto_backup(self) -> Path | None:
        entries = sorted(paths.BACKUP_DIR.glob("auto-*.sqlite3"), key=lambda p: p.stat().st_mtime)
        return entries[-1] if entries else None

    def verify_backup(self, backup_path: str | Path) -> tuple[bool, str]:
        """校验备份文件是否可用于恢复。"""
        path = Path(backup_path)
        if not path.exists():
            return False, "文件不存在。"
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                check = conn.execute("PRAGMA integrity_check").fetchone()
                result = check[0] if check else "unknown"
                if result != "ok":
                    return False, f"结构检查未通过：{result}"
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                required = {"nodes", "activities", "events", "associations", "meta"}
                missing = required - tables
                if missing:
                    return False, "缺少数据表：" + "、".join(sorted(missing))
                version_row = conn.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()
                version = int(version_row[0]) if version_row else 0
                if version > 0 and version > _current_schema_version():
                    return False, f"备份来自更高版本的数据结构（v{version}），当前应用无法读取。"
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            return False, f"文件不是有效的 SQLite 数据库：{exc}"
        return True, "备份文件可用。"

    def restore_backup(self, backup_path: str | Path) -> Path:
        """从备份恢复。恢复前自动为当前数据库创建安全备份。"""
        path = Path(backup_path)
        ok, message = self.verify_backup(path)
        if not ok:
            raise ValidationError(f"无法从该文件恢复：{message}")
        safety = self.backup(label="pre-restore")
        diary_in_backup = path.with_name(path.stem + ".diary.md")
        if diary_in_backup.exists() and paths.DIARY_PATH.exists():
            shutil.copy2(paths.DIARY_PATH, safety.with_name(safety.stem + ".diary.md"))
        self._replace_database(path)
        if diary_in_backup.exists():
            shutil.copy2(diary_in_backup, paths.DIARY_PATH)
        logger.warning("已从备份恢复：%s（恢复前的数据保存在 %s）", path, safety)
        self._touch()
        return safety

    def _replace_database(self, source: Path) -> None:
        self.db.close()
        target = self.db.path
        for suffix in ("-wal", "-shm"):
            leftover = Path(str(target) + suffix)
            if leftover.exists():
                leftover.unlink()
        shutil.copy2(source, target)
        self.db.connect()
        self.db.initialize(seed=False)

    # ------------------------------------------- 单个节点的活动记录导出
    def export_activities_csv(
        self, node_id: str, destination: str | Path | None = None
    ) -> tuple[Path, int]:
        """把某个节点的**全部**活动记录导出成 CSV（Excel 可直接打开）。

        领域 = 它下面所有进程与线程的记录；进程 = 它下面线程的记录。
        口径与界面一致：按活动发生时的归属快照统计，而不是节点现在的归属。
        按日期从早到晚排列，返回 (文件路径, 条数)。
        """
        node = self.require_node(node_id)
        if node.type is NodeType.AREA:
            records = self.list_activities(area_id=node_id)
        elif node.type is NodeType.PROCESS:
            records = self.list_activities(process_id=node_id)
        else:
            records = self.list_activities(thread_id=node_id)
        records = sorted(
            records, key=lambda item: (item.activity_date, item.created_at)
        )

        paths.ensure_directories()
        if destination is None:
            safe_name = "".join(
                "-" if ch in '\\/:*?"<>|' else ch for ch in node.name
            ).strip()
            stamp = now_stamp().replace(":", "").replace(" ", "-")
            destination = paths.EXPORT_DIR / f"{safe_name}-活动记录-{stamp}.csv"
        target = Path(destination)
        # utf-8-sig：带 BOM，Excel 打开中文才不会乱码
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("日期", "归属快照", "记在哪", "备注", "记录时间"))
            for activity in records:
                target_label = (
                    "（进程）" if activity.node_type is NodeType.PROCESS else "（线程）"
                )
                writer.writerow(
                    (
                        activity.activity_date,
                        activity.path_text,
                        target_label,
                        activity.note,
                        activity.created_at,
                    )
                )
        logger.info("导出活动记录：%s（%d 条）", target, len(records))
        return target, len(records)

    # --------------------------------------------------------------- JSON
    def export_json(self, destination: str | Path | None = None) -> Path:
        paths.ensure_directories()
        if destination is None:
            stamp = now_stamp().replace(":", "").replace(" ", "-")
            destination = paths.EXPORT_DIR / f"key-actions-{stamp}.json"
        destination = Path(destination)
        payload = {
            "application": "Key Actions",
            "format": JSON_FORMAT,
            "format_version": JSON_FORMAT_VERSION,
            "schema_version": _current_schema_version(),
            "exported_at": now_stamp(),
            "nodes": [dict(row) for row in self.db.conn.execute("SELECT * FROM nodes ORDER BY created_at")],
            "associations": [
                dict(row) for row in self.db.conn.execute("SELECT * FROM associations ORDER BY created_at")
            ],
            "activities": [
                dict(row)
                for row in self.db.conn.execute(
                    "SELECT * FROM activities ORDER BY activity_date, created_at"
                )
            ],
            "events": [
                dict(row) for row in self.db.conn.execute("SELECT * FROM events ORDER BY created_at")
            ],
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("导出 JSON：%s", destination)
        return destination

    def validate_import_file(self, source: str | Path) -> dict[str, Any]:
        """只读校验导入文件，不修改任何现有数据。"""
        path = Path(source)
        if not path.exists():
            raise ImportValidationError("文件不存在。")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ImportValidationError(f"文件不是合法的 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise ImportValidationError("根节点必须是一个 JSON 对象。")
        if payload.get("format") != JSON_FORMAT:
            raise ImportValidationError("不是 Key Actions 导出的 JSON 文件（format 字段不匹配）。")
        for key in ("nodes", "activities", "events", "associations"):
            if not isinstance(payload.get(key), list):
                raise ImportValidationError(f"缺少列表字段：{key}")
        node_ids: set[str] = set()
        for raw in payload["nodes"]:
            for field_name in ("id", "type", "name", "status", "created_at", "updated_at"):
                if field_name not in raw:
                    raise ImportValidationError(f"节点缺少字段：{field_name}")
            if raw["type"] not in {t.value for t in NodeType}:
                raise ImportValidationError(f"节点类型不合法：{raw['type']}")
            try:
                Status(raw["status"])
            except ValueError as exc:
                raise ImportValidationError(f"节点状态不合法：{raw['status']}") from exc
            if raw["id"] in node_ids:
                raise ImportValidationError(f"节点 ID 重复：{raw['id']}")
            node_ids.add(raw["id"])
        for raw in payload["nodes"]:
            parent = raw.get("parent_id")
            if parent and parent not in node_ids:
                raise ImportValidationError(f"节点 {raw['name']} 的上级不存在于文件中。")
            expected = NodeType(raw["type"]).parent_type
            if parent is None:
                if raw["type"] != NodeType.AREA.value:
                    raise ImportValidationError(f"节点 {raw['name']} 缺少上级归属。")
            else:
                parent_row = next(n for n in payload["nodes"] if n["id"] == parent)
                if parent_row["type"] != expected.value:
                    raise ImportValidationError(f"节点 {raw['name']} 的上级类型不匹配。")
        for raw in payload["activities"]:
            for field_name in ("id", "node_id", "node_type", "activity_date", "created_at"):
                if field_name not in raw:
                    raise ImportValidationError(f"活动记录缺少字段：{field_name}")
            if raw["node_id"] not in node_ids:
                raise ImportValidationError("存在指向不存在节点的活动记录。")
            if raw["node_type"] not in (NodeType.PROCESS.value, NodeType.THREAD.value):
                raise ImportValidationError(f"活动记录的节点类型不合法：{raw['node_type']}")
        return payload

    def import_json(self, source: str | Path) -> Path:
        """覆盖导入：导入前自动备份，失败时保留全部现有数据。"""
        payload = self.validate_import_file(source)
        safety = self.backup(label="pre-import")
        tables = ("activities", "events", "associations", "nodes")
        node_columns = (
            "id", "type", "name", "parent_id", "position", "status", "pinned",
            "next_action", "created_at", "updated_at", "archived_at",
            "description",
        )
        assoc_columns = ("id", "thread_id", "process_id", "created_at", "ended_at")
        activity_columns = (
            "id", "node_id", "node_type", "activity_date", "created_at", "note",
            "area_id", "process_id", "thread_id", "path_snapshot",
        )
        event_columns = (
            "id", "node_id", "event_type", "before_value", "after_value", "created_at", "note",
        )

        def _rows(items: Sequence[dict[str, Any]], columns: Sequence[str]) -> list[tuple]:
            return [tuple(item.get(column) for column in columns) for item in items]

        try:
            with self.db.transaction() as tx:
                # 节点表有自引用外键（parent_id），批量替换时延迟到提交点检查。
                tx.execute("PRAGMA defer_foreign_keys = ON")
                for table in tables:
                    tx.execute(f"DELETE FROM {table}")
                # 先写 Area，再写 Process，最后写 Thread，保证父节点先于子节点落库。
                depth = {NodeType.AREA.value: 0, NodeType.PROCESS.value: 1, NodeType.THREAD.value: 2}
                ordered_nodes = sorted(payload["nodes"], key=lambda item: depth[item["type"]])
                tx.executemany(
                    f"INSERT INTO nodes({','.join(node_columns)})"
                    f" VALUES({','.join('?' * len(node_columns))})",
                    # description 是新字段：旧版导出的 JSON 里没有它，这里补空串
                    [
                        tuple(
                            (item.get(column) or "") if column == "description"
                            else item.get(column)
                            for column in node_columns
                        )
                        for item in ordered_nodes
                    ],
                )
                tx.executemany(
                    f"INSERT INTO associations({','.join(assoc_columns)})"
                    f" VALUES({','.join('?' * len(assoc_columns))})",
                    _rows(payload["associations"], assoc_columns),
                )
                tx.executemany(
                    f"INSERT INTO activities({','.join(activity_columns)})"
                    f" VALUES({','.join('?' * len(activity_columns))})",
                    _rows(payload["activities"], activity_columns),
                )
                tx.executemany(
                    f"INSERT INTO events({','.join(event_columns)})"
                    f" VALUES({','.join('?' * len(event_columns))})",
                    _rows(payload["events"], event_columns),
                )
                tx.execute(
                    "INSERT INTO meta(key, value) VALUES('seeded', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = '1'"
                )
        except sqlite3.Error as exc:
            raise ImportValidationError(
                f"导入过程中出现错误，现有数据未被改动：{exc}"
            ) from exc
        logger.warning("已覆盖导入 JSON：%s（导入前的数据保存在 %s）", source, safety)
        self._touch()
        return safety


def _is_valid_date(value: str) -> bool:
    from datetime import date as _date

    try:
        _date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _current_schema_version() -> int:
    from .storage import SCHEMA_VERSION

    return SCHEMA_VERSION


def describe_event(event: Event) -> str:
    """把历史事件渲染成人类可读的中文描述。"""
    before = event.before or {}
    after = event.after or {}

    def _path(value: dict[str, Any]) -> str:
        steps = value.get("path")
        if isinstance(steps, list) and steps:
            return " → ".join(str(step) for step in steps)
        return "（无归属）"

    if event.event_type is EventType.MOVE:
        return f"归属：{_path(before)} → {_path(after)}"
    if event.event_type is EventType.RENAME:
        return f"名称：{before.get('name', '')} → {after.get('name', '')}"
    if event.event_type is EventType.STATUS:
        return f"状态：{before.get('status', '')} → {after.get('status', '')}"
    if event.event_type is EventType.CREATE:
        return f"创建于 {_path(after)}"
    if event.event_type is EventType.ASSOCIATE:
        return f"关联：{after.get('thread_name', '')} ⇄ {after.get('process_name', '')}"
    if event.event_type is EventType.DISSOCIATE:
        return f"解除关联：{before.get('thread_name', '')} ⇄ {before.get('process_name', '')}"
    if event.event_type is EventType.PIN:
        return "置顶为重点" if after.get("pinned") else "取消置顶"
    if event.event_type is EventType.NEXT_ACTION:
        old = before.get("next_action") or "（空）"
        new = after.get("next_action") or "（空）"
        return f"下一步行动：{old} → {new}"
    if event.event_type is EventType.DELETE:
        return f"移入已删除清单（原归属：{_path(before)}）"
    if event.event_type is EventType.RESTORE:
        return f"恢复到 {_path(after)}"
    if event.event_type is EventType.DESCRIPTION:
        old = before.get("description") or "（空）"
        new = after.get("description") or "（空）"
        return f"注释：{old} → {new}"
    return event.event_type.label
