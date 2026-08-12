# hyeseongkit 수동 연동 (MCP 미지원 툴용)

## 이어서 시작하기
1. 아무 터미널에서: hk resume --last --format prompt
2. 출력 전체를 대화창에 붙여넣는다

## 상태 저장하기
1. AI에게: "지금까지의 작업 상태를 hyeseongkit push 형식(컨텍스트/한 일/할 일/알아야 할 것/미결 질문,
   결정·지시·오류·식별자·수치는 원문 보존)의 마크다운으로 정리해줘"
2. 출력물을 파일로 저장 후: hk push --file <파일> --title "<제목>"
   (또는 클립보드에서: hk push --stdin --title "<제목>" 후 붙여넣고 Ctrl-Z/Ctrl-D)

## 결정 기록
hk decide --file <결정문파일>   # 한국어는 CLI 인자 대신 파일/stdin으로 (Windows 인코딩)
