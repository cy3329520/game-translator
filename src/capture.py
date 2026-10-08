"""屏幕捕获、区域框选、画面变化检测。

坐标系约定：
  - 对外一律使用「逻辑坐标」（tkinter 的屏幕坐标，Retina 屏上是物理像素的一半）
  - 只在真正抓屏的那一刻换算成物理像素
"""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageChops, ImageEnhance, ImageTk

from .platform_window import UI_FONT


class ScreenGrabber:
    """mss 的封装。mss 实例不能跨线程共享，请在将要使用它的线程里创建。"""

    def __init__(self, logical_width: int | None = None, logical_height: int | None = None):
        import mss  # 延迟导入：无 GUI 环境下也能 import 本模块

        self.sct = mss.mss()
        # monitors[0] 是所有显示器的并集，monitors[1] 是主显示器
        self.monitor = dict(self.sct.monitors[1])
        self.scale = 1.0
        if logical_width and logical_height:
            self.set_logical_size(logical_width, logical_height)

    # ------------------------------------------------------------------
    def set_logical_size(self, width: int, height: int) -> None:
        """告诉抓屏器：这块屏幕的逻辑分辨率是多少，用于 Retina 换算。"""
        if width > 0:
            self.scale = self.monitor["width"] / float(width)

    @property
    def physical_size(self) -> tuple[int, int]:
        return self.monitor["width"], self.monitor["height"]

    # ------------------------------------------------------------------
    def _physical_box(self, region) -> dict:
        left, top, width, height = region
        return {
            "left": self.monitor["left"] + int(round(left * self.scale)),
            "top": self.monitor["top"] + int(round(top * self.scale)),
            "width": max(1, int(round(width * self.scale))),
            "height": max(1, int(round(height * self.scale))),
        }

    def grab_region(self, region) -> Image.Image:
        """抓取指定逻辑坐标区域的画面。"""
        raw = self.sct.grab(self._physical_box(region))
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    def grab_full(self) -> Image.Image:
        """抓取整块主显示器。"""
        raw = self.sct.grab(self.monitor)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


class ChangeDetector:
    """画面变化检测。

    度量方式是「明显变化的像素占比（%）」，而不是「平均灰度差」。
    原因是逐字显示（打字机效果）时一个字只占区域的百分之一左右，
    平均下来差异会被稀释到 1 上下，很容易低于阈值而被忽略；
    按"有多少像素发生了明显变化"来算，局部的小变化就不会被稀释。

    采样宽度也不能太小：96px 宽会把单个汉字抗锯齿糊掉。
    """

    def __init__(self, sample_width: int = 224, strong_delta: int = 40):
        self.sample_width = sample_width
        self.strong_delta = strong_delta   # 灰度差超过它才算"明显变化"
        self._prev: Image.Image | None = None

    def reset(self) -> None:
        self._prev = None

    def diff(self, image: Image.Image) -> float:
        """返回「明显变化像素的百分比」（0~100），并更新内部状态。

        第一帧（或尺寸变了）返回 100.0，表示必然需要处理一次。
        """
        sample_height = max(1, int(round(self.sample_width * image.height / max(1, image.width))))
        sample = image.convert("L").resize((self.sample_width, sample_height), Image.BILINEAR)

        if self._prev is None or self._prev.size != sample.size:
            self._prev = sample
            return 100.0

        delta = ImageChops.difference(sample, self._prev)
        self._prev = sample

        histogram = delta.histogram()      # 256 个桶，索引就是灰度差
        total = sample.width * sample.height
        strong = sum(histogram[self.strong_delta:])
        return strong * 100.0 / max(1, total)

    def is_changed(self, image: Image.Image, threshold: float) -> bool:
        return self.diff(image) >= threshold


