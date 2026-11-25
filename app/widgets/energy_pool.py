"""能量池水缸控件：QPainter 绘制玻璃方缸 + 正弦波液面，轻量动画。

由 pages/energy.py 的共享定时器驱动相位；页面隐藏时定时器暂停，不空转。
颜色全部走 theme 运行时引用，浅色 / 深色两套配色都适配。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .. import theme
from ..energy import TOTAL_UNITS, units_to_percent


class PoolCard(QWidget):
    """单个能量池卡片：上方方形水缸，下方名称。点击发出 clicked 信号（池 id）。"""

    clicked = Signal(str)

    def __init__(self, pool: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pool = pool
        self._phase = 0.0
        self.setMinimumSize(150, 182)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击编辑该能量池")

    def set_pool(self, pool: dict) -> None:
        self.pool = pool
        self.update()

    def set_phase(self, phase: float) -> None:
        self._phase = phase
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.pool["id"])
        super().mousePressEvent(event)

    # ---------------------------------------------------------------- 绘制

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        name_h = 26
        side = min(w, h - name_h) - 8
        x = (w - side) / 2
        tank = QRectF(x, 4, side, side)
        radius = side * 0.09
        dark = theme.current_theme() == "dark"

        # 玻璃缸体：深色里透一点白、浅色里透一点墨，都只留极淡的一层
        glass = QPainterPath()
        glass.addRoundedRect(tank, radius, radius)
        p.fillPath(glass, QColor(255, 255, 255, 9) if dark else QColor(31, 35, 40, 6))

        inner = tank.adjusted(3, 3, -3, -3)
        inner_path = QPainterPath()
        inner_path.addRoundedRect(inner, radius - 2, radius - 2)
        p.setClipPath(inner_path)

        units = self.pool["percentage_units"]
        level = units / TOTAL_UNITS
        color = QColor(self.pool["color"])
        if level > 0:
            amp = 0.0 if level >= 0.999 else max(1.5, side * 0.016)
            surface_y = inner.bottom() - level * inner.height()
            # 后层淡波，增加层次
            back = self._wave_path(inner, surface_y + amp * 0.6, amp,
                                   self._phase * 0.8 + math.pi / 2)
            back_color = QColor(color)
            back_color.setAlpha(90)
            p.fillPath(back, back_color)
            # 主液面
            front = self._wave_path(inner, surface_y, amp, self._phase)
            main_color = QColor(color)
            main_color.setAlpha(205)
            p.fillPath(front, main_color)

        # 左侧玻璃高光（浅色下主要落在液面上才看得见，空缸部分自然隐去）
        highlight = QRectF(inner.left() + inner.width() * 0.07, inner.top() + 6,
                           inner.width() * 0.055, inner.height() - 12)
        hp = QPainterPath()
        hp.addRoundedRect(highlight, 3, 3)
        p.fillPath(hp, QColor(255, 255, 255, 16 if dark else 110))
        p.setClipping(False)

        # 缸体描边
        p.setPen(QPen(QColor(theme.BORDER), 1.4))
        p.drawPath(glass)

        # 百分比文字（带阴影保证水上水下都可读）
        text = units_to_percent(units) + "%"
        font = QFont(self.font())
        font.setPointSizeF(max(11.0, side * 0.115))
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(0, 0, 0, 130))
        p.drawText(tank.adjusted(1, 1, 1, 1), Qt.AlignmentFlag.AlignCenter, text)
        p.setPen(QColor(theme.TEXT))
        p.drawText(tank, Qt.AlignmentFlag.AlignCenter, text)

        # 名称
        name_font = QFont(self.font())
        name_font.setPointSizeF(10.0)
        p.setFont(name_font)
        p.setPen(QColor(theme.TEXT_MUTED))
        name_rect = QRectF(0, tank.bottom() + 4, w, name_h - 4)
        metrics = p.fontMetrics()
        name = metrics.elidedText(self.pool["name"], Qt.TextElideMode.ElideRight, int(w - 8))
        p.drawText(name_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, name)
        p.end()

    @staticmethod
    def _wave_path(inner: QRectF, surface_y: float, amp: float,
                   phase: float) -> QPainterPath:
        path = QPainterPath()
        wavelength = inner.width() / 1.3
        steps = max(12, int(inner.width() / 5))
        first_y = surface_y + amp * math.sin(phase)
        path.moveTo(inner.left(), first_y)
        for i in range(1, steps + 1):
            fx = i / steps
            px = inner.left() + fx * inner.width()
            py = surface_y + amp * math.sin(2 * math.pi * fx * inner.width()
                                            / wavelength + phase)
            path.lineTo(QPointF(px, py))
        path.lineTo(inner.right(), inner.bottom() + 4)
        path.lineTo(inner.left(), inner.bottom() + 4)
        path.closeSubpath()
        return path