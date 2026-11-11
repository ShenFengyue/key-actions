"""数据模型：节点、关联、活动记录、历史事件。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from . import clock


def now_stamp() -> str:
    """北京时间时间戳，用于 created_at / updated_at / 事件时间。"""
    return clock.stamp()


def today_str() -> str:
    """北京时间的今天（YYYY-MM-DD）。"""
    return clock.today_str()


@dataclass(frozen=True)
class Slogan:
    """左栏顶部的一条标语。

    每次修改都插入一条新记录，旧的记录原样保留 —— 历史就是这么来的，
    不需要额外的"版本"概念；当前显示的就是最新那一条。
    """

    id: str
    text: str
    created_at: str

    @property
    def display_text(self) -> str:
        return self.text if self.text else "（已清空）"

    @classmethod
    def from_row(cls, row: Any) -> "Slogan":
        return cls(
            id=row["id"],
            text=row["text"] or "",
            created_at=row["created_at"],
        )


class NodeType(str, Enum):
    AREA = "area"
    PROCESS = "process"
    THREAD = "thread"

    @property
    def label(self) -> str:
        return {"area": "领域", "process": "进程", "thread": "线程"}[self.value]

    @property
    def child_type(self) -> "NodeType | None":
        """该类型节点的子节点类型（Area → Process → Thread）。"""
        return {NodeType.AREA: NodeType.PROCESS, NodeType.PROCESS: NodeType.THREAD}.get(self)

    @property
    def parent_type(self) -> "NodeType | None":
        return {NodeType.PROCESS: NodeType.AREA, NodeType.THREAD: NodeType.PROCESS}.get(self)


class Status(str, Enum):
    """节点状态。状态只用于管理当前事项，不决定热力图颜色。"""

    TODO = "待开始"
    DOING = "进行中"
    PAUSED = "已暂停"
    DONE = "已完成"
    ARCHIVED = "已归档"


STATUS_CHOICES: tuple[Status, ...] = (
    Status.TODO,
    Status.DOING,
    Status.PAUSED,
    Status.DONE,
    Status.ARCHIVED,
)


class EventType(str, Enum):
    """历史事件类型（只追加，不覆盖）。"""

    CREATE = "create"
    RENAME = "rename"
    MOVE = "move"
    STATUS = "status"
    PIN = "pin"
    NEXT_ACTION = "next_action"
    ASSOCIATE = "associate"
    DISSOCIATE = "dissociate"
    DELETE = "delete"
    RESTORE = "restore"
    DESCRIPTION = "description"

    @property
    def label(self) -> str:
        return {
            "create": "创建",
            "rename": "重命名",
            "move": "归属变更",
            "status": "状态变更",
            "pin": "重点置顶",
            "next_action": "下一步行动",
            "associate": "建立关联",
            "dissociate": "解除关联",
            "delete": "移入已删除",
            "restore": "恢复",
            "description": "注释更新",
        }[self.value]


@dataclass
class Node:
    id: str
    type: NodeType
    name: str
    parent_id: str | None
    position: int
    status: Status
    pinned: bool
    next_action: str
    created_at: str
    updated_at: str
    archived_at: str | None = None
    deleted_at: str | None = None
    deleted_group: str | None = None
    description: str = ""

    @property
    def is_archived(self) -> bool:
        return self.status is Status.ARCHIVED

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @classmethod
    def from_row(cls, row: Any) -> "Node":
        return cls(
            id=row["id"],
            type=NodeType(row["type"]),
            name=row["name"],
            parent_id=row["parent_id"],
            position=row["position"],
            status=Status(row["status"]),
            pinned=bool(row["pinned"]),
            next_action=row["next_action"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived_at=row["archived_at"],
            deleted_at=row["deleted_at"],
            deleted_group=row["deleted_group"],
            description=row["description"] or "",
        )


@dataclass
class Association:
    id: str
    thread_id: str
    process_id: str
    created_at: str
    ended_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Association":
        return cls(
            id=row["id"],
            thread_id=row["thread_id"],
            process_id=row["process_id"],
            created_at=row["created_at"],
            ended_at=row["ended_at"],
        )


@dataclass
class PathStep:
    """归属路径快照中的一层。"""

    id: str
    name: str
    type: NodeType

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "type": self.type.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PathStep":
        return cls(id=data["id"], name=data["name"], type=NodeType(data["type"]))


@dataclass
class Activity:
    """一条活动记录。

    归属快照（area_id / process_id / thread_id / path_snapshot）在创建时写入，
    之后不因节点移动、重命名、暂停或归档而改变。
    """

    id: str
    node_id: str
    node_type: NodeType
    activity_date: str
    created_at: str
    note: str
    area_id: str | None
    process_id: str | None
    thread_id: str | None
    path_snapshot: list[PathStep] = field(default_factory=list)

    @property
    def path_text(self) -> str:
        return " → ".join(step.name for step in self.path_snapshot)

    @classmethod
    def from_row(cls, row: Any) -> "Activity":
        try:
            raw = json.loads(row["path_snapshot"] or "[]")
        except (json.JSONDecodeError, TypeError):
            raw = []
        return cls(
            id=row["id"],
            node_id=row["node_id"],
            node_type=NodeType(row["node_type"]),
            activity_date=row["activity_date"],
            created_at=row["created_at"],
            note=row["note"] or "",
            area_id=row["area_id"],
            process_id=row["process_id"],
            thread_id=row["thread_id"],
            path_snapshot=[PathStep.from_dict(item) for item in raw],
        )


@dataclass
class Event:
    id: str
    node_id: str
    event_type: EventType
    before: Any
    after: Any
    created_at: str
    note: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "Event":
        def _load(value: Any) -> Any:
            if value is None:
                return None
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value

        return cls(
            id=row["id"],
            node_id=row["node_id"],
            event_type=EventType(row["event_type"]),
            before=_load(row["before_value"]),
            after=_load(row["after_value"]),
            created_at=row["created_at"],
            note=row["note"] or "",
        )
