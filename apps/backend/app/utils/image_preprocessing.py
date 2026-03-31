"""
圖片預處理模組 - 用於奉獻袋 OCR 預處理

處理步驟：
1. page_0001.png - 原圖
2. page_0002.png - 藍黑色變全黑（表格只處理右邊欄位，白字黃字不動）
3. page_0003.png - 去除橘色（含深橘色）
4. page_0004.png - OCR 辨識區塊標記紅框
5. page_0005.png - 手寫內容標註
"""

import cv2
import numpy as np
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass

from app.utils.logger import logger


@dataclass
class TextBlock:
    """OCR 辨識的文字區塊"""
    text: str
    bbox: Tuple[int, int, int, int]  # (x, y, width, height)
    confidence: float
    vertices: List[Tuple[int, int]]  # 四個頂點座標


@dataclass
class PreprocessResult:
    """預處理結果"""
    original_path: str
    contrast_enhanced_path: str   # page_0002.png (藍黑變全黑)
    orange_removed_path: str      # page_0003.png (去橘色)
    ocr_boxes_path: str           # page_0004.png (OCR 紅框)
    annotated_path: str           # page_0005.png (手寫標註)
    text_blocks: List[TextBlock]
    grouped_lines: List[List[TextBlock]]


def rgb_to_hsv(r: float, g: float, b: float) -> Tuple[float, float, float]:
    """RGB 轉 HSV (H: 0-1, S: 0-1, V: 0-1)"""
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    delta = max_c - min_c

    # Hue
    if delta == 0:
        h = 0
    elif max_c == r:
        h = ((g - b) / delta) % 6
    elif max_c == g:
        h = (b - r) / delta + 2
    else:
        h = (r - g) / delta + 4
    h /= 6
    if h < 0:
        h += 1

    # Saturation
    s = 0 if max_c == 0 else delta / max_c

    # Value
    v = max_c

    return h, s, v


def remove_orange_pixels(image: np.ndarray) -> np.ndarray:
    """
    亮度感知去橘：亮橘色背景→白色，暗色帶橘調（文字）→灰階保留，非橘色不動
    """
    logger.info("開始亮度感知去除橘色像素...")

    img_float = image.astype(np.float32) / 255.0
    result = image.copy()

    height, width = image.shape[:2]
    white_count = 0
    gray_count = 0

    for y in range(height):
        for x in range(width):
            b, g, r = img_float[y, x]
            h, s, v = rgb_to_hsv(r, g, b)

            # 橘色色調判斷
            is_orange_hue = (0.0 <= h <= 0.14) and (s > 0.15)
            if not is_orange_hue:
                continue  # 非橘色色調 → 不動

            # 感知亮度
            luminance = 0.299 * r + 0.587 * g + 0.114 * b

            if luminance > 0.55:
                # 亮橘色背景 → 白色
                result[y, x] = [255, 255, 255]
                white_count += 1
            elif luminance > 0.35:
                # 中等亮度帶橘調（文字邊緣）→ 去色保留亮度
                gray = int(max(0, min(255, luminance * 230)))
                result[y, x] = [gray, gray, gray]
                gray_count += 1
            # luminance <= 0.35：暗色文字 → 完全不動

    total = height * width
    logger.info(f"去除橘色: 白化={white_count}({white_count/total*100:.1f}%), 灰階={gray_count}({gray_count/total*100:.1f}%)")
    return result


