"""界面冒烟测试（离屏渲染）：确保五个页面可以构建、切换与刷新。"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402  必须在设置平台后导入

from app import heatmap as heatmap_stats  # noqa: E402
from app import paths, theme  # noqa: E402
from app.appconfig import AppConfig  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.models import NodeType, Status, today_str  # noqa: E402
from app.services import LifeManagerService  # noqa: E402
from app.storage import Database  # noqa: E402
from app.widgets.heatmap_calendar import HeatmapCalendar  # noqa: E402
from tests._support import make_temp_dir, remove_temp_dir  # noqa: E402


class UiSmokeTest(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.tmp = make_temp_dir()
        self._saved = {"BACKUP_DIR": paths.BACKUP_DIR, "EXPORT_DIR": paths.EXPORT_DIR}
        paths.BACKUP_DIR = self.tmp / "backups"
        paths.EXPORT_DIR = self.tmp / "exports"
        self._saved["DIARY_PATH"] = paths.DIARY_PATH
        paths.DIARY_PATH = self.tmp / "diary.md"
        self.db = Database(self.tmp / "ui.sqlite3")
        self.db.initialize()
        self.service = LifeManagerService(self.db)
        self.config = AppConfig(self.tmp / "config.json")
        self.window = MainWindow(self.service, self.config)

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.db.close()
        for key, value in self._saved.items():
            setattr(paths, key, value)
        remove_temp_dir(self.tmp)

    def test_all_pages_build_and_switch(self) -> None:
        for key in ("overview", "heatmaps", "mindmap", "activity", "processes", "diary", "data"):
            self.window.set_page(key)
            self.app.processEvents()
            self.assertIs(self.window.stack.currentWidget(), self.window.pages[key])

    def test_nav_order_and_data_at_bottom(self) -> None:
        from app.widgets.sidebar import ALL_NAV_ITEMS, NAV_ITEMS

        keys = [key for key, _label, _shortcut in NAV_ITEMS]
        self.assertEqual(
            keys, ["overview", "ai", "heatmaps", "mindmap", "processes", "activity"]
        )
        self.assertEqual(ALL_NAV_ITEMS[-1][0], "settings")
        # 设置按钮在左栏里是最靠下的那个
        buttons = self.window.sidebar.buttons
        self.assertGreater(
            buttons["settings"]
            .mapTo(self.window.sidebar, buttons["settings"].rect().center())
            .y(),
            buttons["diary"].mapTo(self.window.sidebar, buttons["diary"].rect().center()).y(),
        )

    def test_nav_spacing_has_three_tiers(self) -> None:
        """组内最紧、两组之间一档、与总览 / 与日记最大。"""
        sidebar = self.window.sidebar
        self.window.resize(1400, 900)
        self.window.show()
        self.app.processEvents()

        def gap(upper: str, lower: str) -> int:
            top = sidebar.buttons[upper].mapTo(sidebar, QPoint(0, 0)).y()
            bottom = (
                sidebar.buttons[upper].mapTo(sidebar, QPoint(0, 0)).y()
                + sidebar.buttons[upper].height()
            )
            return sidebar.buttons[lower].mapTo(sidebar, QPoint(0, 0)).y() - bottom

        inside = gap("heatmaps", "mindmap")       # 组内
        between = gap("mindmap", "processes")     # 两组之间
        from_home = gap("overview", "heatmaps")   # 与总览
        self.assertLess(inside, between)
        self.assertLessEqual(between, from_home)
        # 日记现在紧贴数据上方
        self.assertLess(gap("diary", "data"), between)

    def test_sidebar_can_collapse_and_expand(self) -> None:
        self.window.set_page("overview")
        self.assertTrue(self.window.sidebar.isVisibleTo(self.window))
        self.window.toggle_sidebar()
        self.assertFalse(self.window.sidebar.isVisibleTo(self.window))
        self.window.toggle_sidebar()
        self.assertTrue(self.window.sidebar.isVisibleTo(self.window))

    def test_sidebar_buttons_are_independent(self) -> None:
        """导航按钮彼此独立、留有间隙，且不再有分组框容器。

        （"整组居中"是旧布局的要求，现在总览靠上、日记贴底，所以不再断言居中。）
        """
        from PySide6.QtWidgets import QFrame

        from app.widgets.sidebar import NAV_ITEMS

        sidebar = self.window.sidebar
        self.window.resize(1400, 900)
        self.window.show()
        self.app.processEvents()

        buttons = [sidebar.buttons[key] for key, _label, _shortcut in NAV_ITEMS]
        self.assertEqual(len(buttons), len(NAV_ITEMS))
        for above, below in zip(buttons, buttons[1:]):
            gap = (
                below.mapTo(sidebar, QPoint(0, 0)).y()
                - above.mapTo(sidebar, QPoint(0, 0)).y()
                - above.height()
            )
            self.assertGreater(gap, 0, "相邻导航按钮之间应有空隙")

        self.assertEqual(sidebar.findChildren(QFrame, "NavGroup"), [])

    def test_legend_appears_exactly_once_per_page(self) -> None:
        """「少 → 多」图例不重复：热力图页与总览页各只留一个。"""
        from app.widgets.heatmap_calendar import HeatmapLegend

        for key in ("heatmaps", "overview"):
            self.window.set_page(key)
            self.app.processEvents()
            legends = self.window.pages[key].findChildren(HeatmapLegend)
            self.assertEqual(
                len(legends), 1, f"{key} 页面的图例应有 1 个，实际 {len(legends)} 个"
            )

    def test_empty_heatmap_is_dimmed(self) -> None:
        """整张窗口没有记录的热力图进入淡化状态，有记录就恢复。"""
        from app.widgets.heatmap_calendar import HeatmapCalendar

        calendar = HeatmapCalendar()
        calendar.set_data({})
        self.assertTrue(calendar.is_empty)
        calendar.set_data({"2026-10-15": 1})
        self.assertFalse(calendar.is_empty)

    def test_heatmap_page_rows_are_tiered(self) -> None:
        """热力图页：领域行比进程行高、进程行有缩进、领域之间有分隔线。"""
        from PySide6.QtWidgets import QFrame

        from app.pages.heatmaps import (
            AREA_ROW_HEIGHT,
            PROCESS_INDENT,
            PROCESS_ROW_HEIGHT,
        )

        page = self.window.pages["heatmaps"]
        self.window.set_page("heatmaps")
        self.app.processEvents()
        area_row = next(iter(page._area_rows.values()))
        process_row = next(iter(page._process_rows.values()))

        self.assertEqual(area_row.height(), AREA_ROW_HEIGHT)
        self.assertEqual(process_row.height(), PROCESS_ROW_HEIGHT)
        self.assertGreater(AREA_ROW_HEIGHT, PROCESS_ROW_HEIGHT)
        spacer = process_row.layout().itemAt(0).spacerItem()
        self.assertIsNotNone(spacer)
        self.assertEqual(spacer.sizeHint().width(), PROCESS_INDENT)
        self.assertGreaterEqual(
            len(page.container.findChildren(QFrame, "HeatmapDivider")), 2
        )

    def test_process_tree_spacing_is_tiered(self) -> None:
        """进程树只用间距分层：三级行高递减、缩进加深。"""
        from app.pages.processes import TREE_INDENT

        page = self.window.pages["processes"]
        self.window.set_page("processes")
        self.app.processEvents()
        tree = page.tree
        self.assertEqual(tree.indentation(), TREE_INDENT)

        process_item = None
        for index in range(tree.topLevelItemCount()):
            area = tree.topLevelItem(index)
            for child_index in range(area.childCount()):
                child = area.child(child_index)
                if child.childCount():
                    process_item = child
                    break
            if process_item is not None:
                break
        self.assertIsNotNone(process_item)
        area_item = process_item.parent()
        thread_item = process_item.child(0)
        self.assertGreater(
            area_item.sizeHint(0).height(), process_item.sizeHint(0).height()
        )
        self.assertGreater(
            process_item.sizeHint(0).height(), thread_item.sizeHint(0).height()
        )

    def test_area_divider_only_between_areas(self) -> None:
        """领域之间的分割线：第一个领域上方不画，进程与线程行也不画。"""
        from app.pages.processes import AreaDividerDelegate, should_draw_area_divider

        tree = self.window.pages["processes"].tree
        self.assertIsInstance(tree.itemDelegate(), AreaDividerDelegate)
        self.assertGreaterEqual(tree.topLevelItemCount(), 2)

        first = tree.indexFromItem(tree.topLevelItem(0), 0)
        second = tree.indexFromItem(tree.topLevelItem(1), 0)
        child = tree.indexFromItem(tree.topLevelItem(0).child(0), 0)
        self.assertFalse(should_draw_area_divider(first))
        self.assertTrue(should_draw_area_divider(second))
        self.assertFalse(should_draw_area_divider(child))

    def test_mindmap_card_marks_today_records(self) -> None:
        """记录地图卡片会带上"今天记过几次"，供绿色小圆点使用。"""
        area = self.service.areas()[0]
        process = self.service.processes(area.id)[0]
        page = self.window.pages["mindmap"]
        self.window.set_page("mindmap")
        self.app.processEvents()
        self.assertEqual(page.view._cards[process.id].data.today_count, 0)

        self.service.record_activity(process.id, "今天做过")
        page.refresh()
        self.assertEqual(page.view._cards[process.id].data.today_count, 1)

    def test_quick_capture_shortcut_registered(self) -> None:
        """Ctrl+N 是极简快速记录（左栏不显示这个键，但功能在）。"""
        from PySide6.QtGui import QKeySequence

        self.assertEqual(
            self.window.quick_capture_shortcut.key(), QKeySequence("Ctrl+N")
        )

    def test_sidebar_shows_slogan_and_hint(self) -> None:
        """没写过标语时给一句灰字提示；写过之后左栏显示最新那条。"""
        from app.widgets.sidebar import SloganLabel

        sidebar = self.window.sidebar
        self.assertIsInstance(sidebar.slogan_label, SloganLabel)
        self.assertEqual(sidebar.slogan_text(), "")
        self.assertIn("双击", sidebar.slogan_label.text())

        self.service.set_slogan("一次只推进一件事")
        self.window.reload_slogan()
        self.assertEqual(sidebar.slogan_text(), "一次只推进一件事")
        self.assertEqual(sidebar.slogan_label.text(), "一次只推进一件事")

    def test_slogan_label_double_click_requests_edit(self) -> None:
        """双击标语会发出编辑请求（真正弹窗的是主窗口，这里只验证控件本身）。"""
        from PySide6.QtTest import QTest

        from app.widgets.sidebar import SloganLabel

        label = SloganLabel()
        seen: list[int] = []
        label.editRequested.connect(lambda: seen.append(1))
        QTest.mouseDClick(label, Qt.MouseButton.LeftButton)
        self.assertEqual(len(seen), 1)

    def test_data_page_lists_slogan_history(self) -> None:
        """数据页按时间倒序列出标语历史，第一条标成当前显示。"""
        self.service.set_slogan("第一版")
        self.service.set_slogan("第二版")
        page = self.window.pages["data"]
        self.window.set_page("data")
        self.app.processEvents()

        self.assertEqual(page.slogan_table.rowCount(), 2)
        newest = page.slogan_table.item(0, 1).text()
        self.assertIn("第二版", newest)
        self.assertIn("当前显示", newest)
        self.assertIn("第一版", page.slogan_table.item(1, 1).text())

    def test_tree_move_order_syncs_to_mindmap_and_heatmaps(self) -> None:
        """进程树里「下移一位」：记录地图与热力图页的顺序跟着一起变。"""
        area = self.service.areas()[0]
        before = [node.id for node in self.service.processes(area.id)]
        self.assertGreaterEqual(len(before), 2)

        page = self.window.pages["processes"]
        self.window.set_page("processes")
        self.app.processEvents()
        page.tree.setCurrentItem(page.tree.topLevelItem(0).child(0))
        page._move_selected_order(1)

        after = [node.id for node in self.service.processes(area.id)]
        self.assertEqual(after[0], before[1])
        self.assertEqual(after[1], before[0])

        mindmap = self.window.pages["mindmap"]
        self.window.set_page("mindmap")
        self.app.processEvents()
        seen = [node_id for node_id in mindmap.view._cards if node_id in set(before)]
        self.assertEqual(seen[:2], after[:2], "记录地图的顺序应与进程树一致")

        heatmaps = self.window.pages["heatmaps"]
        self.window.set_page("heatmaps")
        self.app.processEvents()
        self.assertEqual(
            list(heatmaps._process_rows)[:2], after[:2], "热力图页的顺序应与进程树一致"
        )

    def test_tree_drop_rules_only_allow_same_level(self) -> None:
        """拖放规则：同一层内可调序；跨层级、「放进节点里」、拖到自己身上都拒绝。"""
        from PySide6.QtWidgets import QAbstractItemView

        tree = self.window.pages["processes"].tree
        first_area = tree.topLevelItem(0)
        second_area = tree.topLevelItem(1)
        process_a = first_area.child(0)
        process_b = first_area.child(1)
        Above = QAbstractItemView.DropIndicatorPosition.AboveItem
        Below = QAbstractItemView.DropIndicatorPosition.BelowItem
        On = QAbstractItemView.DropIndicatorPosition.OnItem

        self.assertTrue(tree.can_drop(process_b, process_a, Above))
        self.assertTrue(tree.can_drop(process_a, process_b, Below))
        self.assertFalse(tree.can_drop(process_a, process_a, Above))
        self.assertFalse(tree.can_drop(process_a, second_area, Below))
        self.assertFalse(tree.can_drop(process_a, second_area, On))
        if process_b.childCount():
            self.assertFalse(
                tree.can_drop(process_b.child(0), process_a, Above),
                "线程不能靠拖动换到别的进程下（那是「移动到…」的活）",
            )

    def test_focus_cards_keep_manual_order(self) -> None:
        """当前重点可以手动调序，顺序记在界面配置里，数据本身不受影响。"""
        area = self.service.areas()[0]
        ids = [process.id for process in self.service.processes(area.id)[:3]]
        self.assertEqual(len(ids), 3)
        for node_id in ids:
            self.service.set_pinned(node_id, True)

        page = self.window.pages["overview"]
        page.refresh()
        self.assertEqual([card.node_id for card in page._focus_cards], ids)

        # 把最后一张挪到最前面
        page._move_focus_card(ids[-1], 0)
        expected = [ids[2], ids[0], ids[1]]
        self.assertEqual([card.node_id for card in page._focus_cards], expected)
        self.assertEqual(self.config.get("focus_order"), expected)

        # 再次刷新（相当于重开页面）仍然按手动顺序显示
        page.refresh()
        self.assertEqual([card.node_id for card in page._focus_cards], expected)

        # 只是显示顺序：置顶状态与集合没有变化
        self.assertEqual(
            sorted(node.id for node in self.service.focused_nodes()), sorted(ids)
        )

    def test_focus_card_drag_uses_pointer_slot(self) -> None:
        """拖动落点按鼠标横向位置换算：停在第 2 张的左侧，就排到第 2 位。"""
        area = self.service.areas()[0]
        ids = [process.id for process in self.service.processes(area.id)[:3]]
        for node_id in ids:
            self.service.set_pinned(node_id, True)

        self.window.resize(1400, 900)
        self.window.show()
        self.app.processEvents()
        page = self.window.pages["overview"]
        page.refresh()
        self.app.processEvents()

        second = page._focus_cards[1]
        drop_at = second.mapToGlobal(second.rect().topLeft() + QPoint(5, 5))
        page._on_focus_card_moved(ids[0], drop_at)
        self.assertEqual(
            [card.node_id for card in page._focus_cards], [ids[1], ids[0], ids[2]]
        )

    def test_focus_card_drag_with_real_mouse_events(self) -> None:
        """真实鼠标链路：按下 → 拖过阈值 → 移到第三张上 → 松手，卡片换位并写入配置。"""
        from PySide6.QtTest import QTest

        area = self.service.areas()[0]
        ids = [process.id for process in self.service.processes(area.id)[:3]]
        for node_id in ids:
            self.service.set_pinned(node_id, True)

        self.window.resize(1400, 900)
        self.window.show()
        self.app.processEvents()
        page = self.window.pages["overview"]
        page.refresh()
        self.app.processEvents()

        first = page._focus_cards[0]
        third = page._focus_cards[2]
        center = first.rect().center()
        QTest.mousePress(first, Qt.MouseButton.LeftButton, pos=center)
        QTest.mouseMove(first, center + QPoint(10, 8))
        target = third.mapTo(first, third.rect().center())
        QTest.mouseMove(first, target)
        self.assertEqual([card.node_id for card in page._focus_cards], [ids[1], ids[2], ids[0]])
        QTest.mouseRelease(first, Qt.MouseButton.LeftButton, pos=target)
        self.assertEqual(self.config.get("focus_order"), [ids[1], ids[2], ids[0]])

    def test_ctrl_n_really_opens_the_capture_panel(self) -> None:
        """端到端：按下 Ctrl+N 会弹出面板（用定时器把它关掉，避免阻塞测试）。"""
        from PySide6.QtCore import QTimer
        from PySide6.QtTest import QTest

        from app.widgets.quick_capture import QuickCaptureDialog

        opened: list[object] = []

        def close_active() -> None:
            active = QApplication.activeModalWidget()
            opened.append(active)
            if active is not None:
                active.reject()

        self.window.show()
        self.app.processEvents()
        QTimer.singleShot(150, close_active)
        QTest.keyClick(
            self.window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier
        )
        self.assertTrue(opened)
        self.assertIsInstance(opened[0], QuickCaptureDialog)

    def test_sidebar_buttons_are_large_and_hide_shortcuts(self) -> None:
        from PySide6.QtGui import QShortcut

        sidebar = self.window.sidebar
        self.assertEqual(len(sidebar.buttons), 9)
        for button in sidebar.buttons.values():
            self.assertGreaterEqual(button.minimumHeight(), 44, "按钮太小，不好点击")
            self.assertFalse(hasattr(button, "hint"), "快捷键文字应该已经移除")
            self.assertTrue(button.shortcut_text.startswith("Ctrl+"))
        # 快捷键功能保留（只是不再显示在界面上）
        shortcuts = {
            shortcut.key().toString() for shortcut in self.window.findChildren(QShortcut)
        }
        for key in ("Ctrl+1", "Ctrl+7", "Ctrl+8", "Ctrl+9"):
            self.assertIn(key, shortcuts)

    def test_process_tree_lists_all_areas(self) -> None:
        page = self.window.pages["processes"]
        self.window.set_page("processes")
        self.app.processEvents()
        names = [
            page.tree.topLevelItem(index).text(0)
            for index in range(page.tree.topLevelItemCount())
        ]
        self.assertEqual(names, ["领域 1", "领域 2", "领域 3"])

    def test_heatmap_page_has_area_and_process_calendars(self) -> None:
        page = self.window.pages["heatmaps"]
        self.window.set_page("heatmaps")
        self.app.processEvents()
        self.assertEqual(len(page._area_rows), 3)
        self.assertEqual(len(page._process_rows), 9)

    def test_heatmap_renders_github_green_cells(self) -> None:
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        from datetime import date, timedelta

        today = date.today()
        # 四天分别记录 1 / 2 / 3 / 5 次，覆盖全部四档绿色
        for offset, times in ((1, 1), (2, 2), (3, 3), (4, 5)):
            for index in range(times):
                self.service.record_activity(
                    english.id,
                    f"{times} 次中的第 {index + 1} 次",
                    (today - timedelta(days=offset)).isoformat(),
                )

        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        row = self.window.pages["heatmaps"]._process_rows[explore.id]
        row.apply_window(heatmap_stats.build_calendar())
        self.app.processEvents()

        pixmap = row.calendar.grab()
        image = pixmap.toImage()
        colors = {
            image.pixelColor(x, y).name()
            for x in range(0, image.width(), 2)
            for y in range(0, image.height(), 2)
        }
        self.assertIn(heatmap_stats.LEVEL_COLORS[1], colors)
        self.assertIn(heatmap_stats.LEVEL_COLORS[2], colors)
        self.assertIn(heatmap_stats.LEVEL_COLORS[3], colors)
        self.assertIn(heatmap_stats.LEVEL_COLORS[4], colors)

    def test_overview_lists_recent_activity(self) -> None:
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "界面冒烟", "2026-10-01")
        page = self.window.pages["overview"]
        self.window.set_page("overview")
        self.app.processEvents()
        self.assertGreaterEqual(page.recent_table.rowCount(), 1)
        self.assertEqual(page.recent_table.item(0, 1).text(), "领域 3 → 进程 3-3 → 线程 3-3-2")

    def test_activity_page_filters_by_node(self) -> None:
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "筛选测试", "2026-10-02")
        page = self.window.pages["activity"]
        self.window.set_page("activity")
        self.app.processEvents()
        page.keyword.setText("筛选测试")
        page.refresh()
        self.app.processEvents()
        self.assertEqual(len(page._activities), 1)
        self.assertIn("1 条记录", page.count_label.text())

    def test_data_page_reports_integrity(self) -> None:
        page = self.window.pages["data"]
        self.window.set_page("data")
        self.app.processEvents()
        text = page.integrity_output.toPlainText()
        self.assertIn("SQLite 结构检查：ok", text)
        self.assertIn("领域 3", text)

    def test_theme_colors_match_github_scale(self) -> None:
        self.assertEqual(theme.LEVEL_COLORS, heatmap_stats.LEVEL_COLORS)

    def test_app_icon_is_available_at_taskbar_sizes(self) -> None:
        """任务栏/标题栏图标：资源在位、能被 Qt 读出、各档尺寸都不是空白。"""
        from app import paths

        self.assertTrue(paths.LOGO_PNG.exists(), "缺少 assets/logo.png")
        self.assertTrue(paths.LOGO_ICO.exists(), "缺少 assets/logo.ico（多尺寸图标）")

        icon = theme.app_icon()
        self.assertFalse(icon.isNull(), "应用图标读不出来")
        sizes = {(item.width(), item.height()) for item in icon.availableSizes()}
        for expected in ((16, 16), (32, 32), (48, 48)):
            self.assertIn(expected, sizes, f"图标缺少 {expected[0]}px 这一档")

        for size in (16, 32, 48):
            pixmap = icon.pixmap(size, size)
            self.assertFalse(pixmap.isNull(), f"{size}px 图标是空的")
            image = pixmap.toImage()
            dark = sum(
                1
                for x in range(image.width())
                for y in range(image.height())
                if image.pixelColor(x, y).lightness() < 100
            )
            self.assertGreater(dark, 0, f"{size}px 图标里没有深色图形（像是空白图）")

    def test_main_window_has_the_icon(self) -> None:
        self.assertFalse(self.window.windowIcon().isNull(), "主窗口没有设置图标")

    def test_refresh_removes_stale_widgets(self) -> None:
        """回归测试：刷新后不能残留带默认几何的旧控件（会盖住其它控件）。"""
        from PySide6.QtWidgets import QWidget as _QWidget

        from app.widgets.common import Card

        page = self.window.pages["overview"]
        self.window.set_page("overview")
        for _ in range(3):
            page.refresh()
        self.app.processEvents()

        pinned = len(self.service.focused_nodes())
        cards = [w for w in page.findChildren(Card) if w.objectName() == "FocusCard"]
        self.assertEqual(len(cards), pinned)
        stale = [
            child
            for child in page.findChildren(_QWidget)
            if child.isVisible()
            and child.size().width() == 640
            and child.size().height() == 480
        ]
        self.assertEqual(stale, [], "存在未从控件树中移除的旧控件")

    def test_node_combo_selects_selectable_node(self) -> None:
        page = self.window.pages["overview"]
        self.window.set_page("overview")
        self.app.processEvents()
        node_id = page.quick_combo.current_node_id()
        self.assertIsNotNone(node_id)
        self.assertIn(self.service.require_node(node_id).type, (NodeType.PROCESS, NodeType.THREAD))

    def test_node_combo_can_list_areas_as_choices(self) -> None:
        """回归测试：要选"领域"时（新建进程 / 移动进程），领域必须是可选条目。"""
        from app.widgets.common import NodeComboBox

        combo = NodeComboBox()
        combo.populate(self.service, (NodeType.AREA,))
        self.assertEqual(combo.count(), 3, "选领域的下拉框是空的")
        self.assertEqual(
            [combo.itemText(index) for index in range(combo.count())], ["领域 1", "领域 2", "领域 3"]
        )
        current = combo.current_node_id()
        self.assertIsNotNone(current, "没有默认选中任何领域")
        self.assertIs(self.service.require_node(current).type, NodeType.AREA)

    def test_creating_a_process_lets_you_choose_the_area(self) -> None:
        """回归测试：新建进程时能选领域，并且真的建在所选领域下。"""
        from PySide6.QtWidgets import QDialog, QInputDialog

        from app.pages import processes as processes_module

        page = self.window.pages["processes"]
        self.window.set_page("processes")
        self.app.processEvents()
        target_area = self.service.node_by_name("领域 2", NodeType.AREA)

        original_exec = processes_module.ChooseNodeDialog.exec

        def fake_exec(dialog) -> int:
            self.assertIsNotNone(dialog.selected_node_id(), "对话框里没有可选的领域")
            dialog.combo.set_current_node(target_area.id)
            return QDialog.DialogCode.Accepted

        processes_module.ChooseNodeDialog.exec = fake_exec
        original_text = QInputDialog.getText
        QInputDialog.getText = staticmethod(lambda *args, **kwargs: ("测试进程", True))
        try:
            page._create_node(NodeType.PROCESS)
        finally:
            processes_module.ChooseNodeDialog.exec = original_exec
            QInputDialog.getText = original_text

        created = self.service.node_by_name("测试进程", NodeType.PROCESS)
        self.assertIsNotNone(created, "进程没有建出来")
        self.assertEqual(created.parent_id, target_area.id)

    def test_moving_a_process_to_another_area(self) -> None:
        """同一个下拉框也用于"移动进程到别的领域"，一起修好了。"""
        from PySide6.QtWidgets import QDialog

        from app.widgets import common as common_module

        page = self.window.pages["processes"]
        self.window.set_page("processes")
        self.app.processEvents()
        process = self.service.node_by_name("进程 3-2", NodeType.PROCESS)
        target_area = self.service.node_by_name("领域 1", NodeType.AREA)
        self.assertNotEqual(process.parent_id, target_area.id)

        original_exec = common_module.ChooseNodeDialog.exec

        def fake_exec(dialog) -> int:
            self.assertIsNotNone(dialog.selected_node_id(), "对话框里没有可选的领域")
            dialog.combo.set_current_node(target_area.id)
            return QDialog.DialogCode.Accepted

        common_module.ChooseNodeDialog.exec = fake_exec
        original_confirm = common_module.confirm
        common_module.confirm = lambda *args, **kwargs: True
        try:
            page.select_node(process.id)
            page._move_selected()
        finally:
            common_module.ChooseNodeDialog.exec = original_exec
            common_module.confirm = original_confirm

        self.assertEqual(self.service.require_node(process.id).parent_id, target_area.id)

    def test_tree_is_not_rebuilt_when_nothing_changed(self) -> None:
        """回归测试：没有数据变化时切换页面不应重建整棵树（闪烁的根源）。"""
        page = self.window.pages["processes"]
        self.window.set_page("processes")
        self.app.processEvents()
        work = self.service.node_by_name("领域 1", NodeType.AREA)
        first_item = page._items[work.id]

        self.window.set_page("overview")
        self.window.set_page("processes")
        self.app.processEvents()
        self.assertIs(page._items[work.id], first_item)
        page.refresh()
        self.assertIs(page._items[work.id], first_item)

    def test_status_change_refreshes_tree_once_applied(self) -> None:
        page = self.window.pages["processes"]
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        page.select_node(english.id)
        self.app.processEvents()
        self.service.set_status(english.id, Status.PAUSED)
        self.window.set_page("processes")
        self.app.processEvents()
        self.assertEqual(page._items[english.id].text(1), Status.PAUSED.value)

    def test_event_history_widget_deletes_selected_event(self) -> None:
        from app.widgets import common as common_module
        from app.widgets.common import EventHistoryList

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        widget = EventHistoryList(self.service, english.id)
        count_before = self.service.event_count(english.id)
        self.assertGreater(count_before, 0)
        widget.listing.item(0).setSelected(True)
        removed_id = widget.selected_event_ids()[0]

        original_confirm = common_module.confirm
        common_module.confirm = lambda *args, **kwargs: True
        try:
            widget._delete_selected()
        finally:
            common_module.confirm = original_confirm

        self.assertEqual(self.service.event_count(english.id), count_before - 1)
        remaining = [
            widget.listing.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(widget.listing.count())
        ]
        self.assertNotIn(removed_id, remaining)

    def test_deleted_node_leaves_tree_and_can_be_restored_from_dialog(self) -> None:
        from app.widgets import deleted_nodes as deleted_module
        from app.widgets.deleted_nodes import DeletedNodesDialog

        page = self.window.pages["processes"]
        self.window.set_page("processes")
        self.app.processEvents()
        travel = self.service.node_by_name("进程 3-2", NodeType.PROCESS)
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "保留数据", "2026-10-13")

        self.service.delete_node(travel.id)
        self.window.on_data_changed()
        self.app.processEvents()
        self.assertNotIn(travel.id, page._items)
        self.assertIn("已删除（1）", page.deleted_button.text())

        dialog = DeletedNodesDialog(self.service, self.window)
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertFalse(dialog.restore_button.isEnabled())  # 未选中时不可点
        dialog.table.selectRow(0)
        self.app.processEvents()
        self.assertTrue(dialog.restore_button.isEnabled())

        original_info = deleted_module.info
        deleted_module.info = lambda *args, **kwargs: None
        try:
            dialog._restore_selected()
        finally:
            deleted_module.info = original_info
        self.assertEqual(self.service.deleted_entry_count(), 0)
        self.assertIsNotNone(self.service.node_by_name("进程 3-2", NodeType.PROCESS))
        self.assertEqual(len(self.service.list_activities(node_id=english.id)), 1)

    def test_deleted_scopes_are_hidden_but_their_data_stays(self) -> None:
        """删掉的节点不再在热力图页出现，但它的活动数据仍然保留。"""
        page = self.window.pages["heatmaps"]
        travel = self.service.node_by_name("进程 3-2", NodeType.PROCESS)
        self.service.record_activity(travel.id, "删除前的进程 3-2", "2026-10-14")
        self.service.delete_node(travel.id)
        self.window.set_page("heatmaps")
        self.app.processEvents()

        # 界面上彻底不出现（连"已删除"那一块也没有了）
        self.assertNotIn(travel.id, page._rows)
        self.assertNotIn(travel.id, page._process_rows)
        # 这一页里任何一行对应的节点都必须是没被删除的
        for shown_id in list(page._rows) + list(page._process_rows):
            self.assertFalse(self.service.get_node(shown_id).is_deleted)
        # 数据还在：活动仍然记在这个进程名下，恢复节点后热力图会重新出现
        self.assertEqual(len(self.service.list_activities(node_id=travel.id)), 1)
        # 领域汇总里仍然含这笔（按当时的归属快照），所以那条标注保留
        area_row = page._area_rows[
            self.service.node_by_name("领域 3", NodeType.AREA).id
        ]
        self.assertIn("含已删除进程：进程 3-2", area_row.note)

    def test_activity_page_can_delete_and_edit_record(self) -> None:
        from app.pages import activity as activity_module
        from app.widgets.activity_edit import ActivityEditDialog

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        activity = self.service.record_activity(english.id, "待修改", "2026-10-15")
        page = self.window.pages["activity"]
        self.window.set_page("activity")
        page.keyword.setText("待修改")
        page.refresh()
        self.app.processEvents()
        self.assertEqual(len(page._activities), 1)

        dialog = ActivityEditDialog(self.service, self.window, activity)
        dialog.note_edit.setText("改好了")
        dialog._save()
        self.assertTrue(dialog.saved)
        self.assertEqual(self.service.require_activity(activity.id).note, "改好了")

        original_confirm = activity_module.confirm
        activity_module.confirm = lambda *args, **kwargs: True
        try:
            page._delete_activity(self.service.require_activity(activity.id))
        finally:
            activity_module.confirm = original_confirm
        self.assertEqual(self.service.list_activities(node_id=english.id), [])

    def test_mindmap_shows_whole_structure(self) -> None:
        page = self.window.pages["mindmap"]
        self.window.set_page("mindmap")
        self.app.processEvents()
        # 3 领域 + 9 进程 + 6 线程
        self.assertEqual(len(page.view._cards), 18)
        left_most = min(card.placement.x for card in page.view._cards.values())
        for area in self.service.areas():
            self.assertEqual(page.view._cards[area.id].placement.x, left_most)
        # 父卡片默认展开：线程在第三列
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.assertGreater(
            page.view._cards[english.id].placement.x,
            page.view._cards[english.parent_id].placement.x,
        )

    def test_hiding_archived_nodes_from_mindmap(self) -> None:
        page = self.window.pages["mindmap"]
        travel = self.service.node_by_name("进程 3-2", NodeType.PROCESS)
        self.service.set_status(travel.id, Status.ARCHIVED)
        self.window.set_page("mindmap")
        self.app.processEvents()
        self.assertNotIn(travel.id, page.view._cards)
        page.archive_toggle.setChecked(True)
        self.app.processEvents()
        self.assertIn(travel.id, page.view._cards)
        self.assertIn("已归档", page.view._cards[travel.id].data.subtitle)

    def test_recording_from_mindmap_updates_every_page(self) -> None:
        """核心要求：地图上记一笔，其它页面和热力图必须跟着变。"""
        from app.pages import mindmap as mindmap_module

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        life = self.service.node_by_name("领域 3", NodeType.AREA)
        page = self.window.pages["mindmap"]
        self.window.set_page("mindmap")
        self.app.processEvents()

        class FakeRecordDialog:
            def __init__(self, service, parent=None, **kwargs) -> None:
                self.kwargs = kwargs
                self._service = service
                self.recorded = None

            def exec(self):
                self.recorded = self._service.record_activity(
                    # 用"今天"而不是写死的日期：这条测试紧接着会断言卡片显示「今日 1」，
                    # 写死日期的话，一过午夜就会假红。
                    self.kwargs["preset_node_id"], "地图上记的", today_str()
                )
                return 1

        original = mindmap_module.QuickRecordDialog
        mindmap_module.QuickRecordDialog = FakeRecordDialog
        try:
            page.record_for_node(english.id)
        finally:
            mindmap_module.QuickRecordDialog = original

        # 1) 地图自己的卡片立即更新
        self.assertIn("今日 1", page.view._cards[english.id].data.subtitle)
        self.assertIn("今日 1", page.view._cards[explore.id].data.subtitle)
        self.assertIn("今天已记录 1 次", page.today_label.text())

        # 2) 活动记录页
        self.window.set_page("activity")
        activity_page = self.window.pages["activity"]
        self.app.processEvents()
        self.assertEqual(len(activity_page._activities), 1)
        self.assertEqual(activity_page._activities[0].note, "地图上记的")

        # 3) 热力图（进程与领域两张都变）
        self.window.set_page("heatmaps")
        heatmap_page = self.window.pages["heatmaps"]
        self.app.processEvents()
        self.assertEqual(heatmap_page._process_rows[explore.id].calendar.totals()[0], 1)
        self.assertEqual(heatmap_page._area_rows[life.id].calendar.totals()[0], 1)

        # 4) 总览的最近活动
        self.window.set_page("overview")
        self.app.processEvents()
        self.assertEqual(self.window.pages["overview"].recent_table.rowCount(), 1)

        # 5) 进程树
        self.window.set_page("processes")
        self.app.processEvents()
        self.assertEqual(self.service.activity_count_for_node(english.id), 1)

    def test_mindmap_delete_moves_node_to_deleted_list(self) -> None:
        from app.widgets import common as common_module

        page = self.window.pages["mindmap"]
        travel = self.service.node_by_name("进程 3-2", NodeType.PROCESS)
        self.window.set_page("mindmap")
        self.app.processEvents()
        self.assertIn(travel.id, page.view._cards)

        original_confirm = common_module.confirm
        common_module.confirm = lambda *args, **kwargs: True
        try:
            page._delete(travel)
        finally:
            common_module.confirm = original_confirm

        self.assertNotIn(travel.id, page.view._cards)
        self.assertEqual(self.service.deleted_entry_count(), 1)
        self.window.set_page("processes")
        self.app.processEvents()
        self.assertNotIn(travel.id, self.window.pages["processes"]._items)

    def test_mindmap_card_click_starts_recording(self) -> None:
        """回归测试：单击卡片必须弹出记录窗口（曾经因为不接受按下事件而完全没反应）。"""
        from PySide6.QtTest import QTest

        from app.pages import mindmap as mindmap_module

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        page = self.window.pages["mindmap"]
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.window.show()
        self.window.set_page("mindmap")
        self.app.processEvents()

        opened: list[str | None] = []

        class FakeRecordDialog:
            def __init__(self, service, parent=None, **kwargs) -> None:
                opened.append(kwargs.get("preset_node_id"))
                self.recorded = None

            def exec(self):
                return 0

        original = mindmap_module.QuickRecordDialog
        mindmap_module.QuickRecordDialog = FakeRecordDialog
        try:
            card = page.view._cards[english.id]
            point = page.view.mapFromScene(card.sceneBoundingRect().center())
            QTest.mouseClick(
                page.view.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                point,
            )
            self.app.processEvents()
        finally:
            mindmap_module.QuickRecordDialog = original

        self.assertEqual(opened, [english.id], "单击卡片没有触发记录窗口")

    def test_mindmap_right_drag_pans_and_skips_menu(self) -> None:
        """按住右键拖动 = 自由平移；拖动之后不再弹出节点菜单。"""
        from PySide6.QtGui import QMouseEvent

        page = self.window.pages["mindmap"]
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.window.show()
        self.window.set_page("mindmap")
        self.app.processEvents()
        view = page.view
        view.zoom_by(1.6)
        self.app.processEvents()

        def send(kind, pos, button, buttons) -> None:
            event = QMouseEvent(
                kind,
                QPointF(pos),
                QPointF(pos),
                button,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(view.viewport(), event)

        before = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
        start = view.viewport().rect().center()
        send(QMouseEvent.Type.MouseButtonPress, start, Qt.MouseButton.RightButton, Qt.MouseButton.RightButton)
        for step in range(1, 6):
            send(
                QMouseEvent.Type.MouseMove,
                start + QPoint(-24 * step, -16 * step),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.RightButton,
            )
        send(
            QMouseEvent.Type.MouseButtonRelease,
            start + QPoint(-120, -80),
            Qt.MouseButton.RightButton,
            Qt.MouseButton.NoButton,
        )
        self.app.processEvents()

        after = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
        self.assertNotEqual(before, after, "右键拖动没有平移画布")
        self.assertTrue(view._suppress_menu, "右键拖动之后仍然会弹出菜单")

    def test_mindmap_view_emits_menu_request_on_right_click(self) -> None:
        """右键单击（没有拖动）仍然要弹出节点菜单。"""
        from PySide6.QtGui import QContextMenuEvent

        from app.mindmap import LayoutNode, layout_forest
        from app.widgets.mindmap_view import CardData, MindMapView

        view = MindMapView()
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        view.resize(640, 420)
        layout = layout_forest(
            [LayoutNode(id="area", depth=0, children=(LayoutNode(id="proc", depth=1),))],
            level_widths=[150.0, 170.0],
        )
        view.set_parent_map({"area": None, "proc": "area"})
        view.rebuild(
            [
                CardData(node_id="area", title="领域", subtitle="", kind="area", color="#0969da"),
                CardData(node_id="proc", title="进程", subtitle="", kind="process", color="#0969da"),
            ],
            layout,
        )
        view.show()
        self.app.processEvents()
        requested: list[str] = []
        view.nodeMenuRequested.connect(lambda node_id, _pos: requested.append(node_id))

        point = view.mapFromScene(view._cards["proc"].sceneBoundingRect().center())
        event = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, point, view.viewport().mapToGlobal(point)
        )
        QApplication.sendEvent(view.viewport(), event)
        self.app.processEvents()
        self.assertEqual(requested, ["proc"])
        view.close()
        view.deleteLater()

    def test_activity_menu_offers_edit_and_delete(self) -> None:
        from app.widgets.common import build_activity_menu

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        activity = self.service.record_activity(english.id, "右键测试", "2026-10-15")
        calls: list[tuple[str, str]] = []
        menu = build_activity_menu(
            self.window,
            activity,
            on_edit=lambda item: calls.append(("edit", item.id)),
            on_delete=lambda item: calls.append(("delete", item.id)),
        )
        self.assertEqual(
            [action.text() for action in menu.actions()],
            ["修改备注与日期…", "彻底删除这条记录…"],
        )
        for action in menu.actions():
            action.trigger()
        self.assertEqual(calls, [("edit", activity.id), ("delete", activity.id)])

    def test_activity_pages_have_context_menus(self) -> None:
        from app.widgets.common import ActivityListDialog

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        activity = self.service.record_activity(english.id, "表格右键", "2026-10-15")
        page = self.window.pages["activity"]
        self.window.set_page("activity")
        page.keyword.setText("表格右键")
        page.refresh()
        self.app.processEvents()
        self.assertEqual(page.table.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
        self.assertEqual(
            page._activity_at(page.table.visualItemRect(page.table.item(0, 0)).center()).id,
            activity.id,
        )

        dialog = ActivityListDialog(
            self.service,
            self.window,
            title="测试",
            subtitle="",
            loader=lambda: self.service.list_activities(node_id=english.id),
        )
        self.assertEqual(
            dialog.table.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.assertEqual(
            dialog._activity_at(dialog.table.visualItemRect(dialog.table.item(0, 0)).center()).id,
            activity.id,
        )
        dialog.close()

    def test_delete_record_from_dialog(self) -> None:
        from app.widgets import common as common_module
        from app.widgets.common import ActivityListDialog

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        activity = self.service.record_activity(english.id, "待删除", "2026-10-15")
        dialog = ActivityListDialog(
            self.service,
            self.window,
            title="测试",
            subtitle="",
            loader=lambda: self.service.list_activities(node_id=english.id),
        )
        dialog._activity_at(dialog.table.visualItemRect(dialog.table.item(0, 0)).center())
        original_confirm = common_module.confirm
        common_module.confirm = lambda *args, **kwargs: True
        try:
            dialog._delete_selected()
        finally:
            common_module.confirm = original_confirm
        self.assertEqual(self.service.list_activities(node_id=english.id), [])
        self.assertEqual(dialog.table.rowCount(), 0)
        dialog.close()

    def test_window_geometry_is_remembered(self) -> None:
        from app.main_window import MainWindow, clamp_to_available

        available = QRect(0, 0, 1920, 1040)
        self.assertEqual(
            clamp_to_available(QRect(100, 100, 800, 600), available), QRect(100, 100, 800, 600)
        )
        self.assertEqual(
            clamp_to_available(QRect(1800, 1000, 800, 600), available),
            QRect(1120, 440, 800, 600),
        )
        self.assertEqual(
            clamp_to_available(QRect(-300, -200, 800, 600), available), QRect(0, 0, 800, 600)
        )
        oversized = clamp_to_available(QRect(0, 0, 3000, 2000), available)
        self.assertEqual((oversized.width(), oversized.height()), (1920, 1040))

        # 关闭时保存，重新打开时恢复（用独立配置文件，避免与其它用例互相影响）
        geo_path = self.tmp / "config-geo.json"
        config = AppConfig(geo_path)
        window = MainWindow(self.service, config)
        window.resize(1180, 720)
        self.app.processEvents()
        window.close()
        self.assertTrue(config.get("window_geometry"))

        restored = MainWindow(self.service, AppConfig(geo_path))
        # 恢复出的尺寸不会超过记下来的尺寸，也不会小于窗口最小尺寸
        self.assertLessEqual(restored.width(), 1180)
        self.assertLessEqual(restored.height(), 720)
        self.assertGreaterEqual(restored.width(), restored.minimumWidth())
        self.assertGreaterEqual(restored.height(), restored.minimumHeight())
        restored.close()

    def test_clamp_to_screen_pulls_window_back_into_view(self) -> None:
        from app.main_window import MainWindow

        window = MainWindow(self.service, AppConfig(self.tmp / "config-clamp.json"))
        window.setMinimumSize(200, 150)
        window.resize(600, 400)
        screen = QApplication.primaryScreen().availableGeometry()
        window.move(screen.right() + 400, screen.bottom() + 400)
        window._clamp_to_screen()
        frame = window.frameGeometry()
        self.assertGreaterEqual(frame.left(), screen.left())
        self.assertGreaterEqual(frame.top(), screen.top())
        self.assertLessEqual(frame.right(), screen.right() + 1)
        self.assertLessEqual(frame.bottom(), screen.bottom() + 1)
        window.close()

    def test_start_maximized_setting_is_saved(self) -> None:
        from app.main_window import MainWindow

        page = self.window.pages["data"]
        page.maximized_checkbox.setChecked(True)
        self.app.processEvents()
        self.assertTrue(self.config.get("start_maximized"))
        self.assertTrue(AppConfig(self.tmp / "config.json").get("start_maximized"))

        max_config_path = self.tmp / "config-max.json"
        max_config = AppConfig(max_config_path)
        max_config.set("start_maximized", True)
        max_config.save()
        window = MainWindow(self.service, max_config)
        self.assertTrue(window.windowState() & Qt.WindowState.WindowMaximized)
        window.setWindowState(Qt.WindowState.WindowNoState)
        window.close()
        # 还原开关，避免影响同一测试类里的其它用例
        page.maximized_checkbox.setChecked(False)
        self.assertFalse(self.config.get("start_maximized"))

    def test_heatmap_widget_size_matches_github_layout(self) -> None:
        widget = HeatmapCalendar()
        expected_width = 30 + heatmap_stats.DEFAULT_WEEKS * 14 - 3
        expected_height = 18 + 7 * 14 - 3
        self.assertEqual(widget.width(), expected_width)
        self.assertEqual(widget.height(), expected_height)

    def test_heatmap_windows_end_at_today(self) -> None:
        """热力图窗口永远对齐到今天：默认构建、控件刷新、热力图页都是。"""
        from datetime import date, timedelta

        today = date.today()
        # 1) 默认构建的窗口最后一天就是今天
        self.assertEqual(heatmap_stats.build_calendar().end, today)

        # 2) 控件提供 refresh_window()，能把旧窗口挪到今天（跨午夜用）
        widget = HeatmapCalendar()
        widget.set_window(heatmap_stats.build_calendar(end_date=today - timedelta(days=30)))
        self.assertEqual(widget.grid.end, today - timedelta(days=30))
        self.assertTrue(widget.refresh_window())
        self.assertEqual(widget.grid.end, today)
        self.assertFalse(widget.refresh_window())  # 已经对齐就不再动

        # 3) 热力图页：默认窗口终点是今天；往前翻之后"回到最近"仍以今天为准
        page = self.window.pages["heatmaps"]
        self.window.set_page("heatmaps")
        self.app.processEvents()
        self.assertEqual(page._window_offset_weeks, 0)
        self.assertEqual(page._grid().end, today)
        page._shift_window(52)
        self.assertEqual(page._window_offset_weeks, 52)
        self.assertEqual(page._grid().end, today - timedelta(weeks=52))
        page._reset_window()
        self.assertEqual(page._window_offset_weeks, 0)
        self.assertEqual(page._grid().end, today)

        # 4) 总览里的热力图每次刷新都会对齐到今天
        overview = self.window.pages["overview"]
        card = overview._total_card
        card.calendar.set_window(heatmap_stats.build_calendar(end_date=today - timedelta(days=10)))
        card.refresh()
        self.assertEqual(card.calendar.grid.end, today)

    def test_process_note_hidden_until_set(self) -> None:
        """进程注释：有注释才显示小字，没有就完全不留空位。"""
        from app.pages import heatmaps as heatmaps_module

        page = self.window.pages["heatmaps"]
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        self.window.set_page("heatmaps")
        self.app.processEvents()
        row = page._process_rows[explore.id]
        self.assertEqual(row.note, "")
        self.assertEqual(row._note_label.text(), "")

        class FakeInputDialog:
            @staticmethod
            def getMultiLineText(*args, **kwargs):
                return "精听为主，先保连续 4 周", True

        original = heatmaps_module.QInputDialog
        heatmaps_module.QInputDialog = FakeInputDialog
        try:
            page._edit_note(explore.id)
        finally:
            heatmaps_module.QInputDialog = original

        self.app.processEvents()
        self.assertEqual(
            self.service.require_node(explore.id).description, "精听为主，先保连续 4 周"
        )
        self.assertEqual(row.note, "精听为主，先保连续 4 周")
        self.assertEqual(row._note_label.text(), "精听为主，先保连续 4 周")

        # 清空之后又完全不留空位
        self.service.set_description(explore.id, "")
        self.window.on_data_changed()
        self.app.processEvents()
        self.assertEqual(row.note, "")
        self.assertEqual(row._note_label.text(), "")

    def test_overview_shows_total_heatmap(self) -> None:
        """总览第一张热力图 = 全部领域与线程的总热力图。"""
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        daily = self.service.node_by_name("进程 1-1", NodeType.PROCESS)
        self.service.record_activity(english.id, "A", "2026-10-14")
        self.service.record_activity(english.id, "B", "2026-10-14")
        self.service.record_activity(daily.id, "C", "2026-10-15")

        page = self.window.pages["overview"]
        self.window.set_page("overview")
        self.app.processEvents()

        total_card = page._total_card
        self.assertIsNone(total_card.area_id)
        self.assertEqual(total_card.calendar.totals()[0], 3)
        self.assertIn("3 次", total_card.stats_label.text())
        # 各领域相加等于总数（每条记录只算一次）
        self.assertEqual(sum(card.calendar.totals()[0] for card in page._area_cards), 3)
        # 领域热力图各自一张，共三张
        self.assertEqual(len(page._area_cards), 3)
        # 总热力图明显比领域热力图大
        self.assertGreater(total_card.calendar.width(), page._area_cards[0].calendar.width())
        self.assertGreater(total_card.calendar.height(), page._area_cards[0].calendar.height())

    def test_total_heatmap_card_shows_beijing_clock(self) -> None:
        """总热力图右侧那块空白放的是实时北京时间。"""
        from app import clock

        page = self.window.pages["overview"]
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.window.show()
        self.window.set_page("overview")
        self.app.processEvents()
        card = page._total_card
        self.assertTrue(hasattr(card, "clock_widget"), "总热力图右侧没有时钟")

        widget = card.clock_widget
        widget.refresh_time()
        moment, day = clock.display()
        self.assertEqual(widget.time_label.text(), moment)
        self.assertIn(day, widget.date_label.text())
        self.assertEqual(widget.zone_label.text(), clock.BEIJING_LABEL)
        # 字号要大（之前 24px 被嫌小）
        self.assertGreaterEqual(widget.time_size, 32, "时钟字号太小")
        # 时钟在热力图右边，且（窗口够宽时）居中在"热力图右边"与"卡片右边"之间
        calendar_right = card.calendar.mapTo(card, card.calendar.rect().topRight()).x()
        clock_left = widget.mapTo(card, widget.rect().topLeft()).x()
        self.assertGreater(clock_left, calendar_right, "时钟应该在热力图右侧")
        self.window.resize(1680, 922)
        self.app.processEvents()
        calendar_right = card.calendar.mapTo(card, card.calendar.rect().topRight()).x()
        clock_left = widget.mapTo(card, widget.rect().topLeft()).x()
        clock_right = widget.mapTo(card, widget.rect().topRight()).x()
        left_gap = clock_left - calendar_right
        right_gap = card.width() - clock_right
        self.assertGreater(left_gap, 20, "时钟离热力图太近")
        self.assertLess(
            abs(left_gap - right_gap), 40, f"时钟没有居中：左 {left_gap} 右 {right_gap}"
        )
        # 总热力图保持放大尺寸，不因为放时钟而缩小
        self.assertGreaterEqual(card.calendar.width(), 940, "总热力图被改小了")
        # 领域热力图不带时钟（只有总热力图那块空白需要）
        self.assertFalse(hasattr(page._area_cards[0], "clock_widget"))

    def test_history_delete_dialog_text_matches_behaviour(self) -> None:
        """删除历史事件的提示不能再声称"数据库里还留着"。"""
        from app.widgets import common as common_module
        from app.widgets.common import EventHistoryList

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        widget = EventHistoryList(self.service, english.id)
        widget.listing.item(0).setSelected(True)
        captured: dict[str, str] = {}
        original = common_module.confirm

        def fake_confirm(parent, title, text, **kwargs):
            captured["text"] = text
            return False

        common_module.confirm = fake_confirm
        try:
            widget._delete_selected()
        finally:
            common_module.confirm = original

        text = captured.get("text", "")
        self.assertIn("无法单独恢复", text)
        self.assertNotIn("数据库备份里仍保留", text)
        # 取消确认后事件还在
        self.assertGreater(self.service.event_count(english.id), 0)

    def test_note_entry_is_on_row_right_click(self) -> None:
        """右键热力图的任意一行（领域 / 进程）都能打开注释入口。"""
        from PySide6.QtGui import QContextMenuEvent

        from app.pages import heatmaps as heatmaps_module

        page = self.window.pages["heatmaps"]
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.window.show()
        self.window.set_page("heatmaps")
        self.app.processEvents()

        row = page._process_rows[explore.id]
        requested: list = []
        row.noteRequested.connect(lambda: requested.append(1))

        class CancellingInputDialog:
            """右键会真的去弹输入框 —— 这里替换成"点了取消"，免得测试卡住。"""

            @staticmethod
            def getMultiLineText(*args, **kwargs):
                return "", False

        original = heatmaps_module.QInputDialog
        heatmaps_module.QInputDialog = CancellingInputDialog
        try:
            center = row.rect().center()
            QApplication.sendEvent(
                row,
                QContextMenuEvent(
                    QContextMenuEvent.Reason.Mouse, center, row.mapToGlobal(center)
                ),
            )
            self.app.processEvents()
            self.assertEqual(len(requested), 1, "右键进程行没有触发注释入口")

            area_row = next(iter(page._area_rows.values()))
            area_requested: list = []
            area_row.noteRequested.connect(lambda: area_requested.append(1))
            area_center = area_row.rect().center()
            QApplication.sendEvent(
                area_row,
                QContextMenuEvent(
                    QContextMenuEvent.Reason.Mouse,
                    area_center,
                    area_row.mapToGlobal(area_center),
                ),
            )
            self.app.processEvents()
            self.assertEqual(area_requested, [1], "领域行现在也应该有注释入口")
        finally:
            heatmaps_module.QInputDialog = original


    def test_diary_page_starts_empty_and_saves_to_markdown(self) -> None:
        """日记：新的一天是空的，写了保存后进 data/diary.md。"""
        from app import diary as diary_store
        from app import paths
        from app.models import today_str
        from app.widgets.common import str_to_qdate

        page = self.window.pages["diary"]
        self.window.set_page("diary")
        self.app.processEvents()
        today = today_str()
        self.assertEqual(page._current_day, today)
        self.assertEqual(page.editor.toPlainText(), "", "新的一天编辑器应该是空的")

        page.editor.setPlainText("今天试了日记功能。")
        page._save(silent=False)
        self.assertIn("今天试了日记功能。", paths.DIARY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(diary_store.read_day(today), "今天试了日记功能。")
        self.assertIn(today, [
            page.entry_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(page.entry_list.count())
        ])

        # 换到另一天：编辑器清空（不显示上一天的正文）
        page.date_edit.setDate(str_to_qdate("2026-10-01"))
        self.app.processEvents()
        self.assertEqual(page.editor.toPlainText(), "")
        self.assertEqual(page._current_day, "2026-10-01")

        # 回到今天：内容还在
        page.date_edit.setDate(str_to_qdate(today))
        self.app.processEvents()
        self.assertEqual(page.editor.toPlainText(), "今天试了日记功能。")

    def test_diary_autosaves_when_leaving_the_page(self) -> None:
        from PySide6.QtGui import QHideEvent

        from app import diary as diary_store
        from app.models import today_str

        page = self.window.pages["diary"]
        self.window.set_page("diary")
        self.app.processEvents()
        page.editor.setPlainText("写了一半就切走")
        # setPlainText 会重置"已修改"标记，这里模拟用户敲过字的状态
        page.editor.document().setModified(True)

        page.hideEvent(QHideEvent())

        self.assertEqual(diary_store.read_day(today_str()), "写了一半就切走")
        self.assertFalse(page.editor.document().isModified())

    def _open_dialog_without_blocking(self, module, block_call) -> object:
        """把 open_activity_dialog 换成一个不阻塞的版本，方便在测试里操作弹窗。"""
        from app.widgets.common import ActivityListDialog

        captured: dict[str, ActivityListDialog] = {}
        original = module.open_activity_dialog

        def fake_open(service, parent, **kwargs):
            dialog = ActivityListDialog(
                service,
                parent,
                title=kwargs["title"],
                subtitle=kwargs["subtitle"],
                loader=kwargs["loader"],
                empty_text=kwargs.get("empty_text", "当日没有活动记录。"),
            )
            if kwargs.get("on_changed") is not None:
                dialog.changed.connect(kwargs["on_changed"])
            captured["dialog"] = dialog

        module.open_activity_dialog = fake_open
        try:
            block_call()
        finally:
            module.open_activity_dialog = original
        return captured["dialog"]

    def _delete_first_row_in_dialog(self, dialog) -> None:
        from app.widgets import common as common_module

        dialog._activity_at(dialog.table.visualItemRect(dialog.table.item(0, 0)).center())
        original_confirm = common_module.confirm
        common_module.confirm = lambda *args, **kwargs: True
        try:
            dialog._delete_selected()
        finally:
            common_module.confirm = original_confirm

    def test_deleting_inside_heatmap_dialog_refreshes_page_and_popup(self) -> None:
        """回归测试：从热力图点开的那天弹窗里删掉记录后，弹窗和热力图都必须当场更新。"""
        from app.pages import heatmaps as heatmaps_module

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        self.service.record_activity(english.id, "待删的记录", "2026-10-15")
        page = self.window.pages["heatmaps"]
        self.window.set_page("heatmaps")
        self.app.processEvents()
        self.assertEqual(page._process_rows[explore.id].calendar.totals()[0], 1)

        dialog = self._open_dialog_without_blocking(
            heatmaps_module,
            lambda: page._process_rows[explore.id]._on_date_clicked("2026-10-15"),
        )
        self.assertEqual(dialog.table.rowCount(), 1)
        self._delete_first_row_in_dialog(dialog)

        # 1) 弹窗自己的列表立即变空（loader 是现场查询，不是快照）
        self.assertEqual(dialog.table.rowCount(), 0)
        # 2) 背后的热力图立即变浅，不用切页面
        self.assertEqual(page._process_rows[explore.id].calendar.totals()[0], 0)
        dialog.close()

    def test_deleting_inside_total_heatmap_dialog_refreshes_overview(self) -> None:
        """同一个问题在总览的总热力图上也要修好。"""
        from app.pages import overview as overview_module

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "总览里删掉它", "2026-10-15")
        page = self.window.pages["overview"]
        self.window.set_page("overview")
        self.app.processEvents()
        self.assertEqual(page._total_card.calendar.totals()[0], 1)

        dialog = self._open_dialog_without_blocking(
            overview_module, lambda: page._total_card._on_date_clicked("2026-10-15")
        )
        self.assertEqual(dialog.table.rowCount(), 1)
        self._delete_first_row_in_dialog(dialog)

        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertEqual(page._total_card.calendar.totals()[0], 0)
        self.assertEqual(page.recent_table.rowCount(), 0)
        dialog.close()

    def test_safety_net_refreshes_current_page(self) -> None:
        """兜底机制：即使某处改动忘了通知，当前页面也会自己发现并刷新。"""
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        page = self.window.pages["overview"]
        self.window.set_page("overview")
        self.app.processEvents()
        self.assertEqual(page.recent_table.rowCount(), 0)

        # 绕过界面直接写库（不触发任何信号）
        self.service.record_activity(english.id, "悄悄写入", "2026-10-15")
        self.window._sync_revision()
        self.app.processEvents()

        self.assertEqual(page.recent_table.rowCount(), 1)

    def test_date_change_refreshes_all_heatmap_windows(self) -> None:
        """跨过午夜（日期变了）时：所有页面的热力图窗口都会跟上新的一天。"""
        from datetime import date, timedelta

        from app import heatmap as heatmap_stats
        from app.models import today_str

        self.window.set_page("overview")
        self.app.processEvents()
        # 模拟"昨天打开、一直开着"：窗口停在昨天，已渲染记录也停在昨天
        yesterday = date.today() - timedelta(days=1)
        card = self.window.pages["overview"]._total_card
        card.calendar.set_window(heatmap_stats.build_calendar(end_date=yesterday))
        self.window._rendered_date = yesterday.isoformat()
        self.window._rendered.clear()

        self.window._sync_revision()
        self.app.processEvents()

        self.assertEqual(self.window._rendered_date, today_str())
        self.assertEqual(card.calendar.grid.end, date.today())
        # 其它页面也标记为待刷新：切过去时会重新画
        self.assertNotIn("heatmaps", self.window._rendered)

    def test_diary_page_has_open_file_button(self) -> None:
        """右下角的「打开日记本」：文件不存在就先生成，再交给系统打开。"""
        from app import diary as diary_store
        from app import paths
        from app.pages import diary as diary_page_module

        page = self.window.pages["diary"]
        self.window.set_page("diary")
        self.app.processEvents()
        if paths.DIARY_PATH.exists():
            paths.DIARY_PATH.unlink()

        opened: list[str] = []

        class FakeDesktopServices:
            @staticmethod
            def openUrl(url) -> bool:
                opened.append(url.toLocalFile())
                return True

        original = diary_page_module.QDesktopServices
        diary_page_module.QDesktopServices = FakeDesktopServices
        try:
            page._open_diary_file()
        finally:
            diary_page_module.QDesktopServices = original
        from pathlib import Path

        self.assertEqual(len(opened), 1)
        self.assertEqual(Path(opened[0]).resolve(), paths.DIARY_PATH.resolve())
        self.assertTrue(paths.DIARY_PATH.exists(), "点击后应该已经生成了日记文件")
        self.assertIn("# 日记", paths.DIARY_PATH.read_text(encoding="utf-8"))

        # 系统打不开时给出提示，而不是静默失败
        warned: list[str] = []
        original_warn = diary_page_module.warn

        class FailingDesktopServices:
            @staticmethod
            def openUrl(url) -> bool:
                return False

        diary_page_module.QDesktopServices = FailingDesktopServices
        diary_page_module.warn = lambda parent, title, text: warned.append(title)
        try:
            page._open_diary_file()
        finally:
            diary_page_module.QDesktopServices = original
            diary_page_module.warn = original_warn
        self.assertEqual(warned, ["打不开日记本"])
        _ = diary_store

    def test_diary_side_list_right_click_deletes_the_day(self) -> None:
        """「写过的日子」右键删除：Markdown 文件里那一天的内容同步删掉。"""
        from app import diary as diary_store
        from app import paths
        from app.pages import diary as diary_page_module

        diary_store.write_day("2026-10-13", "要删掉的一天", paths.DIARY_PATH)
        diary_store.write_day("2026-10-15", "留着的一天", paths.DIARY_PATH)

        page = self.window.pages["diary"]
        self.window.set_page("diary")
        self.app.processEvents()
        self.assertEqual(
            page.entry_list.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu
        )
        days_in_list = [
            page.entry_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(page.entry_list.count())
        ]
        self.assertIn("2026-10-13", days_in_list)

        # 右键菜单里就是删除这一天
        menu = page.build_entry_menu("2026-10-13")
        self.assertEqual(len(menu.actions()), 1)
        self.assertIn("2026-10-13", menu.actions()[0].text())

        original_confirm = diary_page_module.confirm
        diary_page_module.confirm = lambda *args, **kwargs: True
        try:
            page._delete_day("2026-10-13")
        finally:
            diary_page_module.confirm = original_confirm

        text = paths.DIARY_PATH.read_text(encoding="utf-8")
        self.assertNotIn("2026-10-13", text)
        self.assertIn("留着的一天", text)
        self.assertEqual(diary_store.read_day("2026-10-13"), "")
        days_in_list = [
            page.entry_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(page.entry_list.count())
        ]
        self.assertNotIn("2026-10-13", days_in_list)
        self.assertIn("2026-10-15", days_in_list)
        self.assertIn("已从日记文件里删除", page.status_label.text())

    def test_diary_deleting_the_day_you_are_writing_clears_the_editor(self) -> None:
        from app import diary as diary_store
        from app import paths
        from app.models import today_str
        from app.pages import diary as diary_page_module
        from app.widgets.common import str_to_qdate

        today = today_str()
        diary_store.write_day(today, "今天写的，等下删掉", paths.DIARY_PATH)
        page = self.window.pages["diary"]
        self.window.set_page("diary")
        page.date_edit.setDate(str_to_qdate(today))
        self.app.processEvents()
        self.assertEqual(page.editor.toPlainText(), "今天写的，等下删掉")

        original_confirm = diary_page_module.confirm
        diary_page_module.confirm = lambda *args, **kwargs: True
        try:
            page._delete_day(today)
        finally:
            diary_page_module.confirm = original_confirm

        self.assertEqual(page.editor.toPlainText(), "")
        self.assertEqual(diary_store.read_day(today), "")

    # ------------------------------------------------- 热力图页：一行一个节点
    def _heatmap_page(self):
        page = self.window.pages["heatmaps"]
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.window.show()
        self.window.set_page("heatmaps")
        self.app.processEvents()
        return page

    def test_clicking_a_row_opens_the_full_detail(self) -> None:
        """点某一行 → 打开那个节点的全屏大图（这里把弹窗换成假的，避免阻塞）。"""
        from PySide6.QtTest import QTest

        from app.pages import heatmaps as heatmaps_module

        page = self._heatmap_page()
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        opened: list[tuple[str, str]] = []

        class FakeDetail:
            def __init__(self, service, node, scope_kind, end_date, **kwargs):
                opened.append((scope_kind, node.id))

            def showMaximized(self) -> None:
                pass

            def exec(self) -> int:
                return 0

        original = heatmaps_module.HeatmapDetailDialog
        heatmaps_module.HeatmapDetailDialog = FakeDetail
        try:
            QTest.mouseClick(page._process_rows[explore.id], Qt.MouseButton.LeftButton)
        finally:
            heatmaps_module.HeatmapDetailDialog = original
        self.assertEqual(opened, [("process", explore.id)])

    def test_detail_dialog_is_bigger_and_can_open_a_day(self) -> None:
        """大图明显比迷你图大；点某一天，记录显示在大图下面的面板里（不再弹新窗口）。"""
        from app import clock
        from app.pages import heatmaps as heatmaps_module
        from app.pages.heatmaps import HeatmapDetailDialog

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        self.service.record_activity(english.id, "大图里的记录", "2026-10-15")

        page = self._heatmap_page()
        row = page._process_rows[explore.id]
        # 注意：这里不能把 self（TestCase）当回调传进去 —— 调用 TestCase 实例会再跑一遍测试。
        dialog = HeatmapDetailDialog(self.service, explore, "process", clock.today())
        self.assertGreater(dialog.calendar.width(), row.calendar.width())
        self.assertGreater(dialog.calendar.height(), row.calendar.height())
        self.assertIn("次活动", dialog.stats_label.text())

        # 还没点日子：面板处于提示状态
        self.assertIsNone(dialog.day_panel.day)
        self.assertEqual(dialog.day_panel.view.table.rowCount(), 0)

        # 点某一天：就地显示，绝不该再弹新窗口
        original = heatmaps_module.open_day_dialog
        heatmaps_module.open_day_dialog = lambda *a, **k: self.fail("不该再弹新窗口")
        try:
            dialog._on_day_clicked("2026-10-15")
        finally:
            heatmaps_module.open_day_dialog = original

        self.assertEqual(dialog.day_panel.day, "2026-10-15")
        table = dialog.day_panel.view.table
        self.assertEqual(table.rowCount(), 1)
        self.assertIn("2026-10-15", dialog.day_panel._title.text())
        cells = " ".join(
            table.item(0, column).text() for column in range(table.columnCount())
        )
        self.assertIn("大图里的记录", cells)

        # 换到没有记录的一天：面板跟着换
        dialog._on_day_clicked("2026-10-14")
        self.assertEqual(dialog.day_panel.view.table.rowCount(), 0)
        self.assertIn("没有记录", dialog.day_panel._title.text())

    def test_deleting_in_day_panel_refreshes_calendar(self) -> None:
        """在大图下面的面板里删掉记录：大图和热力图页当天都立刻变浅。"""
        from app import clock
        from app.pages.heatmaps import HeatmapDetailDialog
        from app.widgets import common as common_module

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        self.service.record_activity(english.id, "面板里删掉的", "2026-10-15")

        page = self._heatmap_page()
        # 传的正是 _open_detail 里会传的那个回调，所以这条链路和真实使用一致
        dialog = HeatmapDetailDialog(
            self.service,
            explore,
            "process",
            clock.today(),
            page._on_activities_changed,
        )
        dialog._on_day_clicked("2026-10-15")
        table = dialog.day_panel.view.table
        self.assertEqual(table.rowCount(), 1)

        dialog.day_panel.view._activity_at(
            table.visualItemRect(table.item(0, 0)).center()
        )
        original_confirm = common_module.confirm
        common_module.confirm = lambda *args, **kwargs: True
        try:
            dialog.day_panel.view._delete_selected()
        finally:
            common_module.confirm = original_confirm

        # 1) 面板自己变空；2) 大图当天变浅；3) 背后的热力图页同步
        self.assertEqual(dialog.day_panel.view.table.rowCount(), 0)
        self.assertIn("没有记录", dialog.day_panel._title.text())
        self.assertEqual(dialog.calendar.totals()[0], 0)
        self.assertEqual(page._process_rows[explore.id].calendar.totals()[0], 0)

    def test_area_row_collapses_and_expands_its_processes(self) -> None:
        """领域行的三角：折叠后它下面的进程行全部隐藏，再点一次回来。"""
        page = self._heatmap_page()
        area_id = self.service.areas()[0].id
        rows = page._rows_by_area[area_id]
        self.assertTrue(rows)
        self.assertTrue(all(row.isVisibleTo(page) for row in rows))

        page._toggle_area(area_id)
        self.assertTrue(all(not row.isVisibleTo(page) for row in rows))
        self.assertEqual(page._area_rows[area_id].toggle_button.text(), "▶")

        page._toggle_area(area_id)
        self.assertTrue(all(row.isVisibleTo(page) for row in rows))
        self.assertEqual(page._area_rows[area_id].toggle_button.text(), "▼")

    def test_collapse_all_hides_every_process_row(self) -> None:
        page = self._heatmap_page()
        page._set_all_expanded(False)
        self.assertTrue(
            all(not row.isVisibleTo(page) for row in page._process_rows.values())
        )
        page._set_all_expanded(True)
        self.assertTrue(
            all(row.isVisibleTo(page) for row in page._process_rows.values())
        )

    def test_row_calendar_keeps_full_size(self) -> None:
        """行内热力图保持原来的大小（格子 11px）—— 缩过一次，被要求改回来了。"""
        from app.widgets.heatmap_calendar import HeatmapCalendar

        page = self._heatmap_page()
        row = next(iter(page._process_rows.values()))
        default = HeatmapCalendar()
        self.assertEqual(row.calendar.width(), default.width())
        self.assertEqual(row.calendar.height(), default.height())
        self.assertGreater(row.calendar.width(), 700)

    def test_data_page_exports_one_nodes_activities(self) -> None:
        """数据页：选一个线程 → 导出它的活动记录 CSV（保存对话框换成固定路径）。"""
        from app.pages import data as data_module

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "导出用的一条", "2026-10-15")
        page = self.window.pages["data"]
        self.window.set_page("data")
        self.app.processEvents()
        page.node_export_combo.set_current_node(english.id)

        target = self.tmp / "exported.csv"
        original_dialog = data_module.QFileDialog.getSaveFileName
        original_info = data_module.info
        data_module.QFileDialog.getSaveFileName = staticmethod(
            lambda *args, **kwargs: (str(target), "CSV 文件 (*.csv)")
        )
        data_module.info = lambda *args, **kwargs: None
        try:
            page._export_node_activities()
        finally:
            data_module.QFileDialog.getSaveFileName = original_dialog
            data_module.info = original_info

        self.assertTrue(target.exists(), "应该写出 CSV 文件")
        text = target.read_text(encoding="utf-8-sig")
        self.assertIn("导出用的一条", text)
        self.assertIn(str(target), page.node_export_hint.text())

    def test_slogan_has_breathing_room_below_the_title(self) -> None:
        """标语不再贴着标题，往下挪了一截。"""
        from PySide6.QtWidgets import QLabel

        sidebar = self.window.sidebar
        self.window.show()
        self.app.processEvents()
        subtitle = sidebar.findChild(QLabel, "SidebarSubtitle")
        gap = (
            sidebar.slogan_label.mapTo(sidebar, QPoint(0, 0)).y()
            - subtitle.mapTo(sidebar, QPoint(0, 0)).y()
            - subtitle.height()
        )
        self.assertGreaterEqual(gap, 24, "标语与标题之间应该留出更多空白")

    def test_middle_click_on_card_opens_heatmap(self) -> None:
        """记录地图：中键单击卡片 → 打开它的大热力图；线程打开所属进程的。"""
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QGraphicsSceneMouseEvent

        from app.pages import mindmap as mindmap_module

        page = self.window.pages["mindmap"]
        self.window.set_page("mindmap")
        self.app.processEvents()
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        thread = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        area = self.service.node_by_name("领域 3", NodeType.AREA)

        opened: list[tuple[str, str]] = []

        class FakeDetail:
            def __init__(self, service, node, scope_kind, end_date, **kwargs):
                opened.append((scope_kind, node.id))

            def showMaximized(self) -> None:
                pass

            def exec(self) -> int:
                return 0

        original = mindmap_module.HeatmapDetailDialog
        mindmap_module.HeatmapDetailDialog = FakeDetail
        try:
            for node in (explore, thread, area):
                card = page.view._cards.get(node.id)
                self.assertIsNotNone(card, f"{node.name} 应该有卡片")
                event = QGraphicsSceneMouseEvent(QEvent.Type.GraphicsSceneMousePress)
                event.setButton(Qt.MouseButton.MiddleButton)
                card.mousePressEvent(event)
                self.assertTrue(
                    event.isAccepted(), "卡片要吃掉中键，别让视图去平移画布"
                )
            # 弹窗是推迟到事件处理结束后执行的：必须在还原成真控件「之前」跑事件循环，
            # 否则这里会弹出真的模态窗口，测试就卡死了。
            self.app.processEvents()
        finally:
            mindmap_module.HeatmapDetailDialog = original

        self.assertEqual(
            opened,
            [
                ("process", explore.id),
                ("thread", thread.id),  # 线程现在有自己的热力图了
                ("area", area.id),
            ],
        )

    def test_middle_click_defers_dialog_until_after_the_event(self) -> None:
        """中键弹窗必须推迟到事件处理结束后 —— 直接在 press 里 exec() 会卡住这次点击，
        手感上就变成"要点两下才开"（这是用户实测发现的问题）。"""
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QGraphicsSceneMouseEvent

        from app.pages import mindmap as mindmap_module

        page = self.window.pages["mindmap"]
        self.window.set_page("mindmap")
        self.app.processEvents()
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        opened: list[str] = []

        class FakeDetail:
            def __init__(self, service, node, scope_kind, end_date, **kwargs):
                opened.append(node.id)

            def showMaximized(self) -> None:
                pass

            def exec(self) -> int:
                return 0

        original = mindmap_module.HeatmapDetailDialog
        mindmap_module.HeatmapDetailDialog = FakeDetail
        try:
            card = page.view._cards[explore.id]
            event = QGraphicsSceneMouseEvent(QEvent.Type.GraphicsSceneMousePress)
            event.setButton(Qt.MouseButton.MiddleButton)
            card.mousePressEvent(event)
            self.assertEqual(
                opened, [], "不该在处理这次点击的过程中就把窗口弹出来"
            )
            self.app.processEvents()
        finally:
            mindmap_module.HeatmapDetailDialog = original
        self.assertEqual(opened, [explore.id], "事件处理结束后应该打开")

    def test_middle_click_through_the_view_opens_heatmap(self) -> None:
        """真实链路：中键点在卡片上（经过 QGraphicsView 转给图元）应该打开热力图。

        上面那条测试是直接调卡片的 mousePressEvent，绕过了 QGraphicsView ——
        所以它发现不了"视图把中键自己吃掉"这类问题。这条走完整的真实链路。
        """
        from PySide6.QtTest import QTest

        from app.pages import mindmap as mindmap_module

        page = self.window.pages["mindmap"]
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.window.show()
        self.window.set_page("mindmap")
        self.app.processEvents()
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        view = page.view
        # 这条测的是中键链路，不测缩放：先缩到全览，保证目标卡片落在视口内
        # （默认 150% 时它可能在屏幕外）。
        view.fit_to_window()
        self.app.processEvents()
        card = view._cards[explore.id]
        inside = view.mapFromScene(card.scenePos()) + QPoint(24, 22)
        self.assertTrue(view.viewport().rect().contains(inside), "卡片应该落在视口内")

        opened: list[str] = []

        class FakeDetail:
            def __init__(self, service, node, scope_kind, end_date, **kwargs):
                opened.append(node.id)

            def showMaximized(self) -> None:
                pass

            def exec(self) -> int:
                return 0

        original = mindmap_module.HeatmapDetailDialog
        mindmap_module.HeatmapDetailDialog = FakeDetail
        try:
            QTest.mouseClick(view.viewport(), Qt.MouseButton.MiddleButton, pos=inside)
            self.app.processEvents()
        finally:
            mindmap_module.HeatmapDetailDialog = original
        self.assertEqual(opened, [explore.id], "中键点在卡片上就应该打开热力图")

    def test_middle_click_on_empty_canvas_does_nothing(self) -> None:
        """中键不再用于拖动画布：点在空白处什么也不发生。"""
        from PySide6.QtTest import QTest

        from app.pages import mindmap as mindmap_module

        page = self.window.pages["mindmap"]
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.window.show()
        self.window.set_page("mindmap")
        self.app.processEvents()
        view = page.view
        empty = QPoint(6, 6)
        self.assertIsNone(view.card_at(empty), "这个点应该是空白处")

        opened: list[str] = []

        class FakeDetail:
            def __init__(self, service, node, scope_kind, end_date, **kwargs):
                opened.append(node.id)

            def showMaximized(self) -> None:
                pass

            def exec(self) -> int:
                return 0

        original = mindmap_module.HeatmapDetailDialog
        mindmap_module.HeatmapDetailDialog = FakeDetail
        try:
            QTest.mousePress(view.viewport(), Qt.MouseButton.MiddleButton, pos=empty)
            self.assertFalse(view._panning, "中键不再是拖动画布")
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.MiddleButton, pos=empty)
            self.app.processEvents()
        finally:
            mindmap_module.HeatmapDetailDialog = original
        self.assertEqual(opened, [])

    def test_right_click_menu_has_open_heatmap(self) -> None:
        """右键菜单里也有一项「看大热力图…」，点了会打开。"""
        from app.pages import mindmap as mindmap_module

        page = self.window.pages["mindmap"]
        self.window.set_page("mindmap")
        self.app.processEvents()
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        opened: list[tuple[str, str]] = []

        class FakeDetail:
            def __init__(self, service, node, scope_kind, end_date, **kwargs):
                opened.append((scope_kind, node.id))

            def showMaximized(self) -> None:
                pass

            def exec(self) -> int:
                return 0

        original = mindmap_module.HeatmapDetailDialog
        mindmap_module.HeatmapDetailDialog = FakeDetail
        try:
            menu = page.build_node_menu(explore)
            labels = [action.text() for action in menu.actions()]
            self.assertIn("看大热力图…", labels)
            for action in menu.actions():
                if action.text() == "看大热力图…":
                    action.trigger()
                    break
            self.app.processEvents()
        finally:
            mindmap_module.HeatmapDetailDialog = original
        self.assertEqual(opened, [("process", explore.id)])

    def test_left_click_still_records_after_middle_click_support(self) -> None:
        """加了中键之后，左键单击卡片仍然是「记录一次」。"""
        from app.pages import mindmap as mindmap_module

        page = self.window.pages["mindmap"]
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        calls: list[str] = []

        class FakeRecord:
            def __init__(self, *args, **kwargs):
                pass

            def exec(self) -> int:
                calls.append("opened")
                return 0

        original = mindmap_module.QuickRecordDialog
        mindmap_module.QuickRecordDialog = FakeRecord
        try:
            page.record_for_node(explore.id)
        finally:
            mindmap_module.QuickRecordDialog = original
        self.assertEqual(calls, ["opened"])

    def test_thread_heatmap_rows_stay_collapsed(self) -> None:
        """线程热力图默认收起；「全部展开」也不展开它 —— 只能点进程的三角。"""
        page = self._heatmap_page()
        area_id = self.service.areas()[0].id
        process = next(
            candidate
            for candidate in self.service.processes(area_id)
            if self.service.threads(candidate.id)
        )
        thread = self.service.threads(process.id)[0]
        thread_row = page._rows[thread.id]
        self.assertFalse(thread_row.isVisibleTo(page), "线程行默认应该是收起的")

        page._set_all_expanded(True)
        self.assertTrue(page._process_rows[process.id].isVisibleTo(page))
        self.assertFalse(
            thread_row.isVisibleTo(page), "「全部展开」不应该展开线程热力图"
        )

        page._toggle_process(process.id)
        self.assertTrue(thread_row.isVisibleTo(page), "点进程的三角才应该展开线程")
        self.assertEqual(page._process_rows[process.id].toggle_button.text(), "▼")

        page._toggle_process(process.id)
        self.assertFalse(thread_row.isVisibleTo(page))
        self.assertEqual(page._process_rows[process.id].toggle_button.text(), "▶")

    def test_new_thread_gets_a_heatmap_row(self) -> None:
        """新建线程之后，热力图页自动多出一行（默认收起）。"""
        page = self._heatmap_page()
        area_id = self.service.areas()[0].id
        process = self.service.processes(area_id)[0]
        thread = self.service.create_node(NodeType.THREAD, "刚建的线程", process.id)

        self.window.on_data_changed()
        self.app.processEvents()

        self.assertIn(thread.id, page._rows, "新线程应该自动有热力图行")
        row = page._rows[thread.id]
        self.assertFalse(row.isVisibleTo(page), "新线程那行也应该默认收起")
        page._toggle_process(process.id)
        self.assertTrue(row.isVisibleTo(page))
        self.assertEqual(row._name_label.text(), "刚建的线程")

    def test_thread_row_opens_its_own_heatmap(self) -> None:
        """点线程那一行 → 打开线程自己的大热力图（不再是所属进程的）。"""
        from PySide6.QtTest import QTest

        from app.pages import heatmaps as heatmaps_module

        page = self._heatmap_page()
        area_id = self.service.areas()[0].id
        process = next(
            candidate
            for candidate in self.service.processes(area_id)
            if self.service.threads(candidate.id)
        )
        thread = self.service.threads(process.id)[0]
        page._toggle_process(process.id)

        opened: list[tuple[str, str]] = []

        class FakeDetail:
            def __init__(self, service, node, scope_kind, end_date, **kwargs):
                opened.append((scope_kind, node.id))

            def showMaximized(self) -> None:
                pass

            def exec(self) -> int:
                return 0

        original = heatmaps_module.HeatmapDetailDialog
        heatmaps_module.HeatmapDetailDialog = FakeDetail
        try:
            QTest.mouseClick(page._rows[thread.id], Qt.MouseButton.LeftButton)
        finally:
            heatmaps_module.HeatmapDetailDialog = original
        self.assertEqual(opened, [("thread", thread.id)])

    def test_thread_heatmap_day_panel_shows_its_own_activities(self) -> None:
        """线程热力图的大图里点某一天：面板要列出这个线程当天的记录。

        回归：面板原来只分「领域 / 其它」两个分支，线程被当成进程去查 process_id，
        于是永远是空的。
        """
        from app import clock
        from app.pages.heatmaps import HeatmapDetailDialog

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "线程那天的记录", "2026-10-15")

        self._heatmap_page()
        dialog = HeatmapDetailDialog(self.service, english, "thread", clock.today())
        dialog._on_day_clicked("2026-10-15")
        table = dialog.day_panel.view.table
        self.assertEqual(table.rowCount(), 1, "线程热力图点某天应该显示当天记录")
        cells = " ".join(
            table.item(0, column).text() for column in range(table.columnCount())
        )
        self.assertIn("线程那天的记录", cells)

    def test_confirm_defaults_to_cancel_for_dangerous_actions(self) -> None:
        """危险确认框的默认按钮必须是「取消」：打开时焦点不能落在「确定」上，
        否则顺手一个回车就把东西删了。"""
        from PySide6.QtWidgets import QMessageBox

        from app.widgets import common as common_module

        seen: list[tuple[str, str]] = []
        original = QMessageBox.exec

        def fake_exec(self):
            default = self.defaultButton()
            escape = self.escapeButton()
            seen.append(
                (
                    default.text() if default is not None else "",
                    escape.text() if escape is not None else "",
                )
            )
            return 0

        QMessageBox.exec = fake_exec
        try:
            common_module.confirm(self.window, "删除", "确定要删除吗？", danger=True)
            common_module.confirm(self.window, "继续", "要继续吗？")
        finally:
            QMessageBox.exec = original

        self.assertEqual(seen[0], ("取消", "取消"), "危险操作应该默认到「取消」")
        self.assertEqual(seen[1], ("确定", "取消"), "普通确认框仍然是「确定」")

    def test_message_boxes_do_not_give_focus_to_buttons(self) -> None:
        """提示框 / 确认框打开时不能把焦点交给按钮 —— 那就是用户看到的"按钮被高亮"。"""
        from PySide6.QtWidgets import QAbstractButton, QApplication, QMessageBox

        from app.widgets import common as common_module

        policies: list[tuple[str, object]] = []
        original = QMessageBox.exec

        def fake_exec(self):
            self.show()
            QApplication.processEvents()
            for button in self.findChildren(QAbstractButton):
                policies.append((button.text(), button.focusPolicy()))
            self.hide()
            return 0

        QMessageBox.exec = fake_exec
        try:
            common_module.info(self.window, "标题", "内容")
            common_module.warn(self.window, "标题", "内容")
            common_module.confirm(self.window, "标题", "内容", danger=True)
        finally:
            QMessageBox.exec = original

        # 离屏环境下 focusWidget() 不可靠（窗口不会被激活），所以断言**焦点策略**：
        # 按钮拿不到焦点，就不可能被高亮 —— 这是确定性的。
        self.assertTrue(policies, "提示框里应该有按钮")
        for text, policy in policies:
            self.assertEqual(
                policy,
                Qt.FocusPolicy.NoFocus,
                f"「{text}」按钮还能抢焦点（打开时会一闪一闪，像是光标跳过去了）",
            )

    def test_custom_dialogs_do_not_focus_buttons(self) -> None:
        """自定义对话框（记录窗 / 当天记录清单 / 大热力图 / 已删除清单）打开时，
        焦点都不该落在按钮上 —— 那才是"光标跳到按钮上"的真正来源。"""
        from PySide6.QtWidgets import QAbstractButton

        from app import clock
        from app.pages.heatmaps import HeatmapDetailDialog
        from app.widgets.common import ActivityListDialog, QuickRecordDialog
        from app.widgets.deleted_nodes import DeletedNodesDialog

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        explore = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        activity = self.service.record_activity(english.id, "焦点测试", "2026-10-15")

        dialogs = [
            QuickRecordDialog(
                self.service, self.window, preset_node_id=explore.id, lock_node=True
            ),
            ActivityListDialog(
                self.service,
                self.window,
                title="标题",
                subtitle="副标题",
                loader=lambda: [activity],
            ),
            HeatmapDetailDialog(self.service, explore, "process", clock.today()),
            DeletedNodesDialog(self.service, self.window),
        ]
        try:
            for dialog in dialogs:
                dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
                dialog.show()
                self.app.processEvents()
                # 注意：离屏环境下窗口不会被真正激活，focusWidget() 拿不到可靠结果
                # （早先这条断言就是这么"假装通过"的）。改成断言**按钮的焦点策略**：
                # 按钮拿不到焦点，就不可能被高亮 —— 这是确定性的。
                buttons = dialog.findChildren(QAbstractButton)
                self.assertTrue(buttons, f"{type(dialog).__name__} 应该有按钮")
                for button in buttons:
                    self.assertEqual(
                        button.focusPolicy(),
                        Qt.FocusPolicy.NoFocus,
                        f"{type(dialog).__name__} 里的「{button.text()}」还能抢焦点",
                    )
                dialog.close()
                self.app.processEvents()
        finally:
            for dialog in dialogs:
                dialog.deleteLater()
            self.app.processEvents()

    def test_theme_switch_changes_colors_and_can_go_back(self) -> None:
        """切主题会换掉配色；切回来必须完全复原（浅色不能被改坏）。"""
        from app import theme

        self.addCleanup(theme.apply_theme, theme.current_theme())
        before = (theme.PAGE_BG, theme.TEXT, theme.LEVEL_COLORS[0])

        theme.apply_theme("dark")
        self.assertEqual(theme.current_theme(), "dark")
        self.assertNotEqual(theme.PAGE_BG, before[0])
        self.assertIn(f"background: {theme.PAGE_BG}", theme.stylesheet())
        self.assertNotIn(f"background: {before[0]}", theme.stylesheet())

        theme.apply_theme("light")
        self.assertEqual((theme.PAGE_BG, theme.TEXT, theme.LEVEL_COLORS[0]), before)

    def test_unknown_theme_name_falls_back_to_light(self) -> None:
        from app import theme

        self.addCleanup(theme.apply_theme, theme.current_theme())
        theme.apply_theme("深色")
        self.assertEqual(theme.current_theme(), "light")

    def test_switching_theme_from_sidebar_saves_config(self) -> None:
        """侧栏切主题：写进配置并请求重建窗口（重建由 main.py 的循环完成）。"""
        self.window.sidebar.themeRequested.emit("dark")
        self.app.processEvents()
        self.assertEqual(self.config.get("theme"), "dark")
        self.assertTrue(self.window.restart_requested)

    def test_sidebar_has_theme_segment(self) -> None:
        from app import theme

        sidebar = self.window.sidebar
        self.assertEqual(set(sidebar.theme_buttons), {"light", "dark"})
        self.assertTrue(sidebar.theme_buttons[theme.current_theme()].isChecked())

    def test_mindmap_page_is_labelled_thought_map(self) -> None:
        """侧栏和页面标题都叫「思维导图」（快捷键 Ctrl+3 不变）。"""
        button = self.window.sidebar.buttons["mindmap"]
        self.assertEqual(button.label.text(), "思维导图")
        self.assertEqual(button.shortcut_text, "Ctrl+3")
        self.window.set_page("mindmap")
        self.app.processEvents()
        self.assertEqual(self.window.title_label.text(), "思维导图")

    def test_mindmap_shows_current_zoom_percentage(self) -> None:
        """工具栏上的百分比跟着缩放实时变化，数值就是当前实际缩放。"""
        page = self.window.pages["mindmap"]
        self.window.set_page("mindmap")
        self.app.processEvents()

        start = page.view.zoom_percent()
        self.assertEqual(page.zoom_label.text(), f"{start}%")

        page.view.zoom_by(1.15)
        self.app.processEvents()
        self.assertGreater(page.view.zoom_percent(), start)
        self.assertEqual(page.zoom_label.text(), f"{page.view.zoom_percent()}%")

        page._fit()
        self.app.processEvents()
        self.assertEqual(page.zoom_label.text(), f"{page.view.zoom_percent()}%")

    def test_mindmap_opens_at_150_percent(self) -> None:
        """重启应用后思维导图默认按 150% 打开（不记住上次的缩放）。"""
        page = self.window.pages["mindmap"]
        self.assertEqual(page.view.zoom_percent(), 150)
        self.assertEqual(page.zoom_label.text(), "150%")

    def test_activity_rows_mark_the_first_row_of_each_day(self) -> None:
        """活动记录：每天的第一行被标记（用来画分割线），同一天的其它行不标记。"""
        from app.widgets.common import NEW_DAY_ROLE

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "A", "2026-10-13")
        self.service.record_activity(english.id, "B", "2026-10-13")
        self.service.record_activity(english.id, "C", "2026-10-14")

        page = self.window.pages["activity"]
        self.window.set_page("activity")
        self.app.processEvents()
        flags = [
            bool(page.table.item(row, 0).data(NEW_DAY_ROLE))
            for row in range(page.table.rowCount())
        ]
        # 倒序排列：第一行是 10-14（上面是表头，不画线）；第二行是 10-13 的第一条 → 画线；
        # 第三条仍是 10-13 → 不画
        self.assertEqual(flags, [False, True, False])
        # 用"间距"分开：每天第一行更高，同一天里保持紧凑
        heights = [page.table.rowHeight(row) for row in range(page.table.rowCount())]
        self.assertGreater(heights[1], heights[0])
        self.assertGreater(heights[1], heights[2])

    def test_day_row_keeps_the_same_line_spacing(self) -> None:
        """每天第一行与当天第二条的间距，必须和普通行距完全一致。

        "每天第一行"这块改过三版：居中加高、贴底加高都不行——
        多出来的高度会被字体度量吃掉，那一行不是离上面的天更远，
        就是和当天的第二条贴得更近。现在整格下移，空隙只留在交界处。
        """
        from PySide6.QtGui import QImage

        def text_bands(image: QImage) -> list[tuple[int, int]]:
            bands: list[tuple[int, int]] = []
            start: int | None = None
            for y in range(image.height()):
                dark = sum(
                    1
                    for x in range(0, image.width(), 2)
                    if image.pixelColor(x, y).value() < 160
                )
                on = dark > 3
                if on and start is None:
                    start = y
                elif not on and start is not None:
                    bands.append((start, y - 1))
                    start = None
            if start is not None:
                bands.append((start, image.height() - 1))
            return bands

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        for note, day in (
            ("A", "2026-10-14"),
            ("B", "2026-10-13"),
            ("C", "2026-10-13"),
            ("D", "2026-10-13"),
        ):
            self.service.record_activity(english.id, note, day)

        page = self.window.pages["activity"]
        self.window.set_page("activity")
        self.app.processEvents()
        page.table.clearSelection()
        self.app.processEvents()

        bands = text_bands(page.table.viewport().grab().toImage())
        self.assertEqual(len(bands), 4, "没找到 4 行文字")
        gaps = [bands[i][0] - bands[i - 1][1] for i in range(1, 4)]
        self.assertGreaterEqual(gaps[0], gaps[2] + 6, "两天的交界处没有拉开距离")
        self.assertLessEqual(
            abs(gaps[1] - gaps[2]),
            1,
            f"每天第一行与当天第二条的间距（{gaps[1]}）和普通行距（{gaps[2]}）不一致",
        )

    def test_activity_days_stay_together_with_backdated_records(self) -> None:
        """补记（活动日期早于记录时间）不能把同一天拆成两段。"""
        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        for note, day in (
            ("A", "2026-10-14"),
            ("B", "2026-10-13"),
            ("C", "2026-10-15"),
            ("D", "2026-10-13"),
        ):
            self.service.record_activity(english.id, note, day)

        page = self.window.pages["activity"]
        self.window.set_page("activity")
        self.app.processEvents()

        days = [page.table.item(row, 0).text() for row in range(page.table.rowCount())]
        self.assertEqual(days, ["2026-10-15", "2026-10-14", "2026-10-13", "2026-10-13"])
        day_starts = sum(1 for index, day in enumerate(days) if index == 0 or days[index - 1] != day)
        self.assertEqual(day_starts, len(set(days)), "同一天被拆成了多段")

    def test_each_day_is_separated_by_a_drawn_line(self) -> None:
        """天与天之间真的画了一条线（不是只靠行高，行高会被文字对齐方式吃掉）。"""
        from app.widgets.common import ActivityTable, NEW_DAY_ROLE

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        self.service.record_activity(english.id, "A", "2026-10-13")
        self.service.record_activity(english.id, "B", "2026-10-14")

        page = self.window.pages["activity"]
        self.window.set_page("activity")
        self.app.processEvents()
        table = page.table

        image = table.viewport().grab().toImage()
        day_row = 1  # 倒序：第一行是 10-14，第二行是 10-13 的第一条
        self.assertTrue(table.item(day_row, 0).data(NEW_DAY_ROLE))
        y = table.rowViewportPosition(day_row) + ActivityTable.DIVIDER_OFFSET
        dark = sum(
            1
            for x in range(ActivityTable.DIVIDER_INSET + 4, image.width() - ActivityTable.DIVIDER_INSET - 4)
            if image.pixelColor(x, y).value() < 250
        )
        span = image.width() - 2 * (ActivityTable.DIVIDER_INSET + 4)
        self.assertGreater(dark / span, 0.9, "这一行没有画出分隔线")

    def test_selected_day_row_keeps_the_gap_unselected(self) -> None:
        """选中"每天第一行"时，它上方那段空隙不能被一起涂成选中色。"""
        from PySide6.QtGui import QColor

        from app import theme
        from app.widgets.common import ActivityTable

        english = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        for note, day in (
            ("A", "2026-10-14"),
            ("B", "2026-10-13"),
            ("C", "2026-10-13"),
        ):
            self.service.record_activity(english.id, note, day)

        page = self.window.pages["activity"]
        self.window.set_page("activity")
        self.app.processEvents()
        table = page.table
        table.selectRow(1)  # 10-13 的第一条（每天第一行）
        self.app.processEvents()

        image = table.viewport().grab().toImage()
        top = table.rowViewportPosition(1)
        x = image.width() - 26  # 右侧空白处，不会被文字干扰
        gap_pixel = image.pixelColor(x, top + 3)
        row_pixel = image.pixelColor(x, top + ActivityTable.DAY_ROW_HEIGHT - 4)
        self.assertEqual(gap_pixel.name(), QColor(theme.BG).name(), "空隙跟着选中一起变色了")
        self.assertNotEqual(
            row_pixel.name(), gap_pixel.name(), "选中色没有落在文字那一行"
        )

    def test_heatmap_notes_match_the_title_and_work_on_every_level(self) -> None:
        """注释的字号/颜色跟同一行的标题一致；领域、进程、线程都能写。"""
        area = self.service.node_by_name("领域 3", NodeType.AREA)
        process = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        thread = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        for node, text in (
            (area, "领域注释"),
            (process, "进程注释"),
            (thread, "线程注释"),
        ):
            self.service.set_description(node.id, text)

        page = self.window.pages["heatmaps"]
        self.window.set_page("heatmaps")
        self.app.processEvents()

        for node, text in (
            (area, "领域注释"),
            (process, "进程注释"),
            (thread, "线程注释"),
        ):
            row = page._rows[node.id]
            self.assertEqual(row.note, text)
            self.assertEqual(row._note_label.styleSheet(), row._name_label.styleSheet())
            self.assertTrue(row._note_label.isVisibleTo(row))

    def test_paused_nodes_are_hidden_from_heatmaps(self) -> None:
        """进程树里设成「已暂停」的进程 / 线程，不在热力图页出现。"""
        process = self.service.node_by_name("进程 3-3", NodeType.PROCESS)
        thread = self.service.node_by_name("线程 3-3-2", NodeType.THREAD)
        page = self.window.pages["heatmaps"]
        self.window.set_page("heatmaps")
        self.app.processEvents()
        self.assertIn(process.id, page._rows)
        self.assertIn(thread.id, page._rows)

        self.service.set_status(process.id, Status.PAUSED)
        self.service.set_status(thread.id, Status.PAUSED)
        page.refresh()
        self.app.processEvents()
        self.assertNotIn(process.id, page._rows)
        self.assertNotIn(thread.id, page._rows)

        self.service.set_status(process.id, Status.DOING)
        self.service.set_status(thread.id, Status.DOING)
        page.refresh()
        self.app.processEvents()
        self.assertIn(process.id, page._rows)
        self.assertIn(thread.id, page._rows)

    def test_global_heatmap_has_two_five_level_scales(self) -> None:
        """总热力图有两套 1–5 级：1–5 绿色，6–10 橙色；其它热力图不受影响。"""
        self.assertEqual(
            [heatmap_stats.level_for_global(n) for n in (0, 1, 5, 6, 10, 15)],
            [0, 1, 5, 6, 10, 10],
        )
        self.assertEqual(len(heatmap_stats.GREEN_SCALE), 5)
        self.assertEqual(len(heatmap_stats.ORANGE_SCALE), 5)
        self.assertEqual(len(heatmap_stats.LEVEL_COLORS_GLOBAL), 11)
        self.assertNotIn(
            heatmap_stats.ORANGE_SCALE[0], heatmap_stats.LEVEL_COLORS
        )

        page = self.window.pages["overview"]
        self.window.set_page("overview")
        self.app.processEvents()
        self.assertTrue(page._total_card.calendar._hot)
        if page._area_cards:
            self.assertFalse(page._area_cards[0].calendar._hot)

        from app.widgets.heatmap_calendar import HeatmapLegend

        legend = page._total_card.findChild(HeatmapLegend)
        self.assertIsNotNone(legend)
        self.assertTrue(legend._hot)
        single = HeatmapLegend(scale=legend._scale)
        self.assertFalse(single._hot)
        self.assertGreater(legend.height(), single.height(), "两行图例应该比单行更高")

    def test_sidebar_puts_ai_under_overview_and_settings_at_bottom(self) -> None:
        """左栏：AI 紧跟在总览下面；设置在最底部（数据之后）。"""
        from app.widgets.sidebar import ALL_NAV_ITEMS, BOTTOM_ITEMS, NAV_ITEMS

        keys = [key for key, _label, _shortcut in NAV_ITEMS]
        self.assertEqual(keys[:2], ["overview", "ai"])
        self.assertEqual(BOTTOM_ITEMS[-1][0], "settings")
        self.assertEqual(ALL_NAV_ITEMS[-1][0], "settings")
        for key in ("ai", "settings"):
            self.assertIn(key, self.window.pages)

    def test_ai_page_stays_offline_until_configured(self) -> None:
        """没有 Key / 没启用时页面仍然可以点；发送会提示先去设置，且不写任何数据。"""
        from app import ai
        from app.pages.ai import AIPage

        page = AIPage(self.service, ai_config=ai.AIConfig(self.tmp / "ai.json"))
        self.assertFalse(page.ai_config.ready)
        self.assertTrue(page.input.isEnabled(), "没配置也要能点，不能像死掉一样")
        self.assertTrue(page.send_button.isEnabled())
        self.assertIn("设置", page.notice.text())

        page.input.setPlainText("测试一下")
        page.send()
        self.assertEqual(self.service.ai_conversations(), [])
        self.assertIn("设置", page.status.text())

        page.ai_config.set("enabled", True)
        page.ai_config.set("api_key", "sk-test")
        page.reload_config()
        self.assertTrue(page.input.isEnabled())

    def test_settings_page_saves_ai_config_to_its_own_file(self) -> None:
        """设置页把 Key 存进 ai.json（测试里换成临时路径），并发出 saved。"""
        from app import ai
        from app.pages.settings import SettingsPage

        config = ai.AIConfig(self.tmp / "ai.json")
        page = SettingsPage(self.config, ai_config=config)
        saved: list[int] = []
        page.saved.connect(lambda: saved.append(1))

        page.enabled_box.setChecked(True)
        page.base_url.setText("https://api.deepseek.com")
        page.model.setText("deepseek-chat")
        page.api_key.setText("sk-test-123")
        page.temperature.setValue(0.3)
        page.system_prompt.setPlainText("简洁回答")
        page.save()
        self.app.processEvents()

        self.assertEqual(saved, [1])
        stored = ai.AIConfig(self.tmp / "ai.json")
        self.assertEqual(stored.get("api_key"), "sk-test-123")
        self.assertEqual(stored.get("model"), "deepseek-chat")
        self.assertAlmostEqual(float(stored.get("temperature")), 0.3)
        self.assertTrue(stored.get("enabled"))
        # 开关关闭时字段也要能编辑（以前整块表单被禁用，Key 根本填不进去）
        page.enabled_box.setChecked(False)
        self.assertTrue(page.api_key.isEnabled())
        self.assertTrue(page.base_url.isEnabled())

    def test_ai_worker_reports_reply_and_error_without_network(self) -> None:
        """后台 worker 只发信号：成功回文本，失败回可读错误。"""
        from app import ai
        from app.widgets.ai_worker import ChatWorker

        config = ai.AIConfig(self.tmp / "ai.json")
        config.set("enabled", True)
        config.set("api_key", "sk-test")

        original = ai.chat
        try:
            ai.chat = lambda *args, **kwargs: "测试回复"
            replies: list[str] = []
            worker = ChatWorker(config, [{"role": "user", "content": "hi"}])
            worker.signals.done.connect(replies.append)
            worker.run()
            self.assertEqual(replies, ["测试回复"])

            def boom(*args, **kwargs):
                raise ai.AIError("网络不通：测试")

            ai.chat = boom
            errors: list[str] = []
            worker = ChatWorker(config, [{"role": "user", "content": "hi"}])
            worker.signals.failed.connect(errors.append)
            worker.run()
            self.assertEqual(errors, ["网络不通：测试"])
        finally:
            ai.chat = original

    def test_ai_messages_sit_in_a_centered_column(self) -> None:
        """对话栏水平居中且限宽，消息不再贴着窗口两边。"""
        page = self.window.pages["ai"]
        self.window.resize(1400, 900)
        self.window.show()
        self.window.set_page("ai")
        for _ in range(10):
            self.app.processEvents()

        holder = page.scroll.widget()
        column = page.messages_column
        expected = (holder.width() - column.width()) / 2
        self.assertLess(abs(column.x() - expected), 4, "对话栏没有居中")
        self.assertLessEqual(column.width(), 860, "对话栏太宽，会贴着两边")

    def test_ai_messages_render_markdown(self) -> None:
        """AI 回复按 Markdown 渲染：标题、列表、粗体、代码都不再显示原始符号。"""
        from app.pages.ai import MarkdownView, MessageBubble

        bubble = MessageBubble("assistant", "## 标题\n\n- 一\n- 二\n\n**粗体** 和 `code`")
        view = bubble.findChild(MarkdownView)
        self.assertIsNotNone(view)
        text = view.document().toPlainText()
        self.assertIn("标题", text)
        self.assertIn("粗体", text)
        self.assertIn("code", text)
        for token in ("##", "**", "`"):
            self.assertNotIn(token, text, "Markdown 符号没有被解析掉")


if __name__ == "__main__":
    unittest.main()
