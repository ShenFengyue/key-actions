"""能量分配：分配算法不变量、管理器、Markdown 档案、v6→v7 升级与页面冒烟。"""

from __future__ import annotations

import os
import random
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402  必须在设置平台后导入

from app import paths  # noqa: E402
from app.energy import (  # noqa: E402
    TOTAL_UNITS,
    EnergyManager,
    PoolLog,
    add_pool_units,
    percent_to_units,
    remove_pool_units,
    set_value,
    units_to_percent,
    validate,
)
from app.storage import SCHEMA_VERSION, Database  # noqa: E402
from tests._support import make_temp_dir, remove_temp_dir  # noqa: E402


class AllocationTest(unittest.TestCase):
    """分配不变量：总和恒等于 10000；加能量只从空余扣，不够报能量不足。"""

    def test_initial_total(self) -> None:
        units = [3000, 2000, 2500, 1000, 1500]
        validate(units)
        self.assertEqual(sum(units), TOTAL_UNITS)

    def test_increase_takes_from_free_only(self) -> None:
        # 空余 3000，给池 1 加 2000：只动空余，池 2 不受影响
        self.assertEqual(set_value([3000, 5000, 2000], 1, 7000, 0), [1000, 7000, 2000])

    def test_increase_insufficient_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            set_value([0, 5000, 5000], 1, 6000, 0)
        self.assertIn("能量不足", str(ctx.exception))
        # 差一点点也不行：空余 1000，要加 1001
        with self.assertRaises(ValueError):
            set_value([1000, 5000, 4000], 1, 6001, 0)

    def test_increase_exactly_free_is_ok(self) -> None:
        self.assertEqual(set_value([1000, 5000, 4000], 1, 6000, 0), [0, 6000, 4000])

    def test_decrease_returns_to_free(self) -> None:
        self.assertEqual(set_value([1000, 5000, 4000], 1, 3000, 0), [3000, 3000, 4000])

    def test_decrease_to_zero_returns_all(self) -> None:
        self.assertEqual(set_value([0, 10000], 1, 0, 0), [10000, 0])

    def test_free_pool_cannot_be_set_directly(self) -> None:
        with self.assertRaises(ValueError):
            set_value([3000, 7000], 0, 1000, 0)
        with self.assertRaises(ValueError):
            set_value([10000], 0, 0, 0)

    def test_add_zero_pool_preserves_total(self) -> None:
        result = add_pool_units([6000, 4000])
        self.assertEqual(result, [6000, 4000, 0])
        validate(result)

    def test_delete_moves_energy_to_free(self) -> None:
        # 删除 3000 的池，能量全部进入空余池（索引 3）
        self.assertEqual(remove_pool_units([5000, 3000, 2000, 0], 1, 3), [5000, 2000, 3000])

    def test_delete_exact_amount_no_rounding_loss(self) -> None:
        self.assertEqual(remove_pool_units([9999, 1, 0], 1, 2), [9999, 1])
        self.assertEqual(remove_pool_units([3333, 3333, 3334, 0], 2, 3), [3333, 3333, 3334])

    def test_free_pool_cannot_be_deleted(self) -> None:
        with self.assertRaises(ValueError):
            remove_pool_units([0, 10000], 0, 0)

    def test_last_user_pool_can_be_deleted(self) -> None:
        # 用户池全删光也行，能量全部回到空余
        self.assertEqual(remove_pool_units([0, 10000], 1, 0), [10000])

    def test_negative_input_clamped(self) -> None:
        result = set_value([5000, 5000], 1, -500, 0)
        self.assertEqual(result, [10000, 0])
        self.assertTrue(all(u >= 0 for u in result))

    def test_over_100_clamped(self) -> None:
        self.assertEqual(set_value([5000, 5000], 1, 20000, 0), [0, 10000])
        # 钳到 100% 也要空余够才行
        with self.assertRaises(ValueError):
            set_value([0, 5000, 5000], 1, 20000, 0)

    def test_no_change_is_noop(self) -> None:
        self.assertEqual(set_value([2000, 5000, 3000], 1, 5000, 0), [2000, 5000, 3000])

    def test_percent_conversion(self) -> None:
        self.assertEqual(percent_to_units(30.25), 3025)
        self.assertEqual(percent_to_units(0), 0)
        self.assertEqual(percent_to_units(100), 10000)
        self.assertEqual(percent_to_units(150), 10000)
        self.assertEqual(units_to_percent(3025), "30.25")

    def test_random_operations_keep_invariant(self) -> None:
        rng = random.Random(42)
        units = [0, 10000]  # 索引 0 固定为空余能量池
        for _ in range(300):
            op = rng.choice(["set", "add", "del"])
            if op == "set" and len(units) > 1:
                i = rng.randrange(1, len(units))
                try:
                    units = set_value(units, i, rng.randrange(-100, 10101), 0)
                except ValueError:
                    pass  # 能量不足：保持原样
            elif op == "add" and len(units) < 12:
                units = add_pool_units(units)
            elif op == "del" and len(units) > 2:
                units = remove_pool_units(units, rng.randrange(1, len(units)), 0)
            validate(units)
            self.assertEqual(sum(units), TOTAL_UNITS)
            self.assertGreaterEqual(min(units), 0)


