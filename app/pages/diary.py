"""日记页面。

每天一篇，写不写都行；编辑器默认打开"今天"，新的一天自然是空的。
所有日记写进项目内的一个 Markdown 文件（`data/diary.md`），按日期从早到晚排列，
可以直接用任何文本编辑器打开查看。
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import diary as diary_store
from .. import paths
from .. import theme
from ..models import today_str
from ..widgets.common import (
    Card,
    Muted,
    SectionHeader,
    Subtle,
    confirm,
    hline,
    qdate_to_str,
    str_to_qdate,
    warn,
)


class DiaryPage(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self._build_toolbar())

        body = QHBoxLayout()
        body.setSpacing(12)

        editor_card = Card()
        self.editor = QPlainTextEdit()
        self.editor.setMinimumHeight(360)
        self.editor.textChanged.connect(self._on_text_changed)
        editor_card.body.addWidget(self.editor, 1)

        layout_row = QHBoxLayout()
        self.status_label = Subtle("")
        layout_row.addWidget(self.status_label)
        self.word_label = Subtle("")
        layout_row.addWidget(self.word_label)
        layout_row.addStretch(1)
        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(lambda: self._save(silent=False))
        layout_row.addWidget(self.save_button)
        editor_card.body.addLayout(layout_row)
        body.addWidget(editor_card, 1)

        side = Card()
        side.setFixedWidth(190)
        side.body.addWidget(SectionHeader("写过的日子"))
        self.entry_list = QListWidget()
        self.entry_list.itemClicked.connect(self._on_entry_clicked)
        self.entry_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.entry_list.customContextMenuRequested.connect(self._show_entry_menu)
        side.body.addWidget(self.entry_list, 1)
        body.addWidget(side)
        layout.addLayout(body, 1)

        bottom_row = QHBoxLayout()
        self.path_label = Subtle(
            f"全部日记都保存在一个文件里：{paths.relative_display(paths.DIARY_PATH)}"
        )
        bottom_row.addWidget(self.path_label)
        bottom_row.addStretch(1)
        self.open_file_button = QPushButton("打开日记本")
        self.open_file_button.setToolTip("用系统默认的 Markdown 程序打开这个文件")
        self.open_file_button.clicked.connect(self._open_diary_file)
        bottom_row.addWidget(self.open_file_button)
        layout.addLayout(bottom_row)

        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(lambda: self._save(silent=False))
        self._current_day = today_str()
        self.refresh()

    # -------------------------------------------------------------- 工具栏
    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        previous = QPushButton("← 前一天")
        previous.clicked.connect(lambda: self._shift_day(-1))
        following = QPushButton("后一天 →")
        following.clicked.connect(lambda: self._shift_day(1))
        today = QPushButton("回到今天")
        today.clicked.connect(self._go_today)
        row.addWidget(previous)
        row.addWidget(following)
        row.addWidget(today)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(str_to_qdate(today_str()))
        self.date_edit.dateChanged.connect(lambda _value: self._load_day(qdate_to_str(self.date_edit.date())))
        row.addWidget(self.date_edit)

        self.header_label = QLabel("")
        self.header_label.setStyleSheet(f"font-size: {theme.FONT_SIZE + 1}px; font-weight: 600;")
        row.addWidget(self.header_label)
        row.addStretch(1)
        return bar

    # ---------------------------------------------------------------- 数据
    def refresh(self) -> None:
        """重新载入"写过的日子"列表；没有未保存内容时同步刷新当前这一天。"""
        self._reload_list()
        if not self.editor.document().isModified():
            self._load_day(self._current_day)
        else:
            self._sync_header()
        self._set_status("")

    def _reload_list(self) -> None:
        self.entry_list.clear()
        days = diary_store.days_with_entries()
        for day in days:
            body = diary_store.read_day(day)
            snippet = body.strip().splitlines()[0][:16] if body.strip() else ""
            item = QListWidgetItem(f"{day}　{snippet}".rstrip())
            item.setData(Qt.ItemDataRole.UserRole, day)
            if day == self._current_day:
                item.setSelected(True)
            self.entry_list.addItem(item)
        if not days:
            placeholder = QListWidgetItem("还没有写过")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.entry_list.addItem(placeholder)

    def _load_day(self, day: str) -> None:
        self._current_day = day
        self._loading = True
        try:
            self.editor.setPlainText(diary_store.read_day(day))
            self.editor.document().setModified(False)
        finally:
            self._loading = False
        self._sync_header()
        self._update_word_count()
        self._set_status("")

    def _sync_header(self) -> None:
        day = self._current_day
        is_today = day == today_str()
        label = "今天" if is_today else ""
        self.header_label.setText(
            f"{day} {diary_store.weekday_label(day)}" + (f"（{label}）" if label else "")
        )

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._update_word_count()
        self._set_status("有未保存的修改（Ctrl+S 保存）")

    def _update_word_count(self) -> None:
        text = self.editor.toPlainText().strip()
        self.word_label.setText(f"{len(text)} 字" if text else "")

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    # ---------------------------------------------------------------- 操作
    def flush(self) -> None:
        """把当前编辑框的内容立即写盘（切换主题重建界面之前会调用）。"""
        self._save(silent=True)

    def _save(self, *, silent: bool) -> None:
        day = self._current_day
        body = self.editor.toPlainText()
        try:
            diary_store.write_day(day, body)
        except (OSError, ValueError) as exc:
            self._set_status(f"保存失败：{exc}")
            return
        self.editor.document().setModified(False)
        self._reload_list()
        self._set_status(
            f"已保存（{day}）· 文件：{paths.relative_display(paths.DIARY_PATH)}"
            if not silent
            else f"已自动保存（{day}）"
        )

    def _shift_day(self, days: int) -> None:
        target = date.fromisoformat(self._current_day) + timedelta(days=days)
        self.date_edit.setDate(str_to_qdate(target.isoformat()))

    def _go_today(self) -> None:
        self.date_edit.setDate(str_to_qdate(today_str()))

    def _on_entry_clicked(self, item: QListWidgetItem) -> None:
        day = item.data(Qt.ItemDataRole.UserRole)
        if day:
            self.date_edit.setDate(str_to_qdate(day))

    # ------------------------------------------------------------ 右键删除
    def build_entry_menu(self, day: str) -> QMenu:
        """「写过的日子」里某一天的右键菜单。"""
        menu = QMenu(self)
        action = QAction(f"删除 {day} 这一天的日记…", menu)
        action.triggered.connect(lambda _checked=False: self._delete_day(day))
        menu.addAction(action)
        return menu

    def _show_entry_menu(self, pos) -> None:
        item = self.entry_list.itemAt(pos)
        if item is None:
            return
        day = item.data(Qt.ItemDataRole.UserRole)
        if not day:
            return
        self.entry_list.setCurrentItem(item)
        self.build_entry_menu(day).exec(self.entry_list.viewport().mapToGlobal(pos))

    def _delete_day(self, day: str) -> None:
        """删掉某一天的日记：Markdown 文件里对应的那一节会一起删除。"""
        if not confirm(
            self,
            "删除这一天的日记",
            f"从日记文件里删掉 {day} 这一天的内容？\n\n"
            "删除后应用内无法撤销；整份日记的备份在 data/backups 里"
            "（创建数据库备份时会一起存一份）。",
            danger=True,
        ):
            return
        # 先把正在写的内容存好，避免顺手丢掉
        if self.editor.document().isModified():
            self._save(silent=True)
        try:
            diary_store.write_day(day, "")
        except (OSError, ValueError) as exc:
            warn(self, "删除失败", str(exc))
            return
        self._load_day(self._current_day)
        self._reload_list()
        self._set_status(f"已从日记文件里删除 {day} 这一天的内容")

    def _open_diary_file(self) -> None:
        """打开日记本（Markdown 文件）：没有就先生成，再交给系统默认程序打开。"""
        if self.editor.document().isModified():
            self._save(silent=True)
        try:
            target = diary_store.ensure_file()
        except OSError as exc:
            warn(self, "打不开日记本", f"无法创建日记文件：{exc}")
            return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            return
        warn(
            self,
            "打不开日记本",
            "系统里没有找到能打开 Markdown 的程序。\n\n"
            f"文件就在这里：\n{target}\n\n"
            "可以用右键「打开方式」选记事本，或到「数据」页面打开文件夹。",
        )

    def hideEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        # 切走页面时自动保存，避免写了一半的内容丢掉
        if self.editor.document().isModified():
            self._save(silent=True)
        super().hideEvent(event)
