"""CI 불변식 11종 — `uv run python -m ci.invariants`

DDR v2.2 §10. `--only I1,I2` 로 부분 실행한다.

🔴 이 파일은 부트스트랩 골격이다. I11 만 구현돼 있고 나머지 10종은 P0-7 에서 채운다.
   미구현을 조용히 통과시키지 않는다 — 돌지 않는 불변식은 없는 것과 같고,
   사이클이 있는 그래프(n9→n5, n10⟲)에 정지 보장이 사라진다.
   미구현 개수를 매 실행마다 크게 찍어서 "다 통과했다"는 착각을 막는다.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """🔴 Windows 기본 인코딩(cp949)으로는 한글 출력을 디코드하다 죽고
    proc.stdout 이 None 이 된다. 자식에도 UTF-8 을 강제한다."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    return subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )

# 결과: (통과 여부, 한 줄 사유). 미구현은 None 을 돌려준다.
Check = Callable[[], "tuple[bool, str] | None"]


def i11_checkpoint_budget() -> tuple[bool, str]:
    """체크포인트 blob 실측 회귀 — C=4/6/8 을 직렬화해 5,120B 이하인지.

    I1 이 런타임 검사라면 I11 은 채널 추가 시점의 정적 검사다.
    값은 문서가 아니라 코드가 진실이다.
    """
    proc = _run([sys.executable, "tools/measure_state.py", "--assert-under", "5120"])
    if proc.returncode == 0:
        tail = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("C=")]
        return True, " / ".join(x.strip() for x in tail)
    return False, ((proc.stderr or proc.stdout).strip().splitlines() or ["(출력 없음)"])[-1][:160]


def _todo() -> None:
    return None


CHECKS: dict[str, tuple[str, Check]] = {
    "I1":  ("체크포인트 blob < 5KB (런타임)                    D-23", _todo),
    "I2":  ("리듀서 순서 독립성 — 셔플 5회 결과 1종            D-15", _todo),
    "I3":  ("모든 LLM 노드 ctx_chars <= budget                 D-28", _todo),
    "I4":  ("View 스키마에 금지 필드 부재 (정적 검사)          D-28", _todo),
    "I5":  ("Evidence 중복: UNIQUE(run_id, content_sha256)     F4", _todo),
    "I6":  ("루프 종료 6항목 + total_llm_calls <= 4C+9         D-13", _todo),
    "I7":  ("CitationRef.span ⊂ Evidence.raw_span              F5", _todo),
    "I8":  ("canonical 4종이 output_schema 로 안 쓰임 (AST)    v2.2 S-9", _todo),
    "I9":  ("어댑터 source_type == PROVIDER_SOURCE_TYPE        v2.2 S-7", _todo),
    "I10": ("State 참조 채널 6개가 전부 Store 메서드를 가짐    v2.2 §3", _todo),
    "I11": ("체크포인트 blob 실측 회귀 (정적)                  v2.2 §5.1", i11_checkpoint_budget),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ci.invariants")
    ap.add_argument("--only", help="예: --only I1,I2")
    args = ap.parse_args(argv)

    names = list(CHECKS)
    if args.only:
        names = [n.strip().upper() for n in args.only.split(",")]
        unknown = [n for n in names if n not in CHECKS]
        if unknown:
            print(f"🔴 알 수 없는 불변식: {', '.join(unknown)}", file=sys.stderr)
            return 1

    failed, todo = [], []
    for name in names:
        label, fn = CHECKS[name]
        result = fn()
        if result is None:
            todo.append(name)
            print(f"⬜ {name:<4}{label}  — 미구현 (P0-7)")
            continue
        ok, detail = result
        print(f"{'✅' if ok else '🔴'} {name:<4}{label}")
        if detail:
            print(f"       {detail}")
        if not ok:
            failed.append(name)

    print()
    if failed:
        print(f"🔴 불변식 실패: {', '.join(failed)}", file=sys.stderr)
        return 1
    if todo:
        print(f"⚠️  미구현 {len(todo)}/{len(names)}: {', '.join(todo)}")
        print("   통과가 아니라 '아직 검사하지 않았다' 는 뜻이다. P0-7 에서 채운다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