def remove_orange_pixels_fast(image: np.ndarray) -> np.ndarray:
    """
    亮度感知去橘（向量化版本）：
    - 亮橘色背景（亮度高）→ 白色
    - 暗色但帶橘色調（文字邊緣）→ 去色保留亮度（轉灰階）
    - 非橘色像素 → 不動
    """
    logger.info("=" * 50)
    logger.info("===== remove_orange_pixels_fast（亮度感知版）被呼叫 =====")
    logger.info("=" * 50)

    # 轉換到 HSV 色彩空間（OpenCV 的 HSV：H: 0-179, S: 0-255, V: 0-255）
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)

    # 正規化為 0-1 範圍
    h_norm = h.astype(np.float32) / 179.0  # H: 0-179 → 0-1
    s_norm = s.astype(np.float32) / 255.0  # S: 0-255 → 0-1

    result = image.copy()

    # 計算感知亮度 (ITU-R BT.601)
    img_float = image.astype(np.float32) / 255.0
    b_ch, g_ch, r_ch = cv2.split(img_float)
    luminance = 0.299 * r_ch + 0.587 * g_ch + 0.114 * b_ch

    # 橘色色調判斷（寬鬆，包含深橘到淺橘）
    # H: 0-0.14 (對應角度 0-50°)，S > 0.15
    is_orange_hue = (h_norm >= 0.0) & (h_norm <= 0.14) & (s_norm > 0.15)

    # 亮橘色背景 (luminance > 0.55) → 白色
    bright_orange_mask = is_orange_hue & (luminance > 0.55)
    result[bright_orange_mask] = [255, 255, 255]

    # 中等亮度帶橘調 (0.35 < luminance <= 0.55) → 灰階
    mid_orange_mask = is_orange_hue & (luminance > 0.35) & (luminance <= 0.55)
    gray_values = np.clip(luminance * 230, 0, 255).astype(np.uint8)
    result[mid_orange_mask, 0] = gray_values[mid_orange_mask]
    result[mid_orange_mask, 1] = gray_values[mid_orange_mask]
    result[mid_orange_mask, 2] = gray_values[mid_orange_mask]

    # luminance <= 0.35：暗色像素（確定是文字）→ 完全不動

    total_pixels = image.shape[0] * image.shape[1]
    white_count = np.sum(bright_orange_mask)
    gray_count = np.sum(mid_orange_mask)
    logger.info(f"去除橘色: 白化={white_count}({white_count/total_pixels*100:.1f}%), 灰階={gray_count}({gray_count/total_pixels*100:.1f}%)")
    logger.info("===== remove_orange_pixels_fast 完成 =====")

    return result


