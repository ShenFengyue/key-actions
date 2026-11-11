"""项目内路径解析。

规则三要求：所有用户数据、配置、操作日志、历史记录和备份都存储在项目文件夹内，
不写入 Windows AppData、Documents、注册表或其他项目以外的位置。
因此所有路径都从项目根目录动态计算，不依赖当前工作目录，也不硬编码绝对路径。
"""

from __future__ import annotations

from pathlib import Path

APP_DIR_NAME = "app"
DATA_DIR_NAME = "data"


def project_root() -> Path:
    """返回项目根目录（本文件位于 <root>/app/paths.py）。"""
    return Path(__file__).resolve().parent.parent


ROOT = project_root()
DATA_DIR = ROOT / DATA_DIR_NAME
ASSETS_DIR = ROOT / "assets"
LOGO_PNG = ASSETS_DIR / "logo.png"
LOGO_ICO = ASSETS_DIR / "logo.ico"
DATABASE_PATH = DATA_DIR / "key_actions.sqlite3"
DIARY_PATH = DATA_DIR / "diary.md"
BACKUP_DIR = DATA_DIR / "backups"
EXPORT_DIR = DATA_DIR / "exports"
LOG_DIR = DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "key_actions.log"

REQUIREMENTS_PATH = ROOT / "requirements.txt"
LAUNCHER_PATH = ROOT / "启动.pyw"
ENTRY_PATH = ROOT / APP_DIR_NAME / "main.py"


def ensure_directories() -> None:
    """确保项目内的数据目录存在。"""
    for directory in (DATA_DIR, BACKUP_DIR, EXPORT_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def is_inside_project(path: str | Path) -> bool:
    """判断给定路径是否位于项目文件夹内。"""
    try:
        candidate = Path(path).resolve()
    except OSError:  # pragma: no cover - 非法路径
        return False
    try:
        candidate.relative_to(ROOT)
    except ValueError:
        return False
    return True


def relative_display(path: str | Path) -> str:
    """尽可能以相对于项目根目录的形式展示路径。"""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except (OSError, ValueError):
        return str(path)
