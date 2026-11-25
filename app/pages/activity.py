"""页面 D：活动记录 Activity Log。

按时间倒序展示已记录活动，支持按 Area / Process / Thread 与日期范围筛选，
并展示每条活动发生时的归属快照以及目标节点的归属变更历史。
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import clock, theme
from ..models import Activity
from ..services import LifeManagerService, ValidationError
from ..widgets.common import (
    ActivityTable,
    Card,
    EventHistoryList,
    Muted,
    SectionHeader,
    Subtle,
    build_activity_menu,
    clear_layout,
    confirm,
    hline,
    open_activity_dialog,
    qdate_to_str,
    str_to_qdate,
    warn,
)


class ActivityPage(QWidget):
    changed = Signal()
    openNodeRequested = Signal(str)

    def __init__(self, service: LifeManagerService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._activities: list[Activity] = []
        self._detail_widgets: list[QWidget] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self._build_filters())

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(1)

        self.table = ActivityTable()
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_activity_menu)
        splitter.addWidget(self.table)

        self.detail_card = Card()
        splitter.addWidget(self.detail_card)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([520, 320])
        layout.addWidget(splitter, 1)
        self.refresh()

    # -------------------------------------------------------------- 筛选
    def _build_filters(self) -> QWidget:
        holder = Card()
        row = QHBoxLayout()
        row.setSpacing(8)

        self.area_combo = QComboBox()
        self.area_combo.setMinimumWidth(110)
        self.area_combo.currentIndexChanged.connect(self._on_area_changed)
        self.process_combo = QComboBox()
        self.process_combo.setMinimumWidth(120)
        self.process_combo.currentIndexChanged.connect(self._on_process_changed)
        self.thread_combo = QComboBox()
        self.thread_combo.setMinimumWidth(140)

        for label, combo in (("领域", self.area_combo), ("进程", self.process_combo), ("线程", self.thread_combo)):
            block = QVBoxLayout()
            block.setSpacing(4)
            block.addWidget(QLabel(label))
            block.addWidget(combo)
            row.addLayout(block)

        self.range_toggle = QCheckBox("限定日期范围")
        self.range_toggle.stateChanged.connect(lambda _state: self._on_range_toggled())
        row.addWidget(self.range_toggle)
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        self.date_from.setDate(
            str_to_qdate((clock.today() - timedelta(days=30)).isoformat())
        )
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        self.date_to.setDate(str_to_qdate(clock.today_str()))
        row.addWidget(self.date_from)
        row.addWidget(QLabel("至"))
        row.addWidget(self.date_to)

        self.keyword = QLineEdit()
        self.keyword.setPlaceholderText("关键词（备注）")
        self.keyword.returnPressed.connect(self.refresh)
        row.addWidget(self.keyword, 1)

        apply_button = QPushButton("筛选")
        apply_button.setObjectName("Primary")
        apply_button.clicked.connect(self.refresh)
        reset_button = QPushButton("重置")
        reset_button.clicked.connect(self._reset_filters)
        row.addWidget(apply_button)
        row.addWidget(reset_button)

        holder.body.addLayout(row)
        self.count_label = Subtle("")
        holder.body.addWidget(self.count_label)
        self._on_range_toggled()
        return holder

    def _on_range_toggled(self) -> None:
        enabled = self.range_toggle.isChecked()
        self.date_from.setEnabled(enabled)
        self.date_to.setEnabled(enabled)

    def _reset_filters(self) -> None:
        self.area_combo.setCurrentIndex(0)
        self.keyword.clear()
        self.range_toggle.setChecked(False)
        self.refresh()

    def _reload_filter_options(self) -> None:
        current_area = self.area_combo.currentData()
        current_process = self.process_combo.currentData()
        current_thread = self.thread_combo.currentData()

        self.area_combo.blockSignals(True)
        self.area_combo.clear()
        self.area_combo.addItem("全部领域", None)
        for area in self.service.areas():
            self.area_combo.addItem(area.name, area.id)
        index = self.area_combo.findData(current_area)
        self.area_combo.setCurrentIndex(max(index, 0))
        self.area_combo.blockSignals(False)

        self.process_combo.blockSignals(True)
        self._fill_process_combo(current_area)
        index = self.process_combo.findData(current_process)
        self.process_combo.setCurrentIndex(max(index, 0))
        self.process_combo.blockSignals(False)

        self.thread_combo.blockSignals(True)
        self._fill_thread_combo(current_process)
        index = self.thread_combo.findData(current_thread)
        self.thread_combo.setCurrentIndex(max(index, 0))
        self.thread_combo.blockSignals(False)

    def _fill_process_combo(self, area_id: str | None) -> None:
        self.process_combo.clear()
        self.process_combo.addItem("全部进程", None)
        processes = (
            self.service.processes(area_id) if area_id else self.service.processes()
        )
        for process in processes:
            self.process_combo.addItem(process.name, process.id)

    def _fill_thread_combo(self, process_id: str | None) -> None:
        self.thread_combo.clear()
        self.thread_combo.addItem("全部线程", None)
        threads = self.service.threads(process_id) if process_id else self.service.threads()
        for thread in threads:
            self.thread_combo.addItem(thread.name, thread.id)

    def _on_area_changed(self) -> None:
        area_id = self.area_combo.currentData()
        self.process_combo.blockSignals(True)
        self._fill_process_combo(area_id)
        self.process_combo.blockSignals(False)
        self.thread_combo.blockSignals(True)
        self._fill_thread_combo(None)
        self.thread_combo.blockSignals(False)
        self.refresh()

    def _on_process_changed(self) -> None:
        process_id = self.process_combo.currentData()
        self.thread_combo.blockSignals(True)
        self._fill_thread_combo(process_id)
        self.thread_combo.blockSignals(False)
        self.refresh()

    # ---------------------------------------------------------------- 数据
    def refresh(self) -> None:
        self._reload_filter_options()
        date_from = qdate_to_str(self.date_from.date()) if self.range_toggle.isChecked() else None
        date_to = qdate_to_str(self.date_to.date()) if self.range_toggle.isChecked() else None
        self._activities = self.service.list_activities(
            area_id=self.area_combo.currentData(),
            process_id=self.process_combo.currentData(),
            thread_id=self.thread_combo.currentData(),
            date_from=date_from,
            date_to=date_to,
            keyword=self.keyword.text().strip() or None,
        )
        self.table.load(self._activities)
        self.count_label.setText(
            f"共 {len(self._activities)} 条记录 · 按时间倒序 · 归属以记录发生时的快照为准"
        )
        self._clear_detail()
        if self._activities:
            self.table.selectRow(0)

    def _clear_detail(self) -> None:
        clear_layout(self.detail_card.body)
        self.detail_card.body.addWidget(Muted("在上面的列表里选择一条活动，查看它的归属快照与相关历史。"))

    # ---------------------------------------------------------------- 详情
    def _current_activity(self) -> Activity | None:
        rows = {index.row() for index in self.table.selectedItems()}
        if not rows:
            return None
        row = sorted(rows)[0]
        if 0 <= row < len(self._activities):
            return self._activities[row]
        return None

    def _activity_at(self, pos) -> Activity | None:
        item = self.table.itemAt(pos)
        if item is None:
            return None
        row = item.row()
        self.table.selectRow(row)
        return self._activities[row] if 0 <= row < len(self._activities) else None

    def _show_activity_menu(self, pos) -> None:
        """右键任意一条活动记录：直接修改或彻底删除。"""
        activity = self._activity_at(pos)
        if activity is None:
            return
        menu = build_activity_menu(
            self,
            activity,
            on_edit=self._edit_activity,
            on_delete=self._delete_activity,
            extras=(("跳转到该节点", lambda: self.openNodeRequested.emit(activity.node_id)),),
        )
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_row_selected(self) -> None:
        activity = self._current_activity()
        if activity is None:
            return
        clear_layout(self.detail_card.body)

        header = QHBoxLayout()
        header.addWidget(SectionHeader(f"{activity.activity_date} 的活动记录"))
        header.addStretch(1)
        self.detail_card.body.addLayout(header)

        info = QLabel(
            f"归属快照：{activity.path_text}\n"
            f"备注：{activity.note or '—'}\n"
            f"记录时间：{activity.created_at} · 记录 ID：{activity.id[:8]}"
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: {theme.FONT_SIZE - 1}px;")
        self.detail_card.body.addWidget(info)

        target = self.service.get_node(activity.node_id)
        if target is not None:
            self.detail_card.body.addWidget(
                Muted(
                    f"目标节点「{target.name}」当前归属：{self.service.path_text(target.id)}"
                    f"（状态 {target.status.value}）"
                )
            )
        else:  # pragma: no cover - 节点不会被硬删除
            self.detail_card.body.addWidget(Muted("目标节点已不存在，此记录仍保留快照。"))

        change_note = self._ownership_note(activity)
        if change_note:
            self.detail_card.body.addWidget(Muted(change_note))

        self.detail_card.body.addWidget(hline())
        self.detail_card.body.addWidget(Subtle("该节点的归属及关系变化历史（含本次记录前后的变更）"))
        history = EventHistoryList(
            self.service,
            activity.node_id,
            list_height=130,
            reference_date=activity.activity_date,
            description="标记 ◀ 的变动发生在这一天或更早。选中可删除历史事件，删除不影响活动与热力图。",
        )
        history.changed.connect(self._on_history_changed)
        self.detail_card.body.addWidget(history)

        buttons = QHBoxLayout()
        target_deleted = target is not None and target.is_deleted
        jump = QPushButton("查看已删除清单" if target_deleted else "跳转到该节点")
        if target_deleted:
            jump.clicked.connect(self._open_deleted_list)
        else:
            jump.clicked.connect(lambda: self.openNodeRequested.emit(activity.node_id))
        buttons.addWidget(jump)
        all_activities = QPushButton("查看该节点全部活动")
        all_activities.clicked.connect(lambda: self._show_node_activities(activity.node_id))
        buttons.addWidget(all_activities)
        edit = QPushButton("修改")
        edit.clicked.connect(lambda: self._edit_activity(activity))
        buttons.addWidget(edit)
        delete = QPushButton("删除")
        delete.clicked.connect(lambda: self._delete_activity(activity))
        buttons.addWidget(delete)
        buttons.addStretch(1)
        self.detail_card.body.addLayout(buttons)

    def _edit_activity(self, activity: Activity) -> None:
        from ..widgets.activity_edit import ActivityEditDialog

        dialog = ActivityEditDialog(self.service, self, activity)
        if dialog.exec() and dialog.saved:
            self.refresh()
            self.changed.emit()

    def _delete_activity(self, activity: Activity) -> None:
        if not confirm(
            self,
            "删除活动记录",
            f"彻底删除 {activity.activity_date} 的这条记录？\n\n"
            f"归属快照：{activity.path_text}\n备注：{activity.note or '（空）'}\n\n"
            "删除后数据库里不再保留这条记录，对应热力图当天会少一次，应用内无法恢复。",
            danger=True,
        ):
            return
        try:
            self.service.delete_activity(activity.id)
        except ValidationError as exc:
            warn(self, "无法删除", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def _open_deleted_list(self) -> None:
        from ..widgets.deleted_nodes import DeletedNodesDialog

        dialog = DeletedNodesDialog(self.service, self)
        dialog.changed.connect(lambda: self.changed.emit())
        dialog.exec()
        self.refresh()

    def _ownership_note(self, activity: Activity) -> str:
        events = [
            event
            for event in self.service.events_for_node(activity.node_id, limit=200)
            if event.event_type.value == "move"
        ]
        if not events:
            return ""
        latest = max(events, key=lambda event: event.created_at)
        if latest.created_at[:10] > activity.activity_date:
            return (
                f"这条记录发生在最近一次归属变更之前（变更于 {latest.created_at}），"
                f"因此按发生时的快照计入 {activity.path_text}。"
            )
        return (
            f"这条记录发生在最近一次归属变更之后（变更于 {latest.created_at}），"
            f"按快照计入 {activity.path_text}。"
        )

    def _show_node_activities(self, node_id: str) -> None:
        node = self.service.get_node(node_id)
        activities = self.service.list_activities(node_id=node_id)
        open_activity_dialog(
            self.service,
            self,
            title=f"{node.name} 的全部活动记录",
            subtitle=f"共 {len(activities)} 条，包含因为归属变更而分散在不同热力图中的记录。",
            loader=lambda: self.service.list_activities(node_id=node_id),
            empty_text="该节点还没有活动记录。",
            on_changed=self._on_activities_changed,
        )

    def _on_activities_changed(self) -> None:
        """弹窗里改了/删了活动：立刻刷新本页，并通知主窗口。"""
        self.refresh()
        self.changed.emit()

    def _on_history_changed(self) -> None:
        self._on_row_selected()
        self.changed.emit()
