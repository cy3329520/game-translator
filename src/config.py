"""配置管理：读写 ~/.game-translator/config.json"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

# 允许用环境变量指定配置目录：自动化测试靠它写临时文件，
# 避免测试用的假接口地址被写进用户真实配置里。
CONFIG_DIR = Path(os.environ.get("GAME_TRANSLATOR_HOME", "").strip()
                  or (Path.home() / ".game-translator"))
CONFIG_PATH = CONFIG_DIR / "config.json"


PROMPT_TRANSLATION_ONLY = """你是一个游戏实时字幕翻译引擎。用户会给你一张游戏画面的截图。

请完成两件事：
1. 识别图片中所有可见的日文文本（OCR）。
2. 把它们翻译成自然、通顺的简体中文。

输出规则：
- 只输出翻译后的中文，不要输出任何解释、说明、标题、代码块或前后缀。
- 保持原文的阅读顺序与换行结构：原文分几行，译文就分几行。
- 忽略纯 UI 元素、数字、进度条、按钮图标等没有实际语义的内容。
- 如果画面中没有可识别的日文文本，只输出一个空行，不要输出「无文本」「没有日文」之类的说明。
- 人名、地名、技能名、道具名等专有名词，使用游戏圈通用译法。
- 不要自行添加原文没有的引号或标点。"""


PROMPT_BILINGUAL = """你是一个游戏实时字幕翻译引擎。用户会给你一张游戏画面的截图。

请完成两件事：
1. 识别图片中所有可见的日文文本（OCR）。
2. 把它们翻译成自然、通顺的简体中文。

输出规则：
- 每一组「日文原文」和「中文译文」各占一行，原文在前、译文紧跟其后。
- 组与组之间空一行。
- 不要输出任何解释、说明、标题或代码块。
- 如果画面中没有可识别的日文文本，只输出一个空行，不要输出任何说明文字。
- 人名、地名、技能名、道具名等专有名词，使用游戏圈通用译法。"""


@dataclass
class Config:
    # ---------- 接口 ----------
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    reasoning_effort: str = "none"      # none / low / high / max
    max_output_tokens: int = 1024

    # ---------- 图像 ----------
    image_detail: str = "high"          # high / low
    image_max_width: int = 1280         # 送模型前的最长边缩放上限
    image_quality: int = 82             # JPEG 质量

    # ---------- 翻译区域（逻辑坐标，tkinter 坐标系）----------
    region: list[Any] | None = None     # [left, top, width, height]

    # ---------- 触发方式 ----------
    # auto   = 画面一变化就自动翻译
    # manual = 只在点击「区域旁边的悬浮按钮」或按快捷键时翻译
    trigger_mode: str = "auto"

    # ---------- 实时策略 ----------
    poll_interval_ms: int = 200         # 画面检测间隔（截图很便宜，密一点判定更准）
    # 画面变化阈值，单位是「明显变化的像素占区域的百分比」。
    # 越小越敏感。实测静止画面是 0.00，出现单个汉字约 0.15~0.43，
    # 所以 0.1 既不会漏掉逐字变化，也不会被噪点误触发。
    change_threshold: float = 0.1
    min_request_interval_ms: int = 1200  # 两次请求之间的最小间隔（防抖 + 省费用）
    # 画面静止多久才算"文字显示完了"，然后才发起翻译。
    # 游戏用打字机效果逐字显示对话时，靠它把一次对话合并成一次请求，
    # 避免翻到半句话。设为 0 则退化成"画面一变就翻"。
    settle_ms: int = 350

    # ---------- 输出 ----------
    output_mode: str = "translation_only"  # translation_only / bilingual
    font_size: int = 16
    overlay_opacity: float = 0.92
    overlay_gap: int = 8                # 字幕条与区域的间距
    # 字幕窗置顶强度：off / floating / status / screen_saver
    # Tk 自带的 -topmost 只到「浮动」层级，遇到全屏或高层级窗口会被压住，
    # 所以默认走原生 API 拉到最高层级。
    overlay_level: str = "screen_saver"

    # ---------- 快捷键 ----------
    hotkey: str = "<cmd>+<shift>+t"     # pynput 组合键语法
    hotkey_enabled: bool = True

    # ---------- 高级 ----------
    extra_prompt: str = ""              # 追加到系统提示词末尾

    # ------------------------------------------------------------------
    @property
    def system_prompt(self) -> str:
        base = PROMPT_BILINGUAL if self.output_mode == "bilingual" else PROMPT_TRANSLATION_ONLY
        extra = (self.extra_prompt or "").strip()
        if extra:
            base = base + "\n\n补充要求：\n" + extra
        return base

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/responses"

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if not CONFIG_PATH.exists():
            return cfg
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cfg
        if not isinstance(raw, dict):
            return cfg
        valid = {f.name for f in fields(cls)}
        for key, value in raw.items():
            if key in valid and value is not None:
                setattr(cfg, key, value)
        # 兼容旧配置：早先用 auto_translate 布尔值表示"自动 / 手动"
        if "trigger_mode" not in raw and "auto_translate" in raw:
            cfg.trigger_mode = "auto" if raw["auto_translate"] else "manual"
        return cfg


def mask_key(key: str) -> str:
    """给 API Key 打码，用于界面显示。"""
    key = (key or "").strip()
    if not key:
        return "（未设置）"
    if len(key) <= 12:
        return key[:2] + "*" * max(0, len(key) - 2)
    return f"{key[:6]}...{key[-4:]}"
