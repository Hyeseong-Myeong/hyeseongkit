# 🚀 NAS 배포 런북 — Jenkins 재설치부터 P1 검증까지

> 상위 문서: [기획서](session_persistence_design.md) (왜) · [설계서](session_persistence_impl_spec.md) (무엇을)
> **이 문서는 "어떤 순서로"** — 사용자가 NAS에서 직접 수행하는 절차만 담는다.
> 기준일: 2026-08-12 · 대상: P0(Jenkins) → P1(허브 배포) → P2/P3(기기 설정)

## 0. 이 문서를 읽는 법

- **플레이스홀더(`<...>`)는 실제 값으로 바꿔 입력한다.** 실제 값은 이 문서에 적지 않는다 (D28, 설계서 §0-3-1)
- **`docker` 명령은 대개 `sudo` 없이 된다**(계정이 docker 그룹에 속해 있다면). 반면 `/volume1/...` 아래의 **파일 조작은 `sudo`가 필요하다** — 컨테이너가 만든 파일은 uid 1000 소유이기 때문이다. 권한 오류가 나면 `sudo`를 붙인다
- **되돌릴 수 없는 단계에는 ⛔ 표시**를 했다. 그 앞 단계의 확인을 건너뛰지 않는다
- 각 단계 끝의 **"확인"** 을 통과하지 못하면 다음으로 넘어가지 않는다 — 반쯤 된 상태로 진행하면 원인 추적이 몇 배 어려워진다

### 0-1. 시작 전에 실측할 값 3가지

설계서의 미확정 항목(U8·U9)이며, **추정하면 배포가 실패한다.**

```sh
# ① docker.sock의 소유 그룹 GID (U8) — Jenkins compose의 DOCKER_GID
stat -c '%g' /var/run/docker.sock
getent group "$(stat -c '%g' /var/run/docker.sock)"    # 어느 그룹인지 이름까지 확인 (선택)

# ② 비어 있는 포트 확인 (U9) — Jenkins 웹 UI 포트
sudo netstat -tlnp | awk '{print $4}' | grep -oE '[0-9]+$' | sort -n | uniq
#   ⚠️ sudo 없이 실행하면 다른 사용자의 소켓이 보이지 않아 "비어 있음"이 거짓일 수 있다

# ③ 기존 Jenkins의 실체 — 컨테이너인가 패키지인가 (§2에서 분기)
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | grep -i jenkins
synopkg list 2>/dev/null | grep -i jenkins
```

| 값 | 쓰이는 곳 |
|---|---|
| `DOCKER_GID` | `<JENKINS_DIR>/.env` — 이 값이 틀리면 배포 시 `permission denied` |
| `JENKINS_PORT` | `<JENKINS_DIR>/.env` — 겹치면 컨테이너가 뜨지 않는다 |
| 기존 Jenkins 형태 | §2 제거 절차의 분기 |

> **`DOCKER_GID`가 65536 이상이어도 정상이다.** Synology DSM은 사용자가 만든 그룹에 **65536부터** GID를 부여하므로, 일반적인 리눅스의 `docker` 그룹(999 등)과 값의 모양이 다르다. `docker ps`가 `sudo` 없이 동작한다면 현재 계정이 이미 그 그룹에 속해 있다는 뜻이고, 같은 GID를 컨테이너에 `group_add`로 넘기면 된다.

> **포트 선택 기준:** ⓐ 허브가 **9100**을 쓴다 ⓑ 8000번대는 개인 서버가 쓰고 있다 → **18080** 같은 18000번대를 권한다(Jenkins의 관례적 대체 포트이고 위 두 대역과 멀다). ②의 목록에 그 번호가 없는지 확인하고 고른다.

---

## 1. 전체 순서

