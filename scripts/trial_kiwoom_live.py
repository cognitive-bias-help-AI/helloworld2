"""Read-only development smoke check for the configured Kiwoom provider."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.runtime.local import load_dotenv
from providers.kiwoom.core import (
    Environment,
    KiwoomAdapter,
    KiwoomCredentials,
    KiwoomRequest,
)


def _safe_presence(name: str) -> str:
    return "SET" if os.environ.get(name, "").strip() else "UNSET"


def _print_stage(stage: str, environment: str, result) -> None:
    error = result.error
    data = result.data
    if isinstance(data, list):
        item_count = len(data)
    elif isinstance(data, dict):
        item_count = 1 if data else 0
    else:
        item_count = 0
    print(f"{stage}: environment={environment} status={result.status.value}")
    print(
        "  http_status={} return_code={} category={} retryable={} item_count={}".format(
            error.http_status if error else "n/a",
            error.code if error else 0,
            error.category.value if error else "none",
            error.retryable if error else False,
            item_count,
        )
    )


async def _run() -> None:
    load_dotenv()
    environment_name = os.environ.get("KIWOOM_ENV", "").strip().lower()
    print(f"KIWOOM_ENV={environment_name or 'UNSET'}")
    if environment_name not in {"mock", "production"}:
        print("configuration: status=unavailable selected_app_key=UNSET selected_secret=UNSET")
        return
    prefix = "MOCK" if environment_name == "mock" else "PROD"
    key_name = f"KIWOOM_{prefix}_APP_KEY"
    secret_name = f"KIWOOM_{prefix}_APP_SECRET"
    print(
        f"configuration: selected_app_key={_safe_presence(key_name)} "
        f"selected_secret={_safe_presence(secret_name)}"
    )
    app_key = os.environ.get(key_name, "").strip()
    secret_key = os.environ.get(secret_name, "").strip()
    if not app_key or not secret_key:
        print("configuration: status=unavailable")
        return

    environment = Environment(environment_name)
    async with httpx.AsyncClient() as client:
        adapter = KiwoomAdapter(client, KiwoomCredentials(app_key, secret_key))
        auth = await adapter.authenticate(environment)
        _print_stage("auth", environment.value, auth)
        if auth.status.value == "error":
            return
        base_date = datetime.now(UTC).strftime("%Y%m%d")
        requests = (
            ("ka10007", {"stk_cd": "005930"}),
            (
                "ka10081",
                {"stk_cd": "005930", "base_dt": base_date, "upd_stkpc_tp": "1"},
            ),
            (
                "ka10059",
                {
                    "dt": base_date,
                    "stk_cd": "005930",
                    "amt_qty_tp": "1",
                    "trde_tp": "0",
                    "unit_tp": "1",
                },
            ),
        )
        for tr, params in requests:
            result = await adapter.request(KiwoomRequest(tr, params, environment))
            _print_stage(tr, environment.value, result)


if __name__ == "__main__":
    asyncio.run(_run())