def find_top_table_area(image: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """
    找出圖片上方的表格區域（奉獻袋的項目/金額表格）

    Returns:
        (x, y, w, h) 或 None
    """
    height, width = image.shape[:2]

    # 表格通常在上半部
    top_half = image[:height // 2, :]

    # 轉灰階
    gray = cv2.cvtColor(top_half, cv2.COLOR_BGR2GRAY)

    # 找白色區域（表格內部）
    _, white_mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)

    # 形態學操作
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 10))
    white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_CLOSE, kernel)

    # 找輪廓
    contours, _ = cv2.findContours(white_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # 找最大的白色區域（表格）
    max_area = 0
    best_box = None
    for contour in contours:
        area = cv2.contourArea(contour)
        if area > max_area and area > 10000:  # 最小面積限制
            x, y, w, h = cv2.boundingRect(contour)
            # 表格應該有一定寬度
            if w > width * 0.3:
                max_area = area
                best_box = (x, y, w, h)

    if best_box:
        logger.info(f"找到上方表格區域: {best_box}")
    else:
        logger.info("未找到上方表格區域")

    return best_box


def enhance_blue_black_to_black(image: np.ndarray) -> np.ndarray:
    """
    增強對比度：將藍色墨水和已經是深色（但不夠黑）的像素變成全黑

    處理邏輯：
    1. 藍色墨水 → 全黑
    2. 已經很深的灰黑色（V < 80）→ 全黑
    3. 白色字、黃色字、橘色、中間灰度：全部不處理
    4. 上方表格左邊（標籤欄）：保持不動
    """
    logger.info("=" * 50)
    logger.info("===== enhance_blue_black_to_black 被呼叫 =====")
    logger.info("處理邏輯: 藍色墨水→黑, 深色(V<80)→黑, 其他不動")
    logger.info("=" * 50)

    height, width = image.shape[:2]

    # 轉換到 HSV
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)

    # 結果圖像（複製原圖，保留原始顏色）
    result = image.copy()

    # 找出上方表格區域
    top_table = find_top_table_area(image)

    # 建立上方表格左半部的遮罩（這部分不處理）
    left_exclude_mask = np.zeros((height, width), dtype=np.uint8)
    if top_table:
        tx, ty, tw, th = top_table
        # 左半部（約 40% 是標籤欄）
        left_width = int(tw * 0.4)
        left_exclude_mask[ty:ty+th, tx:tx+left_width] = 255
        logger.info(f"  上方表格左邊排除區域: ({tx},{ty}) 到 ({tx+left_width},{ty+th})")

    # === 定義橘色範圍（這些不處理，保留給下一步）===
    # 橘色 H: 0-25 (OpenCV scale), 高飽和度
    orange_mask = (
        ((h >= 0) & (h <= 25) & (s > 80) & (v > 80)) |  # 標準橘色
        ((h >= 0) & (h <= 20) & (s > 60) & (v > 60))     # 深橘色
    )

    # === 明確排除白色區域（V > 150 的都不處理）===
    white_area_mask = (v > 150)
    logger.info(f"  白色區域(V>150): {np.sum(white_area_mask)} 像素，這些不會被處理")

    # === 需要變黑的顏色（只處理藍色墨水和很暗的像素）===

    # 1. 藍色墨水：H 90-130, S > 30（提高飽和度門檻，確保是真正的藍色）
    blue_mask = ((h >= 90) & (h <= 130) & (s > 30))

    # 2. 已經很深的像素：V < 80（只轉換本來就很暗的像素）
    very_dark_mask = (v < 80)

    # 合併需要變黑的遮罩（排除橘色、排除白色區域）
    ink_mask = (blue_mask | very_dark_mask) & (~orange_mask) & (~white_area_mask)

    # 排除上方表格左邊區域
    final_ink_mask = ink_mask & (left_exclude_mask == 0)

    # 將墨水區域設為全黑
    result[final_ink_mask] = [0, 0, 0]

    # 統計
    ink_count = np.sum(final_ink_mask)
    total_pixels = height * width
    logger.info(f"轉換了 {ink_count} 個像素為黑色 ({ink_count / total_pixels * 100:.2f}%)")
    logger.info(f"  藍色墨水: {np.sum(blue_mask)}, 深色(V<80): {np.sum(very_dark_mask)}")
    logger.info(f"  白色(V>150,保留): {np.sum(white_area_mask)}, 橘色(保留): {np.sum(orange_mask)}")
    logger.info("===== enhance_blue_black_to_black 完成 =====")

    return result


def enhance_blue_black_contrast(image: np.ndarray) -> np.ndarray:
    """舊版本介面（保留相容性）"""
    return enhance_blue_black_to_black(image)


def draw_ocr_boxes(
    image: np.ndarray,
    text_blocks: List[TextBlock],
    box_color: Tuple[int, int, int] = (0, 0, 255),  # BGR: 紅色
    thickness: int = 2
) -> np.ndarray:
    """
    在圖片上繪製 OCR 辨識的紅框
    """
    logger.info(f"繪製 {len(text_blocks)} 個辨識區塊的紅框...")

    result = image.copy()

    for block in text_blocks:
        if block.vertices and len(block.vertices) >= 4:
            # 使用四個頂點繪製多邊形
            pts = np.array(block.vertices, np.int32)
            pts = pts.reshape((-1, 1, 2))
            cv2.polylines(result, [pts], True, box_color, thickness)
        else:
            # 使用矩形
            x, y, w, h = block.bbox
            cv2.rectangle(result, (x, y), (x + w, y + h), box_color, thickness)

    return result


