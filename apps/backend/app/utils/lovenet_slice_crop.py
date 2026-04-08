"""
愛盟奉獻袋「切圖辨識」：依 Google Vision 文字框座標裁切四個區塊。

錨點辨識須設定 GOOGLE_VISION_API_KEY；裁切後各區塊可再送 GLM 或 Vision。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image

from app.utils.google_vision_ocr import TextBlock, ocr_with_google_vision
from app.utils.logger import logger


class SliceCropError(Exception):
    """無法依錨點裁切（缺字或 Vision 失敗）。"""


def _bbox_xyxy(b: TextBlock) -> Tuple[int, int, int, int]:
    x, y, w, h = b.bbox
    return int(x), int(y), int(x + w), int(y + h)


def _union_xyxy(blocks: Sequence[TextBlock]) -> Optional[Tuple[int, int, int, int]]:
    if not blocks:
        return None
    xs1, ys1, xs2, ys2 = [], [], [], []
    for b in blocks:
        x1, y1, x2, y2 = _bbox_xyxy(b)
        xs1.append(x1)
        ys1.append(y1)
        xs2.append(x2)
        ys2.append(y2)
    return min(xs1), min(ys1), max(xs2), max(ys2)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _blocks_hitting_any(blocks: Sequence[TextBlock], needles: Sequence[str]) -> List[TextBlock]:
    out: List[TextBlock] = []
    for b in blocks:
        raw = b.text or ""
        n = _norm(raw)
        for needle in needles:
            if needle in raw or _norm(needle) in n:
                out.append(b)
                break
    return out


def _cluster_blocks_into_lines(
    blocks: Sequence[TextBlock], y_tol: int = 28
) -> List[List[TextBlock]]:
    """將 Vision 小框依垂直位置併成閱讀行（處理長句被拆成多框）。"""
    if not blocks:
        return []
    rows: List[Tuple[int, int, TextBlock]] = []
    for b in blocks:
        x1, y1, x2, y2 = _bbox_xyxy(b)
        cy = (y1 + y2) // 2
        rows.append((cy, x1, b))
    rows.sort(key=lambda t: (t[0], t[1]))
    lines: List[List[TextBlock]] = []
    cur: List[TextBlock] = []
    ref_cy: Optional[int] = None
    for cy, _x1, b in rows:
        if ref_cy is None or abs(cy - ref_cy) <= y_tol:
            cur.append(b)
            if ref_cy is None:
                ref_cy = cy
            else:
                ref_cy = (ref_cy + cy) // 2
        else:
            cur.sort(key=lambda bb: _bbox_xyxy(bb)[0])
            lines.append(cur)
            cur = [b]
            ref_cy = cy
    if cur:
        cur.sort(key=lambda bb: _bbox_xyxy(bb)[0])
        lines.append(cur)
    return lines


def _cluster_blocks_into_paragraphs(
    blocks: Sequence[TextBlock], max_gap: int = 100
) -> List[List[TextBlock]]:
    """將相鄰多行併成一段（聲明長句常被拆成多行小框）。"""
    if not blocks:
        return []
    sorted_b = sorted(blocks, key=lambda b: (_bbox_xyxy(b)[1], _bbox_xyxy(b)[0]))
    paras: List[List[TextBlock]] = []
    cur: List[TextBlock] = []
    group_bottom = -1
    for b in sorted_b:
        _x1, y1, _x2, y2 = _bbox_xyxy(b)
        if not cur:
            cur = [b]
            group_bottom = y2
        elif y1 - group_bottom <= max_gap:
            cur.append(b)
            group_bottom = max(group_bottom, y2)
        else:
            paras.append(cur)
            cur = [b]
            group_bottom = y2
    if cur:
        paras.append(cur)
    return paras


def _vertices_to_xyxy(vertices: Sequence[Dict[str, Any]]) -> Optional[Tuple[int, int, int, int]]:
    if not vertices or len(vertices) < 4:
        return None
    xs = [int(v.get("x", 0)) for v in vertices]
    ys = [int(v.get("y", 0)) for v in vertices]
    return min(xs), min(ys), max(xs), max(ys)


def _union_xyxy_tuple_list(
    boxes: Sequence[Tuple[int, int, int, int]]
) -> Optional[Tuple[int, int, int, int]]:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _word_boxes_from_paragraph(paragraph: Dict[str, Any]) -> List[Tuple[int, int, int, int]]:
    out: List[Tuple[int, int, int, int]] = []
    for word in paragraph.get("words") or []:
        bb = word.get("boundingBox") or {}
        vertices = bb.get("vertices") or []
        xy = _vertices_to_xyxy(vertices)
        if xy:
            out.append(xy)
    return out


def _paragraph_text(paragraph: Dict[str, Any]) -> str:
    parts: List[str] = []
    for word in paragraph.get("words") or []:
        wt = ""
        for symbol in word.get("symbols") or []:
            wt += symbol.get("text") or ""
        parts.append(wt)
    return "".join(parts)


def privacy_declaration_bbox_from_fulltext(
    raw_response: Dict[str, Any],
) -> Optional[Tuple[int, int, int, int]]:
    """
    以 fullTextAnnotation 的段落／區塊聚合文案與座標，避開 textAnnotations 單框過碎無法拼句的問題。
    """
    responses = raw_response.get("responses") or []
    if not responses:
        return None
    full_text = responses[0].get("fullTextAnnotation") or {}
    pages = full_text.get("pages") or []
    best_bbox: Optional[Tuple[int, int, int, int]] = None
    best_score = -1

    def consider(n: str, boxes: List[Tuple[int, int, int, int]]) -> None:
        nonlocal best_bbox, best_score
        if not boxes:
            return
        sc = _score_privacy_declaration_line(n)
        if "揭露" in n or "捐露" in n or "捐璐" in n:
            sc += 2
        if sc < 1:
            return
        u = _union_xyxy_tuple_list(boxes)
        if u is None:
            return
        if sc > best_score:
            best_score = sc
            best_bbox = u

    for page in pages:
        for block in page.get("blocks") or []:
            block_boxes: List[Tuple[int, int, int, int]] = []
            block_chars: List[str] = []
            for paragraph in block.get("paragraphs") or []:
                ptxt = _paragraph_text(paragraph)
                pboxes = _word_boxes_from_paragraph(paragraph)
                block_chars.append(ptxt)
                block_boxes.extend(pboxes)
                pn = _norm(ptxt)
                consider(pn, pboxes)
            bn = _norm("".join(block_chars))
            consider(bn, block_boxes)

    if best_bbox is not None and best_score >= 1:
        return best_bbox
    return None


def _line_norm_text(line_blocks: Sequence[TextBlock]) -> str:
    return _norm("".join((b.text or "") for b in line_blocks))


def _score_privacy_declaration_line(n: str) -> int:
    """長句常被拆行／錯字，用加權分數找聲明列。"""
    s = 0
    if "公開揭露" in n or "公開捐露" in n or "公開捐璐" in n:
        s += 4
    if "捐款不公開" in n or "不公開聲明" in n:
        s += 3
    if "不同意" in n or "不周意" in n or "不回意" in n or "不同周意" in n:
        s += 2
    if "本人在此聲明" in n or ("本人在此" in n and "聲明" in n):
        s += 2
    if "第25條" in n or "第25 條" in n:
        s += 2
    if "財團法人法" in n:
        s += 1
    if "捐款" in n and "姓名" in n:
        s += 1
    if "捐款者" in n and "姓名" in n:
        s += 1
    if "將本人" in n or "本人捐款" in n:
        s += 1
    if "揭露" in n or "捐露" in n:
        s += 1
    return s


def _blocks_for_privacy_declaration(blocks: Sequence[TextBlock]) -> List[TextBlock]:
    lines = _cluster_blocks_into_lines(blocks)
    best_blocks: Optional[List[TextBlock]] = None
    best_score = 0
    for lb in lines:
        n = _line_norm_text(lb)
        sc = _score_privacy_declaration_line(n)
        if sc > best_score:
            best_score = sc
            best_blocks = list(lb)
    if best_score >= 2:
        return best_blocks or []
    # 次佳：任一小框含強關鍵字
    strong = _blocks_hitting_any(
        blocks,
        (
            "公開揭露",
            "公開捐露",
            "捐款不公開",
            "本人在此聲明",
            "不同意將本人",
            "不同意將",
        ),
    )
    if strong:
        return strong
    # 兩框各含一半：一框「公開」一框「揭露」且同一行
    for lb in lines:
        n = _line_norm_text(lb)
        if "公開" in n and "揭露" in n and len(n) >= 6:
            return list(lb)
    # 垂直多行併段（textAnnotations 小框過碎時，同行合併仍拼不出整句）
    for para in _cluster_blocks_into_paragraphs(blocks, max_gap=100):
        n = _line_norm_text(para)
        sc = _score_privacy_declaration_line(n)
        if sc >= 2:
            return list(para)
        if sc >= 1 and (
            "揭露" in n
            or "捐露" in n
            or "聲明" in n
            or "第25條" in n
            or "第25 條" in n
        ):
            return list(para)
    return []


def _blocks_for_budget_header(blocks: Sequence[TextBlock]) -> List[TextBlock]:
    """課程推廣列：整行合併後比對，並接受子字串／錯字。"""
    needles = (
        "課程推廣與發展",
        "課程推廣",
        "推廣與發展",
        "課程推廣與發",
        "媒體製作與傳播",  # 同表下一列，可當表頭區參考
    )
    hit = _blocks_hitting_any(blocks, needles)
    if hit:
        return hit
    for lb in _cluster_blocks_into_lines(blocks):
        n = _line_norm_text(lb)
        if "課程" in n and "推廣" in n:
            return list(lb)
        if "推廣" in n and "發展" in n:
            return list(lb)
    return []


def _blocks_for_receipt_options(blocks: Sequence[TextBlock]) -> List[TextBlock]:
    b_tax = _blocks_hitting_any(
        blocks,
        (
            "代上傳國稅局無紙本",
            "代上傅國稅局無紙本",
            "代上傳國税局",
            "國稅局無紙本",
            "無紙本",
            "上傳國稅局",
        ),
    )
    b_e = _blocks_hitting_any(blocks, ("電子收據", "電子 收據", "電子收據。"))
    b_paper = _blocks_hitting_any(
        blocks, ("年度紙本收據", "年度纸本收據", "紙本收據", "年度紙本")
    )
    return b_tax + b_e + b_paper


@dataclass
class LovnetSliceRects:
    """四區裁切座標 (left, top, right, bottom)，像素，含邊界。"""

    block1: Tuple[int, int, int, int]
    block2: Tuple[int, int, int, int]
    block3: Tuple[int, int, int, int]
    block4: Tuple[int, int, int, int]


def _clamp_slice_box(
    box: Tuple[int, int, int, int], iw: int, ih: int
) -> Tuple[int, int, int, int]:
    left, top, right, bottom = box
    left = max(0, min(left, iw - 1))
    top = max(0, min(top, ih - 1))
    right = max(left + 1, min(right, iw))
    bottom = max(top + 1, min(bottom, ih))
    return left, top, right, bottom


def write_one_slice_png(
    img: Image.Image,
    output_dir: Path,
    index: int,
    box: Tuple[int, int, int, int],
) -> str:
    """裁切單一區塊並立即寫入磁碟，回傳絕對路徑。"""
    iw, ih = img.size
    left, top, right, bottom = _clamp_slice_box(box, iw, ih)
    crop = img.crop((left, top, right, bottom))
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"Block_{index}.png"
    p = output_dir / name
    crop.save(str(p), format="PNG")
    logger.info("切圖已儲存 %s (%s,%s)-(%s,%s)", name, left, top, right, bottom)
    return str(p.resolve())


def _compute_block1_rect(
    blocks: Sequence[TextBlock],
    w: int,
    h: int,
    *,
    block1_top_pad: int,
    block1_bottom_pad: int,
) -> Tuple[int, int, int, int]:
    b_course = _blocks_for_budget_header(blocks)
    b_heji = _blocks_hitting_any(blocks, ("合計", "合 計", "合計金額"))
    u_course = _union_xyxy(b_course)
    u_heji = _union_xyxy(b_heji)
    if u_course:
        y1 = max(0, u_course[1] - block1_top_pad)
    else:
        y1 = 0
        logger.warning("切圖：未找到「課程推廣與發展」，Block_1 從頂端開始")
    if u_heji:
        y2 = min(h, u_heji[3] + block1_bottom_pad)
    else:
        y2 = min(h, max(1, int(h * 0.42)))
        logger.warning("切圖：未找到「合計」，Block_1 使用預設高度比例")
    return (0, y1, w, y2)


def _compute_block2_rect(
    blocks: Sequence[TextBlock],
    w: int,
    h: int,
    *,
    margin: int,
    raw_response: Optional[Dict[str, Any]],
) -> Tuple[int, int, int, int]:
    b_decl = _blocks_for_privacy_declaration(blocks)
    opts = _blocks_for_receipt_options(blocks)
    u_decl = _union_xyxy(b_decl)
    if not u_decl and raw_response:
        u_decl = privacy_declaration_bbox_from_fulltext(raw_response)
        if u_decl:
            logger.info("切圖：聲明列改以 fullTextAnnotation 段落座標為錨點")
    u_opts = _union_xyxy(opts)
    if not u_decl:
        raise SliceCropError("找不到「本人…公開揭露」聲明列（已嘗試合併同行文字與錯字變體），無法裁切 Block_2")
    if not u_opts:
        y_fallback = min(h, u_decl[3] + max(margin * 4, int(h * 0.12)))
        logger.warning(
            "切圖：未完整辨識收據三選項，Block_2 下緣改為聲明列下方 %.0f%% 圖高",
            100.0 * (y_fallback - u_decl[1]) / max(h, 1),
        )
        y1 = max(0, u_decl[1] - margin)
        y2 = y_fallback
    else:
        y1 = max(0, u_decl[1] - margin)
        y2 = min(h, u_opts[3] + margin)
    return (0, y1, w, y2)


def _compute_block3_rect(
    blocks: Sequence[TextBlock],
    w: int,
    h: int,
    *,
    margin: int,
) -> Tuple[int, int, int, int]:
    b_id = _blocks_hitting_any(blocks, ("身分證字號", "身份證字號", "身分分證字號"))
    b_title = _blocks_hitting_any(blocks, ("奉獻收據抬頭", "收據抬頭"))
    u_id = _union_xyxy(b_id)
    u_title = _union_xyxy(b_title)
    if not u_id:
        raise SliceCropError("找不到「身分證字號」錨點，無法裁切 Block_3")
    if not u_title:
        raise SliceCropError("找不到「奉獻收據抬頭」錨點，無法裁切 Block_3")
    y1 = max(0, u_id[1] - margin)
    y2 = min(h, u_title[3] + margin)
    return (0, y1, w, y2)


def _compute_block4_rect(
    blocks: Sequence[TextBlock],
    w: int,
    h: int,
    *,
    margin: int,
) -> Tuple[int, int, int, int]:
    b_addr = _blocks_hitting_any(
        blocks,
        ("奉獻收據寄送地址", "收據寄送地址", "郵寄地址", "奉献收據寄送地址"),
    )
    u_addr = _union_xyxy(b_addr)
    if not u_addr:
        raise SliceCropError("找不到「奉獻收據寄送地址」錨點，無法裁切 Block_4")
    y1 = max(0, u_addr[1] - margin)
    y2 = h
    return (0, y1, w, y2)


def compute_lovenet_slice_rects(
    blocks: Sequence[TextBlock],
    image_width: int,
    image_height: int,
    *,
    margin: int = 10,
    block1_top_pad: int = 40,
    block1_bottom_pad: int = 12,
    raw_response: Optional[Dict[str, Any]] = None,
    incremental_save: Optional[Tuple[str, str]] = None,
) -> Tuple[LovnetSliceRects, Dict[str, str]]:
    """
    依 Vision textAnnotations 區塊計算四張切圖矩形。

    若傳入 incremental_save=(來源圖路徑, 輸出目錄)，則每算完一個區塊就立刻裁切並存檔，並回傳
    檔名→路徑；後續區塊若失敗，已存檔的區塊仍保留。
    若不傳 incremental_save，則回傳的 paths 為空，請另呼叫 save_slice_images。
    """
    w, h = image_width, image_height
    paths: Dict[str, str] = {}
    od: Optional[Path] = None
    src_path: Optional[str] = None
    if incremental_save:
        src_path, out_s = incremental_save
        od = Path(out_s)

    img: Optional[Image.Image] = None

    def _maybe_save(index: int, box: Tuple[int, int, int, int]) -> None:
        nonlocal img
        if od is None or src_path is None:
            return
        if img is None:
            img = Image.open(src_path).convert("RGB")
        paths[f"Block_{index}.png"] = write_one_slice_png(img, od, index, box)

    try:
        block1 = _compute_block1_rect(
            blocks,
            w,
            h,
            block1_top_pad=block1_top_pad,
            block1_bottom_pad=block1_bottom_pad,
        )
        _maybe_save(1, block1)

        block2 = _compute_block2_rect(
            blocks, w, h, margin=margin, raw_response=raw_response
        )
        _maybe_save(2, block2)

        block3 = _compute_block3_rect(blocks, w, h, margin=margin)
        _maybe_save(3, block3)

        block4 = _compute_block4_rect(blocks, w, h, margin=margin)
        _maybe_save(4, block4)
    finally:
        if img is not None:
            img.close()

    rects = LovnetSliceRects(
        block1=block1,
        block2=block2,
        block3=block3,
        block4=block4,
    )
    return rects, paths


def save_slice_images(
    source_image_path: str,
    rects: LovnetSliceRects,
    output_dir: str,
) -> Dict[str, str]:
    """裁切並存成 Block_1.png ~ Block_4.png，回傳檔名→絕對路徑。"""
    img = Image.open(source_image_path).convert("RGB")
    out: Dict[str, str] = {}
    od = Path(output_dir)
    try:
        for i, box in enumerate(
            (rects.block1, rects.block2, rects.block3, rects.block4), start=1
        ):
            out[f"Block_{i}.png"] = write_one_slice_png(img, od, i, box)
    finally:
        img.close()
    return out


def build_canonical_original_filename(
    engine_display: str,
    ext: str = "png",
) -> str:
    """與手機慣例對齊：ocr_日期_時間_Engine_e0_pp0_一般拍照.ext"""
    from datetime import datetime

    d = datetime.now()
    date_part = d.strftime("%Y%m%d")
    time_part = d.strftime("%H%M%S")
    safe_engine = re.sub(r"[^\w\-]+", "", engine_display) or "OCR"
    return f"ocr_{date_part}_{time_part}_{safe_engine}_e0_pp0_一般拍照.{ext.lstrip('.')}"


async def vision_blocks_for_slice(image_path: str):
    """整頁 Vision OCR，取得 text_blocks 供錨點計算。"""
    r = await ocr_with_google_vision(image_path)
    return r.text_blocks, r
