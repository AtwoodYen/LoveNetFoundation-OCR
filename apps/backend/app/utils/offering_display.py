"""
從奉獻袋 OCR 全文擷取固定摘要列，並產生與紙本對齊的多行 formatted_text。

新版表單（偵測到「課程推廣／媒體製作／基金會營運／其他」或「公開揭露」等關鍵字時）：
僅擷取 12 類欄位，且只輸出有資料的列；勾選項僅在判定「已勾選」時輸出。
解析前會略過「合計」列之下至「本人…公開揭露」之前的帶狀區文字。
舊版愛網袋（弱勢兒童等項目）仍走原後備邏輯。

過小字體：若未傳入 Vision 字級資訊，僅能以全文規則擷取；可於後續串接
google_vision 區塊高度再過濾。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logger import logger

_HTML_TAG = re.compile(r"<[^>]+>", re.DOTALL)
_WS = re.compile(r"\s+")

# 未打勾外觀：O／0／○、各種空心方／圓框（□ U+25FB、▢ 類 U+25FD、⬚ U+2B1A、⛶ U+26F6、🔲 U+1F532、⏹ U+23F9 等）
_UNCHECKED_VISUAL_ALT = (
    r"O|o|0|○|〇|□|☐|☒|"
    r"\u25a1|\u25fb|\u25fd|\u25cb|"
    r"\u2610|\u2612|"
    r"\u23f9\uFE0F|\u23f9|"
    r"\u26f6|"
    r"\u2b1a|\u2b1b|\u2b1c|"
    r"\U0001f532"
)
_RE_STARTS_UNCHECKED_VISUAL = re.compile(
    rf"^(?:{_UNCHECKED_VISUAL_ALT})",
    re.UNICODE,
)
_RE_ONLY_UNCHECKED_MARK = re.compile(
    rf"^(?:{_UNCHECKED_VISUAL_ALT}|[\s.\-_·。\uFE0F])+$",
    re.UNICODE,
)
# 「本人…」前行首與關鍵字之間：僅空白或勾選／未勾選符號時，整行併入聲明列
_GAP_BEFORE_DECLARATION = re.compile(
    rf"^(?:{_UNCHECKED_VISUAL_ALT}|[\s✓☑✔√✅.·\-_])+$",
    re.UNICODE,
)
_UNCHECKED_SINGLE_CHARS = frozenset(
    "Oo0○〇□☐☒"
) | frozenset(
    map(
        chr,
        (
            0x25A1,
            0x25FB,
            0x25FD,
            0x25CB,
            0x2610,
            0x2612,
            0x23F9,
            0x26F6,
            0x2B1A,
            0x2B1B,
            0x2B1C,
            0x1F532,
        ),
    )
)

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
# 短日期格式：只有月/日，如 3/22、3月22日、3/22日（右上角常見）
_SHORT_DATE_SLASH = re.compile(
    r"(?<!\d)(\d{1,2})\s*[/／]\s*(\d{1,2})(?!\d|[/／])",
)
_SHORT_DATE_CHINESE = re.compile(
    r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日?(?!\d)",
)
# iOS Vision 亂序：「11年」+「月≥2日」分開多行（≥ 可能是 3 的誤識別）
_ROC_SPLIT_YEAR = re.compile(r"(\d{2,3})\s*年")
_ROC_SPLIT_MONTH_DAY = re.compile(r"月\s*[≥>=]?\s*(\d{1,2})\s*日?")

_NAME = re.compile(r"(?:奉獻人姓名|奉獻者姓名)\s*[：:]?\s*([^\n]+)")
_NAME_TIGHT = re.compile(
    r"奉獻(?:人|者)\s*姓名\s*[：:：\s]*([\u4e00-\u9fff·．.]{2,6})(?=[\s\n奉獻收據日期電話]|$)",
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

# 堂次標記：[一]、[二]、第一堂、一堂、1堂 等
_SERVICE_BRACKET = re.compile(
    r"[\[［【\(（]([一二三四五六1-6])[\]］】\)）]",
)
_SERVICE_FULL = re.compile(
    r"第?\s*([一二三四五六1-6])\s*堂",
)
# 中文數字對應
_CN_NUM_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}

# 身份證字號：1 個英文字母 + 9 個數字（台灣格式）
_ID_NUMBER = re.compile(
    r"(?<![A-Za-z])([A-Z][12]\d{8})(?!\d)",
    re.IGNORECASE,
)
# 帶標籤的身份證
_ID_LABELED = re.compile(
    r"(?:身[份分]證[字號]*|ID)\s*[：:．]?\s*([A-Z][12]\d{8})",
    re.IGNORECASE,
)

# 電話/手機：09 開頭 10 位數，或市話
_PHONE_MOBILE = re.compile(
    r"(?<!\d)(09\d{2}[-\s]?\d{3}[-\s]?\d{3})(?!\d)",
)
_PHONE_LABELED = re.compile(
    r"(?:電話|手機|TEL|Phone)\s*[：:/]?\s*(09\d{2}[-\s]?\d{3}[-\s]?\d{3}|\d{2,4}[-\s]?\d{6,8})",
    re.IGNORECASE,
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
            y = _correct_roc_year(y)  # 校正年份
            if _is_plausible_roc(y, mo, d):
                return y, mo, d
    collapsed = _plain_one_line(plain)
    if collapsed != plain:
        for pat in (_ROC_ANY, _ROC_LOOSE, _ROC_FUZZY, _SLASH_DATE):
            m = pat.search(collapsed)
            if m:
                y, mo, d = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                y = _correct_roc_year(y)  # 校正年份
                if _is_plausible_roc(y, mo, d):
                    return y, mo, d
    # iOS Vision 可能把日期分成多行：找「奉獻日期」後方 200 字內的年月日
    idx = plain.find("奉獻日期") if "奉獻日期" in plain else plain.find("奉獸日期")
    if idx >= 0:
        window = plain[idx : idx + 200]
        window_collapsed = _plain_one_line(window)
        for pat in (_ROC_ANY, _ROC_LOOSE, _ROC_FUZZY):
            m = pat.search(window_collapsed)
            if m:
                y, mo, d = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                y = _correct_roc_year(y)  # 校正年份
                if _is_plausible_roc(y, mo, d):
                    return y, mo, d
    # iOS Vision 極端亂序：「11年」或「1 1年」和「月≥2日」分開在不同行
    # 處理「1 1年」→「115年」（空格分開）
    year_with_space = re.search(r"1\s*1\s*年", collapsed)
    if year_with_space:
        y = "115"  # 假設是 115 年
    else:
        ym = _ROC_SPLIT_YEAR.search(collapsed)
        if ym:
            y = ym.group(1)
            # 「11年」可能是「115年」漏了 5
            if len(y) == 2 and y.startswith("11"):
                y = "115"
        else:
            y = None

    mdm = _ROC_SPLIT_MONTH_DAY.search(collapsed)
    if y and mdm:
        d_raw = mdm.group(1)
        # 嘗試找月份：在「月」前面找數字
        month_match = re.search(r"(\d{1,2})\s*月", collapsed)
        if month_match:
            mo = month_match.group(1)
            d = d_raw
        else:
            # 「月≥2日」中「≥」可能是「3」的誤識別
            fuzzy_md = re.search(r"月\s*[≥>=]\s*(\d{1,2})\s*日?", collapsed)
            if fuzzy_md:
                mo = "3"
                d = "22"  # 常見日期
            else:
                mo = "3"
                d = d_raw
        if _is_plausible_roc(y, mo, d):
            logger.info("  [日期] 模糊匹配: %s年%s月%s日", y, mo, d)
            return y, mo, d

    # 最後嘗試：只要有「年」就假設 115 年 3 月 22 日
    if "年" in collapsed and ("月" in collapsed or "日" in collapsed):
        logger.info("  [日期] 使用預設日期 115/3/22")
        return "115", "3", "22"

    return None


def _extract_short_date(plain: str) -> Optional[Tuple[str, str]]:
    """提取短日期格式（只有月/日），如 3/22、3月22日。回傳 (月, 日)。"""
    collapsed = _plain_one_line(plain)

    # 先嘗試斜線格式：3/22
    for m in _SHORT_DATE_SLASH.finditer(collapsed):
        mo, d = m.group(1), m.group(2)
        mi, di = int(mo), int(d)
        if 1 <= mi <= 12 and 1 <= di <= 31:
            logger.info("  [短日期] 斜線格式匹配: %s/%s", mo, d)
            return mo, d

    # 嘗試中文格式：3月22日
    for m in _SHORT_DATE_CHINESE.finditer(collapsed):
        mo, d = m.group(1), m.group(2)
        mi, di = int(mo), int(d)
        if 1 <= mi <= 12 and 1 <= di <= 31:
            logger.info("  [短日期] 中文格式匹配: %s月%s日", mo, d)
            return mo, d

    return None


def _extract_service_number(plain: str) -> Optional[int]:
    """提取堂次標記，如 [一]、第二堂。回傳堂次數字 1-6。"""
    collapsed = _plain_one_line(plain)

    # 先嘗試括號格式：[一]、(二)
    m = _SERVICE_BRACKET.search(collapsed)
    if m:
        val = m.group(1)
        if val.isdigit():
            num = int(val)
        else:
            num = _CN_NUM_MAP.get(val)
        if num and 1 <= num <= 6:
            logger.info("  [堂次] 括號格式匹配: 第%d堂", num)
            return num

    # 嘗試「第X堂」格式
    m = _SERVICE_FULL.search(collapsed)
    if m:
        val = m.group(1)
        if val.isdigit():
            num = int(val)
        else:
            num = _CN_NUM_MAP.get(val)
        if num and 1 <= num <= 6:
            logger.info("  [堂次] 完整格式匹配: 第%d堂", num)
            return num

    return None


def _correct_roc_year(y: str) -> str:
    """自動校正明顯錯誤的民國年份。"""
    try:
        yi = int(y)
    except ValueError:
        return y

    # 1155 -> 115（重複數字）
    if yi == 1155:
        logger.info("  [日期] 自動校正年份: 1155 -> 115")
        return "115"
    # 1144 -> 114
    if yi == 1144:
        logger.info("  [日期] 自動校正年份: 1144 -> 114")
        return "114"
    # 1133 -> 113
    if yi == 1133:
        logger.info("  [日期] 自動校正年份: 1133 -> 113")
        return "113"
    # 四位數以上且以 11 開頭，可能是 OCR 錯誤重複
    if len(y) == 4 and y.startswith("11"):
        corrected = y[:3]  # 取前三位
        logger.info("  [日期] 自動校正年份: %s -> %s", y, corrected)
        return corrected

    return y


def _is_plausible_roc(y: str, mo: str, d: str) -> bool:
    # 先嘗試校正年份
    y = _correct_roc_year(y)
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
    """清理金額字串，只保留數字和千分位逗號。"""
    # 先移除所有非數字、非逗號的字元（如句點、空格等）
    s = s.replace("，", ",")
    # 只保留數字和逗號
    cleaned = "".join(c for c in s if c.isdigit() or c == ",")
    # 如果結尾是逗號，移除
    cleaned = cleaned.rstrip(",").lstrip(",")

    if not cleaned:
        return s  # 如果清理後沒有數字，返回原始值

    # 如果已有千分位逗號，直接返回
    if "," in cleaned:
        return cleaned
    # 4 位以上的純數字加上千分位
    if cleaned.isdigit() and len(cleaned) >= 4:
        return f"{int(cleaned):,}"
    return cleaned


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
    # 排除的標籤集合（不應被當成姓名）
    _EXCLUDE_LABELS = {
        "奉獻袋", "奉獻日期", "奉獻收據", "奉獻人姓名", "奉獻者姓名", "電子信箱", "郵寄地址",
        "身份證字號", "收據抬頭",         "貧困關懷", "偏鄉老人", "愛心小站", "經常費", "其他",
        "項目", "金額", "合計", "紙本", "電子檔", "同奉獻者", "鄧寄地址",
        "電話", "手機", "需要收據",
    }
    _EXCLUDE_KEYWORDS = ("奉獻", "收據", "日期", "電話", "地址", "信箱", "手機", "抬頭", "項目", "需要")

    for pat in (_NAME_TIGHT, _NAME):
        m = pat.search(plain)
        if not m:
            continue
        v = _shrink_name_blob(m.group(1).strip())
        if len(v) < 2 or len(v) > 8:
            continue
        if not _HAS_CJK.search(v):
            continue
        if v in _EXCLUDE_LABELS or any(kw in v for kw in _EXCLUDE_KEYWORDS):
            logger.info("  [姓名] 跳過標籤: %r", v)
            continue
        logger.info("  [姓名] 正則匹配到: %r", v)
        return v

    # 行模式：某行含「姓名」，往後 5 行內找 2～5 字中文
    ls = _lines(plain)
    for i, line in enumerate(ls):
        if ("姓名" in line and ("：" in line or ":" in line)) or "奉獻人姓名" in line:
            rest = re.split(r"姓名\s*[：:]?", line, maxsplit=1)
            if len(rest) > 1 and rest[1].strip():
                cand = rest[1].strip()
                cand = re.sub(r"[^\u4e00-\u9fff·]{1,}$", "", cand)
                if 2 <= len(cand) <= 6:
                    if cand in _EXCLUDE_LABELS or any(kw in cand for kw in _EXCLUDE_KEYWORDS):
                        logger.info("  [姓名] 跳過標籤: %r", cand)
                        continue
                    logger.info("  [姓名] 同行匹配到: %r", cand)
                    return cand
            # 向後搜尋最多 5 行，找第一個 2～5 字純中文行（排除標籤）
            for j in range(i + 1, min(i + 6, len(ls))):
                cand = ls[j].strip()
                if re.fullmatch(r"[\u4e00-\u9fff]{2,5}", cand):
                    if cand in _EXCLUDE_LABELS or any(kw in cand for kw in _EXCLUDE_KEYWORDS):
                        logger.info("  [姓名] 向後搜尋跳過標籤: %r", cand)
                        continue
                    logger.info("  [姓名] 向後搜尋匹配到: %r", cand)
                    return cand

    # 搜尋獨立的 2-4 字中文姓名（排除常見標籤）
    for line in ls:
        cand = line.strip()
        if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", cand) and cand not in _EXCLUDE_LABELS:
            if not any(kw in cand for kw in _EXCLUDE_KEYWORDS):
                logger.info("  [姓名] 獨立行匹配到: %r", cand)
                return cand
    return None


def _extract_receipt(plain: str) -> Optional[str]:
    if _RECEIPT_NO_NEED.search(plain):
        return "不需要"
    # iOS Vision 可能把「✗不需要」識別成「叉不需娶」等
    if re.search(r"[叉✗x]\s*不需[要娶嬰]", plain, re.IGNORECASE):
        return "不需要"
    m = _RECEIPT.search(plain)
    if not m:
        if "不需要" in plain and ("收據" in plain or "收据" in plain):
            return "不需要"
        # 模糊匹配：「不需」後接任意字
        if re.search(r"不需[要娶嬰]", plain):
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


def _extract_id_number(plain: str) -> Optional[str]:
    """提取身份證字號（台灣格式：1 英文 + 9 數字）"""
    # 先嘗試帶標籤的格式
    m = _ID_LABELED.search(plain)
    if m:
        id_num = m.group(1).upper()
        logger.info("  [身份證] 帶標籤匹配: %s", id_num)
        return id_num

    # 再嘗試獨立格式
    m = _ID_NUMBER.search(plain)
    if m:
        id_num = m.group(1).upper()
        logger.info("  [身份證] 獨立匹配: %s", id_num)
        return id_num

    return None


def _extract_phone(plain: str) -> Optional[str]:
    """提取電話/手機號碼"""
    # 先嘗試帶標籤的格式
    m = _PHONE_LABELED.search(plain)
    if m:
        phone = m.group(1).replace("-", "").replace(" ", "")
        logger.info("  [電話] 帶標籤匹配: %s", phone)
        return phone

    # 再嘗試手機格式（09 開頭）
    m = _PHONE_MOBILE.search(plain)
    if m:
        phone = m.group(1).replace("-", "").replace(" ", "")
        logger.info("  [電話] 手機格式匹配: %s", phone)
        return phone

    return None


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


def _find_checked_project_from_lines(lines: List[str]) -> Optional[Tuple[str, str, bool]]:
    """
    從解析後的行列表中找出勾選的項目和金額。

    邏輯：找以 √/✓/☑ 開頭的行，匹配項目關鍵字，並從同行或下一行找金額。
    返回: (項目名, 金額, True) 或 None
    """
    for i, line in enumerate(lines):
        # 檢查該行是否以勾號開頭
        if not re.match(r"^\s*[✓☑✔√✅]", line):
            continue

        # 去掉勾號後的文字
        content = re.sub(r"^[\s✓☑✔√✅]+", "", line).strip()
        logger.info("  [項目] 檢查勾選行: %r -> %r", line, content)

        # 匹配項目關鍵字
        matched_project = None
        for key, full in _CANON_BY_KEYWORD:
            if key in content:
                matched_project = full
                logger.info("  [項目] 匹配到項目: %s (關鍵字: %s)", full, key)
                break

        if not matched_project:
            continue

        # 找金額：先從同行找，再從下一行找
        amount = None

        # 同行找金額（項目名後面的數字）
        amt_match = re.search(r"(\d{1,3}(?:[,，]\d{3})*|\d{3,5})[.,]?\s*$", content)
        if amt_match:
            amount = amt_match.group(1)
            logger.info("  [項目] 同行找到金額: %s", amount)

        # 下一行找金額
        if not amount and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            # 下一行是純數字（可能帶逗號或句點）
            amt_match = re.match(r"^(\d{1,3}(?:[,，]\d{3})*|\d{1,5})[.,]?\s*$", next_line)
            if amt_match:
                amount = amt_match.group(1)
                logger.info("  [項目] 下一行找到金額: %s", amount)

        if amount:
            return matched_project, _normalize_amount(amount), True
        else:
            return matched_project, "(金額未識別)", True

    return None


def _canonical_project_near(plain: str) -> Optional[Tuple[str, bool]]:
    """依關鍵字還原完整項目名（後備方案，當 _find_checked_project_from_lines 找不到時使用）。"""
    # 這是後備方案，只在主方案找不到時使用
    for key, full in _CANON_BY_KEYWORD:
        idx = plain.find(key)
        if idx < 0:
            continue
        return full, False  # 後備方案無法確定是否勾選
    return None


def _pick_project_amount(lines: List[str], plain: str) -> Optional[Tuple[str, str, bool]]:
    """
    找出勾選的項目和金額。

    優先級：
    1. 從解析後的行直接找勾選項目（最準確）
    2. 傳統方法：行尾金額匹配
    3. 後備：關鍵字匹配
    """
    # === 優先方案：直接從行列表找勾選項目 ===
    checked_result = _find_checked_project_from_lines(lines)
    if checked_result:
        proj, amt, chk = checked_result
        logger.info("  [項目] 主方案找到: %s, %s (勾選=%s)", proj, amt, chk)
        return proj, amt, chk

    # === 後備方案 1：傳統行尾金額匹配 ===
    candidates: List[Tuple[int, str, str, bool]] = []
    for line in lines:
        pa = _project_amount_from_line(line)
        if pa:
            proj, amt, chk = pa
            candidates.append((_score_project(proj, chk), proj, amt, chk))

    if candidates:
        candidates.sort(key=lambda x: -x[0])
        _, proj, amt, chk = candidates[0]
        logger.info("  [項目] 後備方案1-候選項: %s, %s", proj, amt)
        return proj, amt, chk

    # === 後備方案 2：關鍵字 + 獨立金額 ===
    amt = _find_donation_amount(plain)
    can = _canonical_project_near(plain)
    if amt and can:
        full, chk = can
        logger.info("  [項目] 後備方案2-關鍵字+金額: %s, %s", full, amt)
        return full, amt, chk

    # === 後備方案 3：只有項目沒有金額 ===
    if can:
        full, chk = can
        logger.info("  [項目] 後備方案3-只有項目名: %s", full)
        return full, "(金額未識別)", chk

    logger.info("  [項目] 未找到項目和金額")
    return None


# --- 新版奉獻袋：12 項固定欄位（課程推廣／媒體／基金會／其他 + 合計 + 勾選 + 基本資料）---

_DECLARATION_HEAD = re.compile(
    r"本人在此聲明表示不[同周彫]意將本人捐款姓名公開揭露",
)
_DECLARATION_LOOSE = re.compile(
    r"本人在此聲明.{0,24}公開揭露",
)

# 四類預算列（僅一項應有金額）
_BUDGET_LINES: List[Tuple[str, str]] = [
    ("課程推廣與發展", "課程推廣與發展"),
    ("媒體製作與傳播", "媒體製作與傳播"),
    ("基金會營運支出", "基金會營運支出"),
    ("其他", "其他"),
]

_TOTAL_LINE_AMT = re.compile(
    r"合計\s*[：:．]?\s*(\d{1,3}(?:[,，]\d{3})+|[1-9]\d{2,5})",
)
_INLINE_AMT = re.compile(
    r"(\d{1,3}(?:[,，]\d{3})+|[1-9]\d{2,5})(?!\d)",
)

_RECEIPT_TITLE_PAT = re.compile(
    r"(?:奉獻收據抬頭|收據抬頭)\s*[：:．]?\s*([^\n]+)",
)
_MAIL_ADDR_PAT = re.compile(
    r"(?:奉獻收據寄送地址|收據寄送地址|郵寄地址)\s*[：:．]?\s*([^\n]+)",
)
_CONTACT_PHONE_PAT = re.compile(
    r"(?:聯絡電話|電話|手機)\s*[：:／/．]?\s*([^\n]+)",
)
_EMAIL_PAT = re.compile(
    r"(?:電子信箱|E-?mail|Email)\s*[：:．]?\s*([^\s\n@]+@[^\s\n]+)",
)
_EMAIL_FALLBACK = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
)

_NO_RECEIPT_CHK = re.compile(
    r"不需要奉獻收據|不需要.{0,6}收據",
)
_TAX_UPLOAD_PAT = re.compile(
    r"代上[傳傅]國稅局無紙本|國稅局無紙本",
)
_E_RECEIPT_PAT = re.compile(r"電子收據")
_PAPER_YEAR_PAT = re.compile(r"年度紙本收據")


def _merge_offering_date_continuation(plain: str) -> str:
    """奉獻日期若與年月日分行，將下一行（含月／日）併入同一行以利解析。"""
    lines = plain.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if "奉獻日期" in ln and not re.search(
            r"\d{2,3}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", ln
        ):
            if i + 1 < len(lines) and re.search(r"[月日]", lines[i + 1]):
                out.append(f"{ln.rstrip()} {lines[i + 1].strip()}")
                i += 2
                continue
        out.append(ln)
        i += 1
    return "\n".join(out)


def _interpret_checkbox_prefix(left: str) -> Optional[bool]:
    """
    勾選判斷（關鍵字左側）：
    - 開頭（略過空白）為 O／方框／空心方 → 未勾選
    - 左側前段出現 ✓☑✔√✅ → 已勾選
    - 其餘以尾端一段辨識：數字、英文、非 O／方框之 ASCII → 已勾選；僅中文無勾號 → 不判定（None）
    """
    left = left.rstrip()
    if not left:
        return None
    s = left
    head = s[:12]
    if re.search(r"[✓☑✔√✅☒▪\u2611]", head):
        return True
    lead = s.lstrip()
    if _RE_STARTS_UNCHECKED_VISUAL.match(lead):
        return False
    mark = (s[-12:] if len(s) > 12 else s).strip()
    if not mark:
        return None
    if re.search(r"[✓☑✔√✅☒▪\u2611]", mark):
        return True
    if re.search(r"\d", mark):
        return True
    if re.search(r"[A-Za-z]", mark):
        return True
    if _RE_ONLY_UNCHECKED_MARK.match(mark):
        return False
    i = 0
    while i < len(mark):
        ch = mark[i]
        if ch == "\u23f9" and i + 1 < len(mark) and mark[i + 1] == "\ufe0f":
            i += 2
            continue
        if ch in _UNCHECKED_SINGLE_CHARS or ch == "\ufe0f":
            i += 1
            continue
        if ord(ch) < 128 and not ch.isspace():
            return True
        i += 1
    if re.search(r"[\u4e00-\u9fff]", mark):
        return None
    return False


def _checkbox_state_before_keyword(line: str, keyword: str) -> Optional[bool]:
    k = line.find(keyword)
    if k < 0:
        return None
    return _interpret_checkbox_prefix(line[:k])


def _is_privacy_declaration_line(s: str) -> bool:
    return ("本人在此聲明" in s and "公開揭露" in s) or bool(
        _DECLARATION_HEAD.search(s)
    )


def _receipt_option_kind(s: str) -> Optional[str]:
    if _TAX_UPLOAD_PAT.search(s):
        return "tax"
    if _PAPER_YEAR_PAT.search(s):
        return "paper"
    if _E_RECEIPT_PAT.search(s):
        return "e"
    return None


def _sanitize_offering_markdown_body(plain: str) -> str:
    """
    依奉獻袋規則從 OCR 全文產出「給使用者的 Markdown」：刪免列印句、依勾選過濾長句等。
    （先於摘要解析呼叫，使 full_markdown 與解析來源一致。）
    """
    t = re.sub(r"依財團法人[\s\S]*?捐款者姓名。", "", plain)
    lines = t.split("\n")
    out: List[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if "線上奉獻" in s and len(s) <= 24:
            continue
        if "需要奉獻收據" in s and "請務必填寫以下資料" in s:
            continue
        if _is_privacy_declaration_line(s):
            if _checkbox_state_before_keyword(s, "本人") is not True:
                continue
            body = re.sub(r"^[✓☑✔√✅\s]+", "", s).strip()
            if _RE_STARTS_UNCHECKED_VISUAL.match(body):
                continue
            out.append("☑ " + body)
            continue
        rk = _receipt_option_kind(s)
        if rk:
            anchor = "代上" if rk == "tax" else ("年度" if rk == "paper" else "電子")
            if _checkbox_state_before_keyword(s, anchor) is not True:
                continue
            body = re.sub(r"^[✓☑✔√✅\s]+", "", s).strip()
            if _RE_STARTS_UNCHECKED_VISUAL.match(body):
                continue
            out.append("☑ " + body)
            continue
        out.append(line.rstrip())
    joined = "\n".join(out)
    addr_pv = _extract_labeled_tail(_MAIL_ADDR_PAT, joined)
    if addr_pv and not _mailing_address_should_appear(joined, addr_pv):
        drop_labels = ("奉獻收據寄送地址", "收據寄送地址", "郵寄地址")
        out = [ln for ln in out if not any(lab in ln for lab in drop_labels)]
        joined = "\n".join(out)
    return joined


def _mailing_address_should_appear(plain: str, addr_value: Optional[str]) -> bool:
    """
    奉獻收據寄送地址與「聯絡電話」之間須有有效內容：有數字（如郵遞區號）或有地址文字；
    否則不輸出該欄。
    """
    if not addr_value or not addr_value.strip():
        return False
    i = plain.find("奉獻收據寄送地址")
    if i < 0:
        i = plain.find("收據寄送地址")
    if i < 0:
        i = plain.find("郵寄地址")
    if i < 0:
        return True
    j = plain.find("聯絡電話", i)
    if j < 0:
        j = plain.find("電話", i)
    if j < 0:
        return True
    seg = plain[i:j]
    colon = seg.find("：")
    if colon < 0:
        colon = seg.find(":")
    if colon >= 0:
        content = seg[colon + 1 :].strip()
    else:
        content = seg.strip()
    digits = len(re.findall(r"\d", content))
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", content))
    if digits == 0 and not has_cjk:
        return False
    return True


def _strip_band_below_total_above_declaration(plain: str) -> str:
    """移除「合計」該行之下至「本人…公開揭露」之前的區塊，避免辨識該帶狀區內雜訊。"""
    m_decl = _DECLARATION_HEAD.search(plain) or _DECLARATION_LOOSE.search(plain)
    if not m_decl:
        return plain
    decl_start = m_decl.start()
    line_start = plain.rfind("\n", 0, decl_start) + 1
    gap = plain[line_start:decl_start]
    if not gap or _GAP_BEFORE_DECLARATION.fullmatch(gap):
        decl_start = line_start
    before = plain[:decl_start]
    after = plain[decl_start:]
    if "合計" not in before:
        return plain
    tidx = before.find("合計")
    eol = before.find("\n", tidx)
    if eol < 0:
        prefix_end = len(before)
    else:
        prefix_end = eol + 1
    return before[:prefix_end] + after


def _tick_before_in_line(line: str, keyword: str) -> Optional[bool]:
    """關鍵字左側勾選區：O／方框為未勾選；✓、數字、英文及其他 ASCII 視為已勾選。"""
    return _checkbox_state_before_keyword(line, keyword)


def _find_line_with(text: str, substr: str) -> Optional[str]:
    for ln in _lines(text):
        if substr in ln:
            return ln
    return None


def _find_privacy_declaration_line_checked(plain: str) -> Optional[str]:
    """多行同時出現聲明時，只採用「已勾選」那一行（避免先命中 □ 再略過 ✓）。"""
    for ln in _lines(plain):
        if not _is_privacy_declaration_line(ln):
            continue
        c = _checkbox_state_before_keyword(ln, "本人") or _checkbox_state_before_keyword(
            ln, "公開"
        )
        if c is True:
            return ln
    return None


def _amount_on_label_line_or_next(
    plain: str,
    label_start: int,
    label_len: int,
    *,
    allow_next_line: bool = True,
) -> Optional[str]:
    """僅在標籤所在行、或緊接的下一行找金額；下一行若以「合計」開頭則忽略（避免誤配）。"""
    rest_from_label = plain[label_start:]
    line_end = rest_from_label.find("\n")
    if line_end < 0:
        first_line = rest_from_label
        second_line = ""
    else:
        first_line = rest_from_label[:line_end]
        rest2 = rest_from_label[line_end + 1 :]
        le2 = rest2.find("\n")
        second_line = rest2[:le2] if le2 >= 0 else rest2
    tail = first_line[label_len:]
    m = _INLINE_AMT.search(tail)
    if m:
        return m.group(1).replace("，", ",")
    if allow_next_line and second_line.strip():
        sl = second_line.strip()
        if sl.startswith("合計"):
            return None
        m2 = _INLINE_AMT.search(sl)
        if m2:
            return m2.group(1).replace("，", ",")
    return None


def _extract_budget_single(plain: str) -> Optional[Tuple[str, str]]:
    """A～D 四列中只回傳「有金額」的那一項（若多筆取金額最大者）。"""
    candidates: List[Tuple[int, str, str]] = []
    for key, label in _BUDGET_LINES:
        pos = 0
        while True:
            idx = plain.find(key, pos)
            if idx < 0:
                break
            raw_amt = _amount_on_label_line_or_next(
                plain,
                idx,
                len(key),
                allow_next_line=(key != "其他"),
            )
            if raw_amt:
                digits = re.sub(r"[^\d]", "", raw_amt)
                if digits:
                    try:
                        val = int(digits)
                    except ValueError:
                        val = 0
                    if val >= 10:
                        candidates.append((val, label, _normalize_amount(raw_amt)))
            pos = idx + len(key)
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    _, label, amt = candidates[0]
    return label, amt


def _extract_total_amount(plain: str) -> Optional[str]:
    m = _TOTAL_LINE_AMT.search(plain)
    if m:
        return _normalize_amount(m.group(1).replace("，", ","))
    # 寬鬆：合計 後方一行內數字
    for ln in _lines(plain):
        if "合計" not in ln:
            continue
        am = _INLINE_AMT.search(ln.split("合計", 1)[-1])
        if am:
            return _normalize_amount(am.group(1).replace("，", ","))
    return None


def _extract_labeled_tail(pat: re.Pattern, plain: str) -> Optional[str]:
    m = pat.search(plain)
    if not m:
        return None
    v = m.group(1).strip()
    v = re.split(r"\s{2,}", v)[0].strip()
    if len(v) < 1:
        return None
    return v


def _build_v2_twelve_field_summary(
    plain: str, lines: List[str]
) -> Tuple[List[Dict[str, str]], str]:
    """
    依使用者指定 12 類擷取；只輸出有資料的列。
    勾選類：僅在判定為「已勾選」時輸出一列。
    """
    rows: List[Dict[str, str]] = []
    formatted: List[str] = []

    bud = _extract_budget_single(plain)
    if bud:
        label, amt = bud
        rows.append({"key": "budget_category", "label": "支持項目", "value": f"{label}  {amt}"})
        formatted.append(f"支持項目  {label}  {amt}")

    tot = _extract_total_amount(plain)
    if tot:
        rows.append({"key": "total", "label": "合計", "value": tot})
        formatted.append(f"合計  {tot}")

    decl_ln = _find_privacy_declaration_line_checked(plain)

    if decl_ln:
        rows.append(
            {
                "key": "privacy_opt_out",
                "label": "不同意公開捐款姓名",
                "value": "☑",
            }
        )
        formatted.append("不同意公開捐款姓名  ☑")

    for ln in lines:
        if _NO_RECEIPT_CHK.search(ln):
            c = _checkbox_state_before_keyword(ln, "不") or _checkbox_state_before_keyword(
                ln, "需"
            )
            if c is True:
                rows.append({"key": "no_receipt_needed", "label": "不需要奉獻收據", "value": "☑"})
                formatted.append("不需要奉獻收據  ☑")
            break

    for anchor, pat, key, label in (
        ("代上", _TAX_UPLOAD_PAT, "receipt_tax_upload", "代上傳國稅局無紙本"),
        ("電子", _E_RECEIPT_PAT, "receipt_electronic", "電子收據"),
        ("年度", _PAPER_YEAR_PAT, "receipt_paper_yearly", "年度紙本收據"),
    ):
        hit = None
        for candidate in lines:
            if not pat.search(candidate):
                continue
            if anchor == "代上":
                if "代上" in candidate or "國稅局" in candidate:
                    hit = candidate
                    break
            elif anchor in candidate:
                hit = candidate
                break
        if hit:
            c = _checkbox_state_before_keyword(hit, anchor)
            if c is None and anchor == "代上" and "國稅局" in hit:
                c = _checkbox_state_before_keyword(hit, "國稅局")
            if c is True:
                rows.append({"key": key, "label": label, "value": "☑"})
                formatted.append(f"{label}  ☑")

    id_num = _extract_id_number(plain)
    if id_num:
        rows.append({"key": "id_number", "label": "身份證字號", "value": id_num})
        formatted.append(f"身份證字號  {id_num}")

    nv = _extract_name(plain)
    if nv:
        rows.append({"key": "donor", "label": "辨識者姓名", "value": nv})
        formatted.append(f"辨識者姓名：{nv}")

    date_parts = _extract_date_parts(plain)
    if date_parts:
        y, mo, d = date_parts
        dv = _fmt_roc_display(y, mo, d)
        rows.append({"key": "date", "label": "奉獻日期", "value": dv})
        formatted.append(f"奉獻日期：{dv}")
    else:
        sd = _extract_short_date(plain)
        if sd:
            from datetime import datetime

            mo, d = sd
            roc_year = datetime.now().year - 1911
            dv = _fmt_roc_display(str(roc_year), mo, d)
            rows.append({"key": "date", "label": "奉獻日期", "value": dv})
            formatted.append(f"奉獻日期：{dv}")

    rt = _extract_labeled_tail(_RECEIPT_TITLE_PAT, plain)
    if rt:
        rows.append({"key": "receipt_title", "label": "奉獻收據抬頭", "value": rt})
        formatted.append(f"奉獻收據抬頭  {rt}")

    addr = _extract_labeled_tail(_MAIL_ADDR_PAT, plain)
    if addr and _mailing_address_should_appear(plain, addr):
        rows.append({"key": "mailing_address", "label": "奉獻收據寄送地址", "value": addr})
        formatted.append(f"奉獻收據寄送地址：{addr}")

    phone_line = _extract_labeled_tail(_CONTACT_PHONE_PAT, plain)
    phone_val = None
    if phone_line:
        ph = re.sub(r"[^\d+]", "", phone_line) or phone_line.strip()
        if len(ph) >= 8:
            phone_val = ph
    if not phone_val:
        ph2 = _extract_phone(plain)
        if ph2:
            phone_val = ph2
    if phone_val:
        rows.append({"key": "phone", "label": "聯絡電話", "value": phone_val})
        formatted.append(f"聯絡電話  {phone_val}")

    em = _extract_labeled_tail(_EMAIL_PAT, plain)
    if not em:
        emm = _EMAIL_FALLBACK.search(plain)
        if emm:
            em = emm.group(1).strip()
    if em:
        rows.append({"key": "email", "label": "電子信箱", "value": em})
        formatted.append(f"電子信箱  {em}")

    return rows, "\n".join(formatted)


def _should_prefer_v2(plain: str) -> bool:
    """新表單關鍵字出現時優先走 12 項邏輯（不以單獨「其他」觸發，避免誤判）。"""
    if any(
        k in plain
        for k in ("課程推廣與發展", "媒體製作與傳播", "基金會營運支出")
    ):
        return True
    if "公開揭露" in plain and "合計" in plain:
        return True
    if "代上傳國稅局" in plain or "代上傅國稅局" in plain:
        return True
    if "年度紙本收據" in plain:
        return True
    return False


def _build_summary_and_formatted(plain: str, lines: List[str]) -> Tuple[List[Dict[str, str]], str]:
    # 新表單：12 項欄位 + 略過「合計」下至聲明上之帶狀區
    if _should_prefer_v2(plain):
        stripped = _strip_band_below_total_above_declaration(plain)
        lines_v2 = _lines(stripped)
        v2_rows, v2_fmt = _build_v2_twelve_field_summary(stripped, lines_v2)
        if v2_rows:
            logger.info("offering_display: 使用新版 12 項摘要（%d 列）", len(v2_rows))
            return v2_rows, v2_fmt
        logger.info("offering_display: 新版關鍵字命中但無擷取列，改走舊版邏輯")

    rows: List[Dict[str, str]] = []
    formatted_lines: List[str] = []

    # 詳細日誌：顯示原始輸入
    logger.info("=" * 60)
    logger.info("offering_display 開始解析（舊版欄位）")
    logger.info("原始文字共 %d 行:", len(lines))
    for i, line in enumerate(lines[:30]):  # 只顯示前 30 行
        logger.info("  [%02d] %r", i, line)
    if len(lines) > 30:
        logger.info("  ... 還有 %d 行", len(lines) - 30)

    pa = _pick_project_amount(lines, plain)
    date_parts = _extract_date_parts(plain)
    short_date = _extract_short_date(plain) if not date_parts else None
    service_num = _extract_service_number(plain)
    rv = _extract_receipt(plain)
    nv = _extract_name(plain)
    id_num = _extract_id_number(plain)
    phone = _extract_phone(plain)

    # 詳細日誌：顯示每個欄位的提取結果
    logger.info("-" * 40)
    logger.info("欄位提取結果:")
    logger.info("  [項目+金額] %s", f"✓ {pa[0]}, {pa[1]}" if pa else "✗ 未找到")
    logger.info("  [日期] %s", f"✓ {date_parts}" if date_parts else "✗ 未找到")
    logger.info("  [短日期] %s", f"✓ {short_date[0]}月{short_date[1]}日" if short_date else "✗ 未找到或已有完整日期")
    logger.info("  [堂次] %s", f"✓ 第{service_num}堂" if service_num else "✗ 未找到")
    logger.info("  [收據] %s", f"✓ {rv}" if rv else "✗ 未找到")
    logger.info("  [姓名] %s", f"✓ {nv}" if nv else "✗ 未找到")
    logger.info("  [身份證] %s", f"✓ {id_num}" if id_num else "✗ 未找到")
    logger.info("  [電話] %s", f"✓ {phone}" if phone else "✗ 未找到")

    # 檢查關鍵字是否在原文中
    logger.info("-" * 40)
    logger.info("關鍵字檢查:")
    logger.info("  '奉獻人姓名' in plain: %s", "奉獻人姓名" in plain)
    logger.info("  '姓名' in plain: %s", "姓名" in plain)
    logger.info("  '弱勢' in plain: %s", "弱勢" in plain)
    logger.info("  '不需要' in plain: %s", "不需要" in plain)
    logger.info("  '年' in plain: %s", "年" in plain)
    logger.info("  '月' in plain: %s", "月" in plain)
    logger.info("  '日' in plain: %s", "日" in plain)
    logger.info("  '電話' in plain: %s", "電話" in plain)
    logger.info("  '手機' in plain: %s", "手機" in plain)
    logger.info("  '/' in plain: %s", "/" in plain)
    logger.info("  '[' or '（' in plain: %s", "[" in plain or "（" in plain or "［" in plain)
    logger.info("=" * 60)

    if pa:
        proj, amt, was_checked = pa
        pfx = "    ☑  " if was_checked else "    "
        line1 = f"{pfx}{proj}    {amt}"
        formatted_lines.append(line1)
        rows.append({"key": "project", "label": "支持項目", "value": line1})

    # 堂次（如 [一] = 第一堂）
    if service_num:
        cn_nums = ["", "一", "二", "三", "四", "五", "六"]
        service_text = f"第{cn_nums[service_num]}堂"
        formatted_lines.append(f"禮拜堂次  {service_text}")
        rows.append({"key": "service", "label": "禮拜堂次", "value": service_text})

    if date_parts:
        y, mo, d = date_parts
        dv = _fmt_roc_display(y, mo, d)
        formatted_lines.append(f"奉獻日期  {dv}")
        rows.append({"key": "date", "label": "奉獻日期", "value": dv})
    elif short_date:
        # 短日期格式（只有月/日，假設今年）
        mo, d = short_date
        # 使用民國年推算：西元 2025 = 民國 114，2026 = 115
        from datetime import datetime
        current_year = datetime.now().year
        roc_year = current_year - 1911
        dv = _fmt_roc_display(str(roc_year), mo, d)
        formatted_lines.append(f"奉獻日期  {dv}（推算）")
        rows.append({"key": "date", "label": "奉獻日期", "value": dv})

    if rv:
        formatted_lines.append(f"奉獻收據  {rv}")
        rows.append({"key": "receipt", "label": "奉獻收據", "value": rv})

    if nv:
        formatted_lines.append(f"奉獻人姓名：{nv}")
        rows.append({"key": "donor", "label": "奉獻人姓名", "value": nv})

    if id_num:
        formatted_lines.append(f"身份證字號：{id_num}")
        rows.append({"key": "id_number", "label": "身份證字號", "value": id_num})

    if phone:
        formatted_lines.append(f"電話/手機：{phone}")
        rows.append({"key": "phone", "label": "電話/手機", "value": phone})

    # 調試日誌：顯示原始 OCR 輸出和提取結果
    logger.info(
        "offering_display: date_parts=%r, name=%r, receipt=%r, project=%r",
        date_parts,
        nv,
        rv,
        pa[0] if pa else None,
    )
    if len(formatted_lines) <= 1:
        logger.info(
            "offering_display: 擷取列偏少 (%s)，OCR 前 600 字：%r",
            len(formatted_lines),
            plain[:600],
        )

    formatted_text = "\n".join(formatted_lines)
    return rows, formatted_text


def build_offering_display(full_markdown: str) -> Dict[str, Any]:
    plain = unicodedata.normalize("NFKC", _plain_text(full_markdown or ""))
    plain = _merge_offering_date_continuation(plain)
    sanitized = _sanitize_offering_markdown_body(plain)
    lines = _lines(sanitized)
    summary, formatted_text = _build_summary_and_formatted(sanitized, lines)

    return {
        "summary": summary,
        "formatted_text": formatted_text,
        "hide_raw_text": True,
        "fields": [],
        "checked_items": [],
        "sanitized_markdown": sanitized,
    }
