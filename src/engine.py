"""翻译引擎：在后台线程里跑「截图 → 变化检测 → 调用 DeepSeek → 投递结果」。

线程模型：
  - 主线程只跑 tkinter
  - 本模块跑在独立工作线程里，所有界面更新都通过 queue 交回主线程
"""

from __future__ import annotations

import queue
import threading
import time

from .capture import ChangeDetector, ScreenGrabber
from .config import Config
from .deepseek import ApiError, Translator

PARTIAL_THROTTLE = 0.08      # 流式文本最快 80ms 刷一次字幕
MIN_FORCE_INTERVAL = 0.35    # 手动触发的最短间隔，防连按
MAX_BACKOFF = 8.0
# 画面一直在动（立绘动画、循环特效）时，最多等这么久就强制翻一次，
# 否则会永远等不到"静止"而一直不翻译。
MAX_SETTLE_WAIT_MS = 2500


class TranslateEngine:
    def __init__(self, cfg: Config, event_queue: "queue.Queue[dict]"):
        self.cfg = cfg
        self.q = event_queue
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._force = threading.Event()
        self._running = False

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    def start(self, logical_size: tuple[int, int]) -> bool:
        if self._running:
            return False
        self._stop.clear()
        self._force.clear()
        self._thread = threading.Thread(
            target=self._run, args=(logical_size,), name="translate-engine", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self._force.set()

    def trigger_once(self) -> None:
        """请求立刻翻译一次（快捷键 / 手动按钮）。"""
        self._force.set()

    # ------------------------------------------------------------------
    def _emit(self, kind: str, /, **payload) -> None:
        """往界面线程投递一个事件。

        `kind` 特意声明成位置限定参数（`/`）：它的名字和 payload 里可能出现的
        键名容易撞车，用位置传参能避免"got multiple values for argument 'kind'"
        这种在异常处理分支里才炸出来的错误。
        """
        try:
            self.q.put_nowait({"kind": kind, **payload})
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _run(self, logical_size: tuple[int, int]) -> None:
        self._running = True
        self._emit("state", running=True)

        try:
            grabber = ScreenGrabber(*logical_size)
        except Exception as exc:
            self._emit("error", message=f"初始化屏幕捕获失败：{exc}", fatal=True)
            self._running = False
            self._emit("state", running=False)
            return

        detector = ChangeDetector()
        translator = Translator(self.cfg)

        last_request = 0.0
        last_text: str | None = None
        last_error: str | None = None
        failures = 0
        tracked_region = tuple(self.cfg.region) if self.cfg.region else None
        # 变化结算状态：画面一变先不发请求，等它安静下来再说
        pending_since: float | None = None   # 本轮变化第一次被检测到的时刻
        last_change_at: float | None = None  # 最近一次检测到变化的时刻
        quiet_streak = 0                     # 连续多少次检测"画面没变"

        self._emit("status", text="运行中，等待画面变化…")
        self._emit("info", text=f"截屏缩放系数 {grabber.scale:.2f}（物理 {grabber.physical_size[0]}×{grabber.physical_size[1]}）")

        try:
            while not self._stop.is_set():
                cfg = self.cfg
                region = cfg.region
                if not region or len(region) != 4 or region[2] < 4 or region[3] < 4:
                    self._emit("error", message="还没有框选翻译区域", fatal=True)
                    break

                region_key = tuple(int(v) for v in region)
                if region_key != tracked_region:
                    tracked_region = region_key
                    detector.reset()
                    last_text = None
                    self._emit("status", text="区域已更新，等待画面变化…")

                try:
                    image = grabber.grab_region(region_key)
                except Exception as exc:
                    self._emit("error", message=f"截图失败：{exc}")
                    time.sleep(1.0)
                    continue

                forced = self._force.is_set()
                if forced:
                    self._force.clear()

                changed = detector.is_changed(image, cfg.change_threshold)
                now = time.time()
                since_last = now - last_request
                hard_ready = since_last * 1000 >= MIN_FORCE_INTERVAL * 1000
                soft_ready = since_last * 1000 >= cfg.min_request_interval_ms

                # 画面一变不立刻请求，先记下来，等它安静下来再发。
                # 这样逐字显示（打字机效果）的对话只会翻一次完整句子，
                # 而不是每冒出几个字就翻一遍。
                if changed:
                    if pending_since is None:
                        pending_since = now
                        self._emit("status", text="画面有变化，等文字显示完再翻…")
                    last_change_at = now
                    quiet_streak = 0
                else:
                    quiet_streak += 1

                settle_ms = max(0, int(cfg.settle_ms))
                # 至少连续确认几次"画面没变"。取 3 次是因为存在"拍频"：
                # 检测周期和打字节奏接近时，偶尔会连续两次都恰好落在字与字之间，
                # 三次连续落空则几乎不可能。稳定判定设为 0 时退化成最快模式。
                min_quiet = 3 if settle_ms > 0 else 1
                quiet_enough = quiet_streak >= min_quiet
                time_enough = (
                    last_change_at is not None
                    and (now - last_change_at) * 1000 >= settle_ms
                )
                settled = quiet_enough and time_enough
                # 画面上有循环动画时永远等不到静止，给一个等待上限兜底
                waited_too_long = (
                    pending_since is not None
                    and (now - pending_since) * 1000 >= MAX_SETTLE_WAIT_MS
                )

                auto_trigger = (
                    cfg.trigger_mode == "auto"
                    and pending_since is not None
                    and (settled or waited_too_long)
                    and soft_ready
                    and hard_ready
                )
                trigger = (forced and hard_ready) or auto_trigger

                if not trigger:
                    time.sleep(max(0.05, cfg.poll_interval_ms / 1000.0))
                    continue

                pending_since = None
                last_change_at = None
                quiet_streak = 0
                last_request = time.time()
                self._emit("status", text="识别中…")

                last_push = 0.0

                def on_delta(text: str, _t0=last_request) -> None:
                    nonlocal last_push
                    now_inner = time.time()
                    if now_inner - last_push >= PARTIAL_THROTTLE:
                        last_push = now_inner
                        self._emit("partial", text=text, region=region_key)

                try:
                    result = translator.translate(
                        image, on_delta=on_delta, should_stop=self._stop.is_set
                    )
                except ApiError as exc:
                    failures += 1
                    message = str(exc)
                    if message != last_error:
                        last_error = message
                        # 注意：这里不能用 kind=...，会和 _emit 的第一个位置参数重名
                        self._emit("error", message=message, region=region_key,
                                   error_kind=exc.kind, status=exc.status)
                    else:
                        self._emit("log", text=f"⚠ {message}")
                    if exc.kind in ("no_key", "http") and exc.status in (401, 403):
                        self._emit("status", text="已停止：请检查 API Key")
                        break
                    time.sleep(min(MAX_BACKOFF, 0.8 * failures))
                    continue
                except Exception as exc:  # 兜底，别让线程死掉
                    failures += 1
                    self._emit("error", message=f"未预期的错误：{exc}", region=region_key)
                    time.sleep(min(MAX_BACKOFF, 0.8 * failures))
                    continue

                failures = 0
                last_error = None
                text = (result.get("text") or "").strip()

                if text == last_text:
                    self._emit("status", text=f"内容未变化 · 用时 {result['elapsed']:.1f}s")
                else:
                    last_text = text
                    self._emit("result", text=text, region=region_key, stats={
                        "elapsed": result.get("elapsed"),
                        "first_token": result.get("first_token"),
                        "usage": result.get("usage") or {},
                    })

                if self._stop.is_set():
                    break

                time.sleep(max(0.05, cfg.poll_interval_ms / 1000.0))
        except Exception as exc:
            # 兜底：任何意外异常都不能让引擎线程静默死掉，
            # 否则界面上会一直卡在"运行中"，按钮也点不动。
            self._emit("error", message=f"引擎内部错误，已停止：{exc}", fatal=True)
        finally:
            self._running = False
            self._emit("state", running=False)
            self._emit("status", text="已停止")
