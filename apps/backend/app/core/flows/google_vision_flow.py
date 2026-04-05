"""
Google Vision OCR 處理流程

流程：圖片上傳 -> 預處理（奉獻袋）-> Google Vision OCR -> 結果整理 -> 輸出

預處理步驟（僅奉獻袋）：
1. page_0001.png - 原圖
2. page_0002.png - 藍黑色變全黑（提高對比度）
3. page_0003.png - 去除橘色（含深橘色）
4. page_0004.png - OCR 辨識區塊標記紅框
5. page_0005.png - 手寫內容標註
"""

import json
from typing import Dict, Any, List
from pathlib import Path
import shutil
import numpy as np

from app.core.flows.base import TaskProcessingFlow, ProcessingContext
from app.utils.google_vision_ocr import (
    ocr_with_google_vision,
    ocr_with_blocks,
    ocr_multiple_images,
    GoogleVisionOCRError,
    TextBlock,
)
from app.utils.image_preprocessing import (
    preprocess_offering_envelope,
    PreprocessResult,
    remove_orange_pixels_fast,
    enhance_blue_black_to_black,
    draw_ocr_boxes,
    group_blocks_by_y,
    draw_annotated_result,
)
from app.utils.logger import logger
from app.utils.config import settings
from app.utils.offering_display import build_offering_display


