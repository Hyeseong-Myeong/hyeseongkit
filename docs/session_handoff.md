---
kit: hyeseongkit/session
v: 1
thread: T-20260810-session-persistence
title: hyeseongkit 세션 영속화 — 설계
status: active
sensitivity: tech
project: hyeseongkit
created: 2026-08-10T21:00+09:00
updated: 2026-08-11T14:00+09:00
last_tool: claude-code
last_device: desktop
next_action: "사용자 확인 후 첫 push → CPU 기준선 기록 → Phase 0 실행"
tags: [session, hyeseongkit, design, handoff]
---

> 📌 이 파일은 **다음 세션 인계용**이며, 동시에 **Phase 0(스키마 손으로 검증)의 첫 번째 시험**이다.
> 새 세션은 이 파일만 읽고 이어서 작업할 수 있어야 한다. 그렇지 못하면 §6-4 스키마를 고쳐야 한다.
> **읽는 순서: `## 3. 할 일` → `## 4. 알아야 할 것` → 나머지.**

---

## 1. 컨텍스트

여러 AI 서비스(Claude / Claude Code / Antigravity / Codex / 로컬 Ollama)를 오가며 **하나의 작업을 이어서** 하고 싶은데, 각 툴이 대화 이력을 자기 저장소에 가둬서 불가능하다. Bifrost로 **모델 라우팅은 이미 완성**되어 있으나 라우팅은 요청 단위이고 세션 개념이 없다.

해결책으로 **`hyeseongkit`** — 프로젝트·AI 서비스에 무관한 범용 CLI 허브 — 를 설계 중이다. 세션 영속화는 그 첫 번째 스킬이다.

참조 설계는 YouTube "제가 실제로 매일 돌리는 AI workflow 싹 다 공개합니다 (feat. JetBrains)"의 4계층 모델이며, 그중 **2계층(스킬 계층, 원본명 `mingus-kit`)** 만 채택한다. 요약본 원문은 `docs/references/panddu_youtube.md`에 있다.

**작업 방식:** 사용자는 기획 → 설계 → 구현을 명확히 분리해서 진행한다. 지금은 **기획·설계 완료, 구현(P0/P1) 착수 직전**이다.

## 2. 한 일

- `docs/session_persistence_design.md` **기획서 완료** (v3.0)
  - 아키텍처, SSOT 확정, 데이터 모델, 요약 금지 규약, 스킬 플러그인 구조, 툴별 자동화 수준, Phase P-0~P8, 결정 17건, 위험 17건, 가능/불가능 목록, 성공 기준
