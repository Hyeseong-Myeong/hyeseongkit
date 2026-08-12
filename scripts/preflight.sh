#!/bin/sh
# push 전 검사 — 개인정보·시크릿 유입과 lint/테스트 실패를 막는다 (.agents/AGENTS.md 규약)
#
# 사용법:  sh scripts/preflight.sh
# 대상:    git이 커밋할 파일 전체 (추적 중 + 무시되지 않은 미추적)
#          → .gitignore가 제대로 걸려 있으면 tmp/·.env·.venv는 애초에 들어오지 않는다
#
# 이 저장소는 **공개**이고 문서가 개인 인프라를 다루므로, 자동 스캐너가 모르는
# "기기 고유값"(내부 IP·NAS 경로·호스트명)을 직접 찾는 것이 이 스크립트의 핵심이다.
set -eu
cd "$(dirname "$0")/.."

FAIL=0
FILES=$(git ls-files --cached --others --exclude-standard)

# 스캐너는 찾는 패턴을 본문에 담을 수밖에 없다 — 자기 자신은 늘 제외한다
SELF='^scripts/preflight\.sh:'

# 마스킹 규칙(§6)을 검증하려면 진짜 형식의 합성 시크릿이 필요하다 — 그 파일들만 추가 제외
FIXTURES="$SELF|^(tests/test_redact\.py|tests/test_api\.py|src/hyeseongkit/core/redact\.py|docs/session_persistence_impl_spec\.md):"

check() {
  label="$1"; pattern="$2"; allow="${3:-$SELF}"
  hits=$(echo "$FILES" | xargs grep -nE "$pattern" 2>/dev/null | grep -vE "$allow" || true)
  if [ -n "$hits" ]; then
    echo "✗ $label"
    echo "$hits" | sed 's/^/    /'
    FAIL=1
  else
    echo "✓ $label"
  fi
}

echo "── 개인정보·고유값 스캔 ──────────────────────────────"

# Tailscale CGNAT (100.64.0.0/10). 100.64.0.1은 §6-5 벡터라 픽스처에서만 허용
check "Tailscale 주소 없음" \
  '\b100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}\b' "$FIXTURES"

check "사설 IP 없음" \
  '\b(192\.168\.[0-9]{1,3}\.[0-9]{1,3}|10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})\b'

# /volume1/... 같은 총칭은 DSM 공통 지식이라 허용. /volume1/docker/jenkins처럼
# 실제 배포 경로가 드러나는 두 단계 이상만 잡는다
check "NAS 실경로 없음" '/volume[0-9]/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+'

check "호스트명·계정명 없음" 'SNLG|hyeseong_admin|couchdb-obsidian-sync'

check "개발 기기 절대경로 없음" 'C:\\\\(Users|hyeseongkit)|/Users/[a-z]+/'

echo ""
echo "── 시크릿 스캔 ──────────────────────────────────────"

check "토큰·키 없음" \
  '\bhk_[a-f0-9]{40}\b|\bgh[pousr]_[A-Za-z0-9]{36,}|\bAKIA[0-9A-Z]{16}\b|\bxox[baprs]-[0-9A-Za-z-]{10,}|\bAIza[0-9A-Za-z_-]{35}\b|gAAAAA[A-Za-z0-9_=-]{20,}' \
  "$FIXTURES"

# .env.example에는 키만 남고 값은 비어 있어야 한다 (D28). 무해한 기본값만 예외
ENVS=$(echo "$FILES" | grep -E '(^|/)\.env\.example$' || true)
if [ -n "$ENVS" ]; then
  hits=$(echo "$ENVS" | xargs grep -nE '^[A-Z_]+="?[^"[:space:]#]' 2>/dev/null \
    | grep -vE '="(latest|/vault-out|/data|2000|hyeseongkit_[a-z]+)"' || true)
  if [ -n "$hits" ]; then
    echo "✗ .env.example에 값이 채워진 키 있음"; echo "$hits" | sed 's/^/    /'; FAIL=1
  else
    echo "✓ .env.example은 키만 보유"
  fi
fi

# .gitignore가 실제로 듣고 있는지 (규칙이 있어도 이미 추적 중이면 무의미하다)
leaked=$(echo "$FILES" | grep -E '^(tmp/|\.venv/)|(^|/)\.env$' || true)
if [ -n "$leaked" ]; then
  echo "✗ 커밋 대상에 제외 대상이 포함됨"; echo "$leaked" | sed 's/^/    /'; FAIL=1
else
  echo "✓ tmp/·.env·.venv 제외 확인"
fi

if command -v gitleaks >/dev/null 2>&1; then
  echo ""
  echo "── gitleaks ─────────────────────────────────────────"
  gitleaks detect --no-banner --redact -c .gitleaks.toml || FAIL=1
fi

echo ""
echo "── lint·테스트 ──────────────────────────────────────"
if command -v ruff >/dev/null 2>&1; then RUFF=ruff
elif [ -x .venv/Scripts/ruff.exe ]; then RUFF=.venv/Scripts/ruff.exe
elif [ -x .venv/bin/ruff ]; then RUFF=.venv/bin/ruff
else RUFF=""; fi

if [ -n "$RUFF" ]; then
  "$RUFF" check src/ tests/ && echo "✓ ruff check" || FAIL=1
  "$RUFF" format --check src/ tests/ >/dev/null && echo "✓ ruff format" || { echo "✗ ruff format"; FAIL=1; }
else
  echo "⚠ ruff 없음 — 건너뜀 (CI가 잡는다)"
fi

if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then PY=python3
else PY=""; fi

if [ -n "$PY" ]; then
  "$PY" -m pytest -q >/dev/null && echo "✓ pytest" || { echo "✗ pytest"; FAIL=1; }
else
  echo "⚠ python 없음 — 테스트 건너뜀"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "통과 — push해도 좋다"
else
  echo "실패 — 위 항목을 해결하기 전에 push하지 않는다"
fi
exit "$FAIL"
