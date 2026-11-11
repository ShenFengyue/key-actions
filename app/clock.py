"""全项目统一的时间来源：北京时间（UTC+8）。

**校准方式**：本应用**不联网校准**（规则三：不使用远程 API、不需要联网）。
它读取的是 Windows / 系统时钟给出的绝对时间（UTC），再换算成固定的 UTC+8 时区，
所以即使系统时区被设成别的城市，显示与记录下来的仍然是北京时间。
时间准不准取决于系统时钟本身；Windows 默认会自己联网对时
（设置 → 时间和语言 → 日期和时间 → 自动设置时间 / 立即同步）。

**唯一例外**：本模块是项目里唯一允许读取系统时间的地方。
`tests/test_ui_smoke.py` 里有一条测试会扫描源码，禁止其它模块直接调用
`datetime.now()` / `date.today()`，避免时间来源不统一。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
BEIJING_LABEL = "北京时间"
WEEKDAY_NAMES = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def now() -> datetime:
    """当前的北京时间（带时区信息的 datetime）。"""
    return datetime.now(BEIJING_TZ)


def today() -> date:
    """北京时间的今天。"""
    return now().date()


def stamp() -> str:
    """北京时间的时间戳，用于写入数据库：2026-10-15 21:34:56。"""
    return now().strftime("%Y-%m-%d %H:%M:%S")


def compact_stamp() -> str:
    """用于文件名的紧凑时间戳：2026-10-15-213456。"""
    return now().strftime("%Y-%m-%d-%H%M%S")


def today_str() -> str:
    """北京时间的今天（YYYY-MM-DD）。"""
    return today().isoformat()


def display(value: datetime | None = None) -> tuple[str, str]:
    """给界面用：返回（时刻, 日期）两行文本。"""
    current = value or now()
    return (
        current.strftime("%H:%M:%S"),
        f"{current.year}年{current.month}月{current.day}日 {WEEKDAY_NAMES[current.weekday()]}",
    )
