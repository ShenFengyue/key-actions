"""快速记录面板（`Ctrl+N`）：一个输入框，两段式。

第一段：输入关键字（模糊匹配）挑一个进程或线程，回车选中；
第二段：同一个输入框改用来写备注，再按一次回车就保存并关闭。
没点「记录」（也就是没按第二次回车）之前，数据库不会有任何变化。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..models import Activity, today_str
from ..quick_capture import (
    DEFAULT_LIMIT,
    Candidate,
    build_candidates,
    match,
    recent_candidates,
)
from ..services import LifeManagerService, ValidationError
from .common import AppDialog, Subtle

MATCH_PLACEHOLDER = "输入关键字匹配进程 / 线程，回车选中"
NOTE_PLACEHOLDER = "备注（可留空，再按一次回车保存）"
MATCH_HINT = "上下键选择 · 回车选中 · Esc 取消"
# 输入恰好等于这个词时，列表第一行出现「休息日」选项（不影响下面的节点匹配）
REST_KEYWORD = "休息"


class QuickCaptureDialog(AppDialog):
    """极简记录窗：一个输入框，先匹配再写。"""

    def __init__(
        self, service: LifeManagerService, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.recorded: Activity | None = None
        # 休息日结果：rest_day 是被标记/取消的日期，rest_cleared 表示这次是取消
        self.rest_day: str | None = None
        self.rest_cleared = False
        self._rest_mode = False
        self.stage = "match"
        self.candidates = build_candidates(service)
        self.matches: list[Candidate] = []
        self.target: Candidate | None = None

        self.setObjectName("QuickCapture")
        self.setWindowTitle("快速记录")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setMinimumWidth(560)
        self.setMaximumWidth(760)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.input = QLineEdit()
        self.input.setObjectName("QuickCaptureInput")
        self.input.setPlaceholderText(MATCH_PLACEHOLDER)
        self.input.textChanged.connect(self._on_text_changed)
        self.input.returnPressed.connect(self._on_return)
        self.input.installEventFilter(self)
        layout.addWidget(self.input)

        self.result_list = QListWidget()
        self.result_list.setObjectName("QuickCaptureList")
        self.result_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.result_list.setUniformItemSizes(True)
        self.result_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.result_list.itemClicked.connect(lambda _item: self._accept_match())
        layout.addWidget(self.result_list)

        self.target_label = Subtle("")
        self.target_label.setObjectName("QuickCaptureHint")
        self.target_label.setVisible(False)
        layout.addWidget(self.target_label)

        self.status_label = Subtle("")
        self.status_label.setObjectName("QuickCaptureHint")
        layout.addWidget(self.status_label)

        self._refresh_matches()
        self.input.setFocus()

    # ------------------------------------------------------------ 第一段
    def _on_text_changed(self, text: str) -> None:
        if self.stage != "match":
            return
        self._refresh_matches(text)

    def _refresh_matches(self, query: str | None = None) -> None:
        if query is None:
            query = self.input.text()
        typed = bool(query.strip())
        self._rest_mode = typed and query.strip() == REST_KEYWORD
        if typed:
            self.matches = match(self.candidates, query, DEFAULT_LIMIT)
        else:
            # 没输入就先给出最近记过的几个，方便重复记录同一件事
            self.matches = recent_candidates(self.service, self.candidates)
        self._fill_list()
        if self._rest_mode:
            self._set_status("回车确认 · Esc 取消")
        elif not self.matches:
            self._set_status(
                "没有匹配的进程或线程" if typed else "输入关键字匹配进程 / 线程"
            )
        elif typed:
            self._set_status(MATCH_HINT)
        else:
            self._set_status(f"最近记录 · {MATCH_HINT}")

    def _fill_list(self) -> None:
        self.result_list.clear()
        if self._rest_mode:
            day = today_str()
            if self.service.is_rest_day(day):
                text = f"休息日 · 今天已是休息日，回车取消标记（{day}）"
            else:
                text = f"休息日 · 把今天标记为休息日，总热力图变粉（{day}）"
            item = QListWidgetItem(text, self.result_list)
            item.setToolTip("休息日不算活动次数；再输入一次「休息」可以取消")
        for candidate in self.matches:
            item = QListWidgetItem(candidate.display, self.result_list)
            item.setToolTip(f"{candidate.name}（{candidate.type_label}） · {candidate.parent_path}")
        if self._rest_mode or self.matches:
            self.result_list.setCurrentRow(0)
        self.result_list.setVisible(bool(self._rest_mode or self.matches))
        self._resize_list()

    def _resize_list(self) -> None:
        count = self.result_list.count()
        row_height = self.result_list.sizeHintForRow(0) if count else 30
        row_height = max(row_height, 30)
        self.result_list.setFixedHeight(row_height * min(count, DEFAULT_LIMIT) + 4)
        self.adjustSize()

    def _move_selection(self, step: int) -> None:
        count = self.result_list.count()
        if not count:
            return
        row = (self.result_list.currentRow() + step) % count
        self.result_list.setCurrentRow(row)

    def _accept_match(self) -> None:
        row = self.result_list.currentRow()
        if self._rest_mode and row == 0:
            self._commit_rest()
            return
        index = row - 1 if self._rest_mode else row
        if index < 0 or index >= len(self.matches):
            self._set_status("先用关键字选一个进程或线程", danger=True)
            return
        self.target = self.matches[index]
        self.stage = "note"
        self.result_list.clear()
        self.result_list.setVisible(False)
        self.input.clear()
        self.input.setPlaceholderText(NOTE_PLACEHOLDER)
        self.target_label.setText(
            f"记到：{self.target.parent_path} → {self.target.name} · {today_str()}"
        )
        self.target_label.setVisible(True)
        self._set_status("回车保存 · Esc 取消")
        self.adjustSize()

    # ------------------------------------------------------------ 第二段
    def _on_return(self) -> None:
        if self.stage == "match":
            self._accept_match()
        else:
            self._commit()

    def _commit(self) -> None:
        if self.target is None:  # pragma: no cover - 状态机保证不会发生
            return
        try:
            self.recorded = self.service.record_activity(
                self.target.node_id, self.input.text(), today_str()
            )
        except ValidationError as exc:
            self._set_status(str(exc), danger=True)
            return
        self.accept()

    def _commit_rest(self) -> None:
        """回车选中「休息日」：今天没标记就标记，已标记就取消。"""
        day = today_str()
        try:
            if self.service.is_rest_day(day):
                self.service.clear_rest_day(day)
                self.rest_cleared = True
            else:
                self.service.mark_rest_day(day)
        except ValidationError as exc:
            self._set_status(str(exc), danger=True)
            return
        self.rest_day = day
        self.accept()

    # ------------------------------------------------------------ 杂项
    def _set_status(self, text: str, *, danger: bool = False) -> None:
        color = theme.DANGER if danger else theme.TEXT_SUBTLE
        self.status_label.setStyleSheet(f"color: {color}; font-size: {theme.FONT_SIZE - 2}px;")
        self.status_label.setText(text)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._move_selection(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move_selection(-1)
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            geometry = parent.frameGeometry()
            self.move(
                geometry.center().x() - self.width() // 2,
                geometry.center().y() - self.height() // 2,
            )
