"""PreToolUse(Bash) 훅 — 파괴적 명령을 물리적으로 차단한다.

Claude_Code_Reference_Guide.md §5: "PreToolUse: 파괴적인 Bash 쉘 명령어(예: rm -rf)가
감지되면 물리적으로 차단합니다."

`CLAUDE.md` 의 "하지 마라"는 안내지 강제가 아니다. 이 훅이 강제다.
exit 2 로 도구 호출 자체를 막고 에이전트에게 사유를 돌려준다.

🔴 차단 목록은 보수적으로 짧게 유지한다. 과잉 차단은 훅을 꺼버리게 만들고,
   꺼진 훅은 없는 훅이다.
"""
from __future__ import annotations

import json
import re
import sys

# (정규식, 사유). 정규식은 소문자화된 명령 문자열에 대해 검사한다.
BLOCKED: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-z]*\s+)*-[a-z]*[rf][a-z]*\b", "rm -rf 계열 재귀·강제 삭제"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard — 커밋 안 된 작업이 사라진다"),
    (r"\bgit\s+clean\s+-[a-z]*[fd]", "git clean -fd — 추적 안 된 파일이 사라진다"),
    (r"\bgit\s+push\s+.*--force(?!-with-lease)", "git push --force — 원격 이력이 사라진다"),
    (r"\bgit\s+checkout\s+--\s", "git checkout -- — 워킹트리 변경이 사라진다"),
    (r">\s*/dev/sd", "블록 디바이스 직접 쓰기"),
    (r"\bmkfs\b|\bdiskpart\b|\bformat\s+[a-z]:", "파일시스템 포맷"),
    (r"\bdrop\s+(database|table)\b", "DROP DATABASE / DROP TABLE"),
    (r"\bcurl\b.*\|\s*(ba)?sh\b", "원격 스크립트를 받아 즉시 실행"),
]

# 🔴 이 저장소 고유 — frozen.py 는 3인 approve 대상이다. 훅으로도 한 번 막는다.
FROZEN_GUARD = (
    r"(>|>>|tee|sed\s+-i|truncate).*app[/\\]schemas[/\\]frozen\.py",
    "frozen.py 를 셸로 덮어쓰려 한다. 3인 approve 대상이다",
)
BLOCKED.append(FROZEN_GUARD)


def main() -> int:
    # utf-8-sig: 일부 셸이 BOM 을 붙인다. BOM 하나에 가드가 통째로 꺼지면 안 된다.
    raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")

    try:
        command = (json.loads(raw).get("tool_input") or {}).get("command") or ""
    except Exception:
        # 🔴 파싱 실패 시 fail-open 하지 않는다. 페이로드 모양이 바뀌면
        #    가드가 조용히 사라지고, 그걸 아무도 눈치채지 못한다.
        #    그렇다고 전면 차단하면 Bash 가 통째로 막혀 사람들이 훅을 지운다.
        #    → 원문 전체를 그대로 패턴 검사한다. 열지도 잠그지도 않는다.
        command = raw

    haystack = command.lower()

    for pattern, reason in BLOCKED:
        if re.search(pattern, haystack):
            print(
                f"🔴 차단됨: {reason}\n"
                f"   명령: {command[:200]}\n"
                f"   되돌릴 수 없는 작업이다. 정말 필요하면 사람에게 직접 실행을 요청해라.",
                file=sys.stderr,
            )
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
