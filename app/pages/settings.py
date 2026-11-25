"""设置页：AI 接口配置 + 少量应用设置。

Key 明文保存在项目内的 `data/ai.json`（不进 Git、不写日志），页面上写明了这一点。
"""

from __future__ import annotations

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import ai, paths, theme
from ..appconfig import AppConfig
from ..widgets.ai_worker import TestWorker
from ..widgets.common import Card, Muted, SectionHeader, Subtle


class SettingsPage(QWidget):
    saved = Signal()

    def __init__(
        self,
        config: AppConfig,
        parent: QWidget | None = None,
        *,
        ai_config: ai.AIConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.ai_config = ai_config or ai.AIConfig()
        self._pool = QThreadPool.globalInstance()
        self._test_worker: TestWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 14)
        outer.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(10)

        layout.addWidget(SectionHeader("AI 助手"))
        layout.addWidget(self._build_ai_card())

        layout.addWidget(SectionHeader("应用"))
        layout.addWidget(self._build_app_card())
        layout.addStretch(1)

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        for field in (self.base_url, self.model, self.api_key):
            field.textChanged.connect(self._sync_enabled)
        self.refresh()

    # ------------------------------------------------------------- 构建
    def _build_ai_card(self) -> Card:
        card = Card()
        self.enabled_box = QCheckBox("启用 AI 助手（关闭时完全不联网）")
        self.enabled_box.stateChanged.connect(lambda _state: self._sync_enabled())
        card.body.addWidget(self.enabled_box)

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(8)

        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://api.deepseek.com")
        form.addRow("接口地址", self.base_url)

        self.model = QLineEdit()
        self.model.setPlaceholderText("deepseek-chat")
        form.addRow("模型", self.model)

        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(8)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-…")
        self.show_key = QCheckBox("显示")
        self.show_key.stateChanged.connect(
            lambda state: self.api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if state else QLineEdit.EchoMode.Password
            )
        )
        key_row.addWidget(self.api_key, 1)
        key_row.addWidget(self.show_key, 0)
        key_widget = QWidget()
        key_widget.setLayout(key_row)
        form.addRow("API Key", key_widget)

        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setDecimals(1)
        form.addRow("温度", self.temperature)

        self.system_prompt = QPlainTextEdit()
        self.system_prompt.setFixedHeight(84)
        self.system_prompt.setPlaceholderText("可选：给模型的人设 / 语气要求")
        form.addRow("系统提示词", self.system_prompt)
        card.body.addLayout(form)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self.save_button = QPushButton("保存设置")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.clicked.connect(self.save)
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self._test_connection)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        card.body.addLayout(buttons)

        self.status = Muted("")
        self.status.setWordWrap(True)
        card.body.addWidget(self.status)

        privacy = Subtle(
            "Key 以明文保存在项目内的 data/ai.json（不进 Git，也不写进日志）。"
            "点击发送时只会把「你输入的文字 + 本会话历史 + 上面的系统提示词」发给你配置的接口；"
            "不会上传数据库、活动记录、日记、备份或导出文件。"
        )
        privacy.setWordWrap(True)
        card.body.addWidget(privacy)
        return card

    def _build_app_card(self) -> Card:
        card = Card()
        self.start_maximized = QCheckBox("启动时最大化")
        card.body.addWidget(self.start_maximized)

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(8)
        self.global_font = QSpinBox()
        self.global_font.setRange(*theme.FONT_SIZE_RANGE)
        self.global_font.setSuffix(" px")
        self.global_font.setMaximumWidth(120)
        form.addRow("全局字体大小", self.global_font)
        self.notes_font = QSpinBox()
        self.notes_font.setRange(12, 28)
        self.notes_font.setSuffix(" px")
        self.notes_font.setMaximumWidth(120)
        form.addRow("笔记字体大小", self.notes_font)
        card.body.addLayout(form)
        card.body.addWidget(
            Subtle("全局字体影响整个界面；笔记字体只影响笔记页。保存后界面会按新字号重建一次。")
        )

        card.body.addWidget(
            Subtle(f"所有数据都在项目文件夹内：{paths.relative_display(paths.DATA_DIR)}")
        )
        return card

    # ------------------------------------------------------------- 数据
    def refresh(self) -> None:
        self.ai_config.load()
        self.enabled_box.setChecked(bool(self.ai_config.get("enabled")))
        self.base_url.setText(str(self.ai_config.get("base_url") or ""))
        self.model.setText(str(self.ai_config.get("model") or ""))
        self.api_key.setText(str(self.ai_config.get("api_key") or ""))
        self.temperature.setValue(float(self.ai_config.get("temperature") or 0.7))
        self.system_prompt.setPlainText(str(self.ai_config.get("system_prompt") or ""))
        self.start_maximized.setChecked(bool(self.config.get("start_maximized")))
        self.global_font.setValue(int(self.config.get("font_size")))
        self.notes_font.setValue(int(self.config.get("notes_font_size")))
        self._sync_enabled()

    def _collect(self) -> ai.AIConfig:
        config = ai.AIConfig(self.ai_config.path)
        config.set("enabled", self.enabled_box.isChecked())
        config.set("base_url", self.base_url.text().strip())
        config.set("model", self.model.text().strip())
        config.set("api_key", self.api_key.text().strip())
        config.set("temperature", round(float(self.temperature.value()), 1))
        config.set("system_prompt", self.system_prompt.toPlainText().strip())
        return config

    def save(self) -> None:
        config = self._collect()
        try:
            config.save()
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.config.set("start_maximized", self.start_maximized.isChecked())
        self.config.set("font_size", int(self.global_font.value()))
        self.config.set("notes_font_size", int(self.notes_font.value()))
        self.config.save()
        self.ai_config = config
        self.status.setText("已保存。")
        self._sync_enabled()
        self.saved.emit()

    def _sync_enabled(self) -> None:
        """开关只管"能不能联网"，配置字段任何时候都能编辑。

        之前把整块表单跟着开关禁用，导致想填 Key 却点不动。
        """
        ready = bool(
            self.base_url.text().strip()
            and self.model.text().strip()
            and self.api_key.text().strip()
        )
        self.test_button.setEnabled(ready)
    # ------------------------------------------------------------- 测试
    def _test_connection(self) -> None:
        if self._test_worker is not None:
            return
        config = self._collect()
        if not config.ready:
            self.status.setText("先把开关打开，并填好接口地址、模型和 Key。")
            return
        self.test_button.setEnabled(False)
        self.status.setText("正在测试连接…")
        worker = TestWorker(config)
        self._test_worker = worker
        worker.signals.done.connect(self._on_test_done)
        worker.signals.failed.connect(self._on_test_failed)
        self._pool.start(worker)

    def _finish_test(self) -> None:
        self._test_worker = None
        self.test_button.setEnabled(self.enabled_box.isChecked())

    def _on_test_done(self, reply: str) -> None:
        self._finish_test()
        self.status.setText(f"连接正常：{reply[:60]}")

    def _on_test_failed(self, message: str) -> None:
        self._finish_test()
        self.status.setText(f"连接失败：{message}")
