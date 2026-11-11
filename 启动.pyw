# -*- coding: utf-8 -*-
"""Key Actions 启动器（日常双击这个文件）。

独立桌面窗口，不弹出 CMD / PowerShell 窗口，不打开浏览器，不启动本地 Web 服务器。
首次运行时会用项目内的 .venv 准备运行环境（一次性），之后每次启动都直接进入应用。
不修改系统 Python，也不写入项目文件夹以外的任何位置。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
SCRIPTS_DIR = VENV_DIR / "Scripts"
VENV_PYTHON = SCRIPTS_DIR / "python.exe"
VENV_PYTHONW = SCRIPTS_DIR / "pythonw.exe"
ENTRY = ROOT / "app" / "main.py"
REQUIREMENTS = ROOT / "requirements.txt"

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


# --------------------------------------------------------------- 基础检查
def running_inside_project_venv() -> bool:
    try:
        return Path(sys.executable).resolve().parent == SCRIPTS_DIR.resolve()
    except OSError:
        return False


def python_has_pyside(interpreter: Path) -> bool:
    if not interpreter.exists():
        return False
    try:
        result = subprocess.run(
            [str(interpreter), "-c", "import PySide6"],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError:
        return False
    return result.returncode == 0


def venv_is_ready() -> bool:
    return (
        VENV_PYTHONW.exists()
        and VENV_PYTHON.exists()
        and ENTRY.exists()
        and python_has_pyside(VENV_PYTHON)
    )


def launch_application() -> int:
    """在独立、无控制台的进程中启动应用。"""
    os.chdir(ROOT)
    if running_inside_project_venv():
        sys.path.insert(0, str(ROOT))
        from app.main import main  # 延迟导入：环境未准备好时不会提前失败

        return main()
    if not VENV_PYTHONW.exists():
        return 3
    subprocess.Popen(
        [str(VENV_PYTHONW), str(ENTRY)],
        cwd=str(ROOT),
        close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
    )
    return 0


# ------------------------------------------------------------- 环境准备向导
def find_base_python() -> list[str] | None:
    """找一个可用的 64 位 Python 3.9+ 解释器来创建 .venv。"""
    probe = "import struct,sys;print('%d.%d' % sys.version_info[:2], struct.calcsize('P')*8)"
    candidates: list[list[str]] = []
    py_launcher = shutil.which("py")
    if py_launcher:
        for version in ("3.13", "3.12", "3.11", "3.10", "3.9"):
            candidates.append([py_launcher, f"-{version}"])
    candidates.append([sys.executable])
    for candidate in candidates:
        try:
            result = subprocess.run(
                candidate + ["-c", probe],
                capture_output=True,
                text=True,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError:
            continue
        if result.returncode != 0:
            continue
        output = (result.stdout or "").strip().split()
        if len(output) != 2 or output[1] != "64":
            continue
        major_minor = tuple(int(part) for part in output[0].split("."))
        if major_minor < (3, 9):
            continue
        return candidate
    return None


def manual_instructions() -> str:
    return (
        "手动准备方式（在项目文件夹里打开 PowerShell 执行一次）：\n"
        f'  py -3.13 -m venv "{VENV_DIR}"\n'
        f'  "{VENV_PYTHON}" -m pip install -r "{REQUIREMENTS}"\n'
        "之后每天双击 启动.pyw 即可。"
    )


def show_message_box(title: str, text: str, error: bool = False) -> None:
    """没有 tkinter 时的兜底提示。"""
    try:
        import ctypes

        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except Exception:  # pragma: no cover - 极端兜底
        print(f"{title}\n{text}")


def run_wizard() -> int:
    try:
        import tkinter as tk
        from tkinter import scrolledtext, ttk
    except ImportError:
        show_message_box(
            "Key Actions",
            "首次运行需要准备环境，但当前 Python 没有 tkinter。\n\n" + manual_instructions(),
            error=True,
        )
        return 4

    base_python = find_base_python()
    root = tk.Tk()
    root.title("Key Actions · 首次环境准备")
    root.geometry("720x520")
    root.minsize(640, 460)

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)

    ttk.Label(
        frame,
        text="首次运行：准备本地运行环境",
        font=("Microsoft YaHei UI", 13, "bold"),
    ).pack(anchor="w")
    ttk.Label(
        frame,
        text=(
            "应用会在项目文件夹内创建 .venv 并安装 PySide6（约 100MB，需要联网一次）。\n"
            "不会修改系统 Python，也不会把任何数据写到项目文件夹以外。"
        ),
        justify="left",
        wraplength=660,
    ).pack(anchor="w", pady=(6, 12))

    status = ttk.Label(frame, text="", foreground="#0969da")
    status.pack(anchor="w")

    log = scrolledtext.ScrolledText(frame, height=18, wrap="word")
    log.pack(fill="both", expand=True, pady=(8, 12))

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x")
    prepare_button = ttk.Button(buttons, text="开始准备环境")
    prepare_button.pack(side="left")
    launch_button = ttk.Button(buttons, text="启动应用", state="disabled")
    launch_button.pack(side="left", padx=8)
    close_button = ttk.Button(buttons, text="退出", command=root.destroy)
    close_button.pack(side="right")

    def write(line: str) -> None:
        log.insert("end", line + "\n")
        log.see("end")

    if base_python is None:
        status.configure(text="未找到可用的 64 位 Python（需要 3.9 及以上）。", foreground="#cf222e")
        write("没有检测到合适的 Python 解释器。")
        write(manual_instructions())
        prepare_button.configure(state="disabled")
    else:
        version = subprocess.run(
            base_python + ["-c", "import sys;print(sys.version.split()[0])"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        ).stdout.strip()
        status.configure(text=f"将使用 Python {version}（{' '.join(base_python)}）创建项目内环境。")
        write(f"检测到的 Python：{' '.join(base_python)} → {version}")
        write(manual_instructions())

    def worker() -> None:
        try:
            if base_python is None:
                return
            commands: list[list[str]] = []
            if VENV_DIR.exists():
                commands.append(base_python + ["-m", "venv", "--clear", str(VENV_DIR)])
            else:
                commands.append(base_python + ["-m", "venv", str(VENV_DIR)])
            commands.append([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"])
            commands.append([str(VENV_PYTHON), "-m", "pip", "install", "-r", str(REQUIREMENTS)])
            for command in commands:
                root.after(0, write, f"\n$ {' '.join(command)}")
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=CREATE_NO_WINDOW,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    root.after(0, write, line.rstrip())
                process.wait()
                if process.returncode != 0:
                    root.after(
                        0,
                        lambda: status.configure(
                            text="环境准备失败，请按上面的说明手动执行。", foreground="#cf222e"
                        ),
                    )
                    return
            if venv_is_ready():
                root.after(
                    0,
                    lambda: status.configure(
                        text="环境准备完成，正在启动应用…", foreground="#1a7f37"
                    ),
                )
                root.after(0, write, "\n环境准备完成。")
                root.after(200, start_now)
            else:
                root.after(
                    0,
                    lambda: status.configure(
                        text="依赖安装完成但校验未通过，请查看日志。", foreground="#cf222e"
                    ),
                )
        except Exception as exc:  # 向导需要展示任何失败
            root.after(0, write, f"\n出现错误：{exc}")
            root.after(
                0, lambda: status.configure(text="环境准备失败。", foreground="#cf222e")
            )

    def prepare() -> None:
        prepare_button.configure(state="disabled")
        status.configure(text="正在准备环境，请稍候（首次约 1—3 分钟）…", foreground="#0969da")
        threading.Thread(target=worker, daemon=True).start()

    def start_now() -> None:
        launch_button.configure(state="normal")
        try:
            launch_application()
        finally:
            root.destroy()

    prepare_button.configure(command=prepare)
    launch_button.configure(command=start_now)

    if venv_is_ready():
        status.configure(text="环境已就绪，正在启动应用…", foreground="#1a7f37")
        root.after(150, start_now)

    root.mainloop()
    return 0


def main() -> int:
    if venv_is_ready():
        return launch_application()
    return run_wizard()


if __name__ == "__main__":
    raise SystemExit(main())
