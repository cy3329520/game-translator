"""全局快捷键（macOS 需要「辅助功能」权限，Windows 无需额外权限）。"""

from __future__ import annotations

from .platform_window import IS_MACOS

if IS_MACOS:
    PERMISSION_HINT = ("若快捷键无反应，请到「系统设置 → 隐私与安全性 → 辅助功能」里，"
                       "把运行本程序的终端（Terminal / iTerm）勾选上，然后重启程序。")
else:
    PERMISSION_HINT = ("若快捷键无反应，可能是组合键被别的软件占用，换一个组合键再试"
                       "（例如 <ctrl>+<alt>+j）。")


def permission_hint() -> str:
    return PERMISSION_HINT


class HotkeyManager:
    """用 pynput 注册一个全局热键。不可用时优雅降级，不影响主流程。"""

    def __init__(self, on_trigger):
        self.on_trigger = on_trigger
        self._listener = None
        self._combo: str | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._listener is not None

    def register(self, combo: str) -> tuple[bool, str]:
        """注册热键。返回 (是否成功, 提示文本)。"""
        self.unregister()
        combo = (combo or "").strip()
        if not combo:
            return False, "未配置快捷键"

        try:
            from pynput import keyboard
        except Exception as exc:  # pragma: no cover - 环境相关
            self.last_error = str(exc)
            return False, f"未安装 pynput：{exc}"

        def wrapped() -> None:
            # 回调运行在监听线程里，只做线程安全的投递
            try:
                self.on_trigger()
            except Exception:
                pass

        try:
            listener = keyboard.GlobalHotKeys({combo: wrapped})
            listener.daemon = True
            listener.start()
            self._listener = listener
            self._combo = combo
            self.last_error = None
            return True, f"已注册 {combo}"
        except Exception as exc:
            self.last_error = str(exc)
            return False, f"快捷键注册失败：{exc}"

    def unregister(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
            self._combo = None

    # ------------------------------------------------------------------
    @staticmethod
    def permission_hint() -> str:
        return PERMISSION_HINT
