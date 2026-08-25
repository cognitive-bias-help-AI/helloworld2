"""Local-only stderr diagnostics for the review execution path."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError


def debug_enabled() -> bool:
    return os.getenv("REVIEW_DEBUG_LOGS", "").strip().lower() in {"1", "true", "yes", "on"}


def debug_log(scope: str, event: str, **fields: Any) -> None:
    if not debug_enabled():
        return
    parts = [datetime.now(UTC).isoformat(), f"[{scope}]", event]
    for key, value in fields.items():
        if value is None:
            continue
        rendered = json.dumps(value, ensure_ascii=False, default=str)
        parts.append(f"{key}={rendered}")
    print(" ".join(parts), file=sys.stderr, flush=True)


def safe_exception_fields(error: BaseException) -> dict[str, object]:
    """Return bounded diagnostics without arbitrary exception messages."""
    fields: dict[str, object] = {"exception_type": type(error).__name__}
    if hasattr(error, "category"):
        for name in ("category", "slot_id", "semantic_kind", "segment_id"):
            value = getattr(error, name, None)
            if value is not None:
                fields[name] = value
        return fields
    if isinstance(error, FileNotFoundError):
        known = {"krx_stock_master.json", "stock_directory.csv", "dart_corp_code.json"}
        names = [part for part in known if part in str(error)]
        fields["artifact"] = names[0] if names else "unknown"
        return fields
    if isinstance(error, ValidationError):
        fields["errors"] = [
            {"type": item.get("type"), "loc": list(item.get("loc", ()))}
            for item in error.errors(include_context=False)
        ]
        return fields
    if isinstance(error, RuntimeError):
        safe_codes = {
            "KIWOOM_ENV", "MODEL_BACKEND", "DART_API_KEY", "NAVER_CLIENT_ID",
            "NAVER_CLIENT_SECRET", "LUNA_API_URL", "LUNA_API_KEY",
            "TERRA_API_URL", "TERRA_API_KEY", "SOL_API_URL", "SOL_API_KEY",
        }
        for code in safe_codes:
            if code in str(error):
                fields["config"] = code
                break
    return fields


__all__ = ["debug_enabled", "debug_log", "safe_exception_fields"]
