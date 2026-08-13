"""PostToolUse(Edit|Write) 훅 — 방금 고친 파일만 즉시 린트한다.

전체 저장소를 돌리지 않는 이유: 편집마다 몇 초씩 붙으면 아무도 훅을 켜두지 않는다.
전체 검사는 Stop 훅(verify.py)이 한 번 한다.

훅 입력은 stdin 으로 오는 JSON 이다. tool_input.file_path 를 읽는다.
파싱에 실패해도 절대 편집을 막지 않는다(exit 0) — 린터 때문에 작업이 죽으면 안 된다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    path = (payload.get("tool_input") or {}).get("file_path")
    if not path or not str(path).endswith(".py"):
        return 0

    target = Path(path)
    if not target.exists():
        return 0

    proc = subprocess.run(
        ["uv", "run", "ruff", "check", str(target)],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        # exit 2 로 올려야 에이전트가 결과를 읽고 고친다. stdout 은 무시된다.
        print(proc.stdout + proc.stderr, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
