"""
從奉獻袋類 OCR 全文擷取：僅輸出有資料的欄位、勾選項目（供 App 結構化顯示）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_HTML_TAG = re.compile(r"<[^>]+>", re.DOTALL)
_WS = re.compile(r"\s+")

# 行首勾選符（手機／版面 OCR 常見變形）
_CHECK_LEAD = re.compile(
    r"^[\s\u200b\u00a0]*[✓☑✔√✅✔︎☒▪▸►➤❑◆◇]\s*",
    re.UNICODE,
)
# 行內「項目名 ✓」
_CHECK_TAIL = re.compile(
    r"^(.+?)[\s\u200b]*[✓☑✔√✅]+$",
    re.UNICODE,
)
_MARKDOWN_X = re.compile(r"^\s*\[[xX✓]\]\s*(.+)")

_ROC_DATE = re.compile(
    r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
)
_SLASH_DATE = re.compile(
    r"(?<!\d)(\d{2,3})\s*[/／．]\s*(\d{1,2})\s*[/／．]\s*(\d{1,2})(?!\d)",
)


def _plain_text(markdown: str) -> str:
    if not markdown:
        return ""
    t = _HTML_TAG.sub("\n", markdown)
    return t


def _lines(text: str) -> List[str]:
    raw = text.splitlines()
    out: List[str] = []
    for ln in raw:
        s = _WS.sub(" ", ln).strip()
        if s:
            out.append(s)
    return out


def _extract_roc_date(text: str) -> Optional[str]:
    m = _ROC_DATE.search(text)
    if m:
        return f"{m.group(1)} 年 {m.group(2)} 月 {m.group(3)} 日"
    m2 = _SLASH_DATE.search(text)
    if m2:
        return f"{m2.group(1)} 年 {m2.group(2)} 月 {m2.group(3)} 日"
    return None


def _extract_amount(text: str) -> Optional[str]:
    """優先擷取千分位金額，並避免把民國年數字當金額。"""
    best: Optional[Tuple[int, str]] = None
    for m in re.finditer(r"\d{1,3}(?:[,\，]\d{3})+(?:\.\d+)?", text):
        s = m.group(0).replace("，", ",")
        span = m.span()
        win = text[max(0, span[0] - 12) : min(len(text), span[1] + 12)]
        # 千分位數字不當成民國日期；僅在「無千分位逗號」時才用 年/月 鄰近排除
        if "," not in s and "，" not in m.group(0) and "年" in win and "月" in win:
            continue
        score = len(s)
        if best is None or score > best[0]:
            best = (score, s)
    if best:
        return best[1]
    for m in re.finditer(r"(?<![0-9])(\d{4,6})(?![0-9])", text):
        s = m.group(1)
        span = m.span()
        win = text[max(0, span[0] - 12) : min(len(text), span[1] + 12)]
        if "年" in win:
            continue
        if s.startswith("11") and len(s) == 3 and "年" in text[span[1] : span[1] + 6]:
            continue
        return s
    return None


def _extract_checked_items(lines: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for line in lines:
        if _CHECK_LEAD.match(line):
            item = _CHECK_LEAD.sub("", line).strip()
            item = re.sub(r"^[.:：、，,]\s*", "", item)
            if item and item not in seen:
                seen.add(item)
                out.append(item)
            continue
        mx = _MARKDOWN_X.match(line)
        if mx:
            item = mx.group(1).strip()
            if item and item not in seen:
                seen.add(item)
                out.append(item)
            continue
        tm = _CHECK_TAIL.match(line)
        if tm:
            item = tm.group(1).strip()
            if item and item not in seen:
                seen.add(item)
                out.append(item)
    return out


def build_offering_display(full_markdown: str) -> Dict[str, Any]:
    """
    回傳：
    - fields: 僅「有擷取到值」的欄位（含 label / value）
    - checked_items: 推測為打勾的項目列
    """
    plain = _plain_text(full_markdown or "")
    lines = _lines(plain)

    fields: List[Dict[str, str]] = []
    date_v = _extract_roc_date(plain)
    if date_v:
        fields.append({"key": "date", "label": "日期", "value": date_v})

    amt = _extract_amount(plain)
    if amt:
        fields.append({"key": "amount", "label": "奉獻金額", "value": amt})

    checked = _extract_checked_items(lines)

    return {
        "fields": fields,
        "checked_items": checked,
    }
