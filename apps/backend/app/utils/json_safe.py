"""
將 metadata 等結構轉成可 json 序列化的純 Python 型別（避免 PIL IFDRational 等）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Union

_JSONScalar = Union[str, int, float, bool, None]


def json_sanitize(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    try:
        import numpy as np  # type: ignore[import-not-found]

        if isinstance(obj, np.generic):
            return obj.item()
    except ImportError:
        pass
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {str(k): json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_sanitize(x) for x in obj]
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    try:
        if hasattr(obj, "numerator") and hasattr(obj, "denominator"):
            return float(obj)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    try:
        return float(obj)
    except (TypeError, ValueError):
        pass
    try:
        return int(obj)
    except (TypeError, ValueError):
        pass
    return str(obj)


def sanitize_metadata_dict(meta: Dict[str, Any]) -> Dict[str, Any]:
    """淺層常用欄位強制轉換，並遞迴清理其餘鍵。"""
    return json_sanitize(meta)  # type: ignore[return-value]
