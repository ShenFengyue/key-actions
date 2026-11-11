"""把 AI 请求放到后台线程，避免卡住界面。"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal

from .. import ai


class WorkerSignals(QObject):
    done = Signal(str)
    failed = Signal(str)


class ChatWorker(QRunnable):
    """一次 /chat/completions 调用。只发信号，不碰任何控件。"""

    def __init__(
        self,
        config: ai.AIConfig,
        messages: list[dict[str, str]],
        *,
        system_hint: str = "",
    ) -> None:
        super().__init__()
        self.config = config
        self.messages = messages
        self.system_hint = system_hint
        self.signals = WorkerSignals()

    def run(self) -> None:  # pragma: no cover - 需要网络或打桩
        try:
            reply = ai.chat(self.config, self.messages, system_hint=self.system_hint)
        except ai.AIError as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - 任何异常都要回到界面
            self.signals.failed.emit(f"出了点问题：{exc}")
        else:
            self.signals.done.emit(reply)


class TestWorker(QRunnable):
    """设置页的「测试连接」。"""

    def __init__(self, config: ai.AIConfig) -> None:
        super().__init__()
        self.config = config
        self.signals = WorkerSignals()

    def run(self) -> None:  # pragma: no cover - 需要网络或打桩
        try:
            reply = ai.test_connection(self.config)
        except ai.AIError as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(f"出了点问题：{exc}")
        else:
            self.signals.done.emit(reply)
