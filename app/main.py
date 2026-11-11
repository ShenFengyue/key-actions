"""应用入口：初始化日志、数据库与主窗口。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

if __package__ in (None, ""):  # 允许直接以脚本方式运行 app/main.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import diary, paths, theme
from app.appconfig import AppConfig
from app.logging_setup import setup_logging
from app.main_window import MainWindow
from app.services import LifeManagerService
from app.storage import DataCorruptionError, Database

logger = logging.getLogger(__name__)


def _set_windows_app_id() -> None:
    """让 Windows 把这个进程当成一个独立应用。

    否则任务栏会把窗口归到 pythonw.exe 名下，图标可能显示成空白或 Python 的标志。
    这个调用只是给进程起一个"身份名"，不写注册表、不写 AppData。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "LifeManager.Desktop.v1"
        )
    except Exception:  # noqa: BLE001 - 设置失败不影响使用
        logger.warning("设置 Windows 应用标识失败（图标可能回退成默认样式）", exc_info=True)


def _install_exception_hook() -> None:
    """未捕获异常写入日志并弹窗提示，避免 .pyw 方式启动时静默失败。"""

    def hook(exc_type, exc_value, traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, traceback)
            return
        logger.critical("未捕获的异常", exc_info=(exc_type, exc_value, traceback))
        try:
            QMessageBox.critical(
                None,
                "程序出现错误",
                f"{exc_type.__name__}: {exc_value}\n\n"
                f"详细信息已写入日志：\n{paths.relative_display(paths.LOG_PATH)}",
            )
        except Exception:  # pragma: no cover - 弹窗失败时至少已经写日志
            pass

    sys.excepthook = hook


def _corruption_dialog(db: Database, message: str) -> LifeManagerService | None:
    """数据库损坏时不静默清空数据，只提供查看目录与从备份恢复两条路径。"""
    box = QMessageBox()
    box.setWindowTitle("数据库无法读取")
    box.setIcon(QMessageBox.Icon.Critical)
    box.setText("本地数据库文件无法正常打开或已经损坏。")
    box.setInformativeText(
        f"{message}\n\n应用不会自动清空或覆盖数据。你可以从 data/backups 里的备份恢复，"
        "或先关闭应用手动检查该文件。"
    )
    restore_button = box.addButton("从备份恢复…", QMessageBox.ButtonRole.AcceptRole)
    open_button = box.addButton("打开数据文件夹", QMessageBox.ButtonRole.ActionRole)
    box.addButton("退出", QMessageBox.ButtonRole.RejectRole)
    box.exec()

    if box.clickedButton() is open_button:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.DATA_DIR)))
        return None
    if box.clickedButton() is not restore_button:
        return None

    backup, _ = QFileDialog.getOpenFileName(
        None, "选择备份文件", str(paths.BACKUP_DIR), "SQLite 备份 (*.sqlite3)"
    )
    if not backup:
        return None
    service = LifeManagerService(db)
    try:
        service.restore_backup(backup)
    except Exception as exc:  # noqa: BLE001 - 需要把失败原因展示给用户
        QMessageBox.critical(None, "恢复失败", str(exc))
        return None
    return service


def main() -> int:
    setup_logging()
    _install_exception_hook()
    paths.ensure_directories()
    _set_windows_app_id()
    diary_file = diary.ensure_file()
    logger.info("日记文件：%s", diary_file)
    logger.info("Key Actions 启动，项目根目录：%s", paths.ROOT)

    app = QApplication(sys.argv)
    app.setApplicationName("Key Actions")
    app.setApplicationDisplayName("Key Actions")
    app.setWindowIcon(theme.app_icon())
    app.setStyle("Fusion")
    app.setFont(theme.app_font())

    config = AppConfig()
    theme.apply_theme(str(config.get("theme")))
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.stylesheet())
    db = Database()
    service: LifeManagerService | None = None
    try:
        db.initialize()
        service = LifeManagerService(db)
        # 每天第一次启动自动备份一份（含日记）；失败不影响启动
        try:
            created = service.auto_backup_if_needed()
            if created is not None:
                logger.info("今日自动备份：%s", created)
        except Exception:  # noqa: BLE001 - 备份失败不能挡住启动
            logger.exception("自动备份失败（不影响使用）")
    except DataCorruptionError as exc:
        logger.error("数据库损坏：%s", exc)
        service = _corruption_dialog(db, str(exc))
        if service is None:
            logger.warning("用户放弃恢复，正常退出，未修改任何数据")
            return 1
    except Exception as exc:  # noqa: BLE001 - 启动阶段的任何异常都要可见
        logger.exception("启动失败")
        QMessageBox.critical(None, "启动失败", f"应用无法启动：\n{exc}")
        return 2

    # 主题切换后主窗口会请求重建：这里循环一次，用新配色把所有控件重新创建一遍。
    # 这样"在代码里拼字符串设样式"的地方也会拿到新颜色，不会留下旧配色的角落。
    code = 0
    while True:
        theme.apply_theme(str(config.get("theme")))
        app.setPalette(theme.palette())
        app.setStyleSheet(theme.stylesheet())
        window = MainWindow(service, config)
        window.show()
        code = app.exec()
        if not window.restart_requested:
            break
        logger.info("按新主题重建界面")
    db.close()
    logger.info("Key Actions 退出")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
