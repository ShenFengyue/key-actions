"""页面 B：进程树 Processes。

树状列表展示全部 Area / Process / Thread，右侧详情展示归属、状态、关联、
下一步行动、近期活动与归属变更历史。移动使用明确的「移动到」操作。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..models import Node, NodeType, STATUS_CHOICES, Status
from ..services import LifeManagerService, ValidationError
from ..widgets.common import (
    ActivityTable,
    AssociationDialog,
    Card,
    ChooseNodeDialog,
    EventHistoryList,
    Muted,
    QuickRecordDialog,
    SectionHeader,
    StatusChip,
    Subtle,
    clear_layout,
    run_delete_node,
    run_move_dialog,
    warn,
)

# ------------------------------------------------------------------ 间距分层
# 纯间距表达层级：三级给三档行高，缩进加深，领域之间再补一条内缩的分割线。
ROW_HEIGHTS = {
    NodeType.AREA: 40,
    NodeType.PROCESS: 32,
    NodeType.THREAD: 26,
}
TREE_INDENT = 26
SPLITTER_HANDLE_WIDTH = 12


def should_draw_area_divider(index) -> bool:
    """领域之间的分割线：只画在「第二个及以后」的领域行顶部。"""
    if index is None or index.parent().isValid():
        return False
    return index.row() > 0


class AreaDividerDelegate(QStyledItemDelegate):
    """在领域行上方画一条两端不顶头的浅灰分割线。

    线的两端各留一点内缩，不贴着树的两边，看起来才像是"分组边界"而不是表格框线。
    """

    LEFT_INSET = 10
    RIGHT_INSET = 14

    def paint(self, painter, option, index) -> None:  # pragma: no cover - 需要 GUI
        super().paint(painter, option, index)
        if not should_draw_area_divider(index):
            return
        model = index.model()
        last_column = model.columnCount() - 1 if model is not None else index.column()
        rect = option.rect
        left = rect.left() + (self.LEFT_INSET if index.column() == 0 else 0)
        right = rect.right() + (
            1 if index.column() != last_column else -self.RIGHT_INSET
        )
        if right <= left:
            return
        painter.save()
        painter.setPen(QPen(QColor(theme.BORDER)))
        y = rect.top()
        painter.drawLine(int(left), int(y), int(right), int(y))
        painter.restore()


class NodeTree(QTreeWidget):
    """进程树本体：允许**同一父级内**拖动调整顺序。

    跨层级（把一个线程拖进另一个进程）在这里会被拒绝 —— 那是「移动到…」的活，
    因为它改变归属、要写历史事件。拖动结束后发出 orderChanged，由页面写回数据库。
    """

    orderChanged = Signal()

    def can_drop(self, source, target, indicator) -> bool:
        """这次拖放是否允许：只允许同一父级内调整顺序。"""
        if source is None or target is None or source is target:
            return False
        # "放进某个节点里" = 改归属，交给「移动到…」
        if indicator == QAbstractItemView.DropIndicatorPosition.OnItem:
            return False
        return source.parent() is target.parent()

    def dropEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        selected = self.selectedItems()
        target = self.itemAt(event.position().toPoint())
        if len(selected) != 1 or not self.can_drop(
            selected[0] if selected else None, target, self.dropIndicatorPosition()
        ):
            event.ignore()
            return
        super().dropEvent(event)
        self.orderChanged.emit()


class ProcessesPage(QWidget):
    changed = Signal()
    openActivityRequested = Signal(str)

    def __init__(self, service: LifeManagerService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._selected_id: str | None = None
        self._expanded: set[str] = set()
        self._building = False
        self._initialized = False
        self._tree_signature_cache: tuple | None = None
        self._details_signature_cache: tuple | None = None
        self._items: dict[str, QTreeWidgetItem] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self._build_toolbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(SPLITTER_HANDLE_WIDTH)
        splitter.addWidget(self._build_tree())
        splitter.addWidget(self._build_details())
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([760, 480])
        layout.addWidget(splitter, 1)

        self.refresh()

    # -------------------------------------------------------------- 工具栏
    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.new_button = QPushButton("新建节点 ▾")
        new_menu = QMenu(self.new_button)
        for node_type, label in (
            (NodeType.AREA, "新建领域（Area）"),
            (NodeType.PROCESS, "新建进程（Process）"),
            (NodeType.THREAD, "新建线程（Thread）"),
        ):
            action = QAction(label, new_menu)
            action.triggered.connect(lambda _checked=False, t=node_type: self._create_node(t))
            new_menu.addAction(action)
        self.new_button.setMenu(new_menu)
        row.addWidget(self.new_button)

        for label, handler in (
            ("重命名", self._rename_selected),
            ("移动到…", self._move_selected),
            ("上移一位", lambda: self._move_selected_order(-1)),
            ("下移一位", lambda: self._move_selected_order(1)),
            ("关联…", self._manage_associations),
            ("置顶", self._toggle_pin),
            ("记录活动", self._record_for_selected),
            ("删除", self._delete_selected),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            row.addWidget(button)

        self.status_button = QPushButton("状态 ▾")
        status_menu = QMenu(self.status_button)
        for status in STATUS_CHOICES:
            action = QAction(status.value, status_menu)
            action.triggered.connect(lambda _checked=False, s=status: self._set_status(s))
            status_menu.addAction(action)
        self.status_button.setMenu(status_menu)
        row.addWidget(self.status_button)

        row.addStretch(1)

        self.deleted_button = QPushButton("已删除…")
        self.deleted_button.setToolTip("查看已删除的节点并恢复（数据一直保留）")
        self.deleted_button.clicked.connect(self._open_deleted_list)
        row.addWidget(self.deleted_button)

        self.archive_toggle = QCheckBox("显示已归档")
        self.archive_toggle.stateChanged.connect(lambda _state: self.refresh())
        row.addWidget(self.archive_toggle)

        expand_button = QPushButton("展开全部")
        expand_button.clicked.connect(self._expand_all)
        collapse_button = QPushButton("折叠全部")
        collapse_button.clicked.connect(self._collapse_all)
        row.addWidget(expand_button)
        row.addWidget(collapse_button)
        return bar

    # ---------------------------------------------------------------- 树
    def _build_tree(self) -> QWidget:
        self.tree = NodeTree()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(("名称", "状态", "下一步行动", "关联", "重点"))
        self.tree.setColumnWidth(0, 260)
        self.tree.setColumnWidth(1, 76)
        self.tree.setColumnWidth(2, 220)
        self.tree.setColumnWidth(3, 190)
        self.tree.setColumnWidth(4, 48)
        # 三级不同行高，所以要关掉"等高行"优化
        self.tree.setUniformRowHeights(False)
        self.tree.setIndentation(TREE_INDENT)
        self.tree.setItemDelegate(AreaDividerDelegate(self.tree))
        self.tree.setAnimated(True)
        # 同一层内可以拖动调整顺序（跨层级会被拒绝，见 NodeTree.dropEvent）
        self.tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setDropIndicatorShown(True)
        self.tree.orderChanged.connect(self._on_tree_order_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemExpanded.connect(self._on_expanded)
        self.tree.itemCollapsed.connect(self._on_collapsed)
        self.tree.itemDoubleClicked.connect(lambda *_: self._record_for_selected())
        return self.tree

    # ------------------------------------------------------------ 调整顺序
    def _on_tree_order_changed(self) -> None:
        """拖拽结束：把树里的新顺序写进数据库，然后重建树。

        内部拖拽会让 Qt 重建 QTreeWidgetItem，所以这里必须整体重建一次，
        否则页面里按 id 记的映射表会指向已经不存在的旧对象。
        """
        self._apply_tree_order()
        self.refresh(force=True)
        self.changed.emit()

    def _apply_tree_order(self) -> None:
        """把树里当前的显示顺序写回数据库（只改 position）。"""

        def walk(parent_item: QTreeWidgetItem | None, parent_id: str | None) -> None:
            count = (
                parent_item.childCount()
                if parent_item is not None
                else self.tree.topLevelItemCount()
            )
            children: list[tuple[QTreeWidgetItem, str]] = []
            for index in range(count):
                item = (
                    parent_item.child(index)
                    if parent_item is not None
                    else self.tree.topLevelItem(index)
                )
                children.append((item, item.data(0, Qt.ItemDataRole.UserRole)))
            if children:
                self.service.apply_child_order(
                    parent_id, [node_id for _item, node_id in children]
                )
            for item, node_id in children:
                walk(item, node_id)

        walk(None, None)

    def _move_selected_order(self, step: int) -> None:
        """把选中的节点在同一层里上移或下移一位（右键菜单用）。"""
        item = self.tree.currentItem()
        if item is None:
            return
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        parent = item.parent()
        parent_id = (
            parent.data(0, Qt.ItemDataRole.UserRole) if parent is not None else None
        )
        siblings = [node.id for node in self.service.children(parent_id)]
        if node_id not in siblings:
            return
        index = siblings.index(node_id)
        target = index + step
        if not 0 <= target < len(siblings):
            return
        siblings.pop(index)
        siblings.insert(target, node_id)
        self.service.apply_child_order(parent_id, siblings)
        self.refresh(force=True)
        self.changed.emit()

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        if not self._building:
            self._expanded.add(item.data(0, Qt.ItemDataRole.UserRole))

    def _on_collapsed(self, item: QTreeWidgetItem) -> None:
        if not self._building:
            self._expanded.discard(item.data(0, Qt.ItemDataRole.UserRole))

    def _expand_all(self) -> None:
        self.tree.expandAll()
        self._expanded = {node.id for node in self.service.all_nodes() if node.type is not NodeType.THREAD}

    def _collapse_all(self) -> None:
        self.tree.collapseAll()
        self._expanded.clear()

    def _build_item(self, node: Node) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(0, Qt.ItemDataRole.UserRole, node.id)
        item.setSizeHint(0, QSize(0, ROW_HEIGHTS[node.type]))
        item.setText(0, node.name)
        if node.type is NodeType.AREA:
            font = QFont()
            font.setBold(True)
            item.setFont(0, font)
            item.setText(1, "—")
            item.setText(2, "—")
            threads = sum(len(self.service.threads(p.id)) for p in self.service.processes(node.id))
            item.setText(3, f"{len(self.service.processes(node.id))} 个进程 · {threads} 条线程")
        elif node.type is NodeType.PROCESS:
            item.setText(1, node.status.value)
            item.setText(2, node.next_action or "—")
            linked = self.service.threads_for_process(node.id)
            item.setText(3, "、".join(thread.name for thread in linked) if linked else "—")
        else:
            item.setText(1, node.status.value)
            item.setText(2, node.next_action or "—")
            linked = self.service.processes_for_thread(node.id)
            item.setText(3, "、".join(process.name for process in linked) if linked else "—")
        item.setText(4, "★" if node.pinned else "")
        if node.is_archived:
            for column in range(5):
                item.setForeground(column, Qt.GlobalColor.gray)
        if node.pinned:
            item.setForeground(4, Qt.GlobalColor.darkYellow)
        return item

    def refresh(self, force: bool = False) -> None:
        """刷新进程树。

        结构没变时直接跳过重建：状态切换、记录活动这类操作不会再让整棵树重绘，
        也就不会出现窗口闪烁。
        """
        signature = self._tree_signature()
        if force or signature != self._tree_signature_cache:
            self._tree_signature_cache = signature
            self._rebuild_tree()
        count = self.service.deleted_entry_count()
        self.deleted_button.setText(f"已删除（{count}）…" if count else "已删除…")
        self._refresh_details()

    def _tree_signature(self) -> tuple:
        nodes = tuple(
            (
                node.id,
                node.parent_id,
                node.position,
                node.name,
                node.status.value,
                int(node.pinned),
                node.next_action,
            )
            for node in self.service.all_nodes()
        )
        associations = tuple(
            sorted((item.thread_id, item.process_id) for item in self.service.active_associations())
        )
        return (self.archive_toggle.isChecked(), nodes, associations)

    def _rebuild_tree(self) -> None:
        include_archived = self.archive_toggle.isChecked()
        # 重建期间关闭重绘与动画，避免中间状态被画出来（窗口闪烁的根源）。
        self.tree.setUpdatesEnabled(False)
        self.tree.setAnimated(False)
        scroll_position = self.tree.verticalScrollBar().value()
        first_build = not self._initialized
        self._building = True
        self.tree.clear()
        self._items.clear()
        selected_item: QTreeWidgetItem | None = None

        def add_children(parent_item: QTreeWidgetItem | None, parent_id: str | None) -> None:
            for child in self.service.children(parent_id):
                if child.is_archived and not include_archived:
                    continue
                item = self._build_item(child)
                self._items[child.id] = item
                if parent_item is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)
                add_children(item, child.id)

        add_children(None, None)
        self._building = False

        def restore(item: QTreeWidgetItem) -> None:
            nonlocal selected_item
            node_id = item.data(0, Qt.ItemDataRole.UserRole)
            if node_id in self._expanded:
                item.setExpanded(True)
            if node_id == self._selected_id:
                selected_item = item
            for index in range(item.childCount()):
                restore(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            restore(self.tree.topLevelItem(index))
        if selected_item is not None:
            self.tree.setCurrentItem(selected_item)
        elif first_build and self.tree.topLevelItemCount():
            # 首次构建时展开整棵树，便于一眼看清全部结构。
            self.tree.expandAll()
            self._expanded = {
                node.id for node in self.service.all_nodes() if node.type is not NodeType.THREAD
            }
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.tree.verticalScrollBar().setValue(scroll_position)
        self.tree.setAnimated(True)
        self.tree.setUpdatesEnabled(True)
        self._initialized = True

    def select_node(self, node_id: str) -> None:
        self._selected_id = node_id
        if self.service.get_node(node_id) is None:
            return
        item = self._items.get(node_id)
        if item is None:
            self.refresh(force=True)
            item = self._items.get(node_id)
        if item is None:
            return
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            self._expanded.add(parent.data(0, Qt.ItemDataRole.UserRole))
            parent = parent.parent()
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        self._refresh_details(force=True)

    def _selected_node(self) -> Node | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        return self.service.get_node(item.data(0, Qt.ItemDataRole.UserRole))

    def _on_selection_changed(self) -> None:
        node = self._selected_node()
        self._selected_id = node.id if node else None
        self._refresh_details()

    # -------------------------------------------------------------- 详情
    def _build_details(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        scroll.setWidget(holder)
        self.details_layout = QVBoxLayout(holder)
        self.details_layout.setContentsMargins(16, 4, 8, 16)
        self.details_layout.setSpacing(12)
        self.details_layout.addStretch(1)
        return scroll

    def _clear_details(self) -> None:
        clear_layout(self.details_layout)

    def _details_signature(self, node: Node | None) -> tuple:
        if node is None:
            return ("none",)
        return (
            node.id,
            node.name,
            node.status.value,
            int(node.pinned),
            node.next_action,
            node.parent_id,
            self.service.path_text(node.id),
            self.service.activity_summary(node.id),
            self.service.event_count(node.id),
            tuple(sorted((p.id, p.name) for p in self.service.processes_for_thread(node.id))),
            tuple(sorted((t.id, t.name) for t in self.service.threads_for_process(node.id))),
            tuple(sorted((p.id, p.name) for p in self.service.processes(node.id))),
        )

    def _refresh_details(self, force: bool = False) -> None:
        node = self._selected_node()
        signature = self._details_signature(node)
        if not force and signature == self._details_signature_cache:
            return
        self._details_signature_cache = signature
        self._clear_details()
        if node is None:
            self.details_layout.addWidget(Muted("在上面的树里选择一个节点查看详情。"))
            self.details_layout.addStretch(1)
            return

        header = Card()
        title_row = QHBoxLayout()
        title = QLabel(node.name)
        title.setStyleSheet(f"font-size: {theme.FONT_SIZE + 3}px; font-weight: 600;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        badge = QLabel(node.type.label)
        badge.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; background: {theme.SURFACE}; border-radius: 10px;"
            f" padding: 1px 8px; font-size: {theme.FONT_SIZE - 2}px;"
        )
        title_row.addWidget(badge)
        header.body.addLayout(title_row)
        header.body.addWidget(Muted(f"当前归属：{self.service.path_text(node.id)}"))

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(StatusChip(node.status))
        if node.pinned:
            pinned = QLabel("已置顶为重点")
            pinned.setStyleSheet(f"color: #9a6700; font-size: {theme.FONT_SIZE - 1}px;")
            status_row.addWidget(pinned)
        status_row.addStretch(1)
        header.body.addLayout(status_row)
        if node.type is not NodeType.AREA:
            header.body.addWidget(
                Subtle(f"创建于 {node.created_at} · 更新于 {node.updated_at}")
            )

        if node.type is not NodeType.AREA:
            card = Card()
            card.body.addWidget(SectionHeader("下一步行动"))
            self.next_action_label = Muted(node.next_action or "尚未设置")
            card.body.addWidget(self.next_action_label)
            edit = QPushButton("修改下一步行动")
            edit.clicked.connect(self._edit_next_action)
            row = QHBoxLayout()
            row.addWidget(edit)
            row.addStretch(1)
            card.body.addLayout(row)
            self.details_layout.addWidget(card)

        assoc_card = Card()
        if node.type is NodeType.THREAD:
            assoc_card.body.addWidget(SectionHeader("关联进程"))
            linked = self.service.processes_for_thread(node.id)
            assoc_card.body.addWidget(
                Muted("、".join(p.name for p in linked) if linked else "还没有关联其他进程")
            )
            assoc_card.body.addWidget(
                Subtle("主要归属：%s。关联只表达联系，不改变归属，也不影响热力图。" %
                       (self.service.get_node(node.parent_id).name if node.parent_id else "—"))
            )
            button = QPushButton("管理关联")
            button.clicked.connect(self._manage_associations)
            row = QHBoxLayout()
            row.addWidget(button)
            row.addStretch(1)
            assoc_card.body.addLayout(row)
        elif node.type is NodeType.PROCESS:
            assoc_card.body.addWidget(SectionHeader("关联线程（反向查找）"))
            linked = self.service.threads_for_process(node.id)
            assoc_card.body.addWidget(
                Muted("、".join(t.name for t in linked) if linked else "还没有线程关联到这个进程")
            )
        else:
            assoc_card.body.addWidget(SectionHeader("下属"))
            processes = self.service.processes(node.id)
            assoc_card.body.addWidget(Muted("、".join(p.name for p in processes)))
        self.details_layout.addWidget(assoc_card)

        if node.type is not NodeType.AREA:
            activity_card = Card()
            activity_header = QHBoxLayout()
            activity_header.addWidget(SectionHeader("近期活动"))
            activity_header.addStretch(1)
            total = self.service.activity_count_for_node(node.id)
            activity_header.addWidget(Subtle(f"共 {total} 次"))
            activity_card.body.addLayout(activity_header)
            activities = self.service.list_activities(node_id=node.id, limit=8)
            if activities:
                table = ActivityTable()
                table.load(activities, compact=True)
                table.setMinimumHeight(150)
                activity_card.body.addWidget(table)
            else:
                activity_card.body.addWidget(Muted("还没有活动记录。"))
            record = QPushButton("记录活动")
            record.setObjectName("Primary")
            record.clicked.connect(self._record_for_selected)
            row = QHBoxLayout()
            row.addWidget(record)
            row.addStretch(1)
            activity_card.body.addLayout(row)
            self.details_layout.addWidget(activity_card)

        history_card = Card()
        history_card.body.addWidget(SectionHeader("归属及关系变化历史"))
        history = EventHistoryList(self.service, node.id, list_height=180)
        history.changed.connect(self._on_history_deleted)
        history_card.body.addWidget(history)
        self.details_layout.addWidget(history_card)
        self.details_layout.addStretch(1)

    def _on_history_deleted(self) -> None:
        self._refresh_details(force=True)
        self.changed.emit()

    # -------------------------------------------------------------- 操作
    def _show_context_menu(self, position) -> None:
        menu = QMenu(self)
        actions = (
            ("记录活动", self._record_for_selected),
            ("重命名", self._rename_selected),
            ("移动到…", self._move_selected),
            ("上移一位", lambda: self._move_selected_order(-1)),
            ("下移一位", lambda: self._move_selected_order(1)),
            ("关联…", self._manage_associations),
            ("置顶 / 取消置顶", self._toggle_pin),
            ("设置为进行中", lambda: self._set_status(Status.DOING)),
            ("设置为已暂停", lambda: self._set_status(Status.PAUSED)),
            ("设置为已完成", lambda: self._set_status(Status.DONE)),
            ("归档", lambda: self._set_status(Status.ARCHIVED)),
        )
        for label, handler in actions:
            action = QAction(label, menu)
            action.triggered.connect(lambda _checked=False, h=handler: h())
            menu.addAction(action)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _require_selection(self) -> Node | None:
        node = self._selected_node()
        if node is None:
            warn(self, "请先选择节点", "请在上面的树里选择一个节点。")
        return node

    def _create_node(self, node_type: NodeType) -> None:
        parent_id: str | None = None
        if node_type is not NodeType.AREA:
            dialog = ChooseNodeDialog(
                self.service,
                self,
                title=f"选择{node_type.parent_type.label}归属",
                prompt=f"新建的{node_type.label}会落在所选{node_type.parent_type.label}下。",
                kinds=(node_type.parent_type,),
                confirm_text="下一步",
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            parent_id = dialog.selected_node_id()
            if not parent_id:
                warn(self, "未选择归属", f"请选择一个{node_type.parent_type.label}。")
                return
        name, ok = QInputDialog.getText(self, f"新建{node_type.label}", "名称")
        if not ok or not name.strip():
            return
        try:
            node = self.service.create_node(node_type, name, parent_id)
        except ValidationError as exc:
            warn(self, "无法新建", str(exc))
            return
        self._selected_id = node.id
        if node.parent_id:
            self._expanded.add(node.parent_id)
        self.refresh()
        self.changed.emit()

    def _rename_selected(self) -> None:
        node = self._require_selection()
        if node is None:
            return
        name, ok = QInputDialog.getText(self, "重命名节点", "新名称", text=node.name)
        if not ok or not name.strip():
            return
        try:
            self.service.rename_node(node.id, name)
        except ValidationError as exc:
            warn(self, "无法重命名", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def _move_selected(self) -> None:
        node = self._require_selection()
        if node is None:
            return
        if run_move_dialog(self.service, self, node):
            self.refresh()
            self.changed.emit()

    def _manage_associations(self) -> None:
        node = self._require_selection()
        if node is None:
            return
        if node.type is not NodeType.THREAD:
            warn(self, "只能关联线程", "关联关系建立在 Thread 与 Process 之间，请选择一条线程。")
            return
        dialog = AssociationDialog(self.service, self, node)
        dialog.exec()
        if dialog.changed:
            self.refresh()
            self.changed.emit()

    def _toggle_pin(self) -> None:
        node = self._require_selection()
        if node is None:
            return
        try:
            self.service.set_pinned(node.id, not node.pinned)
        except ValidationError as exc:
            warn(self, "无法置顶", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def _set_status(self, status: Status) -> None:
        node = self._require_selection()
        if node is None:
            return
        try:
            self.service.set_status(node.id, status)
        except ValidationError as exc:
            warn(self, "无法修改状态", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def _delete_selected(self) -> None:
        node = self._require_selection()
        if node is None:
            return
        if run_delete_node(self.service, self, node):
            if self._selected_id == node.id:
                self._selected_id = None
            self._details_signature_cache = None
            self.refresh()
            self.changed.emit()

    def _open_deleted_list(self) -> None:
        from ..widgets.deleted_nodes import DeletedNodesDialog

        dialog = DeletedNodesDialog(self.service, self)
        dialog.changed.connect(self._on_deleted_list_changed)
        dialog.exec()
        self.refresh()

    def _on_deleted_list_changed(self) -> None:
        self.refresh()
        self.changed.emit()

    def _edit_next_action(self) -> None:
        node = self._require_selection()
        if node is None or node.type is NodeType.AREA:
            return
        text, ok = QInputDialog.getText(self, "下一步行动", "行动内容", text=node.next_action)
        if not ok:
            return
        try:
            self.service.set_next_action(node.id, text)
        except ValidationError as exc:
            warn(self, "无法保存", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def _record_for_selected(self) -> None:
        node = self._selected_node()
        if node is None or node.type is NodeType.AREA:
            warn(self, "无法记录", "活动只能记录在 Process 或 Thread 上。")
            return
        dialog = QuickRecordDialog(self.service, self, preset_node_id=node.id)
        if dialog.exec() and dialog.recorded is not None:
            self.refresh()
            self.changed.emit()
