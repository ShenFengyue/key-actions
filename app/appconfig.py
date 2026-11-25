"""应用配置存储。

规则三要求不得使用会把设置写入注册表的配置方式（因此不使用 QSettings 的默认后端），
配置文件固定位于项目内的 data/config.json。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import paths

logger = logging.getLogger(__name__)

CONFIG_PATH = paths.DATA_DIR / "config.json"

DEFAULTS: dict[str, Any] = {
    "window_width": 1360,
    "window_height": 880,
    "window_geometry": "",  # 上次关闭时的窗口位置与大小（base64，含最大化状态）
    "start_maximized": False,
    "sidebar_collapsed": False,
    "last_page": "overview",
    # 配色：light / dark。切换后主窗口会整体重建一次（见 main.py）。
    "theme": "light",
    # 字体大小（px）：全局影响整个界面，笔记只影响笔记编辑与预览。
    # 改动保存后主窗口会按新字号整体重建一次（与配色切换同一机制）。
    "font_size": 14,
    "notes_font_size": 15,
    # 总览「当前重点」卡片的显示顺序（节点 id 列表）。
    # 这是纯界面偏好：只影响总览页里的排列，不写数据库、不改动置顶状态本身。
    "focus_order": [],
}


class AppConfig:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else CONFIG_PATH
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("配置文件读取失败，使用默认值：%s", exc)
            return
        if isinstance(raw, dict):
            self._data.update({k: v for k, v in raw.items() if k in DEFAULTS})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:  # pragma: no cover - 磁盘异常
            logger.warning("配置文件写入失败：%s", exc)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
