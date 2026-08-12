<!-- hyeseongkit:start (managed by `hk init` — 이 블록 안은 수동 편집 금지) -->
## hyeseongkit 세션 연속성

이 프로젝트는 hyeseongkit으로 세션을 툴 간 인계한다.

- 작업 시작 시: MCP 도구 `hk_resume`(last:true)으로 이전 상태를 불러와 "할 일"부터 확인
- 중요한 결정이 확정되면: `hk_decide`로 결정·근거·기각안을 **원문 그대로** 기록
- 작업을 마치거나 오래 자리를 뜨기 전: `hk_push`로 상태 저장
  (결정·지시·오류·식별자·수치·할 일·질문은 요약 금지 — 원문 보존)
- MCP를 쓸 수 없는 환경이면 `HYESEONGKIT.md`의 수동 절차를 따른다
<!-- hyeseongkit:end -->