class GoogleVisionFlow(TaskProcessingFlow):
    """
    Google Vision OCR 處理流程

    處理步驟：
    1. 準備圖片 (0-10%)
    2. 預處理（奉獻袋：去橘色、加深藍黑、標記紅框、標註手寫）(10-40%)
    3. Google Vision OCR (40-80%)
    4. 結果整理與輸出 (80-100%)
    """

    STEP_WEIGHTS = {
        "prepare": 0.10,
        "preprocess": 0.30,
        "google_vision_ocr": 0.40,
        "result_output": 0.20,
    }

    async def process(self) -> Dict[str, Any]:
        """執行 Google Vision OCR 流程"""

        logger.info(f"[{self.context.task_id}] Starting Google Vision flow")

        # 準備輸出目錄
        output_dir = self._prepare_output_dir()
        self.context.set_output_dir(output_dir)

        # 步驟1: 準備圖片
        image_paths = await self._step_prepare_images()

        # 檢查是否為奉獻袋表單
        form_template = (self.context.ocr_config or {}).get("form_template")
        preprocess_result = None

        if form_template == "offering_envelope" and len(image_paths) > 0:
            # 步驟2: 奉獻袋預處理
            preprocess_result = await self._step_preprocess_offering(image_paths[0])
            # 使用預處理後的圖片進行 OCR
            ocr_image_paths = [preprocess_result.contrast_enhanced_path]
        else:
            ocr_image_paths = image_paths

        # 步驟3: Google Vision OCR
        ocr_results = await self._step_google_vision_ocr(ocr_image_paths, preprocess_result)

        # 步驟4: 結果整理與輸出
        final_result = await self._step_result_output(ocr_results, preprocess_result)

        logger.info(f"[{self.context.task_id}] Google Vision flow completed")

        return {
            "success": True,
            "md_output_path": final_result["md_output_path"],
            "json_output_path": final_result["json_output_path"],
            "output_files": final_result.get("output_files", []),
            "metadata": final_result.get("metadata", {}),
        }

    def _prepare_output_dir(self) -> str:
        """準備輸出目錄"""
        output_base = Path(settings.OUTPUT_DIR) / self.context.task_id
        output_base.mkdir(parents=True, exist_ok=True)
        return str(output_base)

    async def _step_prepare_images(self) -> list:
        """步驟1: 準備圖片"""
        step_name = "prepare"
        logger.info(f"[{self.context.task_id}] Starting step: {step_name}")

        await self.update_progress(
            step_name=step_name,
            progress=0.0,
            overall_progress=0.0,
            message="準備圖片中",
        )

        file_path = Path(self.context.file_path)
        file_type = self.context.file_type.lower()

        image_paths = []

        if file_type in ["jpg", "jpeg", "png", "gif", "bmp", "webp"]:
            # 單張圖片
            image_paths = [str(file_path)]
            logger.info(f"單張圖片: {file_path.name}")

        elif file_type == "pdf":
            # PDF 需要先轉換為圖片
            from app.core.steps.pdf_to_image import PdfToImageStepInput, pdf_to_image

            pdf_result = await pdf_to_image(
                context=self.context,
                input=PdfToImageStepInput(
                    file_path=str(file_path),
                    output_dir=self.context.get_output_dir(),
                    dpi=self.context.ocr_config.get("dpi", 200),
                    format="png",
                ),
                progress_callback=lambda p, msg: None,
            )
            image_paths = pdf_result.get("output_files", [])
            logger.info(f"PDF 轉換完成: {len(image_paths)} 頁")

        else:
            raise ValueError(f"不支援的檔案類型: {file_type}")

        await self.update_progress(
            step_name=step_name,
            progress=100.0,
            overall_progress=self.STEP_WEIGHTS["prepare"] * 100,
            message=f"準備完成，共 {len(image_paths)} 張圖片",
        )

        return image_paths

    async def _step_preprocess_offering(self, image_path: str) -> PreprocessResult:
        """步驟2: 奉獻袋圖片預處理

        處理順序：
        1. page_0001.png - 原圖
        2. page_0002.png - 藍黑色變全黑（提高對比度）
        3. page_0003.png - 去除橘色（含深橘色）
        4. page_0004.png - OCR 辨識區塊標記紅框
        5. page_0005.png - 手寫內容標註
        """
        step_name = "preprocess"
        logger.info(f"[{self.context.task_id}] Starting step: {step_name}")

        base_progress = self.STEP_WEIGHTS["prepare"] * 100

        await self.update_progress(
            step_name=step_name,
            progress=0.0,
            overall_progress=base_progress,
            message="開始奉獻袋圖片預處理",
        )

        import cv2

        output_dir = Path(self.context.get_output_dir())

        # 讀取原始圖片
        original = cv2.imread(image_path)
        if original is None:
            raise ValueError(f"無法讀取圖片: {image_path}")

        height, width = original.shape[:2]
        logger.info(f"圖片尺寸: {width}x{height}")

        # 儲存原始圖片為 page_0001.png
        original_copy_path = output_dir / "page_0001.png"
        cv2.imwrite(str(original_copy_path), original)
        logger.info(f"已儲存原圖: {original_copy_path}")

        await self.update_progress(
            step_name=step_name,
            progress=20.0,
            overall_progress=base_progress + 20 * self.STEP_WEIGHTS["preprocess"],
            message="將藍/黑色像素變全黑，提高對比度",
        )

        # 步驟 1: 藍黑色變全黑（提高對比度）
        logger.info(f"[{self.context.task_id}] ===== 開始呼叫 enhance_blue_black_to_black =====")
        contrast_enhanced = enhance_blue_black_to_black(original)
        logger.info(f"[{self.context.task_id}] ===== enhance_blue_black_to_black 完成 =====")
        contrast_enhanced_path = output_dir / "page_0002.png"
        cv2.imwrite(str(contrast_enhanced_path), contrast_enhanced)
        logger.info(f"[{self.context.task_id}] 已儲存 page_0002.png: {contrast_enhanced_path}")

        await self.update_progress(
            step_name=step_name,
            progress=40.0,
            overall_progress=base_progress + 40 * self.STEP_WEIGHTS["preprocess"],
            message="去除橘色像素（含深橘色）",
        )

        # 步驟 2: 去除橘色像素（在原圖上操作，不是在對比度增強後的圖）
        # 先去橘色，再疊加藍黑變黑的結果
        orange_removed = remove_orange_pixels_fast(original)
        # 將藍黑色區域（對比度增強後變黑的區域）也設為黑色
        black_pixels = np.all(contrast_enhanced == [0, 0, 0], axis=2)
        orange_removed[black_pixels] = [0, 0, 0]

        orange_removed_path = output_dir / "page_0003.png"
        cv2.imwrite(str(orange_removed_path), orange_removed)
        logger.info(f"已儲存去橘色圖: {orange_removed_path}")

        await self.update_progress(
            step_name=step_name,
            progress=60.0,
            overall_progress=base_progress + 60 * self.STEP_WEIGHTS["preprocess"],
            message="OCR 辨識處理後區塊",
        )

        # 步驟 3: 對處理後圖片 OCR 並標記紅框
        text_blocks = await ocr_with_blocks(str(orange_removed_path))
        logger.info(f"處理後辨識到 {len(text_blocks)} 個文字區塊")

        # 轉換為 image_preprocessing 模組的 TextBlock 格式
        from app.utils.image_preprocessing import TextBlock as PreprocTextBlock
        preproc_blocks = [
            PreprocTextBlock(
                text=b.text,
                bbox=b.bbox,
                confidence=b.confidence,
                vertices=list(b.vertices) if b.vertices else []
            )
            for b in text_blocks
        ]

        ocr_boxes_image = draw_ocr_boxes(orange_removed, preproc_blocks)
        ocr_boxes_path = output_dir / "page_0004.png"
        cv2.imwrite(str(ocr_boxes_path), ocr_boxes_image)
        logger.info(f"已儲存紅框標記圖: {ocr_boxes_path}")

        await self.update_progress(
            step_name=step_name,
            progress=80.0,
            overall_progress=base_progress + 80 * self.STEP_WEIGHTS["preprocess"],
            message="標註手寫內容中",
        )

        # 步驟 4: 依 Y 軸分組並標註手寫內容
        grouped_lines = group_blocks_by_y(preproc_blocks)
        annotated_image = draw_annotated_result(
            orange_removed, grouped_lines, height, width
        )
        annotated_path = output_dir / "page_0005.png"
        cv2.imwrite(str(annotated_path), annotated_image)
        logger.info(f"已儲存標註圖: {annotated_path}")

        await self.update_progress(
            step_name=step_name,
            progress=100.0,
            overall_progress=(self.STEP_WEIGHTS["prepare"] + self.STEP_WEIGHTS["preprocess"]) * 100,
            message="預處理完成",
        )

        return PreprocessResult(
            original_path=image_path,
            contrast_enhanced_path=str(contrast_enhanced_path),
            orange_removed_path=str(orange_removed_path),
            ocr_boxes_path=str(ocr_boxes_path),
            annotated_path=str(annotated_path),
            text_blocks=preproc_blocks,
            grouped_lines=grouped_lines,
        )

    async def _step_google_vision_ocr(
        self,
        image_paths: list,
        preprocess_result: PreprocessResult = None
    ) -> Dict[str, Any]:
        """步驟3: Google Vision OCR"""
        step_name = "google_vision_ocr"
        logger.info(f"[{self.context.task_id}] Starting step: {step_name}")

        # 計算基礎進度（考慮是否有預處理步驟）
        if preprocess_result:
            base_progress = (self.STEP_WEIGHTS["prepare"] + self.STEP_WEIGHTS["preprocess"]) * 100
        else:
            base_progress = self.STEP_WEIGHTS["prepare"] * 100

        await self.update_progress(
            step_name=step_name,
            progress=0.0,
            overall_progress=base_progress,
            message="開始 Google Vision OCR",
        )

        all_text_parts = []
        page_results = []

        total_images = len(image_paths)
        for i, image_path in enumerate(image_paths):
            progress = ((i + 1) / total_images) * 80  # 主要 OCR 佔 80%

            await self.update_progress(
                step_name=step_name,
                progress=progress,
                overall_progress=base_progress + progress * self.STEP_WEIGHTS["google_vision_ocr"],
                message=f"OCR 處理中 ({i + 1}/{total_images})",
            )

            try:
                result = await ocr_with_google_vision(image_path)
                all_text_parts.append(result.text)
                page_results.append({
                    "page": i + 1,
                    "image_path": image_path,
                    "text": result.text,
                    "char_count": len(result.text),
                    "text_blocks": len(result.text_blocks),
                })
                logger.info(f"頁 {i + 1} OCR 完成: {len(result.text)} 字元, {len(result.text_blocks)} 區塊")
            except GoogleVisionOCRError as e:
                logger.error(f"頁 {i + 1} OCR 失敗: {e}")
                page_results.append({
                    "page": i + 1,
                    "image_path": image_path,
                    "text": "",
                    "error": str(e),
                })

        full_text = "\n\n".join(all_text_parts)

        # 檢查是否有表格區域圖片需要額外 OCR
        form_area_text = None
        form_area_path = (self.context.ocr_config or {}).get("form_area_path")
        if form_area_path and Path(form_area_path).exists():
            logger.info(f"[{self.context.task_id}] 開始表格區域 OCR: {form_area_path}")

            await self.update_progress(
                step_name=step_name,
                progress=90.0,
                overall_progress=base_progress + 90 * self.STEP_WEIGHTS["google_vision_ocr"],
                message="表格區域 OCR 處理中",
            )

            try:
                form_result = await ocr_with_google_vision(form_area_path)
                form_area_text = form_result.text
                logger.info(f"表格區域 OCR 完成: {len(form_area_text)} 字元")

                # 添加到結果中
                page_results.append({
                    "page": "form_area",
                    "image_path": form_area_path,
                    "text": form_area_text,
                    "char_count": len(form_area_text),
                    "text_blocks": len(form_result.text_blocks),
                    "is_form_area": True,
                })
            except GoogleVisionOCRError as e:
                logger.error(f"表格區域 OCR 失敗: {e}")
                page_results.append({
                    "page": "form_area",
                    "image_path": form_area_path,
                    "text": "",
                    "error": str(e),
                    "is_form_area": True,
                })

        # 計算完成進度
        if preprocess_result:
            complete_progress = (
                self.STEP_WEIGHTS["prepare"] +
                self.STEP_WEIGHTS["preprocess"] +
                self.STEP_WEIGHTS["google_vision_ocr"]
            ) * 100
        else:
            complete_progress = (
                self.STEP_WEIGHTS["prepare"] +
                self.STEP_WEIGHTS["google_vision_ocr"]
            ) * 100

        await self.update_progress(
            step_name=step_name,
            progress=100.0,
            overall_progress=complete_progress,
            message=f"OCR 完成，共 {len(full_text)} 字元" + (f"，表格區域 {len(form_area_text)} 字元" if form_area_text else ""),
        )

        return {
            "full_text": full_text,
            "form_area_text": form_area_text,
            "page_results": page_results,
            "total_pages": total_images,
        }

    async def _step_result_output(
        self,
        ocr_results: Dict[str, Any],
        preprocess_result: PreprocessResult = None
    ) -> Dict[str, Any]:
        """步驟4: 結果整理與輸出"""
        step_name = "result_output"
        logger.info(f"[{self.context.task_id}] Starting step: {step_name}")

        # 計算基礎進度
        if preprocess_result:
            base_progress = (
                self.STEP_WEIGHTS["prepare"] +
                self.STEP_WEIGHTS["preprocess"] +
                self.STEP_WEIGHTS["google_vision_ocr"]
            ) * 100
        else:
            base_progress = (
                self.STEP_WEIGHTS["prepare"] +
                self.STEP_WEIGHTS["google_vision_ocr"]
            ) * 100

        await self.update_progress(
            step_name=step_name,
            progress=0.0,
            overall_progress=base_progress,
            message="整理結果中",
        )

        output_dir = Path(self.context.get_output_dir())
        full_text = ocr_results["full_text"]

        # 輸出 Markdown
        md_output_path = output_dir / "result.md"
        md_content = f"# OCR 結果\n\n{full_text}"
        md_output_path.write_text(md_content, encoding="utf-8")

        # 收集輸出檔案
        output_files = [str(md_output_path)]

        # 準備 JSON 結果
        result_data = {
            "task_id": self.context.task_id,
            "document_id": self.context.document_id,
            "processing_mode": "google_vision",
            "ocr_engine": "Google Cloud Vision API",
            "full_markdown": full_text,
            "page_results": ocr_results["page_results"],
            "total_pages": ocr_results["total_pages"],
            "total_chars": len(full_text),
        }

        # 如果有預處理結果，添加預處理圖片路徑
        if preprocess_result:
            result_data["preprocessing"] = {
                "original": preprocess_result.original_path,
                "contrast_enhanced": preprocess_result.contrast_enhanced_path,  # page_0002.png
                "orange_removed": preprocess_result.orange_removed_path,        # page_0003.png
                "ocr_boxes": preprocess_result.ocr_boxes_path,                  # page_0004.png
                "annotated": preprocess_result.annotated_path,                  # page_0005.png
                "text_blocks_count": len(preprocess_result.text_blocks),
                "grouped_lines_count": len(preprocess_result.grouped_lines),
            }
            # 添加預處理圖片到輸出檔案列表
            output_files.extend([
                preprocess_result.contrast_enhanced_path,
                preprocess_result.orange_removed_path,
                preprocess_result.ocr_boxes_path,
                preprocess_result.annotated_path,
            ])
            logger.info(f"預處理圖片已添加到輸出: page_0002~page_0005")

        # 檢查是否為奉獻袋表單
        form_template = (self.context.ocr_config or {}).get("form_template")
        if form_template == "offering_envelope":
            # 使用完整信封 OCR 結果建立奉獻袋摘要
            offering_display = build_offering_display(full_text)

            # 如果有表格區域 OCR 結果，嘗試從中提取更精確的金額
            form_area_text = ocr_results.get("form_area_text")
            if form_area_text:
                logger.info(f"表格區域 OCR 結果:\n{form_area_text}")
                # 從表格區域也建立一個摘要，用於比對/補充
                form_area_display = build_offering_display(form_area_text)
                result_data["form_area_ocr"] = {
                    "text": form_area_text,
                    "display": form_area_display,
                }
                logger.info(f"表格區域奉獻袋摘要: {form_area_display}")

                # 如果主摘要沒有金額但表格區域有，使用表格區域的金額
                if form_area_display.get("items") and not offering_display.get("items"):
                    offering_display["items"] = form_area_display["items"]
                    offering_display["total"] = form_area_display.get("total")
                    logger.info("使用表格區域的金額資訊補充主摘要")

            result_data["offering_display"] = offering_display
            sm = offering_display.get("sanitized_markdown")
            if isinstance(sm, str) and sm.strip():
                result_data["full_markdown"] = sm
                md_output_path.write_text(f"# OCR 結果\n\n{sm}", encoding="utf-8")
            logger.info(f"奉獻袋摘要: {offering_display}")

        # 輸出 JSON
        json_output_path = output_dir / "result.json"
        json_output_path.write_text(
            json.dumps(result_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        output_files.append(str(json_output_path))

        await self.update_progress(
            step_name=step_name,
            progress=100.0,
            overall_progress=100.0,
            message="結果輸出完成",
        )

        return {
            "md_output_path": str(md_output_path),
            "json_output_path": str(json_output_path),
            "output_files": output_files,
            "metadata": {
                "ocr_engine": "Google Cloud Vision API",
                "total_pages": ocr_results["total_pages"],
                "total_chars": len(full_text),
                "has_preprocessing": preprocess_result is not None,
            },
        }