| # | 단계 | 결과물 | 되돌리기 |
|---|---|---|---|
| §2 | 기존 Jenkins 백업 → 제거 → `jenkins_home` 초기화 | `<BACKUP_DIR>/jenkins_home-<날짜>.tar.gz` + 빈 `jenkins_home` | 아카이브 + `.old-<날짜>` 폴더 |
| §3 | Jenkins 재설치 (이미지 빌드 포함) | Jenkins 컨테이너 | 컨테이너 삭제 후 재실행 |
| §4 | `hk-deploy` job 생성 | Build Now 버튼 | job 삭제 |
| §5 | **CPU 기준선 재기록** | `tmp/cpu_baseline.md` 갱신 | — |
| §6 | CouchDB 네트워크 연결 + 계정·DB·권한 준비 | 전용 네트워크 + `hk_hub` 계정 + DB 3개 | `network disconnect` / 계정·DB 삭제 |
| §7 | 배포 디렉터리와 `.env` | `<DEPLOY_DIR>/` | 파일 삭제 |
| §8 | 첫 배포 + 검증 (T11·T14·T9) | 허브 가동 | §10 롤백 |
| §9 | 기기 설정 (P2/P3) | `hk` CLI 동작 | pipx uninstall |

> **§5의 위치가 중요하다.** 기존 CPU 기준선(`tmp/cpu_baseline.md`)은 *구 Jenkins가 돌던 상태*의 값이다. 허브가 CPU에 미친 영향(L7)만 보려면 **"새 Jenkins는 있고 허브는 없는" 상태**를 기준선으로 다시 찍어야 한다. 순서를 바꾸면 1주 관측(L7)의 비교 대상이 사라진다.

---

## 2. 기존 Jenkins 백업 및 제거 ⛔

> 사용자 지시 (2026-08-12): *"젠킨스는 기존 내용을 삭제하고 새로 깔아야 합니다."*
> 백업은 확정된 안전망이다 (2026-08-12 사용자 선택 — "백업 후 삭제").

### 2-1. 무엇이 돌고 있는지 먼저 본다

컨테이너 이름은 `docker ps`에서 확인한다 (흔히 그냥 `jenkins`).

```sh
# 어떤 포트를 쓰고 있었나 — 새 Jenkins가 같은 포트를 쓸지 판단하는 근거
docker inspect <기존-jenkins-컨테이너> --format '{{json .HostConfig.PortBindings}}'

# jenkins_home이 어디인가 — 백업 대상
docker inspect <기존-jenkins-컨테이너> \
  --format '{{range .Mounts}}{{.Type}}  {{.Name}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
```

`/var/jenkins_home`에 대응하는 줄을 본다:

| `.Type` | 백업 대상 경로 |
|---|---|
| `bind` | 표시된 `Source` 경로 그대로 |
| `volume` | `docker volume inspect <이름> --format '{{.Mountpoint}}'` 의 출력 |

> 포트가 어디에도 바인딩돼 있지 않다면(`{}` 또는 빈 값) 기존 Jenkins는 **호스트 포트로 노출되지 않은 상태**다 — DSM 리버스 프록시나 컨테이너 네트워크로만 접근했을 수 있다. 그 경우 §0-1 ②의 목록에 Jenkins 포트가 안 나타나는 것이 정상이며, 새 Jenkins는 원하는 포트를 자유롭게 고르면 된다.

### 2-2. 백업 — **Jenkins를 먼저 멈춘다**

`<BACKUP_DIR>`는 **`jenkins_home` 바깥**이어야 한다 (§2-4에서 그 폴더를 비우기 때문).

```sh
# ① 정지 — 가동 중에 tar하면 쓰는 중인 파일을 반쯤 담을 수 있다
docker stop <기존-jenkins-컨테이너>

# ② 백업 (sudo 필요 — jenkins_home은 uid 1000 소유이고 secrets/는 600이라 일반 계정이 못 읽는다)
sudo mkdir -p <BACKUP_DIR>
sudo tar czf <BACKUP_DIR>/jenkins_home-$(date +%Y%m%d).tar.gz -C <기존-jenkins_home-경로> .
ls -lh <BACKUP_DIR>/jenkins_home-*.tar.gz          # 크기가 0이 아닌지 확인
```

> `sudo` 없이 tar하면 `secrets/`·`credentials.xml` 같은 파일이 **조용히 빠진 채** 아카이브가 만들어진다 — 크기만 보고 성공했다고 판단하지 말고 아래 확인을 거친다.

> ⚠️ `jenkins_home`에는 **자격증명이 들어 있을 수 있다** (job의 토큰·SSH 키). 이 아카이브는 NAS 로컬에 두고, 저장소·볼트·클라우드에 올리지 않는다. NAS 밖으로 옮겨야 하면 암호화한다.

**확인:**

```sh
sudo tar tzf <BACKUP_DIR>/jenkins_home-*.tar.gz | grep -E '(config\.xml|secrets/|jobs/)' | head
```

