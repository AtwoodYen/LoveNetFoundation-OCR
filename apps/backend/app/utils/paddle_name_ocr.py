"""
PaddleOCR 中文姓名專用 OCR 模組

針對奉獻袋手寫「奉獻者姓名」欄位，做字元級高精度辨識：
  1. 依 donation_rules 找到的姓名 block indices，計算姓名區域 bbox
  2. 裁切圖片（含容錯邊距）
  3. 用 PaddleOCR TextRecognition 對裁切圖做 OCR
  4. 用中文常用姓名字元集過濾，排除明顯辨識錯誤
  5. 回傳修正後的姓名字串

座標系對應（與 google_vision_flow.py 一致）：
  block["x"]      = Vision_y  = 圖片垂直位置（上→下）
  block["y"]      = Vision_x  = 圖片水平位置（左→右）
  block["width"]  = Vision_h  = 垂直方向大小
  block["height"] = Vision_w  = 水平方向大小

原始圖片 bbox [left, top, right, bottom]：
  left   = block["y"]
  top    = block["x"]
  right  = block["y"] + block["height"]
  bottom = block["x"] + block["width"]
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logger import logger

# PaddleOCR 源碼路徑
_PADDLE_SRC = str(
    (Path(__file__).resolve().parents[4] / "PaddleOCR-main").resolve()
)

# ──────────────────────────────────────────────────────────────────────────────
# 中文常用姓名字元集
# ──────────────────────────────────────────────────────────────────────────────

# 台灣常用姓氏（前 200 姓）
_COMMON_SURNAMES = set(
    "李王張陳林劉楊黃吳趙周徐孫馬朱胡郭何高林羅鄭梁謝唐許韓馮董程柳蔡"
    "袁盧丁魏蘇潘江呂薛葉閻余潘杜戴蔣鐘汪田任姜範方石姚譚廖鄒熊金陸郝"
    "孔白崔康毛邱秦江史顧侯邵孟龍萬段漳雷錢湯尹黎易常武喬賀賴龔文蒲文"
    "施尤邱曾游傅巫呂歐顏莊曹鍾洪簡古詹藍湯魏翁連韓嚴童明賴范章蕭孔龐"
    "溫嚴沈宋包丁許夏魯伍溫吳甘丘裴況牛習申管盛歐陽諸葛司馬"
)

# 中文常用名字字元（涵蓋常見名字用字）
_COMMON_NAME_CHARS = set(
    # 自然美好類
    "明亮光華美麗芳花草木山水雲天星月日風雨雪霞晨曦輝耀照燦燦爛彩"
    # 品德類
    "仁義禮智信忠孝廉恕寬德賢善良正直誠真純淳雅文武才智慧賢"
    # 力量類
    "強健壯勇武豪俊傑英雄宏偉大剛毅勤奮勇力進取"
    # 長輩期望類
    "福祿壽喜吉祥瑞慶康安平和寧靜穩"
    # 常見名字字
    "志遠明建國民興偉峰濤博學文軍凱旭超龍飛"
    "淑惠玲珍美慧婷婉秀娟芝菊蓮萍雅詩如涵欣"
    "俊宇浩然成佳佩信宸嘉琦琳甄嫚曼"
    # 附加常用字
    "弘立哲謙敏聰靈昱昇昭晴晶晶珊珮珠玫瑰桂椿梅柔柳秋冬春夏"
    "千萬億兆同同協洽和睦親情愛心念思意願望念祈禱"
    "以之乃其若而此彼何如即則故因此所以但卻然雖雖雖"
    # 常見字（補充）
    "翰軒豪毅凌翼展鵬鴻飛翔羽振奮向上進取揚帆起航"
    "艷嬌嬌妍柔溫婉善賢淑貞靜優雅莊重端莊秀麗"
    "德全純誠忠義仁孝敬禮廉恥"
)

# 標點和非姓名字元（應排除）
_NON_NAME_CHARS = set("0123456789０１２３４５６７８９ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz：:;；,，.。!！?？@#$%^&*()（）[]【】{}「」『』<>《》/\\|_-+=~`")

# 最大合理姓名長度（含姓 1-2 字 + 名 1-3 字）
_MAX_NAME_LEN = 6
_MIN_NAME_LEN = 2


# ──────────────────────────────────────────────────────────────────────────────
# 模型懶載入
# ──────────────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_ocr_pipeline = None
_load_failed = False


def _get_pipeline():
    """取得（並快取）PaddleOCR TextRecognition pipeline。"""
    global _ocr_pipeline, _load_failed

    if _load_failed:
        return None
    if _ocr_pipeline is not None:
        return _ocr_pipeline

    with _lock:
        if _load_failed:
            return None
        if _ocr_pipeline is not None:
            return _ocr_pipeline

        try:
            if _PADDLE_SRC not in sys.path:
                sys.path.insert(0, _PADDLE_SRC)

            # 關閉 paddlex 的 model source connectivity check 加速啟動
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

            from paddleocr import TextRecognition  # noqa: PLC0415

            logger.info("[PaddleNameOCR] 載入 TextRecognition 模型...")
            pipeline = TextRecognition()
            _ocr_pipeline = pipeline
            logger.info("[PaddleNameOCR] 模型載入成功")
            return _ocr_pipeline

        except ImportError as e:
            logger.warning("[PaddleNameOCR] PaddleOCR 未安裝（%s）；跳過姓名 re-OCR", e)
        except Exception as e:
            logger.warning("[PaddleNameOCR] 模型載入失敗：%s；跳過姓名 re-OCR", e)

        _load_failed = True
        return None


# ──────────────────────────────────────────────────────────────────────────────
# 座標計算工具
# ──────────────────────────────────────────────────────────────────────────────

def _blocks_to_image_bbox(
    blocks: List[Dict[str, Any]],
    padding: int = 20,
    image_w: Optional[int] = None,
    image_h: Optional[int] = None,
) -> Optional[Tuple[int, int, int, int]]:
    """
    從 Vision API blocks（已互換座標）計算原始圖片 bbox。

    Returns:
        (left, top, right, bottom) 或 None
    """
    if not blocks:
        return None

    lefts, tops, rights, bottoms = [], [], [], []
    for b in blocks:
        stored_x = b.get("x", 0)      # Vision_y = 垂直
        stored_y = b.get("y", 0)      # Vision_x = 水平
        stored_w = b.get("width", 0)  # Vision_h = 垂直大小
        stored_h = b.get("height", 0) # Vision_w = 水平大小
        left   = stored_y
        top    = stored_x
        right  = stored_y + stored_h
        bottom = stored_x + stored_w
        lefts.append(left)
        tops.append(top)
        rights.append(right)
        bottoms.append(bottom)

    x1 = max(0, min(lefts) - padding)
    y1 = max(0, min(tops) - padding)
    x2 = min(image_w, max(rights) + padding) if image_w else max(rights) + padding
    y2 = min(image_h, max(bottoms) + padding) if image_h else max(bottoms) + padding

    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


# ──────────────────────────────────────────────────────────────────────────────
# 中文姓名過濾
# ──────────────────────────────────────────────────────────────────────────────

def _is_valid_name_char(ch: str) -> bool:
    """判斷單一字元是否為合法姓名字元（漢字）。"""
    if ch in _NON_NAME_CHARS:
        return False
    code = ord(ch)
    # CJK Unified Ideographs: 4E00-9FFF（主要漢字區）
    # CJK Extension A: 3400-4DBF
    return (0x4E00 <= code <= 0x9FFF) or (0x3400 <= code <= 0x4DBF)


def _clean_and_filter_name(raw: str) -> str:
    """
    清理並過濾 PaddleOCR 辨識結果，保留合法的中文姓名字元。

    處理：
    - 移除所有非漢字字元（數字、英文、標點）
    - 若長度超過 _MAX_NAME_LEN，截取最可能的部分
    - 若結果不足 _MIN_NAME_LEN，回傳空字串
    """
    # 僅保留合法漢字
    filtered = "".join(ch for ch in raw if _is_valid_name_char(ch))

    if len(filtered) < _MIN_NAME_LEN:
        return ""

    # 若過長，截取到合理上限
    if len(filtered) > _MAX_NAME_LEN:
        filtered = filtered[:_MAX_NAME_LEN]

    return filtered


def _score_name_candidate(name: str) -> float:
    """
    對姓名候選字串評分（越高越可能是真實姓名）。
    評分依據：
    - 第一個字是否為常見姓氏（+2）
    - 其餘字是否為常見名字字元（每字 +1）
    - 長度適當（2-4 字加分）
    """
    if not name:
        return 0.0
    score = 0.0
    if name[0] in _COMMON_SURNAMES:
        score += 2.0
    for ch in name[1:]:
        if _is_valid_name_char(ch):
            score += 1.0
        if ch in _COMMON_NAME_CHARS:
            score += 0.5
    # 長度 2-4 加分
    if 2 <= len(name) <= 4:
        score += 1.0
    return score


# ──────────────────────────────────────────────────────────────────────────────
# 主要公開 API
# ──────────────────────────────────────────────────────────────────────────────

def reocr_donor_name(
    image_path: str,
    all_blocks: List[Dict[str, Any]],
    donor_name_info: Dict[str, Any],
    padding: int = 30,
) -> Optional[str]:
    """
    用 PaddleOCR 對奉獻者姓名區域做 re-OCR。

    Args:
        image_path:       已預處理的圖片路徑（去橘色後）
        all_blocks:       全部 Vision API blocks（已互換座標）
        donor_name_info:  donation_rules 回傳的 donate_no["Donor_Name"] dict
                          需含 "block_indices" 和 "label_index"
        padding:          裁切區域的邊距像素

    Returns:
        PaddleOCR 辨識並過濾後的姓名字串；
        若模型不可用、辨識失敗或結果不合格則回傳 None（呼叫端保留原始結果）
    """
    pipeline = _get_pipeline()
    if pipeline is None:
        return None

    if not donor_name_info:
        return None

    try:
        from PIL import Image  # noqa: PLC0415

        img = Image.open(image_path).convert("RGB")
        img_w, img_h = img.size

        # 取得姓名 blocks
        block_indices: List[int] = donor_name_info.get("block_indices", [])
        label_index: int = donor_name_info.get("label_index", -1)
        index_to_block = {b["index"]: b for b in all_blocks}

        name_blocks = [index_to_block[i] for i in block_indices if i in index_to_block]

        # 若 block_indices 為空（e.g. 未找到任何姓名塊），用標籤周圍估算
        if not name_blocks and label_index >= 0:
            label_block = index_to_block.get(label_index)
            if label_block:
                name_blocks = [label_block]
                logger.info("[PaddleNameOCR] 姓名 blocks 為空，改用標籤 block 估算裁切範圍")

        if not name_blocks:
            logger.info("[PaddleNameOCR] 無法取得有效 blocks，跳過 re-OCR")
            return None

        # 計算 bbox
        bbox = _blocks_to_image_bbox(name_blocks, padding=padding, image_w=img_w, image_h=img_h)
        if bbox is None:
            logger.info("[PaddleNameOCR] 無法計算有效 bbox，跳過 re-OCR")
            return None

        left, top, right, bottom = bbox
        logger.info(
            "[PaddleNameOCR] 裁切姓名區域: left=%d top=%d right=%d bottom=%d (原圖 %dx%d)",
            left, top, right, bottom, img_w, img_h,
        )

        # 裁切圖片並放大（小圖 OCR 效果差）
        crop = img.crop((left, top, right, bottom))
        crop_w, crop_h = crop.size

        # 若裁切圖太小，放大以提升辨識率（目標最小 50px 高）
        min_height = 50
        if crop_h < min_height:
            scale = min_height / crop_h
            new_w = max(int(crop_w * scale), 1)
            new_h = max(int(crop_h * scale), 1)
            crop = crop.resize((new_w, new_h), Image.LANCZOS)
            logger.debug("[PaddleNameOCR] 放大裁切圖: %dx%d → %dx%d", crop_w, crop_h, new_w, new_h)

        # 存到臨時檔再傳給 PaddleOCR（部分 API 不接受 PIL Image）
        import tempfile  # noqa: PLC0415
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        crop.save(tmp_path)

        try:
            result = pipeline.predict(tmp_path)
            raw_texts: List[str] = []
            for batch in result:
                for item in (batch if isinstance(batch, list) else [batch]):
                    rec_text = None
                    if hasattr(item, "rec_text"):
                        rec_text = item.rec_text
                    elif isinstance(item, dict):
                        rec_text = item.get("rec_text") or item.get("text")
                    if rec_text:
                        raw_texts.append(str(rec_text))

            raw = "".join(raw_texts)
            logger.info("[PaddleNameOCR] 原始辨識結果: %r", raw)

            candidate = _clean_and_filter_name(raw)
            if not candidate:
                logger.info("[PaddleNameOCR] 過濾後為空，放棄使用 PaddleOCR 結果")
                return None

            score = _score_name_candidate(candidate)
            logger.info("[PaddleNameOCR] 過濾後候選姓名: %r（score=%.1f）", candidate, score)

            # 低分（可能是完全無關內容）則不採用
            if score < 1.0:
                logger.info("[PaddleNameOCR] 評分過低（%.1f < 1.0），不採用", score)
                return None

            return candidate

        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    except Exception as e:
        logger.warning("[PaddleNameOCR] re-OCR 失敗：%s，保留規則引擎結果", e)
        return None
