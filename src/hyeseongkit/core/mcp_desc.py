"""MCP 도구 description — 허브 서버와 stdio 브리지가 공유 (설계서 §4, D21).

모델이 세션 본문을 작성하므로(D21) 원문 보존 규약(N1~N7)을 도구 정의에 심는다.
"""

PUSH_DESCRIPTION = (
    "현재 작업 상태를 hyeseongkit 세션으로 저장한다. "
    "sections는 대화 맥락에서 직접 작성한다. "
    "결정·사용자 지시·오류 메시지·식별자(경로/SHA/포트/명령)·수치·할 일·미결 질문은 "
    "요약·변형 금지, 원문 그대로 쓴다(N1~N7). todo와 know는 필수다. "
    "title이 한글 등 비ASCII면 slug에 작업 주제를 짧은 영문 kebab-case로 요약해 "
    "함께 제공하라 — thread ID가 된다."
)

RESUME_DESCRIPTION = (
    "hyeseongkit 세션을 컨텍스트 패킷으로 불러온다. "
    "thread 지정 또는 last:true(프로젝트 최신 활성). "
    "events>0이면 최근 N개 이벤트 원문을 덧붙인다."
)

STATUS_DESCRIPTION = "활성 스레드 목록과 허브 연결 상태를 보여준다."

DECIDE_DESCRIPTION = (
    "결정 사항을 원문 그대로 세션에 기록한다(append-only, N1). "
    "rationale(근거)·rejected(기각안)는 대화에서 확인되는 경우에만 — 창작 금지."
)

SEARCH_DESCRIPTION = "과거 hyeseongkit 세션을 검색한다 (title/tags/본문 부분 일치)."

CLOSE_DESCRIPTION = "세션을 종료하고 아카이브 대상으로 표시한다 (outcome: done|dropped)."
