"""主窗口：可收起的左侧导航 + 五个页面。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QByteArray, QRect, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import clock, paths, theme
from .appconfig import AppConfig
from .pages.activity import ActivityPage
from .pages.ai import AIPage
from .pages.data import DataPage
from .pages.diary import DiaryPage
from .pages.heatmaps import HeatmapsPage
from .pages.mindmap import MindMapPage
from .pages.overview import OverviewPage
from .pages.processes import ProcessesPage
from .pages.settings import SettingsPage
from .services import SLOGAN_MAX_LENGTH, LifeManagerService, ValidationError
from .widgets.common import QuickRecordDialog, warn
from .widgets.diary_capture import DiaryCaptureDialog
from .widgets.quick_capture import QuickCaptureDialog
from .widgets.sidebar import ALL_NAV_ITEMS, Sidebar
from .models import today_str

PAGE_TITLES = {
    "overview": "总览",
    "ai": "AI",
    "mindmap": "思维导图",
    "processes": "进程树",
    "heatmaps": "热力图",
    "activity": "活动记录",
    "diary": "日记",
    "data": "数据",
    "settings": "设置",
}


def clamp_to_available(frame: QRect, available: QRect) -> QRect:
    """把窗口矩形校正到屏幕可用区域内：先限制尺寸，再把位置推回可见范围。"""
    width = min(frame.width(), available.width())
    height = min(frame.height(), available.height())
    left = min(max(frame.left(), available.left()), available.right() - width + 1)
    top = min(max(frame.top(), available.top()), available.bottom() - height + 1)
    return QRect(left, top, width, height)


class MainWindow(QMainWindow):
    def __init__(self, service: LifeManagerService, config: AppConfig) -> None:
        super().__init__()
        self.service = service
        self.config = config
        self._rendered: dict[str, int] = {}
        self._rendered_date = today_str()
        self._geometry_clamped = False
        self.setWindowTitle("Key Actions")
        self.setWindowIcon(theme.app_icon())
        self.resize(
            int(config.get("window_width")), int(config.get("window_height"))
        )
        self.setMinimumSize(1024, 700)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.pageSelected.connect(self.set_page)
        self.sidebar.sloganEditRequested.connect(self._edit_slogan)
        self.sidebar.themeRequested.connect(self.switch_theme)
        # 切换主题时置为 True，main.py 的循环据此重建整个窗口
        self.restart_requested = False
        root.addWidget(self.sidebar)
        self.reload_slogan()

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self._build_top_bar())

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {
            "overview": OverviewPage(service, config=config),
            "ai": AIPage(service),
            "mindmap": MindMapPage(service),
            "processes": ProcessesPage(service),
            "heatmaps": HeatmapsPage(service),
            "activity": ActivityPage(service),
            "diary": DiaryPage(),
            "data": DataPage(service, config=config),
            "settings": SettingsPage(config),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        right_layout.addWidget(self.stack, 1)
        root.addWidget(right, 1)

        self._wire_pages()
        self.statusBar().showMessage(
            f"数据文件：{paths.relative_display(paths.DATABASE_PATH)} · 关闭窗口即可退出"
        )
        self._install_shortcuts()

        self._sidebar_collapsed = bool(config.get("sidebar_collapsed"))
        self._set_sidebar_collapsed(self._sidebar_collapsed)
        last_page = str(config.get("last_page") or "overview")
        self.set_page(last_page if last_page in self.pages else "overview")
        self._restore_window_state()
        # 兜底：万一某处改动忘了通知，最多 2 秒后当前页面会自己检查并刷新
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(2000)
        self._sync_timer.timeout.connect(self._sync_revision)
        self._sync_timer.start()

    def _sync_revision(self) -> None:
        """每分钟检查两件事：日期变了（跨过午夜）或数据版本变了，就刷新页面。"""
        if not self.service.db.connected:
            return
        key = self.current_page_key()
        if today_str() != self._rendered_date:
            # 跨过午夜：热力图窗口要整体跟上新的一天，
            # 把"已渲染版本"全部清掉，各页面切回时也会重新画。
            self._rendered_date = today_str()
            self._rendered.clear()
            if key:
                self._refresh_page(key)
                self.statusBar().showMessage("新的一天，热力图窗口已更新到今天", 4000)
            return
        if key and self._rendered.get(key) != self.service.revision:
            self._refresh_page(key)
            self.statusBar().showMessage("数据有变化，已自动刷新当前页面", 3000)

    # ------------------------------------------------------------ 窗口状态
    def _restore_window_state(self) -> None:
        """恢复上次的窗口大小与位置；没有记录时用默认尺寸。"""
        saved = str(self.config.get("window_geometry") or "")
        if saved:
            try:
                self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii")))
            except (ValueError, TypeError):
                saved = ""
        if not saved:
            self.resize(
                int(self.config.get("window_width")), int(self.config.get("window_height"))
            )
        if bool(self.config.get("start_maximized")):
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def _clamp_to_screen(self) -> None:
        """确保窗口完整落在屏幕可用区域内，不会再被屏幕边缘挡掉一部分。"""
        if self.isMaximized() or self.isFullScreen():
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        frame = self.frameGeometry()
        target = clamp_to_available(frame, screen.availableGeometry())
        if target.size() != frame.size():
            self.resize(
                max(self.minimumWidth(), self.width() - (frame.width() - target.width())),
                max(self.minimumHeight(), self.height() - (frame.height() - target.height())),
            )
        if target.topLeft() != frame.topLeft():
            self.move(
                self.x() + (target.x() - frame.x()),
                self.y() + (target.y() - frame.y()),
            )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._geometry_clamped:
            self._geometry_clamped = True
            # 等窗口边框真正生成后再校正，结果才准确。
            QTimer.singleShot(0, self._clamp_to_screen)

    # ---------------------------------------------------------------- 顶栏
    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(52)
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 0, 16, 0)
        row.setSpacing(10)

        self.sidebar_toggle = QPushButton("☰")
        self.sidebar_toggle.setObjectName("Collapse")
        self.sidebar_toggle.setToolTip("收起 / 展开左侧导航")
        self.sidebar_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sidebar_toggle.clicked.connect(self.toggle_sidebar)
        row.addWidget(self.sidebar_toggle)

        self.title_label = QLabel("总览")
        self.title_label.setObjectName("PageTitle")
        row.addWidget(self.title_label)
        row.addStretch(1)

        quick = QPushButton("记录活动")
        quick.setObjectName("Primary")
        quick.setToolTip("打开完整记录窗口（可以改日期）· Ctrl+N 是极简快速记录")
        quick.clicked.connect(self._quick_record)
        row.addWidget(quick)
        return bar

    def _install_shortcuts(self) -> None:
        for key, _label, shortcut_text in ALL_NAV_ITEMS:
            shortcut = QShortcut(QKeySequence(shortcut_text), self)
            shortcut.activated.connect(lambda page_key=key: self.set_page(page_key))
        # Ctrl+N：极简快速记录（任意页面都能用），只做这一件事
        self.quick_capture_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self.quick_capture_shortcut.activated.connect(self.quick_capture)
        # Ctrl+M：极简日记（一句话追加到今天）
        self.diary_capture_shortcut = QShortcut(QKeySequence("Ctrl+M"), self)
        self.diary_capture_shortcut.activated.connect(self.diary_capture)

    # ------------------------------------------------------------ 页面连接
    def _wire_pages(self) -> None:
        overview: OverviewPage = self.pages["overview"]
        processes: ProcessesPage = self.pages["processes"]
        activity: ActivityPage = self.pages["activity"]
        ai_page: AIPage = self.pages["ai"]
        settings: SettingsPage = self.pages["settings"]

        for page in self.pages.values():
            if hasattr(page, "changed"):
                page.changed.connect(self.on_data_changed)
            if hasattr(page, "openNodeRequested"):
                page.openNodeRequested.connect(self.open_node)
            if hasattr(page, "openPageRequested"):
                page.openPageRequested.connect(self.set_page)
            if hasattr(page, "sloganEditRequested"):
                page.sloganEditRequested.connect(self._edit_slogan)
        activity.openNodeRequested.connect(self.open_node)
        settings.saved.connect(ai_page.reload_config)
        ai_page.openSettingsRequested.connect(lambda: self.set_page("settings"))

    def on_data_changed(self) -> None:
        """数据变化后只刷新当前页面；其余页面在切回时按数据版本自动刷新。

        这样每次操作只重绘眼前需要更新的内容，避免整窗闪烁与无谓开销。
        """
        key = self.current_page_key()
        if key:
            self._refresh_page(key)
        self.statusBar().showMessage(
            f"数据已更新 · {clock.now().strftime('%H:%M:%S')}（北京时间）· 所有写入均为本地事务",
            5000,
        )

    def current_page_key(self) -> str | None:
        current = self.stack.currentWidget()
        for key, page in self.pages.items():
            if page is current:
                return key
        return None

    def _refresh_page(self, key: str) -> None:
        page = self.pages[key]
        if hasattr(page, "refresh"):
            page.refresh()
        self._rendered[key] = self.service.revision

    def set_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self.title_label.setText(PAGE_TITLES.get(key, ""))
        self.sidebar.set_current(key)
        self.config.set("last_page", key)
        if self._rendered.get(key) != self.service.revision:
            self._refresh_page(key)

    def open_node(self, node_id: str) -> None:
        if not node_id:
            self.set_page("heatmaps")
            return
        processes: ProcessesPage = self.pages["processes"]
        self.set_page("processes")
        processes.select_node(node_id)

    # -------------------------------------------------------------- 侧边栏
    def toggle_sidebar(self) -> None:
        self._set_sidebar_collapsed(not self._sidebar_collapsed)

    def _set_sidebar_collapsed(self, collapsed: bool) -> None:
        self._sidebar_collapsed = collapsed
        self.sidebar.setVisible(not collapsed)
        self.sidebar_toggle.setText("☰" if collapsed else "‹")
        self.config.set("sidebar_collapsed", collapsed)

    # ------------------------------------------------------------ 快速记录
    def _quick_record(self) -> None:
        dialog = QuickRecordDialog(self.service, self)
        if dialog.exec() and dialog.recorded is not None:
            self.on_data_changed()
            self.statusBar().showMessage(
                f"已记录一次活动：{self.service.path_text(dialog.recorded.node_id)}", 5000
            )

    def diary_capture(self) -> None:
        """Ctrl+M：一个输入框，回车就把这句话追加到今天的日记里。"""
        dialog = DiaryCaptureDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved_text:
            diary_page = self.pages.get("diary")
            refresh = getattr(diary_page, "refresh", None)
            if callable(refresh):
                refresh()
            self.statusBar().showMessage("已记到今天的日记里", 4000)

    # ---------------------------------------------------------------- 标语
    def switch_theme(self, name: str) -> None:
        """切换配色。

        配色切换靠"重建整个窗口"完成：所有控件都会用新配色重新创建一次，
        那些在 Python 里拼字符串设样式的地方也就不需要单独处理。
        重建前先把日记草稿落盘，避免丢掉没点保存的内容。
        """
        name = theme.normalize(name)
        if name == theme.current_theme():
            return
        diary_page = self.pages.get("diary")
        if diary_page is not None and hasattr(diary_page, "flush"):
            diary_page.flush()
        self.config.set("theme", name)
        self.config.save()
        self.restart_requested = True
        # 关掉窗口：closeEvent 会把窗口位置与大小存进配置，新窗口启动时原样恢复
        self.close()

    def reload_slogan(self) -> None:
        """把数据库里最新的标语显示到左栏（没设置过就显示灰字提示）。"""
        slogan = self.service.current_slogan()
        self.sidebar.set_slogan(slogan.text if slogan else "")

    def _edit_slogan(self) -> None:
        """改标语：留空表示清空；每次修改都会留下一条历史（在「数据」页面）。"""
        current = self.service.current_slogan()
        text, ok = QInputDialog.getMultiLineText(
            self,
            "修改标语",
            f"左栏顶部显示的一句话（最多 {SLOGAN_MAX_LENGTH} 字；留空则不显示）：",
            current.text if current else "",
        )
        if not ok:
            return
        try:
            self.service.set_slogan(text)
        except ValidationError as exc:
            warn(self, "无法保存标语", str(exc))
            return
        self.reload_slogan()
        self.on_data_changed()
        self.statusBar().showMessage(
            "标语已更新；以前的版本在「数据」页面能看到", 5000
        )

    def quick_capture(self) -> None:
        """Ctrl+N：一个输入框，先匹配节点，再写备注，两次回车完成。"""
        dialog = QuickCaptureDialog(self.service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.recorded is not None:
            self.on_data_changed()
            self.statusBar().showMessage(
                f"已记录一次活动：{self.service.path_text(dialog.recorded.node_id)}", 5000
            )

    # ---------------------------------------------------------------- 关闭
    def closeEvent(self, event) -> None:
        self._sync_timer.stop()
        # 位置、大小与最大化状态一起记住，下次启动原样恢复
        self.config.set("window_geometry", bytes(self.saveGeometry().toBase64()).decode("ascii"))
        if not self.isMaximized() and not self.isFullScreen():
            self.config.set("window_width", self.width())
            self.config.set("window_height", self.height())
        self.config.save()
        super().closeEvent(event)
