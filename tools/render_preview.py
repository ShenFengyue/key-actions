# -*- coding: utf-8 -*-
"""开发用预览渲染：用临时数据库把五个页面导出为 PNG。

用途：在不接触真实数据的前提下检查界面与热力图外观。
运行方式：<项目>/.venv/Scripts/python.exe tools/render_preview.py
输出目录：<项目>/.preview/
"""

from __future__ import annotations

import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import time  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import clock, paths, theme  # noqa: E402
from app.appconfig import AppConfig  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.models import NodeType, Status  # noqa: E402
from app.services import LifeManagerService  # noqa: E402
from app.storage import Database  # noqa: E402
from tests._support import make_temp_dir  # noqa: E402

OUTPUT_DIR = ROOT / ".preview"

SAMPLE_NOTES = {
    "线程 3-3-2": ["推进了一小步", "完成一次练习", "整理了一条笔记"],
    "线程 3-3-1": ["查了资料", "列了一份清单", "对比了两个方案"],
    "线程 3-1-1": ["搭好了骨架", "修掉一个问题", "写了两条测试"],
    "线程 1-2-1": ["整理了一下结构", "做了一个原型", "沟通了一次"],
    "线程 2-1-1": ["确认了方向", "准备齐了材料", "整理了一套方案"],
    "线程 2-1-2": ["完成了一次练习", "记录了一下数据", "持续了 40 分钟"],
    "进程 1-1": ["推进了一个节点", "完成一次复盘"],
    "进程 1-3": ["写了一份草稿", "整理了素材"],
    "进程 1-4": ["梳理了一个假设", "聊了 30 分钟"],
    "进程 2-2": ["推进了一小步", "完成一次复盘"],
    "进程 3-2": ["整理了一份计划", "推进了一小步"],
    "进程 3-1": ["读了一章", "整理了一次笔记"],
}


