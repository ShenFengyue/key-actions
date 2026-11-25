"""能量分配：把 100% 的精力分给若干个「能量池」，水缸界面在 pages/energy.py。

核心不变量：任何操作后所有池的占比总和恰好等于 10000 基点（= 100.00%），
每个值 >= 0。舍入用最大余数法，平局按位置先后，结果完全确定。

「空余能量」池常驻且不可删除：删除其他池时额度全部流入它，等待重新分配；
调低它的占比即可把空余能量按比例分给其他池。

每个池的变化自动追加进项目内的 Markdown 档案（data/pool_logs/pool_<id>.md），
可以直接用文本编辑器查看或修改。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import clock
from .storage import Database, new_id

TOTAL_UNITS = 10000

FREE_NAME = "空余能量"
FREE_COLOR = "#6B7680"
FREE_SORT = 9999

# 首次使用时的示例分配（与 Energy_Money 原版一致）。
DEMO_POOLS = [
    ("AI / Coding", 3000, "#48C6C3"),
    ("英语", 2000, "#5B8DEF"),
    ("工作", 2500, "#E2B477"),
    ("运动", 1000, "#7FC97F"),
    ("娱乐", 1500, "#C77DDB"),
]

DEFAULT_COLORS = ["#48C6C3", "#5B8DEF", "#E2B477", "#7FC97F",
                  "#C77DDB", "#E27D7D", "#6FD3E8", "#D9C86A"]


# ---------------------------------------------------------------- 分配算法

def largest_remainder(weights: list[int], total: int) -> list[int]:
    """按权重比例把 total 分给各项，返回整数列表，和恰好为 total。

    权重全为 0 时平均分配；余数按小数余量从大到小补齐，平局按索引。
    """
    if total < 0:
        raise ValueError("total 不能为负")
    n = len(weights)
    if n == 0:
        if total != 0:
            raise ValueError("没有可分配的对象")
        return []
    if any(w < 0 for w in weights):
        raise ValueError("权重不能为负")
    s = sum(weights)
    if s == 0:
        base, rem = divmod(total, n)
        return [base + (1 if i < rem else 0) for i in range(n)]
    floors = [total * w // s for w in weights]
    remainders = [total * w % s for w in weights]
    leftover = total - sum(floors)
    order = sorted(range(n), key=lambda i: (-remainders[i], i))
    for i in order[:leftover]:
        floors[i] += 1
    return floors


def validate(units: list[int]) -> None:
    if not units:
        raise ValueError("至少需要一个能量池")
    if any(u < 0 for u in units):
        raise ValueError("分配值不能为负")
    if sum(units) != TOTAL_UNITS:
        raise ValueError(f"分配总和必须为 {TOTAL_UNITS}，当前为 {sum(units)}")


def set_value(units: list[int], index: int, new_value: int) -> list[int]:
    """把 index 池设为 new_value，其余池按原比例分摊剩余额度。"""
    validate(units)
    if not 0 <= index < len(units):
        raise IndexError("池索引越界")
    new_value = max(0, min(TOTAL_UNITS, int(new_value)))
    if len(units) == 1:
        return [TOTAL_UNITS]
    others = [u for i, u in enumerate(units) if i != index]
    remaining = TOTAL_UNITS - new_value
    if sum(others) == 0:
        distributed = largest_remainder([0] * len(others), remaining)
    else:
        distributed = largest_remainder(others, remaining)
    result: list[int] = []
    it = iter(distributed)
    for i in range(len(units)):
        result.append(new_value if i == index else next(it))
    validate(result)
    return result


def add_pool_units(units: list[int]) -> list[int]:
    """新增池初始为 0%，总和不变。"""
    validate(units)
    result = units + [0]
    validate(result)
    return result


def remove_pool_units(units: list[int], index: int, free_index: int) -> list[int]:
    """删除池，其额度全部转入「空余能量」池，等待重新分配。

    空余能量池本身不可删除；其余池可以全部删光（能量全部回到空余）。
    """
    validate(units)
    if not 0 <= free_index < len(units):
        raise IndexError("空余能量池索引越界")
    if index == free_index:
        raise ValueError("空余能量池不能删除")
    if not 0 <= index < len(units):
        raise IndexError("池索引越界")
    removed = units[index]
    result = [u for i, u in enumerate(units) if i != index]
    new_free = free_index if free_index < index else free_index - 1
    result[new_free] += removed
    validate(result)
    return result


def units_to_percent(units: int) -> str:
    return f"{units / 100:.2f}"


def percent_to_units(percent: float) -> int:
    """界面输入的百分比转基点，四舍五入到 0.01%。"""
    return max(0, min(TOTAL_UNITS, int(round(percent * 100))))


# ---------------------------------------------------------------- Markdown 档案

class PoolLog:
    """能量池 Markdown 档案：每个池一个 .md 文件，变化自动追加，可人工编辑。

    存放位置：<数据库所在目录>/pool_logs/pool_<id>.md（正式环境即 data/pool_logs/）。
    """

    def __init__(self, base_dir: Path | str):
        self.dir = Path(base_dir) / "pool_logs"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, pool_id: str) -> Path:
        return self.dir / f"pool_{pool_id}.md"

    def append(self, pool_id: str, name: str, line: str) -> None:
        p = self.path(pool_id)
        stamp = clock.now().strftime("%Y-%m-%d %H:%M")
        if not p.exists():
            p.write_text(f"# 能量池：{name}\n\n", encoding="utf-8")
        with p.open("a", encoding="utf-8") as f:
            f.write(f"- {stamp} {line}\n")

    def read(self, pool_id: str, name: str) -> str:
        p = self.path(pool_id)
        if p.exists():
            return p.read_text(encoding="utf-8")
        return f"# 能量池：{name}\n\n（暂无记录）\n"

    def write(self, pool_id: str, content: str) -> None:
        self.path(pool_id).write_text(content, encoding="utf-8")

    def clear(self, pool_id: str, name: str) -> None:
        self.path(pool_id).write_text(f"# 能量池：{name}\n\n", encoding="utf-8")


# ---------------------------------------------------------------- 管理器

class EnergyManager:
    """能量池业务逻辑：管理 + 分配不变量 + 持久化 + 空余能量池 + Markdown 档案。

    所有多步写入都在同一个事务里完成；写入成功后调用 on_change
    （接 LifeManagerService.notify_external_change，让界面按数据版本刷新）。
    """

    def __init__(self, db: Database, on_change: Callable[[], None] | None = None,
                 pool_log: PoolLog | None = None):
        self.db = db
        self.on_change = on_change
        self.log = pool_log or PoolLog(db.path.parent)
        if not self.pools():
            self._seed_demo()
        self._ensure_free_pool()

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def _seed_demo(self) -> None:
        stamp = clock.stamp()
        with self.db.transaction() as tx:
            for i, (name, units, color) in enumerate(DEMO_POOLS):
                tx.execute(
                    "INSERT INTO energy_pools(id, name, percentage_units, color,"
                    " sort_order, is_free, created_at, updated_at)"
                    " VALUES(?,?,?,?,?,0,?,?)",
                    (new_id(), name, units, color, i, stamp, stamp))
        self._notify()

    def _ensure_free_pool(self) -> None:
        if not any(p["is_free"] for p in self.pools()):
            stamp = clock.stamp()
            with self.db.transaction() as tx:
                tx.execute(
                    "INSERT INTO energy_pools(id, name, percentage_units, color,"
                    " sort_order, is_free, created_at, updated_at)"
                    " VALUES(?,?,0,?,?,1,?,?)",
                    (new_id(), FREE_NAME, FREE_COLOR, FREE_SORT, stamp, stamp))
            self._notify()

    # ------------------------------------------------------------ 读取

    def pools(self) -> list[dict]:
        rows = self.db.conn.execute(
            "SELECT * FROM energy_pools ORDER BY sort_order, id").fetchall()
        return [dict(r) for r in rows]

    def free_pool(self) -> dict:
        return next(p for p in self.pools() if p["is_free"])

    def total_units(self) -> int:
        return sum(p["percentage_units"] for p in self.pools())

    def _require(self, pool_id: str) -> tuple[list[dict], int]:
        pools = self.pools()
        ids = [p["id"] for p in pools]
        if pool_id not in ids:
            raise ValueError("能量池不存在")
        return pools, ids.index(pool_id)

    # ------------------------------------------------------------ 写入

    def add_pool(self, name: str, color: str | None = None) -> str:
        pools = self.pools()
        color = color or DEFAULT_COLORS[len(pools) % len(DEFAULT_COLORS)]
        add_pool_units([p["percentage_units"] for p in pools])
        pid = new_id()
        stamp = clock.stamp()
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO energy_pools(id, name, percentage_units, color,"
                " sort_order, is_free, created_at, updated_at)"
                " VALUES(?,?,0,?,?,0,?,?)",
                (pid, name.strip() or "未命名", color, len(pools) - 1, stamp, stamp))
        self.log.append(pid, name.strip() or "未命名", "创建，初始占比 0.00%")
        self._notify()
        return pid

    def update_info(self, pool_id: str, name: str | None = None,
                    color: str | None = None) -> None:
        pools, idx = self._require(pool_id)
        pool = pools[idx]
        fields: dict[str, object] = {}
        if name is not None and name.strip() and not pool["is_free"]:
            new_name = name.strip()
            if new_name != pool["name"]:
                self.log.append(pool_id, new_name,
                                f"改名：{pool['name']} → {new_name}")
            fields["name"] = new_name
        if color is not None and not pool["is_free"]:
            if color != pool["color"]:
                self.log.append(pool_id, pool["name"], f"颜色改为 {color}")
            fields["color"] = color
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.transaction() as tx:
            tx.execute(
                f"UPDATE energy_pools SET {sets}, updated_at=? WHERE id=?",
                (*fields.values(), clock.stamp(), pool_id))
        self._notify()

    def set_percentage(self, pool_id: str, units: int) -> None:
        pools, idx = self._require(pool_id)
        old_units = [p["percentage_units"] for p in pools]
        new_units = set_value(old_units, idx, units)
        stamp = clock.stamp()
        with self.db.transaction() as tx:
            for p, u in zip(pools, new_units):
                tx.execute(
                    "UPDATE energy_pools SET percentage_units=?, updated_at=? WHERE id=?",
                    (u, stamp, p["id"]))
        target_name = pools[idx]["name"]
        for p, old, new in zip(pools, old_units, new_units):
            if old == new:
                continue
            if p["id"] == pool_id:
                line = f"占比 {units_to_percent(old)}% → {units_to_percent(new)}%"
            else:
                line = (f"占比 {units_to_percent(old)}% → {units_to_percent(new)}%"
                        f"（因「{target_name}」调整自动分摊）")
            self.log.append(p["id"], p["name"], line)
        self._notify()

    def delete_pool(self, pool_id: str) -> None:
        pools, idx = self._require(pool_id)
        free_idx = next(i for i, p in enumerate(pools) if p["is_free"])
        if idx == free_idx:
            raise ValueError("空余能量池不能删除")
        removed = pools[idx]["percentage_units"]
        new_units = remove_pool_units(
            [p["percentage_units"] for p in pools], idx, free_idx)
        rest = [p for p in pools if p["id"] != pool_id]
        stamp = clock.stamp()
        with self.db.transaction() as tx:
            tx.execute("DELETE FROM energy_pools WHERE id=?", (pool_id,))
            for p, u in zip(rest, new_units):
                tx.execute(
                    "UPDATE energy_pools SET percentage_units=?, updated_at=? WHERE id=?",
                    (u, stamp, p["id"]))
        self.log.append(pool_id, pools[idx]["name"],
                        f"删除，{units_to_percent(removed)}% 转入空余能量")
        free = pools[free_idx]
        self.log.append(free["id"], free["name"],
                        f"接收来自「{pools[idx]['name']}」的 {units_to_percent(removed)}%")
        self._notify()

    def record_activity(self, pool_id: str, note: str) -> None:
        """手动记录一条能量动态，追加进该池的 Markdown 档案。"""
        pools, idx = self._require(pool_id)
        self.log.append(pool_id, pools[idx]["name"], f"记录：{note}")
        self._notify()