"""通用界面组件：卡片、状态标签、节点选择器与对话框。"""

from __future__ import annotations

import logging

from PySide6.QtCore import QDate, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from .. import clock, theme
from ..models import Node, NodeType, Status, today_str
from ..services import LifeManagerService, ValidationError, describe_event

logger = logging.getLogger(__name__)


def qdate_to_str(value: QDate) -> str:
    return value.toString("yyyy-MM-dd")


def str_to_qdate(value: str) -> QDate:
    return QDate.fromString(value, "yyyy-MM-dd")


class Card(QFrame):
    """带 1px 边框与圆角的白底卡片。"""

    def __init__(self, parent: QWidget | None = None, name: str = "Card") -> None:
        super().__init__(parent)
        self.setObjectName(name)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(8)


def log_cursor(tag: str) -> None:
    """诊断用：记录鼠标指针当前位置。

    定位"开弹窗时指针像自己跳到按钮上"这类问题时，会在几个关键点各记一次，
    对比三段坐标就能知道到底是谁、在哪一步动了指针。
    """
    position = QCursor.pos()
    logger.info("[鼠标] %s → (%d,%d)", tag, position.x(), position.y())


class AppDialog(QDialog):
    """项目里所有自定义对话框的基类。

    作用只有一个：打开时**不把键盘焦点交给按钮**。Qt 默认会给默认按钮焦点，那个按钮就被高亮、
    被虚线框住，看起来像"光标跳到了按钮上"。这里把按钮的焦点策略清掉，焦点留给输入框或
    对话框本身；按钮照样能点，回车也照样触发默认按钮（那是另一套机制，不受影响）。
    """

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        for button in self.findChildren(QAbstractButton):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        focused = self.focusWidget()
        if focused is None or isinstance(focused, QAbstractButton):
            self.setFocus()
        # 诊断：定位"开弹窗时光标像是跳到了按钮上"。
        # 记下鼠标位置、鼠标底下到底是哪个控件、以及窗口落在哪 —— 一次就能看出
        # 是"指针被移走了"还是"窗口正好开在鼠标底下"。
        position = QCursor.pos()
        under = QApplication.widgetAt(position)
        if isinstance(under, QAbstractButton):
            under_text = f"{type(under).__name__}「{under.text()}」"
        elif under is not None:
            under_text = type(under).__name__
        else:
            under_text = "自己窗口之外"
        logger.info(
            "对话框显示：%s | 鼠标 (%d,%d) | 鼠标下：%s | 窗口 (%d,%d,%d,%d)",
            type(self).__name__,
            position.x(),
            position.y(),
            under_text,
            self.x(),
            self.y(),
            self.width(),
            self.height(),
        )
class SectionHeader(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("SectionHeader")


class BeijingClock(QWidget):
    """极简的实时北京时间：大字号时刻 + 一行日期。"""

    def __init__(self, parent: QWidget | None = None, *, time_size: int = 34) -> None:
        super().__init__(parent)
        self.time_size = time_size
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.time_label = QLabel()
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.time_label.setStyleSheet(
            f"font-size: {time_size}px; font-weight: 600; color: {theme.TEXT};"
        )
        self.date_label = Subtle("")
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.date_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 13px; background: transparent;"
        )
        self.zone_label = Subtle("")
        self.zone_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.time_label)
        layout.addWidget(self.date_label)
        layout.addWidget(self.zone_label)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh_time)
        self._timer.start()
        self.refresh_time()

    def refresh_time(self) -> None:
        moment, day = clock.display()
        self.time_label.setText(moment)
        self.date_label.setText(day)
        self.zone_label.setText(clock.BEIJING_LABEL)