def build_demo_service() -> tuple[Database, LifeManagerService]:
    tmp = make_temp_dir()
    saved = {"BACKUP_DIR": paths.BACKUP_DIR, "EXPORT_DIR": paths.EXPORT_DIR}
    paths.BACKUP_DIR = tmp / "backups"
    paths.EXPORT_DIR = tmp / "exports"
    paths.DIARY_PATH = tmp / "diary.md"
    _ = saved  # 预览进程结束后自动清理，无需还原（独立进程）
    db = Database(tmp / "preview.sqlite3")
    db.initialize()
    service = LifeManagerService(db)

    random.seed(20261015)
    today = clock.today()
    for node in service.all_nodes():
        if node.type is not NodeType.THREAD:
            continue
        notes = SAMPLE_NOTES.get(node.name, ["推进一次"])
        for offset in range(0, 190):
            day = today - timedelta(days=offset)
            if random.random() > 0.34:
                continue
            for _ in range(random.choice((1, 1, 1, 2, 2, 3))):
                service.record_activity(node.id, random.choice(notes), day.isoformat())

    # 演示一次归属变更：线程 3-3-2 从「领域 3 → 进程 3-3」移动到「领域 1 → 进程 1-4」
    english = service.node_by_name("线程 3-3-2", NodeType.THREAD)
    startup = service.node_by_name("进程 1-4", NodeType.PROCESS)
    service.move_node(english.id, startup.id)
    for offset in range(0, 20):
        day = today - timedelta(days=offset)
        if random.random() < 0.6:
            service.record_activity(english.id, random.choice(SAMPLE_NOTES["线程 3-3-2"]), day.isoformat())

    for name, action in (
        ("线程 3-3-2", "保持每周三次"),
        ("线程 3-1-1", "把当前这块做完"),
        ("线程 2-1-2", "本周完成三次"),
    ):
        node = service.node_by_name(name, NodeType.THREAD)
        if node is not None:
            service.set_next_action(node.id, action)
            service.set_pinned(node.id, True)
    service.set_status(service.node_by_name("线程 3-3-1", NodeType.THREAD).id, Status.PAUSED)

    # 进程注释：演示「有注释才显示小字」
    for name, note in (
        ("进程 3-3", "近期重点在这里，保持节奏"),
        ("进程 2-1", "每周两次，记录就好"),
    ):
        node = service.node_by_name(name, NodeType.PROCESS)
        if node is not None:
            service.set_description(node.id, note)

    # 演示「删除」：进程 3-2移入已删除清单，数据保留（热力图里仍能看到）
    travel = service.node_by_name("进程 3-2", NodeType.PROCESS)
    service.delete_node(travel.id)

    # 演示休息日：最近几天里挑三天标记，总热力图上应是粉色
    for offset in (0, 3, 9):
        service.mark_rest_day((today - timedelta(days=offset)).isoformat())

    # 演示日记：写两天的内容进同一个 Markdown 文件
    from app import diary as diary_store

    # 演示标语：改过两次，「数据」页面能看到历史
    service.set_slogan("先把今天过明白")
    service.set_slogan("一次只推进一件事")

    diary_store.write_day(
        today.isoformat(),
        "今天把界面调顺了一些。\n\n晚上出去走了走。",
    )
    diary_store.write_day(
        (today - timedelta(days=2)).isoformat(), "补写的：前两天整理了一下计划。"
    )
    return db, service


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    db, service = build_demo_service()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(theme.palette())
    app.setFont(theme.app_font())
    app.setStyleSheet(theme.stylesheet())
    if not QFontDatabase.families():
        print("警告：当前平台插件没有可用字体，文字会渲染成方块。")
    config = AppConfig(OUTPUT_DIR / "preview-config.json")
    window = MainWindow(service, config)
    window.resize(1560, 980)
    # 使用真实平台插件真正显示窗口后再抓取，保证控件全部完成绘制（含下拉控件与文字）。
    window.show()
    _settle(app)

    shots = (
        ("overview", 1560, 980),
        ("ai", 1560, 980),
        ("mindmap", 1560, 1180),
        ("processes", 1560, 980),
        ("heatmaps", 1560, 1180),
        ("activity", 1560, 980),
        ("energy", 1560, 980),
        ("diary", 1560, 980),
        ("data", 1560, 980),
        ("settings", 1560, 980),
    )
    for key, width, height in shots:
        window.resize(width, height)
        window.set_page(key)
        _settle(app)
        target = OUTPUT_DIR / f"{key}.png"
        window.grab().save(str(target))
        print(f"已渲染 {key} → {target}")

    # 数据页滚到底部再抓一张：新增的「导出活动记录」在页面下半部分
    from PySide6.QtWidgets import QScrollArea

    window.resize(1560, 980)
    window.set_page("data")
    _settle(app)
    scroll = window.pages["data"].findChild(QScrollArea)
    if scroll is not None:
        bar = scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        _settle(app)
        window.grab().save(str(OUTPUT_DIR / "data-bottom.png"))
        print(f"已渲染 data-bottom → {OUTPUT_DIR / 'data-bottom.png'}")

    # 热力图页：展开一个有线程的进程，看线程热力图那一行长什么样
    heatmaps_page = window.pages["heatmaps"]
    window.set_page("heatmaps")
    _settle(app)
    for process_id, thread_rows in heatmaps_page._thread_rows.items():
        if thread_rows:
            heatmaps_page._toggle_process(process_id)
            break
    _settle(app)
    window.grab().save(str(OUTPUT_DIR / "heatmaps-threads.png"))
    print(f"已渲染 heatmaps-threads → {OUTPUT_DIR / 'heatmaps-threads.png'}")

    from app.widgets.deleted_nodes import DeletedNodesDialog

    dialog = DeletedNodesDialog(service, None)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dialog.show()
    _settle(app)
    dialog.grab().save(str(OUTPUT_DIR / "deleted.png"))
    print(f"已渲染 deleted → {OUTPUT_DIR / 'deleted.png'}")

    # 能量分配：编辑一个能量池的对话框（占比 / 颜色 / 删除 / 档案入口）
    from app.widgets.energy_dialogs import PoolDialog

    energy_page = window.pages["energy"]
    pool_dialog = PoolDialog(energy_page.manager, energy_page.manager.pools()[0], 6, None)
    pool_dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    pool_dialog.show()
    _settle(app)
    pool_dialog.grab().save(str(OUTPUT_DIR / "energy-pool-dialog.png"))
    print(f"已渲染 energy-pool-dialog → {OUTPUT_DIR / 'energy-pool-dialog.png'}")

    # 快速记录（Ctrl+N）：两段式，各抓一张
    from app.widgets.quick_capture import QuickCaptureDialog

    quick = QuickCaptureDialog(service, None)
    quick.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    quick.show()
    quick.input.setText("线程 3-3")
    _settle(app)
    quick.grab().save(str(OUTPUT_DIR / "quick-capture-match.png"))
    print(f"已渲染 quick-capture-match → {OUTPUT_DIR / 'quick-capture-match.png'}")

    quick.input.returnPressed.emit()
    quick.input.setText("推进了一小步")
    _settle(app)
    quick.grab().save(str(OUTPUT_DIR / "quick-capture-note.png"))
    print(f"已渲染 quick-capture-note → {OUTPUT_DIR / 'quick-capture-note.png'}")

    # 快速记录输入「休息」：第一行应是休息日选项
    rest_dialog = QuickCaptureDialog(service, None)
    rest_dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    rest_dialog.show()
    rest_dialog.input.setText("休息")
    _settle(app)
    rest_dialog.grab().save(str(OUTPUT_DIR / "quick-capture-rest.png"))
    print(f"已渲染 quick-capture-rest → {OUTPUT_DIR / 'quick-capture-rest.png'}")

    # 热力图页点开的全屏大图
    from app.pages.heatmaps import HeatmapDetailDialog, scope_data

    process = service.node_by_name("进程 3-3", NodeType.PROCESS)
    detail = HeatmapDetailDialog(service, process, "process", clock.today())
    detail.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    detail.resize(1780, 840)
    detail.show()
    _settle(app)
    # 点一个记录最多的日子：展示"大图 + 下方当天记录"的样子
    counts, _summaries = scope_data(service, "process", process.id, detail.calendar.grid)
    if counts:
        detail._on_day_clicked(max(counts, key=lambda day: counts[day]))
    _settle(app)
    detail.grab().save(str(OUTPUT_DIR / "heatmap-detail.png"))
    print(f"已渲染 heatmap-detail → {OUTPUT_DIR / 'heatmap-detail.png'}")

    # 深色模式：换一套配色、用新配色重建窗口，把主要页面再抓一遍，方便逐页核对显示问题
    theme.apply_theme("dark")
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.stylesheet())
    light_window = window
    window = MainWindow(service, config)
    window.resize(1560, 980)
    window.show()
    _settle(app)
    for key in (
        "overview", "ai", "heatmaps", "processes", "activity",
        "energy", "mindmap", "diary", "data", "settings",
    ):
        window.set_page(key)
        _settle(app)
        target = OUTPUT_DIR / f"dark-{key}.png"
        window.grab().save(str(target))
        print(f"已渲染 dark-{key} → {target}")
    light_window.close()

    db.close()
    return 0


def _settle(app: QApplication, seconds: float = 0.6) -> None:
    """给 Qt 一点真实时间完成布局与绘制。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)


if __name__ == "__main__":
    raise SystemExit(main())
