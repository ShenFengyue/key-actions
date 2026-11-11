"""左侧导航（可收起）。

六个导航按钮是**彼此独立的圆角矩形**（白底 + 1px 浅边框，选中时浅蓝底 + 蓝边 + 蓝字），
整组垂直居中在左栏中间；「数据」样式相同，固定在左栏最下面。
快捷键不再显示在界面上，但功能仍然保留（注册在 main_window 里）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
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


class NavButton(QPushButton):
    """一个导航按钮：名称居中；样式（圆角、边框、选中态）见 theme.stylesheet()。"""

    def __init__(self, label: str, shortcut: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(44)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(8)
        self.label = QLabel(label)
        self.label.setObjectName("NavLabel")
        row.addStretch(1)
        row.addWidget(self.label)
        row.addStretch(1)
        # 快捷键不再显示在界面上（功能保留，注册在 main_window 里）
        self.shortcut_text = shortcut
        # 选中状态的文字颜色在代码里切换：
        # Qt 的样式表对「后代选择器 + 伪状态」（如 #NavButton:checked #NavLabel）支持不可靠，
        # 写成那样会让所有条目都套用选中样式。
        self.toggled.connect(self._sync_label_style)
        self._sync_label_style(False)

    def _sync_label_style(self, checked: bool) -> None:
        color = theme.ACCENT if checked else theme.TEXT
        weight = 600 if checked else 500
        self.label.setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: {weight}; background: transparent;"
        )


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
    """极简导航：支持整体收起，收起后由顶栏的切换按钮重新展开。"""

    pageSelected = Signal(str)
    sloganEditRequested = Signal()
    themeRequested = Signal(str)

    WIDTH = 248
    # 间距分三档：组内最紧、两组之间一档、与总览 / 与日记最大
    BUTTON_GAP = 8
    GROUP_GAP = 18
    HOME_GAP = 26
    TOP_GAP = 22
    BOTTOM_GAP = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(self.WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(0)

        title = QLabel("Key Actions")
        title.setObjectName("SidebarTitle")
        subtitle = QLabel(f"v{__version__} · 本地单机")
        subtitle.setObjectName("SidebarSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        # 标语和标题之间留出更多空白：原来贴着标题显得太靠上
        layout.addSpacing(30)
        self._slogan_text = ""
        self.slogan_label = SloganLabel()
        self.slogan_label.editRequested.connect(self.sloganEditRequested.emit)
        layout.addWidget(self.slogan_label)
        self.set_slogan("")

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}

        # 总览紧贴在标语下方（比原来"整组居中"的位置更靠上）
        layout.addSpacing(self.TOP_GAP)
        for index, (key, label, shortcut) in enumerate(NAV_ITEMS):
            if key == "heatmaps":
                layout.addSpacing(self.HOME_GAP)   # 与总览的距离：最大一档
            elif key == "processes":
                layout.addSpacing(self.GROUP_GAP)  # 两组之间：小一档
            elif index:
                layout.addSpacing(self.BUTTON_GAP)
            self._add_button(layout, key, label, shortcut)

        # 剩下的空白全部留在下面：日记与数据贴着底部
        layout.addStretch(1)
        for index, (key, label, shortcut) in enumerate(BOTTOM_ITEMS):
            if index:
                layout.addSpacing(self.BOTTOM_GAP)
            self._add_button(layout, key, label, shortcut)

        # 最底下：配色切换（两个小段，选中的那个是当前主题）
        layout.addSpacing(10)
        segment = QFrame()
        segment.setObjectName("ThemeSeg")
        segment_layout = QHBoxLayout(segment)
        segment_layout.setContentsMargins(3, 3, 3, 3)
        segment_layout.setSpacing(3)
        self.theme_buttons: dict[str, QPushButton] = {}
        for key in ("light", "dark"):
            button = QPushButton(theme.THEME_LABELS[key])
            button.setObjectName("ThemeButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, name=key: self.themeRequested.emit(name)
            )
            segment_layout.addWidget(button)
            self.theme_buttons[key] = button
        layout.addWidget(segment)
        self.set_theme(theme.current_theme())

        self.set_current("overview")

    def set_theme(self, name: str) -> None:
        """把当前主题那一小段标成选中。"""
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
            f"color: {color}; font-size: 12px; background: transparent;"
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