class RegionSelector:
    """全屏半透明遮罩，拖拽框选一块区域。返回逻辑坐标 (left, top, width, height)。"""

    MIN_SIZE = 16

    @classmethod
    def select(cls, master: tk.Misc, screenshot: Image.Image,
               logical_width: int, logical_height: int,
               initial=None) -> tuple[int, int, int, int] | None:
        win = tk.Toplevel(master)
        win.overrideredirect(True)
        win.geometry(f"{logical_width}x{logical_height}+0+0")
        win.attributes("-topmost", True)
        win.configure(bg="#000000")

        canvas = tk.Canvas(win, width=logical_width, height=logical_height,
                           highlightthickness=0, bd=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)

        base = screenshot.resize((logical_width, logical_height), Image.LANCZOS)
        base = ImageEnhance.Brightness(base).enhance(0.60)
        photo = ImageTk.PhotoImage(base)
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas.image_ref = photo  # 防止被 GC

        state: dict = {"x0": None, "y0": None, "rect": None, "size_text": None,
                       "buttons": [], "result": None, "finished": False}

        canvas.create_text(24, 20, anchor="nw", fill="#EBEEF5",
                           font=(UI_FONT, 15, "bold"),
                           text="拖动鼠标框选要翻译的区域")
        canvas.create_text(24, 48, anchor="nw", fill="#98A2B3",
                           font=(UI_FONT, 12),
                           text="框选游戏里的日文对话/字幕区域 · Esc 取消")

        if initial:
            cls._draw_guides(canvas, initial, logical_width, logical_height)

        def clear_buttons() -> None:
            for item in state["buttons"]:
                canvas.delete(item)
            state["buttons"] = []

        def on_press(event) -> None:
            if state["finished"]:
                return
            clear_buttons()
            state["x0"], state["y0"] = event.x, event.y

        def on_drag(event) -> None:
            if state["finished"] or state["x0"] is None:
                return
            left, top, width, height = cls._normalize(state["x0"], state["y0"], event.x, event.y)
            if state["rect"] is None:
                state["rect"] = canvas.create_rectangle(
                    left, top, left + width, top + height,
                    outline="#4C8DFF", width=2)
                state["size_text"] = canvas.create_text(
                    left + 6, max(0, top - 16), anchor="nw", fill="#4C8DFF",
                    font=(UI_FONT, 12, "bold"), text="")
            else:
                canvas.coords(state["rect"], left, top, left + width, top + height)
                canvas.coords(state["size_text"], left + 6, max(0, top - 16))
            canvas.itemconfigure(state["size_text"], text=f"{width} × {height}")

        def on_release(event) -> None:
            if state["finished"] or state["x0"] is None:
                return
            left, top, width, height = cls._normalize(state["x0"], state["y0"], event.x, event.y)
            state["x0"] = None
            if width < cls.MIN_SIZE or height < cls.MIN_SIZE:
                return
            state["pending"] = (left, top, width, height)
            cls._draw_confirm(canvas, state, left, top, width, height, logical_width, logical_height)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)

        def confirm(_event=None) -> None:
            state["result"] = state.get("pending")
            win.destroy()

        def restart(_event=None) -> None:
            state["finished"] = False
            state["pending"] = None
            clear_buttons()
            if state["rect"]:
                canvas.delete(state["rect"])
                state["rect"] = None
            if state["size_text"]:
                canvas.delete(state["size_text"])
                state["size_text"] = None

        def cancel(_event=None) -> None:
            state["result"] = None
            win.destroy()

        state["confirm"] = confirm
        state["restart"] = restart
        state["cancel"] = cancel
        state["canvas"] = canvas

        win.bind("<Escape>", cancel)
        win.bind("<Return>", lambda e: confirm() if state.get("pending") else None)
        win.focus_force()

        win.grab_set()
        master.wait_window(win)
        return state["result"]

    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(x0, y0, x1, y1) -> tuple[int, int, int, int]:
        left, top = min(x0, x1), min(y0, y1)
        return int(left), int(top), int(abs(x1 - x0)), int(abs(y1 - y0))

    @staticmethod
    def _draw_guides(canvas, region, logical_width, logical_height) -> None:
        left, top, width, height = region
        if width < 4 or height < 4:
            return
        canvas.create_rectangle(left, top, left + width, top + height,
                                outline="#98A2B3", width=1, dash=(4, 4))

    @staticmethod
    def _draw_confirm(canvas, state, left, top, width, height,
                      logical_width, logical_height) -> None:
        for item in state["buttons"]:
            canvas.delete(item)
        state["buttons"] = []
        state["finished"] = True

        canvas.itemconfigure(state["rect"], outline="#2FBF71", width=3)

        btn_y = min(top + height + 12, logical_height - 44)
        btn_y = max(btn_y, 12)
        ok_x = min(left, logical_width - 210)
        ok_x = max(ok_x, 12)

        ok_bg = canvas.create_rectangle(ok_x, btn_y, ok_x + 130, btn_y + 34,
                                        fill="#2FBF71", outline="")
        ok_tx = canvas.create_text(ok_x + 65, btn_y + 17, fill="#0B0D12",
                                   font=(UI_FONT, 13, "bold"),
                                   text="✓ 使用此区域")
        re_bg = canvas.create_rectangle(ok_x + 142, btn_y, ok_x + 252, btn_y + 34,
                                        fill="#2A2D36", outline="")
        re_tx = canvas.create_text(ok_x + 197, btn_y + 17, fill="#D5DAE3",
                                   font=(UI_FONT, 13), text="↺ 重新框选")

        state["buttons"] += [ok_bg, ok_tx, re_bg, re_tx]

        canvas.tag_bind(ok_bg, "<Button-1>", lambda e: state["confirm"]())
        canvas.tag_bind(ok_tx, "<Button-1>", lambda e: state["confirm"]())
        canvas.tag_bind(re_bg, "<Button-1>", lambda e: state["restart"]())
        canvas.tag_bind(re_tx, "<Button-1>", lambda e: state["restart"]())
