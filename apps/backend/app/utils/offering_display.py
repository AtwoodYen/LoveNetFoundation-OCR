"""
從奉獻袋 OCR 全文擷取固定摘要列（支持項目＋金額、奉獻日期、收據、姓名）。
不輸出 PDF 印刷全文；由 offering_display.hide_raw_text 驅動 API 隱藏 full_markdown。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

_HTML_TAG = re.compile(r"<[^>]+>", re.DOTALL)
_WS = re.compile(r"\s+")

# 行首：方塊、勾、Vision 常見碎片
_LEAD_NOISE = re.compile(
    r"^[\s\u200b\u00a0"
    r"\u2610\u2611\u2612"  # ☐ ☑ ☒
    r"\u2b1b\u2b1c"  # ⬛ ⬜
    r"□☐☑✓✔√✅◇◆▪▸►➤"
    r"\u20dd\u20e3\ufe0f"  # combining / keycap
    r"]+",
    re.UNICODE,
)

_ROC_LABELED = re.compile(
    r"奉獻日期\s*[：:]?\s*(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
)
_ROC_ANY = re.compile(
    r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
)
_SLASH_DATE = re.compile(
    r"(?<!\d)(\d{2,3})\s*[/／．]\s*(\d{1,2})\s*[/／．]\s*(\d{1,2})(?!\d)",
)

_NAME = re.compile(r"奉獻人姓名\s*[：:]?\s*([^\n]+)")
_RECEIPT = re.compile(r"奉獻收據\s*[：:]?\s*([^\n]+)")

_AMOUNT_TAIL = re.compile(r"(\d{1,3}(?:[,\，]\d{3})+)(?:\s*$|\s+[^\d])")
_AMOUNT_ONLY_LINE = re.compile(r"^[\sD]*(\d{1,3}(?:[,\，]\d{3})+)\s*$", re.IGNORECASE)

_CHECK_LEAD = re.compile(
    r"^[\s\u200b\u00a0]*[✓☑✔√✅☒▪\u2611]",
    re.UNICODE,
)
_MARKDOWN_X = re.compile(r"^\s*\[[xX✓]\]\s*(.+)")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")


def _plain_text(markdown: str) -> str:
    if not markdown:
        return ""
    return _HTML_TAG.sub("\n", markdown)


def _lines(text: str) -> List[str]:
    out: List[str] = []
    for ln in text.splitlines():
        s = _WS.sub(" ", ln).strip()
        if s:
            out.append(s)
    return out


def _fmt_roc(a: str, b: str, c: str) -> str:
    return f"{a.strip()} 年 {b.strip()} 月 {c.strip()} 日"


def _extract_date(plain: str) -> Optional[str]:
    m = _ROC_LABELED.search(plain)
    if m:
        return _fmt_roc(m.group(1), m.group(2), m.group(3))
    m2 = _ROC_ANY.search(plain)
    if m2:
        return _fmt_roc(m2.group(1), m2.group(2), m2.group(3))
    m3 = _SLASH_DATE.search(plain)
    if m3:
        return _fmt_roc(m3.group(1), m3.group(2), m3.group(3))
    return None


def _extract_name(plain: str) -> Optional[str]:
    m = _NAME.search(plain)
    if not m:
        return None
    v = m.group(1).strip()
    v = re.split(r"\s{2,}", v)[0]
    for cut in ("奉獻日期", "奉獻收據", "支持", "□", "☐"):
        if cut in v:
            v = v.split(cut)[0].strip()
    return v or None


def _extract_receipt(plain: str) -> Optional[str]:
    m = _RECEIPT.search(plain)
    if not m:
        return None
    v = m.group(1).strip()
    v = re.split(r"\s{2,}", v)[0]
    if "不要" in v or "不需" in v or v in ("否", "無", "No", "no"):
        return "不需要"
    if "要" in v or v in ("是", "Yes", "yes"):
        return "需要"
    return v or None


def _line_has_check(line: str) -> bool:
    if _CHECK_LEAD.match(line):
        return True
    if _MARKDOWN_X.match(line):
        return True
    if re.match(r"^\s*\[[xX✓]\]", line):
        return True
    return False


def _strip_lead(line: str) -> str:
    mx = _MARKDOWN_X.match(line)
    if mx:
        return mx.group(1).strip()
    s = line.strip()
    s = _CHECK_LEAD.sub("", s).strip()
    s = _LEAD_NOISE.sub("", s).strip()
    return s


def _project_amount_from_line(line: str) -> Optional[Tuple[str, str, bool]]:
    """
    回傳 (專案名稱, 金額字串, 是否像已勾選列)；金額须在行尾或行內最後一組千分位。
    """
    raw = line.strip()
    checked = _line_has_check(raw)
    m = _AMOUNT_TAIL.search(raw)
    if not m:
        return None
    amt = m.group(1).replace("，", ",")
    left = raw[: m.start()].strip()
    proj = _strip_lead(left)
    proj = re.sub(r"\s+", " ", proj).strip()
    if not proj or len(proj) < 2:
        return None
    if not _HAS_CJK.search(proj) and len(proj) < 6:
        return None
    return proj, amt, checked


def _pick_project_amount(lines: List[str]) -> Optional[Tuple[str, str, bool]]:
    """優先：有勾選符號且含金額的行；否則分數最高者。回傳 (專案, 金額, 是否帶勾)。 """
    candidates: List[Tuple[int, str, str, bool]] = []
    for line in lines:
        pa = _project_amount_from_line(line)
        if pa:
            proj, amt, chk = pa
            score = (1 if chk else 0) * 1000 + len(proj)
            candidates.append((score, proj, amt, chk))

    if not candidates:
        for i, line in enumerate(lines[:-1]):
            if not _line_has_check(line):
                continue
            left = _strip_lead(line)
            if _AMOUNT_TAIL.search(left):
                continue
            if not left or len(left) < 2:
                continue
            nxt = lines[i + 1]
            mo = _AMOUNT_ONLY_LINE.match(nxt)
            if mo:
                amt = mo.group(1).replace("，", ",")
                return left, amt, True
        return None

    candidates.sort(key=lambda x: -x[0])
    _, proj, amt, chk = candidates[0]
    return proj, amt, chk


def _build_summary(plain: str, lines: List[str]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []

    pa = _pick_project_amount(lines)
    if pa:
        proj, amt, was_checked = pa
        mark = "☑ " if was_checked else ""
        rows.append(
            {
                "key": "project",
                "label": "支持項目",
                "value": f"{mark}{proj}    {amt}",
            }
        )

    dv = _extract_date(plain)
    if dv:
        rows.append({"key": "date", "label": "奉獻日期", "value": dv})

    rv = _extract_receipt(plain)
    if rv:
        rows.append({"key": "receipt", "label": "奉獻收據", "value": rv})

    nv = _extract_name(plain)
    if nv:
        rows.append({"key": "donor", "label": "奉獻人姓名", "value": nv})

    return rows


def build_offering_display(full_markdown: str) -> Dict[str, Any]:
    plain = unicodedata.normalize("NFKC", _plain_text(full_markdown or ""))
    lines = _lines(plain)
    summary = _build_summary(plain, lines)

    return {
        "summary": summary,
        "hide_raw_text": True,
        # 舊版 App 相容（可全空）
        "fields": [],
        "checked_items": [],
    }