def group_blocks_by_y(
    text_blocks: List[TextBlock],
    y_threshold: float = 0.03  # Y 座標差異閾值（相對於圖片高度）
) -> List[List[TextBlock]]:
    """
    依據 Y 座標將文字區塊分組為行
    """
    if not text_blocks:
        return []

    # 按 Y 座標排序（從上到下）
    sorted_blocks = sorted(text_blocks, key=lambda b: b.bbox[1])

    lines: List[List[TextBlock]] = []
    current_line: List[TextBlock] = [sorted_blocks[0]]
    current_y = sorted_blocks[0].bbox[1]

    # 取得圖片高度（從 bbox 估算）
    max_y = max(b.bbox[1] + b.bbox[3] for b in text_blocks)
    threshold_pixels = max_y * y_threshold

    for block in sorted_blocks[1:]:
        block_y = block.bbox[1]

        if abs(block_y - current_y) <= threshold_pixels:
            # 同一行
            current_line.append(block)
        else:
            # 新的一行
            # 將當前行按 X 座標排序
            current_line.sort(key=lambda b: b.bbox[0])
            lines.append(current_line)
            current_line = [block]
            current_y = block_y

    # 最後一行
    if current_line:
        current_line.sort(key=lambda b: b.bbox[0])
        lines.append(current_line)

    return lines


# 奉獻袋印刷標籤（用於識別手寫內容）
PRINTED_LABELS = [
    "項目", "金額", "貧困關懷", "弱勢及偏鄉兒童青少年", "偏鄉老人",
    "愛心小站", "經常費", "合計", "奉獻日期", "年", "月", "日",
    "奉獻收據", "不需要", "上傳國稅局", "需要收據", "紙本", "電子檔",
    "奉獻人姓名", "電話", "手機", "收據抬頭", "同奉獻者",
    "身份證字號", "郵寄地址", "電子信箱", "無電子信箱者必填",
    "憐憫貧窮的", "就是借給耶和華", "他的善行", "耶和華必償還",
    "箴", "歡迎利用線上奉獻平台", "詳見背面", "無收據",
    "上傳國稅局（無收據）", "電子擔", "鄧寄地址"
]

# 印刷標籤的部分匹配關鍵字
PRINTED_KEYWORDS = [
    "奉獻", "收據", "姓名", "電話", "地址", "信箱", "日期",
    "項目", "金額", "合計", "耶和華", "貧窮", "偏鄉", "弱勢",
    "需要", "紙本", "電子", "身份證", "手機", "平台"
]


def classify_text_block(block: TextBlock) -> str:
    """
    分類文字區塊為手寫或印刷

    返回: "handwritten" 或 "printed"
    """
    text = block.text.strip()
    confidence = block.confidence

    # 空白文字
    if not text:
        return "printed"

    # 1. 完全匹配印刷標籤
    if text in PRINTED_LABELS:
        return "printed"

    # 2. 包含印刷關鍵字
    for keyword in PRINTED_KEYWORDS:
        if keyword in text:
            return "printed"

    # 3. 信心度判斷
    # Google Vision 對印刷字體通常有較高信心度 (> 0.95)
    # 手寫字體信心度通常較低 (< 0.90)
    if confidence < 0.85:
        return "handwritten"

    # 4. 文字特徵判斷
    # 純數字（可能是金額）- 通常是手寫
    digits_only = text.replace(",", "").replace(".", "").replace(" ", "")
    if digits_only.isdigit() and len(digits_only) >= 3:
        return "handwritten"

    # 2-4 個中文字且不是標籤（可能是姓名）- 通常是手寫
    if 2 <= len(text) <= 4:
        is_all_chinese = all('\u4e00' <= c <= '\u9fff' for c in text)
        if is_all_chinese and text not in PRINTED_LABELS:
            # 檢查是否包含印刷關鍵字
            has_keyword = any(kw in text for kw in PRINTED_KEYWORDS)
            if not has_keyword:
                return "handwritten"

    # 身份證格式 - 手寫
    if len(text) == 10 and text[0].isalpha() and text[1:].replace(" ", "").isdigit():
        return "handwritten"

    # 電話格式 - 手寫
    phone_digits = text.replace("-", "").replace(" ", "")
    if phone_digits.startswith("09") and len(phone_digits) == 10:
        return "handwritten"

    # 5. 區塊大小判斷（較小的區塊可能是手寫數字）
    _, _, w, h = block.bbox
    if w < 50 and h < 50 and text.isdigit():
        return "handwritten"

    # 預設為印刷
    return "printed"


