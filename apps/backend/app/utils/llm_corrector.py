"""
LLM 後處理糾正模組

在 13 階段規則引擎完成後，透過 GPT-4o-mini 修正
手寫 OCR 辨識錯誤（姓名誤字、電子信箱網域錯誤等），
同時保留所有不應被修改的欄位（日期、身分證、金額、電話）。

設計原則：
- 失敗靜默降級（fallback 到規則引擎原輸出）
- 有逾時保護（預設 15 秒）
- 不依賴 openai 套件，改用 httpx 直接呼叫 REST API
  （避免增加強制依賴，httpx 為已有套件）
"""

from __future__ import annotations

import json
import re
from typing import Optional

import httpx

from app.utils.config import settings
from app.utils.logger import logger

# OpenAI Chat Completions 端點
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# 系統提示：只修正手寫 OCR 誤讀，保留不可動欄位
_SYSTEM_PROMPT = """你是奉獻袋手寫 OCR 後處理助手，任務是修正因手寫字跡辨識錯誤造成的明顯錯誤。

## 可以修正的欄位
- 奉獻者姓名：修正相似字誤讀（如「玲→玩」、「琳→琳」）及重複字元（「嫚嫚→嫚」）
- 奉獻收據抬頭：若與奉獻者姓名高度相似，優先保持姓名一致
- 電子信箱：修正常見網域誤讀（`.con`→`.com`、`.cpm`→`.com`、`.con.tw`→`.com.tw`）
- 奉獻收據寄送地址：補全因 OCR 切割造成的明顯缺字

## 絕對不能修正的欄位（原樣保留，即使看起來有誤）
- 奉獻日期（例如「32日」等不合理日期仍保留）
- 身分證字號
- 任何金額（含捐獻項目金額、合計）
- 電話號碼

## 輸出規則
- 只輸出修正後的結果，格式與輸入完全一致（每行一個欄位）
- 不加任何說明、前言、JSON 包裝
- 若不需要修正則原樣輸出"""

_USER_PROMPT_TEMPLATE = """請修正以下奉獻袋 OCR 結構化結果中的手寫辨識錯誤：

{structured_output}"""


def _is_enabled() -> bool:
    """是否已設定 OpenAI API Key"""
    return bool(settings.OPENAI_API_KEY and settings.OPENAI_API_KEY.strip())


def _build_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }


def _build_payload(structured_output: str) -> dict:
    return {
        "model": settings.LLM_CORRECTION_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _USER_PROMPT_TEMPLATE.format(
                    structured_output=structured_output
                ),
            },
        ],
        "temperature": 0.0,   # 確定性輸出，不需要創意
        "max_tokens": 512,
    }


def _parse_llm_response(raw: str, original: str) -> str:
    """
    解析 LLM 回傳文字，確保格式正確。
    若解析結果行數與原始不符（代表 LLM 亂寫），降級回原始輸出。
    """
    cleaned = raw.strip()
    if not cleaned:
        return original

    orig_lines = [l for l in original.strip().splitlines() if l.strip()]
    new_lines = [l for l in cleaned.splitlines() if l.strip()]

    if not new_lines:
        return original

    # 若行數落差超過 2，代表 LLM 輸出不符預期，保守降級
    if abs(len(new_lines) - len(orig_lines)) > 2:
        logger.warning(
            "[LLM糾正] 行數差異過大（原始=%d，LLM=%d），降級使用原始輸出",
            len(orig_lines),
            len(new_lines),
        )
        return original

    return "\n".join(new_lines)


def _log_diff(original: str, corrected: str) -> None:
    """記錄修正前後差異（僅記錄有變化的行）"""
    orig_lines = original.strip().splitlines()
    corr_lines = corrected.strip().splitlines()
    changed = False
    for i, (a, b) in enumerate(zip(orig_lines, corr_lines)):
        if a != b:
            logger.info("[LLM糾正] 第%d行  修正前: %r", i + 1, a)
            logger.info("[LLM糾正] 第%d行  修正後: %r", i + 1, b)
            changed = True
    if not changed:
        logger.info("[LLM糾正] 無需修正，輸出與原始相同")


async def correct_donation_output(
    structured_output: str,
    *,
    timeout: float = 15.0,
) -> Optional[str]:
    """
    使用 LLM 修正奉獻袋 OCR 結構化輸出中的手寫辨識錯誤。

    Args:
        structured_output: 規則引擎產出的結構化文字（每行一欄位）
        timeout:           HTTP 逾時秒數（預設 15 秒）

    Returns:
        修正後的結構化文字；若未啟用、逾時或發生錯誤則回傳 None（呼叫端保留原始輸出）
    """
    if not structured_output or not structured_output.strip():
        return None

    if not _is_enabled():
        logger.debug("[LLM糾正] OPENAI_API_KEY 未設定，跳過 LLM 糾正")
        return None

    logger.info("[LLM糾正] 開始呼叫 LLM 糾正（model=%s）", settings.LLM_CORRECTION_MODEL)
    logger.debug("[LLM糾正] 輸入:\n%s", structured_output)

    payload = _build_payload(structured_output)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                _OPENAI_URL,
                headers=_build_headers(),
                json=payload,
            )
            resp.raise_for_status()

        data = resp.json()
        llm_text = data["choices"][0]["message"]["content"]
        corrected = _parse_llm_response(llm_text, structured_output)

        _log_diff(structured_output, corrected)
        logger.info("[LLM糾正] 完成")
        return corrected

    except httpx.TimeoutException:
        logger.warning("[LLM糾正] 逾時（%.1f 秒），降級使用規則引擎輸出", timeout)
    except httpx.HTTPStatusError as e:
        logger.warning("[LLM糾正] HTTP 錯誤 %d：%s，降級使用規則引擎輸出", e.response.status_code, e.response.text[:200])
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.warning("[LLM糾正] 解析回應失敗：%s，降級使用規則引擎輸出", e)
    except Exception as e:
        logger.warning("[LLM糾正] 未預期錯誤：%s，降級使用規則引擎輸出", e)

    return None
