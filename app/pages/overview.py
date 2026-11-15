"""页面 A：总览 Overview。

版式是"纸面"式的：不画卡片框，区块之间只靠留白与细线分隔。
    - 总热力图在最上，右侧是时钟；下面依次是当前重点、快速记录、领域热力图、最近活动；
    - 当前重点不画框，只在卡片之间画一条竖细线；
    - 快速记录是三个"只有下划线"的字段。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import heatmap as heatmap_stats
from .. import theme
from ..appconfig import AppConfig
from ..models import Node, Status, today_str
from ..services import LifeManagerService, ValidationError
from ..widgets.common import (
    ActivityTable,
    BeijingClock,
    Card,
    Muted,
    NodeComboBox,
    open_activity_dialog,
    QuickRecordDialog,
    SectionHeader,
    Subtle,
    clear_layout,
    qdate_to_str,
    str_to_qdate,
    warn,
)
from ..widgets.heatmap_calendar import HeatmapCalendar, HeatmapLegend

# 区块之间、区块内部两档留白（纸面风格只靠这两档拉开层次）
SECTION_GAP = 32
INNER_GAP = 14


class FocusCard(Card):
    """一个重点：名称、主要归属、状态、下一步行动。

    卡片本身可以按住拖动 —— 只调整它在总览里的显示顺序，不改任何数据。
    外观上没有边框；除了第一张，每张左侧画一条竖细线当分隔。
    """

    recordRequested = Signal(str)
    detailRequested = Signal(str)
    dragMoved = Signal(str, QPoint)
    dragFinished = Signal(str)

    DRAG_THRESHOLD = 6

    def __init__(self, service: LifeManagerService, node_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, name="FocusCard")
        self.service = service
        self.node_id = node_id
        self._drag_origin: QPoint | None = None
        self._dragging = False
        self._divider = False
        self.body.setContentsMargins(0, 8, 26, 8)
        self.body.setSpacing(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self._title = QLabel()
        self._title.setStyleSheet("font-size: 14px; font-weight: 600;")
        title_row.addWidget(self._title)
        # 状态用纯文字：一屏里只留一个重色，状态不再抢按钮的注意力
        self._chip = QLabel()
        self._chip.setStyleSheet("font-size: 11px;")
        title_row.addWidget(self._chip)
        title_row.addStretch(1)
        self.body.addLayout(title_row)

        self._path = Subtle("")
        self._path.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; font-size: 12px;")
        self.body.addWidget(self._path)

        self._action = QLabel()
        self._action.setWordWrap(True)
        self.body.addWidget(self._action)
        self.body.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(14)
        record = QPushButton("记录活动")
        record.setObjectName("Primary")
        record.setFixedHeight(28)
        record.clicked.connect(lambda: self.recordRequested.emit(self.node_id))
        detail = QPushButton("详情")
        detail.setObjectName("Ghost")
        detail.clicked.connect(lambda: self.detailRequested.emit(self.node_id))
        buttons.addWidget(record)
        buttons.addWidget(detail)
        buttons.addStretch(1)
        self.body.addLayout(buttons)
        self.update_content()

    # ------------------------------------------------------------ 分隔线
    def set_divider(self, show: bool) -> None:
        """第一张不画；其余每张左侧画一条竖线，代替卡片框。

        画线的同时把正文往右挪一点，否则文字会贴着那条线。
        """
        self.body.setContentsMargins(26 if show else 0, 8, 26, 8)
        if self._divider != show:
            self._divider = show
            self.update()

    def paintEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        super().paintEvent(event)
        if not self._divider:
            return
        painter = QPainter(self)
        pen = QColor(theme.HAIRLINE)
        painter.setPen(pen)
        painter.drawLine(0, 8, 0, self.height() - 8)
        painter.end()

    # ------------------------------------------------------------ 拖动换位
    def mousePressEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.position().toPoint()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        if self._drag_origin is None:
            return
        if not self._dragging:
            offset = event.position().toPoint() - self._drag_origin
            if offset.manhattanLength() < self.DRAG_THRESHOLD:
                return
            self._dragging = True
            self._set_dragging_style(True)
        self.dragMoved.emit(self.node_id, event.globalPosition().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        if self._drag_origin is not None:
            was_dragging = self._dragging
            self._drag_origin = None
            self._dragging = False
            if was_dragging:
                self._set_dragging_style(False)
                self.dragFinished.emit(self.node_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _set_dragging_style(self, active: bool) -> None:
        if active:
            self.setStyleSheet(f"#FocusCard {{ background: {theme.SURFACE}; border-radius: 8px; }}")
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            self.setStyleSheet("")
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def update_content(self) -> None:
        """只更新文字，不重建控件（避免总览在记录活动后闪烁）。"""
        node = self.service.require_node(self.node_id)
        next_action = node.next_action.strip()
        self._title.setText(node.name)
        self._chip.setText(node.status.value)
        self._chip.setStyleSheet(
            f"color: {theme.STATUS_STYLE[node.status.value][0]}; font-size: 11px;"
        )
        self._path.setText(self.service.path_text(self.node_id))
        self._action.setText(f"下一步：{next_action}" if next_action else "下一步：尚未设置")
        self._action.setStyleSheet(
            f"color: {theme.TEXT if next_action else theme.TEXT_SUBTLE}; font-size: 13px;"
        )


class OverviewHeatmapCard(Card):
    """总览里的一张热力图：area_id 为 None 时表示「全部领域与进程」的总热力图。

    两种形态：
        - 总热力图：标题一行，右侧时钟，下面一张放大的日历；
        - 领域热力图：左边一列写名字与统计，右边一张普通大小的日历（同一时间轴对齐）。
    """

    def __init__(
        self,
        service: LifeManagerService,
        area_id: str | None = None,
        parent: QWidget | None = None,
        *,
        scale: float = 1.0,
        on_changed=None,
        show_labels: bool = True,
    ) -> None:
        super().__init__(parent, name="PlainCard")
        self.service = service
        self.area_id = area_id
        self.total = area_id is None
        self._on_changed = on_changed
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(6)

        self.calendar = HeatmapCalendar(
            scale=scale,
            hot=self.total,
            # 彩蛋：只在这张总热力图上把这一天涂成粉色
            highlight_day="2026-10-15" if self.total else None,
            show_labels=show_labels,
        )
        self.calendar.dateClicked.connect(self._on_date_clicked)

        if self.total:
            header = QHBoxLayout()
            header.setSpacing(12)
            title = QLabel("总热力图")
            title.setStyleSheet("font-size: 15px; font-weight: 600;")
            header.addWidget(title)
            header.addWidget(Subtle("全部领域与进程汇总 · 每条只算一次"))
            header.addStretch(1)
            self.stats_label = Muted("")
            self.stats_label.setWordWrap(False)
            header.addWidget(self.stats_label)
            self.body.addLayout(header)

            # 日历与图例放在同一列：图例贴住热力图的右缘，不贴卡片右缘
            plot = QVBoxLayout()
            plot.setContentsMargins(0, 0, 0, 0)
            plot.setSpacing(8)
            plot.addWidget(self.calendar, 0, Qt.AlignmentFlag.AlignLeft)
            legend_row = QHBoxLayout()
            legend_row.setContentsMargins(0, 0, 0, 0)
            legend_row.addStretch(1)
            legend_row.addWidget(HeatmapLegend(scale=scale, hot=True))
            plot.addLayout(legend_row)

            row = QHBoxLayout()
            row.setContentsMargins(0, 4, 0, 0)
            row.addLayout(plot, 0)
            row.addStretch(1)
            self.clock_widget = BeijingClock(time_size=32)
            row.addWidget(self.clock_widget, 0, Qt.AlignmentFlag.AlignTop)
            self.body.addLayout(row)
            return

        # 领域行：左标签列 + 右日历
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(18)

        side = QWidget()
        side.setFixedWidth(112)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(2)
        name = QLabel(self.service.require_node(area_id).name)
        name.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.stats_label = Subtle("")
        side_layout.addWidget(name)
        side_layout.addWidget(self.stats_label)
        side_layout.addStretch(1)
        line.addWidget(side, 0, Qt.AlignmentFlag.AlignVCenter)
        line.addWidget(self.calendar, 0, Qt.AlignmentFlag.AlignVCenter)
        line.addStretch(1)
        self.body.addLayout(line)

    def refresh(self) -> None:
        # 一直开着跨过午夜时，把窗口挪到今天
        self.calendar.refresh_window()
        grid = self.calendar.grid  # 与控件使用同一窗口
        conn = self.service.db.conn
        if self.total:
            counts = heatmap_stats.counts_all(conn, grid.date_from, grid.date_to)
            summaries = heatmap_stats.summaries_all(conn, grid.date_from, grid.date_to)
        else:
            counts = heatmap_stats.counts_for_area(
                conn, self.area_id, grid.date_from, grid.date_to
            )
            summaries = heatmap_stats.summaries(
                conn, "area_id", self.area_id, grid.date_from, grid.date_to
            )
        self.calendar.set_data(counts, summaries)
        total, active_days = heatmap_stats.window_totals(counts)
        if self.total:
            self.stats_label.setText(f"近 {grid.weeks} 周 {total} 次 · {active_days} 天有记录")
        else:
            self.stats_label.setText(f"{total} 次 · {active_days} 天")

    def _on_date_clicked(self, day: str) -> None:
        if self.total:
            open_activity_dialog(
                self.service,
                self,
                title=f"{day} · 全部领域与进程",
                subtitle="这一天的全部活动记录（每条只算一次）",
                loader=lambda: self.service.list_activities(date_from=day, date_to=day),
                empty_text="这一天没有任何记录。",
                on_changed=self._on_changed,
            )
            return
        area = self.service.require_node(self.area_id)
        open_activity_dialog(
            self.service,
            self,
            title=f"{day} · {area.name}",
            subtitle=f"{area.name} 领域当天的活动记录（按发生时的归属快照统计）",
            loader=lambda: self.service.list_activities(
                area_id=self.area_id, date_from=day, date_to=day
            ),
            on_changed=self._on_changed,
        )


class OverviewPage(QWidget):
    changed = Signal()
    openNodeRequested = Signal(str)
    openPageRequested = Signal(str)

    def __init__(
        self,
        service: LifeManagerService,
        parent: QWidget | None = None,
        *,
        config: AppConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.config = config
        self._area_cards: list[OverviewHeatmapCard] = []
        self._focus_cards: list[FocusCard] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 22, 24, 28)
        layout.setSpacing(0)

        # ---------------------------------------------------------- 总热力图
        self._total_card = OverviewHeatmapCard(
            # 总热力图保持放大尺寸（1.25 倍），时钟在右上角
            self.service, None, scale=1.25, on_changed=self._on_activities_changed
        )
        layout.addWidget(self._total_card)
        layout.addSpacing(SECTION_GAP)

        # ---------------------------------------------------------- 当前重点
        focus_header = QHBoxLayout()
        focus_header.setSpacing(12)
        focus_header.addWidget(SectionHeader("当前重点"))
        focus_header.addWidget(Subtle("最多 3 个置顶节点"))
        focus_header.addStretch(1)
        layout.addLayout(focus_header)
        layout.addSpacing(INNER_GAP)

        self.focus_row = QHBoxLayout()
        self.focus_row.setSpacing(0)
        layout.addLayout(self.focus_row)

        self.focus_empty = Muted(
            "还没有置顶的重点。到「进程树」里把正在推进的进程或线程置顶，这里就会显示它的状态与下一步行动。"
        )
        layout.addWidget(self.focus_empty)
        layout.addSpacing(SECTION_GAP)

        # ---------------------------------------------------------- 快速记录
        quick_header = QHBoxLayout()
        quick_header.setSpacing(12)
        quick_header.addWidget(SectionHeader("快速记录"))
        quick_header.addWidget(Subtle("独立进展记一次活动"))
        quick_header.addStretch(1)
        layout.addLayout(quick_header)
        layout.addSpacing(INNER_GAP)

        self.quick_combo = NodeComboBox()
        self.quick_date = QDateEdit()
        self.quick_date.setCalendarPopup(True)
        self.quick_date.setDisplayFormat("yyyy-MM-dd")
        self.quick_date.setDate(str_to_qdate(today_str()))
        self.quick_note = QLineEdit()
        self.quick_note.setPlaceholderText("可留空")
        self.quick_note.returnPressed.connect(self._on_quick_record)
        self.quick_button = QPushButton("记录")
        self.quick_button.setObjectName("Primary")
        self.quick_button.setFixedHeight(30)
        self.quick_button.setMinimumWidth(72)
        self.quick_button.clicked.connect(self._on_quick_record)

        quick_row = QHBoxLayout()
        quick_row.setContentsMargins(0, 0, 0, 0)
        quick_row.setSpacing(22)
        quick_row.addWidget(self._underline("进程", self.quick_combo, 300))
        quick_row.addWidget(self._underline("日期", self.quick_date, 172))
        quick_row.addWidget(self._underline("备注", self.quick_note), 1)
        quick_row.addWidget(self.quick_button, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(quick_row)
        layout.addSpacing(10)
        layout.addWidget(Subtle("热力图按次数着色，不按字数。"))
        layout.addSpacing(SECTION_GAP)

        # -------------------------------------------------------- 领域热力图
        heatmap_header = QHBoxLayout()
        heatmap_header.setSpacing(12)
        heatmap_header.addWidget(SectionHeader("领域热力图"))
        heatmap_header.addWidget(Subtle("同一条时间轴，只换统计范围"))
        heatmap_header.addStretch(1)
        more = QPushButton("在热力图页查看全部")
        more.setObjectName("Ghost")
        more.clicked.connect(lambda: self.openPageRequested.emit("heatmaps"))
        heatmap_header.addWidget(more)
        layout.addLayout(heatmap_header)
        layout.addSpacing(INNER_GAP)

        self.heatmap_container = QVBoxLayout()
        self.heatmap_container.setSpacing(INNER_GAP)
        layout.addLayout(self.heatmap_container)
        layout.addSpacing(SECTION_GAP)

        # ---------------------------------------------------------- 最近活动
        recent_header = QHBoxLayout()
        recent_header.setSpacing(12)
        recent_header.addWidget(SectionHeader("最近活动"))
        recent_header.addStretch(1)
        self.recent_count = Subtle("")
        recent_header.addWidget(self.recent_count)
        layout.addLayout(recent_header)
        layout.addSpacing(INNER_GAP)

        self.recent_table = ActivityTable()
        # 无框表：只有行、没有表头与网格；"记在哪"恒为线程/进程，删掉这一列
        self.recent_table.setObjectName("BareTable")
        self.recent_table.horizontalHeader().setVisible(False)
        self.recent_table.setColumnHidden(2, True)
        self.recent_table.ROW_HEIGHT = 32
        self.recent_table.load([], compact=True)
        layout.addWidget(self.recent_table)

        layout.addStretch(1)
        self.refresh()

    # ------------------------------------------------------------ 下划线字段
    def _underline(self, label: str, widget: QWidget, width: int | None = None) -> QWidget:
        """一个"只有下划线"的字段：左侧小号灰字，右侧无框控件。"""
        frame = QFrame()
        frame.setObjectName("Underline")
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 6)
        row.setSpacing(8)
        key = QLabel(label)
        key.setObjectName("FieldKey")
        row.addWidget(key)
        row.addWidget(widget, 1)
        if width is not None:
            frame.setFixedWidth(width)
        return frame

    # ---------------------------------------------------------------- 刷新
    def refresh(self) -> None:
        self._refresh_focus()
        self._refresh_quick()
        self._refresh_heatmaps()
        self._refresh_recent()

    def _refresh_focus(self) -> None:
        focused = self._ordered_focus_nodes()
        if [card.node_id for card in self._focus_cards] != [node.id for node in focused]:
            clear_layout(self.focus_row)
            self._focus_cards = []
            for node in focused:
                card = FocusCard(self.service, node.id)
                card.recordRequested.connect(self._record_for_node)
                card.detailRequested.connect(self.openNodeRequested.emit)
                card.dragMoved.connect(self._on_focus_card_moved)
                card.dragFinished.connect(self._on_focus_drag_finished)
                self._focus_cards.append(card)
                self.focus_row.addWidget(card, 1)
            if len(focused) < 3:
                self.focus_row.addStretch(1)
        else:
            for card in self._focus_cards:
                card.update_content()
        # 第一张不画竖线，其余每张画（重排后也要跟着更新）
        for index, card in enumerate(self._focus_cards):
            card.set_divider(index > 0)
        self.focus_empty.setVisible(not focused)

    # ------------------------------------------------- 当前重点的显示顺序
    def _focus_order(self) -> list[str]:
        """配置里记下的显示顺序（只认还在置顶列表里的节点）。"""
        if self.config is None:
            return []
        order = self.config.get("focus_order") or []
        return [str(item) for item in order] if isinstance(order, list) else []

    def _ordered_focus_nodes(self) -> list[Node]:
        focused = self.service.focused_nodes()
        order = self._focus_order()
        if not order:
            return focused
        by_id = {node.id: node for node in focused}
        ordered = [by_id[node_id] for node_id in order if node_id in by_id]
        known = set(order)
        ordered.extend(node for node in focused if node.id not in known)
        return ordered

    def _on_focus_card_moved(self, node_id: str, global_pos: QPoint) -> None:
        """拖动中的卡片：按鼠标横向位置算出它该排到第几个。"""
        if not self._focus_cards:
            return
        position = self.mapFromGlobal(global_pos)
        target = len(self._focus_cards) - 1
        for index, card in enumerate(self._focus_cards):
            center_x = card.mapTo(self, card.rect().center()).x()
            if position.x() < center_x:
                target = index
                break
        self._move_focus_card(node_id, target, save=False)

    def _move_focus_card(
        self, node_id: str, target_index: int, *, save: bool = True
    ) -> None:
        """把某张重点卡片挪到第 target_index 位：只改显示顺序，不碰任何数据。"""
        ids = [card.node_id for card in self._focus_cards]
        if node_id not in ids:
            return
        current = ids.index(node_id)
        target = max(0, min(target_index, len(ids) - 1))
        if current == target:
            return
        ids.pop(current)
        ids.insert(target, node_id)
        by_id = {card.node_id: card for card in self._focus_cards}
        self._focus_cards = [by_id[item] for item in ids]
        for card in self._focus_cards:
            self.focus_row.removeWidget(card)
        for index, card in enumerate(self._focus_cards):
            self.focus_row.insertWidget(index, card, 1)
            card.set_divider(index > 0)
        if save:
            self._save_focus_order()

    def _on_focus_drag_finished(self, _node_id: str) -> None:
        """松手时才写配置：拖动过程中不反复写文件。"""
        self._save_focus_order()

    def _save_focus_order(self) -> None:
        if self.config is None:
            return
        self.config.set("focus_order", [card.node_id for card in self._focus_cards])
        self.config.save()

    def _refresh_quick(self) -> None:
        current = self.quick_combo.current_node_id()
        self.quick_combo.populate(self.service, selected_id=current)

    def _refresh_heatmaps(self) -> None:
        areas = self.service.areas()
        self._total_card.refresh()
        if [card.area_id for card in self._area_cards] != [area.id for area in areas]:
            clear_layout(self.heatmap_container)
            self._area_cards = []
            for index, area in enumerate(areas):
                card = OverviewHeatmapCard(
                    self.service,
                    area.id,
                    # 领域图比总热力图小一号：三行叠起来不至于把页面拉得太长
                    scale=0.9,
                    on_changed=self._on_activities_changed,
                    # 只有第一行画月份/星期标记：下面的行与它严格对齐，省掉重复标签
                    show_labels=index == 0,
                )
                self._area_cards.append(card)
                self.heatmap_container.addWidget(card)
        for card in self._area_cards:
            card.refresh()

    def _refresh_recent(self) -> None:
        activities = self.service.list_activities(limit=12)
        self.recent_table.load(activities, compact=True)
        total = self.service.db.counts()["activities"]
        self.recent_count.setText(f"共 {total} 条活动记录")

    def _on_activities_changed(self) -> None:
        """弹窗里改了/删了活动：立刻重画总览，并通知主窗口。"""
        self.refresh()
        self.changed.emit()

    # ---------------------------------------------------------------- 交互
    def _on_quick_record(self) -> None:
        node_id = self.quick_combo.current_node_id()
        if not node_id:
            warn(self, "无法记录", "请先选择一个进程或线程。新建节点请到「进程树」页面。")
            return
        try:
            self.service.record_activity(
                node_id, self.quick_note.text(), qdate_to_str(self.quick_date.date())
            )
        except ValidationError as exc:
            warn(self, "无法记录", str(exc))
            return
        self.quick_note.clear()
        self.changed.emit()

    def _record_for_node(self, node_id: str) -> None:
        dialog = QuickRecordDialog(self.service, self, preset_node_id=node_id)
        if dialog.exec() and dialog.recorded is not None:
            self.changed.emit()
