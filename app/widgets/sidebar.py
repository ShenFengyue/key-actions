"""左侧导航（可收起）。

导航是**文字列表**，不是一列按钮：没有底色、没有边框，只有一条 1px 竖线把左栏和内容分开。
选中项用左侧 2px 竖条 + 加粗表示，因此一屏里只有一个「重」元素。
收起后由顶栏的切换按钮重新展开；快捷键不在界面上展示，但功能保留（注册在 main_window 里）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from .. import theme

NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("overview", "总览", "Ctrl+1"),
    ("ai", "AI", "Ctrl+8"),
    ("heatmaps", "热力图", "Ctrl+2"),
    ("mindmap", "思维导图", "Ctrl+3"),
    ("processes", "进程树", "Ctrl+5"),
    ("activity", "活动记录", "Ctrl+4"),
)

# 贴着左栏底部的一组：日记 + 数据 + 设置
BOTTOM_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("diary", "日记", "Ctrl+6"),
    ("data", "数据", "Ctrl+7"),
    ("settings", "设置", "Ctrl+9"),
)

ALL_NAV_ITEMS: tuple[tuple[str, str, str], ...] = NAV_ITEMS + BOTTOM_ITEMS

# 导航项高度：32px 是"文字列表"的节奏，不再是 44px 的按钮
NAV_HEIGHT = 32
# 选中态的竖条：宽度与上下内缩
BAR_WIDTH = 2
BAR_INSET = 6


class NavButton(QPushButton):
    """导航项：左对齐的文字；选中时自己在左侧画一条竖条。

    Qt 的样式表画不了「只在选中时出现的竖条」，所以在 paintEvent 里补两笔。
    """

    def __init__(self, label: str, shortcut: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(NAV_HEIGHT)
        self.setFlat(True)
        # 快捷键不再显示在界面上（功能保留，注册在 main_window 里）
        self.shortcut_text = shortcut

    def paintEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        super().paintEvent(event)
        if not self.isChecked():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawRoundedRect(0, BAR_INSET, BAR_WIDTH, self.height() - BAR_INSET * 2, 1, 1)
        painter.end()


class SloganLabel(QLabel):
    """左栏顶部的标语：双击就能改，改过的历史都存在数据库里。"""

    editRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarSlogan")
        self.setWordWrap(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("双击修改标语；以前的版本在「数据」页面能看到")

    def mouseDoubleClickEvent(self, event) -> None:  # pragma: no cover - 需要 GUI
        if event.button() == Qt.MouseButton.LeftButton:
            self.editRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class Sidebar(QWidget):
    """极简导航：一条竖线 + 一列文字。支持整体收起。"""

    pageSelected = Signal(str)
    sloganEditRequested = Signal()
    themeRequested = Signal(str)

    WIDTH = 206
    # 分三档：组内 2px、两组之间 16px、标语与导航之间 22px
    BUTTON_GAP = 2
    GROUP_GAP = 16
    TOP_GAP = 22

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(self.WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 26, 20, 20)
        layout.setSpacing(0)

        title = QLabel("Key Actions")
        title.setObjectName("SidebarTitle")
        subtitle = QLabel(f"v{__version__} · 本地单机")
        subtitle.setObjectName("SidebarSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        # 标语和标题之间留出更多空白：原来贴着标题显得太靠上
        layout.addSpacing(26)
        self._slogan_text = ""
        self.slogan_label = SloganLabel()
        self.slogan_label.editRequested.connect(self.sloganEditRequested.emit)
        layout.addWidget(self.slogan_label)
        self.set_slogan("")

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}

        layout.addSpacing(self.TOP_GAP)
        for index, (key, label, shortcut) in enumerate(NAV_ITEMS):
            if key == "heatmaps":
                layout.addSpacing(self.GROUP_GAP)   # 核心三页 / 三个视图 之间的分界
            elif index:
                layout.addSpacing(self.BUTTON_GAP)
            self._add_button(layout, key, label, shortcut)

        # 剩下的空白全部留在下面：日记、数据、设置贴着底部
        layout.addStretch(1)
        for index, (key, label, shortcut) in enumerate(BOTTOM_ITEMS):
            if index:
                layout.addSpacing(self.BUTTON_GAP)
            self._add_button(layout, key, label, shortcut)

        # 最底下：配色切换（两个文字项，当前那个加粗）
        theme_row = QHBoxLayout()
        theme_row.setContentsMargins(2, 0, 0, 0)
        theme_row.setSpacing(14)
        self.theme_buttons: dict[str, QPushButton] = {}
        for key in ("light", "dark"):
            button = QPushButton(theme.THEME_LABELS[key])
            button.setObjectName("ThemeText")
            button.setCheckable(True)
            button.setFlat(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, name=key: self.themeRequested.emit(name)
            )
            theme_row.addWidget(button)
            self.theme_buttons[key] = button
        theme_row.addStretch(1)
        layout.addSpacing(12)
        layout.addLayout(theme_row)
        self.set_theme(theme.current_theme())

        self.set_current("overview")

    def set_theme(self, name: str) -> None:
        """把当前主题那一项标成选中。"""
        for key, button in self.theme_buttons.items():
            button.setChecked(key == name)

    # ---------------------------------------------------------------- 标语
    def set_slogan(self, text: str) -> None:
        """显示标语；空着的时候给一句灰字提示，不占额外位置。"""
        text = (text or "").strip()
        self._slogan_text = text
        if text:
            self.slogan_label.setText(text)
            color = theme.TEXT_MUTED
        else:
            self.slogan_label.setText("双击这里写一句标语")
            color = theme.TEXT_SUBTLE
        self.slogan_label.setStyleSheet(
            f"color: {color}; font-size: 12px; background: transparent; line-height: 150%;"
        )

    def slogan_text(self) -> str:
        return self._slogan_text

    def _add_button(self, layout: QVBoxLayout, key: str, label: str, shortcut: str) -> None:
        button = NavButton(label, shortcut)
        button.clicked.connect(
            lambda _checked=False, page_key=key: self.pageSelected.emit(page_key)
        )
        layout.addWidget(button)
        self._group.addButton(button)
        self.buttons[key] = button

    def set_current(self, key: str) -> None:
        button = self.buttons.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)
