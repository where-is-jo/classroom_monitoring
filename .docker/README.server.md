# 공용 GPU 서버 docker compose

`individual_tasks/도커구성/도커구조_공용서버.md`의 컨테이너 구분·모식도를 옮긴 뒤
실제로 돌려 보며 고친 compose 구성이다.
**이 문서는 dev(공용 GPU 서버) 전용이다.** 로컬 스택은 [README.md](./README.md)를 따른다.

> 이 문서가 참조하는 `individual_tasks/` 아래 자료(도커 구조 설계, nginx 연동 요청서)는
> 개인 작업 자료라 `.gitignore` 대상이고 **저장소에 없다.** 필요하면 작성자에게 받는다.

`.docker/`는 커밋된다([결정 0018](../docs/architecture/decisions.md#0018--docker-compose-구성을-저장소에-커밋하고-localdev-파일을-나눈다)).
단 `env/`(비밀값)와 `models/`(가중치)는 제외이므로 **서버에는 그 둘을 따로 올려야 한다.**

## 파일

| 파일 | project name | 담는 것 |
| --- | --- | --- |
| `compose.main.dev.yml` | `classroom-monitoring-dev` | FastAPI, inference worker, deeplearning, MediaMTX, MinIO, n8n |
| `compose.llm.dev.yml` | `classroom-monitoring-dev-llm` | llama-server (Gemma GGUF) |
| `compose.monitoring.dev.yml` | `classroom-monitoring-dev-observability` | Prometheus, Grafana, Loki, Alloy |
| `alloy/config.dev.alloy` | — | 서버용 로그 수집 설정 |
| `env/<서비스>.dev.env` | — | `env_file`로 컨테이너에 주입하는 값. **커밋되지 않는다** |
| `models/` | — | 모델 가중치. **커밋되지 않는다** |

같은 이름의 `.local.yml` 짝이 개발자 PC용으로 따로 있다. 환경마다 다른 것이 값이 아니라
구조(이미지를 빌드하나 받나, GPU 예약, 포트 노출 범위)라서 파일을 나눴다.

세 스택은 project name이 달라 서로 독립적으로 올리고 내릴 수 있다.
network만 공유한다: `compose.main.dev.yml`이 `classroom-monitoring-dev-backend`를 만들고,
나머지 둘은 `external: true`로 참조한다. **따라서 메인 스택을 먼저 올려야 한다.**

문서가 "Prometheus / Grafana → MVP에서는 제외"라고 적은 것을 파일 분리로 구현했다.
메인 스택을 올려도 모니터링 스택은 뜨지 않는다.

## 실행

저장소 루트에서:

**`--env-file`을 주지 않는다.** 커밋되는 파일이라 실행에 필요한 값을 compose 안에 직접
적었다(결정 0018). 환경을 고르는 것은 **파일 이름**이다.

```bash
docker compose -f .docker/compose.main.dev.yml up -d        # 먼저 (network를 만든다)
docker compose -f .docker/compose.llm.dev.yml up -d
docker compose -f .docker/compose.monitoring.dev.yml up -d  # MVP에서는 생략

docker compose -f .docker/compose.main.dev.yml config       # 문법 검증
docker compose -f .docker/compose.llm.dev.yml down
```

내릴 때는 역순이다. 메인 스택을 먼저 내리면 network가 사라져 나머지 스택이 깨진다.

**`.local.yml`을 서버에서 실행하지 않는다.** 로컬 파일은 소스에서 이미지를 빌드하는데
서버에는 소스가 없다.

## 포트

`도커구조_공용서버.md`는 서비스마다 대외 포트를 따로 두는 그림이었다(FastAPI 8282,
n8n 5678, llama-server 8008). **그 방식을 쓰지 않는다** — 공용 서버라 호스트 포트를
점유할수록 다른 팀과 부딪히고, 방화벽도 그 포트들을 열어 주지 않았다.

구성은 **외부 --- 다른 팀 nginx --- 우리 서비스들**이다.
호스트 80·443은 그 nginx가 쓰고 있다.

**우리 쪽 reverse proxy(Caddy)는 두지 않는다.** 앞단에 nginx가 이미 있는데 같은 일을
두 겹으로 할 이유가 없어서다. 컨테이너가 하나 줄고, 경로가 안 맞을 때 들여다볼 곳도
한 군데로 준다. nginx가 나눠야 하는 것은 `/n8n/` 하나뿐이다 — 나머지는 전부 fastapi로
가고, 실시간 영상 시그널링도 fastapi가 중계한다(결정 0014).

포트를 정한 원칙은 셋이다.

1. **꼭 필요한 것만 연다.** 서비스끼리는 `backend` network에서 컨테이너 이름으로 부르므로
   (`minio:9000`, `fastapi:8001`) 호스트에 열 이유가 없다.
2. **여는 것도 기본은 `127.0.0.1`이다.** nginx만 닿으면 되는 것은 루프백에 묶는다.
   외부에 실제로 노출되는 것은 WebRTC 미디어 하나뿐이다.
3. **호스트 쪽 번호는 5자리로 준다.** 공용 서버라 4자리 기본값(5678·8888·8889 …)은
   다른 팀과 겹치기 쉽다. 컨테이너 안 번호는 기본값 그대로 둔다.
   fastapi의 `8076`만 예외 — 이미 nginx 연동 요청서로 알린 번호라 그대로 쓴다.

| 호스트 바인딩 | → 컨테이너 | 서비스 | 왜 필요한가 |
| --- | --- | --- | --- |
| **127.0.0.1:8076** | `fastapi:8001` | fastapi | 웹 화면·API. nginx `location /`이 넘긴다 |
| **127.0.0.1:15678** | `n8n:5678` | n8n | 편집기·webhook. nginx `location /n8n/`이 넘긴다 |
| **18189** (UDP·TCP) | `mediamtx:18189` | mediamtx | **WebRTC 미디어.** UDP 기반 ICE라 프록시할 수 없어 직통한다. **외부에 열리는 유일한 포트다** |

nginx 쪽에 넣을 설정과 방화벽 요청은
`individual_tasks/도커구성/nginx_연동_요청.md`에 정리해 두었다(저장소에 없다).
그 팀이 열어 줄 포트가 정해지면 `compose.main.dev.yml`의
`N8N_EDITOR_BASE_URL`·`WEBHOOK_URL`을 그 주소로 바꿔야 한다 — n8n이 자기 편집기와
webhook 주소를 그 값으로 만들기 때문이다.

`18189`는 **호스트와 컨테이너 번호가 같아야 한다.** MediaMTX가 `MTX_WEBRTCLOCAL*ADDRESS`의
번호를 ICE 후보로 브라우저에 알리므로, 매핑을 어긋나게 하면 브라우저가 닿지 못한다.
번호를 바꾸려면 `ports` 두 줄과 두 `MTX_*` 값을 함께 바꾼다.

나머지는 `ports`를 두지 않는다.

| 서비스 | 컨테이너 안 포트 | 사람이 보려면 |
| --- | --- | --- |
| mediamtx HLS | 8888 | **닫혀 있다.** 화면은 WebRTC로 본다. 필요해지면 nginx에 `/stream/`을 추가하고 `127.0.0.1:18888:8888`을 연다 |
| mediamtx RTSP | 8554 | **닫혀 있다.** 지금은 워커가 외부 카메라에서 당겨온다. 카메라가 서버로 직접 송출하는 방식이 되면 다시 열어야 한다 |
| minio | 9000 / 9001 | 콘솔은 SSH 터널 |
| llama-server | 8008 | 내부 호출 전용 |
| prometheus / grafana / loki / alloy | 9090 / 3000 / 3100 / 12345 | SSH 터널 |

운영자 도구를 볼 때는 해당 서비스의 `ports`를 임시로 되살리고 터널을 판다:

```bash
ssh -L 3000:localhost:3000 <서버>   # Grafana
ssh -L 9001:localhost:9001 <서버>   # MinIO 콘솔
```

inference worker는 원래 포트를 열지 않는다. 결과가 아직 로그로만 나간다.

## 환경변수

**두 계층을 구분한다** ([결정 0017](../docs/architecture/decisions.md)의 세 계층에서
첫 계층이 [결정 0018](../docs/architecture/decisions.md#0018--docker-compose-구성을-저장소에-커밋하고-localdev-파일을-나눈다)로
없어졌다. 규칙은 [환경변수 규칙](../docs/conventions/environment-convention.md)).

| 계층 | 파일 | 컨테이너에 들어가나 | 커밋 |
| --- | --- | --- | --- |
| 컨테이너 앱 설정 | `.docker/env/{fastapi,worker}.<환경>.env` | 예 (`env_file`) | 안 함 |
| 서드파티 자격증명 | `.docker/env/{minio,grafana,n8n}.<환경>.env` | 예 (`env_file`) | 안 함 |

**compose 치환용 `.docker/.env.<환경>`은 없앴다.** 그 파일은 `.env.*` 패턴에 걸려
커밋되지 않으므로, compose가 `${...}`에 의존하면 저장소에서 받은 파일만으로 실행할 수
없기 때문이다. 이제 그 값들은 compose 파일 안에 직접 적혀 있고, **환경을 고르는 것은
파일 이름**이다. `APP_ENV`도 각 compose가 `environment:`에 고정값으로 넣으므로
고른 환경과 컨테이너 안의 값이 어긋날 수 없다.

앱 설정 파일의 변수 목록 기준은 각각
[`webapps/fastapi/.env.example`](../webapps/fastapi/.env.example)과
[`worker/pipeline/.env.example`](../worker/pipeline/.env.example)이다. 여기 복사하지 않는다.
타임아웃·판정 임계값처럼 환경 무관한 값은 이미지 안의 `config/settings.yml`에 있다.

### 새 호스트에 올릴 때

```bash
# 값 파일은 저장소에 없다. 이 호스트에서 직접 만들고 권한을 막는다.
chmod 600 .docker/env/*.env
```

### 지금 이대로는 기동하지 않는 값

- **`.docker/models/`에 가중치 파일이 없다.** `MODEL_PATH=/models/yolo11m.pt`와
  `LLAMA_ARG_MODEL=/models/gemma.gguf`가 가리키는 파일을 호스트에 두어야 한다.
  읽기 전용 마운트라 ultralytics가 자동으로 내려받지 못한다. 의도한 것이다 —
  가중치는 이미지에도 저장소에도 넣지 않는다.
- **`compose.main.dev.yml`의 `MTX_WEBRTCADDITIONALHOSTS`가 서버에서 닿는 주소여야 한다.**
  아니면 실시간 영상이 브라우저에 뜨지 않는다. 위 WebRTC 절 참고.
- **MinIO root 키를 worker와 fastapi가 공용으로 쓴다.** Grafana admin 비밀번호와 함께
  **운영 전환 전에 재발급이 필요하다.**

## 이미지

**환경마다 이미지를 따로 유지한다**(결정 0018). dev는 GHCR에서 pull만 하고,
local은 소스에서 빌드해 `:local` 태그를 붙인다 — 로컬 빌드가 서버가 받는 태그를
덮어쓰지 않게 하려는 것이다.

| 서비스 | dev 이미지 (pull) | local 이미지 (build) |
| --- | --- | --- |
| fastapi | `ghcr.io/where-is-jo/classroom-monitoring-fastapi:develop` | `classroom-monitoring-fastapi:local` |
| inference worker | `ghcr.io/where-is-jo/classroom-monitoring-worker:latest` | `classroom-monitoring-worker:local` |
| deeplearning | `ghcr.io/where-is-jo/classroom-monitoring-deeplearning:latest` | `classroom-monitoring-deeplearning:local` |

**fastapi만 `:develop`을 본다.** CI가 develop 병합마다 `develop`·`sha-*`로 올리고
`latest`는 붙이지 않기 때문이다(결정 0014). `:latest`를 보면 병합해도 서버가 갱신되지
않는다 — 실제로 2026-08-12에 손으로 올린 이미지가 계속 돌아 그 뒤에 들어온 탐지
수신(`/internal/inference/events`)과 ROI 매핑이 서버에 없었다.

**worker와 deeplearning은 CI가 만들지 않아 `:latest`가 곧 최신이다.** 사람이 빌드해
push하므로, 코드를 고쳤으면 이미지도 다시 올려야 한다. 잊으면 fastapi와 달리
아무 신호 없이 옛 코드가 계속 돈다.

> GHCR org는 `whereisjo`가 아니라 `where-is-jo`(하이픈)다. 2026-08-12에 fastapi와
> worker를 `:latest`로 push한 기록이 있다.

서드파티 이미지는 두 환경이 같은 태그를 쓴다. 단 llama-server만 다르다 —
dev는 CUDA 판(`server-cuda-b10362`), local은 CPU 판(`server`)이다. GPU 없는 PC에서
CUDA 이미지는 컨테이너 시작 자체가 막히기 때문이다(아래 참고).

| 서비스 | 이미지 | 근거 |
| --- | --- | --- |
| llama-server | `ghcr.io/ggml-org/llama.cpp:server-cuda-b10362` | llama.cpp 공식 이미지. `server-cuda` 태그와 digest가 같은 것을 확인하고 빌드 번호로 고정했다 (`sha256:182a26fb…`) |
| 그 외 | 로컬 스택과 동일한 고정 태그 | 태그 선정 근거는 [README.md](./README.md) 레지스트리 정책 절 |

### `worker/Dockerfile`을 새로 만들었다

`worker/`에 Dockerfile이 없어 inference worker를 컨테이너로 만들 수 없었다.
**이 파일은 서비스 디렉터리 안이라 최상위 제약과 무관하고, 저장소에 커밋되는 파일이다**
(`webapps/fastapi/Dockerfile`과 같은 위치 규칙).

- 베이스: `python:3.12-slim`. **CUDA 베이스 이미지를 쓰지 않는다** — 아래 참고.
  Python 3.12인 이유는 저장소 코드가 `typing.Self`(3.11+)를 쓰기 때문이다.
- 빌드 단계와 실행 단계를 나누고(멀티스테이지) `/opt/venv`만 옮긴다.
- 실행 명령은 `python -m pipeline.main`. stream과 inference를 한 프로세스로 돌리는
  조립 진입점이다([`worker/pipeline/README.md`](../worker/pipeline/README.md)).

#### 이미지 크기: 14.9 GB → 10.6 GB

CUDA가 있는 PC에서 실제로 빌드하고 GPU 추론까지 돌려 확인한 결과다.

| 구성 | 크기 | 컨테이너 시작 | GPU 추론 |
| --- | --- | --- | --- |
| 이전(`nvidia/cuda:12.8.1-runtime-ubuntu24.04` 베이스) | 14.9 GB | **불가** | — |
| `python:3.12-slim` + 멀티스테이지, torch 기본 휠 | 9.13 GB | 가능 | 불가 |
| **현재**: 위 + torch를 cu126으로 고정, triton 제거 | **10.6 GB** | 가능 | **성공** |

**CUDA 베이스 이미지는 순수한 낭비였다.** torch 휠이 자기 CUDA 런타임을 통째로
번들해 온다(`site-packages/nvidia`, 3.6 GB). 베이스의 CUDA는 쓰이지 않으면서 이미지만
키웠고, 버전도 어긋나 있었다 — 베이스는 12.8인데 그 위에 깔린 torch는 CUDA 13 휠이었다.

크기보다 나빴던 것은 **컨테이너가 시작조차 못 하게 만든 것**이다. CUDA 베이스는
`NVIDIA_REQUIRE_CUDA=cuda>=12.8`을 이미지에 박아 넣는다. 드라이버 560.94(CUDA 12.6)인
호스트에서 `nvidia-container-cli: requirement error: unsatisfied condition: cuda>=12.8`로
기동이 막혔다. 코드가 요구한 제약이 아니라 베이스 이미지가 만든 제약이다.

**triton은 지웠다(688 MB).** `torch.compile`(inductor) 전용이라 추론 경로에 쓰이지 않고,
지운 상태로 GPU 추론이 되는 것을 확인했다.
`nccl`(377 MB)·`cusparselt`(432 MB)·`nvshmem`(195 MB)도 단일 GPU 추론에는 필요 없어
보이지만 **지우면 안 된다** — 셋 다 `import torch` 시점에 dlopen되어 `ImportError`로
죽는다. 하나씩 지워 보고 확인한 것이다.

#### torch의 CUDA 빌드는 `TORCH_INDEX_URL` 빌드 인자로 정한다

**venv나 이미지가 덮어 주지 못하는 경계가 여기다.** 이미지가 고정하는 것은 Python
패키지와 torch가 번들한 CUDA 런타임까지고, **호스트의 NVIDIA 커널 드라이버 버전과
GPU 아키텍처(sm_XX)는 호스트 사실이라 그대로 새어 들어온다.**

기본값은 `https://download.pytorch.org/whl/cu126`이다. 드라이버 525 이상,
Pascal~Hopper(`sm_50`~`sm_90`)를 커버한다. PyPI 기본 휠(CUDA 13)로 두면
드라이버 580 이상에 `sm_75` 이상만 되어 범위가 좁다.

**서버 GPU가 Blackwell(`sm_100`/`sm_120`)이면 기본값으로는 동작하지 않는다.**
그때는 인덱스를 바꿔 빌드한다:

```bash
docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128 worker
```

서버의 `nvidia-smi` 출력(GPU 모델·드라이버 버전)을 확인한 뒤 확정한다.

## 탐지 스냅샷 — 영상 원본은 저장하지 않는다

가용 용량이 약 48 GB인데 1080p 카메라 한 대가 시간당 약 0.9 GB라 상시 녹화가
성립하지 않는다. 그래서 [결정 0011](../docs/architecture/decisions.md#0011--영상-원본을-저장하지-않고-스냅샷만-남긴다)로
**영상 원본 대신 탐지 시점의 정지 이미지만** 남긴다. `worker/recorder`는 이 스택에서
실행하지 않는다.

| 항목 | 값 | 어디에 |
| --- | --- | --- |
| 해상도 / 품질 | 720p / JPEG 80 | 이미지 안의 `inference/config/settings.yml` |
| 카메라당 최소 적재 간격 | 60초 | 이미지 안의 `inference/config/settings.yml` |
| 적재 켜기 | `SNAPSHOT_ENABLED=true` | `.docker/env/worker.dev.env` (yml 기본값은 false) |
| 보존 기간 | 30일 | `compose.main.dev.yml`의 `SNAPSHOT_RETENTION_DAYS` |
| 버킷 | `classroom-snapshots` | 두 곳이 같은 값이어야 한다(아래) |

최악의 경우(간격 캡이 계속 걸릴 때) 카메라 3대 × 12시간 기준 하루 약 259 MB,
30일 약 7.8 GB다.

**버킷 이름이 두 곳에 있다.** `compose.main.dev.yml`의 `SNAPSHOT_BUCKET`(minio-init이 만들
버킷)과 이미지 안의 `config/settings.yml`(worker의 `object_storage_bucket`, fastapi의
`snapshot_storage_bucket`). 갈리면 워커는 올리는데 화면에는 안 보인다. 이전에는 세
곳이었는데, 앱 쪽 두 값이 커밋되는 yml로 옮겨가 한 곳으로 줄었다 — 버킷 이름을 바꾸려면
yml을 고치고 이미지를 다시 빌드해야 한다.

**삭제는 MinIO가 한다.** `minio-init` 서비스가 기동 시 버킷과 lifecycle 만료 규칙을
한 번 만든다(`restart: "no"`). 앱이 지우는 방식과 달리 워커가 죽어 있어도 삭제가 계속된다.

**컨테이너 로그 회전을 걸었다**(`max-size: 10m`, `max-file: 3`). Docker `json-file`
기본값이 무제한이라 48 GB 환경에서 조용히 쌓인다. worker가 프레임 샘플마다 로그를 남긴다.

## 경로 라우팅은 nginx가 한다

**우리 쪽 reverse proxy(Caddy)를 두지 않기로 했다.** 앞단에 다른 팀 nginx가 이미 있고,
같은 경로 분기를 두 겹으로 할 이유가 없다. `caddy/Caddyfile*`은 남아 있지만 어느
compose도 마운트하지 않는다.

그래서 **경로 분기는 nginx가 해야 한다.** 넣어 달라고 요청한 설정은
`individual_tasks/도커구성/nginx_연동_요청.md`에 그대로 있다(저장소에 없다).

| 경로 | 대상 | 비고 |
| --- | --- | --- |
| `/` (나머지 전부) | `127.0.0.1:8076` → `fastapi:8001` | 실질적인 기능은 여기 안에서 쓴다 |
| `/n8n/*` | `127.0.0.1:15678` → `n8n:5678` | 접두사를 떼지 않고 그대로 넘긴다 |

HLS(`/stream/*`)는 요청서에서 뺐다. 화면이 WebRTC로만 보기 때문이다.
필요해지면 `location /stream/`과 `127.0.0.1:18888:8888`을 함께 되살린다.

HTTPS는 nginx가 끝낸다. 도메인이 정해져도 우리 쪽은 평문 HTTP 그대로 두면 되고,
`N8N_EDITOR_BASE_URL`·`N8N_WEBHOOK_URL`만 그 주소로 바꾼다.

### 경로로 나눌 때 걸리는 것

Caddy로 붙여 보며 확인한 것들이다. **nginx로 넘어가면서 같은 문제를 nginx 문법으로
다시 풀어야 한다** — 요청서의 `location` 블록에 그렇게 적어 두었다.

- **n8n은 접두사를 알아야 한다.** 그냥 프록시만 하면 편집기가 자기 자산을 `/assets/…`로
  찾아서 fastapi로 새어 나간다. `N8N_PATH=/n8n/`을 주면 n8n이 HTML의 자산 경로를
  전부 `/n8n/…`로 만든다. `compose.main.dev.yml`의 `n8n.environment`에 있다.
  **로컬(`compose.main.local.yml`)은 nginx가 없어 `N8N_PATH=/`다** — 접두사 없이
  포트로 직접 붙는다.
- **MediaMTX가 돌려주는 `Location`에 접두사가 없었다.** Caddy·nginx로 직접 프록시할
  때 겪던 문제인데, **지금은 fastapi가 시그널링을 중계하면서 자기 경로로 다시 써 주므로
  앞단에서 손댈 것이 없다**(결정 0014).

## 사용자용 실시간 영상은 WebRTC로 간다

지연이 작은 쪽을 택했다. HLS는 화면이 쓰지 않아 포트를 닫아 두었다.

**WebRTC는 reverse proxy만으로 끝나지 않는다.** HTTP로 오가는 것은 시그널링(WHEP)뿐이고,
영상은 ICE로 뚫은 별도 포트로 직접 흐른다. 그래서 둘을 나눠서 구성했다.

| 무엇 | 어디로 | 프록시 |
| --- | --- | --- |
| WHEP 시그널링 | 브라우저 → fastapi → `mediamtx:8889` | **fastapi가 중계한다** |
| 영상(미디어) | `18189` UDP·TCP 직접 | **안 탄다. 포트를 열어야 한다** |

### 시그널링은 fastapi가 중계한다 (결정 0014)

브라우저는 **MediaMTX 주소·포트를 모른다.** 재생 세션을 만들면
(`POST /api/v1/video-streams/<id>/playback-sessions`) fastapi가 자기 경로를
`signaling_url`로 돌려주고, 화면은 그 주소로만 WHEP offer를 보낸다.

| 설정 | 값 | 이유 |
| --- | --- | --- |
| `WHEP_BASE_URL` | `http://mediamtx:8889` | fastapi가 중계할 대상. 같은 network라 컨테이너 이름으로 부른다 |
| `PLAYBACK_SESSION_COOKIE_SECURE` | dev `true` / local `false` | **로컬은 평문 http라 false여야** 브라우저가 세션 cookie를 보낸다 |

덕분에 **MediaMTX 시그널링 포트를 호스트에 열지 않는다.** 앞단 proxy가 경로를
나눠 줄 필요도 없어서 **로컬에서도 서버와 같은 경로로 실시간 영상을 확인할 수 있다.**

MediaMTX 설정은 커스텀 `mediamtx.yml` 대신 `MTX_<파라미터명 대문자>` 환경변수로 준다.
바꾼 값이 compose 파일 안에 다 보이고 마운트할 파일이 늘지 않는다.

| 환경변수 | 값 | 이유 |
| --- | --- | --- |
| `MTX_WEBRTCADDITIONALHOSTS` | `compose.main.dev.yml`에 직접 적혀 있다 | ICE 후보로 알릴 주소 |
| `MTX_WEBRTCIPSFROMINTERFACES` | `no` | 컨테이너 사설 IP가 후보로 새는 것을 막는다 |
| `MTX_WEBRTCLOCALUDPADDRESS` | `:18189` | 미디어. 기본값 8189가 아니라 5자리로 옮겼다 |
| `MTX_WEBRTCLOCALTCPADDRESS` | `:18189` | UDP가 막힌 망을 위한 폴백. 기본은 꺼져 있다 |

**`MEDIAMTX_WEBRTC_HOSTS`를 서버 공인 주소로 반드시 바꿔야 한다.** 지금 값은
로컬 검증용 `localhost`다. 인터페이스에서 주소를 긁는 동작을 껐기 때문에 이 값이
유일한 ICE 후보가 된다 — 틀리면 화면이 안 나온다. 증상이 "연결은 되는데 영상이 없음"이라
원인을 찾기 어려우니 서버에 올릴 때 먼저 확인한다.

세션 주소 재작성도 필요하다. WHEP은 201과 함께 `Location`으로 세션 리소스 주소를 주고,
클라이언트가 거기로 ICE 후보를 PATCH하고 끝날 때 DELETE 한다. 접두사가 빠지면 그
접두사가 빠지면 세션이 정리되지 않는다. **fastapi가 중계하면서 자기 경로로 다시 써
주므로 앞단에서 손댈 것이 없다**(결정 0014).

## 검증한 것 / 못 한 것

**이 머신에 NVIDIA GPU가 없다**(`nvidia-smi` 없음). GPU가 실제로 붙는지는 공용 서버에서만
확인할 수 있다. 그 외는 GPU 없이 돌 수 있는 데까지 실제로 띄워서 확인했다.

**아래 라우팅 검증은 Caddy가 있던 시점의 기록이다.** Caddy를 빼고 경로 분기를 nginx로
넘긴 뒤로는 **다시 확인하지 않았다.** 검증 자체(무엇이 통했고 무엇이 걸렸는지)는 그대로
유효하지만, 지금 그 일을 하는 주체는 nginx이고 그쪽 설정은 아직 들어가지 않았다.
포트 번호도 5자리로 옮겨서(`8189` → `18189`) 아래 로그의 번호와 다르다.

확인한 것:

- 세 compose 파일 모두 `docker compose config` 통과. GPU reservation·포트·마운트 경로가
  의도대로 해석되는 것까지 확인.
- **메인 스택의 GPU 없는 5개(caddy/fastapi/n8n/mediamtx/minio)를 실제로 기동했다.**
  fastapi·minio는 `healthy`.
- `caddy validate`로 `Caddyfile.server` 문법 확인 → `Valid configuration`.
- **Caddy 경로 라우팅 3개 전부 실제 응답으로 확인:**
  - `/health` → 200 `{"status":"ok"}`, `/health/ready` → 200 (fastapi)
  - `/n8n/` → 200. HTML의 자산 경로가 전부 `/n8n/…`로 나오고 그 자산이 실제로 200으로
    받아지는 것까지 확인 (`N8N_PATH`가 먹는다는 뜻)
  - `/stream/…` → **합성 RTSP 스트림을 실제로 넣고 HLS를 끝까지 받아 봤다.**
    ffmpeg로 `rtsp://mediamtx:8554/camera-01`에 송출한 뒤, Caddy를 통해
    마스터 플레이리스트(200, `application/vnd.apple.mpegurl`) →
    variant 플레이리스트(200) → `init.mp4`(200, 683 B) →
    세그먼트(200, `video/mp4`, 약 320 KB)까지 이어서 받았다.
  - `/webrtc/…` → **같은 스트림에 WHEP 요청을 실제로 보냈다.** SDP offer를 POST해
    201 Created + `application/sdp` 응답을 받았고, 응답 SDP의 ICE 후보가
    설정한 호스트만(UDP·TCP 각각) 나오는 것을 확인했다 — 사설 IP 후보 0개.
    `Location`이 `/webrtc/…`로 재작성되는 것과, 그 주소로 보낸 세션 DELETE가
    200으로 처리되는 것까지 확인했다.
- MediaMTX 기동 로그에서 `MTX_*` 환경변수가 먹는 것을 확인:
  `[WebRTC] started with listeners on :8889 (TCP/HTTP), :8189 (UDP/ICE), :8189 (TCP/ICE)`.
  TCP/ICE는 기본이 꺼져 있으므로 이 줄 자체가 설정이 적용됐다는 증거다.
- `worker/Dockerfile`로 **이미지 빌드 성공**
  (`ghcr.io/whereisjo/classroom-monitoring-worker:local`).
  이미지 안의 ffmpeg는 위 HLS 검증에서 송출기로 그대로 썼다.
- **CUDA가 있는 PC(GTX 1060 3GB, 드라이버 560.94)에서 GPU 동작까지 확인했다.**
  `torch.cuda.is_available()` True, `get_device_name(0)`이 GPU를 잡고,
  **YOLOv8n을 `device='cuda'`로 실제 추론**(VRAM peak 27 MB)까지 돌렸다.
  같은 이미지에 `--gpus all`만 주면 `python -m pipeline.main`이 실제 설정 검증까지
  도달해 `STREAM_SOURCES: Field required`로 종료 코드 1을 내는 것도 확인했다.
  같은 PC에서 이전 CUDA 베이스 이미지는 **컨테이너 시작 자체가 막혔다.**
- llama.cpp 이미지의 ENTRYPOINT가 `/app/llama-server`이고 `LLAMA_ARG_HOST=0.0.0.0`이
  이미 들어 있는 것을 이미지 config에서 확인. 그래서 `command` 대신 `LLAMA_ARG_*`
  환경변수로 인자를 넘긴다.
- `server-cuda`와 `server-cuda-b10362`의 digest가 같은 것을 registry API로 대조.

확인하지 못한 것:

- **llama-server는 아직 기동하지 못했다.** Gemma GGUF 가중치가 없다.
- **compose의 GPU 예약이 실제로 GPU를 잡는지.** worker 이미지의 GPU 동작은
  `docker run --gpus all`로 확인했지만, compose의
  `deploy.resources.reservations.devices` 경로로는 아직 확인하지 않았다.
- **실제 카메라.** 아래 검증은 합성 RTSP 스트림으로 했다.

**스냅샷 전 구간은 실제로 확인했다**(CUDA PC, GTX 1060 3GB):

- `minio-init`이 `classroom-snapshots` 버킷과 30일 만료 규칙을 만드는 것(종료 코드 0)
- 사람이 담긴 이미지를 RTSP로 송출 → `INFERENCE_DEVICE=cuda`로 YOLOv8n이 4명 탐지
  (14건 처리, 0 실패) → `camera-01/2026-08-12/20260812T031825Z.jpg` 적재
- 정적 화면이라 탐지 수가 계속 같아 **한 장만** 올라간 것까지 — 트리거가 의도대로 동작
- fastapi `/api/v1/snapshots`가 키에서 카메라·시각을 해석해 돌려주고,
  이미지 프록시가 200 `image/jpeg`로 200,447바이트를 그대로 전달(JPEG 마커 확인)
- **MinIO를 내렸을 때** API는 503 `SNAPSHOT_STORAGE_UNAVAILABLE`, 화면은 "조회하지
  못했습니다"로 빈 상태와 구분되는 것
- n8n 편집기의 실제 조작과 워크플로 저장. HTTP 응답과 자산 로딩까지만 봤다.
- **Caddy를 뺀 뒤의 기동.** 경로 분기를 nginx로 넘기고 호스트 포트를 5자리로 옮긴
  구성은 아직 띄워 보지 않았다. 서버에서 이 순서로 확인한다:

  ```bash
  docker compose -f .docker/compose.main.dev.yml up -d
  curl -i http://127.0.0.1:8076/health          # 우리 쪽 (nginx 없이)
  curl -i http://127.0.0.1:15678/n8n/           # n8n
  curl -i http://116.42.115.24:<nginx 포트>/health   # 전체 경로 (nginx 설정 후)
  ```

- **nginx 쪽 설정.** 요청서를 고쳐 두었을 뿐 그 팀이 적용했는지는 모른다.
  `location` 셋이 들어가기 전에는 `/n8n/`과 실시간 영상이 동작하지 않는다.
- HTTPS·도메인 경로. 계속 평문 HTTP로만 확인했다. TLS는 nginx가 끝내는 구성이다.
- **WebRTC 미디어가 실제로 흐르는 것.** 시그널링(WHEP)과 ICE 후보까지만 봤다.
  DTLS 핸드셰이크부터 영상 재생까지는 브라우저나 WebRTC 클라이언트가 필요하다.
  서버에 올린 뒤 브라우저로 확인한다.
- Prometheus 스크랩 대상. 여전히 자기 자신뿐이다 — 어떤 서비스도 `/metrics`를
  노출하지 않는다(`monitoring/internal/README.md` "구현 전").

## 정하지 않고 남긴 것

- **스냅샷 버킷의 접근 권한.** 지금은 worker와 fastapi가 모두 MinIO root 키로 붙는다.
  쓰기(worker)와 읽기(fastapi)를 나눈 전용 키가 필요하다.
- **어떤 GGUF를 `gemma.gguf`로 둘 것인가.** 파일명은 `compose.llm.dev.yml`에
  고정했고 바꾸지 않는다 — 모델을 바꿀 때마다 compose를 고치면 서버와 저장소가
  어긋난다. **받은 가중치를 `models/gemma.gguf`로 이름을 바꿔 두고, 무엇을 두었는지
  아래 표에 적는다.**

  | 날짜 | 모델 | 양자화 | 크기 |
  | --- | --- | --- | --- |
  | (미기재) | | | |

  아직 아무것도 올리지 않았다. 이 표가 비어 있으면 llama-server는 기동에 실패한다.
- **GPU 분배.** inference worker와 llama-server 둘 다 `device_ids: ["1"]`이다.
  계정에 할당된 GPU가 그것뿐이라 나눌 수 없다. **GPU가 1장뿐인 다른 PC에서는 이
  설정으로 기동에 실패한다** — 그 환경을 쓰려면 별도 파일이 필요하다.
- **비밀값 관리.** `env/*.env`는 지금 로컬 파일이다. 공용 서버에서 어떻게 주입할지
  정해지지 않았다.
- **영상 스트림의 접근 제어.** 재생 세션 API는 인증 없이 열려 있다.
  강의실 영상에는 사람 얼굴이 담기므로 fastapi의 인증과 어떻게 잇을지 정해야 한다
  (MediaMTX 자체 인증을 쓸지, fastapi가 발급한 토큰을 검사할지).
  `monitoring/external`의 경계 문제와 함께 다룬다.