class _EnergyCase(unittest.TestCase):
    """隔离临时目录 + 独立数据库的公共底座。"""

    def setUp(self) -> None:
        self.tmp = make_temp_dir()
        self._saved = {"BACKUP_DIR": paths.BACKUP_DIR}
        paths.BACKUP_DIR = self.tmp / "backups"
        self.db = Database(self.tmp / "energy.sqlite3")
        self.db.initialize(seed=False)
        self.changes = 0
        self.manager = EnergyManager(self.db, on_change=self._count)

    def _count(self) -> None:
        self.changes += 1

    def tearDown(self) -> None:
        self.db.close()
        for key, value in self._saved.items():
            setattr(paths, key, value)
        remove_temp_dir(self.tmp)


class EnergyManagerTest(_EnergyCase):
    def test_seed_demo_and_free_pool(self) -> None:
        pools = self.manager.pools()
        self.assertEqual(len(pools), 6)
        self.assertEqual(self.manager.total_units(), TOTAL_UNITS)
        free = self.manager.free_pool()
        self.assertEqual(free["name"], "空余能量")
        self.assertEqual(free["percentage_units"], 0)
        # 空余池排在最后
        self.assertEqual(pools[-1]["is_free"], 1)
        # 再建一个管理器不会重复播种
        EnergyManager(self.db)
        self.assertEqual(len(self.manager.pools()), 6)

    def test_add_pool_starts_at_zero(self) -> None:
        pid = self.manager.add_pool("阅读")
        pools = self.manager.pools()
        self.assertEqual(len(pools), 7)
        added = next(p for p in pools if p["id"] == pid)
        self.assertEqual(added["percentage_units"], 0)
        self.assertEqual(self.manager.total_units(), TOTAL_UNITS)
        # 新池排在空余池之前
        self.assertEqual(pools[-1]["name"], "空余能量")
        self.assertIn("创建", self.manager.log.read(pid, "阅读"))
        self.assertGreater(self.changes, 0)

    def test_set_percentage_takes_from_free_and_logs(self) -> None:
        # 先删一个池腾出 30% 空余，再给英语加 15%
        self.manager.delete_pool(self.manager.pools()[0]["id"])
        target = next(p for p in self.manager.pools() if p["name"] == "英语")
        before = self.changes
        self.manager.set_percentage(target["id"], 3500)
        pools = self.manager.pools()
        self.assertEqual(
            next(p for p in pools if p["id"] == target["id"])["percentage_units"], 3500)
        self.assertEqual(self.manager.free_pool()["percentage_units"], 1500)
        # 其他池不受影响
        self.assertEqual(
            next(p for p in pools if p["name"] == "工作")["percentage_units"], 2500)
        self.assertEqual(sum(p["percentage_units"] for p in pools), TOTAL_UNITS)
        self.assertEqual(self.changes, before + 1)
        log = self.manager.log.read(target["id"], target["name"])
        self.assertIn("20.00% → 35.00%", log)
        self.assertIn("从空余能量扣除", log)
        free = self.manager.free_pool()
        self.assertIn("自动扣除", self.manager.log.read(free["id"], free["name"]))

    def test_increase_without_free_raises_and_writes_nothing(self) -> None:
        target = self.manager.pools()[0]  # 空余为 0
        before = self.changes
        with self.assertRaises(ValueError) as ctx:
            self.manager.set_percentage(target["id"], 4000)
        self.assertIn("能量不足", str(ctx.exception))
        self.assertEqual(self.manager.pools()[0]["percentage_units"], 3000)
        self.assertEqual(self.changes, before)

    def test_decrease_returns_to_free(self) -> None:
        target = self.manager.pools()[0]
        self.manager.set_percentage(target["id"], 1000)
        self.assertEqual(self.manager.free_pool()["percentage_units"], 2000)
        self.assertEqual(self.manager.total_units(), TOTAL_UNITS)
        log = self.manager.log.read(target["id"], target["name"])
        self.assertIn("回到空余能量", log)

    def test_delete_pool_moves_units_to_free(self) -> None:
        target = self.manager.pools()[0]  # 30%
        self.manager.delete_pool(target["id"])
        pools = self.manager.pools()
        self.assertEqual(len(pools), 5)
        self.assertEqual(self.manager.free_pool()["percentage_units"], 3000)
        self.assertEqual(sum(p["percentage_units"] for p in pools), TOTAL_UNITS)
        self.assertNotIn(target["id"], [p["id"] for p in pools])

    def test_free_pool_cannot_be_deleted_or_renamed(self) -> None:
        free = self.manager.free_pool()
        with self.assertRaises(ValueError):
            self.manager.delete_pool(free["id"])
        self.manager.update_info(free["id"], name="改名试试", color="#123456")
        free = self.manager.free_pool()
        self.assertEqual(free["name"], "空余能量")
        self.assertEqual(free["color"], "#6B7680")

    def test_free_pool_percentage_cannot_be_set(self) -> None:
        free = self.manager.free_pool()
        with self.assertRaises(ValueError):
            self.manager.set_percentage(free["id"], 5000)

    def test_rename_and_color_logged(self) -> None:
        target = self.manager.pools()[1]
        self.manager.update_info(target["id"], name="英语学习", color="#112233")
        pool = next(p for p in self.manager.pools() if p["id"] == target["id"])
        self.assertEqual(pool["name"], "英语学习")
        self.assertEqual(pool["color"], "#112233")
        log = self.manager.log.read(target["id"], pool["name"])
        self.assertIn("改名", log)
        self.assertIn("#112233", log)

    def test_unknown_pool_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.set_percentage("no-such-id", 100)
        with self.assertRaises(ValueError):
            self.manager.delete_pool("no-such-id")

    def test_pool_log_files_live_next_to_database(self) -> None:
        target = self.manager.pools()[0]
        self.manager.record_activity(target["id"], "今天推进了一小时")
        path = self.tmp / "pool_logs" / f"pool_{target['id']}.md"
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("记录：今天推进了一小时", text)
        # 时间戳来自北京时间来源（格式 YYYY-MM-DD HH:MM）
        self.assertRegex(text, r"- \d{4}-\d{2}-\d{2} \d{2}:\d{2} ")

    def test_pool_log_write_and_clear(self) -> None:
        log: PoolLog = self.manager.log
        target = self.manager.pools()[0]
        log.write(target["id"], "# 自定义内容\n")
        self.assertEqual(log.read(target["id"], target["name"]), "# 自定义内容\n")
        log.clear(target["id"], target["name"])
        self.assertNotIn("自定义内容", log.read(target["id"], target["name"]))


