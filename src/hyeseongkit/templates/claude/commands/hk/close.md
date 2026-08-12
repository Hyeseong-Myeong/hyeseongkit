---
description: hyeseongkit 세션 종료
---
1. 먼저 /hk:push 절차대로 `hk_push`로 최종 상태를 저장하라
2. 그다음 `hk_close`를 호출하라 (thread: 현재 스레드, outcome: 완료면 done, 중단이면 dropped — "$ARGUMENTS"에서 판단)
