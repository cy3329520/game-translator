#!/usr/bin/env python3
"""游戏实时翻译器 —— 程序入口。

用法：
    ./run.sh            # 一键启动（首次会自动装依赖）
    python3 app.py      # 已经装好依赖时直接跑
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ui import App  # noqa: E402


def _locate_tcl_libraries() -> None:
    """兜底：部分独立构建的 Python（如 python-build-standalone）在某些启动环境下
    找不到自带的 Tcl/Tk 库，此时显式指给它。"""
    lib = os.path.join(sys.base_prefix, "lib")
    if not os.path.isdir(lib):
        return
    try:
        names = sorted(os.listdir(lib))
    except OSError:
        return
    for name in names:
        if name.startswith("tcl"):
            os.environ.setdefault("TCL_LIBRARY", os.path.join(lib, name))
            tk_name = "tk" + name[len("tcl"):]
            if os.path.isdir(os.path.join(lib, tk_name)):
                os.environ.setdefault("TK_LIBRARY", os.path.join(lib, tk_name))
            return


def create_root() -> tk.Tk:
    try:
        return tk.Tk()
    except tk.TclError:
        _locate_tcl_libraries()
        return tk.Tk()


def main() -> int:
    try:
        root = create_root()
    except tk.TclError as exc:
        print(f"无法创建窗口：{exc}", file=sys.stderr)
        print("请确认已安装 python-tk（Homebrew 的 Python 执行：brew install python-tk）",
              file=sys.stderr)
        return 1

    # 不要手动设置 tk scaling：Retina 屏上强行设成 1.0 会让整个界面缩得极小，
    # 交给 Tk 自己按屏幕 DPI 决定即可。
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
