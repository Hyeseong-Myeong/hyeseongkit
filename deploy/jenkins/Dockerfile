# hyeseongkit 배포 트리거용 Jenkins (설계서 §14-4-1)
#
# 공식 jenkins/jenkins:lts에는 docker CLI가 없어 `docker compose`를 실행할 수 없다.
# CLI와 compose 플러그인만 더한다 — 데몬은 넣지 않는다(DooD: 호스트 데몬을 소켓으로 조종).
# 빌드는 GitHub hosted 러너에서 한다 (NAS 2코어 보호 — §14-1).
FROM jenkins/jenkins:lts-jdk17

USER root

# Docker 공식 apt 저장소에서 CLI + compose 플러그인만 설치 (docker-ce 데몬 아님)
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc; \
    chmod a+r /etc/apt/keyrings/docker.asc; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin; \
    rm -rf /var/lib/apt/lists/*

USER jenkins

# 배포 job은 사람이 Build Now로만 실행한다 (D27) — 설치 마법사는 그대로 두어
# 초기 관리자 계정을 반드시 만들게 한다. runSetupWizard=false로 건너뛰지 않는다.
