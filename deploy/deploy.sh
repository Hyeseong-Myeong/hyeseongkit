#!/bin/sh
# 사용법: ./deploy.sh [이미지태그]   (생략 시 .env의 IMAGE_TAG, 그것도 없으면 latest)
# 실행 위치: <DEPLOY_DIR> — .env, docker-compose.yml 이 있는 곳
# D26 (D): 배포는 NAS Jenkins 수동 빌드(또는 직접 실행) 1회 — GitHub 시크릿·러너·인바운드 0
set -eu
cd "$(dirname "$0")"

[ -f .env ] || { echo ".env가 없습니다 (§12-2 참조)"; exit 1; }

# .env를 셸로 읽어들인다 — 값에 공백이 있으면 반드시 따옴표로 감쌀 것
. ./.env

[ $# -ge 1 ] && export IMAGE_TAG="$1"

# ── CouchDB 연결 보증 (§1) ───────────────────────────────────
# 허브는 컨테이너 이름으로 CouchDB를 찾으므로 둘이 같은 사용자 정의 네트워크에 있어야 한다.
# CouchDB 컨테이너를 재생성하면 그 연결이 풀리는데, 여기서 매번 다시 붙여 자가 복구한다.
# 이미 연결돼 있으면 아무 일도 하지 않는다 (idempotent).
if [ -n "${HK_DOCKER_NET:-}" ]; then
  if ! docker network inspect "$HK_DOCKER_NET" >/dev/null 2>&1; then
    echo "도커 네트워크 '$HK_DOCKER_NET'가 없습니다 — 먼저 만드세요:"
    echo "  docker network create $HK_DOCKER_NET"
    exit 1
  fi
  if [ -n "${HK_COUCHDB_CONTAINER:-}" ]; then
    docker network connect "$HK_DOCKER_NET" "$HK_COUCHDB_CONTAINER" 2>/dev/null || true
  fi
fi

# 비공개 패키지일 때만: .env의 GHCR_TOKEN으로 로그인 (공개면 불필요)
if [ -n "${GHCR_TOKEN:-}" ]; then
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_OWNER" --password-stdin
fi

docker compose pull
docker compose up -d

# healthz — 유한 재시도 (상시 폴링 아님, 제약 L3)
i=0
while [ $i -lt 10 ]; do
  if docker exec hyeseongkit-hub python -c \
     "import urllib.request;urllib.request.urlopen('http://localhost:9100/healthz',timeout=3)" 2>/dev/null; then
    echo "배포 완료: ${IMAGE_TAG:-latest}"; exit 0
  fi
  i=$((i+1)); sleep 3
done
echo "healthz 실패 — 허브 로그를 먼저 확인 (docker logs hyeseongkit-hub --tail 50), 롤백은 §14-6"; exit 1
