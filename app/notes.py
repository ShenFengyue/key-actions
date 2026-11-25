"""笔记存储：真实文件夹 + Markdown 文件，全部落在 data/notes/ 里。

文件夹就是目录，笔记就是 .md 文件——可以直接用资源管理器或任何编辑器打开，
和日记、能量池档案同一个思路：数据是人能读懂的文件，不锁在数据库里。

相对路径一律用 "/" 分隔（如 "工作/周报.md"），"" 表示根目录。
所有操作都校验名字合法、路径不越出 notes 目录。
"""

from __future__ import annotations

import re
from pathlib import Path

from . import paths

MAX_NAME_LENGTH = 60
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def ensure_root() -> Path:
    paths.NOTES_DIR.mkdir(parents=True, exist_ok=True)
    return paths.NOTES_DIR


def check_name(name: str) -> str:
    """校验文件夹 / 笔记名，返回去掉首尾空白的名字；不合法抛 ValueError。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("名字不能为空")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"名字太长（最多 {MAX_NAME_LENGTH} 字）")
    if _ILLEGAL.search(name):
        raise ValueError('名字里不能带 \\ / : * ? " < > | 这些字符')
    if name in (".", ".."):
        raise ValueError("这个名字不行")
    if name.endswith("."):
        raise ValueError("名字不能以点结尾")
    return name


def _resolve(rel: str) -> Path:
    """相对路径 → 绝对路径；越出 notes 目录一律拒绝。"""
    parts = [p for p in (rel or "").split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise ValueError("路径不合法")
    target = ensure_root().joinpath(*parts).resolve()
    root = paths.NOTES_DIR.resolve()
    if target != root and not target.is_relative_to(root):
        raise ValueError("路径不合法")
    return target


def note_name(name: str) -> str:
    """笔记文件名：没写 .md 后缀就补上。"""
    name = check_name(name)
    return name if name.lower().endswith(".md") else f"{name}.md"


def exists(rel: str) -> bool:
    return _resolve(rel).exists()


def tree() -> list[dict]:
    """目录树：文件夹在前、笔记在后，各自按名字排序。

    每项 {"rel", "name", "is_folder", "children"}；笔记的 children 恒为空。
    """
    def scan(folder: Path, prefix: str) -> list[dict]:
        folders: list[dict] = []
        notes: list[dict] = []
        for child in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if child.name.startswith("."):
                continue
            rel = f"{prefix}/{child.name}" if prefix else child.name
            if child.is_dir():
                folders.append({"rel": rel, "name": child.name, "is_folder": True,
                                "children": scan(child, rel)})
            elif child.suffix.lower() == ".md":
                notes.append({"rel": rel, "name": child.name, "is_folder": False,
                              "children": []})
        return folders + notes

    return scan(ensure_root(), "")


def folders() -> list[str]:
    """全部文件夹的相对路径（供"移动到"选择），不含根目录 ""。"""
    result: list[str] = []

    def walk(entries: list[dict]) -> None:
        for entry in entries:
            if entry["is_folder"]:
                result.append(entry["rel"])
                walk(entry["children"])

    walk(tree())
    return result


def create_folder(parent_rel: str, name: str) -> str:
    name = check_name(name)
    parent = _resolve(parent_rel)
    if not parent.is_dir():
        raise ValueError("上级文件夹不存在")
    target = parent / name
    if target.exists():
        raise ValueError(f"「{name}」已经存在")
    target.mkdir()
    return f"{parent_rel}/{name}" if parent_rel else name


def create_note(parent_rel: str, name: str) -> str:
    filename = note_name(name)
    parent = _resolve(parent_rel)
    if not parent.is_dir():
        raise ValueError("上级文件夹不存在")
    target = parent / filename
    if target.exists():
        raise ValueError(f"「{filename}」已经存在")
    target.write_text("", encoding="utf-8")
    return f"{parent_rel}/{filename}" if parent_rel else filename


def read_note(rel: str) -> str:
    target = _resolve(rel)
    if not target.is_file():
        return ""
    return target.read_text(encoding="utf-8")


def write_note(rel: str, text: str) -> None:
    target = _resolve(rel)
    if not target.is_file():
        raise ValueError("笔记不存在")
    target.write_text(text, encoding="utf-8")


def rename(rel: str, new_name: str) -> str:
    target = _resolve(rel)
    if not target.exists():
        raise ValueError("要改名的对象不存在")
    if target.is_dir():
        filename = check_name(new_name)
    else:
        filename = note_name(new_name)
    dest = target.parent / filename
    if dest.exists():
        raise ValueError(f"「{filename}」已经存在")
    target.rename(dest)
    parent_rel = "/".join(rel.split("/")[:-1])
    return f"{parent_rel}/{filename}" if parent_rel else filename


def move_note(rel: str, folder_rel: str) -> str:
    target = _resolve(rel)
    if not target.is_file():
        raise ValueError("只能移动笔记")
    folder = _resolve(folder_rel)
    if not folder.is_dir():
        raise ValueError("目标文件夹不存在")
    dest = folder / target.name
    if dest.exists():
        raise ValueError(f"目标文件夹里已经有「{target.name}」")
    target.rename(dest)
    return f"{folder_rel}/{target.name}" if folder_rel else target.name


def delete_note(rel: str) -> None:
    target = _resolve(rel)
    if not target.is_file():
        raise ValueError("笔记不存在")
    target.unlink()


def delete_folder(rel: str) -> None:
    target = _resolve(rel)
    if not target.is_dir():
        raise ValueError("文件夹不存在")
    if any(target.iterdir()):
        raise ValueError("文件夹里还有内容，先移走或删掉里面的笔记和子文件夹")
    target.rmdir()