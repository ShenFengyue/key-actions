"""AI 对话页：会话历史 + 聊天。

只把「你输入的文字 + 本会话历史 + 设置里的系统提示词」发给模型；
不发送数据库、活动记录、日记或备份。
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import ai, ai_records, theme
from ..quick_capture import build_candidates, match
from ..services import LifeManagerService, ValidationError
from ..widgets import markdown
from ..widgets.ai_worker import ChatWorker
from ..widgets.common import (
    Muted,
    NodeComboBox,
    Subtle,
    qdate_to_str,
    str_to_qdate,
)

CHAT_WIDTH = 860      # 中间对话栏的最大宽度（整栏居中，不贴窗口两边）
BUBBLE_WIDTH = 620    # 单条消息气泡的最大宽度
BODY_MIN_WIDTH = 140  # 极短消息也别把气泡压成一条窄缝


class MarkdownView(QLabel):
    """把一段 Markdown 渲染成宽度贴合内容的富文本（可选中复制，不滚动）。

    宽度按"不换行时最宽的那一行"算，再交给 QLabel 自己换行。这样不会出现
    QTextBrowser 那套「控件宽度和视口宽度差 16px」的坑 —— 那正是长句末尾
    被裁、短句莫名折行的原因。
    """

    def __init__(self, text: str, max_width: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_width = max_width
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QLabel { background: transparent; }")
        self.set_markdown(text)

    def set_markdown(self, text: str) -> None:
        self.setText(markdown.to_html(text or "", self._palette_style()))
        width = self._natural_width()
        self.setFixedWidth(width)
        self.setFixedHeight(self._wrapped_height(width))

    # -------------------------------------------------------- 尺寸
    def _palette_style(self) -> dict[str, str]:
        return {
            "text": theme.TEXT,
            "muted": theme.TEXT_MUTED,
            "code_bg": theme.SURFACE_ALT,
            "border": theme.BORDER,
        }

    def _measure_document(self, width: float) -> QTextDocument:
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setHtml(self.text())
        document.setTextWidth(width)
        return document

    def _natural_width(self) -> int:
        ideal = self._measure_document(-1).idealWidth()
        return int(min(self._max_width, max(BODY_MIN_WIDTH, math.ceil(ideal) + 4)))

    def _wrapped_height(self, width: int) -> int:
        height = self.heightForWidth(width)
        if height <= 0:
            height = int(math.ceil(self._measure_document(width).size().height()))
        return max(20, height)


class MessageBubble(QWidget):
    """一条消息：你（右侧气泡）/ AI（左侧气泡），都在居中的对话栏里。"""

    def __init__(self, role: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        user = role == "user"
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 3, 0, 3)
        row.setSpacing(0)

        bubble = QFrame()
        bubble.setObjectName("UserBubble" if user else "AIBubble")
        bubble.setMaximumWidth(BUBBLE_WIDTH)
        bubble.setStyleSheet(
            f"QFrame#UserBubble {{ background: {theme.ACCENT_SOFT}; border-radius: 12px; }}"
            f"QFrame#AIBubble {{ background: {theme.SURFACE}; border-radius: 12px; }}"
            f"QLabel {{ background: transparent; color: {theme.TEXT}; }}"
        )
        inner = QVBoxLayout(bubble)
        inner.setContentsMargins(14, 10, 14, 10)
        inner.setSpacing(4)

        who = QLabel("你" if user else "AI")
        who.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; font-size: 11px; font-weight: 600;")
        inner.addWidget(who)

        body = MarkdownView(text, BUBBLE_WIDTH - 28)
        body.setStyleSheet(f"QLabel {{ background: transparent; color: {theme.TEXT}; }}")
        inner.addWidget(body)

        if user:
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch(1)


class RecordCard(QFrame):
    """AI 说「帮你记一下」时出现的待写入卡片。

    模型只看得到对话内容（拿不到节点树，这是隐私红线），所以这里显示的是
    本地模糊匹配出来的节点，每条都可以改成别的节点。不点「写入」，数据库不会有
    任何变化。
    """

    written = Signal(int)
    dismissed = Signal()

    def __init__(
        self, service: LifeManagerService, intents: list[ai_records.RecordIntent],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._rows: list[tuple[NodeComboBox, QDateEdit, QLineEdit]] = []
        self.setObjectName("RecordCard")
        self.setMaximumWidth(BUBBLE_WIDTH)
        self.setStyleSheet(
            f"QFrame#RecordCard {{ background: {theme.SURFACE_ALT};"
            f" border: 1px solid {theme.BORDER}; border-radius: 10px; }}"
            "QLabel { background: transparent; border: none; }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(6)

        body = QWidget()
        self._body = body
        inner = QVBoxLayout(body)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(7)

        title = QLabel(f"待写入 {len(intents)} 条活动记录")
        title.setStyleSheet(f"color: {theme.TEXT}; font-size: 13px; font-weight: 600;")
        inner.addWidget(title)
        tip = QLabel("确认后才写进数据库；点「不记了」不会有任何变化。")
        tip.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; font-size: 11px;")
        inner.addWidget(tip)

        candidates = build_candidates(service)
        for index, intent in enumerate(intents, 1):
            hits = match(candidates, intent.target, limit=1)
            caption = QLabel(f"第 {index} 条 · 原话「{intent.target}」")
            caption.setWordWrap(True)
            caption.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
            inner.addWidget(caption)

            line = QHBoxLayout()
            line.setSpacing(8)
            combo = NodeComboBox()
            combo.populate(service, selected_id=hits[0].node_id if hits else None)
            if not hits:
                # 没有匹配到就别替用户默认选第一个节点，会记到错的地方
                combo.setCurrentIndex(-1)
            date_edit = QDateEdit()
            date_edit.setCalendarPopup(True)
            date_edit.setDisplayFormat("yyyy-MM-dd")
            date_edit.setDate(str_to_qdate(intent.day))
            date_edit.setFixedWidth(120)
            line.addWidget(combo, 1)
            line.addWidget(date_edit, 0)
            inner.addLayout(line)

            note_row = QHBoxLayout()
            note_row.setSpacing(6)
            note_label = QLabel("备注")
            note_label.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; font-size: 11px;")
            note_label.setFixedWidth(30)
            note = QLineEdit(intent.note)
            note.setPlaceholderText("可留空")
            note_row.addWidget(note_label)
            note_row.addWidget(note, 1)
            inner.addLayout(note_row)
            self._rows.append((combo, date_edit, note))

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.DANGER}; font-size: 11px;")
        self.error.setVisible(False)
        inner.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.skip_button = QPushButton("不记了")
        self.skip_button.clicked.connect(self.dismiss)
        self.write_button = QPushButton("写入")
        self.write_button.setObjectName("PrimaryButton")
        self.write_button.clicked.connect(self.write)
        buttons.addWidget(self.skip_button)
        buttons.addWidget(self.write_button)
        inner.addLayout(buttons)

        outer.addWidget(body)

    # ------------------------------------------------------------- 动作
    def write(self) -> None:
        missing = [
            index
            for index, (combo, _date, _note) in enumerate(self._rows, 1)
            if not combo.current_node_id()
        ]
        if missing:
            self.error.setText(f"第 {missing[0]} 条还没选节点：请选择要记在哪件事上。")
            self.error.setVisible(True)
            return

        count = 0
        for combo, date_edit, note in self._rows:
            try:
                self.service.record_activity(
                    combo.current_node_id(), note.text(), qdate_to_str(date_edit.date())
                )
            except ValidationError as exc:
                self.error.setText(f"写入失败：{exc}")
                self.error.setVisible(True)
                return
            count += 1
        self._finish(f"已写入 {count} 条活动记录。", theme.ACCENT)
        self.written.emit(count)

    def dismiss(self) -> None:
        self._finish("未写入任何记录。", theme.TEXT_SUBTLE)
        self.dismissed.emit()

    def _finish(self, text: str, color: str) -> None:
        """把编辑区整块换掉，只留一句结果 —— 防止再点一次写出重复记录。"""
        self._body.setParent(None)
        self._body.deleteLater()
        self._rows = []
        result = QLabel(text)
        result.setWordWrap(True)
        result.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 600;")
        self.layout().addWidget(result)


class CenteredColumn(QWidget):
    """把内容限制在一个最大宽度内，并始终水平居中。"""

    def __init__(self, content: QWidget, max_width: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._content = content
        self._max_width = max_width
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)
        row.addWidget(content, 0)
        row.addStretch(1)

    def resizeEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        width = max(280, min(self._max_width, self.width() - 48))
        self._content.setFixedWidth(width)
        super().resizeEvent(event)


class AIPage(QWidget):
    openSettingsRequested = Signal()

    def __init__(
        self,
        service: LifeManagerService,
        parent: QWidget | None = None,
        *,
        ai_config: ai.AIConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.ai_config = ai_config or ai.AIConfig()
        self._pool = QThreadPool.globalInstance()
        self._worker: ChatWorker | None = None
        self.current_id: str | None = None
        # 待确认的记录意图：key 是消息 id。只放在内存里，确认或取消后移除。
        self._pending: dict[str, list[ai_records.RecordIntent]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 14)
        outer.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_chat())
        splitter.setSizes([240, 900])
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)

        self.notice = Subtle("")
        self.notice.setWordWrap(True)
        outer.addWidget(self.notice)

        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.send)

        self.reload_config()
        self.refresh()

    # ------------------------------------------------------------- 构建
    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel("对话历史"))
        self.conversation_list = QListWidget()
        self.conversation_list.currentItemChanged.connect(self._on_conversation_selected)
        layout.addWidget(self.conversation_list, 1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.new_button = QPushButton("新建对话")
        self.new_button.clicked.connect(self.new_chat)
        self.delete_button = QPushButton("删除")
        self.delete_button.clicked.connect(self.delete_conversation)
        row.addWidget(self.new_button)
        row.addWidget(self.delete_button)
        layout.addLayout(row)
        return panel

    def _build_chat(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(8)

        # 消息区：滚动区里放一列限宽的“对话栏”，整栏水平居中（像 ChatGPT）
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.messages_column = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_column)
        self.messages_layout.setContentsMargins(0, 12, 0, 12)
        self.messages_layout.setSpacing(6)
        self.scroll.setWidget(CenteredColumn(self.messages_column, CHAT_WIDTH))
        layout.addWidget(self.scroll, 1)

        # 输入区也限制在同一个宽度里，和上面的对话栏对齐
        input_column = QWidget()
        input_layout = QVBoxLayout(input_column)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(6)

        self.input = QPlainTextEdit()
        self.input.setFixedHeight(92)
        self.input.setPlaceholderText("输入内容，Ctrl+Enter 发送")
        input_layout.addWidget(self.input)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.status = Muted("")
        row.addWidget(self.status, 1)
        self.settings_button = QPushButton("打开设置")
        self.settings_button.clicked.connect(self.openSettingsRequested.emit)
        row.addWidget(self.settings_button)
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.clicked.connect(self.send)
        row.addWidget(self.send_button)
        input_layout.addLayout(row)

        layout.addWidget(CenteredColumn(input_column, CHAT_WIDTH))
        return panel

    # ------------------------------------------------------------- 状态
    def reload_config(self) -> None:
        self.ai_config.load()
        self._sync_ready()

    def _sync_ready(self) -> None:
        ready = self.ai_config.ready
        # 页面随时能点：没配置时也允许打字，点发送会提示先去设置。
        self.input.setEnabled(self._worker is None)
        self.send_button.setEnabled(self._worker is None)
        if ready:
            self.notice.setText(
                "只发送本会话的文字与设置里的人设提示词；不会上传数据库、活动记录、日记或备份。"
            )
        else:
            self.notice.setText(
                "AI 还没有启用或缺少 API Key：到「设置」里填写 Key 并勾选启用（字段可以直接编辑）。"
                "在此之前应用完全离线，其它功能不受影响。"
            )

    def refresh(self) -> None:
        self._fill_conversations()
        if self.current_id is None:
            self._render_messages([])
        elif not any(
            item["id"] == self.current_id for item in self.service.ai_conversations()
        ):
            self.current_id = None
            self._render_messages([])

    def _fill_conversations(self) -> None:
        self.conversation_list.blockSignals(True)
        self.conversation_list.clear()
        current_row = -1
        for row, conversation in enumerate(self.service.ai_conversations()):
            item = QListWidgetItem(conversation["title"])
            item.setData(Qt.ItemDataRole.UserRole, conversation["id"])
            item.setToolTip(
                f"{conversation['message_count']} 条消息 · 最后更新 {conversation['updated_at']}"
            )
            self.conversation_list.addItem(item)
            if conversation["id"] == self.current_id:
                current_row = row
        if current_row >= 0:
            self.conversation_list.setCurrentRow(current_row)
        self.conversation_list.blockSignals(False)
        self.delete_button.setEnabled(self.current_id is not None)

    # ------------------------------------------------------------- 交互
    def new_chat(self) -> None:
        self.current_id = None
        self.conversation_list.blockSignals(True)
        self.conversation_list.setCurrentRow(-1)
        self.conversation_list.blockSignals(False)
        self._render_messages([])
        self.input.setFocus()
        self.status.setText("")
        self.delete_button.setEnabled(False)

    def _on_conversation_selected(self, current, _previous) -> None:
        if current is None:
            self.current_id = None
            self._render_messages([])
            return
        self.current_id = current.data(Qt.ItemDataRole.UserRole)
        self._render_messages(self.service.ai_messages(self.current_id))
        self.delete_button.setEnabled(True)

    def delete_conversation(self) -> None:
        if not self.current_id:
            return
        title = self.conversation_list.currentItem()
        name = title.text() if title is not None else "这条对话"
        confirm = QMessageBox.question(
            self,
            "删除对话",
            f"删除「{name}」以及它的全部消息？删除后无法恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.service.delete_ai_conversation(self.current_id)
        self.current_id = None
        self._render_messages([])
        self._fill_conversations()

    def send(self) -> None:
        if self._worker is not None:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.reload_config()
        if not self.ai_config.ready:
            self.status.setText("还没有配置好 AI：点右下角「打开设置」填写 Key 并勾选启用。")
            return

        if self.current_id is None:
            self.current_id = self.service.create_ai_conversation()
        self.service.append_ai_message(self.current_id, "user", text)
        self.input.clear()
        messages = [
            {"role": message["role"], "content": message["content"]}
            for message in self.service.ai_messages(self.current_id)
        ]
        self._render_messages(self.service.ai_messages(self.current_id), pending=True)
        self._fill_conversations()

        worker = ChatWorker(
            self.ai_config, messages, system_hint=ai_records.protocol_hint()
        )
        self._worker = worker
        worker.signals.done.connect(self._on_reply)
        worker.signals.failed.connect(self._on_error)
        self.send_button.setEnabled(False)
        self.input.setEnabled(False)
        self.status.setText("正在回答…")
        self._pool.start(worker)

    # ------------------------------------------------------------- 回调
    def _finish(self) -> None:
        self._worker = None
        self.input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.status.setText("")
        self.input.setFocus()

    def _on_reply(self, reply: str) -> None:
        clean, intents = ai_records.extract(reply)
        if self.current_id:
            if clean.strip():
                message_id = self.service.append_ai_message(self.current_id, "assistant", clean)
                if intents:
                    self._pending[message_id] = intents
            elif intents:
                message_id = self.service.append_ai_message(
                    self.current_id,
                    "assistant",
                    f"（解析到 {len(intents)} 条待记录内容，请在下方确认。）",
                )
                self._pending[message_id] = intents
            else:
                self.status.setText("模型返回了空内容。")
        self._finish()
        if self.current_id:
            self._render_messages(self.service.ai_messages(self.current_id))
        self._fill_conversations()

    def _on_error(self, message: str) -> None:
        self._finish()
        if self.current_id:
            self._render_messages(
                self.service.ai_messages(self.current_id), error=message
            )
        self.status.setText(f"发送失败：{message}")

    # ------------------------------------------------------------- 渲染
    def _render_messages(
        self, messages: list[dict[str, str]], *, pending: bool = False, error: str = ""
    ) -> None:
        while self.messages_layout.count():
            item = self.messages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        if not messages:
            hint = QLabel("新建一条对话，直接输入问题即可。\n历史记录只保存在本机。")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setStyleSheet(
                f"color: {theme.TEXT_SUBTLE}; font-size: 13px; padding: 70px 0;"
            )
            self.messages_layout.addWidget(hint)

        for message in messages:
            self.messages_layout.addWidget(
                MessageBubble(message["role"], message["content"])
            )
            intents = self._pending.get(str(message.get("id") or ""))
            if intents:
                card = RecordCard(self.service, intents)
                # 默认参数把这条消息绑死，避免闭包捕获循环变量
                card.written.connect(
                    lambda count, msg=message: self._on_records_written(msg, count)
                )
                card.dismissed.connect(
                    lambda msg=message: self._on_records_dismissed(msg)
                )
                self.messages_layout.addWidget(card)

        if pending:
            self.messages_layout.addWidget(MessageBubble("assistant", "正在回答…"))

        if error:
            warn = QLabel(f"发送失败：{error}")
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {theme.DANGER}; padding: 6px 2px;")
            self.messages_layout.addWidget(warn)

        self.messages_layout.addStretch(1)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _on_records_written(self, message: dict[str, str], count: int) -> None:
        self._pending.pop(str(message.get("id") or ""), None)
        self.status.setText(f"已记录 {count} 条活动。")
        self.notice.setText(f"已记录 {count} 条活动，可在「活动记录」页查看。")

    def _on_records_dismissed(self, message: dict[str, str]) -> None:
        self._pending.pop(str(message.get("id") or ""), None)

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
