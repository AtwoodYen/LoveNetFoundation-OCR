"""
PP-DocLayoutV3 版面分析模組

使用 Repo 內已存在的 models/PP-DocLayoutV3_safetensors 模型，
對奉獻袋圖片進行版面偵測，回傳有序排列的版面區域（LayoutRegion）。

設計原則：
- 懶載入（首次呼叫時才載入模型，之後快取）
- 靜默降級（若 transformers/torch 未安裝或模型載入失敗，回傳 None）
- 不依賴 GPU，純 CPU 推論

座標系說明（與 Google Vision API 一致）：
  PP-DocLayoutV3 輸出的 bbox 為標準圖片座標：
    x = 水平（左→右），y = 垂直（上→下）
  bbox 格式：[x_min, y_min, x_max, y_max]（絕對像素）
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from app.utils.config import settings
from app.utils.logger import logger

# ──────────────────────────────────────────────────────────────────────────────
# 資料型別
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LayoutRegion:
    """PP-DocLayoutV3 偵測到的版面區域"""
    label: str                          # e.g. "text", "table", "image", ...
    score: float                        # 信心分數 0~1
    bbox: Tuple[int, int, int, int]     # (x_min, y_min, x_max, y_max) 絕對像素
    region_index: int                   # 依閱讀順序排序後的序號（0-based）

    @property
    def x_center(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0

    @property
    def y_center(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2.0

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    def contains_point(self, px: float, py: float, margin: int = 0) -> bool:
        """判斷點 (px, py) 是否在此區域內（可設定容錯邊距）"""
        x1, y1, x2, y2 = self.bbox
        return (x1 - margin) <= px <= (x2 + margin) and (y1 - margin) <= py <= (y2 + margin)


# ──────────────────────────────────────────────────────────────────────────────
# 模型懶載入（執行緒安全）
# ──────────────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_model = None
_processor = None
_load_failed = False   # 曾經載入失敗就不再重試


def _get_model_and_processor():
    """
    取得（並快取）PP-DocLayoutV3 模型與 ImageProcessor。
    載入失敗時回傳 (None, None)，且之後不再重試。
    """
    global _model, _processor, _load_failed

    if _load_failed:
        return None, None
    if _model is not None and _processor is not None:
        return _model, _processor

    with _lock:
        if _load_failed:
            return None, None
        if _model is not None and _processor is not None:
            return _model, _processor

        model_dir = settings.PP_LAYOUT_MODEL_DIR
        if not model_dir:
            logger.info("[PP-Layout] PP_LAYOUT_MODEL_DIR 未設定，跳過版面分析")
            _load_failed = True
            return None, None

        if not Path(model_dir).exists():
            logger.warning("[PP-Layout] 模型目錄不存在：%s，跳過版面分析", model_dir)
            _load_failed = True
            return None, None

        try:
            import torch
            from transformers import AutoImageProcessor, AutoModelForObjectDetection

            logger.info("[PP-Layout] 載入模型：%s", model_dir)
            proc = AutoImageProcessor.from_pretrained(model_dir)
            mdl = AutoModelForObjectDetection.from_pretrained(
                model_dir,
                torch_dtype=torch.float32,
            )
            mdl.eval()
            _processor = proc
            _model = mdl
            logger.info("[PP-Layout] 模型載入成功（%s）", type(mdl).__name__)
            return _model, _processor

        except ImportError as e:
            logger.warning("[PP-Layout] 缺少依賴（%s）；安裝 transformers+torch 後可啟用版面分析", e)
        except Exception as e:
            logger.warning("[PP-Layout] 模型載入失敗：%s，跳過版面分析", e)

        _load_failed = True
        return None, None


# ──────────────────────────────────────────────────────────────────────────────
# 閱讀順序排序
# ──────────────────────────────────────────────────────────────────────────────

def _sort_regions_reading_order(
    regions: List[LayoutRegion],
    row_tolerance: int = 60,
) -> List[LayoutRegion]:
    """
    將偵測到的版面區域按閱讀順序排序。

    奉獻袋表單為直式（Portrait），閱讀順序：
      由上而下（y_center 升序）→ 同一行內由右而左（x_center 降序，中文慣例）
    
    row_tolerance: 判定為「同一行」的 y 軸容差像素
    """
    if not regions:
        return []

    # 依 y_center 排序後，將 y 相近的 region 歸為同一行
    sorted_by_y = sorted(regions, key=lambda r: r.y_center)
    rows: List[List[LayoutRegion]] = []
    current_row: List[LayoutRegion] = []
    ref_y: Optional[float] = None

    for r in sorted_by_y:
        if ref_y is None or abs(r.y_center - ref_y) <= row_tolerance:
            current_row.append(r)
            ref_y = r.y_center if ref_y is None else (ref_y + r.y_center) / 2
        else:
            rows.append(current_row)
            current_row = [r]
            ref_y = r.y_center

    if current_row:
        rows.append(current_row)

    # 同一行內按 x_center 降序（右→左）
    ordered: List[LayoutRegion] = []
    for row in rows:
        row.sort(key=lambda r: -r.x_center)
        ordered.extend(row)

    # 補上 region_index
    for idx, r in enumerate(ordered):
        r.region_index = idx

    return ordered


# ──────────────────────────────────────────────────────────────────────────────
# 公開 API
# ──────────────────────────────────────────────────────────────────────────────

def detect_layout(
    image_path: str,
    threshold: Optional[float] = None,
) -> Optional[List[LayoutRegion]]:
    """
    對圖片執行 PP-DocLayoutV3 版面偵測，回傳依閱讀順序排序的 LayoutRegion 列表。

    Args:
        image_path: 輸入圖片路徑
        threshold:  信心閾值，低於此值的 region 不採用（預設使用 settings.PP_LAYOUT_THRESHOLD）

    Returns:
        依閱讀順序排序的 LayoutRegion 列表；若模型不可用則回傳 None（呼叫端保留原始處理）
    """
    model, processor = _get_model_and_processor()
    if model is None or processor is None:
        return None

    if threshold is None:
        threshold = settings.PP_LAYOUT_THRESHOLD

    try:
        import torch
        from PIL import Image

        image = Image.open(image_path).convert("RGB")
        orig_w, orig_h = image.size

        inputs = processor(images=image, return_tensors="pt")
        with torch.no_grad():
            outputs = model(**inputs)

        results = processor.post_process_object_detection(
            outputs,
            threshold=threshold,
            target_sizes=[(orig_h, orig_w)],
        )

        raw_regions: List[LayoutRegion] = []
        page_result = results[0]

        for score, label_id, box in zip(
            page_result["scores"],
            page_result["labels"],
            page_result["boxes"],
        ):
            score_f = float(score)
            label_name = model.config.id2label.get(int(label_id), "text")
            x1, y1, x2, y2 = (int(v) for v in box)
            # 確保 bbox 不超出圖片邊界
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(orig_w, x2)
            y2 = min(orig_h, y2)
            raw_regions.append(
                LayoutRegion(
                    label=label_name,
                    score=score_f,
                    bbox=(x1, y1, x2, y2),
                    region_index=0,
                )
            )

        ordered = _sort_regions_reading_order(raw_regions)
        logger.info(
            "[PP-Layout] 偵測完成：%d 個 region（threshold=%.2f）",
            len(ordered),
            threshold,
        )
        for r in ordered:
            logger.debug(
                "[PP-Layout]   [%d] %-20s score=%.3f bbox=%s",
                r.region_index, r.label, r.score, r.bbox,
            )
        return ordered

    except Exception as e:
        logger.warning("[PP-Layout] 推論失敗：%s，降級使用全圖 OCR", e)
        return None
