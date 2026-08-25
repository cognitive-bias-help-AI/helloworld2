"""Deterministic user-text sanitization shared by runtime boundaries."""

from __future__ import annotations

import re


def sanitize_user_text(value: str) -> str:
    """Apply deterministic high-confidence masking before model invocation."""

    value = " ".join(value.split())
    value = re.sub(r"(?<!\d)\d{6}-?[1-8]\d{6}(?!\d)", "[RRN]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+", "[EMAIL]", value)
    return re.sub(r"\b01[016789]-?\d{3,4}-?\d{4}\b", "[PHONE]", value)