`config.xml`과 `secrets/`가 함께 보여야 한다. `secrets/`가 없으면 권한 부족으로 누락된 것이니 `sudo`로 다시 만든다.

### 2-3. 제거 ⛔

**(a) Docker 컨테이너인 경우** (§2-2에서 이미 정지했다)

```sh
docker rm <기존-jenkins-컨테이너>
```

- 기존 이미지가 `jenkins/jenkins:lts` 계열이라면 **지우지 않아도 된다** — 새 이미지가 이것을 베이스로 하므로 레이어를 재사용해 빌드가 빨라진다. 디스크를 정리하고 싶을 때만 `docker rmi`
- **named volume은 자동으로 지워지지 않는다.** `docker rm`만으로는 데이터가 남으므로, §2-2 백업을 마친 뒤 `docker volume rm <이름>`으로 명시적으로 지운다 (남겨 두어도 무해하다 — 새 Jenkins는 다른 볼륨을 쓴다)
- Container Manager의 **프로젝트**로 만들었다면 GUI에서 프로젝트를 중지·삭제하는 편이 깔끔하다 (compose 파일까지 함께 정리된다)

**(b) DSM 패키지인 경우**

패키지 센터 → Jenkins → 중지 → 제거. 데이터 폴더(`/volume1/@appstore/...` 또는 지정한 공유 폴더)가 남을 수 있으니 §2-1에서 확인한 경로를 직접 확인한다.

**확인:** `docker ps -a | grep -i jenkins` 결과가 비어 있고, 기존 Jenkins 포트로 접속되지 않는다.

> 새 Jenkins가 **기존과 같은 포트**를 쓸 계획이라면 이 단계가 반드시 먼저다 — 구 컨테이너가 살아 있으면 포트를 이미 잡고 있어 새 컨테이너가 뜨지 않는다.

### 2-4. `jenkins_home` 초기화 (같은 경로를 재사용하는 경우)

새 Jenkins가 기존 `jenkins_home` 경로를 그대로 쓸 때, 폴더를 비우지 않으면 **옛 설정·job·플러그인을 그대로 물려받는다** — 초기화가 목적이라면 반드시 비운다.

```sh
# ⛔ rm -rf 대신 rename — 새 Jenkins가 정상 동작할 때까지 되돌릴 여지를 남긴다
sudo mv <기존-jenkins_home-경로> <기존-jenkins_home-경로>.old-$(date +%Y%m%d)
sudo mkdir -p <JENKINS_HOME_DIR>
sudo chown -R 1000:1000 <JENKINS_HOME_DIR>
```

`.old-*` 폴더는 **§8 검증까지 끝난 뒤에** 지운다 (§2-2의 아카이브와 중복이지만, 되돌릴 때는 압축을 푸는 것보다 폴더를 되돌리는 편이 빠르다).

**확인:** `ls -a <JENKINS_HOME_DIR>` 가 비어 있고, 소유자가 `1000:1000`이다.

---

## 3. Jenkins 재설치

> **Jenkins는 이 저장소의 산출물이 아니다** (설계서 §14-4-1). hyeseongkit은 "배포 트리거가 무엇을 만족해야 하는가"만 규정하고, 이미지·compose는 **인프라 저장소**가 관리한다.
>
> 필요한 것은 **docker CLI와 compose 플러그인을 갖춘 Jenkins 이미지** 하나다. 공식 `jenkins/jenkins:lts`에는 docker CLI가 없어 그대로는 배포를 실행할 수 없다. 데몬은 넣지 않는다(DooD — 호스트 데몬을 소켓으로 조종).
>
> 이미지는 **NAS에서 1회 빌드**하면 된다. §14-1의 "NAS는 pull과 up만"은 반복되는 파이프라인 빌드에 대한 제약이지, 인프라를 세울 때의 1회성 빌드에는 해당하지 않는다.

### 3-1. 디렉터리와 설정

```sh
mkdir -p <JENKINS_DIR> <JENKINS_HOME_DIR>
# jenkins 컨테이너는 uid 1000으로 돌기 때문에 소유권을 맞춰야 한다
chown -R 1000:1000 <JENKINS_HOME_DIR>
```

(§2-4에서 이미 만들고 `chown` 했다면 그대로 둔다.)

