@echo off
chcp 65001 >nul
rem 游戏实时翻译器 —— 一键启动脚本（Windows）
rem 首次运行会自动创建虚拟环境并安装依赖，之后直接启动。
setlocal
cd /d "%~dp0"

set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"

rem venv 损坏或不存在（比如把项目目录整体移动过）就重建
if exist "%PY%" "%PY%" -c "import sys" >nul 2>&1
if exist "%PY%" if errorlevel 1 (
  echo ==^> 检测到虚拟环境已失效，正在重建...
  rmdir /s /q "%VENV%"
)

if not exist "%PY%" (
  echo ==^> 首次运行，正在创建虚拟环境...
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -m venv "%VENV%"
  ) else (
    python -m venv "%VENV%"
  )
  if not exist "%PY%" (
    echo 找不到 Python，请先安装 Python 3.10+ 并勾选 "Add python.exe to PATH"
    echo 下载地址：https://www.python.org/downloads/
    pause
    exit /b 1
  )
)

rem 依赖是否齐全？缺了就装
"%PY%" -c "import mss, PIL, requests, pynput" >nul 2>&1
if errorlevel 1 (
  echo ==^> 正在安装依赖（mss / Pillow / requests / pynput）...
  "%PY%" -m pip install --upgrade pip
  "%PY%" -m pip install -r requirements.txt
)

echo ==^> 启动游戏实时翻译器...
"%PY%" app.py

if errorlevel 1 (
  echo.
  echo 程序异常退出。若提示缺少 tkinter，请重新安装官方 Python（python.org 版自带 tkinter）。
  pause
)
endlocal
