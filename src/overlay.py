"""悬浮窗：强制置顶的字幕条 + 贴在区域旁边的翻译按钮。"""

from __future__ import annotations

import tkinter as tk

from .platform_window import (IS_MACOS, UI_FONT, apply_level, forget, load_appkit,
                              make_non_activating)

BG = "#0F1014"
BORDER = "#2A2D36"
ACCENT = "#4C8DFF"
TEXT_MAIN = "#F2F4F8"
TEXT_DIM = "#98A2B3"
FONT_FAMILY = UI_FONT

# 给原生窗口起唯一名字，方便从 NSApplication 的窗口列表里精确找到
NATIVE_WINDOW_TITLE = "GameTranslatorOverlay"
NATIVE_BUTTON_TITLE = "GameTranslatorTrigger"

BTN_BG = "#16202E"
BTN_BG_HOVER = "#1F4E8C"
BTN_BG_ACTIVE = "#2E6FDB"
BTN_BG_BUSY = "#141A22"
BTN_BORDER = "#3B82F6"
BTN_FG = "#E8F0FC"
BTN_FG_BUSY = "#7C8CA3"
BTN_WIDTH = 100
BTN_HEIGHT = 38


class SubtitleOverlay:
    """一条跟随翻译区域的字幕条。"""

    def __init__(self, master: tk.Misc, config):
        self.cfg = config
        self.master = master
        self._visible = False
        self._topmost_ok = False
        self._last_geometry: tuple[int, int, int, int] | None = None
        self.native_note: str | None = None

        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.title(NATIVE_WINDOW_TITLE)   # 用于在原生窗口列表里认出自己
        try:
            self.win.attributes("-alpha", float(config.overlay_opacity))
        except tk.TclError:
            pass
        self.win.configure(bg=BORDER)

        self.outer = tk.Frame(self.win, bg=BG)
        self.outer.pack(fill="both", expand=True, padx=1, pady=1)

        self.accent = tk.Frame(self.outer, bg=ACCENT, height=2)
        self.accent.pack(fill="x", side="top")

        self.label = tk.Label(
            self.outer,
            text="",
            bg=BG,
            fg=TEXT_MAIN,
            justify="left",
            anchor="w",
            font=(FONT_FAMILY, int(config.font_size)),
            padx=12,
            pady=8,
        )
        self.label.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    def set_opacity(self, value: float) -> None:
        self.cfg.overlay_opacity = value
        try:
            self.win.attributes("-alpha", float(value))
        except tk.TclError:
            pass

    def set_font_size(self, size: int) -> None:
        self.cfg.font_size = int(size)
        self.label.configure(font=(FONT_FAMILY, int(size)))

    def set_mode_color(self, mode: str) -> None:
        self.accent.configure(bg=ACCENT if mode == "translation_only" else "#A855F7")

    # ------------------------------------------------------------------
    def apply_topmost(self, force: bool = False) -> bool:
        """把字幕窗的层级拉到最高，确保它盖在游戏等所有应用之上。

        等级由 config.overlay_level 决定。底层还会顺带开启：
          * 鼠标穿透 —— 字幕条不拦截点击，不会挡住游戏操作
          * 失活不隐藏 —— 焦点切到游戏后字幕条依然显示
          * 跟随所有 Space —— 包括全屏应用所在的 Space
        """
        if force:
            forget(NATIVE_WINDOW_TITLE)

        if self.cfg.overlay_level == "off":
            apply_level(self.win, NATIVE_WINDOW_TITLE, "off", mouse_through=True)
            self.native_note = "已关闭强制置顶，仅使用 Tk 自带的浮动层级"
            self._topmost_ok = False
            return False

        ok = apply_level(self.win, NATIVE_WINDOW_TITLE,
                         self.cfg.overlay_level, mouse_through=True)
        if ok:
            self.native_note = None
        elif IS_MACOS and load_appkit() is None:
            self.native_note = "未安装 pyobjc，只能使用 Tk 自带的置顶（可能被全屏应用压住）"
        else:
            self.native_note = "未能提升到系统最高层级，置顶可能被游戏窗口压住"
        self._topmost_ok = ok
        return ok

    @property
    def topmost_active(self) -> bool:
        return self._topmost_ok

    # ------------------------------------------------------------------
    def show_text(self, text: str, region) -> None:
        if not text:
            self.show_empty(region)
            return
        self._render(text, region, color=TEXT_MAIN)

    def show_empty(self, region) -> None:
        self._render("画面中未识别到日文", region, color=TEXT_DIM)

    def show_error(self, message: str, region=None) -> None:
        target = region or self.cfg.region
        if not target:
            return
        self._render(f"⚠ {message}", target, color="#FF8A8A")

    def hide(self) -> None:
        self.win.withdraw()
        self._visible = False

    # ------------------------------------------------------------------
    def _render(self, text: str, region, color: str = TEXT_MAIN) -> None:
        left, top, width, height = [int(v) for v in region]
        screen_w = self.win.winfo_screenwidth()
        screen_h = self.win.winfo_screenheight()

        gap = int(self.cfg.overlay_gap)
        box_w = max(340, width)
        box_w = min(box_w, screen_w - 24)

        pad = 12
        self.label.configure(text=text, fg=color, wraplength=max(120, box_w - 2 * pad - 2))
        x = max(6, min(left, screen_w - box_w - 6))

        # 关键：overrideredirect 的窗口如果还在 withdrawn 状态，geometry 的位置不会生效
        # （窗口会停在 0,0）。所以先用估算高度把窗口映射出来，位置一次到位。
        if not self._visible:
            est_h = int(self.cfg.font_size) * 3 + 10
            y0 = self._clamp_y(top, height, est_h, gap, screen_h)
            self.win.geometry(f"{box_w}x{est_h}+{x}+{y0}")
            self.win.deiconify()
            self.win.lift()
            self._visible = True

        # 量出真实文字高度后精修（已映射的窗口再设 geometry 一定生效）
        self.win.update_idletasks()
        label_h = max(self.label.winfo_reqheight(), int(self.cfg.font_size) + 18)
        box_h = min(label_h + 4, int(screen_h * 0.45))

        y = self._clamp_y(top, height, box_h, gap, screen_h)
        self.win.geometry(f"{box_w}x{box_h}+{x}+{y}")
        self._last_geometry = (box_w, box_h, x, y)

        # 窗口真正映射出来之后再确认原生层级，确保它盖在游戏之上
        self.apply_topmost()

    @staticmethod
    def _clamp_y(top: int, height: int, box_h: int, gap: int, screen_h: int) -> int:
        """字幕默认贴在区域下方；下方放不下就翻到区域上方。"""
        y = top + height + gap
        if y + box_h > screen_h - 6:
            above = top - box_h - gap
            y = above if above >= 6 else max(6, screen_h - box_h - 6)
        return int(y)

    def destroy(self) -> None:
        forget(NATIVE_WINDOW_TITLE)
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class TriggerButton:
    """贴在翻译区域旁边的悬浮按钮，点一下翻译一次（手动模式用）。

    和字幕条的关键区别：这个窗口**不能**鼠标穿透，否则点不到。
    另外用 Tk 的 `noActivates` 样式，让点击它时**不激活本应用** ——
    游戏不会因为按钮被点而失焦暂停。
    """

    def __init__(self, master: tk.Misc, config, on_click):
        self.cfg = config
        self.on_click = on_click
        self._visible = False
        self._busy = False
        self._hover = False
        self._pressed = False
        self.native_note: str | None = None

        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.title(NATIVE_BUTTON_TITLE)
        self.win.configure(bg=BTN_BORDER)

        self.body = tk.Frame(self.win, bg=BTN_BG)
        self.body.pack(fill="both", expand=True, padx=1, pady=1)

        self.label = tk.Label(
            self.body, text="翻译", bg=BTN_BG, fg=BTN_FG,
            font=(FONT_FAMILY, 13, "bold"), cursor="pointinghand",
        )
        self.label.pack(fill="both", expand=True)

        for widget in (self.win, self.body, self.label):
            widget.bind("<ButtonPress-1>", self._on_press)
            widget.bind("<ButtonRelease-1>", self._on_release)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

        make_non_activating(self.win)

    # ------------------------------------------------------------------
    @property
    def visible(self) -> bool:
        return self._visible

    def _paint(self) -> None:
        if self._busy:
            bg, fg = BTN_BG_BUSY, BTN_FG_BUSY
        elif self._pressed:
            bg, fg = BTN_BG_ACTIVE, "#FFFFFF"
        elif self._hover:
            bg, fg = BTN_BG_HOVER, "#FFFFFF"
        else:
            bg, fg = BTN_BG, BTN_FG
        self.body.configure(bg=bg)
        self.label.configure(bg=bg, fg=fg)

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._paint()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._pressed = False
        self._paint()

    def _on_press(self, _event=None) -> None:
        if self._busy:
            return
        self._pressed = True
        self._paint()

    def _on_release(self, _event=None) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self._paint()
        if was_pressed and self._hover and not self._busy:
            self.on_click()

    # ------------------------------------------------------------------
    def show(self, region=None) -> None:
        region = region or self.cfg.region
        if not region or len(region) != 4:
            return

        left, top, width, height = [int(v) for v in region]
        screen_w = self.win.winfo_screenwidth()
        screen_h = self.win.winfo_screenheight()

        # 优先贴在区域右侧、与区域顶部对齐；右边放不下就挪到左侧
        x = left + width + 10
        if x + BTN_WIDTH > screen_w - 6:
            x = left - BTN_WIDTH - 10
        x = max(6, min(int(x), screen_w - BTN_WIDTH - 6))
        y = max(6, min(int(top), screen_h - BTN_HEIGHT - 6))

        # 先按目标位置映射出来，再补一次几何（原因同字幕窗）
        self.win.geometry(f"{BTN_WIDTH}x{BTN_HEIGHT}+{x}+{y}")
        if not self._visible:
            self.win.deiconify()
            self.win.lift()
            self._visible = True
            # noActivates 要在窗口真正映射之后再设一次才稳
            make_non_activating(self.win)
        self.win.geometry(f"{BTN_WIDTH}x{BTN_HEIGHT}+{x}+{y}")

        self.reapply_topmost()

    def hide(self) -> None:
        self.win.withdraw()
        self._visible = False
        self._hover = False
        self._pressed = False
        self._paint()

    def set_busy(self, busy: bool) -> None:
        if busy == self._busy:
            return
        self._busy = busy
        self.label.configure(text="翻译中…" if busy else "翻译")
        self._paint()

    def reapply_topmost(self) -> bool:
        """飘在游戏之上（但不穿透鼠标，否则点不到）。"""
        if self.cfg.overlay_level == "off":
            apply_level(self.win, NATIVE_BUTTON_TITLE, "off", mouse_through=False)
            return False
        ok = apply_level(self.win, NATIVE_BUTTON_TITLE,
                         self.cfg.overlay_level, mouse_through=False)
        if not ok and IS_MACOS and load_appkit() is None:
            self.native_note = "未安装 pyobjc，按钮可能被游戏窗口压住"
        else:
            self.native_note = None
        return ok

    def destroy(self) -> None:
        forget(NATIVE_BUTTON_TITLE)
        try:
            self.win.destroy()
        except tk.TclError:
            pass
