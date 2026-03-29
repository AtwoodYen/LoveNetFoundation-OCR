"""
客戶端（例如 iOS Vision）已完成文字辨識時的流程。

仍會做 PDF/圖片轉頁面圖，但不呼叫 layout_ocr_url；改以客戶上傳的
client_markdown 合成與 pipeline 相同形狀的 ocr_result，再走 merge。
"""

import json
from pathlib import Path
from typing import Dict, Any, List

from app.core.flows.pipeline_flow import PipelineFlow
from app.utils.logger import logger


class ClientVisionFlow(PipelineFlow):
    """
    processing_mode: client_vision

    需在 ocr_config 內含 client_markdown（純文字，通常來自手機端 Vision OCR）。
    """

    async def _step_layout_and_ocr(self, pdf_result: Dict[str, Any]) -> Dict[str, Any]:
        step_name = "layout_and_ocr"
        task_id = self.context.task_id
        logger.info(f"[{task_id}] client_vision: skip remote OCR, use device markdown")

        base_progress = self.STEP_WEIGHTS["pdf_to_image"] * 100

        await self.update_progress(
            step_name=step_name,
            progress=0.0,
            overall_progress=base_progress,
            message="Using client-provided OCR text",
        )

        md = (self.context.ocr_config or {}).get("client_markdown")
        if not isinstance(md, str) or not md.strip():
            raise ValueError(
                "client_vision 模式需要 ocr_config.client_markdown（非空字串）"
            )
        text = md.strip()

        output_dir = self.context.get_output_dir()
        output_files: List[str] = pdf_result.get("output_files") or []
        if not output_files:
            raise ValueError("沒有產生任何頁面圖片，無法建立 OCR 結果")

        pages: List[Dict[str, Any]] = []
        for i, img_path in enumerate(output_files):
            page_num = i + 1
            page_text = text if i == 0 else ""
            pages.append(
                {
                    "page_index": page_num,
                    "image_file": img_path,
                    "layout": {
                        "blocks": [
                            {
                                "layout_type": "text",
                                "layout_box": [0.0, 0.0, 1.0, 1.0],
                                "content": page_text,
                                "index": 1,
                                "image_path": None,
                                "page_index": page_num,
                            }
                        ]
                    },
                }
            )

        ocr_result_file = Path(output_dir) / "ocr_result.json"
        ocr_result_data: Dict[str, Any] = {
            "success": True,
            "pages": pages,
            "total_pages": len(pages),
            "images_dir": pdf_result.get("images_dir", ""),
            "ocr_result_file": str(ocr_result_file),
            "ref_image_paths": [],
        }
        with open(ocr_result_file, "w", encoding="utf-8") as f:
            json.dump(ocr_result_data, f, ensure_ascii=False, indent=2)

        await self.update_progress(
            step_name=step_name,
            progress=100.0,
            overall_progress=(
                self.STEP_WEIGHTS["pdf_to_image"] + self.STEP_WEIGHTS["layout_and_ocr"]
            )
            * 100,
            message="Client OCR text applied",
        )

        return ocr_result_data
