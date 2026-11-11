"""热力图统计与日历网格计算（纯 Python，不依赖 Qt）。

统计规则（产品文档第 6 节）：
    - 颜色深浅只取决于当天有效活动记录的数量；
    - 固定分档：0 / 1 / 2 / 3 / 4 次及以上；
    - 每条活动记录在同一张热力图中最多统计一次；
    - 依据活动发生时保存的归属快照统计，不依据今天的树形位置。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from . import clock

DEFAULT_WEEKS = 52
MAX_LEVEL = 4
# 总热力图用两套 1~5 级：1–5 次绿色，6–10 次橙色
MAX_GLOBAL_LEVEL = 10

# GitHub 风格的绿色阶梯（0 无活动背景色 → 4 最深绿色）。
LEVEL_COLORS: tuple[str, ...] = (
    "#ebedf0",
    "#9be9a8",
    "#40c463",
    "#30a14e",
    "#216e39",
)
GREEN_SCALE: tuple[str, ...] = ("#9be9a8", "#40c463", "#30a14e", "#216e39", "#0e4429")
ORANGE_SCALE: tuple[str, ...] = ("#ffd8a8", "#ffb86c", "#f0883e", "#d9651a", "#a33d0a")
# 0 = 空；1–5 = 绿色五级；6–10 = 橙色五级
LEVEL_COLORS_GLOBAL: tuple[str, ...] = (LEVEL_COLORS[0],) + GREEN_SCALE + ORANGE_SCALE
# 注意：绘制的颜色以 app/theme.py 为准（它随深浅色切换）；
# 这里保留同名常量只是为了兼容历史引用，不要用它去画界面。
EMPTY_CELL_BORDER = "#d8dee4"


def level_for(count: int) -> int:
    """固定分档：1→最浅，2→第二档，3→第三档，4 次及以上→最深。"""
    if count <= 0:
        return 0
    return min(count, MAX_LEVEL)


def level_for_global(count: int) -> int:
    """总热力图专用：1–5 次对应绿色五级，6–10 次对应橙色五级。"""
    if count <= 0:
        return 0
    return min(count, MAX_GLOBAL_LEVEL)


def color_for(count: int) -> str:
    return LEVEL_COLORS[level_for(count)]


# ------------------------------------------------------------------ 统计
def _counts(conn: sqlite3.Connection, column: str, scope_id: str,
            date_from: str | None, date_to: str | None) -> dict[str, int]:
    sql = f"SELECT activity_date, count(*) AS n FROM activities WHERE {column} = ?"
    params: list[object] = [scope_id]
    if date_from:
        sql += " AND activity_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND activity_date <= ?"
        params.append(date_to)
    sql += " GROUP BY activity_date"
    return {row["activity_date"]: int(row["n"]) for row in conn.execute(sql, params).fetchall()}


def counts_for_area(
    conn: sqlite3.Connection,
    area_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, int]:
    """Area 热力图：统计发生时归属于该 Area 的活动（含其 Process 与 Thread 的活动）。"""
    return _counts(conn, "area_id", area_id, date_from, date_to)


def counts_all(
    conn: sqlite3.Connection,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, int]:
    """总热力图：全部领域、进程与线程的活动次数。

    一条活动记录只算一次（它本身只有一行），不会因为既属于进程又属于领域而重复。
    """
    sql = "SELECT activity_date, count(*) AS n FROM activities WHERE 1 = 1"
    params: list[object] = []
    if date_from:
        sql += " AND activity_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND activity_date <= ?"
        params.append(date_to)
    sql += " GROUP BY activity_date"
    return {row["activity_date"]: int(row["n"]) for row in conn.execute(sql, params).fetchall()}


def counts_for_process(
    conn: sqlite3.Connection,
    process_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, int]:
    """Process 热力图：只统计发生时归属于该 Process 的活动。

    关联其他 Process 不会让活动进入那张热力图。
    """
    return _counts(conn, "process_id", process_id, date_from, date_to)


def counts_for_thread(
    conn: sqlite3.Connection,
    thread_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, int]:
    """Thread 热力图：只统计「当时就记在这个线程上」的活动。

    记在进程上的活动（记录时没有选具体线程）不会出现在线程热力图里 ——
    它们本来就不属于任何线程，只归属到进程与领域。
    """
    return _counts(conn, "thread_id", thread_id, date_from, date_to)


def summaries(
    conn: sqlite3.Connection,
    column: str,
    scope_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    per_day: int = 3,
) -> dict[str, list[str]]:
    """按日期给出简短活动摘要，用于悬停提示。"""
    where = f"{column} = ?"
    params: list[object] = [scope_id]
    if date_from:
        where += " AND activity_date >= ?"
        params.append(date_from)
    if date_to:
        where += " AND activity_date <= ?"
        params.append(date_to)
    return _summaries(conn, where, params, per_day)


def summaries_all(
    conn: sqlite3.Connection,
    date_from: str | None = None,
    date_to: str | None = None,
    per_day: int = 3,
) -> dict[str, list[str]]:
    """总热力图的悬停摘要。"""
    where = "1 = 1"
    params: list[object] = []
    if date_from:
        where += " AND activity_date >= ?"
        params.append(date_from)
    if date_to:
        where += " AND activity_date <= ?"
        params.append(date_to)
    return _summaries(conn, where, params, per_day)


def _summaries(
    conn: sqlite3.Connection,
    where: str,
    params: list[object],
    per_day: int,
) -> dict[str, list[str]]:
    sql = (
        f"SELECT activity_date, note, path_snapshot FROM activities WHERE {where}"
        " ORDER BY activity_date, created_at"
    )
    result: dict[str, list[str]] = {}
    for row in conn.execute(sql, params).fetchall():
        bucket = result.setdefault(row["activity_date"], [])
        if len(bucket) < per_day:
            note = (row["note"] or "").strip()
            if not note:
                note = _last_name(row["path_snapshot"]) or "活动记录"
            bucket.append(note)
    return result


def _last_name(path_snapshot: str | None) -> str:
    import json

    try:
        steps = json.loads(path_snapshot or "[]")
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(steps, list) and steps:
        return str(steps[-1].get("name", ""))
    return ""


def window_totals(counts: dict[str, int]) -> tuple[int, int]:
    """返回（活动次数, 有活动的天数）。"""
    return sum(counts.values()), sum(1 for value in counts.values() if value > 0)


def scope_counts(conn: sqlite3.Connection, column: str, day: str | None = None) -> dict[str, int]:
    """按作用域（area_id / process_id / thread_id）聚合活动次数。

    day 为 None 时统计全部历史，用于思维导图卡片上的「今日 n · 共 m」。
    """
    if column not in {"area_id", "process_id", "thread_id"}:
        raise ValueError(f"不支持的作用域列：{column}")
    sql = f"SELECT {column} AS scope, count(*) AS n FROM activities WHERE {column} IS NOT NULL"
    params: list[object] = []
    if day:
        sql += " AND activity_date = ?"
        params.append(day)
    sql += " GROUP BY scope"
    return {row["scope"]: int(row["n"]) for row in conn.execute(sql, params).fetchall()}


def latest_activity_by_node(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """每个节点最近一条活动（日期, 备注）。"""
    rows = conn.execute(
        "SELECT node_id, activity_date, note, max(created_at) AS latest"
        " FROM activities GROUP BY node_id"
    ).fetchall()
    return {
        row["node_id"]: (row["activity_date"], row["note"] or "")
        for row in rows
    }


# -------------------------------------------------------------- 日历网格
@dataclass(frozen=True)
class CalendarGrid:
    """GitHub 风格日历网格：横向按周排列，纵向按星期排列。"""

    start: date
    end: date
    weeks: int
    columns: tuple[tuple[str | None, ...], ...]  # 每列 7 个格子（周日 → 周六）
    month_labels: tuple[tuple[int, str], ...]  # (列索引, 形如 "1月")

    @property
    def date_from(self) -> str:
        return self.start.isoformat()

    @property
    def date_to(self) -> str:
        return self.end.isoformat()


def _sunday_offset(day: date) -> int:
    """该日期在「周日为一周第一列」中的行号。"""
    return (day.weekday() + 1) % 7


def build_calendar(end_date: date | None = None, weeks: int = DEFAULT_WEEKS) -> CalendarGrid:
    """构造以 end_date 所在周为最后一列的日历网格。"""
    end = end_date or clock.today()
    current_sunday = end - timedelta(days=_sunday_offset(end))
    start = current_sunday - timedelta(weeks=weeks - 1)

    columns: list[tuple[str | None, ...]] = []
    for week_index in range(weeks):
        days: list[str | None] = []
        for day_index in range(7):
            current = start + timedelta(days=week_index * 7 + day_index)
            days.append(current.isoformat() if current <= end else None)
        columns.append(tuple(days))

    month_labels = _month_labels(start, weeks)
    return CalendarGrid(
        start=start,
        end=end,
        weeks=weeks,
        columns=tuple(columns),
        month_labels=month_labels,
    )


def _month_labels(start: date, weeks: int) -> tuple[tuple[int, str], ...]:
    """月份标记：出现在该月第一列上方，太近的标记会被省略以避免重叠。"""
    labels: list[tuple[int, str]] = []
    previous_month: int | None = None
    last_column = -99
    for week_index in range(weeks):
        column_day = start + timedelta(days=week_index * 7)
        month = column_day.month
        if month == previous_month:
            continue
        previous_month = month
        # 距离上一个标记至少 3 列，且不在最后两列，避免文字压到边缘或相互重叠。
        if week_index - last_column < 3 or week_index > weeks - 3:
            continue
        labels.append((week_index, f"{month}月"))
        last_column = week_index
    return tuple(labels)