def draw_classified_boxes(
    image: np.ndarray,
    text_blocks: List[TextBlock],
    printed_color: Tuple[int, int, int] = (255, 0, 0),   # BGR: 藍色
    handwritten_color: Tuple[int, int, int] = (0, 0, 255),  # BGR: 紅色
    thickness: int = 2
) -> Tuple[np.ndarray, Dict[str, List[TextBlock]]]:
    """
    在圖片上繪製分類後的框：印刷字藍框，手寫字紅框

    Returns:
        (標記後的圖片, {"printed": [...], "handwritten": [...]})
    """
    logger.info(f"開始分類並繪製 {len(text_blocks)} 個文字區塊...")

    result = image.copy()
    classified = {"printed": [], "handwritten": []}

    for block in text_blocks:
        classification = classify_text_block(block)
        classified[classification].append(block)

        # 選擇顏色
        color = printed_color if classification == "printed" else handwritten_color

        if block.vertices and len(block.vertices) >= 4:
            pts = np.array(block.vertices, np.int32)
            pts = pts.reshape((-1, 1, 2))
            cv2.polylines(result, [pts], True, color, thickness)
        else:
            x, y, w, h = block.bbox
            cv2.rectangle(result, (x, y), (x + w, y + h), color, thickness)

    logger.info(f"分類結果: 印刷={len(classified['printed'])}, 手寫={len(classified['handwritten'])}")

    return result, classified


def is_likely_handwritten(text: str) -> bool:
    """判斷文字是否可能是手寫內容"""
    text = text.strip()

    # 空白或太短
    if len(text) < 1:
        return False

    # 完全匹配印刷標籤
    if text in PRINTED_LABELS:
        return False

    # 部分匹配印刷標籤
    for label in PRINTED_LABELS:
        if text == label or label == text:
            return False
        # 如果文字是標籤的子串且長度接近，可能是印刷
        if len(text) >= 2 and text in label and len(text) / len(label) > 0.7:
            return False

    # 純數字（可能是金額或日期）
    if text.replace(",", "").replace(".", "").isdigit():
        return True

    # 2-4 個中文字（可能是姓名）
    if len(text) >= 2 and len(text) <= 4:
        if all('\u4e00' <= c <= '\u9fff' for c in text):
            return True

    # 身份證格式
    if len(text) == 10 and text[0].isalpha() and text[1:].isdigit():
        return True

    # 電話格式
    if text.startswith("09") and len(text.replace("-", "").replace(" ", "")) == 10:
        return True

    # 其他：如果不是印刷標籤，可能是手寫
    return True


def get_label_for_position(
    y_ratio: float,
    x_ratio: float,
    image_height: int,
    image_width: int
) -> str:
    """根據位置推測標籤名稱"""
    # 奉獻袋表單的大致位置分布
    # 上半部：項目和金額
    # 中間：日期、收據
    # 下半部：姓名、電話、身份證、地址

    if y_ratio < 0.3:
        return "金額"
    elif y_ratio < 0.45:
        return "日期"
    elif y_ratio < 0.55:
        return "收據"
    elif y_ratio < 0.7:
        if x_ratio < 0.5:
            return "姓名"
        else:
            return "電話"
    elif y_ratio < 0.8:
        return "身份證"
    else:
        return "其他"


