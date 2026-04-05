"""
Google Cloud Vision OCR 服務

使用 Google Cloud Vision API 進行 OCR 辨識
"""

import base64
import httpx
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from app.utils.logger import logger
from app.utils.config import settings


@dataclass
class TextBlock:
    """OCR 辨識的文字區塊"""
    text: str
    bbox: tuple  # (x, y, width, height)
    confidence: float
    vertices: List[tuple]  # 四個頂點座標 [(x1,y1), (x2,y2), (x3,y3), (x4,y4)]


@dataclass
class GoogleVisionOCRResult:
    """Google Vision OCR 結果"""
    text: str
    pages: List[Dict[str, Any]]
    raw_response: Dict[str, Any]
    text_blocks: List[TextBlock] = None  # 文字區塊列表（含座標）

    def __post_init__(self):
        if self.text_blocks is None:
            self.text_blocks = []


class GoogleVisionOCRError(Exception):
    """Google Vision OCR 錯誤"""
    pass


def extract_text_blocks_from_response(response: Dict[str, Any]) -> List[TextBlock]:
    """
    從 Google Vision API 回應中提取文字區塊

    Args:
        response: API 原始回應

    Returns:
        List[TextBlock]: 文字區塊列表
    """
    blocks = []

    responses = response.get("responses", [])
    if not responses:
        return blocks

    first_response = responses[0]

    # 使用 textAnnotations（第一個是全文，之後是各個單詞/區塊）
    text_annotations = first_response.get("textAnnotations", [])

    # 跳過第一個（全文）
    for annotation in text_annotations[1:]:
        text = annotation.get("description", "")
        bounding_poly = annotation.get("boundingPoly", {})
        vertices = bounding_poly.get("vertices", [])

        if not vertices or len(vertices) < 4:
            continue

        # 提取頂點座標
        vertex_coords = []
        for v in vertices:
            x = v.get("x", 0)
            y = v.get("y", 0)
            vertex_coords.append((x, y))

        # 計算 bounding box (x, y, width, height)
        xs = [v[0] for v in vertex_coords]
        ys = [v[1] for v in vertex_coords]
        x = min(xs)
        y = min(ys)
        width = max(xs) - x
        height = max(ys) - y

        blocks.append(TextBlock(
            text=text,
            bbox=(x, y, width, height),
            confidence=annotation.get("confidence", 1.0),
            vertices=vertex_coords
        ))

    return blocks


def extract_word_blocks_from_fulltext(response: Dict[str, Any]) -> List[TextBlock]:
    """
    從 fullTextAnnotation 中提取更詳細的文字區塊（按單詞）

    Args:
        response: API 原始回應

    Returns:
        List[TextBlock]: 文字區塊列表
    """
    blocks = []

    responses = response.get("responses", [])
    if not responses:
        return blocks

    first_response = responses[0]
    full_text = first_response.get("fullTextAnnotation", {})
    pages = full_text.get("pages", [])

    for page in pages:
        for block in page.get("blocks", []):
            for paragraph in block.get("paragraphs", []):
                for word in paragraph.get("words", []):
                    # 組合單詞的符號
                    word_text = ""
                    for symbol in word.get("symbols", []):
                        word_text += symbol.get("text", "")

                    bounding_box = word.get("boundingBox", {})
                    vertices = bounding_box.get("vertices", [])

                    if not vertices or len(vertices) < 4:
                        continue

                    vertex_coords = []
                    for v in vertices:
                        x = v.get("x", 0)
                        y = v.get("y", 0)
                        vertex_coords.append((x, y))

                    xs = [v[0] for v in vertex_coords]
                    ys = [v[1] for v in vertex_coords]
                    x = min(xs)
                    y = min(ys)
                    width = max(xs) - x
                    height = max(ys) - y

                    confidence = word.get("confidence", 1.0)

                    blocks.append(TextBlock(
                        text=word_text,
                        bbox=(x, y, width, height),
                        confidence=confidence,
                        vertices=vertex_coords
                    ))

    return blocks


