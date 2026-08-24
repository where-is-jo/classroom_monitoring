# 공용 GPU 서버 docker compose

`individual_tasks/도커구성/도커구조_공용서버.md`의 컨테이너 구분·모식도를 옮긴 뒤
실제로 돌려 보며 고친 compose 구성이다.
**이 문서는 dev(공용 GPU 서버) 전용이다.** 로컬 스택은 [README.md](./README.md)를 따른다.

> 이 문서가 참조하는 `individual_tasks/` 아래 자료(도커 구조 설계, nginx 연동 요청서)는
> 개인 작업 자료라 `.gitignore` 대상이고 **저장소에 없다.** 필요하면 작성자에게 받는다.

`.docker/`는 커밋된다([결정 0018](../docs/architecture/decisions.md#0018--docker-compose-구성을-저장소에-커밋하고-localdev-파일을-나눈다)).
단 `env/`(비밀값)와 `models/`(가중치)는 제외이므로 **서버에는 그 둘을 따로 올려야 한다.**

**커밋되는 파일은 CI가 이 서버에 반영한다**([결정 0038](../docs/architecture/decisions.md#0038--gpu-서버의-compose-설정을-github-actions가-ssh로-반영한다)).
`.docker/`를 고쳐 `develop`에 병합하면 `.github/workflows/deploy-gpu-server.yml`이 파일을
옮기고, **그때 떠 있던 스택만** `up -d`로 다시 올린다. 내려 둔 스택은 켜지 않는다.
**서버가 꺼져 있으면 배포는 실패가 아니라 건너뛴다** — 서버를 켠 뒤 Actions 탭에서
`Run workflow`로 다시 돌린다. `env/`와 `models/`는 전송 대상이 아니라 여전히 사람이 올린다.

반영 대상은 compose 파일만이 아니다. **`.docker/` 아래 커밋되는 모든 파일**이며
(`prometheus/`·`alloy/`·`minio/` 포함) 문서(`.md`)만 제외된다.

이 서버의 배포 루트는 **홈 디렉터리 자체**다. 저장소 변수 `GPU_SERVER_DEPLOY_PATH`에
`/home/doyoon`이 들어 있고, 파일은 `~/.docker/`에 놓인다. 배포 직전 백업은
`~/.deploy-backups/<타임스탬프>.tar.gz`로 남고 최근 10개만 유지한다(`env/`·`models/` 제외).

**`~/.docker/`는 Docker CLI의 설정 디렉터리이기도 하다.** `buildx/`와 `config.json`이
같은 곳에 있다. 배포가 `rsync --delete`를 쓰지 않는 이유가 여기서 한 번 더 중요해진다 —
저장소에 없는 파일은 건드리지 않으므로 CLI 설정은 그대로 남는다. 이 경로에서
`--delete`를 켜는 변경은 하지 않는다.

## 파일

| 파일 | project name | 담는 것 |
| --- | --- | --- |
| `compose.main.dev.gpu.yml` | `classroom-monitoring-dev` | inference worker, deeplearning, MediaMTX, MinIO |
| `compose.llm.dev.yml` | `classroom-monitoring-dev-llm` | llama-server (Gemma GGUF) |
| `compose.monitoring.dev.yml` | `classroom-monitoring-dev-observability` | Prometheus, Grafana, Loki, Alloy |
| `alloy/config.dev.alloy` | — | 서버용 로그 수집 설정 |
| `prometheus/prometheus.dev.yml` | — | 서버용 수집 대상 |
| `env/<서비스>.dev.env` | — | `env_file`로 컨테이너에 주입하는 값. **커밋되지 않는다** |
| `models/` | — | 모델 가중치. **커밋되지 않는다** |

**FastAPI와 n8n은 이 서버에 없다.**
[결정 0026](../docs/architecture/decisions.md#0026--백엔드를-개인-pc에-두고-gpu가-필요한-것만-gpu-서버에-남긴다)으로
개인 PC(노트북, `100.119.241.93`)로 옮겼고, 그 파일은 `compose.main.dev.pc.yml`이다.
그래서 `compose.main.dev.yml`이 `.gpu`와 `.pc` 둘로 갈렸다. 이 서버에 남은 것은 GPU가
필요한 셋과, 그 셋이 쓰는 영상·저장소다.

**`.local.yml` 짝은 더 이상 없다.** 개발자 PC용 local 스택을 없앴고, 로컬에서 소스를
고쳐 가며 볼 때는 컨테이너를 거치지 않고 직접 띄운다
([결정 0034](../docs/architecture/decisions.md#0034--local-compose-스택을-없애고-로컬-실행은-소스-직접-구동으로-한정한다)).

세 스택은 project name이 달라 서로 독립적으로 올리고 내릴 수 있다.
network만 공유한다: `compose.main.dev.gpu.yml`이 `classroom-monitoring-dev-backend`를
만들고, 나머지 둘은 `external: true`로 참조한다. **따라서 메인 스택을 먼저 올려야 한다.**

**project name과 network 이름은 분할 전 그대로 뒀다.** 바꾸면 `minio-data` 볼륨 이름이
달라져 쌓인 스냅샷을 잃는다. 대신 `n8n-data`는 개인 PC로 따라가지 않는다 — 볼륨은
기계에 묶여 있어서, 워크플로는 편집기에서 export/import로 옮긴다.

문서가 "Prometheus / Grafana → MVP에서는 제외"라고 적은 것을 파일 분리로 구현했다.
메인 스택을 올려도 모니터링 스택은 뜨지 않는다.

## 실행

저장소 루트에서:

**`--env-file`을 주지 않는다.** 커밋되는 파일이라 실행에 필요한 값을 compose 안에 직접
적었다(결정 0018). 환경을 고르는 것은 **파일 이름**이다.

```bash
docker compose -f .docker/compose.main.dev.gpu.yml up -d    # 먼저 (network를 만든다)
docker compose -f .docker/compose.llm.dev.yml up -d
docker compose -f .docker/compose.monitoring.dev.yml up -d  # MVP에서는 생략

docker compose -f .docker/compose.main.dev.gpu.yml config   # 문법 검증
docker compose -f .docker/compose.llm.dev.yml down
```

내릴 때는 역순이다. 메인 스택을 먼저 내리면 network가 사라져 나머지 스택이 깨진다.

**`.pc.yml`을 여기서 실행하지 않는다** — 개인 PC 쪽 절반이다.

**먼저 `tailscale status`로 tailnet 연결을 확인한다.** 이 스택은 포트 넷을(모니터링
스택까지 세면 Grafana를 더해 다섯) Tailscale 주소(`100.85.0.72`)에 bind하므로,
인터페이스가 없으면 기동에 실패한다. 조용히 뜨는
것보다 낫다 — 떠 있어도 개인 PC가 닿지 못한다.

## 포트

`도커구조_공용서버.md`는 서비스마다 대외 포트를 따로 두는 그림이었다(FastAPI 8282,
n8n 5678, llama-server 8008). **그 방식을 쓰지 않는다** — 공용 서버라 호스트 포트를
점유할수록 다른 팀과 부딪히고, 방화벽도 그 포트들을 열어 주지 않았다.

**다른 팀 nginx 뒤에 들어가는 구성도 더 이상 쓰지 않는다.** 브라우저가 붙던 둘
(fastapi·n8n)이 결정 0026으로 개인 PC로 갔고, 인터넷에 공개하지 않기로 하면서 도메인도
경로 분기도 필요가 없어졌다. `individual_tasks/도커구성/nginx_연동_요청.md`로 보낸
요청은 **철회 대상이다.**

대신 열어야 할 것이 새로 생겼다. **개인 PC의 fastapi가 이 서버의 넷을 부른다.**
전에는 같은 `backend` network 안이라 컨테이너 이름으로 불렀지만 이제 다른 기계다.

포트를 정한 원칙은 넷이다.

1. **꼭 필요한 것만 연다.** 이 서버 안의 서비스끼리는 `backend` network에서 컨테이너
   이름으로 부르므로(`minio:9000`) 호스트에 열 이유가 없다.
2. **개인 PC가 부르는 것은 Tailscale 주소(`100.85.0.72`)에만 묶는다.**
   **`0.0.0.0`에 열면 안 된다** — 이 서버는 공인 IP(`116.42.115.24`)를 가진 공용
   장비라서 그것은 곧 인터넷 공개다. MinIO는 root 키 하나로 스냅샷 전체(학생 얼굴이
   담긴다)가 열리고, llama-server에는 인증이 아예 없다.
3. **`0.0.0.0`은 하나뿐이다.** WebRTC 미디어(`18189`)는 브라우저가 프록시 없이 직접
   붙어야 해서 다른 방법이 없다.
4. **호스트 쪽 번호는 5자리로 준다.** 공용 서버라 4자리 기본값(5678·8888·8889 …)은
   다른 팀과 겹치기 쉽다. 컨테이너 안 번호는 기본값 그대로 둔다.

| 호스트 바인딩 | → 컨테이너 | 누가 부르나 |
| --- | --- | --- |
| **100.85.0.72:18100** | `deeplearning:8100` | 개인 PC의 fastapi — 얼굴 분석 |
| **100.85.0.72:19000** | `minio:9000` | 개인 PC의 fastapi — 스냅샷 읽기 |
| **100.85.0.72:18889** | `mediamtx:8889` | 개인 PC의 fastapi — WHEP 시그널링 중계 |
| **100.85.0.72:18008** | `llama-server:8008` | 개인 PC의 fastapi — 검색 계획 |
| **100.85.0.72:13000** | `grafana:3000` | **팀원의 브라우저** — 관측 대시보드. 서비스가 아니라 사람이 부르는 유일한 포트다 |
| **18189** (UDP·TCP) | `mediamtx:18189` | 브라우저. **WebRTC 미디어.** UDP 기반 ICE라 프록시할 수 없어 직통한다. **외부에 열리는 유일한 포트다** |

**Tailscale이 내려가 있으면 기동에 실패한다.** 없는 주소에는 bind할 수 없다.

이 서버에서 개인 PC로 나가는 방향도 둘 있다. 그쪽은 `100.119.241.93:8076`이다.

| 어디서 | 무엇 | 설정 위치 |
| --- | --- | --- |
| `inference-worker` | 탐지 이벤트 전송 | `env/worker.dev.env`의 `FASTAPI_URL` |
| `prometheus` | 지표 스크랩 | `prometheus/prometheus.dev.yml`의 fastapi target |

**개인 PC는 노트북이라 꺼져 있을 수 있다.** 그때 worker의 탐지 이벤트는 제한 재시도
뒤 버려지고 Prometheus의 fastapi target은 `up=0`이 된다. 둘 다 장애가 아니라 정상
상태이며, 버퍼링·알림 정책은 아직 정해지지 않았다(결정 0026의 남은 일).

`18189`는 **호스트와 컨테이너 번호가 같아야 한다.** MediaMTX가 `MTX_WEBRTCLOCAL*ADDRESS`의
번호를 ICE 후보로 브라우저에 알리므로, 매핑을 어긋나게 하면 브라우저가 닿지 못한다.
번호를 바꾸려면 `ports` 두 줄과 두 `MTX_*` 값을 함께 바꾼다.

나머지는 `ports`를 두지 않는다.

| 서비스 | 컨테이너 안 포트 | 사람이 보려면 |
| --- | --- | --- |
| mediamtx HLS | 8888 | **닫혀 있다.** 화면은 WebRTC로 본다 |
| mediamtx RTSP | 8554 | **닫혀 있다.** 지금은 워커가 외부 카메라에서 당겨온다. 카메라가 서버로 직접 송출하는 방식이 되면 다시 열어야 한다 |
| minio | 9000 / 9001 | S3 API는 Tailscale 주소에 열려 있다(`100.85.0.72:19000`). 콘솔(9001)은 SSH 터널 |
| llama-server | 8008 | **Tailscale 주소에 열려 있다**(`100.85.0.72:18008`). 개인 PC의 fastapi가 부른다 |
| grafana | 3000 | **Tailscale 주소에 열려 있다**(`http://100.85.0.72:13000`). 팀원이 브라우저로 바로 본다 |
| prometheus / loki / alloy | 9090 / 3100 / 12345 | SSH 터널. Grafana가 backend network로 대신 조회하므로 평소에는 볼 일이 없다 |

Grafana 말고 다른 운영자 도구를 볼 때는 해당 서비스의 `ports`를 임시로 되살리고
터널을 판다:

```bash
ssh -L 9090:localhost:9090 <서버>   # Prometheus
ssh -L 9001:localhost:9001 <서버>   # MinIO 콘솔
```

inference worker는 원래 포트를 열지 않는다. 결과가 아직 로그로만 나간다.

## CCTV에는 subnet router를 거쳐 닿는다

**worker가 강의실 CCTV를 직접 당긴다.** RTSP는 push가 아니라 pull이라, 카메라가 서버로
쏘는 것이 아니라 worker가 카메라에 TCP로 접속해서 가져온다. **그래서 필요한 방향은
GPU 서버 → CCTV다.**

문제는 CCTV(`192.168.0.63`)가 개인 PC와 같은 사설망에 있는 임베디드 장치라
**Tailscale 클라이언트를 설치할 수 없다는 것**이다. 100.x 주소를 받지 못하므로 tailnet
안에서 이름을 가질 수 없다.

**입구 카메라용 라즈베리파이가 그 망에서 subnet router 역할을 한다.** 파이는 Linux라
Tailscale에 직접 붙고, 어차피 입구 카메라로 상주해야 하는 장비다.

```text
GPU 서버 ──Tailscale──▶ 라즈베리파이 ──192.168.0.x──▶ CCTV(192.168.0.63:80)
```

```bash
# 라즈베리파이에서
sudo tailscale up --advertise-routes=192.168.0.63/32
# GPU 서버에서
sudo tailscale up --accept-routes
```

그 뒤 **Tailscale 관리 콘솔에서 라우트를 승인해야 실제로 열린다.** 기본은 대기 상태다.

**`192.168.0.0/24`가 아니라 `/32`로 CCTV 한 대만 광고한다.** 대역을 통째로 열면 GPU
서버가 그 사설망의 모든 기기에 접근할 수 있게 된다. 공용 서버라 더 그렇다.

**개인 PC를 subnet router로 쓰지 않는다.** 노트북이 꺼지면 GPU 서버가 CCTV를 못 봐
탐지 자체가 멈춘다 — 결정 0026이 적어 둔 "개인 PC가 꺼지면 탐지 이벤트가 갈 곳을
잃는다"보다 무거운 실패다. 파이는 상시 가동이다.

**GPU 서버 자신의 LAN이 `192.168.0.x`를 쓴다면 확인이 필요하다.** `/32`는 그 서버의
`/24` local route보다 더 구체적이라 우선하고, 같은 주소를 쓰는 그쪽 기기가 있으면
가로챈다. `ip route get 192.168.0.63`으로 어디로 나가는지 본다.

**fastapi는 이 경로를 쓰지 않는다.** 개인 PC가 CCTV와 같은 망이라 ROI 기준 화면 캡처
(결정 0031)는 그냥 닿는다. subnet router가 필요한 것은 GPU 서버뿐이다.

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

### 이 서버에 두는 값 파일은 넷뿐이다

**`fastapi.dev.env`와 `n8n.dev.env`를 여기 두지 않는다.** 두 서비스가 개인 PC로
갔으므로(결정 0026) 이 서버에서는 읽히지 않는데, `fastapi.dev.env`에는 MongoDB Atlas
접속 정보가 들어 있다. **쓰이지도 않으면서 공용 장비에 자격 증명만 남는다.**

| 파일 | 읽는 곳 |
| --- | --- |
| `deeplearning.dev.env` | `compose.main.dev.gpu.yml`의 deeplearning. MongoDB 갤러리 읽기 전용 접속 정보 |
| `worker.dev.env` | `compose.main.dev.gpu.yml`의 inference-worker |
| `minio.dev.env` | `compose.main.dev.gpu.yml`의 minio·minio-init |
| `grafana.dev.env` | `compose.monitoring.dev.yml`의 grafana |

`fastapi.dev.env`와 `n8n.dev.env`는 **개인 PC 쪽 `.docker/env/`에만** 둔다.

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
- **`compose.main.dev.gpu.yml`의 `MTX_WEBRTCADDITIONALHOSTS`가 서버에서 닿는 주소여야 한다.**
  아니면 실시간 영상이 브라우저에 뜨지 않는다. 위 WebRTC 절 참고.
- **MinIO root 키를 worker와 fastapi가 공용으로 쓴다.** Grafana admin 비밀번호와 함께
  **운영 전환 전에 재발급이 필요하다.**

## 얼굴 식별 → 객체 추적 인계 배포

이 경로에는 서로 다른 두 종류의 데이터가 필요하다.

- MongoDB `face_embeddings`: 얼굴 등록 때 FastAPI가 저장한 학생별 대표 embedding이다.
- `thresholds.json`: 별도의 known/unknown validation으로 허용 오인식률에 맞춰 고른
  similarity·margin 기준이다. **등록 embedding을 JSON으로 덤프한 파일이 아니다.**

### 1. 평가 실행 호스트에서 thresholds.json 생성

로컬 또는 GPU 서버 중 모델 파일과 held-out 평가 이미지 네 묶음이 있는 한 곳에서만
실행하면 된다. `deeplearning/training/.env`에는 실행 호스트에 실제로 존재하는 절대경로를
쓴다. 로컬 Windows에서 `/home/...` 경로를 쓰면 안 된다.

```dotenv
FACE_EVAL_GALLERY_SOURCE=mongodb
MONGODB_URI=<등록 embedding MongoDB URI>
MONGODB_DATABASE=<DB 이름>
FACE_EMBEDDING_COLLECTION=face_embeddings
FACE_EVAL_KNOWN_VALIDATION_DIR=<student_id/*.jpg 루트>
FACE_EVAL_UNKNOWN_VALIDATION_DIR=<미등록 validation 이미지 루트>
FACE_EVAL_KNOWN_TEST_DIR=<student_id/*.jpg 루트>
FACE_EVAL_UNKNOWN_TEST_DIR=<미등록 test 이미지 루트>
FACE_DETECTION_MODEL_PATH=<scrfd_10g_bnkps.onnx 절대경로>
FACE_RECOGNITION_MODEL_PATH=<w600k_r50.onnx 절대경로>
FACE_EVAL_THRESHOLD_OUTPUT=<thresholds.json 출력 절대경로>
```

```bash
python -m pip install -r deeplearning/training/requirements-face-eval.txt
python -m deeplearning.training.face_identification_eval
```

test 결과를 보고 임계값을 다시 고르지 않는다. 생성된 파일을 GPU 서버의
`.docker/models/face/config/thresholds.json`으로 복사한다. 얼굴 이미지, embedding,
MongoDB 자격 증명, JSON 산출물은 Git에 커밋하지 않는다.

개인 PC에서 실행하는 FastAPI의 `.docker/env/fastapi.dev.env`에는 생성된 JSON의
`similarity_threshold`와 같은 값을 넣고 FastAPI를 재기동한다.

```dotenv
STUDENT_IDENTITY_CONFIDENCE_THRESHOLD=<thresholds.json의 similarity_threshold>
```

deeplearning은 similarity와 margin을 모두 통과한 신원만 반환하지만 FastAPI의 기본값
`0.5`가 더 높으면 확정된 신원이 학생 상태 판정에서 다시 빠질 수 있다. 두 값은 임의로
고르는 서로 다른 임계값이 아니라 같은 평가 결과로 맞춘다.

### 2. GPU 서버 환경과 모델 배치

`.docker/env/deeplearning.dev.env`에는 아래 두 비밀값만 필수로 둔다. MongoDB 계정은
가능하면 `face_embeddings` 읽기 권한만 부여한다.

```dotenv
FACE_GALLERY_DATABASE_URL=<MongoDB URI>
FACE_GALLERY_DATABASE_NAME=<DB 이름>
```

`.docker/env/worker.dev.env`에는 입구와 교실 CCTV가 모두 있어야 한다. 아래 camera ID는
FastAPI 카메라 설정 및 인계 ROI 설정의 ID와 정확히 같아야 한다.

```dotenv
STREAM_SOURCES=camera-01=rtsp://mediamtx:8554/camera-01,classroom-cctv=rtsp://mediamtx:8554/classroom-cctv
MODEL_PATH=/models/person-yolo11n-n1-v008.pt
INFERENCE_DEVICE=cuda
INFERENCE_TARGET_CLASS_IDS={"0":"person"}
FASTAPI_URL=http://<개인-PC-Tailscale-IP>:8076
FACE_IDENTITY_URL=http://deeplearning:8100
FACE_IDENTITY_CAMERA_IDS=camera-01
PERSON_TRACKING_CAMERA_IDS=camera-01,classroom-cctv
IDENTITY_HANDOVER_ROUTES=[{"entry_camera_id":"camera-01","classroom_camera_id":"classroom-cctv","classroom_entry_zone":[0.0,0.0,0.25,1.0]}]
```

`INFERENCE_CONFIDENCE_THRESHOLD`는 환경파일에서 제거해 이미지의 검증값 0.25를 쓰거나,
직접 둘 경우 반드시 ByteTrack high 0.5보다 낮게 둔다. 0.5이면 2단계 매칭용 탐지가
YOLO 출력에서 이미 사라지며 이제 worker가 기동을 거부한다.

필수 파일은 다음과 같다.

```text
.docker/models/
├── person-yolo11n-n1-v008.pt
└── face/
    ├── scrfd/scrfd_10g_bnkps.onnx
    ├── mediapipe/face_landmarker.task
    ├── buffalo_l/w600k_r50.onnx
    └── config/thresholds.json
```

### 3. GPU 서버에서 현재 소스로 이미지 빌드와 기동

Compose는 GHCR에 남은 오래된 `:latest`를 pull하지 않는다. `develop`에 병합되면 GPU
배포 workflow가 해당 커밋의 추적 소스만 GPU 서버로 보내 candidate 두 개를 빌드한다.
두 빌드와 사전검사가 모두 성공한 뒤 아래의 고정된 GHCR 형식 `:latest` 이름을 함께
새 image ID로 옮긴다. 같은 이름을 계속 덮어쓰므로 배포마다 이미지 이름이 늘어나지 않는다.

워크플로를 쓸 수 없을 때 GPU 서버의 저장소 checkout에서 수동 복구하는 절차는 다음과 같다.

```bash
git switch develop
git pull --ff-only
docker build -t ghcr.io/where-is-jo/classroom-monitoring-deeplearning:latest deeplearning
docker build -t ghcr.io/where-is-jo/classroom-monitoring-worker:latest worker
python .docker/scripts/validate_face_handover_deployment.py
docker compose -f .docker/compose.main.dev.gpu.yml config --quiet
docker compose -f .docker/compose.main.dev.gpu.yml up -d
python .docker/scripts/verify_face_handover_runtime.py
```

deeplearning healthcheck는 모델 파일 존재만이 아니라 MongoDB 갤러리가 비어 있지 않고
ArcFace metadata가 일치하는지까지 확인한다. 그래서 worker는 deeplearning이 healthy가 된
뒤 시작한다. GPU 배포 workflow도 서버에서 같은 사전점검을 실행하므로 env·모델·임계값이
빠진 Compose를 자동 재적용하지 않는다. 실행 중인 main GPU 스택을 재적용한 뒤에는 기본
런타임 검증도 자동 실행한다. 실패하면 설정과 두 `:latest` 태그를 모두 이전 상태로
되돌린다.

### 4. 실제 인계 확인

1. FastAPI `/identity-handover`에서 `camera-01 → classroom-cctv` route와 실제 문 바닥
   ROI를 저장한다. env의 route는 첫 조회 전·장애 시 fallback이다.
2. `docker compose -f .docker/compose.main.dev.gpu.yml ps`에서 deeplearning이 `healthy`,
   inference-worker가 `Up`인지 확인한다.
3. 입구 카메라에 등록 학생 한 명만 지나가게 하고, 이어 CCTV 문 ROI로 들어오게 한다.
4. worker `/metrics`에서 `face_identification_requests_total{outcome="ok"}`와
   `identity_handoff_total{outcome="accepted"}`가 증가하는지 확인한다. 학생 ID나 얼굴
   embedding은 지표·로그에 출력하지 않는다.
5. FastAPI 수신 이벤트에서 같은 CCTV `track_id`에 `student_id`가 유지되고 좌석 ROI에
   들어갔을 때 학생 상태로 반영되는지 확인한다.

위 3번 실제 동선까지 수행한 뒤 다음 명령을 실행하면 두 camera의 처리 프레임, 성공한
얼굴 호출, `accepted` 인계가 모두 1건 이상인지 자동 확인한다. URL·MongoDB 자격 증명·
학생 ID는 출력하지 않는다.

```bash
python .docker/scripts/verify_face_handover_runtime.py --require-live-handoff
```

## 이미지

**환경마다 이미지를 따로 유지한다**(결정 0018). fastapi dev는 CI가 만든 GHCR 이미지를
pull한다. worker와 deeplearning은 GitHub hosted runner의 GHCR 빌드 대상은 아니며, GPU
배포 workflow가 GPU 서버에서 병합된 소스를 직접 빌드한 고정 태그를 쓴다.

| 서비스 | dev 이미지 | local 이미지 (build) |
| --- | --- | --- |
| fastapi | `ghcr.io/where-is-jo/classroom-monitoring-fastapi:develop` | `classroom-monitoring-fastapi:local` |
| inference worker | `ghcr.io/where-is-jo/classroom-monitoring-worker:latest` (GPU 서버에서 같은 이름으로 재빌드) | `classroom-monitoring-worker:local` |
| deeplearning | `ghcr.io/where-is-jo/classroom-monitoring-deeplearning:latest` (GPU 서버에서 같은 이름으로 재빌드) | `classroom-monitoring-deeplearning:local` |

**fastapi만 `:develop`을 본다.** CI가 develop 병합마다 `develop`·`sha-*`로 올리고
`latest`는 붙이지 않기 때문이다(결정 0014). `:latest`를 보면 병합해도 서버가 갱신되지
않는다 — 실제로 2026-08-12에 손으로 올린 이미지가 계속 돌아 그 뒤에 들어온 탐지
수신(`/internal/inference/events`)과 ROI 매핑이 서버에 없었다.

**worker와 deeplearning의 `:latest`는 GPU 서버에서 현재 develop으로 직접 만든다.**
배포 workflow가 candidate 두 개를 모두 성공시킨 뒤 두 `:latest` 태그를 함께 덮어쓴다.
`pull_policy: never`라 서버 로컬 태그가 없으면 기동에 실패하고 registry의 오래된
`latest`를 조용히 받지 않는다.

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
성립하지 않는다. 그래서 [결정 0028](../docs/architecture/decisions.md#0028--영상-원본을-저장하지-않고-스냅샷만-남긴다)로
**영상 원본 대신 탐지 시점의 정지 이미지만** 남긴다. `worker/recorder`는 이 스택에서
실행하지 않는다.

| 항목 | 값 | 어디에 |
| --- | --- | --- |
| 해상도 / 품질 | 720p / JPEG 80 | 이미지 안의 `inference/config/settings.yml` |
| 카메라당 최소 적재 간격 | 60초 | 이미지 안의 `inference/config/settings.yml` |
| 적재 켜기 | `SNAPSHOT_ENABLED=true` | `.docker/env/worker.dev.env` (yml 기본값은 false) |
| 보존 기간 | 30일 | `compose.main.dev.gpu.yml`의 `SNAPSHOT_RETENTION_DAYS` |
| 버킷 | `classroom-snapshots` | 두 곳이 같은 값이어야 한다(아래) |

최악의 경우(간격 캡이 계속 걸릴 때) 카메라 3대 × 12시간 기준 하루 약 259 MB,
30일 약 7.8 GB다.

**버킷 이름이 두 곳에 있다.** `compose.main.dev.gpu.yml`의 `SNAPSHOT_BUCKET`(minio-init이 만들
버킷)과 이미지 안의 `config/settings.yml`(worker의 `object_storage_bucket`, fastapi의
`snapshot_storage_bucket`). 갈리면 워커는 올리는데 화면에는 안 보인다. 이전에는 세
곳이었는데, 앱 쪽 두 값이 커밋되는 yml로 옮겨가 한 곳으로 줄었다 — 버킷 이름을 바꾸려면
yml을 고치고 이미지를 다시 빌드해야 한다.

**삭제는 MinIO가 한다.** `minio-init` 서비스가 기동 시 버킷과 lifecycle 만료 규칙을
한 번 만든다(`restart: "no"`). 앱이 지우는 방식과 달리 워커가 죽어 있어도 삭제가 계속된다.

**컨테이너 로그 회전을 걸었다**(`max-size: 10m`, `max-file: 3`). Docker `json-file`
기본값이 무제한이라 48 GB 환경에서 조용히 쌓인다. worker가 프레임 샘플마다 로그를 남긴다.

## 앞단에 reverse proxy를 두지 않는다

**세 번 바뀌어 여기 왔다.** 기록해 둔다 — 되돌아가려는 논의가 반복되기 때문이다.

1. 우리 쪽에 Caddy를 두고 `:80 → fastapi`로 프록시했다.
2. 앞단에 다른 팀 nginx가 이미 있어 같은 일을 두 겹으로 할 이유가 없었다.
   Caddy를 빼고 경로 분기를 그쪽에 요청했다(`/` → fastapi, `/n8n/` → n8n).
3. **지금은 그 nginx도 쓰지 않는다.** 브라우저가 붙던 둘이 결정 0026으로 개인 PC로
   갔고, **인터넷에 공개하지 않기로 하면서** 도메인·TLS·경로 분기가 전부 필요 없어졌다.

`individual_tasks/도커구성/nginx_연동_요청.md`의 요청은 **철회 대상이다.**
`caddy/Caddyfile*`도 이미 없다.

그래서 브라우저는 tailnet 안에서 개인 PC의 포트에 직접 붙는다.

| 주소 | 대상 |
| --- | --- |
| `http://100.119.241.93:8076` | 웹 화면·API (실시간 영상 시그널링 포함) |
| `http://100.119.241.93:15678` | n8n 편집기. **경로 접두사가 없다**(`N8N_PATH=/`) |

`N8N_PATH=/n8n/`은 nginx 전제였으므로 함께 없앴다. 접두사가 없으면 편집기가 자기 자산을
`/assets/…`로 찾아도 그 앞에 fastapi가 없어 새어 나갈 곳이 없다.

**MediaMTX가 돌려주는 `Location`에 접두사가 없던 문제**는 앞단과 무관하게 이미 풀려
있다 — fastapi가 시그널링을 중계하면서 자기 경로로 다시 써 준다(결정 0014).

### 붙이게 되면 되돌려야 할 것

평문 http 전제로 고정한 값이 셋이다.

| 값 | 지금 | 왜 |
| --- | --- | --- |
| `PLAYBACK_SESSION_COOKIE_SECURE` | `false` | true면 평문 http에서 브라우저가 세션 cookie를 보내지 않아 영상 재생이 조용히 실패한다 |
| `N8N_SECURE_COOKIE` | `false` | 같은 이유로 n8n 로그인 세션이 깨진다 |
| uvicorn `FORWARDED_ALLOW_IPS` | 미설정 | 기본값이 `127.0.0.1`이라 컨테이너 network를 타고 온 `X-Forwarded-*`를 무시한다 |

**공개 도메인을 붙이는 것은 별도 결정이 필요하다.** 앱에 인증이 없어서
(결정 0030) URL을 아는 사람이면 학생 얼굴이 담긴 화면과 스냅샷을 그대로 볼 수 있다.

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
| `MTX_WEBRTCADDITIONALHOSTS` | `compose.main.dev.gpu.yml`에 직접 적혀 있다 | ICE 후보로 알릴 주소 |
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

**아래 라우팅 검증은 Caddy가 있던 시점의 기록이다.** Caddy를 뺀 뒤로도, 앞단에
proxy를 아예 두지 않기로 한 뒤로도 **다시 확인하지 않았다.** 검증 자체(무엇이 통했고
무엇이 걸렸는지)는 그대로 유효하지만 경로 앞단이 이제 없다.
포트 번호도 5자리로 옮겨서(`8189` → `18189`) 아래 로그의 번호와 다르다.

**호스트 분할(결정 0026) 이후의 구성은 아직 아무것도 실행으로 확인하지 않았다.**
`docker compose config` 통과만 확인했다. 확인해야 할 것은 아래 "안 한 것"에 있다.

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

**llama-server는 실제로 기동해 응답까지 확인했다**(2026-08-18, L40S):

- `compose.llm.dev.yml`로 기동 → `model loaded` → `listening on 0.0.0.0:8008`.
  모델 로딩은 2초 남짓이고 `n_slots=4`, `n_ctx_slot=4096`으로 뜬다.
- **compose의 GPU 예약이 실제로 GPU를 잡는다.** `deploy.resources.reservations.devices`
  경로로 GPU 1번에 7,441 MiB를 점유했다 — `LLAMA_ARG_N_GPU_LAYERS=99`가 먹은 것이다.
  worker의 `docker run --gpus all`과 달리 이 경로는 그동안 확인하지 못했던 부분이다.
- **healthcheck가 healthy가 된다.** 이미지에 curl이 있어, 없으면 지우라고 적어 둔
  `compose.llm.dev.yml`의 단서는 그대로 두어도 된다.
- **chat completions가 `system` role을 받는다.** 이 GGUF의 내장 template은
  `System role not supported`로 예외를 던지게 되어 있지만, 서버가 `--jinja` 없이
  도는 legacy 경로라 llama.cpp의 gemma template이 system을 user에 합쳐 준다.
  **`LLAMA_ARG_JINJA`를 켜면 자연어 검색의 모든 요청이 400이 된다** — 켜지 않는다.
- 실제 지시문으로 계획 JSON을 받는 데 **질문당 0.8~0.9초**가 걸렸다.
  `llm_search_timeout_seconds`(20초)는 여유가 크다.

확인하지 못한 것:

- **실제 카메라로 들어오는 영상.** 아래 검증은 합성 RTSP 스트림으로 했다.
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
- n8n 편집기의 실제 조작과 워크플로 저장. HTTP 응답과 자산 로딩까지만 봤다.
- **WebRTC 미디어가 실제로 흐르는 것.** 시그널링(WHEP)과 ICE 후보까지만 봤다.
  DTLS 핸드셰이크부터 영상 재생까지는 브라우저나 WebRTC 클라이언트가 필요하다.
- **subnet router를 거쳐 CCTV에 닿는 것.** 라즈베리파이가 아직 서지 않았다.
  현재 worker 로그는 `No route to host`를 3초 간격으로 반복한다 — **설정이 아니라
  경로가 없어서다.** 파이를 세운 뒤 아래로 확인한다.

  ```bash
  # GPU 서버에서 — 라우트가 실제로 들어왔는지
  tailscale status | grep -i route
  ip route get 192.168.0.63

  # 포트가 열리는지 (RTSP가 554가 아니라 80이다)
  nc -vz 192.168.0.63 80

  # 영상이 실제로 오는지
  ffprobe -rtsp_transport tcp "rtsp://<계정>:<비밀번호>@192.168.0.63:80/rtsp/streaming?channel=07"
  ```

  **라우트 승인을 잊기 쉽다.** 파이에서 광고해도 Tailscale 관리 콘솔에서 승인하기
  전에는 대기 상태로 남아 GPU 서버에 들어오지 않는다.
- **worker -> fastapi 탐지 이벤트 전송.** 카메라가 붙지 않아 보낼 것이 없다.
  주소(`FASTAPI_URL`)는 바꿔 뒀고 반대 방향(Prometheus -> fastapi)이 같은 경로로
  통하는 것은 확인됐으므로, 남은 것은 이벤트가 실제로 실릴 때의 동작이다.

### 호스트 분할(결정 0026) 구성을 실제로 띄워 확인했다

2026-08-22, 양쪽 스택을 모두 올린 상태에서 확인했다.

- **포트가 의도한 인터페이스에만 묶인다.** 서버에서 `ss -lntp`로 실측:
  `18100`·`19000`·`18889`·`18008`은 `100.85.0.72`에만, `18189`만 `0.0.0.0`에 LISTEN.
  공용 서버의 공인 IP로는 넷 다 열리지 않는다.
- **Windows Docker Desktop에서 특정 IP bind가 먹는다.** 노트북의 fastapi·n8n이
  `127.0.0.1`과 `100.119.241.93` 두 주소에 동시에 묶였고 양쪽 다 응답한다.
- **개인 PC 컨테이너 -> GPU 서버 넷 다 통한다.** fastapi 컨테이너 안에서:

  | 대상 | 응답 |
  | --- | --- |
  | `100.85.0.72:18100/health` (deeplearning) | 200 `{"status":"ok"}` · 20ms |
  | `100.85.0.72:19000/minio/health/live` | 200 · 11ms |
  | `100.85.0.72:18008/health` (llama-server) | 200 `{"status":"ok"}` · 13ms |
  | `100.85.0.72:18889/camera-01/whep` (mediamtx) | 405 · 13ms — GET이라 정상이다. WHEP은 POST다 |

  bridge network에서 호스트를 거쳐 tailnet으로 나가는 경로가 그대로 동작한다.
- **스냅샷 읽기가 호스트를 넘어 실제로 된다.** `GET /api/v1/snapshots`가 GPU 서버
  MinIO의 목록을 돌려주고, 이미지 프록시도 통했다 —
  `GET /api/v1/snapshots/image/...` 200, 31,927 B JPEG(640×480), **47ms.**
  결정 0026이 "GPU 서버 -> 개인 PC -> 브라우저로 두 번 이동한다"고 우려한 구간인데
  실측은 견딜 만하다.
- **Prometheus가 개인 PC의 fastapi를 스크랩한다.** target
  `http://100.119.241.93:8076/metrics`가 `up`. GPU 서버 -> 개인 PC 방향이 실제로 열려 있다는
  뜻이므로, 같은 경로를 쓰는 worker의 탐지 이벤트 전송도 주소는 맞다.
- **카메라 자격 증명이 로그에 평문으로 남지 않는다.** worker의 연결 실패 로그를
  200줄 뒤져 비밀번호 문자열이 나오지 않는 것을 확인했다. 로그는 Loki로도 가므로 중요하다.

**같이 드러난 것 — 서버의 `:latest` 이미지 둘이 낡았다.** 호스트 분할과 무관한
기존 문제이며, Prometheus 타겟 둘이 `down`인 원인이다.

| 이미지 | 서버 빌드 시각 | 문제 |
| --- | --- | --- |
| `worker:latest` | 2026-08-12 | `9101`을 열지 않는다. 지표 노출은 08-18 커밋 `34c5d98`로 들어왔다 |
| `deeplearning:latest` | 2026-08-18 05:06 | `/metrics`가 404. OpenAPI에 경로 자체가 없다. 지표는 같은 날 커밋 `748ca7f`로 들어왔다 |

**CLAUDE.md와 `monitoring/internal/README.md`는 둘 다 지표를 노출한다고 적고 있다.**
소스는 맞지만 **서버에 올라간 이미지가 그 커밋 이전이었다.** 이 문제 때문에 GPU 배포
workflow가 worker와 deeplearning을 서버에서 현재 커밋으로 빌드하고, 두 `:latest`를 함께
덮어쓴 뒤 강제 재생성하도록 바꿨다. GitHub hosted runner에서 14.9GB worker 이미지를
빌드하지 않는 결정 0014의 제약은 그대로다.

## 정하지 않고 남긴 것

- **입구 카메라(라즈베리파이 웹캠)를 어느 방향으로 넣을 것인가.** 파이가 RTSP 서버를
  띄워 worker가 당길지, 파이의 ffmpeg가 GPU 서버 MediaMTX로 밀어 넣을지 정해지지 않았다.
  **밀어 넣는 쪽을 고르면 MediaMTX RTSP publish(8554)를 Tailscale 주소에 열어야 한다** —
  지금은 닫혀 있다. 당기는 쪽이면 `STREAM_SOURCES`에 항목을 하나 더 붙이면 끝이다.
  카메라 역할 구분(입구는 좌석을 판정하지 않는다)은 결정 0024에 있다.
- **개인 PC가 꺼져 있을 때 worker의 동작.** 지금은 제한 재시도 뒤 버린다.
  버퍼링할지, 얼마나 들고 있을지, 복구되면 밀린 것을 보낼지가 정해져 있지 않다
  (결정 0026의 남은 일).
- **개인 PC가 꺼졌을 때의 알림.** Prometheus의 fastapi target이 `up=0`이 되는데,
  이것을 장애로 볼지 정상으로 볼지 정해지지 않았다. 노트북이라 늘 켜져 있지 않다.
- **스냅샷 버킷의 접근 권한.** 지금은 worker와 fastapi가 모두 MinIO root 키로 붙는다.
  쓰기(worker)와 읽기(fastapi)를 나눈 전용 키가 필요하다.
  **호스트 분할로 더 급해졌다** — MinIO S3 API가 이제 호스트 포트로 열려 있다.
- **어떤 GGUF를 `gemma.gguf`로 둘 것인가.** 파일명은 `compose.llm.dev.yml`에
  고정했고 바꾸지 않는다 — 모델을 바꿀 때마다 compose를 고치면 서버와 저장소가
  어긋난다. **받은 가중치를 `models/gemma.gguf`로 이름을 바꿔 두고, 무엇을 두었는지
  아래 표에 적는다.**

  | 날짜 | 모델 | 양자화 | 크기 |
  | --- | --- | --- | --- |
  | 2026-08-12 | gemma-2-9b-it | Q4_K_M | 5,761,057,728 B (5.4 GiB) |

  모델·양자화는 GGUF 헤더의 `general.name`과 `general.file_type`(15)에서 읽었다.
  컨텍스트 상한은 8192이고 compose는 그중 4096만 쓴다.
- **GPU 분배.** inference worker와 llama-server 둘 다 `device_ids: ["1"]`이다.
  계정에 할당된 GPU가 그것뿐이라 나눌 수 없다. **GPU가 1장뿐인 다른 PC에서는 이
  설정으로 기동에 실패한다** — 그 환경을 쓰려면 별도 파일이 필요하다.
- **비밀값 관리.** `env/*.env`는 지금 로컬 파일이다. 공용 서버에서 어떻게 주입할지
  정해지지 않았다.
- **영상 스트림의 접근 제어.** 재생 세션 API는 인증 없이 열려 있다.
  강의실 영상에는 사람 얼굴이 담기므로 fastapi의 인증과 어떻게 잇을지 정해야 한다
  (MediaMTX 자체 인증을 쓸지, fastapi가 발급한 토큰을 검사할지).
  `monitoring/external`의 경계 문제와 함께 다룬다.
