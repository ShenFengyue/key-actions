"""页面 C：热力图 Heatmaps。

一行一个节点：左边是领域色条 + 名字 + 一句统计，右边是这张节点的热力图。
点这一行打开**全屏大图**；在大图里点某一天的色块，就能看到那天的全部活动记录。
所有热力图都按活动发生时的归属快照统计（关联不参与统计）。
领域 → 进程 → 线程 三层：**线程默认收起**，只能点进程左边的三角展开。
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import clock, heatmap as heatmap_stats, theme
from ..models import Node, NodeType, Status
from ..services import LifeManagerService, ValidationError
from ..widgets.common import (
    AppDialog,
    ActivityListView,
    Muted,
    SectionHeader,
    Subtle,
    clear_layout,
    open_activity_dialog,
    warn,
)
from ..widgets.heatmap_calendar import HeatmapCalendar, HeatmapLegend

# ------------------------------------------------------------------ 尺寸
# 行内热力图保持和旧版一样大（格子 11px）——只是把"一张图占一屏"改成了"一行一张图"。
ROW_CALENDAR_SCALE = 1.0
AREA_ROW_HEIGHT = 130
PROCESS_ROW_HEIGHT = 124
PROCESS_INDENT = 24  # 进程行相对领域行的缩进
THREAD_INDENT = 24  # 线程行再往里缩一层
LABEL_WIDTH = 240  # 名字与统计那一列的固定宽度，保证热力图左边缘对齐
AREA_GAP = 14  # 领域之间的留白
DETAIL_SCALE_MIN = 1.2
DETAIL_SCALE_MAX = 2.4
BASE_CALENDAR_WIDTH = heatmap_stats.DEFAULT_WEEKS * (HeatmapCalendar.CELL + HeatmapCalendar.GAP) + 30


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("HeatmapDivider")
    line.setFixedHeight(1)
    return line


def level_histogram(counts: dict[str, int]) -> dict[int, int]:
    """每一天的档位分布：几档各有多少天。"""
    histogram: dict[int, int] = {}
    for value in counts.values():
        level = heatmap_stats.level_for(value)
        histogram[level] = histogram.get(level, 0) + 1
    return histogram


def scope_data(service: LifeManagerService, scope_kind: str, scope_id: str, grid) -> tuple[dict, dict]:
    """某张热力图在当前窗口内的 (counts, summaries)。"""
    conn = service.db.conn
    if scope_kind == "area":
        return (
            heatmap_stats.counts_for_area(conn, scope_id, grid.date_from, grid.date_to),
            heatmap_stats.summaries(conn, "area_id", scope_id, grid.date_from, grid.date_to),
        )
    if scope_kind == "thread":
        return (
            heatmap_stats.counts_for_thread(conn, scope_id, grid.date_from, grid.date_to),
            heatmap_stats.summaries(conn, "thread_id", scope_id, grid.date_from, grid.date_to),
        )
    return (
        heatmap_stats.counts_for_process(conn, scope_id, grid.date_from, grid.date_to),
        heatmap_stats.summaries(conn, "process_id", scope_id, grid.date_from, grid.date_to),
    )


def open_day_dialog(
    service: LifeManagerService,
    parent: QWidget | None,
    scope_kind: str,
    scope_id: str,
    day: str,
    on_changed=None,
) -> None:
    """点某一天：列出当天的全部活动记录（可改备注与日期、也可彻底删除）。"""
    node = service.require_node(scope_id)
    if scope_kind == "area":
        subtitle = f"{node.name} 领域当天的活动（含其下全部进程与线程，按发生时的归属快照）"
        loader = lambda: service.list_activities(  # noqa: E731
            area_id=scope_id, date_from=day, date_to=day
        )
    elif scope_kind == "thread":
        subtitle = f"{node.name} 线程当天的活动（按发生时的归属快照）"
        loader = lambda: service.list_activities(  # noqa: E731
            thread_id=scope_id, date_from=day, date_to=day
        )
    else:
        subtitle = f"{node.name} 进程当天的活动（按发生时的归属快照）"
        loader = lambda: service.list_activities(  # noqa: E731
            process_id=scope_id, date_from=day, date_to=day
        )
    open_activity_dialog(
        service,
        parent,
        title=f"{day} · {node.name}",
        subtitle=subtitle,
        loader=loader,
        on_changed=on_changed,
    )


class HeatmapRow(QFrame):
    """一个节点一行：色条 + 名字 + 一句统计 + 迷你热力图。

    点这一行（包括点迷你热力图里的任意一块）→ 打开全屏大图。
    领域行左侧还有一个三角，用来折叠它下面的进程行。
    """

    openRequested = Signal()
    toggleRequested = Signal()
    noteRequested = Signal()

    def __init__(
        self,
        service: LifeManagerService,
        node: Node,
        scope_kind: str,
        color: str,
        on_changed=None,
        parent: QWidget | None = None,
        has_children: bool = False,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.node_id = node.id
        self.node_name = node.name
        self.scope_kind = scope_kind
        self.on_changed = on_changed
        self._is_area = scope_kind == "area"
        self._is_thread = scope_kind == "thread"
        self._note = ""
        self._total = 0
        self._active_days = 0

        self.setObjectName("HeatmapRowArea" if self._is_area else "HeatmapRow")
        self.setFixedHeight(AREA_ROW_HEIGHT if self._is_area else PROCESS_ROW_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击这一行：看它的全屏大热力图，点里面任意一天能看到当天的活动")

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addSpacing(self._indent())

        # 折叠三角：领域行折叠它下面的进程；进程行折叠它下面的线程。
        # 没有下一级的行不显示（但留出同样宽度占位，保证各列对齐）。
        self.toggle_button = QPushButton("▼")
        self.toggle_button.setObjectName("RowToggle")
        self.toggle_button.setFixedSize(22, 22)
        self.toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_button.setToolTip(
            "折叠 / 展开这个领域下的进程"
            if self._is_area
            else "折叠 / 展开这个进程下的线程"
        )
        self.toggle_button.clicked.connect(self.toggleRequested.emit)
        self.toggle_button.setVisible(has_children)
        row.addWidget(self.toggle_button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addSpacing(6)

        accent = QFrame()
        accent.setFixedSize(4, 20 if self._is_area else 16)
        accent.setStyleSheet(f"background: {color}; border-radius: 2px;")

        self._name_label = QLabel(node.name)
        title_style = (
            f"font-size: {'15px' if self._is_area else '13px'};"
            f" font-weight: {700 if self._is_area else 500};"
        )
        self._name_label.setStyleSheet(title_style)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name_row.addWidget(accent)
        name_row.addWidget(self._name_label)
        name_row.addStretch(1)

        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; font-size: 11px;")
        self._stats_label.setIndent(12)  # 与名字左边缘对齐（色条 4 + 间距 8）

        box = QWidget()
        box.setFixedWidth(LABEL_WIDTH)
        left = QVBoxLayout(box)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        left.addLayout(name_row)
        left.addWidget(self._stats_label)
        # 不放 stretch：让这一列的高度刚好等于内容，好和三角、热力图一起垂直居中
        row.addWidget(box, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addSpacing(14)

        self.calendar = HeatmapCalendar(scale=ROW_CALENDAR_SCALE)
        self.calendar.dateClicked.connect(lambda _day: self.openRequested.emit())
        row.addWidget(self.calendar, 0, Qt.AlignmentFlag.AlignVCenter)

        # 注释（领域 / 进程 / 线程都能写）放在迷你图右边，
        # 那里本来就有空白：既不挤，也不会被截断；没有注释时整块不留空位。
        self._note_label = QLabel("")
        # 和左边的标题同字号、同颜色
        self._note_label.setStyleSheet(title_style)
        self._note_label.setVisible(False)
        row.addSpacing(18)
        row.addWidget(self._note_label)
        # 必须有这个 stretch 兜底：注释隐藏时由它吸收剩余空间，
        # 否则 Qt 会把空白均分到各元素之间，整行内容会被推散。
        row.addStretch(1)

    # ---------------------------------------------------------------- 数据
    def _indent(self) -> int:
        """层级缩进：领域 0 / 进程 24 / 线程 48。"""
        if self._is_area:
            return 0
        if self._is_thread:
            return PROCESS_INDENT + THREAD_INDENT
        return PROCESS_INDENT

    def apply_window(self, grid) -> None:
        """按当前日历窗口刷新这张迷你热力图与统计文字。"""
        self.calendar.set_window(grid)
        counts, summaries = scope_data(
            self.service, self.scope_kind, self.node_id, grid
        )
        self.calendar.set_data(counts, summaries)
        self._total, self._active_days = heatmap_stats.window_totals(counts)
        self._sync_stats()

    def set_note(self, note: str) -> None:
        """注释（进程注释 / 已删除进程标注）：有才显示，没有就完全不留空位。"""
        self._note = (note or "").strip()
        self._note_label.setText(self._note)
        self._note_label.setVisible(bool(self._note))

    @property
    def note(self) -> str:
        """当前注释（原样返回，不做截断）。"""
        return self._note

    def _sync_stats(self) -> None:
        base = f"窗口内 {self._total} 次 · {self._active_days} 天有记录"
        self._stats_label.setText(base)

    def set_expanded_arrow(self, expanded: bool) -> None:
        self.toggle_button.setText("▼" if expanded else "▶")

    def _on_date_clicked(self, day: str) -> None:
        """看某一天的活动记录（迷你图上点某天同样走这里）。"""
        open_day_dialog(
            self.service, self, self.scope_kind, self.node_id, day, self.on_changed
        )

    # ---------------------------------------------------------------- 交互
    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        if event.button() == Qt.MouseButton.LeftButton:
            self.openRequested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        self.noteRequested.emit()
        event.accept()


class HeatmapDetailDialog(AppDialog):
    """某个节点的全屏大热力图：点任意一天的色块，**在下方就地**显示当天的活动记录。"""

    def __init__(
        self,
        service: LifeManagerService,
        node: Node,
        scope_kind: str,
        end_date: date,
        on_changed=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.node = node
        self.scope_kind = scope_kind
        self._on_changed = on_changed
        self.setWindowTitle(f"{node.name} · 热力图")
        self.setMinimumSize(900, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel(node.name)
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        header.addWidget(title)
        if scope_kind == "area":
            header.addWidget(Subtle("领域：含其下全部进程与线程"))
        elif scope_kind == "thread":
            header.addWidget(Subtle("只含记在这个线程上的活动"))
        header.addStretch(1)
        header.addWidget(HeatmapLegend())
        layout.addLayout(header)

        layout.addWidget(Muted(f"归属：{service.path_text(node.id)}"))
        self.stats_label = Subtle("")
        layout.addWidget(self.stats_label)

        self.calendar = HeatmapCalendar(scale=self._detail_scale())
        self.calendar.set_window(heatmap_stats.build_calendar(end_date=end_date))
        self.calendar.dateClicked.connect(self._on_day_clicked)
        calendar_row = QHBoxLayout()
        calendar_row.setContentsMargins(0, 0, 0, 0)
        calendar_row.addWidget(self.calendar)
        calendar_row.addStretch(1)
        layout.addLayout(calendar_row)

        # 大图下面那块空白：就地显示当天的活动记录，不再另开窗口
        self.day_panel = DayActivityPanel(
            service,
            scope_kind,
            node.id,
            on_changed=self._on_activities_changed,
            parent=self,
        )
        layout.addWidget(self.day_panel, 1)
        self.refresh()

    @staticmethod
    def _detail_scale() -> float:
        screen = QApplication.primaryScreen()
        width = screen.availableGeometry().width() if screen is not None else 1920
        return max(
            DETAIL_SCALE_MIN,
            min(DETAIL_SCALE_MAX, (width - 200) / BASE_CALENDAR_WIDTH),
        )

    def refresh(self) -> None:
        grid = self.calendar.grid
        counts, summaries = scope_data(
            self.service, self.scope_kind, self.node.id, grid
        )
        self.calendar.set_data(counts, summaries)
        total, active_days = heatmap_stats.window_totals(counts)
        detail = "、".join(
            f"{level} 次 {count} 天"
            for level, count in sorted(level_histogram(counts).items())
            if level
        )
        text = (
            f"最近 {grid.weeks} 周（{grid.date_from} 至 {grid.date_to}）"
            f" · {total} 次活动 · {active_days} 天有记录"
        )
        if detail:
            text += f"（{detail}）"
        self.stats_label.setText(text)

    def _on_day_clicked(self, day: str) -> None:
        """点某一天：在下面的面板里就地显示那天的记录（不弹新窗口）。"""
        self.day_panel.show_day(day)

    def _on_activities_changed(self) -> None:
        """面板里改了/删了记录：大图立刻重画，外面的页面也跟着刷新。"""
        self.refresh()
        if self._on_changed is not None:
            self._on_changed()


class DayActivityPanel(QWidget):
    """大图下面那块：某一天的活动记录，就地显示、就地修改（不再另开窗口）。"""

    def __init__(
        self,
        service: LifeManagerService,
        scope_kind: str,
        scope_id: str,
        on_changed=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.scope_kind = scope_kind
        self.scope_id = scope_id
        self._on_changed = on_changed
        self._day: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._title = SectionHeader("当天活动记录")
        layout.addWidget(self._title)

        self.view = ActivityListView(
            service,
            self,
            empty_text="点上面任意一天的色块，这里会列出那天的活动记录。",
        )
        self.view.changed.connect(self._on_view_changed)
        layout.addWidget(self.view, 1)

        self.show_day(None)

    @property
    def day(self) -> str | None:
        return self._day

    def show_day(self, day: str | None) -> None:
        """显示某一天；传 None 表示还没选日子（回到提示状态）。"""
        self._day = day
        if not day:
            self.view.set_loader(lambda: [])
            self.view.set_empty_text("点上面任意一天的色块，这里会列出那天的活动记录。")
            self.view.reload()
            self._sync_title()
            return
        if self.scope_kind == "area":
            self.view.set_loader(
                lambda: self.service.list_activities(
                    area_id=self.scope_id, date_from=day, date_to=day
                )
            )
        elif self.scope_kind == "thread":
            self.view.set_loader(
                lambda: self.service.list_activities(
                    thread_id=self.scope_id, date_from=day, date_to=day
                )
            )
        else:
            self.view.set_loader(
                lambda: self.service.list_activities(
                    process_id=self.scope_id, date_from=day, date_to=day
                )
            )
        self.view.set_empty_text(f"{day} 没有活动记录。")
        self.view.reload()
        self._sync_title()

    def _sync_title(self) -> None:
        if not self._day:
            self._title.setText("当天活动记录")
            return
        count = len(self.view.activities)
        self._title.setText(
            f"{self._day} · {count} 条记录" if count else f"{self._day} · 没有记录"
        )

    def _on_view_changed(self) -> None:
        """面板里改了/删了记录：刷新标题，并让大图和外面的页面一起跟上。"""
        self._sync_title()
        if self._on_changed is not None:
            self._on_changed()


class HeatmapsPage(QWidget):
    changed = Signal()

    def __init__(self, service: LifeManagerService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        # 距最近的周数：0 表示"最近 52 周"（终点永远是今天，跨午夜也自动跟上）
        self._window_offset_weeks = 0
        self._signature: tuple = ()
        self._rows: dict[str, HeatmapRow] = {}
        self._area_rows: dict[str, HeatmapRow] = {}
        self._process_rows: dict[str, HeatmapRow] = {}
        self._rows_by_area: dict[str, list[HeatmapRow]] = {}
        self._thread_rows: dict[str, list[HeatmapRow]] = {}
        self._collapsed_areas: set[str] = set()
        # 哪些进程被手动展开了线程热力图（线程默认收起，连"全部展开"也不动它）
        self._expanded_processes: set[str] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self._build_toolbar())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 1)
        self.container = QWidget()
        scroll.setWidget(self.container)
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 8, 0)
        self.container_layout.setSpacing(0)
        self.container_layout.addStretch(1)
        self.refresh()

    # -------------------------------------------------------------- 工具栏
    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        earlier = QPushButton("← 更早")
        earlier.clicked.connect(lambda: self._shift_window(52))
        later = QPushButton("更近 →")
        later.clicked.connect(lambda: self._shift_window(-52))
        self.later_button = later
        recent = QPushButton("回到最近")
        recent.clicked.connect(self._reset_window)

        row.addWidget(earlier)
        row.addWidget(later)
        row.addWidget(recent)
        self.window_label = Subtle("")
        row.addWidget(self.window_label)
        row.addStretch(1)
        # 整页只留一个图例：所有小图是同一套配色
        row.addWidget(HeatmapLegend())
        row.addSpacing(10)

        expand = QPushButton("全部展开")
        expand.clicked.connect(lambda: self._set_all_expanded(True))
        collapse = QPushButton("全部折叠")
        collapse.clicked.connect(lambda: self._set_all_expanded(False))
        row.addWidget(expand)
        row.addWidget(collapse)
        return bar

    def _grid(self) -> heatmap_stats.CalendarGrid:
        end = clock.today() - timedelta(weeks=self._window_offset_weeks)
        return heatmap_stats.build_calendar(end_date=end)

    def _shift_window(self, weeks: int) -> None:
        self._window_offset_weeks = max(0, self._window_offset_weeks + weeks)
        self._refresh_data()

    def _reset_window(self) -> None:
        self._window_offset_weeks = 0
        self._refresh_data()

    def _set_all_expanded(self, expanded: bool) -> None:
        """只管领域这一层：线程热力图必须手动点进程的三角才展开，这里不碰它。"""
        self._collapsed_areas = set() if expanded else set(self._rows_by_area)
        self._apply_collapsed()

    def _toggle_area(self, area_id: str) -> None:
        if area_id in self._collapsed_areas:
            self._collapsed_areas.discard(area_id)
        else:
            self._collapsed_areas.add(area_id)
        self._apply_collapsed()

    def _toggle_process(self, process_id: str) -> None:
        """展开 / 收起这个进程下面的线程热力图。"""
        if process_id in self._expanded_processes:
            self._expanded_processes.discard(process_id)
        else:
            self._expanded_processes.add(process_id)
        self._apply_collapsed()

    def _apply_collapsed(self) -> None:
        for area_id, process_rows in self._rows_by_area.items():
            area_collapsed = area_id in self._collapsed_areas
            for row in process_rows:
                row.setVisible(not area_collapsed)
                expanded = row.node_id in self._expanded_processes
                row.set_expanded_arrow(expanded)
                # 线程行只有在"领域没折叠 + 这个进程被手动展开"时才出现
                show_threads = (not area_collapsed) and expanded
                for thread_row in self._thread_rows.get(row.node_id, []):
                    thread_row.setVisible(show_threads)
            area_row = self._area_rows.get(area_id)
            if area_row is not None:
                area_row.set_expanded_arrow(not area_collapsed)

    # ---------------------------------------------------------------- 结构
    def _structure_signature(self) -> tuple:
        signature = []
        for area in self.service.areas():
            processes = tuple(
                (
                    process.id,
                    process.name,
                    # 状态也要进签名：设成「已暂停」后这一页要重建（暂停的不显示）
                    process.status.value,
                    # 线程也要进签名：不然新建/改名/删掉一个线程，这一页不会重建
                    tuple(
                        (thread.id, thread.name, thread.status.value)
                        for thread in self.service.threads(process.id)
                    ),
                )
                for process in self.service.processes(area.id)
            )
            signature.append((area.id, area.name, processes))
        deleted = tuple(
            (node.id, node.name, node.type.value)
            for node in self.service.all_nodes(include_deleted=True)
            if node.is_deleted
        )
        return (tuple(signature), deleted)

    def refresh(self) -> None:
        signature = self._structure_signature()
        if signature != self._signature:
            self._rebuild(signature)
        self._refresh_data()

    def _rebuild(self, signature: tuple) -> None:
        self._signature = signature
        clear_layout(self.container_layout)
        self._rows.clear()
        self._area_rows.clear()
        self._process_rows.clear()
        self._rows_by_area.clear()

        first = True
        for index, area in enumerate(self.service.areas()):
            color = theme.map_color(index)
            # 已暂停的进程 / 线程不进热力图（它们在进程树里仍然保留，数据也不动）
            processes = [
                process
                for process in self.service.processes(area.id)
                if process.status is not Status.PAUSED
            ]
            if not first:
                self.container_layout.addSpacing(AREA_GAP)
                self.container_layout.addWidget(_divider())
                self.container_layout.addSpacing(10)
            first = False

            area_row = HeatmapRow(
                self.service,
                area,
                "area",
                color,
                on_changed=self._on_activities_changed,
                has_children=bool(processes),
            )
            area_row.openRequested.connect(
                lambda area_id=area.id: self._open_detail("area", area_id)
            )
            area_row.toggleRequested.connect(
                lambda area_id=area.id: self._toggle_area(area_id)
            )
            area_row.noteRequested.connect(
                lambda area_id=area.id: self._edit_note(area_id)
            )
            self.container_layout.addWidget(area_row)
            self._rows[area.id] = area_row
            self._area_rows[area.id] = area_row

            rows: list[HeatmapRow] = []
            for process in processes:
                threads = [
                    thread
                    for thread in self.service.threads(process.id)
                    if thread.status is not Status.PAUSED
                ]
                row = HeatmapRow(
                    self.service,
                    process,
                    "process",
                    color,
                    on_changed=self._on_activities_changed,
                    has_children=bool(threads),
                )
                row.openRequested.connect(
                    lambda process_id=process.id: self._open_detail("process", process_id)
                )
                row.toggleRequested.connect(
                    lambda process_id=process.id: self._toggle_process(process_id)
                )
                row.noteRequested.connect(
                    lambda process_id=process.id: self._edit_note(process_id)
                )
                self.container_layout.addWidget(row)
                self._rows[process.id] = row
                self._process_rows[process.id] = row
                rows.append(row)

                # 线程行：建好但默认隐藏，只有点进程的三角才显示
                thread_rows: list[HeatmapRow] = []
                for thread in threads:
                    thread_row = HeatmapRow(
                        self.service,
                        thread,
                        "thread",
                        color,
                        on_changed=self._on_activities_changed,
                    )
                    thread_row.openRequested.connect(
                        lambda thread_id=thread.id: self._open_detail("thread", thread_id)
                    )
                    thread_row.noteRequested.connect(
                        lambda thread_id=thread.id: self._edit_note(thread_id)
                    )
                    thread_row.setVisible(False)
                    self.container_layout.addWidget(thread_row)
                    self._rows[thread.id] = thread_row
                    thread_rows.append(thread_row)
                self._thread_rows[process.id] = thread_rows
            self._rows_by_area[area.id] = rows

        note = Muted(
            "线程热力图统计的是「当时就记在这个线程上」的活动；记在进程上的活动只进进程与领域的热力图。"
            "线程默认收起，点进程左边的三角才展开（「全部展开」不会动它）。"
            "已暂停的进程和线程不在这里显示。点任意一行可以打开全屏大图。"
        )
        self.container_layout.addSpacing(AREA_GAP)
        self.container_layout.addWidget(note)
        self.container_layout.addStretch(1)
        self._apply_collapsed()

    def _refresh_data(self) -> None:
        grid = self._grid()
        self.window_label.setText(
            f"最近 {grid.weeks} 周（{grid.date_from} 至 {grid.date_to}）"
        )
        self.later_button.setEnabled(self._window_offset_weeks > 0)
        # 注释：领域 / 进程 / 线程都能写；有就显示，没有就完全不留空位
        notes = {
            row["id"]: row["description"]
            for row in self.service.db.conn.execute(
                "SELECT id, description FROM nodes WHERE description <> ''"
            ).fetchall()
        }
        for node_id, row in self._rows.items():
            row.apply_window(grid)
            note = notes.get(node_id, "")
            if node_id in self._area_rows:
                # 领域汇总里可能含已删除进程的数据，标出来免得以为是算错了
                extra = self._deleted_children_annotation(node_id, grid)
                note = " · ".join(part for part in (note, extra) if part)
            row.set_note(note)

    def _deleted_children_annotation(self, area_id: str, grid) -> str:
        """标注领域热力图中来自已删除进程的次数（数据保留，不是丢数据）。"""
        parts: list[str] = []
        for process in self.service.deleted_children(area_id, NodeType.PROCESS):
            counts = heatmap_stats.counts_for_process(
                self.service.db.conn, process.id, grid.date_from, grid.date_to
            )
            total = sum(counts.values())
            if total:
                parts.append(f"{process.name} {total} 次")
        return f"含已删除进程：{'、'.join(parts)}" if parts else ""

    # ---------------------------------------------------------------- 操作
    def _open_detail(self, scope_kind: str, node_id: str) -> None:
        """打开这个节点的全屏大热力图。"""
        node = self.service.get_node(node_id)
        if node is None:
            return
        dialog = HeatmapDetailDialog(
            self.service,
            node,
            scope_kind,
            self._grid().end,
            on_changed=self._on_activities_changed,
            parent=self,
        )
        dialog.showMaximized()
        dialog.exec()

    def _on_activities_changed(self) -> None:
        """弹窗里改了/删了活动：立刻重画本页，并通知主窗口（其它页面切回时自动刷新）。"""
        self.refresh()
        self.changed.emit()

    def _edit_note(self, node_id: str) -> None:
        """右键热力图的任意一行：写一句注释（留空则完全不显示）。"""
        node = self.service.require_node(node_id)
        text, ok = QInputDialog.getMultiLineText(
            self,
            f"{node.type.label}注释",
            f"给「{node.name}」的热力图写一句注释（留空则不显示）：",
            node.description,
        )
        if not ok:
            return
        try:
            self.service.set_description(node_id, text)
        except ValidationError as exc:
            warn(self, "无法保存注释", str(exc))
            return
        self.changed.emit()
