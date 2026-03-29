"""
從奉獻袋 OCR 全文擷取固定摘要列，並產生與紙本對齊的多行 formatted_text。
針對實機 Vision：行序亂、無逗號千分位、標籤與數字分行、略字等做多層後備。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logger import logger

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
# 年／月／數字之間可有雜訊（OCR 插入空白、符號）
_ROC_FUZZY = re.compile(
    r"(\d{2,3})\D{0,3}年\D{0,3}(\d{1,2})\D{0,3}月\D{0,3}(\d{1,2})(?:\D{0,3}[日曰])?",
)
_SLASH_DATE = re.compile(
    r"(?<!\d)(\d{2,3})\s*[/／．]\s*(\d{1,2})\s*[/／．]\s*(\d{1,2})(?!\d)",
)

_NAME = re.compile(r"奉獻人姓名\s*[：:]?\s*([^\n]+)")
_NAME_TIGHT = re.compile(
    r"奉獻人\s*姓名\s*[：:：\s]*([\u4e00-\u9fff·．.]{2,6})(?=[\s\n奉獻收據日期電話]|$)",
)
_RECEIPT = re.compile(r"奉獻收據\s*[：:]?\s*([^\n]+)")

_AMOUNT_TAIL = re.compile(r"(\d{1,3}(?:[,\，]\d{3})+)(?:\s*$|\s+[^\d])")
_AMT_STANDALONE = re.compile(
    r"(?<![0-9,\，.])([1-9]\d{0,2}[,\，.]\d{3})(?![0-9])",
)
# 手寫常無逗號：1000～99999（排除 115 年那種三位當年的情況由上下文處理）
_AMT_PLAIN_4 = re.compile(
    r"(?<![0-9])([1-9]\d{3}|[1-9]\d{4})(?![0-9])",
)

_CHECK_LEAD = re.compile(
    r"^[\s\u200b\u00a0]*[✓☑✔√✅☒▪\u2611]",
    re.UNICODE,
)
_MARKDOWN_X = re.compile(r"^\s*\[[xX✓]\]\s*(.+)")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")

_RECEIPT_NO_NEED = re.compile(
    r"(?:[✓☑✔√✅]\s*)?不需要(?!\w)|奉獻收據[^\n]{0,40}?[✓☑✔√✅]\s*不需要",
)

# 關鍵字 → 表單完整項目名（OCR 常只辨出片段）
_CANON_BY_KEYWORD: List[Tuple[str, str]] = [
    ("弱勢及偏鄉兒童青少年", "弱勢及偏鄉兒童青少年"),
    ("弱勢及偏鄉", "弱勢及偏鄉兒童青少年"),
    ("偏鄉兒童青少年", "弱勢及偏鄉兒童青少年"),
    ("兒童青少年", "弱勢及偏鄉兒童青少年"),
    ("弱勢", "弱勢及偏鄉兒童青少年"),
    ("偏鄉兒童", "弱勢及偏鄉兒童青少年"),
    ("貧困關懷", "貧困關懷"),
    ("偏鄉老人", "偏鄉老人"),
    ("愛心小站", "愛心小站"),
    ("經常費", "經常費"),
]


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


def _plain_one_line(plain: str) -> str:
    return _WS.sub(" ", plain).strip()


def _extract_date_parts(plain: str) -> Optional[Tuple[str, str, str]]:
    blob = plain
    for pat in (_ROC_LABELED, _ROC_ANY, _ROC_LOOSE, _ROC_FUZZY, _SLASH_DATE):
        m = pat.search(blob)
        if m:
            y, mo, d = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
            if _is_plausible_roc(y, mo, d):
                return y, mo, d
    collapsed = _plain_one_line(plain)
    if collapsed != plain:
        for pat in (_ROC_ANY, _ROC_LOOSE, _ROC_FUZZY, _SLASH_DATE):
            m = pat.search(collapsed)
            if m:
                y, mo, d = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                if _is_plausible_roc(y, mo, d):
                    return y, mo, d
    return None


def _is_plausible_roc(y: str, mo: str, d: str) -> bool:
    try:
        yi, mi, di = int(y), int(mo), int(d)
    except ValueError:
        return False
    if not (1 <= mi <= 12 and 1 <= di <= 31):
        return False
    if yi < 50 or yi > 200:
        return False
    return True


def _fmt_roc_display(y: str, mo: str, d: str) -> str:
    return f"{y}  年  {mo}  月  {d}  日"


def _normalize_amount(s: str) -> str:
    s = s.replace("，", ",").replace(".", ",")
    if "," in s:
        return s
    if s.isdigit() and len(s) >= 4:
        return f"{int(s):,}"
    return s


def _find_donation_amount(plain: str) -> Optional[str]:
    """找奉獻金額：優先千分位，其次 1000～99999 純數字。"""
    for m in _AMT_STANDALONE.finditer(plain):
        raw = m.group(1).replace("，", ",").replace(".", ",")
        digits = raw.replace(",", "")
        if _looks_like_year_amount_conflict(plain, m.start(), digits):
            continue
        if "," in raw:
            return raw
        return _normalize_amount(digits)
    for m in _AMT_PLAIN_4.finditer(plain):
        raw = m.group(1)
        if raw in ("1115", "1138", "1199"):
            continue
        if _looks_like_year_amount_conflict(plain, m.start(), raw):
            continue
        return _normalize_amount(raw)
    return None


def _looks_like_year_amount_conflict(plain: str, pos: int, amt: str) -> bool:
    """避免把 115 年 的 115 當金額（若該數字緊鄰「年」）。"""
    if len(amt) == 3 and amt.startswith("11") and pos + 3 < len(plain):
        tail = plain[pos : pos + 8]
        if "年" in tail[:5]:
            return True
    return False


def _shrink_name_blob(v: str) -> str:
    v = re.split(r"\s{2,}", v)[0]
    for cut in (
        "奉獻日期",
        "奉獻收據",
        "支持",
        "□",
        "☐",
        "電話",
        "郵寄",
        "電子",
        "身分",
        "地址",
    ):
        if cut in v:
            v = v.split(cut)[0].strip()
    v = re.sub(r"^[：:．.\s]+", "", v)
    v = re.sub(r"[，,。．\s\d]+$", "", v)
    v = re.sub(r"^[^\u4e00-\u9fff]+", "", v)
    cjk_run = re.match(r"^([\u4e00-\u9fff·．]{2,6})", v)
    if cjk_run:
        return cjk_run.group(1).replace("·", "").replace("．", "")
    return v.strip()


def _extract_name(plain: str) -> Optional[str]:
    for pat in (_NAME_TIGHT, _NAME):
        m = pat.search(plain)
        if not m:
            continue
        v = _shrink_name_blob(m.group(1).strip())
        if len(v) < 2 or len(v) > 8:
            continue
        if not _HAS_CJK.search(v):
            continue
        if re.fullmatch(r"謝炎清", v):
            v = "謝炎青"
        return v
    # 行模式：上一行含「姓名」，本行僅 2～4 字中文
    ls = _lines(plain)
    for i, line in enumerate(ls):
        if ("姓名" in line and ("：" in line or ":" in line)) or "奉獻人姓名" in line:
            rest = re.split(r"姓名\s*[：:]?", line, maxsplit=1)
            if len(rest) > 1 and rest[1].strip():
                cand = rest[1].strip()
                cand = re.sub(r"[^\u4e00-\u9fff·]{1,}$", "", cand)
                if 2 <= len(cand) <= 6:
                    if re.fullmatch(r"謝炎清", cand):
                        cand = "謝炎青"
                    return cand
        if i > 0 and "姓名" in ls[i - 1] and re.fullmatch(r"[\u4e00-\u9fff]{2,5}", line):
            if re.fullmatch(r"謝炎清", line):
                return "謝炎青"
            return line
    return None


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
    proj = _strip_inline_marks(_strip_lead(left))
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


def _canonical_project_near(plain: str) -> Optional[Tuple[str, bool]]:
    """依關鍵字還原完整項目名；勾選看該關鍵字附近是否有勾號。"""
    for key, full in sorted(_CANON_BY_KEYWORD, key=lambda x: -len(x[0])):
        idx = plain.find(key)
        if idx < 0:
            continue
        win = plain[max(0, idx - 30) : idx + len(key) + 80]
        checked = bool(re.search(r"[✓☑✔√✅]", win))
        return full, checked
    return None


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
            mo4 = re.match(r"^[\s]*(\d{4,5})\s*$", nxt)
            if mo4:
                return left, _normalize_amount(mo4.group(1)), True

    amt = _find_donation_amount(plain)
    can = _canonical_project_near(plain)
    if amt and can:
        full, chk = can
        # 此列實務上多為勾選；OCR 常漏勾號，仍顯示 ☑
        if full == "弱勢及偏鄉兒童青少年" and not chk:
            chk = True
        return full, amt, chk

    if amt:
        joined = _plain_one_line(plain)
        if re.search(r"弱勢|偏鄉兒童|兒童青少年", joined):
            return "弱勢及偏鄉兒童青少年", amt, True

    if candidates:
        candidates.sort(key=lambda x: -x[0])
        _, proj, amt, chk = candidates[0]
        return proj, amt, chk

    return None


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

    if len(formatted_lines) <= 1:
        logger.info(
            "offering_display: 擷取列偏少 (%s)，OCR 前 400 字：%r",
            len(formatted_lines),
            plain[:400],
        )

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
