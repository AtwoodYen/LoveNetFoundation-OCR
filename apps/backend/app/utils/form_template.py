"""
依表單範本裁切「手寫區」，略過固定印刷區（如奉獻袋表頭）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from app.utils.config import settings
from app.utils.ink_color_filter import filter_blue_black_ink, should_apply_ink_filter
from app.utils.logger import logger


def _templates_dir() -> Path:
    return Path(settings.ASSETS_DIR) / "form_templates"


def load_template(template_id: str) -> Dict[str, Any]:
    path = _templates_dir() / f"{template_id}.json"
    if not path.is_file():
        alt = _templates_dir() / f"{template_id.replace('-', '_')}.json"
        path = alt if alt.is_file() else path
    if not path.is_file():
        raise FileNotFoundError(f"找不到表單範本設定: {template_id}（預期 {path}）")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def regions_for_page(spec: Dict[str, Any], page_index: int) -> List[Dict[str, float]]:
    pages = spec.get("pages")
    if isinstance(pages, list) and page_index < len(pages):
        reg = pages[page_index].get("handwriting_regions") or pages[page_index].get(
            "regions"
        )
        if reg:
            return reg
    default = spec.get("default_page_regions") or spec.get("handwriting_regions")
    if not default:
        raise ValueError(f"範本 {spec.get('id')} 未設定 handwriting_regions / default_page_regions")
    return default


def _norm_box(
    r: Dict[str, Any], width: int, height: int
) -> Tuple[int, int, int, int]:
    x0 = int(float(r["x0"]) * width)
    y0 = int(float(r["y0"]) * height)
    x1 = int(float(r["x1"]) * width)
    y1 = int(float(r["y1"]) * height)
    x0, x1 = max(0, min(x0, x1)), min(width, max(x0, x1))
    y0, y1 = max(0, min(y0, y1)), min(height, max(y0, y1))
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"無效的裁切框: {r}")
    return x0, y0, x1, y1


def composite_handwriting_crops(
    page_image_path: str,
    regions: List[Dict[str, Any]],
    output_dir: Path,
    page_idx: int,
    padding: int = 12,
    ink_filter_spec: Optional[Dict[str, Any]] = None,
) -> str:
    """
    將多個手寫區垂直拼成一張圖，維持「一頁一檔」給後續 OCR。
    ink_filter_spec 非 None 時僅保留藍／黑筆跡（黑字白底）。
    """
    with Image.open(page_image_path) as im:
        img = im.convert("RGB")
        w, h = img.size
        crops: List[Image.Image] = []
        for r in regions:
            box = _norm_box(r, w, h)
            piece = img.crop(box)
            if ink_filter_spec is not None:
                piece = filter_blue_black_ink(piece, ink_filter_spec)
            crops.append(piece)

    if not crops:
        raise ValueError("沒有任何裁切區")

    max_w = max(c.width for c in crops)
    total_h = sum(c.height for c in crops) + padding * (len(crops) - 1)
    canvas = Image.new("RGB", (max_w, total_h), (255, 255, 255))
    y = 0
    for c in crops:
        canvas.paste(c, (0, y))
        y += c.height + padding

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"handwriting_only_{page_idx:04d}.png"
    canvas.save(out_path, "PNG")
    return str(out_path)


def apply_form_template_to_pdf_result(
    pdf_result: Dict[str, Any],
    template_id: str,
    task_output_dir: str,
) -> Dict[str, Any]:
    spec = load_template(template_id)
    original_files: List[str] = pdf_result.get("output_files") or []
    if not original_files:
        return pdf_result

    ink_cfg = should_apply_ink_filter(spec)
    crop_dir = Path(task_output_dir) / "handwriting_crops"
    new_files: List[str] = []
    for i, img_path in enumerate(original_files):
        regions = regions_for_page(spec, i)
        try:
            cropped = composite_handwriting_crops(
                img_path,
                regions,
                crop_dir,
                i + 1,
                ink_filter_spec=ink_cfg,
            )
            new_files.append(cropped)
            logger.info(
                f"表單範本 {template_id}: 第 {i + 1} 頁裁切為手寫區 {len(regions)} 塊 → {cropped}"
            )
        except Exception as e:
            logger.warning(
                f"表單範本裁切失敗（第 {i + 1} 頁），改用原圖: {e}"
            )
            new_files.append(img_path)

    out = dict(pdf_result)
    out["output_files"] = new_files
    out["page_count"] = len(new_files)
    meta = dict(out.get("metadata") or {})
    meta["form_template"] = template_id
    meta["form_template_label"] = spec.get("label", template_id)
    if ink_cfg is not None:
        meta["ink_filter"] = "blue_black"
    out["metadata"] = meta
    return out
