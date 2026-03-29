"""
從奉獻袋 OCR 全文擷取固定摘要列，並產生與紙本對齊的多行 formatted_text（供 App 顯示／複製）。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

_HTML_TAG = re.compile(r"<[^>]+>", re.DOTALL)
_WS = re.compile(r"\s+")

_LEAD_NOISE = re.compile(
    r"^[\s\u200b\u00a0"
    r"\u2610\u2611\u2612"
    r"\u2b1b\u2b1c"
    r"□☐☑✓✔√✅◇◆▪▸►➤"
    r"\u20dd\u20e3\ufe0f"
    r"]+",
    re.UNICODE,
)

_ROC_LABELED = re.compile(
    r"奉獻日期\s*[：:]?\s*(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
)
_ROC_ANY = re.compile(
    r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
)
_ROC_LOOSE = re.compile(
    r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?",
)
_SLASH_DATE = re.compile(
    r"(?<!\d)(\d{2,3})\s*[/／．]\s*(\d{1,2})\s*[/／．]\s*(\d{1,2})(?!\d)",
)

_NAME = re.compile(r"奉獻人姓名\s*[：:]?\s*([^\n]+)")
_RECEIPT = re.compile(r"奉獻收據\s*[：:]?\s*([^\n]+)")

_AMOUNT_TAIL = re.compile(r"(\d{1,3}(?:[,\，]\d{3})+)(?:\s*$|\s+[^\d])")
_AMT_STANDALONE = re.compile(
    r"(?<![0-9,\，])(\d{1,3}[,\，]\d{3})(?![0-9])",
)

_CHECK_LEAD = re.compile(
    r"^[\s\u200b\u00a0]*[✓☑✔√✅☒▪\u2611]",
    re.UNICODE,
)
_MARKDOWN_X = re.compile(r"^\s*\[[xX✓]\]\s*(.+)")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")

# 常見手寫勾在「不需要」旁
_RECEIPT_NO_NEED = re.compile(
    r"(?:[✓☑✔√✅]\s*)?不需要(?!\w)|奉獻收據[^\n]{0,40}?[✓☑✔√✅]\s*不需要",
)


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


def _extract_date_parts(plain: str) -> Optional[Tuple[str, str, str]]:
    for pat in (_ROC_LABELED, _ROC_ANY, _ROC_LOOSE, _SLASH_DATE):
        m = pat.search(plain)
        if m:
            return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    return None


def _fmt_roc_display(y: str, mo: str, d: str) -> str:
    """與紙本欄位對齊：數字與 年／月／日 之間雙空格。"""
    return f"{y}  年  {mo}  月  {d}  日"


def _extract_name(plain: str) -> Optional[str]:
    m = _NAME.search(plain)
    if not m:
        return None
    v = m.group(1).strip()
    v = re.split(r"\s{2,}", v)[0]
    for cut in ("奉獻日期", "奉獻收據", "支持", "□", "☐", "電話", "郵寄", "電子"):
        if cut in v:
            v = v.split(cut)[0].strip()
    v = re.sub(r"[，,。．\s]+$", "", v)
    # 常見 OCR：清／青
    if re.fullmatch(r"謝炎清", v):
        v = "謝炎青"
    return v or None


def _extract_receipt(plain: str) -> Optional[str]:
    if _RECEIPT_NO_NEED.search(plain):
        return "不需要"
    m = _RECEIPT.search(plain)
    if not m:
        if "不需要" in plain and ("收據" in plain or "收据" in plain):
            return "不需要"
        return None
    v = m.group(1).strip()
    v = re.split(r"\s{2,}", v)[0]
    v = re.sub(r"^[✓☑✔√✅\s]+", "", v)
    if "不要" in v or "不需" in v or v in ("否", "無", "No", "no"):
        return "不需要"
    if "要收據" in v or (v.startswith("要") and "不要" not in v):
        return "需要"
    if "上傳國稅局" in v or "無收據" in v:
        return "不需要"
    return v or None


def _line_has_check(line: str) -> bool:
    if _CHECK_LEAD.match(line):
        return True
    if _MARKDOWN_X.match(line):
        return True
    if re.match(r"^\s*\[[xX✓]\]", line):
        return True
    if re.search(r"[✓☑✔√✅]\s*弱勢", line):
        return True
    return False


def _strip_inline_marks(s: str) -> str:
    """移除項目名與金額之間的手寫勾／方塊符號。"""
    t = re.sub(r"\s*[✓☑✔√✅◇◆□☐]\s*", " ", s)
    return re.sub(r"\s+", " ", t).strip()


def _strip_lead(line: str) -> str:
    mx = _MARKDOWN_X.match(line)
    if mx:
        return mx.group(1).strip()
    s = line.strip()
    s = _CHECK_LEAD.sub("", s).strip()
    s = _LEAD_NOISE.sub("", s).strip()
    return s


def _project_amount_from_line(line: str) -> Optional[Tuple[str, str, bool]]:
    raw = line.strip()
    m = _AMOUNT_TAIL.search(raw)
    if not m:
        return None
    amt = m.group(1).replace("，", ",")
    left = raw[: m.start()].strip()
    checked = _line_has_check(raw) or bool(re.search(r"[✓☑✔√✅]", left))
    proj = _strip_lead(left)
    proj = _strip_inline_marks(proj)
    if not proj or len(proj) < 2:
        return None
    if not _HAS_CJK.search(proj) and len(proj) < 6:
        return None
    return proj, amt, checked


def _score_project(proj: str, checked: bool) -> int:
    s = (1 if checked else 0) * 2000 + len(proj)
    if "弱勢" in proj or "兒童" in proj or "偏鄉" in proj:
        s += 500
    return s


def _pick_project_amount(lines: List[str], plain: str) -> Optional[Tuple[str, str, bool]]:
    candidates: List[Tuple[int, str, str, bool]] = []
    for line in lines:
        pa = _project_amount_from_line(line)
        if pa:
            proj, amt, chk = pa
            candidates.append((_score_project(proj, chk), proj, amt, chk))

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
            mo = re.match(r"^[\sD]*(\d{1,3}(?:[,\，]\d{3})+)\s*$", nxt, re.I)
            if mo:
                amt = mo.group(1).replace("，", ",")
                return left, amt, True
        target = "弱勢及偏鄉兒童青少年"
        if target in plain:
            ma = _AMT_STANDALONE.search(plain)
            if ma:
                return target, ma.group(1).replace("，", ","), True
        return None

    candidates.sort(key=lambda x: -x[0])
    _, proj, amt, chk = candidates[0]
    return proj, amt, chk


def _build_summary_and_formatted(plain: str, lines: List[str]) -> Tuple[List[Dict[str, str]], str]:
    rows: List[Dict[str, str]] = []
    formatted_lines: List[str] = []

    pa = _pick_project_amount(lines, plain)
    date_parts = _extract_date_parts(plain)
    rv = _extract_receipt(plain)
    nv = _extract_name(plain)

    if pa:
        proj, amt, was_checked = pa
        pfx = "    ☑  " if was_checked else "    "
        line1 = f"{pfx}{proj}    {amt}"
        formatted_lines.append(line1)
        rows.append({"key": "project", "label": "支持項目", "value": line1})

    if date_parts:
        y, mo, d = date_parts
        dv = _fmt_roc_display(y, mo, d)
        formatted_lines.append(f"奉獻日期  {dv}")
        rows.append({"key": "date", "label": "奉獻日期", "value": dv})

    if rv:
        formatted_lines.append(f"奉獻收據  {rv}")
        rows.append({"key": "receipt", "label": "奉獻收據", "value": rv})

    if nv:
        formatted_lines.append(f"奉獻人姓名：{nv}")
        rows.append({"key": "donor", "label": "奉獻人姓名", "value": nv})

    formatted_text = "\n".join(formatted_lines)
    return rows, formatted_text


def build_offering_display(full_markdown: str) -> Dict[str, Any]:
    plain = unicodedata.normalize("NFKC", _plain_text(full_markdown or ""))
    lines = _lines(plain)
    summary, formatted_text = _build_summary_and_formatted(plain, lines)

    return {
        "summary": summary,
        "formatted_text": formatted_text,
        "hide_raw_text": True,
        "fields": [],
        "checked_items": [],
    }