`<JENKINS_DIR>`에 Dockerfile과 compose를 두고(인프라 저장소에서 가져온다), `.env`에 값을 채운다:

```
JENKINS_PORT=...        # §0-1 ②에서 확인한 빈 포트
JENKINS_BIND=...        # 사설망(Tailscale) 주소 — §3-3-1
JENKINS_HOME_DIR=...    # §2-4에서 비운 그 경로
DEPLOY_DIR=...          # §7에서 만들 허브 배포 디렉터리 (지금 정해 둔다)
DOCKER_GID=...          # stat -c '%g' /var/run/docker.sock 의 값
```

이미지를 빌드한다 (1회, 몇 분 소요):

```sh
cd <JENKINS_DIR> && docker build -t hyeseongkit-jenkins:local .
```

> `DEPLOY_DIR`는 **호스트 경로 그대로** 넣고, compose에서 컨테이너 안에도 **같은 이름으로** 마운트한다 — 그래야 Jenkins가 실행하든 사람이 실행하든 동일한 볼륨·컨테이너를 다룬다 (설계서 §14-4-1 요구사항 3).

### 3-2. 기동

```sh
cd <JENKINS_DIR>
docker compose up -d
docker compose logs -f          # "Jenkins is fully up and running" 대기 (첫 기동은 1~2분)
```

### 3-3. 초기 설정 — 보안 기본값을 여기서 정한다

```sh
docker exec hyeseongkit-jenkins cat /var/jenkins_home/secrets/initialAdminPassword
```

브라우저에서 `http://<NAS_HOST>:<JENKINS_PORT>` 접속 → 위 비밀번호 입력.

**"Install suggested plugins"를 고르지 않는다.** `Select plugins to install` → **전부 해제**하고 진행한다.
배포 job은 셸 한 줄이라 플러그인이 필요 없다. 더 중요한 이유는 **플러그인이 Jenkins 취약점의 최대 공급원**이라는 것이다 — Jenkins는 `docker.sock`을 쥐고 있어 원격 코드 실행 취약점 하나가 곧 호스트 장악이 된다 (설계서 §14-4-2 L-3).

이어서 관리자 계정을 만든다. **익명 접근은 허용하지 않는다** (기본값 유지).

### 3-3-1. ★ 접근 제한 — `docker.sock` 위험의 실질적 방어선

> Jenkins에 네트워크로 닿을 수 있는 사람은 **호스트 root와 같다**(설계서 §14-4-2). 취약점 패치나 강한 비밀번호보다 **"애초에 닿을 수 없게 하는 것"이 압도적으로 효과적**이다. 나머지 공격 경로가 전부 "먼저 Jenkins에 접속할 수 있다"를 전제하기 때문이다.

둘 중 하나(또는 둘 다)를 적용한다:

**(a) 인터페이스 바인딩** — `<JENKINS_DIR>/.env`의 `JENKINS_BIND`에 **Tailscale 주소**를 넣고 재기동한다.

```sh
cd <JENKINS_DIR> && docker compose up -d
docker compose ps      # PORTS 열이 <Tailscale주소>:<포트>->8080/tcp 로 보이면 성공
```

**(b) Synology 방화벽** — DSM → 제어판 → 보안 → 방화벽에서 해당 포트를 **Tailscale 인터페이스에서만** 허용.

**확인 (T17):** 같은 LAN의 다른 기기(휴대폰 Wi-Fi 등)에서 `http://<NAS_LAN_IP>:<JENKINS_PORT>` 접속이 **거부**되고, Tailscale로는 접속된다.

> 이 단계를 건너뛸 생각이라면 Jenkins 대신 **DSM 작업 스케줄러**로 `deploy.sh`를 실행하는 편이 낫다 — 소켓 마운트가 아예 없고 `deploy.sh`는 그대로 쓴다 (설계서 §14-4-2 "완전 회피 경로").

### 3-4. DooD 확인 (T14) — **배포 시도 전에 반드시**

```sh
docker exec hyeseongkit-jenkins docker version
docker exec hyeseongkit-jenkins docker compose version
docker exec hyeseongkit-jenkins docker ps
```