async def ocr_with_google_vision(
    image_path: str,
    language_hints: Optional[List[str]] = None,
) -> GoogleVisionOCRResult:
    """
    使用 Google Cloud Vision API 進行 OCR

    Args:
        image_path: 圖片路徑
        language_hints: 語言提示 (如 ["zh-TW", "zh-CN", "en"])

    Returns:
        GoogleVisionOCRResult: OCR 結果
    """
    api_key = settings.GOOGLE_VISION_API_KEY
    if not api_key:
        raise GoogleVisionOCRError(
            "未設定 GOOGLE_VISION_API_KEY。請在 .env 中設定。"
        )

    # 讀取圖片並 Base64 編碼
    image_path = Path(image_path)
    if not image_path.exists():
        raise GoogleVisionOCRError(f"圖片不存在: {image_path}")

    with open(image_path, "rb") as f:
        image_content = base64.b64encode(f.read()).decode("utf-8")

    # 構建請求
    url = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"

    # 設定語言提示
    if language_hints is None:
        language_hints = ["zh-TW", "zh-CN", "en"]

    request_body = {
        "requests": [
            {
                "image": {"content": image_content},
                "features": [
                    {"type": "DOCUMENT_TEXT_DETECTION"}
                ],
                "imageContext": {
                    "languageHints": language_hints
                }
            }
        ]
    }

    logger.info(f"呼叫 Google Vision API: {image_path.name}")

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(url, json=request_body)
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Google Vision API HTTP 錯誤: {e.response.status_code}")
            raise GoogleVisionOCRError(f"API 請求失敗: {e.response.status_code}")
        except httpx.RequestError as e:
            logger.error(f"Google Vision API 連線錯誤: {e}")
            raise GoogleVisionOCRError(f"連線錯誤: {e}")

    # 解析結果
    responses = result.get("responses", [])
    if not responses:
        return GoogleVisionOCRResult(text="", pages=[], raw_response=result)

    first_response = responses[0]

    # 檢查錯誤
    if "error" in first_response:
        error = first_response["error"]
        raise GoogleVisionOCRError(
            f"API 錯誤: {error.get('message', 'Unknown error')}"
        )

    # 提取全文
    full_text_annotation = first_response.get("fullTextAnnotation", {})
    text = full_text_annotation.get("text", "")

    # 提取頁面資訊
    pages = full_text_annotation.get("pages", [])

    # 提取文字區塊（含座標）
    text_blocks = extract_text_blocks_from_response(result)

    logger.info(f"Google Vision OCR 完成: {len(text)} 字元, {len(text_blocks)} 個區塊")

    return GoogleVisionOCRResult(
        text=text,
        pages=pages,
        raw_response=result,
        text_blocks=text_blocks
    )


async def ocr_multiple_images(
    image_paths: List[str],
    language_hints: Optional[List[str]] = None,
) -> List[GoogleVisionOCRResult]:
    """
    對多張圖片進行 OCR

    Args:
        image_paths: 圖片路徑列表
        language_hints: 語言提示

    Returns:
        List[GoogleVisionOCRResult]: OCR 結果列表
    """
    results = []
    for path in image_paths:
        try:
            result = await ocr_with_google_vision(path, language_hints)
            results.append(result)
        except GoogleVisionOCRError as e:
            logger.error(f"OCR 失敗 ({path}): {e}")
            # 繼續處理其他圖片
            results.append(GoogleVisionOCRResult(
                text=f"[OCR 失敗: {e}]",
                pages=[],
                raw_response={}
            ))
    return results


async def ocr_with_blocks(
    image_path: str,
    language_hints: Optional[List[str]] = None,
) -> List[TextBlock]:
    """
    使用 Google Vision API 進行 OCR 並返回文字區塊列表

    這是給 image_preprocessing 模組使用的封裝函數

    Args:
        image_path: 圖片路徑
        language_hints: 語言提示

    Returns:
        List[TextBlock]: 文字區塊列表
    """
    result = await ocr_with_google_vision(image_path, language_hints)
    return result.text_blocks


async def ocr_image_bytes(
    image_bytes: bytes,
    language_hints: Optional[List[str]] = None,
) -> GoogleVisionOCRResult:
    """
    使用 Google Cloud Vision API 對圖片位元組進行 OCR

    Args:
        image_bytes: 圖片位元組
        language_hints: 語言提示

    Returns:
        GoogleVisionOCRResult: OCR 結果
    """
    api_key = settings.GOOGLE_VISION_API_KEY
    if not api_key:
        raise GoogleVisionOCRError(
            "未設定 GOOGLE_VISION_API_KEY。請在 .env 中設定。"
        )

    image_content = base64.b64encode(image_bytes).decode("utf-8")

    url = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"

    if language_hints is None:
        language_hints = ["zh-TW", "zh-CN", "en"]

    request_body = {
        "requests": [
            {
                "image": {"content": image_content},
                "features": [
                    {"type": "DOCUMENT_TEXT_DETECTION"}
                ],
                "imageContext": {
                    "languageHints": language_hints
                }
            }
        ]
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(url, json=request_body)
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPStatusError as e:
            raise GoogleVisionOCRError(f"API 請求失敗: {e.response.status_code}")
        except httpx.RequestError as e:
            raise GoogleVisionOCRError(f"連線錯誤: {e}")

    responses = result.get("responses", [])
    if not responses:
        return GoogleVisionOCRResult(text="", pages=[], raw_response=result)

    first_response = responses[0]

    if "error" in first_response:
        error = first_response["error"]
        raise GoogleVisionOCRError(
            f"API 錯誤: {error.get('message', 'Unknown error')}"
        )

    full_text_annotation = first_response.get("fullTextAnnotation", {})
    text = full_text_annotation.get("text", "")
    pages = full_text_annotation.get("pages", [])

    return GoogleVisionOCRResult(
        text=text,
        pages=pages,
        raw_response=result
    )