- 기획서 개정 v1 → v3.0 (개정 이력은 문서 상단 표에 원문 보존)
- **실측 조사 완료** — LiveSync 설정값, NAS 사양, `wiki_agent` 감시 범위, Claude Code 전사 위치, `livesync-bridge` 존재 확인 (§4-3의 "실측 사실" 참조)
- **결정 20건 확정** (§4-1) — P0/P1 착수를 막는 미결 없음
- **설계서 완료 (v1.6)** — `docs/session_persistence_impl_spec.md` (API·MCP·CouchDB 스키마·인증·마스킹·프로젝트 식별·렌더러/브리지·훅·`hk setup`/`hk init` 산출물·compose·CI/CD·수용 기준 T1~T13)
- **사용자 회신 반영 (2026-08-11)** — ① 훅=checkpoint 구체화 승인 ② D20/D12/D6/**D22** 확정 ③ U1(CouchDB 컨테이너 주소·관리자 계정 보유)·U5(볼트 백업 없음 → S1 전체 필수) 해소 ④ Codex는 IDE 확장 사용 확인 ⑤ 문서는 완성 후 커밋하기로
- **CI/CD 설계 추가** (설계서 §14, 사용자 지시) — CI(gitleaks/ruff/pytest+CouchDB 서비스/CodeQL 병렬) → main 머지 시 ghcr 이미지 빌드·푸시. **배포는 NAS에서 `deploy.sh` 수동 실행** (최종형은 D26 (D) 참조)
- **D25·D27 확정, D26 보류** (2026-08-11 2차 회신) — D25: 별도 저장소 `hyeseongkit` / D27: 머지→테스트→빌드→승인→배포 / D26: 설계서 §14-1-1에 3안 상세 검토 수록
- ~~D26 = (C) NAS 러너 확정~~ (3차 회신) → **4차 회신에서 (D) 러너 없음으로 재결정됨** (아래 참조)
- **문서 전체 검토 수행** (2026-08-11) — 수정 완료: 훅 기술 불일치(기획서 §5-4/§9/§9-1/§10 ↔ 설계서 §9-2), ghcr 이미지명 소문자, 러너 이미지 docker CLI, 배포 healthz 확인 위치, admin 토큰 발급 경로(NAS docker exec)
- **검토 피드백 F1~F4·C1~C5 회신 반영 완료** (2026-08-11, 설계서 v1.4 / 기획서 v2.9) — F1(a) know 이월 보존 / **F2 어댑터를 기기 단위 `hk setup`으로 재설계 (산출물 전부 비커밋, U4 소멸)** / F3 백업 정책(§12-4: Hyper Backup 일1회 + 논리 덤프 주1회) / F4 계정 분리(hk_hub·vault_client) / C1 canonical aliases / C2 MK08 라틴 한정 / C3 CodeQL+pre-commit / C4 resume 패킷 활성 스레드 목록 / C5 방화벽 추후
- **`hk link` 수동 매칭 신설** (2026-08-11, 설계서 v1.5 — 사용자 제안 채택): 저장소 개명·오분기 시 기존 프로젝트에 수동 연결 + `hk init` 오분기 방지 가드
- **저장소 분리 완료** (2026-08-11) — 별도 저장소 신설 후 git init, 문서 4종 이관, 커밋 2건(`d943bbf` 문서 / `f474c2d` .gitattributes). **GitHub 원격은 미생성**
- **Hyper Backup 설정 완료** (2026-08-11, 사용자 수행) → F3 물리 백업층 충족, S1의 CouchDB 측 전제도 해소
- **D26 재결정 + D28 신설** (2026-08-11 4차 회신, 설계서 v1.6 / 기획서 v3.0) — **self-hosted 러너 폐기 → (D) NAS 수동 pull 배포**(GitHub 시크릿·인바운드·상주 러너 전부 0), **플레이스홀더 + `.env` 규약**(설계서 §0-3-1)으로 문서 전반의 기기 고유값 치환 완료, 외부 기여 비수용 명시(§14-8)

## 3. 할 일

1. **첫 push** — 저장소는 **공개 확정**, 이력은 정리 완료(플레이스홀더화 이후 상태로만 구성), remote 연결됨. **사용자 지시가 있을 때 `git push -u origin main`**
2. **설계서 잔여 실측** — U2(브리지 config, S2 때)·U3(Codex IDE 확장·Antigravity MCP 경로 + AGENTS.md 사용자 단위 대안, P5 전). U4는 해소됨(stdio 채택)
3. **CPU 기준선 기록** — 배포 전 NAS 유휴 CPU(현재 <10%)를 수치로 남긴다. L7 관측의 비교 대상 (설계서 §12-3 P1-①)
4. **Phase 0 실행** — 이 인계 문서로 실제 툴 전환을 3~5회 해 보고 스키마 수정
5. **R14 해소** — Codex / Antigravity의 MCP 설정 경로 실측 (P5 전까지, 설계서 U3)

## 4. 알아야 할 것

> ⚠️ **이 섹션은 요약·삭제 금지.** 기획서 §7 N1~N8 규약 적용 대상.

### 4-1. 확정된 결정 (기획서 §11-1)

| # | 결정 | 근거 / 기각된 대안 |
|---|---|---|
| **D1** | **SSOT = NAS CouchDB `hyeseongkit_sessions`.** Obsidian 볼트는 렌더된 열람 전용 뷰 | *"추후 마이그레이션은 번거롭다"* → 단계적 도입안(A′ 파일 이벤트 소싱) **기각** |
| **D3** | NAS = **Synology DS220+ / Celeron J4025 2코어 x86-64 / RAM 18GB / Docker 가능 / CouchDB 3.5.2.1** | — |
| **D5** | 저장 구조 = **`event` (append-only)**. 저장 계층은 인터페이스로 분리하되 **2차 모드(`document`)는 지금 만들지 않음** | `event`→`document`는 무손실, 반대는 복원 불가 → 기본값을 `event`로 |
| **D7** | CLI 배포 = **Python + pipx** | 기존 스택 일관성 |
| **D14** | 동시 활성 스레드 = **프로젝트당 3개** | — |
| **D15** | CLI **`hk`**(별칭 `hyeseongkit`) / 슬래시 **`/hk:<명령>`** / MCP `hk_<명령>` | `.claude/commands/hk/push.md` → `/hk:push`. 인자 분기 방식(`/hk push`)은 자동완성 발견성이 낮아 미채택 |
| **D17** | 허브 인증 = **기기별 토큰 발급·폐기** | 기기 분실 시 개별 폐기 |
| **D18** | CouchDB 자격증명 = **허브만 보유.** CLI는 DB를 모른다 | 자격증명이 기기에 흩어지는 것 방지 |
| **D19** | 프로젝트 식별 = **git remote URL → 없으면 사람이 직접 명명** | **절대경로 금지** — 데스크톱 `C:\<project>`와 맥북 `~/dev/<project>`이 다른 프로젝트로 갈라져 핸드오프가 깨진다 |
| **D21** | 세션 본문 = **AI가 대화 맥락에서 자동 작성** + 식별자 검증기로 보호 | 사람이 쓰거나 확인시키면 **귀찮아서 안 쓰게 된다**. 그게 이 시스템의 최대 실패 요인 |
| **D24** | NAS CPU 여유 = **해소.** 최초 보고 70%는 오류였고 재부팅 후 <10% | 이 근거로 뒤집었던 D4를 원점 재검토 |
| **D4** | **(C) NAS + livesync-bridge → 세션 전용 볼트 DB `hyeseongkit_vault`** (2026-08-11) | 사용자 판단: 데스크톱을 며칠씩 안 켬 → (B)의 최신성 약점이 치명적. (A)는 (C)에 지배당해 기각. 비용 감수: 위키 `[[링크]]` 불가·기기당 볼트 2개·S2/S4 유지보수 |
| **D20** | **`.hyeseongkit/` 전부 gitignore — `project.toml`도 커밋 안 함** (2026-08-11) | 사용자 지시. `project_id`가 remote URL에서 결정적 유도 + 허브 `proj:` 문서가 공유 설정 기준값이라 정합성 유지 (설계서 §10-3) |
| **D12** | close 후 **15일** → 아카이브. 원문 이벤트는 영구 보존 (2026-08-11) | 권장 30일에서 사용자가 단축 |
| **D6** | 볼트에서 사람이 쓴 메모 **흡수 안 함** — 순수 단방향 (2026-08-11) | — |
| **D22** | 민감도 = **(c) 프로젝트별 지정** (2026-08-11). 인프라 저장소=`tech`, 의심 시 높은 쪽 | 민감도는 P6 요약 시 모델 선택 게이트 (career/personal은 무료 API 금지 — R13) |
| **D25** | 코드 저장소 = **별도 저장소 `hyeseongkit`** (2026-08-11) | 기각: 인프라 저장소 하위 모노레포 |
| **D27** | 배포 트리거 = **머지 → 테스트 → 빌드 → 사용자가 NAS에서 배포 실행** (2026-08-11) | 배포 실행 자체가 사전 동의. 완전 자동은 "재시작 전 사전 동의" 규칙 위반으로 기각 |
| **D26** | 배포 실행 주체 = **(D) 러너 없음 — NAS에서 `deploy.sh` 수동 실행** (2026-08-11 재결정) | GitHub 시크릿 0·NAS 인바운드 0·상주 러너 0 → 공개 저장소에도 노출면 없음. 기각: (A) 데스크톱 러너(가동률) / (B) hosted+Tailscale(시크릿 보관) / **(C) NAS 러너(공개 시 러너 노출·docker.sock 신뢰)** |
| **D28** | 시크릿·고유값 = **플레이스홀더 + `.env` 연동** (2026-08-11) | 문서·워크플로에 내부 주소·경로·자격증명을 쓰지 않는다 (설계서 §0-3-1) |

### 4-2. 미결 결정

| # | 상태 |
|---|---|
| ~~D4 / D6 / D12 / D20 / D22 / D25 / D26 / D27~~ | ✅ **확정 완료 (2026-08-11) — §4-1로 이동. P1 착수를 막는 결정 없음** |
| ~~F1~F4~~ | ✅ **해소 (2026-08-11)** — F1(a) 이월 보존 / F2 기기 단위 `hk setup` / F3 백업 정책 / F4 계정 분리. 설계서 v1.4 반영 완료 |
| D2 / D23 | P2 이후 항목. 지금 막지 않음 |
| D8 / D9 / D10 / D11 / D13 | P4~P6 항목 |
| **D29** 🆕 | **SSOT 암호화** — `hyeseongkit_sessions`도 평문 저장이다. **P1은 평문 진행**, `career`/`personal` 세션을 처음 넣기 전에 결정 (설계서 §0-2-2) |
| ~~저장소 공개 여부~~ | ✅ **공개(public) 확정** (2026-08-11). CodeQL 유지, `GHCR_TOKEN` 불필요 |

### 4-3. 실측으로 확인한 사실 (추측 아님)

| 항목 | 사실 |
|---|---|
| LiveSync 버전 | Self-hosted LiveSync **v1.0.11** |
| `resolveConflictsByNewerFile` | **false** → 충돌 시 **수동 병합**. 다중 라이터가 불가능한 이유 |
| `periodicReplication` / interval | true / **60초**, `syncOnSave`=false, `liveSync`=false |
| `encrypt` | **false** → 원격 CouchDB에 **볼트 평문 저장** (D8 미결) |
| `syncInternalFiles` | **false** → `.obsidian/` 설정·플러그인은 기기 간 미동기화 |
| `useHistory` / `trashInsteadDelete` | 둘 다 **true** → 삭제해도 용량이 줄지 않음 |
| LiveSync 저장 방식 | 마크다운을 **파일로 저장하지 않음.** 엔트리 문서 + 청크 문서로 분해. 청크는 파일 간 공유될 수 있음 → D4 위험의 실체 |
| LiveSync tweak 협상 | 실측 로그에서 tweak 변경 시 *"Database closed for reset"* 수행 확인. **브리지가 이에 어떻게 반응하는지 미검증** |
| `wiki_agent.py` 감시 범위 | `RAW_DIR`만 `recursive=False` (`wiki_agent.py:797-798`) → **`sessions/` 추가해도 위키 파이프라인 간섭 없음** ✅ |
| Claude Code 전사 | `~/.claude/projects/<project-slug>/*.jsonl` 로컬 존재 확인 |
| `~/.codex` | **이 기기에 없음** → Codex 설정 경로 실측 필요 (R14) |
| Antigravity | `~/.antigravity`에 `argv.json`·`extensions`만. MCP 설정 경로 **미확인** (R14) |
| `livesync-bridge` | 실재 확인. LiveSync 개발자(vrtmrz) 제작. **Obsidian 불필요**, Deno 기반, Docker Compose 지원, `baseDir` 선택 동기화, 볼트별 passphrase 지원 |
| 볼트 경로 / 구조 | `<VAULT_PATH>` — 위키 분류 폴더 여러 개 + `raw/`(수집 원문) + `archive/` + `schema.md` |
| 기존 툴 서버 | `fastapi_wiki_server.py` — Bearer 인증, ChromaDB 컬렉션 사용 (포트·키 이름은 `.env` 참조) |

### 4-4. 사용자 작업 규칙 (원문 — 위반 금지)

`.agents/AGENTS.md`에서:
- **`.env` 파일에 절대 접근(Read/Write) 금지.** 새 환경변수는 `.env.example`에 키만 추가(`KEY=""`)하고 값 입력은 사용자에게 안내
- **명시적 지시 없이 commit / push / PR 실행 금지**
- 설정 변경 후 시스템·컨테이너 재시작 시 **사전에 변경 내역을 설명하고 동의를 구한 뒤** 실행
- 사용자가 직접 해야 하는 작업이 있으면 **'완료' 응답이 올 때까지 대기**
- 에러 발생 시 맹목적 재실행 금지. **로그부터 확인**하고 근본 수정

기타 확인된 선호:
- 커밋은 **논리 단위로 분리**. 큰 변경을 한 번에 묶지 않는다
- PR/이슈 제목·본문과 커밋 메시지 **본문**은 **한국어 개조식**. 커밋 제목 줄과 기술 용어는 영어
- `main`은 Ruleset 보호됨 → **항상 브랜치 + PR**
- 큰 작업 종료 시 **완료/남은 작업 bullet 요약** 필수 (터미널 스크롤이 어려움)
- **폴링 루프 금지** — CI·외부 상태를 sleep 루프로 기다리지 말고 사용자에게 완료를 알려달라고 요청

### 4-5. 함정 / 이미 겪은 것

| 함정 | 대응 |
|---|---|
| **Windows CP949 콘솔** | `python -c` 출력에 유니코드가 있으면 `UnicodeEncodeError`. **`PYTHONIOENCODING=utf-8`** 를 붙일 것 |
| 한글 파일명 | macOS NFD vs Windows NFC 정규화 충돌 → **세션 파일명은 ASCII만**. 한글은 frontmatter `title:`에 |
| 문서 인코딩 | UTF-8 **BOM 없음**, LF (`.ps1`은 반대로 BOM 필요) |
| YouTube 링크 | 최초 공유 링크는 ID가 10자로 잘려 있었음(정상 11자). WebFetch로 YouTube 본문·자막 추출 **불가** (JS 렌더링 + 401) |
| 기획서 편집 | 결정 표가 §11-1(확정) / §11-2(미결)로 나뉘어 있다. **확정 항목을 §11-2에 남겨두면 중복**된다 |

### 4-6. 식별자

| 항목 | 값 |
|---|---|
| 저장소 | **`<REPO_DIR>`** (2026-08-11 신설 — D25에 따라 기존 인프라 저장소에서 문서 이관) |
| 브랜치 | `main` (신규 저장소 — **GitHub 원격 아직 없음**, push 전 시크릿 검토 필수) |
| 최신 커밋 | 문서 이관 첫 커밋 (`git log` 참조) |
| 기획서 | `docs/session_persistence_design.md` v2.9 |
| 설계서 | `docs/session_persistence_impl_spec.md` v1.5 |
| 이 문서 | `docs/session_handoff.md` |
| 영상 요약본 | `docs/references/panddu_youtube.md` (사본 — 원본은 `local-llm-setup` 저장소 `tmp/panddu_youtube`) |
| 예정 포트 | 허브 `:9100` (기존: Bifrost 8080, ChromaDB 8000, 툴서버 9000) |
| 예정 CouchDB DB | `hyeseongkit_sessions`, `hyeseongkit_auth`, **`hyeseongkit_vault`** (세션 전용 볼트 뷰 — D4 (C)) |

## 5. 미결 질문

- [x] ~~**D4** — 데스크톱을 거의 매일 켜는가?~~ → **아니오 → (C) 확정 (2026-08-11)**
- [x] ~~Codex를 어떤 형태로 쓰는가?~~ → **IDE 확장** (2026-08-11). 확장의 MCP 설정 경로 실측은 남음 (설계서 U3)
- [ ] Antigravity의 MCP 설정 파일 경로 (설계서 U3)
- [x] ~~NAS의 CouchDB 접근 경로~~ → **컨테이너 주소로 접근 중, 관리자 계정 보유·확인 가능** (2026-08-11). 실제 컨테이너명/네트워크명은 P1-②에서 `.env` 기입 (설계서 U1)
- [x] ~~볼트 백업 수단이 이미 있는가~~ → **없음** (2026-08-11) → **S1 전체 절차 생략 없이 수행** (설계서 U5)
- [x] ~~**D22** — 민감도 규칙~~ → **(c) 동의, 확정** (2026-08-11)
- [x] ~~**D25/D27**~~ → **확정** (2026-08-11): 별도 저장소 / 머지→테스트→빌드→승인→배포
- [x] ~~**D26**~~ → **(C) NAS 러너 확정** (2026-08-11)
- [x] ~~**F1** know 보존 방식~~ → **(a) 서버 fold 이월 보존 확정** (설계서 §2-4)
- [x] ~~**F2** 산출물 커밋 여부~~ → **전부 비커밋 + Claude Code 어댑터는 기기 단위 `hk setup`으로 이전** (설계서 §9). AGENTS.md 마커만 프로젝트에 남음 — R14 실측 때 사용자 단위 경로로 이전 검토
- [x] ~~**F3** SSOT 백업~~ → **정책 확정 + Hyper Backup 설정 완료** (2026-08-11, 설계서 §12-4). 남은 것: 논리 덤프 주 1회 스케줄(P1) + 분기 복원 검증
- [x] ~~저장소 공개/비공개~~ → **공개 확정** (2026-08-11). 이력 재작성 후 remote 연결 완료, **push는 사용자 지시 대기**
- [x] ~~**F4** CouchDB 계정~~ → **admin 사용 중 확인. 구현 시 분리 확정** (설계서 §12-3): P1에서 `hk_hub`, P4에서 `vault_client` 생성

## 6. 다음 세션 시작 방법

```
Docs/session_handoff.md 를 읽고 이어서 진행해 주세요.
"## 3. 할 일"의 1번부터 시작하되, "## 4. 알아야 할 것"의 결정은
이미 확정된 것이므로 재논의하지 마세요.
```

기획서 전체 맥락이 필요하면 `Docs/session_persistence_design.md`를 읽되,
**§11 결정 표와 §12 위험 표를 먼저** 보면 빠르다.
