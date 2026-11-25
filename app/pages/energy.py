"""能量分配页：响应式水缸网格 + 轻量共享动画。

把 100% 的精力分给若干个能量池：点水缸改占比，其余池按原比例自动分摊，
总和始终 100%。「空余能量」池常驻，删除池的额度全部流入它。
业务逻辑在 app/energy.py，这里只负责展示与交互。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..energy import EnergyManager, units_to_percent
from ..services import LifeManagerService
from ..widgets.energy_dialogs import PoolDialog
from ..widgets.energy_pool import PoolCard

CARD_SLOT = 200  # 每个卡片占位宽度，用于计算列数


class EnergyPage(QWidget):
    changed = Signal()

    def __init__(self, service: LifeManagerService,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.manager = EnergyManager(
            service.db, on_change=service.notify_external_change)
        self._cards: list[PoolCard] = []
        self._phase = 0.0
        self._cols = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        self.total_label = QLabel()
        self.total_label.setStyleSheet(
            f"color: {theme.ACCENT}; font-size: 14px; font-weight: 600;")
        hint = QLabel("点水缸调整占比，其余池自动分摊，总和始终 100%")
        hint.setObjectName("Muted")
        add_btn = QPushButton("＋ 新增能量池")
        add_btn.setObjectName("Primary")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._add_pool)
        header.addWidget(self.total_label)
        header.addSpacing(14)
        header.addWidget(hint)
        header.addStretch(1)
        header.addWidget(add_btn)
        root.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(16)
        self.grid.setContentsMargins(0, 0, 8, 8)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)

        # 共享定时器驱动所有可见水缸，页面隐藏时暂停
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

        self.refresh()

    # ---------------------------------------------------------------- 数据

    def refresh(self) -> None:
        pools = self.manager.pools()
        for card in self._cards:
            self.grid.removeWidget(card)
            # 立即隐藏再延迟销毁：deleteLater 要等事件循环，
            # 不先 hide 的话旧卡片会和新卡片叠在一起出现"残影"。
            card.hide()
            card.deleteLater()
        self._cards = []
        for pool in pools:
            card = PoolCard(pool)
            card.clicked.connect(self._edit_pool)
            self._cards.append(card)
        total = sum(p["percentage_units"] for p in pools)
        self.total_label.setText(f"总计 {units_to_percent(total)}%")
        self._cols = 0
        self._relayout()

    def _add_pool(self) -> None:
        dlg = PoolDialog(self.manager, None, len(self._cards), self)
        if dlg.exec():
            self.changed.emit()

    def _edit_pool(self, pool_id: str) -> None:
        pool = next((p for p in self.manager.pools() if p["id"] == pool_id), None)
        if pool is None:
            return
        dlg = PoolDialog(self.manager, pool, len(self._cards), self)
        if dlg.exec():
            self.changed.emit()

    # ---------------------------------------------------------- 布局 / 动画

    def _relayout(self) -> None:
        if not self._cards:
            return
        cols = max(1, self.scroll.viewport().width() // CARD_SLOT)
        if cols == self._cols:
            return
        self._cols = cols
        for card in self._cards:
            self.grid.removeWidget(card)
        for i, card in enumerate(self._cards):
            self.grid.addWidget(card, i // cols, i % cols)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._relayout()

    def _tick(self) -> None:
        self._phase += 0.085
        for card in self._cards:
            card.set_phase(self._phase)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        self._relayout()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().hideEvent(event)
        self._timer.stop()