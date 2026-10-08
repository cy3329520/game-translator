#!/usr/bin/env bash
# 游戏实时翻译器 —— 一键启动脚本
# 首次运行会自动创建虚拟环境并安装依赖，之后直接启动。

set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
PY="$VENV/bin/python"

# venv 损坏或不存在（比如把项目目录整体移动过）就重建
if [ -x "$PY" ] && ! "$PY" -c "import sys" >/dev/null 2>&1; then
  echo "==> 检测到虚拟环境已失效，正在重建..."
  rm -rf "$VENV"
fi

if [ ! -x "$PY" ]; then
  echo "==> 首次运行，正在创建虚拟环境..."
  if command -v python3 >/dev/null 2>&1; then
    python3 -m venv "$VENV"
  else
    echo "找不到 python3，请先安装 Python 3.10+（https://www.python.org/downloads/）" >&2
    exit 1
  fi
fi

# 依赖是否齐全？缺了就装
if ! "$PY" -c "import mss, PIL, requests, pynput" >/dev/null 2>&1; then
  echo "==> 正在安装依赖（mss / Pillow / requests / pynput）..."
  "$PY" -m pip install --upgrade pip >/dev/null
  "$PY" -m pip install -r requirements.txt
fi

if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
  echo "缺少 tkinter。" >&2
  echo "  macOS（Homebrew 的 Python）：brew install python-tk" >&2
  echo "  Linux（Debian/Ubuntu）：     sudo apt install python3-tk" >&2
  exit 1
fi

# 兜底：部分独立构建的 Python 在某些启动方式下找不到自带的 Tcl/Tk 库
if ! "$PY" -c "import tkinter; tkinter.Tk().destroy()" >/dev/null 2>&1; then
  PY_BASE=$("$PY" -c "import sys; print(sys.base_prefix)" 2>/dev/null || true)
  if [ -n "$PY_BASE" ] && [ -d "$PY_BASE/lib" ]; then
    for d in "$PY_BASE"/lib/tcl9.* "$PY_BASE"/lib/tcl8.* "$PY_BASE"/lib/tcl9 "$PY_BASE"/lib/tcl8; do
      if [ -d "$d" ]; then
        export TCL_LIBRARY="$d"
        TK_DIR="${d%/tcl*}/tk${d##*tcl}"
        [ -d "$TK_DIR" ] && export TK_LIBRARY="$TK_DIR"
        break
      fi
    done
  fi
fi

echo "==> 启动游戏实时翻译器..."
exec "$PY" app.py