def draw_annotated_result(
    image: np.ndarray,
    grouped_lines: List[List[TextBlock]],
    image_height: int,
    image_width: int
) -> np.ndarray:
    """
    繪製標註結果：在手寫內容左側添加標籤
    """
    logger.info("繪製標註結果...")

    result = image.copy()

    # 字體設定
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.6
    font_thickness = 2
    label_color = (0, 128, 0)  # 綠色
    handwritten_color = (255, 0, 0)  # 藍色（BGR）
    box_color = (0, 0, 255)  # 紅色

    for line_idx, line in enumerate(grouped_lines):
        for block in line:
            text = block.text.strip()
            if not text:
                continue

            x, y, w, h = block.bbox
            y_ratio = y / image_height
            x_ratio = x / image_width

            # 判斷是否為手寫內容
            is_handwritten = is_likely_handwritten(text)

            if is_handwritten:
                # 繪製紅框
                cv2.rectangle(result, (x, y), (x + w, y + h), box_color, 2)

                # 推測標籤
                label = get_label_for_position(y_ratio, x_ratio, image_height, image_width)

                # 在左側繪製標籤
                label_text = f"[{label}]"

                # 取得標籤文字大小
                (label_w, label_h), baseline = cv2.getTextSize(
                    label_text, font, font_scale, font_thickness
                )

                # 標籤位置（在區塊左側）
                label_x = max(5, x - label_w - 10)
                label_y = y + h // 2 + label_h // 2

                # 繪製標籤背景
                cv2.rectangle(
                    result,
                    (label_x - 2, label_y - label_h - 2),
                    (label_x + label_w + 2, label_y + 2),
                    (255, 255, 255),
                    -1
                )

                # 繪製標籤文字
                cv2.putText(
                    result, label_text,
                    (label_x, label_y),
                    font, font_scale, label_color, font_thickness
                )

                # 在區塊下方顯示辨識內容
                content_y = y + h + 20
                cv2.putText(
                    result, text,
                    (x, content_y),
                    font, font_scale, handwritten_color, font_thickness
                )

    return result


async def preprocess_offering_envelope(
    image_path: str,
    output_dir: str,
    ocr_function: callable
) -> PreprocessResult:
    """
    奉獻袋圖片預處理主流程

    Args:
        image_path: 原始圖片路徑
        output_dir: 輸出目錄
        ocr_function: OCR 函數，接收圖片路徑，返回 TextBlock 列表

    Returns:
        PreprocessResult: 預處理結果
    """
    logger.info(f"開始預處理奉獻袋圖片: {image_path}")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 讀取原始圖片
    original = cv2.imread(image_path)
    if original is None:
        raise ValueError(f"無法讀取圖片: {image_path}")

    height, width = original.shape[:2]
    logger.info(f"圖片尺寸: {width}x{height}")

    # 步驟 1: 去除橘色像素
    logger.info("步驟 1: 去除橘色像素")
    orange_removed = remove_orange_pixels_fast(original)
    orange_removed_path = output_path / "page_0002.png"
    cv2.imwrite(str(orange_removed_path), orange_removed)
    logger.info(f"已儲存: {orange_removed_path}")

    # 步驟 2: 加深藍/黑色並提高對比度
    logger.info("步驟 2: 加深藍/黑色並提高對比度")
    contrast_enhanced = enhance_blue_black_contrast(orange_removed)
    contrast_enhanced_path = output_path / "page_0003.png"
    cv2.imwrite(str(contrast_enhanced_path), contrast_enhanced)
    logger.info(f"已儲存: {contrast_enhanced_path}")

    # 步驟 3: OCR 辨識並標記紅框
    logger.info("步驟 3: OCR 辨識並標記紅框")
    text_blocks = await ocr_function(str(contrast_enhanced_path))
    ocr_boxes_image = draw_ocr_boxes(contrast_enhanced, text_blocks)
    ocr_boxes_path = output_path / "page_0004.png"
    cv2.imwrite(str(ocr_boxes_path), ocr_boxes_image)
    logger.info(f"已儲存: {ocr_boxes_path}")

    # 步驟 4: 依 Y 軸分組並標註手寫內容
    logger.info("步驟 4: 依 Y 軸分組並標註手寫內容")
    grouped_lines = group_blocks_by_y(text_blocks)
    annotated_image = draw_annotated_result(
        contrast_enhanced, grouped_lines, height, width
    )
    annotated_path = output_path / "page_0005.png"
    cv2.imwrite(str(annotated_path), annotated_image)
    logger.info(f"已儲存: {annotated_path}")

    logger.info("預處理完成")

    return PreprocessResult(
        original_path=image_path,
        orange_removed_path=str(orange_removed_path),
        contrast_enhanced_path=str(contrast_enhanced_path),
        ocr_boxes_path=str(ocr_boxes_path),
        annotated_path=str(annotated_path),
        text_blocks=text_blocks,
        grouped_lines=grouped_lines,
    )
