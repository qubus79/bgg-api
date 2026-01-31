from typing import Any, Optional


def to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """Safe conversion of JSON/XML values to integer."""

    if value is None:
        return default

    try:
        text = str(value).strip()
        if text == "":
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Safe conversion of JSON/XML values to float."""

    if value is None:
        return default

    try:
        text = str(value).strip()
        if text == "":
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def to_bool(value: Any) -> Optional[bool]:
    """Normalize boolean-ish values coming from BGG (_=0/1, yes/no)."""

    if value is None:
        return None

    text = str(value).strip().lower()
    if text in ("1", "true", "yes"):
        return True
    if text in ("0", "false", "no"):
        return False
    return None