| 증상 | 원인 | 조치 |
|---|---|---|
| `docker: not found` | 공식 이미지를 그대로 씀 | docker CLI를 더한 이미지로 빌드했는지 확인 (§3-1) |
| `permission denied ... docker.sock` | `DOCKER_GID` 불일치 | `stat -c '%g' /var/run/docker.sock` 재확인 후 `.env` 수정 → `docker compose up -d` |
| `Cannot connect to the Docker daemon` | 소켓 미마운트 | compose의 volumes 확인 |

**확인:** `docker exec hyeseongkit-jenkins docker ps`에 **호스트의 컨테이너 목록**(CouchDB 등)이 보인다.

---

## 4. 배포 job `hk-deploy` 생성

설계서 §14-4-1의 사양표 그대로 만든다. **SCM은 쓰지 않는다** — 저장소에서 job 정의를 가져오면, 저장소에 머지된 코드가 NAS Docker 권한으로 실행되어 D26에서 (C)를 기각한 위험이 그대로 돌아온다.

```
New Item → 이름: hk-deploy → Freestyle project → OK

☑ This project is parameterized
   → Add Parameter → String Parameter
      Name: IMAGE_TAG      Default Value: latest
      Description: ghcr 이미지 태그. 롤백은 sha-<이전커밋>

Source Code Management:  None          ← 그대로 둔다 (★ 중요)
Build Triggers:          전부 해제       ← 폴링 금지 (제약 L3), 수동 실행만 (D27)

Build Steps → Add build step → Execute shell:
   cd <DEPLOY_DIR>
   ./deploy.sh "$IMAGE_TAG"

저장
```

> **job 정의는 버전 관리되지 않는다** (SCM을 안 쓰므로). 그래서 두 가지를 해 둔다:
> ① `<JENKINS_HOME_DIR>`를 **Hyper Backup 대상에 추가** (설계서 §12-4)
> ② 위 사양은 설계서 §14-4-1 표에 남아 있으므로, 최악의 경우 표만 보고 5분 안에 재생성할 수 있다

**확인:** job 페이지에 **Build with Parameters** 버튼이 보인다 (Build Now가 아니라 — 파라미터가 걸렸다는 뜻).
아직 누르지 않는다. `<DEPLOY_DIR>`가 §7에서 만들어진 뒤에 실행한다.

---

## 5. CPU 기준선 재기록

Jenkins가 안정화된 뒤(기동 후 10분 이상), 허브를 올리기 **전에** 찍는다.

```sh
top -b -n 1 | head -5
# 또는 DSM 리소스 모니터에서 5분 평균 확인 (웹 UI 접속 자체가 5~10% 먹는다는 점 감안)
```

`tmp/cpu_baseline.md`에 **새 행으로** 추가한다 (기존 행을 지우지 않는다 — 비교 대상이 늘어나는 것이 이득이다):

| 시점 | 상태 | 유휴 CPU |
|---|---|---|
| 2026-08-11 | 구 Jenkins 가동 | ~10–15% |
| (기록) | **새 Jenkins만, 허브 없음** ← L7 비교 기준 | |
| (P1 후 1주) | 새 Jenkins + 허브 | |

---

## 6. CouchDB 준비

### 6-0. 네트워크 — 허브가 CouchDB를 이름으로 찾을 수 있게 (설계서 §1-1)

CouchDB는 도커 **기본 `bridge`** 에 있는데, 기본 브리지에는 내장 DNS가 없어 컨테이너 이름이 해석되지 않는다. 전용 네트워크를 만들어 CouchDB를 **추가로** 연결한다 — 기존 연결과 공개 포트는 그대로 두므로 **LiveSync는 영향받지 않고 CouchDB 재시작도 없다.**

