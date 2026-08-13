"""Stop 훅 — 세션을 닫기 전 자가 검증.

🔴 exit 2 가 핵심이다. 종료를 막고 에이전트가 로그를 읽어 스스로 고치게 만든다.
   컨텍스트가 차면 에이전트는 코너를 자르기 시작한다 — 계약 테스트를 안 돌리고
   "통과했습니다" 라고 하거나, union 검사를 빼먹는다. 이 프로젝트에서 그건 곧
   제품이 사용자에게 거짓을 인쇄하는 경로가 된다.

T3 §3.2 는 verify.sh 로 적혀 있으나 Python 으로 옮겼다.
이유: 이 머신에서 `bash` 는 git-bash 가 아니라 WSL 로 해소되고(C:\\windows\\system32\\bash.exe),
     WSL 은 다른 파일시스템 뷰를 본다. .sh 훅은 엉뚱한 경로를 검사하게 된다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 🔴 Windows 기본 인코딩(cp949)은 한글 출력을 디코드하다 죽고 stdout 을 None 으로 만든다.
_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

# (표시 이름, 명령, 그 단계가 아직 존재하지 않아도 되는가)
STEPS: list[tuple[str, list[str], bool]] = [
    ("pytest", ["uv", "run", "pytest", "-q"], True),
    ("ruff", ["uv", "run", "ruff", "check", ".", "--quiet"], False),
    ("불변식", ["uv", "run", "python", "-m", "ci.invariants"], True),
    ("체크포인트 예산", ["uv", "run", "python", "tools/measure_state.py",
                         "--assert-under", "5120"], True),
]


def main() -> int:
    if shutil.which("uv") is None:
        print("🔴 uv 가 PATH 에 없다. 검증을 건너뛰지 말고 uv 를 설치해라.", file=sys.stderr)
        return 2

    failures: list[str] = []
    for name, cmd, skippable in STEPS:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=_ENV)
        if proc.returncode == 0:
            continue
        # 아직 안 만든 단계(ci/invariants 미구현 등)는 부트스트랩 동안 통과시킨다.
        merged = ((proc.stdout or "") + (proc.stderr or "")).lower()
        bootstrap_gap = skippable and (
            "no module named" in merged
            or "no tests ran" in merged
            or "file or directory not found" in merged
            or "can't open file" in merged
        )
        if bootstrap_gap:
            print(f"[skip] {name}: 아직 미구현 (부트스트랩)", file=sys.stderr)
            continue
        failures.append(name)
        print(f"\n===== {name} 실패 =====", file=sys.stderr)
        print(((proc.stdout or "") + (proc.stderr or ""))[-3000:], file=sys.stderr)

    if failures:
        print(
            f"\n🔴 검증 실패: {', '.join(failures)}. "
            "로그를 읽고 고친 뒤 다시 끝내라. 이대로 세션을 닫지 마라.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
