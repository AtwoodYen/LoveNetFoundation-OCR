"""
保留偏藍、黑色筆跡，壓抑其他顏色（如紅色印刷），輸出黑字白底供 OCR。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import cv2
import numpy as np
from PIL import Image

##############################################################
# 排除紅/橘色，只保留藍黑墨跡
##############################################################
def _spec_to_params(ink_filter: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """範本 ink_filter 區塊；未指定時用預設。"""
    f = ink_filter or {}
    return {
        "blue_h_lo": int(f.get("blue_h_lo", 88)),
        "blue_h_hi": int(f.get("blue_h_hi", 138)),
        "blue_s_min": int(f.get("blue_s_min", 38)),
        "blue_v_min": int(f.get("blue_v_min", 18)),
        "dark_s_max": int(f.get("dark_s_max", 95)),
        "dark_v_max": int(f.get("dark_v_max", 155)),
        "red_exclude_s_min": int(f.get("red_exclude_s_min", 65)),
        "red_exclude_v_min": int(f.get("red_exclude_v_min", 35)),
        "red_h1_hi": int(f.get("red_h1_hi", 18)),
        "red_h2_lo": int(f.get("red_h2_lo", 168)),
    }


def filter_blue_black_ink(
    pil_rgb: Image.Image,
    ink_filter: Optional[Dict[str, Any]] = None,
) -> Image.Image:
    """
    僅保留藍色與偏黑（低飽和暗色）筆畫，其餘設為白底；筆畫改為純黑。

    會排除飽和度高的紅／橘色區塊，以減少表單上紅色印刷進入 OCR。
    """
    p = _spec_to_params(ink_filter)
    rgb = np.asarray(pil_rgb.convert("RGB"), dtype=np.uint8)
    if rgb.size == 0:
        return pil_rgb
    h, w = rgb.shape[:2]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    lower_b = np.array([p["blue_h_lo"], p["blue_s_min"], p["blue_v_min"]])
    upper_b = np.array([p["blue_h_hi"], 255, 255])
    mask_blue = cv2.inRange(hsv, lower_b, upper_b)

    lower_dark = np.array([0, 0, 0])
    upper_dark = np.array([179, p["dark_s_max"], p["dark_v_max"]])
    mask_dark = cv2.inRange(hsv, lower_dark, upper_dark)

    rs, rv = p["red_exclude_s_min"], p["red_exclude_v_min"]
    mask_red_a = cv2.inRange(hsv, np.array([0, rs, rv]), np.array([p["red_h1_hi"], 255, 255]))
    mask_red_b = cv2.inRange(
        hsv,
        np.array([p["red_h2_lo"], rs, rv]),
        np.array([179, 255, 255]),
    )
    mask_red = cv2.bitwise_or(mask_red_a, mask_red_b)
    mask_dark_ink = cv2.bitwise_and(mask_dark, cv2.bitwise_not(mask_red))

    mask = cv2.bitwise_or(mask_blue, mask_dark_ink)

    ksz = 2
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksz, ksz))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    out = np.full((h, w, 3), 255, dtype=np.uint8)
    out[mask > 0] = (0, 0, 0)
    return Image.fromarray(out, mode="RGB")


def should_apply_ink_filter(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """若範本啟用 ink_filter，回傳設定 dict；否則 None。"""
    ink = spec.get("ink_filter")
    if not isinstance(ink, dict):
        return None
    if not ink.get("enabled", False):
        return None
    return ink