```sh
docker network create <HK_DOCKER_NET>
docker network connect <HK_DOCKER_NET> <HK_COUCHDB_CONTAINER>

# 서브넷이 LAN·Tailscale 대역과 겹치지 않는지 확인
docker network inspect <HK_DOCKER_NET> --format '{{json .IPAM.Config}}'

# CouchDB가 두 네트워크에 붙었는지 확인
docker inspect <HK_COUCHDB_CONTAINER> \
  --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

**확인:** 마지막 명령이 `bridge <HK_DOCKER_NET>` 두 개를 보여준다. LiveSync 클라이언트가 여전히 동기화되는지도 한 번 본다.

> **재생성하면 풀린다.** CouchDB 컨테이너를 재생성하면(이미지 업그레이드 등) 이 연결이 사라진다. 재시작만으로는 풀리지 않는다. `deploy.sh`가 배포할 때마다 다시 붙이므로(`HK_COUCHDB_CONTAINER` 필요) 대개는 저절로 복구되지만, 증상을 알아두면 좋다 — `hk doctor`와 `/healthz`가 `couchdb: down`을 보고한다.

### 6-1. 계정·DB·권한 (관리자 계정으로)

설계서 §2-1·§12-3. **허브는 DB를 만들 수 없다** — `hk_hub`는 서버 관리자가 아니기 때문이다(F4). 그래서 이 단계가 배포보다 앞선다.

```sh
# 이하 <ADMIN>:<ADMINPW>는 CouchDB 서버 관리자 계정. 셸 히스토리에 남지 않게 주의
# ① hk_hub 사용자 생성
curl -X PUT http://<ADMIN>:<ADMINPW>@<COUCHDB_HOST>:5984/_users/org.couchdb.user:hk_hub \
  -H 'Content-Type: application/json' \
  -d '{"name":"hk_hub","password":"<HK_HUB_PW>","roles":[],"type":"user"}'

# ② DB 3개 생성
for db in hyeseongkit_sessions hyeseongkit_auth hyeseongkit_vault; do
  curl -X PUT http://<ADMIN>:<ADMINPW>@<COUCHDB_HOST>:5984/$db
done

# ③ 각 DB의 관리자로 hk_hub 등록 (인덱스=설계문서 생성에 필요 — members 권한으로는 불가)
for db in hyeseongkit_sessions hyeseongkit_auth hyeseongkit_vault; do
  curl -X PUT http://<ADMIN>:<ADMINPW>@<COUCHDB_HOST>:5984/$db/_security \
    -H 'Content-Type: application/json' \
    -d '{"admins":{"names":["hk_hub"],"roles":[]},"members":{"names":[],"roles":[]}}'
done
```

**확인:**

```sh
curl -s http://hk_hub:<HK_HUB_PW>@<COUCHDB_HOST>:5984/hyeseongkit_sessions | head -c 200
# {"db_name":"hyeseongkit_sessions", ...} 가 나오면 성공
curl -s http://hk_hub:<HK_HUB_PW>@<COUCHDB_HOST>:5984/_all_dbs
# obsidian_vault 가 보이더라도 접근은 막혀 있어야 정상 (권한 부여 안 함 — D4 (C))
```

> ⚠️ 기존 위키 볼트 DB(`obsidian_vault`)에는 **어떤 권한도 주지 않는다.** 허브가 그 DB를 건드릴 경로 자체를 만들지 않는 것이 D4 (C)의 물리적 보장이다.

---

## 7. 허브 배포 디렉터리와 `.env`

```sh
mkdir -p <DEPLOY_DIR>
# 저장소의 deploy/docker-compose.yml, deploy.sh 를 <DEPLOY_DIR>/ 로 복사
chmod +x <DEPLOY_DIR>/deploy.sh
# Jenkins(uid 1000)가 읽고 실행할 수 있어야 한다
chown -R 1000:1000 <DEPLOY_DIR>
```

### 7-1. 암호화 키 생성 (D29) — 한 번만, 그리고 잃어버리면 끝이다

```sh
docker run --rm python:3.11-slim sh -c \
  "pip install -q cryptography && python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
```

출력값을 `.env`의 `HK_ENCRYPTION_KEY`에 넣고, **별도 경로에 사본 1부**를 둔다.

> ⛔ **이 키를 잃으면 저장된 모든 세션 본문을 복구할 수 없다.** CouchDB 백업이 있어도 소용없다 — 백업 안의 데이터도 이 키로만 열린다. 키는 `.env`와 물리적으로 다른 곳에 한 부 더 보관한다 (설계서 §12-4).

### 7-2. `.env` 작성

`<DEPLOY_DIR>/.env`를 만들고 저장소 `.env.example`의 키를 채운다. 이 파일은 **NAS에만 존재**한다.

| 키 | 값 |
|---|---|
| `GHCR_OWNER` | ghcr 네임스페이스 (소문자) |
| `IMAGE_TAG` | `latest` (Jenkins 파라미터가 덮어쓴다) |
| `HK_DOCKER_NET` | §6-0에서 만든 전용 네트워크명 |
| `HK_COUCHDB_CONTAINER` | CouchDB 컨테이너 이름 — `deploy.sh`가 네트워크 재연결(자가 복구)에 쓴다 |
| `HK_COUCHDB_URL` | `http://<CouchDB 컨테이너명>:5984` (컨테이너 주소) |
| `HK_COUCHDB_USER` / `_PASSWORD` | `hk_hub` / §6에서 정한 비밀번호 |
| `HK_ADMIN_TOKEN` | 임의의 긴 문자열 (`openssl rand -hex 32`). **기기로 반출하지 않는다** |
| `HK_ENCRYPTION_KEY` | §7-1의 출력값 |

