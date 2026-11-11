"""视觉主题：两套配色（浅色 / 深色），浅色是默认。

切换机制：
    - 所有颜色都是**模块级变量**，`apply_theme()` 就地更新它们；
      界面代码一律写作 `theme.TEXT` 这类运行时引用，因此会读到新值；
    - 切换主题后会**重建整个主窗口**（见 `main.py` 的循环）。这样所有控件都用新配色
      重新构造一次，包括那些在 Python 里拼字符串设样式的地方（`setStyleSheet(f"color: {theme.TEXT}")`），
      不需要任何"局部刷新"，也就不会出现"某个角落还是旧颜色"的问题。

配色：浅色沿用 GitHub 的浅色系（与历史版本完全一致），深色对齐 GitHub Dark。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QIcon, QPalette

from . import paths

# ---------------------------------------------------------------- 浅色（默认）
LIGHT: dict[str, object] = {
    "BG": "#ffffff",
    "SURFACE": "#f6f8fa",
    "SURFACE_ALT": "#eef1f4",
    "BORDER": "#d0d7de",
    "BORDER_MUTED": "#e4e8ec",
    "TEXT": "#1f2328",
    "TEXT_MUTED": "#656d76",
    "TEXT_SUBTLE": "#8b949e",
    "ACCENT": "#0969da",
    "ACCENT_SOFT": "#ddf4ff",
    "ACCENT_HOVER": "#0860ca",
    "DANGER": "#cf222e",
    "PAGE_BG": "#f6f8fa",
    "SIDEBAR_BG": "#eef1f4",
    "BUTTON_PRESSED": "#e6eaee",
    "TOOLTIP_BG": "#24292f",
    "TOOLTIP_TEXT": "#ffffff",
    "SCROLL_HANDLE": "#d0d7de",
    "SCROLL_HANDLE_HOVER": "#afb8c1",
    "DISABLED_FG": "#8b949e",
    "SHADOW": "#d0d7de",
    "DARK_EDGE": "#afb8c1",
    "HEATMAP_DIM_EMPTY": "#f0f3f6",
    "TODAY_DOT": "#30a14e",
    "PINNED": "#9a6700",
    "LEVEL_COLORS": ("#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"),
    # 总热力图专用的两套 1~5 级：1–5 次绿色，6–10 次橙色
    "GLOBAL_GREEN": ("#9be9a8", "#40c463", "#30a14e", "#216e39", "#0e4429"),
    "GLOBAL_ORANGE": ("#ffd8a8", "#ffb86c", "#f0883e", "#d9651a", "#a33d0a"),
    "EMPTY_CELL_BORDER": "#d8dee4",
    "MAP_PALETTE": ("#0969da", "#8250df", "#bf8700", "#1a7f37", "#0e7490", "#cf222e"),
    "STATUS_STYLE": {
        "待开始": ("#656d76", "#f6f8fa"),
        "进行中": ("#0969da", "#ddf4ff"),
        "已暂停": ("#9a6700", "#fff8c5"),
        "已完成": ("#1a7f37", "#dafbe1"),
        "已归档": ("#656d76", "#eaeef2"),
    },
}

# ---------------------------------------------------------------- 深色（GitHub Dark）
DARK: dict[str, object] = {
    "BG": "#161b22",
    "SURFACE": "#21262d",
    "SURFACE_ALT": "#30363d",
    "BORDER": "#30363d",
    "BORDER_MUTED": "#21262d",
    "TEXT": "#e6edf3",
    "TEXT_MUTED": "#9198a1",
    "TEXT_SUBTLE": "#7d8590",
    "ACCENT": "#4493f8",
    "ACCENT_SOFT": "#12385f",
    "ACCENT_HOVER": "#58a6ff",
    "DANGER": "#f85149",
    "PAGE_BG": "#0d1117",
    "SIDEBAR_BG": "#010409",
    "BUTTON_PRESSED": "#30363d",
    "TOOLTIP_BG": "#e6edf3",
    "TOOLTIP_TEXT": "#0d1117",
    "SCROLL_HANDLE": "#484f58",
    "SCROLL_HANDLE_HOVER": "#6e7681",
    "DISABLED_FG": "#6e7681",
    "SHADOW": "#010409",
    "DARK_EDGE": "#010409",
    "HEATMAP_DIM_EMPTY": "#141922",
    "TODAY_DOT": "#3fb950",
    "PINNED": "#d29922",
    # 空格要"比页面底色亮一点点"：这样它是有形状的格子，而不是只剩描边的空框
    "LEVEL_COLORS": ("#1b2028", "#033a16", "#196c2e", "#2ea043", "#56d364"),
    "GLOBAL_GREEN": ("#033a16", "#196c2e", "#2ea043", "#56d364", "#7ee787"),
    "GLOBAL_ORANGE": ("#5a2c08", "#8a4a12", "#c26a1c", "#f0883e", "#ffb86c"),
    # 描边与空格同色：深色下不再画出一片网格轮廓
    "EMPTY_CELL_BORDER": "#1b2028",
    "MAP_PALETTE": ("#4493f8", "#a371f7", "#d29922", "#3fb950", "#39c5cf", "#f85149"),
    "STATUS_STYLE": {
        "待开始": ("#9198a1", "#21262d"),
        "进行中": ("#4493f8", "#12385f"),
        "已暂停": ("#d29922", "#3a2d06"),
        "已完成": ("#3fb950", "#0f2f16"),
        "已归档": ("#7d8590", "#21262d"),
    },
}

THEMES = {"light": LIGHT, "dark": DARK}
THEME_LABELS = {"light": "浅色", "dark": "深色"}

# 当前生效的颜色。这些名字与历史版本一致，界面代码直接 theme.XXX 引用，
# apply_theme() 会把它们就地换成另一套。
BG = LIGHT["BG"]
SURFACE = LIGHT["SURFACE"]
SURFACE_ALT = LIGHT["SURFACE_ALT"]
BORDER = LIGHT["BORDER"]
BORDER_MUTED = LIGHT["BORDER_MUTED"]
TEXT = LIGHT["TEXT"]
TEXT_MUTED = LIGHT["TEXT_MUTED"]
TEXT_SUBTLE = LIGHT["TEXT_SUBTLE"]
ACCENT = LIGHT["ACCENT"]
ACCENT_SOFT = LIGHT["ACCENT_SOFT"]
ACCENT_HOVER = LIGHT["ACCENT_HOVER"]
DANGER = LIGHT["DANGER"]
PAGE_BG = LIGHT["PAGE_BG"]
SIDEBAR_BG = LIGHT["SIDEBAR_BG"]
BUTTON_PRESSED = LIGHT["BUTTON_PRESSED"]
TOOLTIP_BG = LIGHT["TOOLTIP_BG"]
TOOLTIP_TEXT = LIGHT["TOOLTIP_TEXT"]
SCROLL_HANDLE = LIGHT["SCROLL_HANDLE"]
SCROLL_HANDLE_HOVER = LIGHT["SCROLL_HANDLE_HOVER"]
DISABLED_FG = LIGHT["DISABLED_FG"]
SHADOW = LIGHT["SHADOW"]
DARK_EDGE = LIGHT["DARK_EDGE"]
HEATMAP_DIM_EMPTY = LIGHT["HEATMAP_DIM_EMPTY"]
TODAY_DOT = LIGHT["TODAY_DOT"]
PINNED = LIGHT["PINNED"]
LEVEL_COLORS = LIGHT["LEVEL_COLORS"]
GLOBAL_GREEN = LIGHT["GLOBAL_GREEN"]
GLOBAL_ORANGE = LIGHT["GLOBAL_ORANGE"]
EMPTY_CELL_BORDER = LIGHT["EMPTY_CELL_BORDER"]
MAP_PALETTE = LIGHT["MAP_PALETTE"]
STATUS_STYLE = LIGHT["STATUS_STYLE"]

_current = "light"


def current_theme() -> str:
    return _current


def normalize(name: str | None) -> str:
    """把配置里读到的名字规范化；认不出来就退回浅色。"""
    return name if name in THEMES else "light"


def apply_theme(name: str) -> str:
    """切换配色：就地更新所有模块级颜色变量，返回实际生效的主题名。"""
    global _current
    _current = normalize(name)
    for key, value in THEMES[_current].items():
        globals()[key] = value
    return _current


def map_color(index: int) -> str:
    """思维导图里区分领域的强调色（按领域顺序循环使用）。"""
    return MAP_PALETTE[index % len(MAP_PALETTE)]


FONT_FAMILIES = "Microsoft YaHei UI, Microsoft YaHei, Segoe UI, PingFang SC, sans-serif"


def app_font() -> QFont:
    font = QFont("Microsoft YaHei UI")
    font.setPointSize(10)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font


def app_icon() -> QIcon:
    """应用图标（任务栏、标题栏、Alt-Tab 都用它）。

    优先用多尺寸的 `.ico`：Windows 在 16/24/32/48… 各档缩放下都能挑到对应尺寸，
    比单张 PNG 缩小更清晰；`.ico` 不在时退回 PNG；都读不到就返回空图标（不影响运行）。
    """
    for candidate in (paths.LOGO_ICO, paths.LOGO_PNG):
        if candidate.exists():
            icon = QIcon(str(candidate))
            if not icon.isNull():
                return icon
    return QIcon()


def palette() -> QPalette:
    """按当前主题生成调色板。

    桌面 UI 固定使用自己的配色，不跟随 Windows 深浅色设置 ——
    否则未被样式表覆盖的容器会被系统调色板渲染成另一种底色。
    """
    colors = QPalette()
    colors.setColor(QPalette.ColorRole.Window, QColor(BG))
    colors.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    colors.setColor(QPalette.ColorRole.Base, QColor(BG))
    colors.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE))
    colors.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    colors.setColor(QPalette.ColorRole.Button, QColor(SURFACE))
    colors.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    colors.setColor(QPalette.ColorRole.BrightText, QColor(TOOLTIP_TEXT))
    colors.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    colors.setColor(QPalette.ColorRole.HighlightedText, QColor(TOOLTIP_TEXT))
    colors.setColor(QPalette.ColorRole.ToolTipBase, QColor(TOOLTIP_BG))
    colors.setColor(QPalette.ColorRole.ToolTipText, QColor(TOOLTIP_TEXT))
    colors.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_SUBTLE))
    colors.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    colors.setColor(QPalette.ColorRole.Light, QColor(BORDER_MUTED))
    colors.setColor(QPalette.ColorRole.Midlight, QColor(BORDER_MUTED))
    colors.setColor(QPalette.ColorRole.Mid, QColor(BORDER))
    colors.setColor(QPalette.ColorRole.Dark, QColor(DARK_EDGE))
    colors.setColor(QPalette.ColorRole.Shadow, QColor(SHADOW))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        colors.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED_FG))
    colors.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor(SURFACE))
    colors.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor(SURFACE))
    return colors


def stylesheet() -> str:
    """按当前主题生成全局样式表（每次调用都读当前的颜色变量）。"""
    return f"""
    QWidget {{
        font-family: {FONT_FAMILIES};
        font-size: 13px;
        color: {TEXT};
    }}
    QMainWindow {{ background: {PAGE_BG}; }}
    QDialog {{ background: {BG}; }}
    /* 滚动区域的视口跟随页面底色，避免页面灰、滚动区白的两截感 */
    QScrollArea > QWidget#qt_scrollarea_viewport {{ background: {PAGE_BG}; }}
    QToolTip {{
        background: {TOOLTIP_BG};
        color: {TOOLTIP_TEXT};
        border: none;
        padding: 4px 6px;
    }}

    /* ---------------------------------------------------- 侧边导航 */
    #Sidebar {{ background: {SIDEBAR_BG}; border-right: 1px solid {BORDER_MUTED}; }}
    #SidebarTitle {{ color: {TEXT}; font-size: 16px; font-weight: 600; }}
    #SidebarSubtitle {{ color: {TEXT_SUBTLE}; font-size: 11px; }}
    /* 每个导航按钮是独立的圆角矩形，不再包在一个分组框里 */
    #NavButton {{
        text-align: center;
        background: {BG};
        border: 1px solid {BORDER_MUTED};
        border-radius: 10px;
        padding: 0;
    }}
    #NavButton:hover {{ background: {SURFACE}; border-color: {BORDER}; }}
    #NavButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; }}
    /* 主题切换：两个小段，选中那个是当前主题 */
    #ThemeSeg {{
        background: {BG};
        border: 1px solid {BORDER_MUTED};
        border-radius: 8px;
    }}
    #ThemeButton {{
        border: none; border-radius: 7px; padding: 4px 0;
        color: {TEXT_MUTED}; background: transparent;
    }}
    #ThemeButton:hover {{ color: {TEXT}; }}
    #ThemeButton:checked {{ background: {ACCENT_SOFT}; color: {ACCENT}; font-weight: 600; }}

    /* -------------------------------------------------------- 顶栏 */
    #TopBar {{ background: {BG}; border-bottom: 1px solid {BORDER_MUTED}; }}
    #PageTitle {{ font-size: 15px; font-weight: 600; }}

    /* -------------------------------------------------------- 卡片 */
    #Card {{
        background: {BG};
        border: 1px solid {BORDER};
        border-radius: 8px;
    }}
    #FocusCard {{
        background: {BG};
        border: 1px solid {BORDER};
        border-radius: 8px;
    }}
    #SectionHeader {{ color: {TEXT}; font-size: 13px; font-weight: 600; }}
    #Muted {{ color: {TEXT_MUTED}; font-size: 12px; }}
    #Subtle {{ color: {TEXT_SUBTLE}; font-size: 11px; }}
    #Mono {{ font-family: Consolas, "Cascadia Mono", monospace; font-size: 12px; color: {TEXT_MUTED}; }}

    /* -------------------------------------------------------- 按钮 */
    QPushButton {{
        background: {BG};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 5px 12px;
        color: {TEXT};
    }}
    QPushButton:hover {{ background: {SURFACE_ALT}; }}
    QPushButton:pressed {{ background: {BUTTON_PRESSED}; }}
    QPushButton:disabled {{ color: {DISABLED_FG}; background: {SURFACE}; border-color: {BORDER_MUTED}; }}
    QPushButton#Primary {{ background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; font-weight: 600; }}
    QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
    QPushButton#Ghost {{ background: transparent; border: none; color: {ACCENT}; padding: 3px 6px; }}
    QPushButton#Ghost:hover {{ background: {ACCENT_SOFT}; border-radius: 6px; }}
    QPushButton#Collapse {{
        background: transparent; border: 1px solid transparent; color: {TEXT_MUTED};
        font-size: 15px; padding: 2px 8px;
    }}
    QPushButton#Collapse:hover {{ background: {SURFACE_ALT}; border-radius: 6px; color: {TEXT}; }}
    QPushButton#SectionToggle {{
        text-align: left; background: transparent; border: none;
        color: {TEXT}; font-weight: 600; padding: 4px 2px;
    }}
    QPushButton#SectionToggle:hover {{ color: {ACCENT}; }}

    /* --------------------------------------------------- 输入控件 */
    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDateEdit, QSpinBox {{
        background: {BG};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 4px 8px;
        selection-background-color: {ACCENT_SOFT};
        selection-color: {TEXT};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus {{
        border-color: {ACCENT};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: {BG}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_SOFT};
        selection-color: {TEXT}; outline: none;
    }}
    QCheckBox {{ color: {TEXT_MUTED}; spacing: 6px; }}

    /* ----------------------------------------------------- 树与表 */
    QTreeWidget, QTableWidget, QListWidget {{
        background: {BG};
        border: 1px solid {BORDER};
        border-radius: 8px;
        outline: none;
        gridline-color: {BORDER_MUTED};
    }}
    QTreeWidget::item, QListWidget::item {{ padding: 4px 2px; }}
    QTreeWidget::item:selected, QTableWidget::item:selected, QListWidget::item:selected {{
        background: {ACCENT_SOFT}; color: {TEXT};
    }}
    QHeaderView::section {{
        background: {SURFACE};
        color: {TEXT_MUTED};
        border: none;
        border-bottom: 1px solid {BORDER_MUTED};
        border-right: 1px solid {BORDER_MUTED};
        padding: 6px 8px;
        font-weight: 600;
    }}
    QTableWidget {{ gridline-color: {BORDER_MUTED}; }}

    /* ----------------------------------------------------- 滚动条 */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {SCROLL_HANDLE}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {SCROLL_HANDLE_HOVER}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {SCROLL_HANDLE}; border-radius: 5px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QSplitter::handle {{ background: {BORDER_MUTED}; }}
    QSplitter::handle:hover {{ background: {BORDER}; }}
    QScrollArea {{ border: none; background: {PAGE_BG}; }}
    QStatusBar {{ background: {BG}; color: {TEXT_MUTED}; border-top: 1px solid {BORDER_MUTED}; }}
    QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; }}
    QTabBar::tab {{ padding: 6px 12px; color: {TEXT_MUTED}; }}
    QTabBar::tab:selected {{ color: {TEXT}; font-weight: 600; }}
    QMenu {{ background: {BG}; border: 1px solid {BORDER}; padding: 4px; }}
    QMenu::item {{ padding: 5px 20px 5px 12px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
    QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 12px; padding-top: 10px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; color: {TEXT_MUTED}; }}

    /* ------------------------------------------ 热力图页（一行一个节点） */
    #HeatmapDivider {{ background: {BORDER_MUTED}; border: none; }}
    #HeatmapRow, #HeatmapRowArea {{ background: transparent; border-radius: 8px; }}
    #HeatmapRow:hover, #HeatmapRowArea:hover {{ background: {SURFACE_ALT}; }}
    #RowToggle {{
        background: transparent; border: none; padding: 0;
        color: {TEXT_MUTED}; font-size: 10px;
    }}
    #RowToggle:hover {{ color: {ACCENT}; }}

    /* ----------------------------------------------- 快速记录（Ctrl+N） */
    #QuickCapture {{ background: {BG}; border: 1px solid {BORDER}; }}
    #QuickCaptureInput {{
        background: transparent;
        border: none;
        border-bottom: 1px solid {BORDER_MUTED};
        border-radius: 0;
        padding: 12px 14px;
        font-size: 16px;
    }}
    #QuickCaptureInput:focus {{ border-bottom: 1px solid {ACCENT}; }}
    #QuickCaptureList {{ background: {BG}; border: none; border-radius: 0; }}
    #QuickCaptureList::item {{ padding: 6px 14px; }}
    #QuickCaptureHint {{ color: {TEXT_SUBTLE}; font-size: 11px; padding: 6px 14px; }}

    /* --------------------------------------------- 日记快速记录（Ctrl+M） */
    #DiaryCapture {{ background: {BG}; border: 1px solid {BORDER}; }}
    #DiaryCaptureInput {{
        background: transparent;
        border: none;
        border-radius: 0;
        padding: 12px 14px;
        font-size: 15px;
    }}
    """
