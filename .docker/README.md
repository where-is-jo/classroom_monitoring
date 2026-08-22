# docker compose 실행 구성

**팀 공식 실행 수단이다**([결정 0018](../docs/architecture/decisions.md#0018--docker-compose-구성을-저장소에-커밋하고-localdev-파일을-나눈다)).
이 디렉터리는 커밋된다 — 최상위 구조 제약의 유일한 인프라 예외다.

**단, 디렉터리 전체가 커밋되는 것은 아니다.** 아래 둘은 각자 채워야 한다.

| 커밋 안 됨 | 무엇 | 어떻게 채우나 |
| --- | --- | --- |
| `env/<서비스>.<환경>.env` | 컨테이너에 주입하는 실제 값·비밀값 | 변수 목록은 `<서비스>/.env.example`. 저장소 밖에서 전달받는다 |
| `models/` | 모델 가중치(`yolo11m.pt`, `gemma.gguf`) | 각자 내려받아 둔다. `gemma.gguf`는 5.4GB다 |

## 파일

환경마다 다른 것이 값이 아니라 **구조**(이미지를 빌드하나 받나, GPU를 예약하나, 포트를
어디까지 여나)라서 파일 자체를 나눴다.

**남은 것은 dev뿐이고, dev는 호스트 축으로 나뉜다.**
[결정 0026](../docs/architecture/decisions.md#0026--백엔드를-개인-pc에-두고-gpu가-필요한-것만-gpu-서버에-남긴다)이
백엔드를 개인 PC로 옮기면서 dev 환경이 기계 두 대에 걸치게 됐고, 그 4번이 "compose
파일을 호스트 축으로 나눈다"를 남은 일로 적었다. 아래가 그 구현이다.

| 스택 | dev · 개인 PC(노트북) | dev · 공용 GPU 서버 |
| --- | --- | --- |
| 메인 | `compose.main.dev.pc.yml` | `compose.main.dev.gpu.yml` |
| LLM | — | `compose.llm.dev.yml` |
| 모니터링 | — | `compose.monitoring.dev.yml` |

`compose.main.dev.yml` 하나였던 것이 `.pc`와 `.gpu` 둘로 갈렸다. **이름에 환경 다음
호스트를 붙인다**(`compose.<스택>.<환경>.<호스트>.yml`). 호스트가 하나뿐인 스택은
그대로 두 마디를 쓴다 — 나뉘지 않는 것에 축을 붙이면 이름만 길어진다.

| 어느 스택에 무엇이 있나 | 서비스 |
| --- | --- |
| 개인 PC (`compose.main.dev.pc.yml`) | `fastapi`, `n8n` |
| GPU 서버 (`compose.main.dev.gpu.yml`) | `deeplearning`, `inference-worker`, `mediamtx`, `minio`, `minio-init` |
| GPU 서버 (`compose.llm.dev.yml`) | `llama-server` |
| GPU 서버 (`compose.monitoring.dev.yml`) | `prometheus`, `grafana`, `loki`, `alloy` |

### local 스택은 없앴다

`compose.*.local.yml` 셋과 짝인 `prometheus.local.yml`·`config.local.alloy`를 지웠다
([결정 0034](../docs/architecture/decisions.md#0034--local-compose-스택을-없애고-로컬-실행은-소스-직접-구동으로-한정한다)).
백엔드가 노트북으로 오면서 "개발자 PC 한 대에서 전부"와 "dev의 개인 PC 절반"이 같은
기계를 가리키게 됐고, 같은 포트를 두 파일이 다투게 됐다.

**`APP_ENV=local`이 없어진 것이 아니다.** 컨테이너로 띄우는 local 스택만 없앴다.
로컬에서 소스를 고쳐 가며 볼 때는 컨테이너를 거치지 않고 직접 띄운다.

```bash
cd webapps/fastapi
cp .env.example .env.local     # DATABASE_MODE=memory라 외부 의존이 없다
python -m uvicorn app.main:app --reload --port 8000
```

`--reload`가 붙어 이쪽이 오히려 빠르다. 컨테이너 local 스택은 소스를 고칠 때마다
`--build`가 필요했다.

**`--env-file`을 주지 않는다.** 커밋되는 파일이므로 실행에 필요한 값을 파일 안에 직접
적었다. `${...}` 치환에 의존하면 저장소에서 받은 파일만으로 실행할 수 없기 때문이다.

**`--env-file`을 주지 않는다.** 커밋되는 파일이므로 실행에 필요한 값을 파일 안에 직접
적었다. `${...}` 치환에 의존하면 저장소에서 받은 파일만으로 실행할 수 없기 때문이다.

### 두 호스트는 Tailscale로 잇는다

같은 compose network가 아니므로 **호스트를 넘는 호출은 컨테이너 이름이 아니라 Tailscale
주소를 쓴다.** 무엇이 넘고 무엇이 넘지 않는지는 결정 0026의 3번 표가 정본이고, 여기서는
그 결과로 실제로 열린 포트만 적는다.

| 부르는 쪽 | 주소 | 무엇 |
| --- | --- | --- |
| 개인 PC의 `fastapi` | `100.85.0.72:18100` | `deeplearning` 얼굴 분석 |
| 개인 PC의 `fastapi` | `100.85.0.72:19000` | `minio` 스냅샷 읽기 |
| 개인 PC의 `fastapi` | `100.85.0.72:18889` | `mediamtx` WHEP 시그널링 중계 |
| 개인 PC의 `fastapi` | `100.85.0.72:18008` | `llama-server` 검색 계획 |
| GPU 서버의 `inference-worker` | `100.119.241.93:8076` | `fastapi` 탐지 이벤트 전송 |
| GPU 서버의 `prometheus` | `100.119.241.93:8076` | `fastapi` 지표 스크랩 |
| 브라우저 | GPU 서버 `18189` (UDP·TCP) | WebRTC 미디어. **직통** |

**GPU 서버에서 새로 연 넷은 전부 Tailscale 주소(`100.85.0.72`)에만 묶었다.** 그 서버는
공인 IP를 가진 공용 장비라 `0.0.0.0`은 곧 인터넷 공개이고, MinIO는 root 키 하나로
스냅샷 전체(학생 얼굴이 담긴다)가 열린다. `llama-server`도 인증이 없다.

**Tailscale이 내려가 있으면 양쪽 스택 모두 기동에 실패한다.** 없는 주소에는 bind할 수
없어서다. 조용히 뜨는 것보다 낫다 — 떠 있어도 서로 닿지 못한다.

**노트북의 Tailscale 주소가 바뀌면 세 곳을 함께 고친다**: `compose.main.dev.pc.yml`의
`ports`와 n8n 진입 주소, `env/worker.dev.env`의 `FASTAPI_URL`,
`prometheus/prometheus.dev.yml`의 fastapi target.

### 앞단에 reverse proxy를 두지 않는다

**인터넷에 공개하지 않는 프로젝트라 도메인·TLS·경로 분기가 필요 없다.** 브라우저는
tailnet 안에서 `http://100.119.241.93:8076`(화면·API)과 `:15678`(n8n)에 직접 붙는다.

이 때문에 두 값이 평문 http 전제로 고정되어 있다. 붙이는 날 함께 되돌린다.

| 값 | 지금 | 왜 |
| --- | --- | --- |
| `PLAYBACK_SESSION_COOKIE_SECURE` | `false` | true면 평문 http에서 브라우저가 세션 cookie를 보내지 않아 영상 재생이 조용히 실패한다 |
| `N8N_SECURE_COOKIE` | `false` | 같은 이유로 n8n 로그인 세션이 깨진다 |

reverse proxy를 붙이게 되면 uvicorn에 `FORWARDED_ALLOW_IPS`를 함께 줘야 한다 —
기본값이 `127.0.0.1`이라 컨테이너 network를 타고 온 `X-Forwarded-*`는 무시된다.

## 실행 방법

**컨테이너로 띄우는 것은 dev 하나뿐이다.** 소스를 고쳐 가며 볼 때는 위
[local 스택은 없앴다](#local-스택은-없앴다)의 직접 구동을 쓴다.

### dev 스택 — 개인 PC(노트북)

이 노트북에 백엔드와 n8n을 올린다.

```bash
docker compose -f .docker/compose.main.dev.pc.yml pull
docker compose -f .docker/compose.main.dev.pc.yml up -d
docker compose -f .docker/compose.main.dev.pc.yml config    # 문법 검증
docker compose -f .docker/compose.main.dev.pc.yml down
```

**먼저 `tailscale status`로 노트북이 tailnet에 붙어 있는지 확인한다.** 붙어 있지 않으면
`100.119.241.93`에 bind하지 못해 기동에 실패한다.

| 주소 | 무엇 | 누가 |
| --- | --- | --- |
| `http://localhost:8076` | 웹 화면·API | 이 노트북 |
| `http://100.119.241.93:8076` | 같은 것 | 팀원, GPU 서버의 worker·prometheus |
| `http://localhost:15678` | n8n 편집기 | 이 노트북 |
| `http://100.119.241.93:15678` | 같은 것 | 팀원 |

**GPU 서버 스택이 떠 있지 않아도 화면과 API는 뜬다.** 대신 그쪽을 부르는 기능이 각각
실패한다 — 얼굴 등록(deeplearning), 스냅샷 목록(minio), 자연어 검색(llama-server),
실시간 영상(mediamtx). 서로 다른 실패이며 한 번에 다 죽지 않는다.

**n8n 워크플로는 GPU 서버에서 따라오지 않는다.** 볼륨이 기계에 묶여 있고 project name도
`classroom-monitoring-dev`에서 `classroom-monitoring-dev-pc`로 바뀌었다. 옮기려면 서버
쪽 편집기에서 워크플로와 자격 증명을 export한 뒤 여기서 import한다.

#### Docker Desktop이 자동 시작되어야 한다

**노트북이라 절전·재부팅이 잦은데, Docker Desktop이 내려가면 스택도 함께 내려간다.**
실제로 배포 중에 두 컨테이너가 같은 시각에 조용히 종료된 적이 있다 — 앱 오류가 아니라
(`exit 0`, 로그 마지막이 정상 shutdown) 데몬이 내려간 것이었다.

compose에 `restart: unless-stopped`가 걸려 있지만 **데몬 자체가 안 뜨면 소용이 없다.**
Docker Desktop 설정의 **"Start Docker Desktop when you log in"을 켜 둔다.**

꺼져 있으면 노트북을 켜도 스택이 죽어 있고, 증상은 GPU 서버 쪽에서 이렇게 보인다.

- worker의 탐지 이벤트가 갈 곳을 잃는다(제한 재시도 뒤 버려진다)
- Prometheus의 fastapi target이 `up=0`이 된다

#### 개인 PC 스택에서 확인한 것

노트북(Windows, Docker Desktop)에서 실제로 띄워 확인했다.

- 두 컨테이너 기동, fastapi `healthy`.
- **`127.0.0.1`과 `100.119.241.93` 두 주소 모두에 bind가 실제로 먹는다.**
  Windows Docker Desktop에서 특정 IP bind가 되는지가 이 구성의 전제였다.
- `GET /health` → 200 `{"status":"ok"}` (양쪽 주소 모두)
- `GET /health/ready` → 200 `{"status":"ready"}` — MongoDB Atlas 연결까지 성공했다는 뜻이다.
- n8n `GET /healthz` → 200 (양쪽 주소 모두)

**호스트를 넘는 네 경로를 기능 레벨까지 확인했다.** GPU 서버 스택을 함께 올린 상태다.

| 기능 | 경유 | 결과 |
| --- | --- | --- |
| 스냅샷 목록 | fastapi → MinIO | 200 |
| 스냅샷 이미지 프록시 | MinIO → fastapi → 브라우저 | 200, 31,927 B JPEG, **25ms** |
| 자연어 검색 | fastapi → llama-server | 200, **1.1초**. 계획(intent·기간·limit)까지 생성됨 |
| 얼굴 분석 | fastapi → deeplearning | `/health` 200, 19ms |

**GPU 서버 스택이 내려가 있어도 화면과 API는 뜬다.** 대신 위 넷이 각각 실패한다 —
서로 다른 실패이며 한 번에 다 죽지 않는다.

**아직 확인하지 못한 것**은 실시간 영상(WebRTC 미디어가 실제로 흐르는 것, 브라우저가
필요하다)과 worker → fastapi 탐지 이벤트(카메라가 붙지 않아 보낼 것이 없다)다.

### dev 스택 — 공용 GPU 서버

```bash
docker compose -f .docker/compose.main.dev.gpu.yml up -d    # 먼저 (network를 만든다)
docker compose -f .docker/compose.llm.dev.yml up -d
docker compose -f .docker/compose.monitoring.dev.yml up -d
```

자세한 절차는 [README.server.md](./README.server.md)에 있다.


---

> 아래는 `docker-compose.yml` 하나로 돌리던 시절의 기록이다. 그 파일은 더 이상 없다.

## 진행 단계

1. **기본 이미지 세팅** — compose 뼈대(network/volume), 레지스트리 정책 (완료)
2. **FastAPI, MinIO, MediaMTX 컨테이너** (완료 — `docker compose up -d`로 기동·검증함)
3. **Grafana, Prometheus, n8n, reverse proxy(Caddy) 컨테이너** (완료 — 기동·검증함)
3.5. **Loki, Grafana Alloy (로그 수집)** (완료 — 기동·검증함)
4. **이미지 빌드** (완료 — fastapi 명시적 태그 빌드, 서드파티 이미지 버전 고정)
5. **Grafana 대시보드, MinIO 버킷·전용 키** (완료 — 기동·검증함)

## 포트 정리

> **지금은 이렇지 않다.** 두 번 바뀌었다.
>
> 1. 공용 서버에서 다른 팀과 포트가 부딪혀 여는 포트를 줄이고 루프백에 묶었다.
>    **reverse proxy(Caddy)도 쓰지 않기로 했다.**
> 2. 결정 0026으로 fastapi·n8n이 개인 PC로 가면서, GPU 서버에서 그 둘이 부르던 것들
>    (`deeplearning`·`minio`·`mediamtx`·`llama-server`)을 **Tailscale 주소에만** 새로
>    열었다. 앞단의 다른 팀 nginx도 더 이상 전제하지 않는다 — 인터넷에 공개하지 않는다.
>
> 현재 표는 위 [두 호스트는 Tailscale로 잇는다](#두-호스트는-tailscale로-잇는다)와
> [README.server.md의 포트 절](./README.server.md#포트)에 있다. 아래는 그때의 기록이다.

| 서비스 | 포트 | 용도 |
| --- | --- | --- |
| caddy | 80 | **브라우저 진입점.** fastapi로 reverse proxy |
| fastapi | 8000 | 직접 접근(디버깅용). 실제 진입은 80을 통한다 |
| minio | 9000 / 9001 | S3 API / 콘솔 |
| mediamtx | 8554 / 8888 / 8889 | RTSP / HLS / WebRTC |
| prometheus | 9090 | 운영자 직접 접근 |
| grafana | 3000 | 운영자 직접 접근 |
| n8n | 5678 | 운영자 직접 접근 |
| loki | 3100 | 운영자 직접 접근(디버깅). Grafana는 backend network로 접근 |
| alloy | 12345 | Alloy UI(파이프라인 상태 확인), 운영자 직접 접근 |

## 레지스트리 정책

| 서비스 | 이미지 | 비고 |
| --- | --- | --- |
| FastAPI | `ghcr.io/whereisjo/smart-office-monitoring-fastapi:local` (로컬 build) | 저장소 코드 기반 자체 이미지. `ghcr.io/...` 이름은 push를 대비해 붙였을 뿐, **아직 push하지 않았다**(로컬 이미지로만 존재) |
| MinIO | `minio/minio:RELEASE.2025-09-07T16-13-09Z` | `mc` 바이너리가 포함돼 있어 `mc ready local`로 헬스체크한다 |
| MediaMTX | `bluenviron/mediamtx:1.20.0` | `ghcr.io/bluenviron/mediamtx`는 pull 시 `denied` 응답이라 docker.io로 확정 |
| Grafana | `grafana/grafana-oss:13.0.2` | OSS 이미지 |
| Prometheus | `prom/prometheus:v3.13.2` | 공식 이미지 |
| n8n | `n8nio/n8n:2.33.5` | 공식 이미지 |
| Caddy | `caddy:2.11.4` | 공식 이미지 |
| Loki | `grafana/loki:3.7.6` | baked-in `local-config.yaml`(filesystem 저장) 그대로 사용 |
| Grafana Alloy | `grafana/alloy:v1.18.1` | Promtail 대신 채택 — Grafana가 Promtail을 유지보수 모드로 두고 Alloy로 통합하는 중 |

- 태그는 이 세션에서 실제로 pull·기동까지 검증한 버전으로 고정했다(4단계). 버전
  문자열은 추측이 아니라 각 이미지 바이너리의 `--version`/라벨 출력과 digest 비교로
  확인했다 — 예: `docker pull prom/prometheus:v3.13.2` 후 digest가 이미 검증된
  `:latest`의 digest와 동일함을 확인.
- MongoDB Atlas는 컨테이너화하지 않는다(외부 서비스). fastapi 컨테이너가
  `.docker/env/fastapi.<환경>.env`의 `DATABASE_URL`로 접속한다.
  **local 판본은 memory mode라 접속하지 않는다** — 아래 환경변수 항목 참고.

## 환경변수

구조와 규칙은 [결정 0017](../docs/architecture/decisions.md)과
[환경변수 규칙](../docs/conventions/environment-convention.md)에 있다. 요약은
[README.server.md의 환경변수 절](./README.server.md#환경변수)을 본다.

- **`.docker/env/`에는 이제 `*.dev.env`만 있다.** `*.local.env` 다섯은 local 스택과 함께
  지웠다(결정 0034). 소스로 직접 띄울 때는 `.docker/`가 아니라 각 서비스의
  `.env.local`(예: `webapps/fastapi/.env.local`)을 읽는다 — 실행 방식이 다르면 값을
  담는 파일도 다르다는 [결정 0017](../docs/architecture/decisions.md#0017--컨테이너-실행의-환경변수를-세-계층으로-나누고-docker-아래에-둔다)의 구분은 그대로다.
- **컨테이너 이미지에는 `.env`가 들어 있지 않다**(`webapps/fastapi/.dockerignore`).
  `APP_ENV`·`DATABASE_MODE`는 기본값이 없는 필수 설정이라, env 없이 `docker run`하면
  기동 시점에 pydantic `ValidationError`로 죽는다. 이미지 문제가 아니라 주입 문제다.
- 비밀값(`MINIO_ROOT_PASSWORD`, `GF_SECURITY_ADMIN_PASSWORD`)은 로컬 docker 구동
  전용 값이다. **지금은 dev 서버와 같은 값을 쓰고 있어 분리가 필요하다.**
- n8n은 최신 버전에서 basic auth 대신 첫 접속 시 브라우저에서 owner 계정을 직접
  만드는 방식이라 별도 비밀번호 env var가 없다.

### memory mode 주의 (소스 직접 구동에도 그대로 해당한다)

`.env.example`이 `DATABASE_MODE=memory`이고 `DATABASE_URL`은 비어 있다.
**프로세스를 다시 띄우면 데이터가 사라지고 Atlas에는 아무것도 쌓이지 않는다.**
`/health/ready`가 ready인 것도 Atlas 연결 확인이 아니라 memory 저장소 응답이다.
로컬에서 Atlas를 쓰려면 `.env.local`의 `DATABASE_MODE`·`DATABASE_URL`·`DATABASE_NAME`을
채운다. 단 `DATABASE_MODE=memory`는 `APP_ENV=local`에서만 허용되므로 그 반대는 막힌다.

## reverse proxy(Caddy) 설계

아키텍처 규칙 "브라우저는 fastapi만 호출한다"(`docs/agents/AGENTS.md`)에 맞춰 Caddy는
fastapi 하나만 앞단에 둔다(`:80 -> fastapi:8000`). Grafana·Prometheus·n8n·MinIO 콘솔은
브라우저 진입점이 아니라 운영자 도구라서 각자 published port로 직접 접근한다.
경로 기반(`/grafana/*` 등)으로 전부 Caddy 뒤에 몰아넣는 방식도 가능하지만, Grafana·n8n은
서브패스 운영에 별도 설정(`root_url`, base path)이 필요해 깨지기 쉽다. 필요해지면
그때 서비스별로 확인하며 추가한다.

## 2단계에서 확인한 것

- `webapps/fastapi/Dockerfile`, `webapps/fastapi/.dockerignore` 추가 — 이 둘은 서비스
  디렉터리 안이라 최상위 제약과 무관하고, 저장소에 커밋되는 파일이다.
- `docker compose up -d --build` 실행 → fastapi/minio/mediamtx 세 컨테이너 모두 기동.
- `GET /health` → `{"status":"ok"}`, `GET /health/ready` → `{"status":"ready"}` (Atlas
  연결 성공 확인), MinIO 콘솔(`:9001`) → HTTP 200, MediaMTX HLS(`:8888`) → HTTP 404
  (스트림 없음, 서버는 응답함).
- MediaMTX 기본 이미지 설정은 `api: no`라 `:9997` API가 꺼져 있다. 필요해지면
  커스텀 `mediamtx.yml`을 volume으로 마운트해 켜야 한다(지금은 포트 매핑도 하지 않음).
- fastapi/minio 헬스체크는 `docker compose ps`에서 `healthy`로 확인했다.

## 3단계에서 확인한 것

- `.docker/prometheus/prometheus.yml` — 최소 설정. fastapi가 아직
  `/metrics`를 노출하지 않아(`monitoring/internal/README.md` "구현 전") 지금은 Prometheus
  자기 자신만 스크랩한다. 서비스가 지표를 노출하면 job을 추가한다.
- Grafana에 Prometheus 데이터소스를 자동 등록.
  (설정 파일은 이후 `monitoring/internal/grafana/provisioning/`으로 옮겼다 — 5단계 참고)
- `.docker/caddy/Caddyfile` — `:80 -> fastapi:8000` reverse proxy.
- `docker compose up -d` 실행 → 7개 컨테이너(fastapi/minio/mediamtx/prometheus/grafana/n8n/caddy)
  모두 기동.
- `curl http://127.0.0.1/health` → 200 (**Caddy → fastapi 프록시 동작 확인**)
- Prometheus `/-/ready` → ready, 자기 자신 타겟 `health: up`
- Grafana `/api/health` → ok, `/api/datasources` → **Prometheus 데이터소스 자동
  프로비저닝 확인**
- n8n `:5678/` → HTTP 200

## 로그 수집(Loki + Alloy) 설계

- `.docker/alloy/config.alloy` — `discovery.docker`로 같은 docker
  daemon의 모든 컨테이너를 찾고, `loki.source.docker`가 Docker 로그 API로 각 컨테이너의
  로그를 읽어 `loki.write`로 Loki에 push한다. Promtail처럼 `/var/lib/docker/containers`를
  직접 마운트하지 않고 `/var/run/docker.sock`만 있으면 된다.
- Grafana에 Loki 데이터소스 자동 등록(설정 파일 위치는 5단계 참고).
  **Grafana는 시작 시점에만 provisioning 디렉터리를 읽으므로,
  이미 떠 있는 상태에서 provisioning 파일을 추가했으면 `docker compose restart grafana`가
  필요하다.**
- Loki는 이미지에 baked-in된 `local-config.yaml`을 그대로 쓴다(커스텀 설정 파일 없음).
  로컬 검증 용도로는 filesystem 저장으로 충분하다.

### 3.5단계에서 확인한 것

- `docker compose up -d` 실행 → loki/alloy 컨테이너 기동, alloy 로그에 config 파싱
  오류 없음.
- `curl http://127.0.0.1:3100/loki/api/v1/label/container/values` → 실행 중인 9개
  컨테이너 이름이 모두 label 값으로 잡힘 (**Alloy가 전체 컨테이너를 자동 discovery하고
  있다는 뜻**).
- `curl .../loki/api/v1/query_range?query={container="smart-office-monitoring-fastapi-1"}`
  → fastapi의 실제 `/health` 접근 로그 라인이 조회됨 (**로그가 실제로 Loki에 적재되고
  있음을 확인**).
- grafana 재시작 후 `/api/datasources` → `Loki`, `Prometheus` 둘 다 등록 확인.

## 4단계에서 확인한 것

- fastapi 서비스에 `image: ghcr.io/whereisjo/smart-office-monitoring-fastapi:local`을
  붙이고 `docker compose build fastapi`로 그 태그로 빌드. compose가 매번 자동 생성하던
  이름(`smart-office-monitoring-fastapi`) 대신 GHCR push를 염두에 둔 이름을 쓴다.
  **이후 `docker login ghcr.io` 인증 후 실제로 push 완료** — 사용자 요청을 받고서 진행했다.
  digest `sha256:7bd04406860567f59db7588bdc3160832a875dc6b722ab5f946de1261beda050`.
  최초 토큰은 `write:packages` 스코프가 없어 `permission_denied`로 실패했고,
  스코프를 채운 토큰으로 재로그인한 뒤 성공했다.
  **push 당시의 네임스페이스는 `chulgeunhatjo`였다.** 이후 팀 이름이 바뀌어 이 문서와
  compose 파일의 이미지 이름을 `whereisjo`로 바꿨다. 위 digest는 그때 올린 것과 같다.
- 서드파티 이미지 8개(minio/mediamtx/grafana-oss/prometheus/n8n/caddy/loki/alloy) 모두
  `latest` 대신 구체적 버전 태그로 고정. 각 태그를 pull한 뒤 이전에 검증된 `latest`
  digest와 동일한지 대조해서 확정했다(추측 태그 없음).
- `docker compose up -d` 재적용 → 9개 컨테이너 전부 재생성·기동.
- 재생성 후 전체 스모크 테스트 재실행: fastapi `/health/ready` ready, Caddy→fastapi
  200, MinIO 콘솔 200, Prometheus ready, Grafana 데이터소스(Loki+Prometheus) 유지,
  Loki가 9개 컨테이너 전부 계속 discovery, n8n 200 — **모두 정상**.

## 5단계 — Grafana 대시보드, MinIO 버킷·전용 키

### Grafana 설정을 `monitoring/`으로 옮겼다

**`.docker/grafana/`를 통째로 `monitoring/internal/grafana/`로 옮겼고 compose가 거기서 마운트한다.**
`.docker/`는 `.gitignore` 대상이라 여기 두면 대시보드가 저장소에 남지 않는데,
`monitoring/internal/README.md`는 "Grafana 대시보드 정의 파일 관리"를 자기 책임으로 명시하고
루트 `CLAUDE.md`도 `monitoring/`을 "Prometheus·Grafana 설정"으로 규정한다.
데이터소스만 `.docker/`에 남기면 설정이 두 군데로 갈려서 함께 옮겼다.

```yaml
volumes:
  - ../monitoring/internal/grafana/provisioning:/etc/grafana/provisioning:ro
  - ../monitoring/internal/grafana/dashboards:/etc/grafana/dashboards:ro
```

- 대시보드 `stack-status.json`(uid `smart-office-stack`) 하나. 내용과 검증 방법은
  [monitoring/internal/README.md](../monitoring/internal/README.md)에 있다.
- **데이터소스에 `uid`를 명시하면서 기동이 한 번 깨졌다.** uid 없이 먼저 provisioning된
  데이터소스가 grafana volume에 남아 있어 `Datasource provisioning error: data source not
  found`로 재시작 루프에 빠졌다. 각 데이터소스 파일에 `deleteDatasources`를 넣어
  이름 기준으로 지우고 다시 만들도록 해서 해결했다.
- 검증: `/api/datasources`에 uid `prometheus`·`loki` 확인, `/api/search?type=dash-db`에
  대시보드 등록 확인, 데이터소스 프록시로 패널 쿼리 6개 전부 실행해 값이 나오는 것까지 확인.

**Prometheus 지표 패널은 아직 없다.** 어떤 서비스도 `/metrics`를 노출하지 않아
스크랩 타겟은 Prometheus 자기 자신뿐이다. 지표가 정해지면 별도 대시보드로 추가한다.

### MinIO — 버킷과 fastapi 전용 access key

`fastapi`는 아직 MinIO를 쓰지 않는다(ADR-0004 저장 정책 미확정). **연결이 되는지만**
확인하려고 다음을 만들었다.

| 항목 | 값 |
| --- | --- |
| 버킷 | `smart-office-test` (연결 검증 전용) |
| 사용자 | `smart-office-app` (root와 별개) |
| 정책 | `smart-office-app-rw` — `.docker/minio/policy-app-rw.json` |
| 자격 증명 | `env/minio-app.env` (gitignore 대상, 아직 어떤 컨테이너에도 안 걸림) |

정책은 `smart-office-test` 버킷의 `ListBucket`/`GetBucketLocation`과 객체의
`Get`/`Put`/`Delete`만 허용한다. **root 자격 증명은 애플리케이션에 넘기지 않는다.**

**실제 영상·스냅샷 버킷 이름은 정하지 않았다.** ADR-0004가 상시 녹화 여부·보존 기간·
접근 권한·개인정보 처리 근거를 `결정 필요`로 두고 "합의 전에는 영상 상시 저장 기능을
만들지 않는다"고 못박고 있어서, 검증용 버킷 하나만 만들고 이름 규칙은 합의 후로 미뤘다.

검증(별도 컨테이너에서 전용 키로, docker network의 `minio:9000`으로 접속):

```bash
docker run --rm --network smart-office-backend \
  -e MC_HOST_app="http://<ACCESS_KEY>:<SECRET_KEY>@minio:9000" \
  --entrypoint sh minio/minio:RELEASE.2025-09-07T16-13-09Z -c 'mc ls app/smart-office-test'
```

업로드 → 목록 → 다운로드 후 내용 일치 → 권한 없는 버킷 접근 거부 → 버킷 목록에
다른 버킷 미노출 → 삭제까지 **6개 항목 전부 통과**했다.

주의: MinIO 이미지에는 `diff`·`grep`이 없다(최소 이미지). 셸 내장 기능으로 판정해야 한다.
그리고 `mc`는 **alias가 없으면 `alias/bucket`을 로컬 경로로 해석한다** — `mc mb root/버킷`이
버킷 대신 컨테이너 안 `/root/버킷` 디렉터리를 만든 적이 있다. 자격 증명이 붙은 alias가
있는지 `mc alias list <이름>`으로 먼저 확인한다. root 비밀번호가 `-`로 시작하면
`mc alias set`이 플래그로 오해하므로 `MC_HOST_<alias>` 환경변수를 쓴다.

## 안 한 것 / 남은 일

이 디렉터리 안에서 끝낼 수 있는 건 다 했다. 실제로 뭘 더 정하고 만들어야 하는지는
서비스별로 `individual_tasks/남은_작업.md`에 정리해뒀다(수집할 metric, 알림, MinIO/MediaMTX
실 연동, 공용 서버 비밀값 관리 등).

컨테이너는 계속 떠 있는 상태다. 중지하려면 `docker compose -f .docker/docker-compose.yml down`.
