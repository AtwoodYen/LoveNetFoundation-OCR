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
from app.utils.offering_display import (
    build_offering_display,
    should_apply_lovenet_offering_rules,
)
from app.utils.donation_rules import process_donation_ocr
from app.utils.llm_corrector import correct_donation_output
from app.utils.pp_layout_analyzer import detect_layout
from app.utils.offering_spatial_sort import reorder_blocks_by_layout
from app.utils.paddle_name_ocr import reocr_donor_name

# Google Vision OCR 處理流程 =============================================================================
class GoogleVisionFlow(TaskProcessingFlow):
    """
    Google Vision OCR 處理流程

    處理步驟：
    1. 準備圖片 (0-10%)
    2. 預處理（奉獻袋：去橘色、加深藍黑、標記紅框、標註手寫）(10-40%)
    3. Google Vision OCR (40-80%)
    4. 結果整理與輸出 (80-100%)
    """

    # 步驟權重
    STEP_WEIGHTS = {
        "prepare": 0.10,
        "preprocess": 0.25,
        "layout_analysis": 0.05,   # PP-DocLayoutV3 版面分析（奉獻袋專用）
        "google_vision_ocr": 0.40,
        "result_output": 0.20,
    }

    # 執行 Google Vision OCR 流程 =============================================================================
    async def process(self) -> Dict[str, Any]:
        """執行 Google Vision OCR 流程"""

        logger.info(f"[{self.context.task_id}] ===== 開始 Google Vision OCR 流程 ======")

        # 準備輸出目錄
        output_dir = self._prepare_output_dir() # 準備輸出目錄
        self.context.set_output_dir(output_dir) # 設定輸出目錄

        # 步驟1: 準備圖片
        image_paths = await self._step_prepare_images()

        # 檢查是否為奉獻袋表單
        form_template = (self.context.ocr_config or {}).get("form_template") # 表單模板: form_template
        preprocess_result = None # 預處理結果: preprocess_result

        layout_regions = None   # PP-DocLayoutV3 版面區域（奉獻袋專用）

        if form_template == "offering_envelope" and len(image_paths) > 0: # 如果表單模板為奉獻袋，且圖片路徑列表不為空
            # 步驟2: 奉獻袋預處理
            preprocess_result = await self._step_preprocess_offering(image_paths[0])
            # 使用預處理後的圖片進行 OCR
            ocr_image_paths = [preprocess_result.contrast_enhanced_path] # 使用預處理後的圖片進行 OCR

            # 步驟2.5: PP-DocLayoutV3 版面分析（對去橘色後的圖片進行）
            layout_regions = await self._step_layout_analysis(
                preprocess_result.orange_removed_path
            )
        else:
            ocr_image_paths = image_paths # 使用原始圖片進行 OCR

        # 步驟3: Google Vision OCR
        ocr_results = await self._step_google_vision_ocr(ocr_image_paths, preprocess_result) # 使用預處理後的圖片進行 OCR

        # 步驟4: 結果整理與輸出
        final_result = await self._step_result_output(
            ocr_results, preprocess_result, layout_regions=layout_regions
        ) # 結果整理與輸出

        logger.info(f"[{self.context.task_id}] Google Vision OCR 流程完成")

        # 返回結果
        return {
            "success": True, # 成功: True
            "md_output_path": final_result["md_output_path"], # Markdown 輸出路徑: md_output_path
            "json_output_path": final_result["json_output_path"], # JSON 輸出路徑: json_output_path
            "output_files": final_result.get("output_files", []), # 輸出檔案列表: output_files
            "metadata": final_result.get("metadata", {}),   # 檔案元數據: metadata
        }

    # 準備輸出目錄 =============================================================================
    def _prepare_output_dir(self) -> str:
        """準備輸出目錄"""
        output_base = Path(settings.OUTPUT_DIR) / self.context.task_id
        output_base.mkdir(parents=True, exist_ok=True)
        return str(output_base)

    # =====================================================================================================
    # 步驟1: 準備圖片 =======================================================================================
    # =====================================================================================================
    async def _step_prepare_images(self) -> list:
        """步驟1: 準備圖片"""
        step_name = "prepare"
        logger.info(f"[{self.context.task_id}] 初始步驟: {step_name} 準備圖片中")

        await self.update_progress( # 更新進度:0
            step_name=step_name,
            progress=0.0,
            overall_progress=0.0,   # 整體進度: 0.0
            message="準備圖片中",     # 進度消息: "準備圖片中"
        )

        file_path = Path(self.context.file_path)    # 檔案路徑: file_path
        file_type = self.context.file_type.lower()  # 檔案類型: file_type

        image_paths = [] # 圖片路徑列表: image_paths

        if file_type in ["jpg", "jpeg", "png", "gif", "bmp", "webp"]: # 支援的圖片格式
            # 單張圖片
            image_paths = [str(file_path)] # 單張圖片路徑
            logger.info(f"單張圖片: {file_path.name}")

        elif file_type == "pdf": # PDF 需要先轉換為圖片
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

    # =====================================================================================================
    # 步驟2: 奉獻袋圖片預處理 ================================================================================
    # =====================================================================================================
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

        base_progress = self.STEP_WEIGHTS["prepare"] * 100 # 基礎進度: base_progress

        # 更新進度: 0%
        await self.update_progress(
            step_name=step_name,
            progress=0.0,
            overall_progress=base_progress,
            message="開始奉獻袋圖片預處理",
        )

        import cv2

        output_dir = Path(self.context.get_output_dir())  # 輸出目錄: output_dir

        # 讀取原始圖片
        original = cv2.imread(image_path) # 原始圖片: original
        if original is None:
            raise ValueError(f"無法讀取圖片: {image_path}")

        height, width = original.shape[:2]  # 圖片高度: height, 圖片寬度: width
        logger.info(f"圖片尺寸: {width}x{height}")  # 圖片尺寸: width x height

        # 儲存原始圖片為 page_0001.png
        original_copy_path = output_dir / "page_0001.png" # 原始圖片路徑: original_copy_path
        cv2.imwrite(str(original_copy_path), original)  # 儲存原始圖片: page_0001.png 到 output_dir 目錄
        logger.info(f"已儲存原圖: {original_copy_path}")    # 已儲存原圖: page_0001.png 到 output_dir 目錄: original_copy_path

        # 更新進度: 20%
        await self.update_progress( # 更新進度: 20%
            step_name=step_name,
            progress=20.0, # 進度: 20.0
            overall_progress=base_progress + 20 * self.STEP_WEIGHTS["preprocess"], # 整體進度: base_progress + 20 * self.STEP_WEIGHTS["preprocess"]
            message="將藍/黑色像素變全黑，提高對比度", # 進度消息: "將藍/黑色像素變全黑，提高對比度"
        )

        # 步驟 1: 藍黑色變全黑（提高對比度）
        logger.info(f"[{self.context.task_id}] ===== 開始呼叫 enhance_blue_black_to_black =====")
        contrast_enhanced = enhance_blue_black_to_black(original)   # 對比度增強後的圖片: contrast_enhanced
        logger.info(f"[{self.context.task_id}] ===== enhance_blue_black_to_black 完成 =====") # 對比度增強完成
        contrast_enhanced_path = output_dir / "page_0002.png" # 對比度增強後的圖片路徑: contrast_enhanced_path
        cv2.imwrite(str(contrast_enhanced_path), contrast_enhanced) # 儲存對比度增強後的圖片
        logger.info(f"[{self.context.task_id}] 已儲存 page_0002.png: {contrast_enhanced_path}") # 已儲存 page_0002.png: contrast_enhanced_path

        # 更新進度: 40%
        await self.update_progress( # 更新進度: 40%
            step_name=step_name,
            progress=40.0, # 進度: 40.0
            overall_progress=base_progress + 40 * self.STEP_WEIGHTS["preprocess"], # 整體進度: base_progress + 40 * self.STEP_WEIGHTS["preprocess"]
            message="去除橘色像素（含深橘色）", # 進度消息: "去除橘色像素（含深橘色）"
        )

        # 步驟 2: 去除橘色像素（在原圖上操作，不是在對比度增強後的圖）
        # 先去橘色，再疊加藍黑變黑的結果
        orange_removed = remove_orange_pixels_fast(original) # 去橘色圖片: orange_removed
        # 將藍黑色區域（對比度增強後變黑的區域）也設為黑色
        black_pixels = np.all(contrast_enhanced == [0, 0, 0], axis=2)   # 藍黑色區域: black_pixels
        orange_removed[black_pixels] = [0, 0, 0]    # 將藍黑色區域設為黑色

        orange_removed_path = output_dir / "page_0003.png" # 去橘色圖片路徑: orange_removed_path
        cv2.imwrite(str(orange_removed_path), orange_removed)   # 儲存去橘色圖片
        logger.info(f"已儲存去橘色圖: {orange_removed_path}")

        # 更新進度: 60%
        await self.update_progress( # 更新進度: 60%
            step_name=step_name,
            progress=60.0, # 進度: 60.0
            overall_progress=base_progress + 60 * self.STEP_WEIGHTS["preprocess"], # 整體進度: base_progress + 60 * self.STEP_WEIGHTS["preprocess"]
            message="OCR 辨識處理後區塊", # 進度消息: "OCR 辨識處理後區塊"
        )

        # 步驟 3: 對處理後圖片 OCR 並標記紅框
        text_blocks = await ocr_with_blocks(str(orange_removed_path))
        logger.info(f"處理後辨識到 {len(text_blocks)} 個文字區塊")

        # 轉換為 image_preprocessing 模組的 TextBlock 格式
        from app.utils.image_preprocessing import TextBlock as PreprocTextBlock
        preproc_blocks = [ # OCR 辨識到的文字區塊列表: preproc_blocks
            PreprocTextBlock(
                text=b.text, # 文字內容: b.text
                bbox=b.bbox, # 文字區塊座標: b.bbox
                confidence=b.confidence, # 文字區塊信心值: b.confidence
                vertices=list(b.vertices) if b.vertices else [] # 文字區塊頂點座標: b.vertices
            )
            for b in text_blocks
        ]

        ocr_boxes_image = draw_ocr_boxes(orange_removed, preproc_blocks) # 畫出紅框標記圖片
        ocr_boxes_path = output_dir / "page_0004.png" # OCR 紅框標記圖片路徑: ocr_boxes_path
        cv2.imwrite(str(ocr_boxes_path), ocr_boxes_image) # 儲存紅框標記圖片
        logger.info(f"已儲存紅框標記圖: {ocr_boxes_path}")  # 已儲存紅框標記圖: ocr_boxes_path

        # 更新進度: 80%
        await self.update_progress(
            step_name=step_name, # 步驟名稱: step_name
            progress=80.0, # 進度: 80.0
            overall_progress=base_progress + 80 * self.STEP_WEIGHTS["preprocess"], # 整體進度: base_progress + 80 * self.STEP_WEIGHTS["preprocess"]
            message="標註手寫內容中", # 進度消息: "標註手寫內容中"
        )

        # 步驟 4: 依 Y 軸分組並標註手寫內容
        grouped_lines = group_blocks_by_y(preproc_blocks) # 依 Y 軸分組的文字區塊列表: grouped_lines
        annotated_image = draw_annotated_result( # 畫出標註手寫內容圖片
            orange_removed, grouped_lines, height, width # 去橘色後的圖片, 依 Y 軸分組的文字區塊列表, 圖片高度, 圖片寬度
        )
        annotated_path = output_dir / "page_0005.png" # 標註手寫內容圖片路徑: annotated_path
        cv2.imwrite(str(annotated_path), annotated_image) # 儲存標註手寫內容圖片
        logger.info(f"已儲存標註圖: {annotated_path}") # 已儲存標註圖: annotated_path

        # 更新進度: 100%
        await self.update_progress(
            step_name=step_name,
            progress=100.0,
            overall_progress=(self.STEP_WEIGHTS["prepare"] + self.STEP_WEIGHTS["preprocess"]) * 100,
            message="預處理完成",
        )

        # 返回預處理結果: PreprocessResult
        return PreprocessResult(
            original_path=image_path,                           # 原始圖片路徑: image_path
            contrast_enhanced_path=str(contrast_enhanced_path), # 對比度增強後的圖片路徑: contrast_enhanced_path
            orange_removed_path=str(orange_removed_path),       # 去橘色後的圖片路徑: orange_removed_path
            ocr_boxes_path=str(ocr_boxes_path),                 # OCR 紅框標記圖片路徑: ocr_boxes_path
            annotated_path=str(annotated_path),                 # 標註手寫內容圖片路徑: annotated_path
            text_blocks=preproc_blocks,                         # OCR 辨識到的文字區塊列表: preproc_blocks
            grouped_lines=grouped_lines,                        # 依 Y 軸分組的文字區塊列表: grouped_lines
        )

    # =====================================================================================================
    # 步驟2.5: PP-DocLayoutV3 版面分析 =====================================================================
    # =====================================================================================================
    async def _step_layout_analysis(self, image_path: str):
        """
        步驟2.5: 用 PP-DocLayoutV3 對奉獻袋圖片做版面偵測。

        回傳 List[LayoutRegion]，或 None（若模型未啟用 / 偵測失敗）。
        失敗時靜默降級，不影響後續 OCR 流程。
        """
        step_name = "layout_analysis"
        base_progress = (
            self.STEP_WEIGHTS["prepare"] + self.STEP_WEIGHTS["preprocess"]
        ) * 100

        await self.update_progress(
            step_name=step_name,
            progress=0.0,
            overall_progress=base_progress,
            message="PP-DocLayoutV3 版面分析中",
        )

        logger.info("[%s] 開始 PP-DocLayoutV3 版面分析：%s", self.context.task_id, image_path)
        regions = detect_layout(image_path)

        if regions:
            logger.info(
                "[%s] 版面分析完成：%d 個 region", self.context.task_id, len(regions)
            )
        else:
            logger.info("[%s] 版面分析不可用，將使用原始 block 順序", self.context.task_id)

        await self.update_progress(
            step_name=step_name,
            progress=100.0,
            overall_progress=base_progress + self.STEP_WEIGHTS["layout_analysis"] * 100,
            message=f"版面分析完成（{'%d 個 region' % len(regions) if regions else '降級模式'}）",
        )
        return regions

    # =====================================================================================================
    # 步驟3: Google Vision OCR =============================================================================
    # =====================================================================================================
    async def _step_google_vision_ocr(
        self, # 實例: self
        image_paths: list,  # 圖片路徑列表: image_paths, 可能包含預處理後的圖片或原始圖片
        preprocess_result: PreprocessResult = None # 預處理結果: preprocess_result
    ) -> Dict[str, Any]: # 返回值: Dict[str, Any]
        """步驟3: Google Vision OCR"""
        step_name = "google_vision_ocr"
        logger.info(f"[{self.context.task_id}] 開始進行: {step_name} 辨識流程")

        # 計算基礎進度（考慮是否有預處理步驟）
        if preprocess_result: # 如果預處理結果不為空
            base_progress = (self.STEP_WEIGHTS["prepare"] + self.STEP_WEIGHTS["preprocess"]) * 100 # 基礎進度: base_progress
        else: # 如果預處理結果為空
            base_progress = self.STEP_WEIGHTS["prepare"] * 100 # 基礎進度: base_progress
        if preprocess_result:
            base_progress = (self.STEP_WEIGHTS["prepare"] + self.STEP_WEIGHTS["preprocess"]) * 100
        else:
            base_progress = self.STEP_WEIGHTS["prepare"] * 100

        # 更新進度: 0%
        await self.update_progress(
            step_name=step_name,
            progress=0.0,
            overall_progress=base_progress,
            message="開始 Google Vision OCR",
        )

        all_text_parts = [] # 所有文字部分: all_text_parts
        page_results = []   # 頁面結果: page_results

        total_images = len(image_paths) # 總圖片數: total_images
        for i, image_path in enumerate(image_paths): # 遍歷圖片路徑列表
            progress = ((i + 1) / total_images) * 80  # 主要 OCR 佔 80%

            # 更新進度: progress
            await self.update_progress(
                step_name=step_name, # 步驟名稱: step_name
                progress=progress,   # 進度: progress, 主要 OCR 佔 80%
                overall_progress=base_progress + progress * self.STEP_WEIGHTS["google_vision_ocr"], # 整體進度: base_progress + progress * self.STEP_WEIGHTS["google_vision_ocr"]
                message=f"OCR 處理中 ({i + 1}/{total_images})", # 進度消息: "OCR 處理中 ({i + 1}/{total_images})"
            )

            # 嘗試進行 OCR
            try:
                result = await ocr_with_google_vision(image_path) # 進行 OCR
                all_text_parts.append(result.text) # 添加文字部分

                # 建立區塊資料（座標互換：x↔y, width↔height）
                blocks_data = [] # 區塊資料: blocks_data
                for idx, b in enumerate(result.text_blocks): # 遍歷文字區塊列表
                    x, y, w, h = b.bbox # 文字區塊座標: x, y, w, h
                    blocks_data.append({
                        "index": idx,           # 區塊索引: idx
                        "text": b.text or "",   # 文字內容: b.text
                        "x": int(y),            # 顯示 X = Vision API 的 y
                        "y": int(x),            # 顯示 Y = Vision API 的 x
                        "width": int(h),        # 顯示 W = Vision API 的 height
                        "height": int(w),       # 顯示 H = Vision API 的 width
                    })

                # 添加頁面結果
                page_results.append({ 
                    "page": i + 1,              # 頁面索引: i + 1
                    "image_path": image_path,   # 圖片路徑: image_path
                    "text": result.text,        # 文字內容: result.text
                    "char_count": len(result.text),      # 字元數: len(result.text)
                    "text_blocks_count": len(result.text_blocks), # 區塊數: len(result.text_blocks)
                    "text_blocks": blocks_data,            # 儲存區塊資料
                })
                
                logger.info(f"頁 {i + 1} OCR 完成: {len(result.text)} 字元, {len(result.text_blocks)} 區塊") # 頁 {i + 1} OCR 完成: {len(result.text)} 字元, {len(result.text_blocks)} 區塊
            except GoogleVisionOCRError as e: # 如果 OCR 失敗
                logger.error(f"頁 {i + 1} OCR 失敗: {e}") # 頁 {i + 1} OCR 失敗: {e}
                page_results.append({
                    "page": i + 1,              # 頁面索引: i + 1
                    "image_path": image_path,   # 圖片路徑: image_path
                    "text": "",                 # 文字內容: ""
                    "error": str(e),            # 錯誤訊息: str(e)
                    "text_blocks_count": 0,     # 區塊數: 0
                    "text_blocks": [],          # 區塊資料: []
                })

        full_text = "\n\n".join(all_text_parts) # 所有文字部分: full_text

        # 檢查是否有表格區域圖片需要額外 OCR
        form_area_text = None # 表格區域文字: form_area_text
        form_area_path = (self.context.ocr_config or {}).get("form_area_path") # 表格區域圖片路徑: form_area_path
        if form_area_path and Path(form_area_path).exists(): # 如果表格區域圖片路徑存在且圖片存在
            logger.info(f"[{self.context.task_id}] 開始表格區域 OCR: {form_area_path}") # 開始表格區域 OCR: {form_area_path}

            # 更新進度: 90%
            await self.update_progress(
                step_name=step_name,
                progress=90.0,
                overall_progress=base_progress + 90 * self.STEP_WEIGHTS["google_vision_ocr"],
                message="表格區域 OCR 處理中",
            )

            try:
                form_result = await ocr_with_google_vision(form_area_path) # 進行表格區域 OCR
                form_area_text = form_result.text # 表格區域文字: form_area_text
                logger.info(f"表格區域 OCR 完成: {len(form_area_text)} 字元")

                # 建立區塊資料（座標互換）
                form_blocks_data = []      # 表格區塊資料: form_blocks_data
                for idx, b in enumerate(form_result.text_blocks):   # 遍歷文字區塊列表
                    x, y, w, h = b.bbox # 文字區塊座標: x, y, w, h
                    # 添加表格區塊資料
                    form_blocks_data.append({
                        "index": idx,           # 區塊索引: idx
                        "text": b.text or "",   # 文字內容: b.text
                        "x": int(y),            # 顯示 X = Vision API 的 y
                        "y": int(x),            # 顯示 Y = Vision API 的 x
                        "width": int(h),        # 顯示 W = Vision API 的 height
                        "height": int(w),       # 顯示 H = Vision API 的 width
                    })

                # 添加到結果中
                page_results.append({
                    "page": "form_area", # 頁面索引: "form_area"
                    "image_path": form_area_path, # 表格區域圖片路徑: form_area_path
                    "text": form_area_text,      # 文字內容: form_area_text
                    "char_count": len(form_area_text), # 字元數: len(form_area_text)
                    "text_blocks_count": len(form_result.text_blocks), # 區塊數: len(form_result.text_blocks)
                    "text_blocks": form_blocks_data, # 表格區塊資料: form_blocks_data
                    "is_form_area": True,          # 是否為表格區域: True
                })
            except GoogleVisionOCRError as e:
                logger.error(f"表格區域 OCR 失敗: {e}") # 表格區域 OCR 失敗: {e}
                page_results.append({
                    "page": "form_area",          # 頁面索引: "form_area"
                    "image_path": form_area_path, # 表格區域圖片路徑: form_area_path
                    "text": "",                   # 文字內容: ""
                    "error": str(e),             # 錯誤訊息: str(e)
                    "text_blocks_count": 0,      # 區塊數: 0
                    "text_blocks": [],           # 區塊資料: []
                    "is_form_area": True,        # 是否為表格區域: True
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

        # 更新進度: 100%
        await self.update_progress(
            step_name=step_name, # 步驟名稱: step_name
            progress=100.0,      # 進度: 100.0
            overall_progress=complete_progress, # 整體進度: complete_progress
            message=f"OCR 完成，共 {len(full_text)} 字元" + (f"，表格區域 {len(form_area_text)} 字元" if form_area_text else ""), # 進度消息: "OCR 完成，共 {len(full_text)} 字元" + (f"，表格區域 {len(form_area_text)} 字元" if form_area_text else "")
        )

        # 返回結果
        return {
            "full_text": full_text,             # 所有文字部分: full_text
            "form_area_text": form_area_text,   # 表格區域文字: form_area_text
            "page_results": page_results,       # 頁面結果: page_results
            "total_pages": total_images,        # 總圖片數: total_images
        }

    # =====================================================================================================
    # 步驟4: 結果整理與輸出 ================================================================================
    # =====================================================================================================
    async def _step_result_output(
        self, # 實例: self
        ocr_results: Dict[str, Any], #  OCR 結果: ocr_results
        preprocess_result: PreprocessResult = None, # 預處理結果: preprocess_result
        layout_regions=None, # PP-DocLayoutV3 版面區域（List[LayoutRegion] 或 None）
    ) -> Dict[str, Any]: # 返回值: Dict[str, Any]
        """步驟4: 結果整理與輸出"""
        step_name = "result_output"
        logger.info(f"[{self.context.task_id}] 開始進行: {step_name} 結果整理與輸出流程")

        # 計算基礎進度
        if preprocess_result: # 如果預處理結果不為空
            base_progress = ( # 基礎進度: base_progress
                self.STEP_WEIGHTS["prepare"] + # 準備圖片: 10%
                self.STEP_WEIGHTS["preprocess"] + # 預處理: 30%
                self.STEP_WEIGHTS["google_vision_ocr"] # Google Vision OCR: 40%
            ) * 100
        else: # 如果預處理結果為空
            base_progress = ( # 基礎進度: base_progress
                self.STEP_WEIGHTS["prepare"] + # 準備圖片: 10%
                self.STEP_WEIGHTS["google_vision_ocr"] # Google Vision OCR: 40%
            ) * 100

        # 更新進度: 0%
        await self.update_progress(
            step_name=step_name, # 步驟名稱: step_name
            progress=0.0,        # 進度: 0.0
            overall_progress=base_progress, # 整體進度: base_progress
            message="整理結果中",     # 進度消息: "整理結果中"
        )

        output_dir = Path(self.context.get_output_dir()) # 輸出目錄: output_dir
        full_text = ocr_results["full_text"] # 所有文字部分: full_text

        # 輸出 Markdown
        md_output_path = output_dir / "result.md" # Markdown 輸出路徑: md_output_path
        md_content = f"# OCR 結果\n\n{full_text}" # Markdown 內容: md_content
        md_output_path.write_text(md_content, encoding="utf-8") # 寫入 Markdown 檔案

        # 收集輸出檔案
        output_files = [str(md_output_path)] # 輸出檔案列表: output_files

        # 準備 JSON 結果
        result_data = {
            "task_id": self.context.task_id,            # 任務 ID: self.context.task_id
            "document_id": self.context.document_id,    # 文件 ID: self.context.document_id
            "processing_mode": "google_vision",         # 處理模式: "google_vision"
            "ocr_engine": "Google Cloud Vision API",    # OCR 引擎: "Google Cloud Vision API"
            "full_markdown": full_text,                 # 所有文字部分: full_text
            "page_results": ocr_results["page_results"],# 頁面結果: ocr_results["page_results"]
            "total_pages": ocr_results["total_pages"],  # 總圖片數: ocr_results["total_pages"]
            "total_chars": len(full_text),              # 總字元數: len(full_text)
            "layout_anchor_engine": "google_vision",    # 布局錨點引擎: "google_vision"
        }

        # 如果有預處理結果，添加預處理圖片路徑
        if preprocess_result: # 如果預處理結果不為空
            result_data["preprocessing"] = {
                "original": preprocess_result.original_path, # 原始圖片路徑: preprocess_result.original_path
                "contrast_enhanced": preprocess_result.contrast_enhanced_path, # 對比增強圖片路徑: preprocess_result.contrast_enhanced_path
                "orange_removed": preprocess_result.orange_removed_path,        # 去橘色圖片路徑: preprocess_result.orange_removed_path
                "ocr_boxes": preprocess_result.ocr_boxes_path,                  # OCR 框圖片路徑: preprocess_result.ocr_boxes_path
                "annotated": preprocess_result.annotated_path,                  # 標註圖片路徑: preprocess_result.annotated_path
                "text_blocks_count": len(preprocess_result.text_blocks), # 文字區塊數: len(preprocess_result.text_blocks)
                "grouped_lines_count": len(preprocess_result.grouped_lines), # 分組行數: len(preprocess_result.grouped_lines)
            }
            # 添加預處理圖片到輸出檔案列表
            output_files.extend([
                preprocess_result.contrast_enhanced_path, # 對比增強圖片路徑: preprocess_result.contrast_enhanced_path
                preprocess_result.orange_removed_path, # 去橘色圖片路徑: preprocess_result.orange_removed_path
                preprocess_result.ocr_boxes_path, # OCR 框圖片路徑: preprocess_result.ocr_boxes_path
                preprocess_result.annotated_path, # 標註圖片路徑: preprocess_result.annotated_path
            ])
            logger.info(f"預處理圖片已添加到輸出: page_0002~page_0005") # 預處理圖片已添加到輸出: page_0002~page_0005

        # 愛盟奉獻袋：不論是否勾選表單欄位，只要內容符合即套用同一套 Markdown／摘要規則
        form_template = (self.context.ocr_config or {}).get("form_template") # 表單模板: form_template
        if should_apply_lovenet_offering_rules( # 如果應用愛盟奉獻袋規則
            full_text, form_template=form_template
        ):
            # 使用完整信封 OCR 結果建立奉獻袋摘要
            offering_display = build_offering_display(full_text)

            # 如果有表格區域 OCR 結果，嘗試從中提取更精確的金額
            form_area_text = ocr_results.get("form_area_text") # 表格區域文字: form_area_text
            if form_area_text:
                logger.info(f"表格區域 OCR 結果:\n{form_area_text}") # 表格區域 OCR 結果:\n{form_area_text} 
                # 從表格區域也建立一個摘要，用於比對/補充
                form_area_display = build_offering_display(form_area_text)  # 表格區域奉獻袋摘要: form_area_display
                result_data["form_area_ocr"] = {
                    "text": form_area_text, # 文字內容: form_area_text
                    "display": form_area_display, # 奉獻袋摘要: form_area_display
                }
                logger.info(f"表格區域奉獻袋摘要: {form_area_display}") # 表格區域奉獻袋摘要: {form_area_display}

                # 如果主摘要沒有金額但表格區域有，使用表格區域的金額
                if form_area_display.get("items") and not offering_display.get("items"): # 如果表格區域有項目且主摘要沒有項目
                    offering_display["items"] = form_area_display["items"] # 項目: form_area_display["items"]
                    offering_display["total"] = form_area_display.get("total") # 總計: form_area_display.get("total")
                    logger.info("使用表格區域的金額資訊補充主摘要") # 使用表格區域的金額資訊補充主摘要

            result_data["offering_display"] = offering_display # 奉獻袋摘要: offering_display
            sm = offering_display.get("sanitized_markdown") # 清洗後的 Markdown: sm
            if isinstance(sm, str) and sm.strip(): # 如果清洗後的 Markdown 不是空字串
                result_data["full_markdown"] = sm # 所有文字部分: sm
                md_output_path.write_text(f"# OCR 結果\n\n{sm}", encoding="utf-8") # 寫入 Markdown 檔案
            logger.info(f"奉獻袋摘要: {offering_display}") # 奉獻袋摘要: {offering_display}

        # 輸出 Vision.json（區塊座標與文字）
        vision_data = {
            "task_id": self.context.task_id,    # 任務 ID: self.context.task_id
            "char_count": len(full_text),       # 字元數: len(full_text)
            "full_text": full_text,             # 所有文字部分: full_text
            "textAnnotations": [],              # 文字註解: []
            "blocks": [],                       # 區塊: []
            "coordinate_note": "座標已互換：x = Vision API 的 y，y = Vision API 的 x，width = Vision API 的 height，height = Vision API 的 width", # 座標註解: "座標已互換：x = Vision API 的 y，y = Vision API 的 x，width = Vision API 的 height，height = Vision API 的 width"
        }

        # 合併所有頁面的區塊資料
        all_blocks = [] # 所有區塊: all_blocks
        all_texts = []  # 所有文字: all_texts
        block_index = 0 # 區塊索引: block_index
        for page_result in ocr_results["page_results"]: # 遍歷所有頁面結果
            page_blocks = page_result.get("text_blocks", []) # 頁面區塊: page_blocks
            for b in page_blocks: # 遍歷所有頁面區塊
                # 重新編號索引
                block_copy = b.copy() # 複製區塊
                block_copy["index"] = block_index # 重新編號索引
                all_blocks.append(block_copy) # 添加到所有區塊
                all_texts.append(b.get("text", "")) # 添加到所有文字
                block_index += 1 # 區塊索引加1


        # ── PP-DocLayoutV3 空間排序 ─────────────────────────────────────────────
        # 若版面分析成功，依區域位置重新排序 blocks 並重新賦予連續 index；
        # 否則維持 Vision API 原始順序（donation_rules 仍能正常運作）
        layout_applied = False
        if layout_regions:
            try:
                sorted_blocks = reorder_blocks_by_layout(all_blocks, layout_regions)
                if sorted_blocks and len(sorted_blocks) == len(all_blocks):
                    all_blocks = sorted_blocks
                    all_texts = [b.get("text", "") for b in all_blocks]
                    layout_applied = True
                    logger.info(
                        "[%s] 版面空間排序完成：%d blocks 已重新排序",
                        self.context.task_id, len(all_blocks),
                    )
            except Exception as e:
                logger.warning(
                    "[%s] 版面空間排序失敗：%s，使用原始 block 順序",
                    self.context.task_id, e,
                )
        # ────────────────────────────────────────────────────────────────────────

        # 合併所有頁面的區塊資料到 Vision.json =============================================================
        vision_data["blocks"] = all_blocks # 區塊: all_blocks
        vision_data["textAnnotations"] = all_texts # 文字註解: all_texts
        vision_data["block_count"] = len(all_blocks) # 區塊數: len(all_blocks)
        vision_data["layout_sort_applied"] = layout_applied # 是否有套用版面排序

        # 自訂 Vision.json 格式：blocks 每筆一行，textAnnotations 每 50 字換行
        def format_vision_json(data: dict) -> str: # 格式化 Vision.json: format_vision_json
            lines = ["{"] # 行列表: lines

            # task_id, char_count, block_count
            lines.append(f'  "task_id": {json.dumps(data["task_id"], ensure_ascii=False)},') # 任務 ID: data["task_id"]
            lines.append(f'  "char_count": {data["char_count"]},') # 字元數: data["char_count"]
            lines.append(f'  "block_count": {data["block_count"]},') # 區塊數: data["block_count"]

            # full_text
            lines.append(f'  "full_text": {json.dumps(data["full_text"], ensure_ascii=False)},') # 所有文字部分: data["full_text"]

            # textAnnotations - 每 50 字換行
            lines.append('  "textAnnotations": [')
            texts = data["textAnnotations"] # 文字註解: data["textAnnotations"]
            if texts:
                current_line = "    " # 當前行: current_line
                for i, t in enumerate(texts):
                    item = json.dumps(t, ensure_ascii=False) # 項目: json.dumps(t, ensure_ascii=False)
                    if i < len(texts) - 1:
                        item += ", " # 項目加逗號
                    if len(current_line) + len(item) > 80:
                        lines.append(current_line) # 添加當前行
                        current_line = "    " + item # 當前行加項目
                    else:
                        current_line += item # 當前行加項目
                if current_line.strip():
                    lines.append(current_line) # 添加當前行
            lines.append('  ],') # 添加文字註解

            # blocks - 每筆一行
            lines.append('  "blocks": [') # 添加區塊
            for i, block in enumerate(data["blocks"]):
                block_json = json.dumps(block, ensure_ascii=False) # 區塊: json.dumps(block, ensure_ascii=False)
                comma = "," if i < len(data["blocks"]) - 1 else "" # 逗號: "," if i < len(data["blocks"]) - 1 else ""
                lines.append(f"    {block_json}{comma}") # 添加區塊
            lines.append('  ],') # 添加區塊

            # 座標註解
            lines.append(f'  "coordinate_note": {json.dumps(data["coordinate_note"], ensure_ascii=False)}') 
            lines.append("}")

            return "\n".join(lines) # 返回格式化後的 Vision.json: "\n".join(lines)

        vision_output_path = output_dir / "Vision.json" # Vision.json 輸出路徑: vision_output_path
        vision_output_path.write_text(
            format_vision_json(vision_data), # 格式化 Vision.json: format_vision_json(vision_data)
            encoding="utf-8"
        )
        output_files.append(str(vision_output_path))
        logger.info(f"===== 已儲存 Vision.json: {vision_output_path} ======")


        # 捐獻袋 OCR 規則處理 =============================================================================
        # 套用捐獻袋 OCR 規則處理（不需要指定 form_template，自動判斷內容是否為奉獻袋格式）
        donation_rules_result = None # 捐獻袋 OCR 規則處理結果: donation_rules_result
        # 如果應用愛盟奉獻袋規則，且有區塊
        if should_apply_lovenet_offering_rules(full_text, form_template=form_template) and all_blocks: # 如果應用愛盟奉獻袋規則，且有區塊
            logger.info(f"[{self.context.task_id}] 開始套用捐獻袋 OCR 規則處理") # 開始套用捐獻袋 OCR 規則處理
            try:
                # 傳遞 output_dir 以生成 Process.md 和 output.md
                donation_rules_result = process_donation_ocr(all_blocks, output_dir=output_dir) # 捐獻袋 OCR 13階段規則處理結果: donation_rules_result
                result_data["donation_rules"] = donation_rules_result # 捐獻袋 OCR 規則處理結果: donation_rules_result

                # ── PaddleOCR 姓名欄位 re-OCR ────────────────────────────────────
                # 若 donation_rules 找到了姓名，用 PaddleOCR 對姓名區域做二次辨識
                # 以修正 Google Vision 手寫誤讀（如「張嫚玲」→「張嫚嫚玩」）
                donor_name_info = donation_rules_result.get("donate_no", {}).get("Donor_Name")
                if donor_name_info and donor_name_info.get("found"):
                    ocr_image = (
                        preprocess_result.orange_removed_path if preprocess_result else None
                    )
                    if ocr_image:
                        paddle_name = reocr_donor_name(
                            image_path=ocr_image,
                            all_blocks=all_blocks,
                            donor_name_info=donor_name_info,
                        )
                        if paddle_name:
                            old_name = donor_name_info.get("name", "")
                            if paddle_name != old_name:
                                logger.info(
                                    "[PaddleNameOCR] 姓名修正：%r → %r",
                                    old_name, paddle_name,
                                )
                                # 更新 donate_no 與 output_text 中的姓名
                                donation_rules_result["donate_no"]["Donor_Name"]["name"] = paddle_name
                                donation_rules_result["donate_no"]["Donor_Name"]["paddle_corrected"] = True
                                # 同步更新 output_text（將舊姓名整行替換）
                                old_txt = donation_rules_result.get("output_text", "")
                                if old_name and old_name in old_txt:
                                    donation_rules_result["output_text"] = old_txt.replace(
                                        old_name, paddle_name, 1
                                    )
                            else:
                                logger.info("[PaddleNameOCR] PaddleOCR 結果與規則引擎一致：%r", paddle_name)
                # ────────────────────────────────────────────────────────────────

                # 如果有輸出文字，先嘗試 LLM 糾正，再更新 full_markdown
                donation_output = donation_rules_result.get("output_text", "") # 捐獻袋 OCR 辨識結果: donation_output
                if donation_output:
                    # LLM 後處理糾正（若未設定 OPENAI_API_KEY 則靜默跳過）
                    llm_corrected = await correct_donation_output(donation_output)
                    if llm_corrected and llm_corrected.strip():
                        logger.info("[LLM糾正] 使用 LLM 糾正後的輸出")
                        donation_output = llm_corrected
                        donation_rules_result["output_text"] = llm_corrected
                        donation_rules_result["llm_corrected"] = True
                    else:
                        logger.info("[LLM糾正] 未啟用或失敗，使用規則引擎原始輸出")
                        donation_rules_result["llm_corrected"] = False

                    result_data["donation_output"] = donation_output # 捐獻袋 OCR 辨識結果: donation_output
                    logger.info("=" * 60)
                    logger.info("===== 捐獻袋 OCR 辨識結果（回傳 APP）=====") # 捐獻袋 OCR 辨識結果（回傳 APP）====="
                    logger.info("=" * 60) # "=" * 60
                    for line in donation_output.split("\n"): # 遍歷捐獻袋 OCR 辨識結果
                        logger.info(f">>> {line}")
                    logger.info("=" * 60)

                # 儲存 temp.json（捐獻項目結構化資料）
                temp_json_path = output_dir / "temp.json" # temp.json 輸出路徑: temp_json_path
                temp_json_path.write_text(
                    json.dumps(donation_rules_result.get("donate_no", {}), ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                output_files.append(str(temp_json_path)) # 添加 temp.json 到輸出檔案列表
                logger.info(f"已儲存 temp.json: {temp_json_path}")

            except Exception as e:
                logger.error(f"捐獻袋 OCR 規則處理失敗: {e}")
                result_data["donation_rules_error"] = str(e)


        # 輸出 result.json（在所有處理完成後）===============================================================
        # 自訂格式：text_blocks 每筆一行
        def format_result_json(data: dict) -> str:
            """格式化 result.json，讓 text_blocks 每筆一行顯示"""
            def format_value(value, indent_level=1, in_text_blocks=False):  # 格式化值: format_value
                indent = "  " * indent_level # 縮排: "  " * indent_level
                if isinstance(value, dict): # 如果值是字典
                    if not value: # 如果值為空
                        return "{}" # 返回空字典
                    lines = ["{"] # 行列表: ["{"]
                    items = list(value.items()) # 項目列表: list(value.items())
                    for i, (k, v) in enumerate(items): # 遍歷項目列表
                        comma = "," if i < len(items) - 1 else "" # 逗號: "," if i < len(items) - 1 else ""
                        # 檢查是否進入 text_blocks
                        is_text_blocks = (k == "text_blocks") # 是否進入 text_blocks: (k == "text_blocks")
                        formatted_v = format_value(v, indent_level + 1, is_text_blocks) # 格式化值: format_value(v, indent_level + 1, is_text_blocks)
                        if isinstance(v, (dict, list)) and v: # 如果值是字典或列表
                            lines.append(f'{indent}  "{k}": {formatted_v}{comma}') # 添加項目
                        else:
                            lines.append(f'{indent}  "{k}": {formatted_v}{comma}') # 添加項目
                    lines.append(f"{indent}}}")
                    return "\n".join(lines)  # 返回格式化後的 dict
                elif isinstance(value, list):  # 如果值是列表
                    if not value:
                        return "[]"
                    # 如果是 text_blocks，每筆一行
                    if in_text_blocks and all(isinstance(item, dict) for item in value): # 如果進入 text_blocks，且所有項目都是字典
                        lines = ["["] # 行列表: ["["]
                        for i, item in enumerate(value):
                            comma = "," if i < len(value) - 1 else "" # 逗號: "," if i < len(value) - 1 else ""
                            item_json = json.dumps(item, ensure_ascii=False) # 項目: json.dumps(item, ensure_ascii=False)
                            lines.append(f"{indent}  {item_json}{comma}") # 添加項目
                        lines.append(f"{indent}]") # 添加項目
                        return "\n".join(lines) # 返回格式化後的 result.json: "\n".join(lines)
                    else:
                        # 一般陣列處理
                        lines = ["["] # 行列表: ["["]
                        for i, item in enumerate(value): # 遍歷項目列表
                            comma = "," if i < len(value) - 1 else "" # 逗號: "," if i < len(value) - 1 else ""
                            formatted_item = format_value(item, indent_level + 1, False) # 格式化值: format_value(item, indent_level + 1, False)
                            lines.append(f"{indent}  {formatted_item}{comma}") # 添加項目
                        lines.append(f"{indent}]") # 添加項目
                        return "\n".join(lines) # 返回格式化後的 result.json: "\n".join(lines)
                else:
                    return json.dumps(value, ensure_ascii=False) # 返回格式化後的 result.json: json.dumps(value, ensure_ascii=False)

            return format_value(data, 0) # 返回格式化後的 result.json: format_value(data, 0)

        json_output_path = output_dir / "result.json" # result.json 輸出路徑: json_output_path
        json_output_path.write_text(            # 寫入 result.json 檔案
            format_result_json(result_data),    # 格式化 result.json: format_result_json(result_data)
            encoding="utf-8"
        )
        output_files.append(str(json_output_path)) # 添加 result.json 到輸出檔案列表
        logger.info(f"已儲存 result.json: {json_output_path}")


        # 更新進度
        await self.update_progress(
            step_name=step_name,
            progress=100.0,
            overall_progress=100.0,
            message="結果輸出完成",
        )

        # 準備回傳資料
        return_data = {
            "md_output_path": str(md_output_path),  # Markdown 輸出路徑: str(md_output_path)
            "json_output_path": str(json_output_path), # JSON 輸出路徑: str(json_output_path)
            "output_files": output_files, # 輸出檔案列表: output_files
            "metadata": {
                "ocr_engine": "Google Cloud Vision API", # OCR 引擎: "Google Cloud Vision API"
                "total_pages": ocr_results["total_pages"], # 總圖片數: ocr_results["total_pages"]
                "total_chars": len(full_text), # 總字元數: len(full_text)
                "has_preprocessing": preprocess_result is not None,
            }, # 檔案元數據: metadata   
        }

        # 如果有捐獻規則輸出，加入回傳資料
        if donation_rules_result:
            return_data["donation_output"] = donation_rules_result.get("output_text", "") # 捐獻袋 OCR 辨識結果: donation_rules_result.get("output_text", "")
            return_data["donation_rules"] = donation_rules_result # 捐獻袋 OCR 規則處理結果: donation_rules_result

        return return_data # 返回資料: return_data
