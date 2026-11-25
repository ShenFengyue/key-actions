"""笔记页：左边文件夹树，右边 Typora 式 Markdown 即时预览，极简两栏。

文件夹就是 data/notes/ 下的真实目录，笔记就是 .md 文件（存储逻辑在 app/notes.py）。
切走页面 / 切换笔记 / 关闭前自动保存；Ctrl+S 手动保存。
默认即时预览：光标所在块显示源码，其余块实时渲染；「源码」按钮可切回纯文本
编辑（跨段结构被破坏时也会自动回退到这里兜底）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import clock, notes as store, paths, theme
from ..widgets.common import Card, Muted, Subtle, confirm, warn
from ..widgets.markdown_live import MarkdownLiveEdit

REL_ROLE = Qt.ItemDataRole.UserRole
FOLDER_ROLE = Qt.ItemDataRole.UserRole + 1


class NotesPage(QWidget):
    def __init__(self, config=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        store.ensure_root()
        self._current_rel: str | None = None
        self._loading = False
        self._dirty = False
        self._source_mode = False
        # 笔记字号单独可调（设置页），不跟全局字号走
        self._notes_size = int(config.get("notes_font_size")) if config else 15

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(self._build_toolbar())

        body = QHBoxLayout()
        body.setSpacing(12)

        side = Card()
        side.setFixedWidth(240)
        side.body.addWidget(Subtle("文件夹与笔记"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_menu)
        self.tree.currentItemChanged.connect(self._on_select)
        side.body.addWidget(self.tree, 1)
        body.addWidget(side)

        editor_card = Card()
        header = QHBoxLayout()
        self.title_label = QLabel("没有打开笔记")
        self.title_label.setStyleSheet(f"font-size: {theme.FONT_SIZE}px; font-weight: 600;")
        self.path_label = Subtle("")
        header.addWidget(self.title_label)
        header.addSpacing(10)
        header.addWidget(self.path_label, 1)
        editor_card.body.addLayout(header)

        self.stack = QStackedWidget()
        self.live = MarkdownLiveEdit(body_size=self._notes_size)
        self.live.setStyleSheet(f"QTextEdit {{ font-size: {self._notes_size}px; }}")
        self.live.setPlaceholderText("在左边选一篇笔记，或点「新建笔记」")
        self.live.textEdited.connect(self._on_text_changed)
        self.live.structureBroken.connect(self._fallback_to_source)
        self.editor = QPlainTextEdit()
        self.editor.setStyleSheet(f"QPlainTextEdit {{ font-size: {self._notes_size}px; }}")
        self.editor.setPlaceholderText("在左边选一篇笔记，或点「新建笔记」")
        self.editor.textChanged.connect(self._on_text_changed)
        self.stack.addWidget(self.live)
        self.stack.addWidget(self.editor)
        editor_card.body.addWidget(self.stack, 1)

        status_row = QHBoxLayout()
        self.status_label = Subtle("")
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        editor_card.body.addLayout(status_row)
        body.addWidget(editor_card, 1)
        layout.addLayout(body, 1)

        layout.addWidget(Muted(
            f"笔记保存在 {paths.relative_display(paths.NOTES_DIR)}，"
            "文件夹就是真实目录，可以用任何编辑器直接打开"))

        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        save_shortcut.activated.connect(lambda: self._save(silent=False))

        self._set_editor_enabled(False)
        self.refresh()

    # ---------------------------------------------------------------- 工具栏
    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        new_note = QPushButton("＋ 新建笔记")
        new_note.setObjectName("Primary")
        new_note.setCursor(Qt.CursorShape.PointingHandCursor)
        new_note.clicked.connect(self._new_note)
        new_folder = QPushButton("＋ 新建文件夹")
        new_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        new_folder.clicked.connect(self._new_folder)
        row.addWidget(new_note)
        row.addWidget(new_folder)
        row.addStretch(1)
        self.source_button = QPushButton("源码")
        self.source_button.setCheckable(True)
        self.source_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.source_button.setToolTip("切回纯 Markdown 源码编辑（默认是即时预览）")
        self.source_button.toggled.connect(self._toggle_source)
        row.addWidget(self.source_button)
        save = QPushButton("保存")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(lambda: self._save(silent=False))
        row.addWidget(save)
        return row

    # ---------------------------------------------------------------- 目录树
    def refresh(self) -> None:
        """重建目录树（保住展开与选中状态）；当前笔记没改过就同步磁盘内容。"""
        expanded = self._expanded_rels()
        selected = self._current_rel
        self._rebuild_tree(expanded, selected)
        if self._current_rel and store.exists(self._current_rel):
            if not self._dirty:
                self._load_note(self._current_rel)
        elif self._current_rel:
            self._current_rel = None
            self._set_editor_enabled(False)
            self._set_text("")
            self.title_label.setText("没有打开笔记")
            self.path_label.setText("")

    def _expanded_rels(self) -> set[str]:
        result: set[str] = set()

        def walk(item: QTreeWidgetItem) -> None:
            if item.isExpanded():
                result.add(item.data(0, REL_ROLE))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return result

    def _rebuild_tree(self, expanded: set[str], select_rel: str | None) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()

        def add(entries: list[dict], parent: QTreeWidgetItem | None) -> None:
            for entry in entries:
                item = QTreeWidgetItem([entry["name"]])
                item.setData(0, REL_ROLE, entry["rel"])
                item.setData(0, FOLDER_ROLE, entry["is_folder"])
                if parent is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                if entry["is_folder"]:
                    add(entry["children"], item)
                    item.setExpanded(entry["rel"] in expanded)
                if entry["rel"] == select_rel:
                    self.tree.setCurrentItem(item)

        add(store.tree(), None)
        self.tree.blockSignals(False)

    def _selected_folder(self) -> str:
        """新建东西放哪：选中文件夹就放它里面，选中笔记就放它同级，否则根目录。"""
        item = self.tree.currentItem()
        if item is None:
            return ""
        rel = item.data(0, REL_ROLE)
        if item.data(0, FOLDER_ROLE):
            return rel
        return "/".join(rel.split("/")[:-1])

    def _on_select(self, current: QTreeWidgetItem | None,
                   _previous: QTreeWidgetItem | None) -> None:
        if current is None:
            return
        if self._dirty:
            self._save(silent=True)
        if not current.data(0, FOLDER_ROLE):
            self._load_note(current.data(0, REL_ROLE))

    def _load_note(self, rel: str) -> None:
        self._current_rel = rel
        self._loading = True
        try:
            self._set_text(store.read_note(rel))
            self._dirty = False
            self.live.clear_modified()
            self.editor.document().setModified(False)
        finally:
            self._loading = False
        self._set_editor_enabled(True)
        self.title_label.setText(rel.split("/")[-1][:-3])
        self.path_label.setText(paths.relative_display(paths.NOTES_DIR / rel))
        self.status_label.setText("")

    def _set_editor_enabled(self, enabled: bool) -> None:
        self.live.setEnabled(enabled)
        self.editor.setEnabled(enabled)
        self.source_button.setEnabled(enabled)

    # ---------------------------------------------------------------- 编辑
    def _current_text(self) -> str:
        return self.editor.toPlainText() if self._source_mode else self.live.source_text()

    def _set_text(self, text: str) -> None:
        if self._source_mode:
            self.editor.setPlainText(text)
        else:
            self.live.set_text(text)

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._dirty = True
        self.status_label.setText("有未保存的修改（Ctrl+S 保存）")

    def _save(self, *, silent: bool) -> None:
        if not self._current_rel:
            return
        try:
            store.write_note(self._current_rel, self._current_text())
        except (OSError, ValueError) as exc:
            self.status_label.setText(f"保存失败：{exc}")
            return
        self._dirty = False
        self.live.clear_modified()
        self.editor.document().setModified(False)
        stamp = clock.now().strftime("%H:%M")
        self.status_label.setText(
            f"已自动保存 · {stamp}" if silent else f"已保存 · {stamp}")

    def flush(self) -> None:
        """切换主题 / 字号重建界面之前，把没保存的内容先写盘。"""
        if self._dirty:
            self._save(silent=True)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._dirty:
            self._save(silent=True)
        super().hideEvent(event)

    def _toggle_source(self, checked: bool) -> None:
        """源码 ↔ 即时预览切换，内容无损带过去。"""
        if checked == self._source_mode:
            return
        text = self._current_text()
        self._source_mode = checked
        self._loading = True
        try:
            if checked:
                self.editor.setPlainText(text)
                self.stack.setCurrentWidget(self.editor)
                self.editor.setFocus()
            else:
                self.live.set_text(text)
                self.stack.setCurrentWidget(self.live)
                self.live.setFocus()
        finally:
            self._loading = False

    def _fallback_to_source(self) -> None:
        """即时预览里跨段结构被破坏：自动回退源码模式，保住内容。"""
        if self._source_mode:
            return
        text = self.live.source_text()
        self._source_mode = True
        self.source_button.blockSignals(True)
        self.source_button.setChecked(True)
        self.source_button.blockSignals(False)
        self._loading = True
        try:
            self.editor.setPlainText(text)
            self.stack.setCurrentWidget(self.editor)
        finally:
            self._loading = False
        self.status_label.setText("这一段改动太复杂，已切到源码模式，内容没丢")

    # ---------------------------------------------------------------- 操作
    def _new_note(self) -> None:
        name, ok = QInputDialog.getText(self, "新建笔记", "笔记名字（自动加 .md）：")
        if not ok:
            return
        try:
            rel = store.create_note(self._selected_folder(), name)
        except (OSError, ValueError) as exc:
            warn(self, "新建失败", str(exc))
            return
        self.refresh()
        self._select_rel(rel)
        self._load_note(rel)
        (self.editor if self._source_mode else self.live).setFocus()

    def _new_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名字：")
        if not ok:
            return
        try:
            rel = store.create_folder(self._selected_folder(), name)
        except (OSError, ValueError) as exc:
            warn(self, "新建失败", str(exc))
            return
        self.refresh()
        self._select_rel(rel)

    def _select_rel(self, rel: str) -> None:
        def walk(item: QTreeWidgetItem) -> bool:
            if item.data(0, REL_ROLE) == rel:
                self.tree.setCurrentItem(item)
                return True
            for i in range(item.childCount()):
                if walk(item.child(i)):
                    return True
            return False

        for i in range(self.tree.topLevelItemCount()):
            if walk(self.tree.topLevelItem(i)):
                return

    def _show_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        rel = item.data(0, REL_ROLE)
        is_folder = item.data(0, FOLDER_ROLE)
        menu = QMenu(self)
        if is_folder:
            menu.addAction("在此新建笔记", self._new_note)
            menu.addAction("在此新建文件夹", self._new_folder)
            menu.addSeparator()
            menu.addAction("重命名…", lambda: self._rename(rel, True))
            menu.addAction("删除文件夹…", lambda: self._delete_folder(rel))
        else:
            menu.addAction("重命名…", lambda: self._rename(rel, False))
            menu.addAction("移动到…", lambda: self._move(rel))
            menu.addSeparator()
            menu.addAction("删除笔记…", lambda: self._delete_note(rel))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _rename(self, rel: str, is_folder: bool) -> None:
        old = rel.split("/")[-1]
        if not is_folder and old.lower().endswith(".md"):
            old = old[:-3]
        name, ok = QInputDialog.getText(self, "重命名", "新名字：", text=old)
        if not ok:
            return
        try:
            new_rel = store.rename(rel, name)
        except (OSError, ValueError) as exc:
            warn(self, "重命名失败", str(exc))
            return
        if self._current_rel == rel:
            self._current_rel = new_rel
        self.refresh()
        self._select_rel(new_rel)
        if self._current_rel == new_rel:
            self._load_note(new_rel)

    def _move(self, rel: str) -> None:
        choices = ["（根目录）"] + store.folders()
        current_parent = "/".join(rel.split("/")[:-1])
        default_index = choices.index(current_parent) if current_parent in choices else 0
        choice, ok = QInputDialog.getItem(
            self, "移动到", "目标文件夹：", choices, default_index, False)
        if not ok:
            return
        target = "" if choice == "（根目录）" else choice
        try:
            new_rel = store.move_note(rel, target)
        except (OSError, ValueError) as exc:
            warn(self, "移动失败", str(exc))
            return
        if self._current_rel == rel:
            self._current_rel = new_rel
        self.refresh()
        self._select_rel(new_rel)
        if self._current_rel == new_rel:
            self._load_note(new_rel)

    def _delete_note(self, rel: str) -> None:
        if not confirm(self, "删除笔记",
                       f"确定删除「{rel}」吗？\n文件会直接从磁盘删掉，应用内无法撤销。",
                       danger=True):
            return
        try:
            store.delete_note(rel)
        except (OSError, ValueError) as exc:
            warn(self, "删除失败", str(exc))
            return
        if self._current_rel == rel:
            self._current_rel = None
            self._set_editor_enabled(False)
            self._set_text("")
            self.title_label.setText("没有打开笔记")
            self.path_label.setText("")
        self.refresh()

    def _delete_folder(self, rel: str) -> None:
        if not confirm(self, "删除文件夹", f"确定删除文件夹「{rel}」吗？",
                       danger=True):
            return
        try:
            store.delete_folder(rel)
        except (OSError, ValueError) as exc:
            warn(self, "删除失败", str(exc))
            return
        self.refresh()