```sh
chmod 600 <DEPLOY_DIR>/.env
```

**확인:** `docker network ls`에 `HK_DOCKER_NET`의 이름이 실제로 있다.

> ⚠️ `.env`는 `deploy.sh`가 **셸로 읽는다**(`. ./.env`). 값에 공백이 있으면 반드시 따옴표로 감싼다.

---

## 8. 첫 배포와 검증

### 8-1. 배포 (T11)

Jenkins → `hk-deploy` → **Build with Parameters** → `IMAGE_TAG` = `latest`(또는 `sha-<커밋>`) → **Build**
콘솔 출력 마지막에 `배포 완료: <태그>` 가 나오면 성공이다.

`healthz 실패` 로 끝나면 허브 로그부터 본다 (설계서의 디버깅 원칙 — 맹목적 재실행 금지):

```sh
docker logs hyeseongkit-hub --tail 50
```

| 로그 | 원인 | 조치 |
|---|---|---|
| `HK_ENCRYPTION_KEY가 비어 있음` / `형식 오류` | 키 미설정·오타 | §7-1 재수행 (**기존 데이터가 있다면 키를 바꾸지 말 것**) |
| `DB '...'가 없고 생성 권한도 없습니다` | §6 미수행 | §6으로 |
| `인덱스 '...' 생성 권한이 없습니다` | `_security.members`에만 등록 | §6 ③을 `admins`로 다시 |
| `HK_COUCHDB_URL이 설정되지 않았습니다` | `.env` 누락 | §7-2 |

### 8-2. 기기 토큰 발급 (§5-2)

```sh
docker exec hyeseongkit-hub hk admin device add desktop --name "데스크톱"
docker exec hyeseongkit-hub hk admin device add macbook --name "맥북"
```

출력된 `token:` 값은 **이 자리에서만 보인다.** 각 기기의 `HK_API_TOKEN`에 바로 옮긴다.

### 8-3. 왕복 검증 (T2)

```sh
# whoami
curl -s -H "Authorization: Bearer <기기토큰>" http://<HUB_HOST>:9100/v1/whoami

# push (프로젝트 등록 → push → resume)
curl -s -X POST http://<HUB_HOST>:9100/v1/projects \
  -H "Authorization: Bearer <기기토큰>" -H 'Content-Type: application/json' \
  -d '{"project_id":"p-smoke","canonical":"named:smoke","name":"smoke","sensitivity":"tech"}'

curl -s -X POST http://<HUB_HOST>:9100/v1/session/push \
  -H "Authorization: Bearer <기기토큰>" -H 'Content-Type: application/json' \
  -d '{"project_id":"p-smoke","title":"deploy smoke test","slug":"deploy-smoke",
       "sections":{"todo":"1. verify","know":"- port 9100"},"device":"desktop"}'

curl -s -H "Authorization: Bearer <기기토큰>" \
  "http://<HUB_HOST>:9100/v1/session/resume?last=1&project_id=p-smoke"
```

**확인:** resume 응답의 packet에 `1. verify`와 `- port 9100`이 **원문 그대로** 들어 있다.

암호화 확인 (D29):

```sh
curl -s http://hk_hub:<HK_HUB_PW>@<COUCHDB_HOST>:5984/hyeseongkit_sessions/_all_docs?include_docs=true \
  | grep -c "port 9100"        # 0 이어야 한다 (본문이 평문으로 저장되지 않음)
```

### 8-4. 재부팅 생존 (T9)

DSM 재부팅 후:

```sh
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'hyeseongkit|jenkins'
curl -s http://<HUB_HOST>:9100/healthz
```

**확인:** 허브·Jenkins 모두 `Up`, healthz의 `couchdb`가 `ok`.

### 8-5. 검증 후 정리

