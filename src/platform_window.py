"""平台相关的原生窗口控制与常量。

macOS：Tk 的 `-topmost` 只是把窗口标成"浮动"，原生 `NSWindow.level` 仍然是 0
        （NSNormalWindowLevel），遇到全屏应用或高层级窗口照样会被压住。
        这里用 pyobjc 直接改 NSWindow 的 level 和 collectionBehavior。

Windows：Tk 自带的 `-topmost` 已经能盖住多数窗口和无边框游戏，额外需要两件事：
        点击不激活本应用（游戏不失焦）、字幕条鼠标穿透 —— 都用扩展窗口样式实现。
"""

from __future__ import annotations

import sys

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

# 界面中文字体：两套系统自带的中文字体名不同
UI_FONT = "Microsoft YaHei" if IS_WINDOWS else "PingFang SC"

# 原生窗口层级：数值越大越靠前（macOS）
WINDOW_LEVELS = {
    "floating": 3,          # NSFloatingWindowLevel    —— 浮在普通应用窗口之上
    "status": 25,           # NSStatusWindowLevel      —— 连菜单栏一起盖住
    "screen_saver": 1000,   # NSScreenSaverWindowLevel —— 盖住一切
}

_appkit_cache: dict = {}
_appkit_loaded = False
# native_title -> NSWindow 对象（PyObjC 包装）
_ns_windows: dict = {}


def load_appkit() -> dict | None:
    """惰性导入 AppKit，失败返回 None（非 macOS 或没装 pyobjc 时）。"""
    global _appkit_loaded, _appkit_cache
    if _appkit_loaded:
        return _appkit_cache or None

    _appkit_loaded = True
    try:
        from AppKit import (
            NSApplication,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSWindowCollectionBehaviorStationary,
        )
    except ImportError:
        _appkit_cache = {}
        return None

    _appkit_cache = {
        "NSApplication": NSApplication,
        "CanJoinAllSpaces": NSWindowCollectionBehaviorCanJoinAllSpaces,
        "FullScreenAuxiliary": NSWindowCollectionBehaviorFullScreenAuxiliary,
        "IgnoresCycle": NSWindowCollectionBehaviorIgnoresCycle,
        "Stationary": NSWindowCollectionBehaviorStationary,
    }
    return _appkit_cache


def _set_safely(func, *args) -> None:
    try:
        func(*args)
    except Exception:
        pass


def _find_window(appkit, title: str):
    for candidate in appkit["NSApplication"].sharedApplication().windows():
        try:
            if candidate.title() == title:
                return candidate
        except Exception:
            continue
    return None


# ----------------------------------------------------------------------
#  Windows
# ----------------------------------------------------------------------
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080   # 不出现在任务栏
WS_EX_LAYERED = 0x00080000      # 分层窗口，鼠标穿透的前提
WS_EX_TRANSPARENT = 0x00000020  # 鼠标穿透：点击落到下层窗口
WS_EX_NOACTIVATE = 0x08000000   # 点击不激活本应用（游戏不失焦）


def _apply_windows(tk_window, level_key: str, mouse_through: bool) -> bool:
    """设置扩展窗口样式。返回 True 表示已按需调整。

    ponytail: 独占全屏（exclusive fullscreen）的游戏，任何窗口都无法覆盖，
    只能让游戏改成无边框窗口模式 —— 这点和 macOS 一样，不是这里能修的。
    """
    if level_key == "off":
        return False
    try:
        import ctypes

        hwnd = int(tk_window.winfo_id())
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        if mouse_through:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        if mouse_through:
            # 手动加了 WS_EX_LAYERED 后必须给一次分层属性，否则窗口可能不绘制。
            # LWA_ALPHA + 255 = 完全不透明；代价是 Windows 上忽略「不透明度」设置。
            # ponytail: 若要保留半透明，改为读取 Tk 的 -alpha 值传进来
            user32.SetLayeredWindowAttributes(hwnd, 0, 255, 0x2)
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------
def apply_level(tk_window, native_title: str, level_key: str,
                mouse_through: bool) -> bool:
    """把 tk_window 的原生层级拉到 level_key 指定的高度。

    mouse_through=True 表示窗口鼠标穿透（点不到、不挡下面的操作）。
    返回 True 表示成功应用了原生层级控制。
    """
    if IS_WINDOWS:
        return _apply_windows(tk_window, level_key, mouse_through)
    if not IS_MACOS:
        return False

    appkit = load_appkit()
    if appkit is None:
        return False

    ns_window = _ns_windows.get(native_title)
    if ns_window is None:
        # 窗口必须先被系统真正创建出来，否则查不到，等下一次渲染再试
        ns_window = _find_window(appkit, native_title)
        if ns_window is None:
            return False
        _ns_windows[native_title] = ns_window
        # 这两项与层级无关，配置一次即可
        _set_safely(ns_window.setHidesOnDeactivate_, False)
        _set_safely(
            ns_window.setCollectionBehavior_,
            appkit["CanJoinAllSpaces"]
            | appkit["Stationary"]
            | appkit["IgnoresCycle"]
            | appkit["FullScreenAuxiliary"],
        )

    if level_key == "off":
        # 关掉时把层级降回普通窗口，并恢复鼠标事件
        _set_safely(ns_window.setLevel_, 0)   # NSNormalWindowLevel
        _set_safely(ns_window.setIgnoresMouseEvents_, False)
        return False

    _set_safely(ns_window.setIgnoresMouseEvents_, mouse_through)
    _set_safely(ns_window.setLevel_, WINDOW_LEVELS.get(level_key, 1000))
    return True


def make_non_activating(tk_window) -> bool:
    """让点击这个窗口时**不激活本应用**（游戏不会因此失焦）。

    macOS：用 Tk 的私有窗口样式 `noActivates`。
    Windows：由 `apply_level` 统一设置 WS_EX_NOACTIVATE，这里无需重复。
    不支持时返回 False，窗口仍然能用，只是点击会短暂把应用切到前台。
    """
    if not IS_MACOS:
        return False
    try:
        tk_window.tk.call("tk::unsupported::MacWindowStyle", "style",
                          tk_window._w, "help", "noActivates")
        return True
    except Exception:
        return False


def forget(native_title: str) -> None:
    """窗口销毁后清掉缓存，避免持有已释放的 Objective-C 对象。"""
    _ns_windows.pop(native_title, None)