class Muted(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Muted")
        self.setWordWrap(True)


class Subtle(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Subtle")


class StatusChip(QLabel):
    """状态标签：颜色只用于管理当前事项，不参与热力图统计。"""

    def __init__(self, status: Status, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.set_status(status)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_status(self, status: Status) -> None:
        foreground, background = theme.STATUS_STYLE[status.value]
        self.setText(status.value)
        self.setStyleSheet(
            f"color: {foreground}; background: {background}; border-radius: 10px;"
            f" padding: 1px 9px; font-size: 11px;"
        )


class NodeComboBox(QComboBox):
    """按 领域 → 进程 → 线程 层级展示的节点选择器。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumWidth(220)

    def populate(
        self,
        service: LifeManagerService,
        kinds: tuple[NodeType, ...] = (NodeType.PROCESS, NodeType.THREAD),
        *,
        include_archived: bool = False,
        selected_id: str | None = None,
    ) -> None:
        self.clear()
        for area in service.areas():
            if area.is_archived and not include_archived:
                continue
            area_rows: list[tuple[str, Node]] = []
            for process in service.processes(area.id):
                process_rows: list[tuple[str, Node]] = []
                if NodeType.PROCESS in kinds and (include_archived or not process.is_archived):
                    process_rows.append((f"{process.name}", process))
                if NodeType.THREAD in kinds:
                    for thread in service.threads(process.id):
                        if thread.is_archived and not include_archived:
                            continue
                        process_rows.append((f"    {thread.name}", thread))
                if process_rows:
                    area_rows.extend(process_rows)
            # 要选"领域"本身时（新建进程 / 移动进程），领域必须是可选条目；
            # 否则领域只作为不可选的分组标题，用来给下面的进程与线程分组。
            if NodeType.AREA in kinds:
                self.addItem(area.name, area.id)
            elif area_rows:
                self.addItem(area.name)
                index = self.count() - 1
                model_item = self.model().item(index)
                if model_item is not None:
                    model_item.setEnabled(False)
            for text, node in area_rows:
                self.addItem(f"  {text}", node.id)
        if selected_id:
            self.set_current_node(selected_id)
        elif self.count():
            # 默认选中第一个可选项，避免停在「领域」这类不可选的分组标题上。
            for index in range(self.count()):
                model_item = self.model().item(index)
                if model_item is not None and model_item.isEnabled():
                    self.setCurrentIndex(index)
                    break

    def set_current_node(self, node_id: str) -> None:
        index = self.findData(node_id)
        if index >= 0:
            self.setCurrentIndex(index)

    def current_node_id(self) -> str | None:
        return self.currentData()


# 表格单元格上的自定义标记：这一行是不是"某一天的第一行"（那天第一行的行高会更大）
NEW_DAY_ROLE = Qt.ItemDataRole.UserRole + 1
# 每天第一行需要整体下移多少像素（多出来的行高就是与上一天之间的空隙）
DAY_GAP_ROLE = Qt.ItemDataRole.UserRole + 2


class _DayStartDelegate(QStyledItemDelegate):
    """每天第一行：把整格内容下移，多出来的高度成为与上一天之间的空隙。

    不能用"文字贴底"来做：Qt 贴底自带的内边距比正常行小，
    会让这一行和当天的第二条贴得比正常行距更近。
    整体平移才能让行距完全一致，空隙和细线都留在上方。
    """

    def paint(self, painter, option, index) -> None:  # pragma: no cover - 需要 GUI
        extra = index.data(DAY_GAP_ROLE) or 0
        if not extra:
            super().paint(painter, option, index)
            return
        shifted = QStyleOptionViewItem(option)
        shifted.rect = shifted.rect.adjusted(0, int(extra), 0, 0)
        super().paint(painter, shifted, index)


class ActivityTable(QTableWidget):
    """活动记录表格（只读）。"""

    HEADERS = ("日期", "归属快照", "记在哪", "备注", "记录时间")
    # 每天的第一行略高一点，并在它上方画一条内缩的细线：
    # 只靠"加高"分天时，文字无论居中还是贴底，读起来都会有一条记录
    # 跟当天其它记录不在一个节奏上。空隙 + 细线才稳。
    DAY_ROW_HEIGHT = 40
    ROW_HEIGHT = 25
    DIVIDER_INSET = 12
    DIVIDER_OFFSET = 7

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(self.HEADERS), parent)
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setAlternatingRowColors(False)
        self.setItemDelegate(_DayStartDelegate(self))

    def load(self, activities, *, compact: bool = False) -> None:
        from ..models import NodeType

        self.setRowCount(0)
        previous_day = None
        for activity in activities:
            row = self.rowCount()
            self.insertRow(row)
            new_day = activity.activity_date != previous_day
            previous_day = activity.activity_date
            target = "（进程）" if activity.node_type is NodeType.PROCESS else "（线程）"
            cells = (
                activity.activity_date,
                activity.path_text,
                target,
                activity.note or "—",
                activity.created_at,
            )
            day_start = bool(new_day and row > 0)
            for column, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.ItemDataRole.UserRole, activity.id)
                # 第一行不画线（它上面就是表头）；之后每天的第一行才画
                item.setData(NEW_DAY_ROLE, day_start)
                # 整格下移，让这一行的文字仍落在标准行高里（行距与其它行一致）
                item.setData(
                    DAY_GAP_ROLE, self.DAY_ROW_HEIGHT - self.ROW_HEIGHT if day_start else 0
                )
                self.setItem(row, column, item)
            # 靠"空隙 + 细线"把天与天分开：同一天里行高保持一致
            self.setRowHeight(row, self.DAY_ROW_HEIGHT if day_start else self.ROW_HEIGHT)
        self._fit_columns(compact)

    def paintEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        super().paintEvent(event)
        if self.rowCount() == 0:
            return
        painter = QPainter(self.viewport())
        pen = QPen(QColor(theme.BORDER))
        pen.setWidth(1)
        painter.setPen(pen)
        left = self.DIVIDER_INSET
        right = max(left, self.viewport().width() - self.DIVIDER_INSET)
        height = self.viewport().height()
        gap = self.DAY_ROW_HEIGHT - self.ROW_HEIGHT
        background = QColor(theme.BG)
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item is None or not item.data(NEW_DAY_ROLE):
                continue
            top = self.rowViewportPosition(row)
            if top < -self.DAY_ROW_HEIGHT or top > height:
                continue
            if gap > 0:
                # 这一行的上半截只是空隙：选中时不能跟着变蓝，
                # 否则整条分隔线会像是被一起选中了。
                painter.fillRect(0, top, self.viewport().width(), gap, background)
            y = top + self.DIVIDER_OFFSET
            painter.drawLine(left, y, right, y)
        painter.end()

    def _fit_columns(self, compact: bool) -> None:
        from PySide6.QtWidgets import QHeaderView

        header = self.horizontalHeader()
        widths = (90, 260, 70, 320, 150) if not compact else (90, 220, 60, 240, 0)
        for column, width in enumerate(widths):
            if width == 0:
                self.setColumnHidden(column, True)
                continue
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.setColumnWidth(column, width)
        header.setStretchLastSection(True)


class ActivityListView(QWidget):
    """活动记录清单本体：可查看、可修改、可彻底删除。

    单独抽成一个普通控件，是为了既能包成弹窗（ActivityListDialog），
    也能直接嵌进别的窗口里（例如热力图大图下面的那块）。
    """

    changed = Signal()

    def __init__(
        self,
        service: LifeManagerService,
        parent: QWidget | None = None,
        *,
        activities: list | None = None,
        loader=None,
        empty_text: str = "当日没有活动记录。",
        hint: str = "删除是彻底删除、不留存；对应热力图当天会少一次。",
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._loader = loader or (lambda: list(activities or []))
        self._activities: list = []
        self.empty_text = empty_text

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.table = ActivityTable()
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_activity_menu)
        layout.addWidget(self.table, 1)

        self.empty_label = Muted(empty_text)
        layout.addWidget(self.empty_label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.edit_button = QPushButton("修改选中")
        self.edit_button.clicked.connect(self._edit_selected)
        self.delete_button = QPushButton("删除选中")
        self.delete_button.clicked.connect(self._delete_selected)
        row.addWidget(self.edit_button)
        row.addWidget(self.delete_button)
        row.addWidget(Subtle(hint))
        row.addStretch(1)
        layout.addLayout(row)
        self.reload()

    # ---------------------------------------------------------------- 数据
    @property
    def activities(self) -> list:
        return list(self._activities)

    def set_loader(self, loader) -> None:
        self._loader = loader or (lambda: [])

    def set_empty_text(self, text: str) -> None:
        self.empty_text = text
        self.empty_label.setText(text)

    # ---------------------------------------------------------------- 数据
    def reload(self) -> None:
        self._activities = list(self._loader())
        self.table.load(self._activities)
        self.empty_label.setVisible(not self._activities)
        self.table.setVisible(bool(self._activities))
        self._sync_buttons()

    def _selected_activity(self):
        rows = {index.row() for index in self.table.selectedItems()}
        if not rows:
            return None
        row = sorted(rows)[0]
        return self._activities[row] if 0 <= row < len(self._activities) else None

    def _sync_buttons(self) -> None:
        has_selection = self._selected_activity() is not None
        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    def _activity_at(self, pos):
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
            on_edit=lambda _activity: self._edit_selected(),
            on_delete=lambda _activity: self._delete_selected(),
        )
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ---------------------------------------------------------------- 操作
    def _edit_selected(self) -> None:
        activity = self._selected_activity()
        if activity is None:
            return
        from .activity_edit import ActivityEditDialog

        dialog = ActivityEditDialog(self.service, self, activity)
        if dialog.exec() and dialog.saved:
            self.reload()
            self.changed.emit()

    def _delete_selected(self) -> None:
        activity = self._selected_activity()
        if activity is None:
            return
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
        self.reload()
        self.changed.emit()


class ActivityListDialog(AppDialog):
    """活动记录清单弹窗：把 ActivityListView 包成一个独立窗口。"""

    changed = Signal()

    def __init__(
        self,
        service: LifeManagerService,
        parent: QWidget,
        *,
        title: str,
        subtitle: str,
        activities: list | None = None,
        loader=None,
        empty_text: str = "当日没有活动记录。",
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle(title)
        self.resize(880, 460)
        self.empty_text = empty_text

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(SectionHeader(title))
        if subtitle:
            layout.addWidget(Muted(subtitle))

        self.view = ActivityListView(
            service,
            self,
            activities=activities,
            loader=loader,
            empty_text=empty_text,
        )
        self.view.changed.connect(self.changed.emit)
        layout.addWidget(self.view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # ------------------------------------------ 兼容旧调用（都转发给内部控件）
    @property
    def table(self) -> ActivityTable:
        return self.view.table

    @property
    def empty_label(self):
        return self.view.empty_label

    def reload(self) -> None:
        self.view.reload()

    def _activity_at(self, pos):
        return self.view._activity_at(pos)

    def _edit_selected(self) -> None:
        self.view._edit_selected()

    def _delete_selected(self) -> None:
        self.view._delete_selected()


class QuickRecordDialog(AppDialog):
    """记录活动：选择 Process 或 Thread、输入可选备注即可完成。

    lock_node=True 时锁定到指定节点（思维导图点击卡片后使用），
    只有点了「记录」才会写入数据。
    """

    def __init__(
        self,
        service: LifeManagerService,
        parent: QWidget | None = None,
        *,
        preset_node_id: str | None = None,
        preset_date: str | None = None,
        lock_node: bool = False,
        title: str = "记录一次活动",
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.recorded = None
        locked_node = service.require_node(preset_node_id) if (lock_node and preset_node_id) else None
        self.setWindowTitle("记录一次活动")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        heading = f"{title} · {locked_node.name}" if locked_node else title
        layout.addWidget(SectionHeader(heading))
        if locked_node is not None:
            layout.addWidget(Muted(f"归属快照：{service.path_text(locked_node.id)}"))
            layout.addWidget(Subtle("点「记录」才会生成一条数据；不想记就点取消，不会有任何变化。"))
        else:
            layout.addWidget(Muted("选择这条活动记在哪个进程或线程上。归属快照按记录时的事实保存。"))

        form = QVBoxLayout()
        form.setSpacing(6)
        if locked_node is None:
            form.addWidget(QLabel("目标节点"))
        self.node_combo = NodeComboBox()
        self.node_combo.populate(service, selected_id=preset_node_id)
        self.node_combo.setVisible(locked_node is None)
        form.addWidget(self.node_combo)

        row = QHBoxLayout()
        row.setSpacing(10)
        date_box = QVBoxLayout()
        date_box.setSpacing(6)
        date_box.addWidget(QLabel("活动日期"))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(str_to_qdate(preset_date or today_str()))
        date_box.addWidget(self.date_edit)
        row.addLayout(date_box)
        note_box = QVBoxLayout()
        note_box.setSpacing(6)
        note_box.addWidget(QLabel("备注（可留空）"))
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("例如：背单词 30 分钟")
        note_box.addWidget(self.note_edit)
        row.addLayout(note_box, 1)
        form.addLayout(row)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {theme.DANGER}; font-size: 12px;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox()
        record_button = buttons.addButton("记录", QDialogButtonBox.ButtonRole.AcceptRole)
        record_button.setObjectName("Primary")
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_record)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_record(self) -> None:
        node_id = self.node_combo.current_node_id()
        if not node_id:
            self._show_error("请选择一个进程或线程。")
            return
        try:
            self.recorded = self.service.record_activity(
                node_id, self.note_edit.text(), qdate_to_str(self.date_edit.date())
            )
        except ValidationError as exc:
            self._show_error(str(exc))
            return
        self.accept()

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(True)


class ChooseNodeDialog(AppDialog):
    """从某一类节点中选择一个（用于「移动到」）。"""

    def __init__(
        self,
        service: LifeManagerService,
        parent: QWidget | None,
        *,
        title: str,
        prompt: str,
        kinds: tuple[NodeType, ...],
        exclude_ids: set[str] | None = None,
        confirm_text: str = "确定",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self._excluded = exclude_ids or set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        layout.addWidget(SectionHeader(title))
        if prompt:
            layout.addWidget(Muted(prompt))
        self.combo = NodeComboBox()
        self.combo.populate(service, kinds)
        layout.addWidget(self.combo)
        buttons = QDialogButtonBox()
        ok_button = buttons.addButton(confirm_text, QDialogButtonBox.ButtonRole.AcceptRole)
        ok_button.setObjectName("Primary")
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_node_id(self) -> str | None:
        return self.combo.current_node_id()


class EventHistoryList(QWidget):
    """归属及关系变化历史列表，支持多选删除。

    删除只作用于历史事件本身：活动记录、归属快照和热力图统计都不受影响。
    """

    changed = Signal()

    def __init__(
        self,
        service: LifeManagerService,
        node_id: str | None = None,
        parent: QWidget | None = None,
        *,
        limit: int = 40,
        list_height: int = 160,
        description: str | None = None,
        reference_date: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.node_id = node_id
        self.limit = limit
        self.reference_date = reference_date

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.listing = QListWidget()
        self.listing.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.listing.setMinimumHeight(list_height)
        layout.addWidget(self.listing)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.hint = Subtle(
            description
            or "选中后可删除历史事件（只影响这条记录，活动与热力图不变；删了不能单独恢复）。"
        )
        row.addWidget(self.hint, 1)
        self.delete_button = QPushButton("删除选中")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._delete_selected)
        row.addWidget(self.delete_button)
        layout.addLayout(row)

        self.listing.itemSelectionChanged.connect(
            lambda: self.delete_button.setEnabled(bool(self.listing.selectedItems()))
        )
        self.reload()

    # ---------------------------------------------------------------- 数据
    def set_node(self, node_id: str | None) -> None:
        self.node_id = node_id
        self.reload()

    def reload(self) -> None:
        self.listing.clear()
        if not self.node_id:
            self.listing.addItem(QListWidgetItem("选择一个节点后显示它的变化历史。"))
            self.listing.setEnabled(False)
            return
        self.listing.setEnabled(True)
        events = self.service.events_for_node(self.node_id, limit=self.limit)
        if not events:
            self.listing.addItem(QListWidgetItem("该节点还没有变化记录。"))
            return
        for event in events:
            marker = ""
            if self.reference_date and event.created_at[:10] <= self.reference_date:
                marker = "◀ "
            text = f"{marker}{event.created_at} · {event.event_type.label} · {describe_event(event)}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, event.id)
            item.setToolTip(describe_event(event))
            self.listing.addItem(item)

    # ---------------------------------------------------------------- 操作
    def selected_event_ids(self) -> list[str]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.listing.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole)
        ]

    def _delete_selected(self) -> None:
        event_ids = self.selected_event_ids()
        if not event_ids:
            return
        if not confirm(
            self,
            "删除历史事件",
            f"删除选中的 {len(event_ids)} 条历史事件？\n\n"
            "只会删除这些变化记录本身：活动记录、归属快照与热力图统计都不会改变。\n"
            "这些事件会从数据库里彻底删除，应用内无法单独恢复；"
            "只有用「删除之前」创建的整库备份做恢复才可能找回，而那会把其它数据一起回退。",
            danger=True,
        ):
            return
        try:
            self.service.delete_events(event_ids)
        except ValidationError as exc:
            warn(self, "无法删除", str(exc))
            return
        self.reload()
        self.changed.emit()


class AssociationDialog(AppDialog):
    """管理一条 Thread 的关联 Process（建立与解除，均写入历史）。"""

    def __init__(self, service: LifeManagerService, parent: QWidget | None, thread: Node) -> None:
        super().__init__(parent)
        self.service = service
        self.thread = thread
        self.setWindowTitle("关联进程")
        self.resize(460, 480)
        self.changed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(SectionHeader(f"「{thread.name}」关联的进程"))
        layout.addWidget(
            Muted("关联只用于表达联系：不改变主要归属，不复制活动，也不影响任何热力图统计。")
        )

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)

        self.hint = Subtle("勾选表示建立关联，取消勾选表示解除关联。")
        layout.addWidget(self.hint)

        buttons = QDialogButtonBox()
        save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        save_button.setObjectName("Primary")
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load()

    def _load(self) -> None:
        self.list_widget.clear()
        linked = {node.id: True for node in self.service.processes_for_thread(self.thread.id)}
        for area in self.service.areas():
            for process in self.service.processes(area.id):
                item = QListWidgetItem(f"{area.name} → {process.name}")
                item.setData(Qt.ItemDataRole.UserRole, process.id)
                flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                if process.id == self.thread.parent_id:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    item.setCheckState(Qt.CheckState.Checked)
                    item.setText(f"{area.name} → {process.name}（主要归属）")
                else:
                    item.setFlags(flags)
                    item.setCheckState(
                        Qt.CheckState.Checked if process.id in linked else Qt.CheckState.Unchecked
                    )
                self.list_widget.addItem(item)

    def _save(self) -> None:
        target: dict[str, bool] = {}
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            process_id = item.data(Qt.ItemDataRole.UserRole)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                target[process_id] = item.checkState() == Qt.CheckState.Checked
        current = {node.id for node in self.service.processes_for_thread(self.thread.id)}
        errors: list[str] = []
        for process_id, wanted in target.items():
            try:
                if wanted and process_id not in current:
                    self.service.associate(self.thread.id, process_id)
                    self.changed = True
                elif not wanted and process_id in current:
                    self.service.dissociate(self.thread.id, process_id)
                    self.changed = True
            except ValidationError as exc:
                errors.append(str(exc))
        if errors:
            QMessageBox.warning(self, "部分操作未完成", "\n".join(errors))
        self.accept()


def open_activity_dialog(
    service: LifeManagerService,
    parent: QWidget,
    *,
    title: str,
    subtitle: str,
    loader,
    empty_text: str = "当日没有活动记录。",
    on_changed=None,
) -> None:
    """打开活动清单弹窗。

    两个容易出错的地方在这里统一处理：
    1. 弹窗里的列表用 loader 现场查询（不能用一次性快照，否则改/删后列表不更新）；
    2. 弹窗里改/删之后通过 changed 通知调用方刷新自己的页面。
    """
    dialog = ActivityListDialog(
        service,
        parent,
        title=title,
        subtitle=subtitle,
        loader=loader,
        empty_text=empty_text,
    )
    if on_changed is not None:
        dialog.changed.connect(on_changed)
    dialog.exec()


def build_activity_menu(
    parent: QWidget,
    activity,
    *,
    on_edit,
    on_delete,
    extras: tuple[tuple[str, object], ...] = (),
) -> QMenu:
    """活动记录的统一右键菜单：修改备注与日期 / 彻底删除（+ 可选附加项）。"""
    menu = QMenu(parent)

    def add(label: str, handler) -> None:
        action = QAction(label, menu)
        action.triggered.connect(lambda _checked=False, h=handler: h())
        menu.addAction(action)

    add("修改备注与日期…", lambda: on_edit(activity))
    add("彻底删除这条记录…", lambda: on_delete(activity))
    for label, handler in extras:
        menu.addSeparator()
        add(label, handler)
    return menu


def run_move_dialog(service: LifeManagerService, parent: QWidget, node: Node) -> bool:
    """「移动到…」完整流程：选择新上级 → 确认 → 执行。返回是否真的移动了。"""
    if node.type is NodeType.AREA:
        warn(parent, "无法移动", "领域是顶层节点，不能改变归属。")
        return False
    target_type = node.type.parent_type
    dialog = ChooseNodeDialog(
        service,
        parent,
        title=f"移动「{node.name}」",
        prompt=(
            f"选择新的{target_type.label}。移动会记录变更前后的归属，"
            "旧活动仍然留在原来的归属统计里。"
        ),
        kinds=(target_type,),
        confirm_text="移动",
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    new_parent_id = dialog.selected_node_id()
    if not new_parent_id or new_parent_id == node.parent_id:
        return False
    if not confirm(
        parent,
        "确认移动",
        f"把「{node.name}」移动到「{service.path_text(new_parent_id)}」下？\n\n"
        "移动前的历史活动仍按发生时的归属统计，不会被改写。",
    ):
        return False
    try:
        service.move_node(node.id, new_parent_id)
    except ValidationError as exc:
        warn(parent, "无法移动", str(exc))
        return False
    return True


def run_delete_node(service: LifeManagerService, parent: QWidget, node: Node) -> bool:
    """「删除」完整流程：移入已删除清单（数据保留、可恢复）。"""
    removed = [item for item in service.subtree(node.id) if not item.is_deleted]
    activity_total = sum(service.activity_count_for_node(item.id) for item in removed)
    extra = (
        f"\n它下面的 {len(removed) - 1} 个节点也会一起移入清单。"
        if len(removed) > 1
        else ""
    )
    if not confirm(
        parent,
        "移入已删除清单",
        f"把「{node.name}」移入「已删除」清单？{extra}\n\n"
        f"它名下的 {activity_total} 条活动记录、热力图统计和历史事件都会保留，"
        "在「已删除…」里随时可以查看并恢复。\n\n"
        "如果只是想暂时收起来、又想在树里看得见，请改用「归档」。",
        danger=True,
    ):
        return False
    try:
        service.delete_node(node.id)
    except ValidationError as exc:
        warn(parent, "无法删除", str(exc))
        return False
    return True


class _MessageBox(QMessageBox):
    """我们的提示框：**不让按钮抢焦点**。

    默认的 QMessageBox 一打开就把键盘焦点交给默认按钮，视觉上那个按钮会被高亮/虚线框住，
    看起来像"鼠标或光标跳到了按钮上"。这里在显示时把按钮的焦点策略清掉、把焦点留在框本身；
    回车仍然会触发默认按钮，所以"按回车关掉"照旧好用。
    """

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        for button in self.buttons():
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFocus()

    def exec(self) -> int:
        # 诊断：万一还有"鼠标自己乱跳"的情况，这里会留下一行日志（正常时不会写任何东西）。
        before = QCursor.pos()
        code = super().exec()
        after = QCursor.pos()
        if before != after:
            logger.warning(
                "弹窗期间鼠标位置从 (%d,%d) 变到 (%d,%d)：%s",
                before.x(),
                before.y(),
                after.x(),
                after.y(),
                self.windowTitle(),
            )
        return code


def confirm(parent: QWidget, title: str, text: str, *, danger: bool = False) -> bool:
    """确认框。

    `danger=True`（删除这类不可撤销的操作）时，**默认按钮是「取消」** ——
    打开窗口时焦点不会落在「确定」上，免得顺手一个回车就把东西删了。
    """
    box = _MessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    yes = box.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel if danger else yes)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is yes


def info(parent: QWidget, title: str, text: str) -> None:
    box = _MessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Information)
    button = box.addButton("好", QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(button)
    box.exec()


def warn(parent: QWidget, title: str, text: str) -> None:
    box = _MessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning)
    button = box.addButton("好", QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(button)
    box.exec()


def clear_layout(layout) -> None:
    """立即清空布局中的控件。

    只调用 deleteLater() 不够：控件会一直存活到事件循环处理删除事件，
    期间它仍以默认几何（0,0,640,480）参与绘制，会盖住其它控件。
    因此这里先把控件从父级摘除，再安排销毁。
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child_layout = item.layout()
        if child_layout is not None:
            clear_layout(child_layout)


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color: {theme.BORDER_MUTED}; background: {theme.BORDER_MUTED}; max-height: 1px;")
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line
