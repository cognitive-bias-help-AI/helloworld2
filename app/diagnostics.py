"""Local-only stderr diagnostics for the review execution path."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from typing import Any


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


__all__ = ["debug_enabled", "debug_log"]
