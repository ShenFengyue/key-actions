"""测试辅助：在项目内创建隔离的临时目录。

不使用 tempfile.mkdtemp()，因为部分受限环境下新建的临时目录不可写；
统一改为在项目文件夹内的 .test_tmp 下自建目录，测试结束后清理。
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from app import paths


def make_temp_dir() -> Path:
    base = Path(os.environ.get("KEY_ACTIONS_TEST_TMP") or (paths.ROOT / ".test_tmp"))
    base.mkdir(parents=True, exist_ok=True)
    target = base / f"case-{uuid.uuid4().hex[:10]}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def remove_temp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
