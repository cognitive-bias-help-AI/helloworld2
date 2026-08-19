"""Deterministic user-text sanitization shared by runtime boundaries."""

from __future__ import annotations

import re


def sanitize_user_text(value: str) -> str:
    """Apply the existing n0 whitespace, email, and phone masking policy."""

    value = " ".join(value.split())
    value = re.sub(r"[\w.+-]+@[\w.-]+", "[EMAIL]", value)
    return re.sub(r"\b01[016789]-?\d{3,4}-?\d{4}\b", "[PHONE]", value)
