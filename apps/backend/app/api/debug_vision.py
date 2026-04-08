"""
除錯用：Google Vision 區塊與全文預覽（需 GOOGLE_VISION_API_KEY）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, status

from app.core.task_manager import get_task_manager
from app.utils.config import settings
from app.utils.google_vision_ocr import GoogleVisionOCRError, ocr_with_google_vision
from app.utils.logger import logger

router = APIRouter(prefix="/debug", tags=["debug"])


def _resolve_task_preview_image(task_id: str, task_info: Dict[str, Any]) -> Path:
    """優先上傳的圖檔，其次轉頁圖、Vision 規範檔名。"""
    base = Path(settings.OUTPUT_DIR).resolve() / task_id
    ordered: List[Path] = []
    seen: set[Path] = set()

    fp = task_info.get("file_path")
    if fp:
        p = Path(fp).resolve()
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            ordered.append(p)
            seen.add(p)

    page = (base / "images" / "page_0001.png").resolve()
    if page.is_file() and page not in seen:
        ordered.append(page)
        seen.add(page)

    for pattern in ("ocr_*_GoogleVision_*.png", "ocr_*SliceGV*.png", "ocr_*.png"):
        for f in sorted(
            base.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            r = f.resolve()
            if r.is_file() and r not in seen:
                ordered.append(r)
                seen.add(r)

    if ordered:
        return ordered[0]
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="任務目錄內找不到可用的預覽圖片",
    )


@router.get("/vision-ocr/{task_id}")
async def vision_ocr_overlay_data(task_id: str) -> Dict[str, Any]:
    """
    對任務對應的頁面圖執行 Google Vision DOCUMENT_TEXT_DETECTION，
    回傳全文與 textAnnotations 區塊（供預覽頁繪框）。
    """
    task_manager = get_task_manager()
    task_info = await task_manager.get_task_status(task_id)
    # debug/preview 用途：即使 task_manager 沒有狀態（例如重啟後記憶體狀態消失），
    # 只要 OUTPUT_DIR/task_id 目錄存在且可解析出預覽圖，也允許直接查看。
    if not task_info:
        base = (Path(settings.OUTPUT_DIR).resolve() / task_id).resolve()
        if not base.is_dir():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task not found: {task_id}",
            )
        task_info = {}

    image_path = _resolve_task_preview_image(task_id, task_info)
    try:
        result = await ocr_with_google_vision(str(image_path))
    except GoogleVisionOCRError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        ) from e

    blocks: List[Dict[str, Any]] = []
    for i, b in enumerate(result.text_blocks):
        x, y, w, h = b.bbox
        blocks.append(
            {
                "index": i,
                "text": b.text or "",
                "x": int(x),
                "y": int(y),
                "width": int(w),
                "height": int(h),
                "confidence": float(b.confidence),
            }
        )

    path_q = quote(str(image_path.resolve()), safe="")
    image_url = f"/api/v1/tasks/file?path={path_q}"

    segment_texts = [b["text"] for b in blocks]
    segments_bracketed = "\n".join(f"[{t}]" for t in segment_texts)

    return {
        "task_id": task_id,
        "image_path": str(image_path),
        "image_url": image_url,
        "full_text": result.text or "",
        "char_count": len(result.text or ""),
        "block_count": len(blocks),
        "blocks": blocks,
        "segment_texts": segment_texts,
        "segments_bracketed": segments_bracketed,
        "ordering_note": (
            "full_text 來自 fullTextAnnotation：依頁面→區塊→段落→詞的樹狀走訪與版面閱讀順序，"
            "與紙本「由上而下」的視覺順序可能不一致（多欄、核取方塊、區塊偵測順序都會影響）。"
            "segments_bracketed 依 textAnnotations[1:] 陣列順序（每個小框一筆），"
            "通常接近閱讀順序，但與 full_text 的斷行／合併方式不一定相同。"
        ),
        "coordinate_note": (
            "blocks 內 x, y, width, height 為預覽圖像素座標（原點左上，與 Vision textAnnotations 一致）。"
            "靜態預覽頁將整張圖以「信封中心」為軸順時針旋轉 180° 顯示；"
            "紅框在像素座標繞同一中心順時針旋轉 90°（取 AABB）後，再乘上縮放比例繪製。"
            "信封中心可用亮色區域估算（失敗則回退置中 1:2 內接矩形中心），亦可於預覽頁點擊校正。"
        ),
    }
