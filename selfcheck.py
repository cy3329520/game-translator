#!/usr/bin/env python3
"""无 GUI 自检脚本。

会短暂在屏幕中央显示一个红色方块，用来验证：
  1. 屏幕捕获是否真的能拿到屏幕内容（即「屏幕录制」权限是否已授予）
  2. Retina 缩放换算是否准确（逻辑坐标 -> 物理像素）
  3. 变化检测是否灵敏
  4. 送模型的图片编码是否正常

用法：  python3 selfcheck.py
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import ImageStat

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.capture import ChangeDetector, ScreenGrabber   # noqa: E402
from src.deepseek import encode_image                    # noqa: E402
from src.platform_window import IS_MACOS                 # noqa: E402

OK = "  [通过]"
NG = "  [失败]"


def detector_selfcheck() -> None:
    """变化检测的纯逻辑自检：不依赖屏幕，任何平台都能跑（无 GUI）。"""
    from PIL import Image

    black = Image.new("RGB", (640, 360), (0, 0, 0))
    white = Image.new("RGB", (640, 360), (255, 255, 255))
    nearly = Image.new("RGB", (640, 360), (5, 5, 5))     # 与黑几乎一样

    d = ChangeDetector()
    assert d.diff(black) == 100.0            # 第一帧必然算变化
    assert d.diff(black) == 0.0, "同一帧不应判定为变化"
    assert d.diff(white) >= 99.0, "整屏由黑变白应几乎全部判为变化"
    assert d.diff(black) >= 99.0, "整屏由白变黑应几乎全部判为变化"
    assert d.diff(nearly) < 0.1, "极微小差异不应触发（低于默认阈值 0.1）"
    d.reset()
    assert d.diff(black) == 100.0, "reset 后应重新以第一帧处理"
    print(f"{OK} 变化检测逻辑自检通过（ChangeDetector）")


def main() -> int:
    failures: list[str] = []

    detector_selfcheck()   # 纯逻辑自检，不需要屏幕权限

    root = tk.Tk()
    root.withdraw()
    logical_w, logical_h = root.winfo_screenwidth(), root.winfo_screenheight()
    print(f"逻辑分辨率: {logical_w} x {logical_h}")

    grabber = ScreenGrabber(logical_w, logical_h)
    phys_w, phys_h = grabber.physical_size
    print(f"物理分辨率: {phys_w} x {phys_h}")
    print(f"缩放系数:   {grabber.scale:.3f}")

    # ---- 1. 抓屏 ----------------------------------------------------
    tw, th = 400, 200
    region = [(logical_w - tw) // 2, (logical_h - th) // 2, tw, th]
    print(f"\n测试区域: {tw}x{th} @ ({region[0]}, {region[1]})")

    try:
        before = grabber.grab_region(region)
    except Exception as exc:
        print(f"{NG} 抓屏异常: {exc}")
        return 1

    expect_w = int(round(tw * grabber.scale))
    expect_h = int(round(th * grabber.scale))
    if abs(before.width - expect_w) <= 2 and abs(before.height - expect_h) <= 2:
        print(f"{OK} 抓屏尺寸 {before.width}x{before.height}（预期 {expect_w}x{expect_h}）")
    else:
        failures.append(f"抓屏尺寸不符：得到 {before.width}x{before.height}，预期 {expect_w}x{expect_h}")
        print(f"{NG} 抓屏尺寸 {before.width}x{before.height}，预期 {expect_w}x{expect_h}")

    mean_before = ImageStat.Stat(before.convert("L")).mean[0]
    print(f"     抓屏平均亮度: {mean_before:.1f}")

    # ---- 2. 弹一个红块，验证坐标换算与实时性 -------------------------
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.geometry(f"{tw}x{th}+{region[0]}+{region[1]}")
    win.configure(bg="#FF3B30")
    root.update()
    time.sleep(0.8)

    detector = ChangeDetector()
    detector.diff(before)                  # 建立基准帧
    after = grabber.grab_region(region)
    diff = detector.diff(after)            # 与基准帧比较

    center = after.getpixel((after.width // 2, after.height // 2))
    win.destroy()
    root.update()

    print(f"     红块中心像素: {center}  最亮通道: {max(center)}")
    if center[0] > 200 and center[1] < 90 and center[2] < 90:
        print(f"{OK} 捕获到的确实是屏幕上的红块，坐标换算正确")
    else:
        failures.append(f"未捕获到红块（中心像素 {center}）")
        print(f"{NG} 未捕获到红块，中心像素 {center}")
        if IS_MACOS:
            print("     最常见原因：没有给终端授予「屏幕录制」权限，"
                  "此时 macOS 只会返回桌面壁纸。")
        else:
            print("     常见原因：屏幕捕获被安全软件/显卡驱动拦截，"
                  "或游戏运行在独占全屏模式。")

    if diff >= 2.0:
        print(f"{OK} 变化检测生效，前后帧差异 {diff:.1f}")
    else:
        failures.append(f"变化检测未触发（差异仅 {diff:.1f}）")
        print(f"{NG} 变化检测未触发，差异仅 {diff:.1f}")

    # ---- 3. 图片编码 ------------------------------------------------
    data_url = encode_image(before, max_width=1280, quality=82)
    kib = len(data_url) / 1024.0
    if data_url.startswith("data:image/jpeg;base64,") and kib > 1:
        print(f"{OK} 图片编码正常，base64 体积 {kib:.0f} KB")
    else:
        failures.append("图片编码异常")
        print(f"{NG} 图片编码异常（{kib:.1f} KB）")

    # ---- 4. 结果汇总 ------------------------------------------------
    print("\n" + "=" * 46)
    if failures:
        print(f"自检未通过，{len(failures)} 项异常：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("全部自检通过，可以正常使用了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
