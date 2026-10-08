"""主控制台界面。"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from .capture import RegionSelector, ScreenGrabber
from .config import Config, mask_key
from .deepseek import ApiError, Translator
from .engine import TranslateEngine
from .hotkey import HotkeyManager, permission_hint
from .overlay import SubtitleOverlay, TriggerButton
from .platform_window import IS_MACOS, UI_FONT

APP_TITLE = "游戏实时翻译器 · DeepSeek"

# 抓屏失败的提示：两个系统的「屏幕录制权限」说法不同
SCREEN_PERMISSION_HINT = (
    "请到「系统设置 → 隐私与安全性 → 屏幕录制」中授权后重试。"
    if IS_MACOS else
    "请确认程序有屏幕捕获权限（部分安全软件会拦截截屏）。"
)
PREVIEW_HINT = (
    "如果这里是黑屏/纯色，说明没有授予屏幕录制权限"
    if IS_MACOS else
    "如果这里是黑屏/纯色，可能是屏幕捕获被安全软件或显卡驱动拦截"
)

MODE_LABELS = {"translation_only": "仅译文", "bilingual": "日文 + 译文"}
MODE_VALUES = {v: k for k, v in MODE_LABELS.items()}
EFFORT_LABELS = {"none": "关闭（最快）", "low": "低", "high": "高", "max": "最高"}
EFFORT_VALUES = {v: k for k, v in EFFORT_LABELS.items()}
DETAIL_LABELS = {"high": "high（原图，识别更准）", "low": "low（缩到 512，更省）"}
DETAIL_VALUES = {v: k for k, v in DETAIL_LABELS.items()}
LEVEL_LABELS = {
    "screen_saver": "最高 · 盖住所有应用（含全屏游戏）",
    "status": "较高 · 盖住应用与菜单栏",
    "floating": "普通 · 只浮在普通窗口之上",
    "off": "关闭 · 用系统默认层级",
}
LEVEL_VALUES = {v: k for k, v in LEVEL_LABELS.items()}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = Config.load()
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.engine = TranslateEngine(self.cfg, self.events)
        self.overlay = SubtitleOverlay(root, self.cfg)
        self.trigger_button = TriggerButton(root, self.cfg, self._on_trigger_button_clicked)
        self.hotkeys = HotkeyManager(self._on_hotkey_fired)
        self._last_log_text = None

        root.title(APP_TITLE)
        self.screen_w = root.winfo_screenwidth()
        self.screen_h = root.winfo_screenheight()

        # 默认贴在屏幕右上角，尽量避免压住游戏主体；高度随屏幕自适应
        win_w = 540
        win_h = min(720, max(560, self.screen_h - 180))
        x = max(20, self.screen_w - win_w - 40)
        y = 56
        root.geometry(f"{win_w}x{win_h}+{x}+{y}")
        root.minsize(500, 620)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_vars()
        self._build_ui()
        self._register_hotkey(silent=True)
        self._pump()
        self._log(f"屏幕逻辑分辨率 {self.screen_w}×{self.screen_h}")
        self._update_mode_hint()
        if self.cfg.region:
            self.region_var.set(self._format_region(self.cfg.region))
            self._log(f"已载入上次的区域 {self._format_region(self.cfg.region)}")

    # ==================================================================
    #  变量
    # ==================================================================
    def _build_vars(self) -> None:
        cfg = self.cfg
        self.api_key_var = tk.StringVar(value=cfg.api_key)
        self.show_key_var = tk.BooleanVar(value=False)
        self.base_url_var = tk.StringVar(value=cfg.base_url)
        self.region_var = tk.StringVar(value="尚未框选")

        self.effort_var = tk.StringVar(value=EFFORT_LABELS.get(cfg.reasoning_effort, "关闭（最快）"))
        self.mode_var = tk.StringVar(value=MODE_LABELS.get(cfg.output_mode, "仅译文"))
        self.detail_var = tk.StringVar(value=DETAIL_LABELS.get(cfg.image_detail, "high（原图，识别更准）"))
        self.max_tokens_var = tk.IntVar(value=int(cfg.max_output_tokens))
        self.image_width_var = tk.IntVar(value=int(cfg.image_max_width))
        self.image_quality_var = tk.DoubleVar(value=float(cfg.image_quality))

        self.trigger_var = tk.StringVar(value=cfg.trigger_mode)
        self.mode_hint_var = tk.StringVar(value="")
        self.poll_var = tk.IntVar(value=int(cfg.poll_interval_ms))
        self.settle_var = tk.IntVar(value=int(cfg.settle_ms))
        self.threshold_var = tk.DoubleVar(value=float(cfg.change_threshold))
        self.min_interval_var = tk.IntVar(value=int(cfg.min_request_interval_ms))

        self.font_size_var = tk.IntVar(value=int(cfg.font_size))
        self.opacity_var = tk.DoubleVar(value=float(cfg.overlay_opacity))
        self.gap_var = tk.IntVar(value=int(cfg.overlay_gap))
        self.level_var = tk.StringVar(
            value=LEVEL_LABELS.get(cfg.overlay_level, LEVEL_LABELS["screen_saver"]))

        self.hotkey_var = tk.StringVar(value=cfg.hotkey)
        self.hotkey_enabled_var = tk.BooleanVar(value=bool(cfg.hotkey_enabled))

        self.status_var = tk.StringVar(value="就绪")
        self.stats_var = tk.StringVar(value="")
        self.hotkey_hint_var = tk.StringVar(value=self._hotkey_hint_text(cfg.hotkey))

    @staticmethod
    def _pretty_hotkey(hotkey: str) -> str:
        return (hotkey or "").replace("<cmd>", "⌘").replace("<shift>", "⇧") \
            .replace("<ctrl>", "⌃").replace("<alt>", "⌥") \
            .replace("<enter>", "↩").replace("<space>", "空格").replace("+", " ").strip()

    def _hotkey_hint_text(self, hotkey: str) -> str:
        pretty = self._pretty_hotkey(hotkey)
        return f"全局快捷键：{pretty}（按一下立即翻译一次）" if pretty else "未设置全局快捷键"

    # ==================================================================
    #  界面
    # ==================================================================
    def _build_ui(self) -> None:
        # 先把底部状态栏 pack 好，再让 notebook 占满剩余空间，否则状态栏会被挤掉
        bar = ttk.Frame(self.root, padding=(12, 6))
        bar.pack(fill="x", side="bottom")
        ttk.Label(bar, textvariable=self.status_var, foreground="#8E8E93").pack(side="left")
        ttk.Label(bar, textvariable=self.stats_var, foreground="#8E8E93").pack(side="right")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        self.tab_main = ttk.Frame(self.notebook, padding=14)
        self.tab_settings = ttk.Frame(self.notebook)
        self.tab_log = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.tab_main, text="  翻译  ")
        self.notebook.add(self.tab_settings, text="  设置  ")
        self.notebook.add(self.tab_log, text="  日志  ")

        self._build_main_tab()
        self._build_settings_tab()
        self._build_log_tab()

    # ------------------------------------------------------------------
    def _make_scrollable(self, container: ttk.Frame) -> ttk.Frame:
        """把容器变成可滚动区域，返回真正的内层 Frame。"""
        canvas = tk.Canvas(container, highlightthickness=0, borderwidth=0)
        vbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, padding=14)

        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window_id, width=e.width))

        def on_wheel(event) -> None:
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        return inner

    # ------------------------------------------------------------------
    def _build_main_tab(self) -> None:
        parent = self.tab_main
        parent.columnconfigure(0, weight=1)

        tips = ("用法：① 填 API Key 并测试 → ② 框选游戏里的日文区域 → ③ 点「开始实时翻译」。\n"
                "画面一变就自动翻译，字幕条会贴在框选区域的正下方；也可随时按快捷键手动翻一次。")
        ttk.Label(parent, text=tips, foreground="#8E8E93", justify="left",
                  wraplength=470).grid(row=0, column=0, sticky="w", pady=(0, 12))

        # --- API Key ---
        box = ttk.LabelFrame(parent, text=" DeepSeek 接口 ", padding=12)
        box.grid(row=1, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="API Key").grid(row=0, column=0, sticky="w")
        self.key_entry = ttk.Entry(box, textvariable=self.api_key_var, show="•")
        self.key_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Checkbutton(box, text="显示", variable=self.show_key_var,
                        command=self._toggle_key_visible).grid(row=0, column=2)

        key_row = ttk.Frame(box)
        key_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(key_row, text="接口地址").pack(side="left")
        ttk.Entry(key_row, textvariable=self.base_url_var).pack(
            side="left", fill="x", expand=True, padx=(8, 0))

        ttk.Label(box, text="默认 https://api.deepseek.com，一般不用改",
                  foreground="#8E8E93").grid(row=2, column=0, columnspan=3,
                                             sticky="w", pady=(2, 0))

        test_row = ttk.Frame(box)
        test_row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Button(test_row, text="测试接口连通性", command=self._on_test_api).pack(side="left")
        ttk.Label(test_row, text="模型 deepseek-flash（视觉 + 翻译一次完成）",
                  foreground="#8E8E93").pack(side="left", padx=(10, 0))

        # --- 区域 ---
        region_box = ttk.LabelFrame(parent, text=" 翻译区域 ", padding=12)
        region_box.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        region_box.columnconfigure(0, weight=1)

        ttk.Label(region_box, textvariable=self.region_var, foreground="#0A84FF").grid(
            row=0, column=0, sticky="w")
        btns = ttk.Frame(region_box)
        btns.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(btns, text="框选区域", command=self._on_select_region).pack(side="left")
        ttk.Button(btns, text="预览截图", command=self._on_preview).pack(side="left", padx=8)
        ttk.Button(btns, text="隐藏字幕", command=self.overlay.hide).pack(side="left")

        # --- 运行 ---
        run_box = ttk.LabelFrame(parent, text=" 实时翻译 ", padding=12)
        run_box.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        run_box.columnconfigure(0, weight=1)

        self.start_btn = ttk.Button(run_box, text="▶  开始实时翻译", command=self._on_toggle_engine)
        self.start_btn.grid(row=0, column=0, sticky="ew", ipady=6)

        mode_row = ttk.Frame(run_box)
        mode_row.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(mode_row, text="模式").pack(side="left")
        ttk.Radiobutton(mode_row, text="自动 · 画面变化就翻", value="auto",
                        variable=self.trigger_var,
                        command=self._on_trigger_mode_changed).pack(side="left", padx=(10, 14))
        ttk.Radiobutton(mode_row, text="手动 · 点悬浮按钮才翻", value="manual",
                        variable=self.trigger_var,
                        command=self._on_trigger_mode_changed).pack(side="left")

        ttk.Label(run_box, textvariable=self.mode_hint_var, foreground="#8E8E93",
                  wraplength=450, justify="left").grid(row=2, column=0, sticky="w", pady=(8, 0))

        act_row = ttk.Frame(run_box)
        act_row.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(act_row, text="立即翻译一次", command=self._on_translate_once).pack(side="left")
        ttk.Button(act_row, text="测试字幕置顶", command=self._on_test_topmost).pack(side="left", padx=8)

        ttk.Label(run_box, textvariable=self.hotkey_hint_var,
                  foreground="#8E8E93").grid(row=4, column=0, sticky="w", pady=(8, 0))

    # ------------------------------------------------------------------
    def _build_settings_tab(self) -> None:
        parent = self._make_scrollable(self.tab_settings)
        parent.columnconfigure(0, weight=1)

        net = ttk.LabelFrame(parent, text=" 请求 ", padding=12)
        net.grid(row=0, column=0, sticky="ew")
        net.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(net, text="思考模式").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(net, textvariable=self.effort_var, state="readonly",
                     values=list(EFFORT_LABELS.values())).grid(row=r, column=1, sticky="ew", padx=(10, 0), pady=3)
        r += 1
        ttk.Label(net, text="输出格式").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(net, textvariable=self.mode_var, state="readonly",
                     values=list(MODE_LABELS.values())).grid(row=r, column=1, sticky="ew", padx=(10, 0), pady=3)
        r += 1
        ttk.Label(net, text="图片精度").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(net, textvariable=self.detail_var, state="readonly",
                     values=list(DETAIL_LABELS.values())).grid(row=r, column=1, sticky="ew", padx=(10, 0), pady=3)
        r += 1
        ttk.Label(net, text="最大输出 tokens").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Spinbox(net, from_=128, to=8192, increment=128, textvariable=self.max_tokens_var,
                    width=8).grid(row=r, column=1, sticky="w", padx=(10, 0), pady=3)
        r += 1
        ttk.Label(net, text="截图最长边(px)").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Spinbox(net, from_=400, to=3000, increment=100, textvariable=self.image_width_var,
                    width=8).grid(row=r, column=1, sticky="w", padx=(10, 0), pady=3)

        live = ttk.LabelFrame(parent, text=" 实时策略 ", padding=12)
        live.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        live.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(live, text="检测间隔(ms)").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Spinbox(live, from_=100, to=3000, increment=50, textvariable=self.poll_var,
                    width=8).grid(row=r, column=1, sticky="w", padx=(10, 0), pady=3)
        r += 1
        ttk.Label(live, text="稳定判定(ms)").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Spinbox(live, from_=0, to=3000, increment=50, textvariable=self.settle_var,
                    width=8).grid(row=r, column=1, sticky="w", padx=(10, 0), pady=3)
        r += 1
        ttk.Label(live, text="画面静止这么久才开始翻译，用来等打字机效果把话显示完；0 = 最快（一变就翻）",
                  foreground="#8E8E93", wraplength=440, justify="left").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 6))
        r += 1
        ttk.Label(live, text="请求最小间隔(ms)").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Spinbox(live, from_=300, to=15000, increment=100, textvariable=self.min_interval_var,
                    width=8).grid(row=r, column=1, sticky="w", padx=(10, 0), pady=3)
        r += 1
        self._scale_row(live, r, "变化灵敏度阈值", self.threshold_var, 0.0, 3.0, 0.05, "{:.2f}")
        ttk.Label(live, text="单位是「明显变化的像素占比 %」，越小越敏感；画面有循环动画时调大它",
                  foreground="#8E8E93", wraplength=440, justify="left").grid(
            row=r + 1, column=0, columnspan=2, sticky="w")

        style = ttk.LabelFrame(parent, text=" 字幕样式 ", padding=12)
        style.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        style.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(style, text="字号").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Spinbox(style, from_=10, to=42, increment=1, textvariable=self.font_size_var,
                    width=8, command=self._sync_config_from_ui).grid(
            row=r, column=1, sticky="w", padx=(10, 0), pady=3)
        r += 1
        self._scale_row(style, r, "不透明度", self.opacity_var, 0.3, 1.0, 0.02, "{:.2f}")
        r += 2
        ttk.Label(style, text="字幕与区域间距(px)").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Spinbox(style, from_=0, to=200, increment=2, textvariable=self.gap_var,
                    width=8).grid(row=r, column=1, sticky="w", padx=(10, 0), pady=3)
        r += 1
        ttk.Label(style, text="置顶层级").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(style, textvariable=self.level_var, state="readonly",
                     values=list(LEVEL_LABELS.values())).grid(
            row=r, column=1, sticky="ew", padx=(10, 0), pady=3)
        r += 1
        ttk.Label(style, text="字幕盖在哪些窗口之上。Tk 自带的置顶只到「浮动」层级，"
                              "遇到全屏或无边框游戏会被压住，所以默认用原生最高层级。",
                  foreground="#8E8E93", wraplength=440, justify="left").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(2, 0))

        hk = ttk.LabelFrame(parent, text=" 全局快捷键 ", padding=12)
        hk.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        hk.columnconfigure(1, weight=1)
        ttk.Label(hk, text="组合键").grid(row=0, column=0, sticky="w")
        ttk.Entry(hk, textvariable=self.hotkey_var).grid(row=0, column=1, sticky="ew", padx=(10, 8))
        ttk.Button(hk, text="注册", command=self._register_hotkey).grid(row=0, column=2)
        ttk.Label(hk, text=('语法如 <cmd>+<shift>+t、<ctrl>+<alt>+j'
                            if IS_MACOS else
                            '语法如 <ctrl>+<shift>+t、<ctrl>+<alt>+j'),
                  foreground="#8E8E93").grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(hk, text=permission_hint(), foreground="#8E8E93", wraplength=440,
                  justify="left").grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        adv = ttk.LabelFrame(parent, text=" 追加提示词（可选，写给模型的额外要求）", padding=12)
        adv.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        adv.columnconfigure(0, weight=1)
        self.extra_text = tk.Text(adv, height=3, wrap="word", relief="solid", borderwidth=1,
                                  font=(UI_FONT, 12))
        self.extra_text.grid(row=0, column=0, sticky="ew")
        self.extra_text.insert("1.0", self.cfg.extra_prompt or "")
        ttk.Label(adv, text="例如：把「お姉さん」统一译成「大姐姐」；保留技能名原文。",
                  foreground="#8E8E93").grid(row=1, column=0, sticky="w", pady=(6, 0))

        ttk.Button(parent, text="保存设置", command=self._on_save).grid(
            row=5, column=0, sticky="ew", pady=(14, 0), ipady=4)

    # ------------------------------------------------------------------
    def _build_log_tab(self) -> None:
        parent = self.tab_log
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        bar = ttk.Frame(parent)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(bar, text="清空", command=self._clear_log).pack(side="left")
        ttk.Label(bar, text="每条记录：时间 · 耗时 · token · 译文",
                  foreground="#8E8E93").pack(side="left", padx=(10, 0))

        wrapper = tk.Frame(parent, bg="#1C1C1E", highlightthickness=1,
                           highlightbackground="#3A3A3C")
        wrapper.grid(row=1, column=0, sticky="nsew")
        self.log_text = tk.Text(wrapper, wrap="word", bg="#1C1C1E", fg="#E5E5EA",
                                insertbackground="#E5E5EA", relief="flat", borderwidth=0,
                                font=(UI_FONT, 12), padx=10, pady=8)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(wrapper, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set, state="disabled")

    # ------------------------------------------------------------------
    def _scale_row(self, parent, row: int, title: str, var, frm: float, to: float,
                   step: float, fmt: str) -> None:
        ttk.Label(parent, text=title).grid(row=row, column=0, sticky="w", pady=3)
        holder = ttk.Frame(parent)
        holder.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=3)
        holder.columnconfigure(0, weight=1)

        value_label = ttk.Label(holder, width=6, foreground="#0A84FF")

        def refresh(*_args) -> None:
            value_label.configure(text=fmt.format(float(var.get())))

        scale = ttk.Scale(holder, from_=frm, to=to, variable=var, orient="horizontal",
                          command=lambda _v: refresh())
        scale.grid(row=0, column=0, sticky="ew")
        value_label.grid(row=0, column=1, padx=(8, 0))
        var.trace_add("write", refresh)
        refresh()

    # ==================================================================
    #  工具
    # ==================================================================
    @staticmethod
    def _format_region(region) -> str:
        left, top, width, height = [int(v) for v in region]
        return f"{width} × {height}  @ ({left}, {top})"

    def _log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _toggle_key_visible(self) -> None:
        self.key_entry.configure(show="" if self.show_key_var.get() else "•")

    # ==================================================================
    #  配置同步
    # ==================================================================
    def _sync_config_from_ui(self) -> None:
        cfg = self.cfg
        try:
            cfg.api_key = self.api_key_var.get().strip()
            cfg.base_url = self.base_url_var.get().strip() or "https://api.deepseek.com"
            cfg.reasoning_effort = EFFORT_VALUES.get(self.effort_var.get(), "none")
            cfg.output_mode = MODE_VALUES.get(self.mode_var.get(), "translation_only")
            cfg.image_detail = DETAIL_VALUES.get(self.detail_var.get(), "high")
            cfg.max_output_tokens = max(64, int(self.max_tokens_var.get()))
            cfg.image_max_width = max(200, int(self.image_width_var.get()))
            cfg.trigger_mode = self.trigger_var.get()
            cfg.poll_interval_ms = max(50, int(self.poll_var.get()))
            cfg.settle_ms = max(0, int(self.settle_var.get()))
            cfg.change_threshold = float(self.threshold_var.get())
            cfg.min_request_interval_ms = max(200, int(self.min_interval_var.get()))
            cfg.font_size = max(8, int(self.font_size_var.get()))
            cfg.overlay_opacity = min(1.0, max(0.2, float(self.opacity_var.get())))
            cfg.overlay_gap = max(0, int(self.gap_var.get()))
            cfg.hotkey = self.hotkey_var.get().strip()
            cfg.overlay_level = LEVEL_VALUES.get(self.level_var.get(), "screen_saver")
            cfg.extra_prompt = self.extra_text.get("1.0", "end").strip() if hasattr(self, "extra_text") else cfg.extra_prompt
        except (tk.TclError, ValueError):
            pass

        self.overlay.set_font_size(cfg.font_size)
        self.overlay.set_opacity(cfg.overlay_opacity)
        self.overlay.set_mode_color(cfg.output_mode)
        # 置顶强度可能被改过，立即重新应用到字幕窗
        self.overlay.apply_topmost()

    def _save_config(self) -> None:
        self._sync_config_from_ui()
        try:
            self.cfg.save()
        except OSError as exc:
            self._log(f"⚠ 配置保存失败：{exc}")

    def _on_save(self) -> None:
        self._save_config()
        self._log("设置已保存到 ~/.game-translator/config.json")
        self.status_var.set("设置已保存")

    # ==================================================================
    #  截图相关
    # ==================================================================
    def _new_grabber(self) -> ScreenGrabber:
        return ScreenGrabber(self.screen_w, self.screen_h)

    def _on_select_region(self) -> None:
        self._sync_config_from_ui()
        self.overlay.hide()
        self.root.withdraw()
        self.root.update()
        time.sleep(0.35)

        shot = None
        try:
            shot = self._new_grabber().grab_full()
        except Exception as exc:
            self.root.deiconify()
            messagebox.showerror(
                "截图失败",
                f"{exc}\n\n{SCREEN_PERMISSION_HINT}")
            return
        finally:
            self.root.deiconify()
            self.root.update()

        try:
            result = RegionSelector.select(self.root, shot, self.screen_w, self.screen_h,
                                           self.cfg.region)
        except Exception as exc:
            self._log(f"⚠ 框选窗口异常：{exc}")
            return

        if not result:
            self._log("已取消框选")
            return

        self.cfg.region = [int(v) for v in result]
        self.region_var.set(self._format_region(result))
        self._log(f"翻译区域已设为 {self._format_region(result)}")
        self._save_config()
        self._sync_trigger_button()   # 区域变了，悬浮按钮要跟着挪位置
        self._on_preview()

    def _on_preview(self) -> None:
        if not self.cfg.region:
            messagebox.showinfo("还没有区域", "请先点「框选区域」。")
            return
        try:
            image = self._new_grabber().grab_region(self.cfg.region)
        except Exception as exc:
            messagebox.showerror("截图失败", f"{exc}")
            return
        self._show_image_window(image)

    def _on_test_topmost(self) -> None:
        """显示一条测试字幕，并报告原生置顶是否生效。"""
        self._sync_config_from_ui()
        region = self.cfg.region or [
            max(20, self.screen_w // 2 - 320),
            max(20, self.screen_h // 2 - 140),
            640, 110,
        ]
        self.overlay.show_text(
            "置顶测试\n能看到这条字幕盖在游戏画面之上，就说明置顶已生效。", region)

        def report() -> None:
            if self.overlay.topmost_active:
                label = LEVEL_LABELS.get(self.cfg.overlay_level, "")
                self._log(f"✓ 字幕窗已置于原生层级（{label}）")
                self._log("  已开启鼠标穿透，字幕条不会挡住游戏的点击操作")
                self.status_var.set("置顶已生效")
            else:
                note = self.overlay.native_note or "未能提升到原生层级"
                self._log(f"⚠ {note}")
                self.status_var.set("置顶未生效")

        self.root.after(250, report)

    def _show_image_window(self, image: Image.Image) -> None:
        win = tk.Toplevel(self.root)
        win.title("区域预览")
        win.attributes("-topmost", True)

        max_w, max_h = 900, 600
        ratio = min(1.0, max_w / image.width, max_h / image.height)
        shown = image if ratio >= 1.0 else image.resize(
            (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))), Image.LANCZOS)
        photo = ImageTk.PhotoImage(shown)

        canvas = tk.Canvas(win, width=shown.width, height=shown.height,
                           highlightthickness=0, bg="#000000")
        canvas.pack()
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas.image_ref = photo

        ttk.Label(win, text=f"实际抓取 {image.width} × {image.height} 物理像素 —— "
                            f"{PREVIEW_HINT}",
                  foreground="#8E8E93").pack(pady=8)

    # ==================================================================
    #  引擎控制
    # ==================================================================
    def _on_toggle_engine(self) -> None:
        if self.engine.running:
            self.engine.stop()
            return
        self._sync_config_from_ui()
        if not self.cfg.api_key:
            messagebox.showwarning("缺少 API Key", "请先填写 DeepSeek API Key。")
            self.notebook.select(self.tab_main)
            return
        if not self.cfg.region:
            messagebox.showwarning("缺少区域", "请先点「框选区域」选定要翻译的画面。")
            return
        self._save_config()
        self.overlay.set_mode_color(self.cfg.output_mode)
        self.engine.start((self.screen_w, self.screen_h))

    def _on_translate_once(self) -> None:
        self._sync_config_from_ui()
        if not self.cfg.api_key or not self.cfg.region:
            messagebox.showwarning("还差一步", "请先填写 API Key 并框选翻译区域。")
            return
        if not self.engine.running:
            self.engine.start((self.screen_w, self.screen_h))
            self.root.after(400, self.engine.trigger_once)
        else:
            self.engine.trigger_once()

    # ------------------------------------------------------------------
    #  触发模式 / 悬浮按钮
    # ------------------------------------------------------------------
    def _update_mode_hint(self) -> None:
        if self.cfg.trigger_mode == "manual":
            self.mode_hint_var.set(
                "手动模式：不会自动发请求。点「区域旁边的悬浮按钮」翻译一次；"
                "按钮不会激活本应用，游戏不会失焦。快捷键同样有效。")
        else:
            self.mode_hint_var.set(
                "自动模式：画面一变就翻译，不用管。想完全自己控制就切到手动模式。")

    def _on_trigger_mode_changed(self) -> None:
        self._sync_config_from_ui()
        self._update_mode_hint()
        self._save_config()
        self._sync_trigger_button()

    def _sync_trigger_button(self) -> None:
        """按「模式 + 引擎状态 + 是否框了区域」决定悬浮按钮显示还是隐藏。"""
        should_show = (
            self.cfg.trigger_mode == "manual"
            and self.engine.running
            and bool(self.cfg.region)
        )
        if should_show:
            self.trigger_button.show(self.cfg.region)
        else:
            self.trigger_button.hide()

    def _on_trigger_button_clicked(self) -> None:
        self.status_var.set("悬浮按钮：翻译一次")
        self.trigger_button.set_busy(True)
        self._on_translate_once()

    def _on_hotkey_fired(self) -> None:
        self.events.put_nowait({"kind": "hotkey"})

    def _on_test_api(self) -> None:
        self._sync_config_from_ui()
        if not self.cfg.api_key:
            messagebox.showwarning("缺少 API Key", "请先填写 DeepSeek API Key。")
            return
        self._save_config()
        self.status_var.set("正在测试接口…")

        def worker() -> None:
            try:
                reply = Translator(self.cfg).ping()
                self.events.put_nowait({"kind": "log", "text": f"✓ 接口正常（{mask_key(self.cfg.api_key)}）：{reply}"})
                self.events.put_nowait({"kind": "status", "text": "接口连通正常"})
            except ApiError as exc:
                self.events.put_nowait({"kind": "log", "text": f"✗ 接口测试失败：{exc}"})
                self.events.put_nowait({"kind": "status", "text": "接口测试失败"})
            except Exception as exc:
                self.events.put_nowait({"kind": "log", "text": f"✗ 接口测试失败：{exc}"})
                self.events.put_nowait({"kind": "status", "text": "接口测试失败"})

        threading.Thread(target=worker, daemon=True).start()

    def _register_hotkey(self, silent: bool = False) -> None:
        self._sync_config_from_ui()
        if not self.cfg.hotkey_enabled:
            return
        ok, message = self.hotkeys.register(self.cfg.hotkey)
        self.hotkey_hint_var.set(self._hotkey_hint_text(self.cfg.hotkey))
        if ok:
            self._log(f"✓ 快捷键 {self._pretty_hotkey(self.cfg.hotkey)} 已注册")
        elif not silent:
            self._log(f"⚠ {message}")
            messagebox.showinfo("快捷键未注册", message + "\n\n" + permission_hint())
        else:
            self._log(f"⚠ 快捷键未注册：{message}")

    # ==================================================================
    #  事件循环
    # ==================================================================
    def _pump(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle(event)
        except queue.Empty:
            pass
        except Exception as exc:  # pragma: no cover
            self._log(f"⚠ 事件处理异常：{exc}")
        self.root.after(60, self._pump)

    def _handle(self, event: dict) -> None:
        kind = event.get("kind")

        if kind == "state":
            running = bool(event.get("running"))
            self.start_btn.configure(text="■  停止" if running else "▶  开始实时翻译")
            if running:
                self.status_var.set("运行中，等待画面变化…")
            # 悬浮按钮只在手动模式且引擎运行时出现
            self._sync_trigger_button()

        elif kind == "status":
            self.status_var.set(event.get("text", ""))

        elif kind == "info":
            self._log(event.get("text", ""))

        elif kind == "partial":
            self.overlay.show_text(event.get("text", ""), event.get("region"))

        elif kind == "result":
            self.trigger_button.set_busy(False)
            text = event.get("text", "")
            region = event.get("region")
            if text:
                self.overlay.show_text(text, region)
            else:
                self.overlay.show_empty(region)
            self._log_result(text, event.get("stats") or {})

        elif kind == "error":
            self.trigger_button.set_busy(False)
            message = event.get("message", "")
            self.status_var.set(f"⚠ {message}")
            self._log(f"⚠ {message}")
            if event.get("region"):
                self.overlay.show_error(message, event.get("region"))
            if event.get("fatal"):
                self.start_btn.configure(text="▶  开始实时翻译")

        elif kind == "log":
            self._log(event.get("text", ""))

        elif kind == "hotkey":
            self.status_var.set("快捷键触发：翻译一次")
            self._on_translate_once()

    def _log_result(self, text: str, stats: dict) -> None:
        usage = stats.get("usage") or {}
        total = usage.get("total_tokens")
        elapsed = stats.get("elapsed") or 0.0
        first = stats.get("first_token")
        parts = [f"{elapsed:.2f}s"]
        if first is not None:
            parts.append(f"首字 {first:.2f}s")
        if total:
            parts.append(f"{total} tok")
        head = " · ".join(parts)

        if text and text == self._last_log_text:
            return
        self._last_log_text = text
        self._log(f"{head}  →  {text if text else '（画面中未识别到日文）'}")
        self.stats_var.set(f"最近一次：{head}")

    # ==================================================================
    def on_close(self) -> None:
        self._save_config()
        self.engine.stop()
        self.hotkeys.unregister()
        self.overlay.destroy()
        self.trigger_button.destroy()
        self.root.destroy()
