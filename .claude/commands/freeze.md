---
description: frozen.py 변경 여부를 확인한다. 커밋 직전 호출.
---

`git diff --stat app/schemas/frozen.py` 와 `git diff --cached --stat app/schemas/frozen.py` 로
변경 여부를 확인한다.

변경이 있으면 3인 approve 대상이므로 **즉시 보고하고 작업을 멈춘다.**
어느 필드·검증자가 바뀌었는지 diff 를 그대로 보여준다.

변경이 없으면 "frozen 무변경" 한 줄만 보고한다.