class EnergyMigrationTest(_EnergyCase):
    def test_v6_to_v7_upgrade_keeps_data(self) -> None:
        # 先造一个 v6 旧库：有标语数据、没有 energy_pools 表
        from app.services import LifeManagerService

        service = LifeManagerService(self.db)
        service.set_slogan("升级前的标语")
        with self.db.transaction() as tx:
            tx.execute("DROP TABLE energy_pools")
            tx.execute("UPDATE meta SET value = '6' WHERE key = 'schema_version'")
        self.db.close()

        self.db = Database(self.tmp / "energy.sqlite3")
        self.db.initialize(seed=False)
        self.assertEqual(self.db.get_meta("schema_version"), str(SCHEMA_VERSION))
        # 旧数据还在，新表可用
        service = LifeManagerService(self.db)
        self.assertEqual(service.current_slogan().text, "升级前的标语")
        manager = EnergyManager(self.db)
        self.assertEqual(manager.total_units(), TOTAL_UNITS)
        manager.set_percentage(manager.pools()[0]["id"], 2000)
        self.assertEqual(manager.total_units(), TOTAL_UNITS)
        # 升级前自动备份
        self.assertTrue(list(paths.BACKUP_DIR.glob("pre-migration-*.sqlite3")))


class EnergyPageSmokeTest(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.tmp = make_temp_dir()
        self.db = Database(self.tmp / "page.sqlite3")
        self.db.initialize(seed=False)
        from app.services import LifeManagerService

        self.service = LifeManagerService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        remove_temp_dir(self.tmp)

    def test_page_builds_cards_and_total(self) -> None:
        from app.pages.energy import EnergyPage

        page = EnergyPage(self.service)
        self.assertEqual(len(page._cards), 6)
        self.assertIn("100.00%", page.total_label.text())
        # 能量写入会推进数据版本，界面按统一机制刷新
        before = self.service.revision
        page.manager.set_percentage(page.manager.pools()[0]["id"], 2000)
        self.assertEqual(self.service.revision, before + 1)
        page.refresh()
        self.assertIn("100.00%", page.total_label.text())
        page.deleteLater()

    def test_refresh_does_not_leave_ghost_cards(self) -> None:
        """连续刷新后，容器里未隐藏的卡片必须恰好等于池数（旧卡片立即隐藏）。"""
        from app.pages.energy import EnergyPage
        from app.widgets.energy_pool import PoolCard

        page = EnergyPage(self.service)
        page.refresh()
        page.refresh()
        visible = [w for w in page.container.findChildren(PoolCard) if not w.isHidden()]
        self.assertEqual(len(visible), 6)
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()