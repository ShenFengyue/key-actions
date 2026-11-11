# -*- coding: utf-8 -*-
"""AI 回复里的「记录意图」：解析、日期换算、节点匹配。

为什么要单独一层：模型拿不到本地节点树（隐私红线：只发人设 + 对话内容），
所以它只能从用户的话里抽出「记在哪件事上」的关键词，真正的节点由本地匹配决定，
写不写、写到哪，最后都要用户确认。

这个模块不依赖 Qt，也不联网，可以直接单元测试。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta

from . import clock

MARKER = "<<RECORD>>"

# 模型有时会无视协议，把 JSON 包在代码块里；这里一并认。
_MARKER_LINE = re.compile(r"^\s*" + re.escape(MARKER) + r"\s*(\S.*)$", re.M)
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\[.*?\]\s*\})\s*```", re.S)

_RELATIVE = {
    "今天": 0,
    "今日": 0,
    "当天": 0,
    "昨天": 1,
    "昨日": 1,
    "前天": 2,
}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_DAY = re.compile(r"^(\d{1,2})-(\d{1,2})$")


@dataclass(frozen=True)
class RecordIntent:
    """一条待写入的活动：目标关键词、日期、备注。"""

    target: str
    day: str
    note: str

    @property
    def display(self) -> str:
        return f"{self.target} · {self.day}" + (f" · {self.note}" if self.note else "")


def normalize_day(text: str, today: date | None = None) -> str:
    """把「今天 / 昨天 / 前天 / 2026-11-03 / 11-03 / 留空」统一成 ISO 日期。"""
    base = today or clock.today()
    raw = (text or "").strip()
    if not raw:
        return base.isoformat()
    if raw in _RELATIVE:
        return (base - timedelta(days=_RELATIVE[raw])).isoformat()
    if _ISO.match(raw):
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            return base.isoformat()
    match = _MONTH_DAY.match(raw)
    if match:
        try:
            guess = date(base.year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return base.isoformat()
        # 说的是「未来」的日子，多半指去年（例如 1 月 2 日说 12-30）
        return (guess if guess <= base else guess.replace(year=base.year - 1)).isoformat()
    return base.isoformat()


def _payload_from(text: str) -> tuple[str, list[dict]]:
    """从回复里抠出记录数据，返回（去掉该行的文本, 原始条目列表）。"""
    if not text:
        return "", []

    kept_lines: list[str] = []
    payloads: list[str] = []
    for line in text.splitlines():
        match = _MARKER_LINE.match(line)
        if match:
            payloads.append(match.group(1).strip())
            continue
        kept_lines.append(line)
    cleaned = "\n".join(kept_lines).strip("\n")

    if not payloads:
        for match in _FENCED_JSON.finditer(text):
            payloads.append(match.group(1).strip())
        if payloads:
            cleaned = _FENCED_JSON.sub("", text).strip("\n")

    items: list[dict] = []
    for payload in payloads:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else data.get("records", data.get("items", []))
        if isinstance(candidates, dict):
            candidates = [candidates]
        for entry in candidates if isinstance(candidates, list) else []:
            if isinstance(entry, dict):
                items.append(entry)
    return cleaned, items


def extract(text: str, today: date | None = None) -> tuple[str, list[RecordIntent]]:
    """把回复拆成（给人看的文本, 待写入记录）。

    数据坏了就当没有记录 —— 显示不能因为一段解析失败而崩掉。
    """
    cleaned, items = _payload_from(text or "")
    intents: list[RecordIntent] = []
    for entry in items:
        target = str(entry.get("target") or entry.get("node") or entry.get("name") or "").strip()
        if not target:
            continue
        intents.append(
            RecordIntent(
                target=target,
                day=normalize_day(str(entry.get("date") or entry.get("day") or ""), today),
                note=str(entry.get("note") or entry.get("remark") or "").strip(),
            )
        )
    return cleaned, intents


def protocol_hint() -> str:
    """发给模型的附加系统提示：怎样输出记录意图。

    这里描述的是格式约定，不含任何用户数据。
    """
    return (
        "当用户让你记录一次活动（例如“记一下今天背了四十分钟单词”），"
        "在正常回答之外，单独输出一行，以 "
        f"{MARKER} 开头，紧跟一个 JSON："
        '{"records":[{"target":"用户原话里指出做的事","date":"今天或昨天或YYYY-MM-DD","note":"简短备注"}]}。'
        "target 必须照抄用户用过的说法，不要猜节点名；date 缺省写 今天；"
        "一次可以有多条。用户没有要求记录时，不要输出这一行。"
    )
