"""GitHub 风格热力图控件。

形状与配色对齐 GitHub 贡献图：
    - 方格边长 11px、间距 3px、圆角 2px；
    - 横向按周排列（一列一周），纵向按星期排列（周日在上，与 GitHub 一致）；
    - 顶部显示月份标记，左侧显示星期一/三/五；
    - 绿色阶梯固定分档：1 / 2 / 3 / 4 次及以上；
    - 悬停显示日期、次数与摘要，点击查看当天全部记录。
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from .. import clock, heatmap as heatmap_stats, theme

WEEKDAY_LABELS = {1: "周一", 3: "周三", 5: "周五"}
WEEKDAY_NAMES = ("周日", "周一", "周二", "周三", "周四", "周五", "周六")


class HeatmapCalendar(QWidget):
    """单个作用域（Area 或 Process）的 GitHub 风格日历热力图。"""

    CELL = 11
    GAP = 3
    RADIUS = 2
    LEFT_GUTTER = 30
    TOP_GUTTER = 18

    dateClicked = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        weeks: int = heatmap_stats.DEFAULT_WEEKS,
        scale: float = 1.0,
        hot: bool = False,
    ) -> None:
        super().__init__(parent)
        self._weeks = weeks
        # 总热力图专用：多一档橙色，用来区分"记了很多条"的日子
        self._hot = bool(hot)
        # scale 让总热力图可以做得明显比其它热力图更大
        self._scale = max(0.6, float(scale))
        self._cell = self.CELL * self._scale
        self._gap = self.GAP * self._scale
        self._step = self._cell + self._gap
        self._radius = self.RADIUS * self._scale
        self._left_gutter = self.LEFT_GUTTER * self._scale
        self._top_gutter = self.TOP_GUTTER * self._scale
        self._grid = heatmap_stats.build_calendar(weeks=weeks)
        self._counts: dict[str, int] = {}
        self._summaries: dict[str, list[str]] = {}
        # 整个窗口内一条记录都没有时为 True（整张图淡化显示）
        self._dim = True
        self._hover: tuple[int, int] | None = None
        self._labels_font = QFont()
        self._labels_font.setPointSize(max(8, round(8 * self._scale)))
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._apply_size()

    # ------------------------------------------------------------ 数据
    def set_window(self, grid: heatmap_stats.CalendarGrid) -> None:
        self._weeks = grid.weeks
        self._grid = grid
        self._apply_size()
        self.update()

    def refresh_window(self, end_date: date | None = None) -> bool:
        """把日历窗口对齐到最新（默认今天）。

        程序一直开着跨过午夜时，用这个方法把窗口往前挪一天。
        返回是否真的挪动了。
        """
        target_end = end_date or clock.today()
        if self._grid.end == target_end:
            return False
        self.set_window(heatmap_stats.build_calendar(end_date=target_end, weeks=self._weeks))
        return True

    def set_data(self, counts: dict[str, int], summaries: dict[str, list[str]] | None = None) -> None:
        self._counts = dict(counts)
        self._summaries = dict(summaries or {})
        self._dim = not any(self._counts.values())
        self.update()

    @property
    def is_empty(self) -> bool:
        """当前窗口内没有任何活动记录。"""
        return self._dim

    @property
    def grid(self) -> heatmap_stats.CalendarGrid:
        """当前显示的日历窗口（统计时使用同一窗口以保证口径一致）。"""
        return self._grid

    def totals(self) -> tuple[int, int]:
        return heatmap_stats.window_totals(self._counts)

    # ------------------------------------------------------------ 布局
    def _apply_size(self) -> None:
        width = self._left_gutter + self._weeks * self._step - self._gap
        height = self._top_gutter + 7 * self._step - self._gap
        self.setFixedSize(int(round(width)), int(round(height)))

    def sizeHint(self) -> QSize:  # pragma: no cover - Qt 覆写
        return QSize(
            int(round(self._left_gutter + self._weeks * self._step - self._gap)),
            int(round(self._top_gutter + 7 * self._step - self._gap)),
        )

    def _cell_rect(self, column: int, row: int) -> QRectF:
        x = self._left_gutter + column * self._step
        y = self._top_gutter + row * self._step
        return QRectF(x, y, self._cell, self._cell)

    def _cell_at(self, pos: QPoint) -> tuple[int, int] | None:
        column = int((pos.x() - self._left_gutter) // self._step)
        row = int((pos.y() - self._top_gutter) // self._step)
        if not (0 <= column < self._weeks and 0 <= row < 7):
            return None
        if pos.x() - self._left_gutter - column * self._step > self._cell:
            return None
        if pos.y() - self._top_gutter - row * self._step > self._cell:
            return None
        if self._grid.columns[column][row] is None:
            return None
        return int(column), int(row)

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(self._labels_font)
        painter.setPen(QPen(QColor(theme.TEXT_SUBTLE)))

        # 月份标记
        for column, label in self._grid.month_labels:
            x = self._left_gutter + column * self._step
            painter.drawText(
                QRect(int(x), 0, 44, int(self._top_gutter)),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

        # 星期标记
        text_rect_height = int(self._cell) + 2
        for row, label in WEEKDAY_LABELS.items():
            y = self._top_gutter + row * self._step - 1
            painter.drawText(
                QRect(0, int(y), int(self._left_gutter) - 6, text_rect_height),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

        # 方格
        # 空格描边也用主题色（深色下它与空格填充同色，等于不描边，不会画出一片网格）
        empty_border = QPen(QColor(theme.EMPTY_CELL_BORDER))
        empty_border.setWidthF(0.8)
        for column, days in enumerate(self._grid.columns):
            for row, day in enumerate(days):
                if day is None:
                    continue
                count = self._counts.get(day, 0)
                level = (
                    heatmap_stats.level_for_global(count)
                    if self._hot
                    else heatmap_stats.level_for(count)
                )
                rect = self._cell_rect(column, row)
                painter.setPen(Qt.PenStyle.NoPen)
                if self._dim:
                    # 一条记录都没有：只留极淡的格纹，不描边，不去抢视线
                    painter.setBrush(QColor(theme.HEATMAP_DIM_EMPTY))
                    painter.drawRoundedRect(rect, self._radius, self._radius)
                    continue
                palette = (
                    (theme.LEVEL_COLORS[0],) + theme.GLOBAL_GREEN + theme.GLOBAL_ORANGE
                    if self._hot
                    else theme.LEVEL_COLORS
                )
                painter.setBrush(QColor(palette[level]))
                painter.drawRoundedRect(rect, self._radius, self._radius)
                if level == 0:
                    painter.setPen(empty_border)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(rect, self._radius, self._radius)
                    painter.setPen(Qt.PenStyle.NoPen)

        # 悬停高亮
        if self._hover is not None:
            column, row = self._hover
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(QColor(theme.TEXT))
            pen.setWidthF(1.4)
            painter.setPen(pen)
            painter.drawRoundedRect(self._cell_rect(column, row), self._radius, self._radius)
        painter.end()

    # ------------------------------------------------------------ 交互
    def _tooltip_for(self, day: str) -> str:
        count = self._counts.get(day, 0)
        parsed = date.fromisoformat(day)
        title = f"{day} {WEEKDAY_NAMES[(parsed.weekday() + 1) % 7]}"
        if count == 0:
            return f"{title}\n无活动记录"
        lines = [title, f"{count} 次活动"]
        for summary in self._summaries.get(day, [])[:3]:
            lines.append(f"· {summary}")
        if count > len(self._summaries.get(day, [])[:3]):
            lines.append(f"… 还有 {count - len(self._summaries.get(day, [])[:3])} 条")
        return "\n".join(lines)

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        cell = self._cell_at(event.position().toPoint())
        if cell != self._hover:
            self._hover = cell
            self.update()
        if cell is None:
            QToolTip.hideText()
        else:
            day = self._grid.columns[cell[0]][cell[1]]
            if day:
                QToolTip.showText(event.globalPosition().toPoint(), self._tooltip_for(day), self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        self._hover = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        if event.button() == Qt.MouseButton.LeftButton:
            cell = self._cell_at(event.position().toPoint())
            if cell is not None:
                day = self._grid.columns[cell[0]][cell[1]]
                if day:
                    self.dateClicked.emit(day)
        super().mouseReleaseEvent(event)


class HeatmapLegend(QWidget):
    """GitHub 风格图例：少 ▢▢▢▢▢ 多。"""

    CELL = 11
    GAP = 3

    def __init__(
        self,
        parent: QWidget | None = None,
        text_less: str = "少",
        text_more: str = "多",
        scale: float = 1.0,
        hot: bool = False,
    ) -> None:
        super().__init__(parent)
        self._less = text_less
        self._more = text_more
        self._hot = bool(hot)
        self._scale = max(0.6, float(scale))
        self._cell = self.CELL * self._scale
        self._gap = self.GAP * self._scale
        font = QFont()
        font.setPointSize(max(8, round(8 * self._scale)))
        self.setFont(font)
        # 总热力图用两行图例：橙色（6–10）在上，绿色（1–5）在下
        rows = 2 if self._hot else 1
        self.setFixedHeight(int(round((self._cell + 6) * rows)))
        self.setFixedWidth(int(round(150 * self._scale)))

    def paintEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(self.font())
        painter.setPen(QPen(QColor(theme.TEXT_SUBTLE)))
        metrics = painter.fontMetrics()
        rows = (
            (("6–10", theme.GLOBAL_ORANGE), ("1–5", theme.GLOBAL_GREEN))
            if self._hot
            else ((self._less, theme.LEVEL_COLORS),)
        )
        label_width = 46.0 * self._scale if self._hot else 22.0
        row_height = self.height() / len(rows)
        for index, (label, colors) in enumerate(rows):
            top = index * row_height
            y = top + (row_height - self._cell) / 2
            painter.setPen(QPen(QColor(theme.TEXT_SUBTLE)))
            painter.drawText(
                QRect(0, int(top), int(label_width), int(row_height)),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            x = label_width
            for color in colors:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                painter.drawRoundedRect(QRectF(x, y, self._cell, self._cell), 2, 2)
                x += self._cell + self._gap
            if not self._hot:
                painter.setPen(QPen(QColor(theme.TEXT_SUBTLE)))
                painter.drawText(
                    QRect(
                        int(x) + 2,
                        int(top),
                        metrics.horizontalAdvance(self._more) + 4,
                        int(row_height),
                    ),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    self._more,
                )
        painter.end()
