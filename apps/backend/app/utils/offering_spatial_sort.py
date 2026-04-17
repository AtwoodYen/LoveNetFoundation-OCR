"""
奉獻袋 OCR Blocks 空間排序模組

問題根源：Google Vision API 回傳的 block index 順序，
並不等於閱讀順序（尤其是手寫內容的 block 可能比其對應的印刷標籤更早出現）。

解法：用 PP-DocLayoutV3 偵測的版面區域（LayoutRegion）重新排列 blocks，
確保每個區域內的 blocks 按正確閱讀順序排列，並重新賦予連續的 index。

座標系對應說明
==============
Google Vision API blocks（座標已在 google_vision_flow.py 互換）：
  block["x"]      = Vision_y  = 圖片垂直位置（上→下）
  block["y"]      = Vision_x  = 圖片水平位置（左→右）
  block["width"]  = Vision_h  = 垂直方向大小
  block["height"] = Vision_w  = 水平方向大小

PP-DocLayoutV3 LayoutRegion.bbox = (x1, y1, x2, y2)：
  x1, x2 = 水平邊界（圖片 x 軸，左→右）
  y1, y2 = 垂直邊界（圖片 y 軸，上→下）

因此，block 中心點在圖片座標中：
  image_cx = block["y"] + block["height"] / 2   （水平中心）
  image_cy = block["x"] + block["width"]  / 2   （垂直中心）

Block 在 region 內的條件：
  x1 - margin <= image_cx <= x2 + margin
  y1 - margin <= image_cy <= y2 + margin
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.utils.logger import logger

# PP-DocLayoutV3 labels 分組
# 與閱讀正文相關的 labels（用於決定區域內排序方向）
_CONTENT_LABELS = {
    "text", "content", "paragraph_title", "doc_title", "aside_text",
    "abstract", "reference", "reference_content", "footnote", "footer",
    "header", "number", "vertical_text",
}

_IGNORE_LABELS = {
    "image", "figure_title", "chart",   # 圖片類不需要排序
    "seal",                              # 印章
}


def _block_image_center(block: Dict[str, Any]) -> Tuple[float, float]:
    """
    回傳 block 在原始圖片座標系中的中心點 (image_cx, image_cy)。

    block["y"] + block["height"]/2 = 水平中心（image x）
    block["x"] + block["width"] /2 = 垂直中心（image y）
    """
    image_cx = block.get("y", 0) + block.get("height", 0) / 2.0
    image_cy = block.get("x", 0) + block.get("width", 0) / 2.0
    return image_cx, image_cy


def _assign_blocks_to_regions(
    blocks: List[Dict[str, Any]],
    regions,  # List[LayoutRegion]
    margin: int = 30,
) -> Tuple[Dict[int, List[Dict]], List[Dict]]:
    """
    將每個 block 指派給包含其中心點的 LayoutRegion。

    Args:
        blocks:  Vision API 的 all_blocks（已互換座標）
        regions: PP-DocLayoutV3 偵測到的 LayoutRegion 列表（已按閱讀順序排序）
        margin:  超出 region 邊界的容錯像素

    Returns:
        (region_blocks, unassigned_blocks)
        region_blocks: {region_index: [block, ...]}（每個 region 的 blocks）
        unassigned_blocks: 未落入任何 region 的 blocks
    """
    from app.utils.pp_layout_analyzer import LayoutRegion

    region_blocks: Dict[int, List[Dict]] = {r.region_index: [] for r in regions}
    unassigned: List[Dict] = []

    for block in blocks:
        image_cx, image_cy = _block_image_center(block)
        assigned = False

        for region in regions:
            if region.label in _IGNORE_LABELS:
                continue
            if region.contains_point(image_cx, image_cy, margin=margin):
                region_blocks[region.region_index].append(block)
                assigned = True
                break

        if not assigned:
            unassigned.append(block)

    return region_blocks, unassigned


def _sort_blocks_in_region(
    blocks: List[Dict[str, Any]],
    is_rtl: bool = True,
) -> List[Dict[str, Any]]:
    """
    對同一 region 內的 blocks 按閱讀順序排序。

    排序邏輯（奉獻袋直式表單）：
      主要：block["x"] 升序（垂直位置上→下）
      次要：block["y"] 降序（水平位置右→左，中文慣例）

    is_rtl: True = 右→左（預設，中文）；False = 左→右
    """
    if not blocks:
        return blocks

    def sort_key(b: Dict[str, Any]):
        vert = b.get("x", 0)    # 垂直位置（上→下）
        horiz = b.get("y", 0)   # 水平位置（左→右）
        return (vert, -horiz if is_rtl else horiz)

    return sorted(blocks, key=sort_key)


def reorder_blocks_by_layout(
    blocks: List[Dict[str, Any]],
    regions,  # Optional[List[LayoutRegion]] 但避免 circular import 不寫 type hint
    margin: int = 30,
    reassign_indices: bool = True,
) -> List[Dict[str, Any]]:
    """
    利用 PP-DocLayoutV3 版面區域，對 Vision API blocks 重新排序並重新編號。

    流程：
    1. 將每個 block 指派給包含它的 LayoutRegion
    2. 按 LayoutRegion.region_index（閱讀順序）輸出各 region 的 blocks
    3. 每個 region 內部按 (垂直上→下, 水平右→左) 排序
    4. 未指派的 blocks 依同樣規則排序後附在最後
    5. 重新賦予連續的 index（donation_rules 依賴 index 大小做範圍判斷）

    Args:
        blocks:            Vision API 的 all_blocks（已互換座標）
        regions:           PP-DocLayoutV3 的 LayoutRegion 列表，已按閱讀順序排序；
                           若為 None 或空列表，直接回傳原始 blocks（不改動）
        margin:            區域邊界容錯像素
        reassign_indices:  是否重新賦予連續 index（強烈建議 True）

    Returns:
        重新排序且重新編號的 blocks 列表
    """
    if not regions:
        logger.debug("[SpatialSort] 無版面區域資訊，回傳原始 blocks")
        return blocks

    if not blocks:
        return blocks

    logger.info(
        "[SpatialSort] 開始排序：%d blocks × %d regions",
        len(blocks),
        len(regions),
    )

    # 1. 指派 blocks 到各 region
    region_blocks_map, unassigned = _assign_blocks_to_regions(blocks, regions, margin=margin)

    logger.info(
        "[SpatialSort] 指派結果：%d blocks 已指派，%d blocks 未指派",
        sum(len(v) for v in region_blocks_map.values()),
        len(unassigned),
    )

    # 2. 依 region 閱讀順序組合已排序的 blocks
    sorted_blocks: List[Dict[str, Any]] = []
    for region in regions:
        if region.label in _IGNORE_LABELS:
            continue
        rb = region_blocks_map.get(region.region_index, [])
        if not rb:
            continue
        sorted_rb = _sort_blocks_in_region(rb)
        sorted_blocks.extend(sorted_rb)
        logger.debug(
            "[SpatialSort] Region[%d] %-20s → %d blocks",
            region.region_index, region.label, len(sorted_rb),
        )

    # 3. 未指派的 blocks 依空間順序附加到最後
    if unassigned:
        sorted_unassigned = _sort_blocks_in_region(unassigned)
        sorted_blocks.extend(sorted_unassigned)
        logger.debug("[SpatialSort] 未指派 blocks: %d 個（附加至末尾）", len(unassigned))

    # 4. 重新賦予連續 index
    if reassign_indices:
        for new_idx, block in enumerate(sorted_blocks):
            block = block.copy()
            block["index"] = new_idx
            sorted_blocks[new_idx] = block

    # 5. 安全檢查：確保沒有 block 遺失
    if len(sorted_blocks) != len(blocks):
        logger.error(
            "[SpatialSort] ⚠️  排序後 block 數量不一致（原 %d → 排序後 %d），回傳原始 blocks",
            len(blocks),
            len(sorted_blocks),
        )
        return blocks

    logger.info("[SpatialSort] 排序完成：%d blocks", len(sorted_blocks))
    return sorted_blocks
