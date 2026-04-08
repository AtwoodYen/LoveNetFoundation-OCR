"""
切圖辨識流程：愛盟奉獻袋四區裁切後再 OCR（GLM 或 Google Vision）。

錨點定位使用 Google Vision（須設定 GOOGLE_VISION_API_KEY）。
"""

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image

from app.core.flows.base import TaskProcessingFlow
from app.core.steps.pdf_to_image import PdfToImageStepInput, pdf_to_image
from app.core.ocr_client import LayoutAndOCRClient
from app.utils.google_vision_ocr import ocr_with_google_vision, GoogleVisionOCRError
from app.utils.lovenet_slice_crop import (
    SliceCropError,
    build_canonical_original_filename,
    compute_lovenet_slice_rects,
    save_slice_images,
    vision_blocks_for_slice,
)
from app.utils.json_safe import json_sanitize
from app.utils.logger import logger
from app.utils.offering_display import build_offering_display, should_apply_lovenet_offering_rules
from app.utils.config import settings


class SliceCropFlow(TaskProcessingFlow):
    """
    1) PDF/圖 → page_0001.png
    2) 複製為 ocr_* 規範檔名
    3) Vision 錨點 → Block_1~4.png
    4) 各區塊以 slice_engine 再辨識
    5) 合併為 Markdown + 可選奉獻袋摘要
    """

    STEP_WEIGHTS = {
        "pdf_to_image": 0.15,
        "slice_and_ocr": 0.75,
        "result_output": 0.10,
    }

    async def process(self) -> Dict[str, Any]:
        logger.info(f"[{self.context.task_id}] Starting slice_crop flow")
        output_dir = self._prepare_output_dir()
        self.context.set_output_dir(output_dir)

        cfg = self.context.ocr_config or {}
        slice_engine = str(cfg.get("slice_engine") or "google_vision").strip().lower()
        if slice_engine not in ("google_vision", "glm"):
            slice_engine = "google_vision"
        engine_label = "GoogleVision" if slice_engine == "google_vision" else "GLM"
        custom_url = cfg.get("custom_url")
        if isinstance(custom_url, str):
            custom_url = custom_url.strip() or None

        # --- 1. 轉頁圖 ---
        await self.update_progress("pdf_to_image", 0.0, 0.0, "轉換頁面圖片")
        pdf_result = await pdf_to_image(
            context=self.context,
            input=PdfToImageStepInput(
                file_path=self.context.file_path,
                output_dir=output_dir,
                dpi=cfg.get("dpi", 200),
                format=cfg.get("image_format", "png"),
            ),
            progress_callback=None,
        )
        pages = pdf_result.get("output_files") or []
        if not pages:
            raise ValueError("slice_crop：未取得頁面圖片")
        page_image = pages[0]
        self.context.set("pdf_result", pdf_result)
        self.context.metadata = pdf_result.get("metadata") or self.context.metadata

        await self.update_progress(
            "pdf_to_image",
            100.0,
            self.STEP_WEIGHTS["pdf_to_image"] * 100,
            "頁面圖就緒",
        )

        # --- 2. 規範檔名原始圖 ---
        ext = Path(page_image).suffix.lower().lstrip(".") or "png"
        canon_name = build_canonical_original_filename(engine_label, ext)
        canon_path = Path(output_dir) / canon_name
        shutil.copy2(page_image, canon_path)
        logger.info(f"[{self.context.task_id}] 原始圖規範存檔: {canon_path}")

        # --- 3. Vision 錨點 + 切圖 ---
        await self.update_progress(
            "slice_and_ocr",
            5.0,
            self.STEP_WEIGHTS["pdf_to_image"] * 100 + 5,
            "Google Vision 定位錨點",
        )
        try:
            blocks, vision_result = await vision_blocks_for_slice(str(canon_path))
        except GoogleVisionOCRError as e:
            raise RuntimeError(f"切圖辨識需要 Google Vision 定位錨點：{e}") from e

        with Image.open(canon_path) as im:
            iw, ih = im.size

        try:
            rects, slice_paths = compute_lovenet_slice_rects(
                blocks,
                iw,
                ih,
                raw_response=vision_result.raw_response,
                incremental_save=(str(canon_path), output_dir),
            )
        except SliceCropError as e:
            raise RuntimeError(str(e)) from e

        if not slice_paths:
            slice_paths = save_slice_images(str(canon_path), rects, output_dir)
        ordered_keys = ["Block_1.png", "Block_2.png", "Block_3.png", "Block_4.png"]

        # --- 4. 各區塊 OCR ---
        part_texts: List[str] = []
        block_details: List[Dict[str, Any]] = []
        for i, key in enumerate(ordered_keys, start=1):
            path = slice_paths[key]
            await self.update_progress(
                "slice_and_ocr",
                10.0 + (i / 4) * 80.0,
                (
                    self.STEP_WEIGHTS["pdf_to_image"]
                    + (0.05 + 0.75 * (i / 4)) * self.STEP_WEIGHTS["slice_and_ocr"]
                )
                * 100,
                f"辨識 Block_{i}",
            )
            if slice_engine == "google_vision":
                try:
                    r = await ocr_with_google_vision(path)
                    txt = (r.text or "").strip()
                except GoogleVisionOCRError as e:
                    txt = f"[Block_{i} Vision 錯誤: {e}]"
            else:
                cli = LayoutAndOCRClient()
                try:
                    sub = await cli.process_single_image(path, custom_url=custom_url)
                    pieces = []
                    for b in sub:
                        c = b.get("content")
                        if c and str(c).strip():
                            pieces.append(str(c).strip())
                    txt = "\n".join(pieces)
                except Exception as e:
                    txt = f"[Block_{i} GLM 錯誤: {e}]"
            part_texts.append(f"## 區塊 {i}\n\n{txt}")
            block_details.append(
                {"index": i, "image": key, "path": path, "text": txt}
            )

        merged_raw = "\n\n".join(part_texts)

        await self.update_progress(
            "result_output",
            0.0,
            (
                self.STEP_WEIGHTS["pdf_to_image"]
                + self.STEP_WEIGHTS["slice_and_ocr"]
            )
            * 100,
            "合併結果",
        )

        full_markdown = merged_raw
        offering_display = None
        ft = cfg.get("form_template")
        if should_apply_lovenet_offering_rules(merged_raw, form_template=ft):
            offering_display = build_offering_display(merged_raw)
            sm = offering_display.get("sanitized_markdown")
            if isinstance(sm, str) and sm.strip():
                full_markdown = sm.strip()

        md_path = Path(output_dir) / "result.md"
        md_path.write_text(
            "# OCR 結果（切圖辨識）\n\n" + full_markdown + "\n",
            encoding="utf-8",
        )

        result_data: Dict[str, Any] = {
            "task_id": self.context.task_id,
            "document_id": self.context.document_id,
            "processing_mode": "slice_crop",
            "slice_engine": slice_engine,
            "canonical_original": str(canon_path.resolve()),
            "block_images": {k: slice_paths[k] for k in ordered_keys},
            "block_ocr": block_details,
            "full_markdown": full_markdown,
            "merged_raw_markdown": merged_raw,
            "layout_anchor_engine": "google_vision",
        }
        if offering_display is not None:
            result_data["offering_display"] = offering_display

        json_path = Path(output_dir) / "result.json"
        json_path.write_text(
            json.dumps(json_sanitize(result_data), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        await self.update_progress("result_output", 100.0, 100.0, "完成")

        out_files = [
            str(md_path),
            str(json_path),
            str(canon_path),
        ] + [slice_paths[k] for k in ordered_keys]

        return {
            "success": True,
            "md_output_path": str(md_path),
            "json_output_path": str(json_path),
            "output_files": out_files,
            "metadata": {
                "ocr_engine": f"slice_crop/{slice_engine}",
                "canonical_original": str(canon_path),
            },
        }

    def _prepare_output_dir(self) -> str:
        output_base = Path(settings.OUTPUT_DIR) / self.context.task_id
        output_base.mkdir(parents=True, exist_ok=True)
        return str(output_base)