스모크 테스트로 만든 스레드는 남겨 두면 D14(활성 3개) 예산을 잡아먹는다:

```sh
curl -s -X POST http://<HUB_HOST>:9100/v1/session/close \
  -H "Authorization: Bearer <기기토큰>" -H 'Content-Type: application/json' \
  -d '{"thread":"<스모크-thread-id>","outcome":"dropped","device":"desktop"}'
```

---

## 9. 기기 설정 (P2/P3)

각 기기(데스크톱·맥북)에서:

```sh
pipx install git+https://github.com/<owner>/hyeseongkit.git      # 또는 저장소 클론 후 pipx install .
```

`~/.hyeseongkit/config.env` 작성 (설계서 §11-1):

```
HK_HUB_URL=http://<HUB_HOST>:9100
HK_API_TOKEN=hk_...          # §8-2에서 그 기기용으로 발급한 것
HK_DEVICE_ID=desktop         # 토큰을 발급한 device_id와 일치해야 한다
```

```sh
hk doctor          # 전 항목 통과 확인
hk setup           # 슬래시 커맨드·훅·MCP 등록 (기기당 1회)
cd <프로젝트> && hk init
```

**확인 (T6):** Claude Code를 재시작하면 SessionStart 훅이 이전 상태를 주입한다. 허브를 내려도 Claude Code는 정상 시작해야 한다(훅은 실패해도 exit 0).

---

## 10. 문제가 생기면

| 상황 | 대응 |
|---|---|
| 배포 후 허브가 이상 | Jenkins에서 `IMAGE_TAG=sha-<이전커밋>`로 재실행 (설계서 §14-6) |
| Jenkins가 죽음 | NAS 셸에서 `cd <DEPLOY_DIR> && ./deploy.sh <태그>` — 배포 능력은 Jenkins에 종속되지 않는다 |
| CPU가 기준선 대비 크게 상승 | 1주 관측(L7) 결과에 따라 렌더러를 데스크톱으로 후퇴 검토 (기획서 §4-1-3 (B)) |
| 세션이 저장되지 않음 | `hk doctor` → 큐 적체 확인 → `hk queue --flush` |
| 본문이 안 읽힘 | `HK_ENCRYPTION_KEY`가 바뀌었는지 확인. ⛔ 키를 되돌리는 것 외에 복구 수단이 없다 |

---

## 부록. 체크리스트

```
[ ] §0-1 실측 3값 (DOCKER_GID / JENKINS_PORT / 기존 Jenkins 형태)
[ ] §2   jenkins_home 백업 → 크기 확인 → 컨테이너 제거 → jenkins_home 초기화(rename)
[ ] §3   Jenkins 기동 → 플러그인 전부 해제 설치 → 관리자 계정
[ ] §3-3-1 ★ 접근 제한 (Tailscale 바인딩 또는 방화벽) → LAN에서 접속 거부 확인  ← T17
[ ] §3-4 DooD 확인 (docker ps가 호스트 컨테이너를 보여줌)          ← T14
[ ] §4   hk-deploy job (SCM 없음 / 트리거 없음 / IMAGE_TAG 파라미터)
[ ] §4   <JENKINS_HOME_DIR>를 Hyper Backup 대상에 추가
[ ] §5   CPU 기준선 재기록 (Jenkins 있고 허브 없는 상태)
[ ] §6-0 전용 네트워크 생성 + CouchDB 추가 연결 (LiveSync 정상 확인)
[ ] §6-1 hk_hub 계정 + DB 3개 + _security.admins
[ ] §7   deploy/ 복사 + chmod +x + chown 1000
[ ] §7-1 HK_ENCRYPTION_KEY 생성 + 별도 보관                        ← 분실 시 복구 불가
[ ] §7-2 .env 작성 + chmod 600
[ ] §8-1 첫 배포 성공 (healthz)                                    ← T11
[ ] §8-2 기기 토큰 발급 (desktop / macbook)
[ ] §8-3 push→resume 왕복 + 암호화 확인                            ← T2, D29
[ ] §8-4 재부팅 후 자동 기동                                       ← T9
[ ] §8-5 스모크 스레드 close
[ ] §9   기기별 pipx + config.env + hk setup + hk init             ← T6
[ ] §10  1주 CPU 관측 시작 (L7)
[ ] 마무리: jenkins_home.old-* 폴더 삭제 (§8 검증 통과 후)
```
