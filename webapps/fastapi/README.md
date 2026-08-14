# fastapi

관리자가 사용하는 강의실 모니터링 화면과 HTTP API를 제공한다.
이 저장소에서 실행 가능한 웹 서비스이자 브라우저의 단일 진입점이다.

**이 서비스가 학생 상태 판정을 소유한다.** 탐지 결과를 `PRESENT` / `WRONG_SEAT` /
`ABSENT`로 바꾸는 규칙은 여기 있고, `worker`나 `deeplearning`에 두지 않는다
([결정 0008](../../docs/architecture/decisions.md#0008--학생-상태-판정을-rule-engine으로-분리하고-fastapi가-소유한다)).

> **현재 범위는 강의실 좌석 현황, 실시간 모니터링, 자연어 검색과 학생 등록이다.**
> 학생 원장은 memory 또는 MongoDB의 `students` 컬렉션에 저장된다. 얼굴 데이터 수집은 별도 프로필로 구현되어 있다.
> 현재 좌석 상태는 "자리가 찼는지"를 뜻하며 "누가 앉았는지"가 아니다.
> 앞으로 만들 도메인과 계약은 [학생 모니터링 MVP 명세](../../docs/specs/student-monitoring-mvp.md)에 있다.

## 빠른 시작

Python 3.12 환경에서 실행한다.

```bash
cd webapps/fastapi
python -m pip install -r requirements.txt
cp .env.example .env.local
export APP_ENV=local   # 생략하면 어차피 local로 동작한다
python -m uvicorn app.main:app --reload --port 8001
```

실행 환경마다 `.env.local` / `.env.dev` / `.env.prod` 중 해당하는 파일을 만든다.
어떤 파일을 읽을지는 실제 OS 환경변수 `APP_ENV`가 정한다. 재시도 횟수·타임아웃·판정
임계값처럼 환경과 무관한 값은 `.env.*`가 아니라 커밋된 [`config/settings.yml`](./config/settings.yml)에 있다.

얼굴 등록 기능을 로컬에서 실행할 때는 SCRFD·MediaPipe·품질 검사를 담당하는
`deeplearning` 서버도 필요하다. 필요한 패키지가 설치된 Python 또는 Conda 환경을
활성화한 뒤 아래 스크립트를 실행하면 분석 서버(`8100`)와 웹 서버(`8000`)가 함께
시작된다.

```powershell
cd webapps/fastapi
.\run-face-enrollment.ps1
```

PowerShell 실행 정책으로 차단되면 현재 실행에만 우회 정책을 적용할 수 있다.

```powershell
powershell -ExecutionPolicy Bypass -File .\run-face-enrollment.ps1
```

특정 Python 실행 파일을 사용하려면 `-PythonPath`로 지정한다.

```powershell
.\run-face-enrollment.ps1 -PythonPath "$env:CONDA_PREFIX\python.exe"
```

스크립트를 실행한 창에서 Enter를 누르면 두 서버를 함께 종료한다.

기본 예제는 `APP_ENV=local`, `DATABASE_MODE=memory`라서 외부 서비스 없이 기동한다.
**로그인이 없다.** 채워야 하는 비밀값도 없다. `http://127.0.0.1:8001`을 열면
`/classrooms`로 이동한다.

`config/settings.yml`에서 `demo_mode_enabled: true`로 바꾸면(또는 실행 시 실제 OS
환경변수 `DEMO_MODE_ENABLED=true`로 즉석 재정의하면) 개인정보 없는 합성 영상 source와
고정 검색 catalog가 붙고, memory 저장소에 강의실·좌석 fixture가 멱등하게 채워진다.
`APP_ENV=prod`에서는 활성화를 거부한다.

MongoDB를 사용하려면 `DATABASE_MODE=mongodb`와 `DATABASE_URL`, `DATABASE_NAME`을
주입한다. 앱은 요청을 받기 전에 연결을 확인하고 필요한 index를 idempotent하게 초기화한다.
`memory` mode는 `local`에서만 허용한다.

> **`APP_ENV=prod`로 배포하지 않는다.** 현재 인증이 없고, 운영 접근 통제 방식이
> 아직 정해지지 않았다
> ([결정 0010](../../docs/architecture/decisions.md#0010--mvp-제품-사용자를-관리자-하나로-한정한다)).

## 화면과 API

실행 중인 설정에서 공개되는 정확한 JSON 계약은 `/docs`와 `/openapi.json`에서 확인한다.
Jinja2 화면 경로는 OpenAPI에 넣지 않는다. 모든 JSON API 오류는
`{"error": {"code", "message", "details"}}` envelope를 사용한다.

### 화면

| 경로 | 설명 |
| --- | --- |
| `/` | `/classrooms`로 이동 |
| `/classrooms` | 강의실 선택, 좌석 지도, 재석·부재·확인 필요 집계와 마지막 관측 시각 |
| `/classrooms/create` | 강의실 등록 |
| `/classrooms/{id}/edit` | 강의실 수정 |
| `/classrooms/{id}/seats` | 강의실별 좌석 목록·배치도 관리 |
| `/classrooms/{id}/seats/create` | 좌석 추가 (배치도 위치 비율 입력) |
| `/classrooms/{id}/seats/{seat_id}/edit` | 좌석 수정 |
| `/roi-connections` | 메모리 가상 강의실·좌석과 DB 학생을 다각형 ROI로 연결하고 MongoDB에 저장 |
| `/monitoring` | 영상 source 목록과 연결 상태. demo가 꺼져 있으면 빈 상태 |
| `/video-search` | **데모 영상 검색.** 규칙 기반 한국어 토큰 매칭이며 대상은 합성 catalog다. LLM을 쓰지 않는다. demo가 꺼져 있으면 빈 결과 |
| `/llm-search` | **자연어 탐지 검색.** 질문을 LLM이 검색 조건으로 바꾸고 서버가 검증한 뒤 탐지 기록을 찾는다. 탐지 인원이 바뀐 시점만 보여준다 |

`/classrooms/{id}` 상세 페이지는 없다. `/classrooms?classroom_id={id}`에서 같은
정보를 선택해 본다.

### API

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `GET` | `/api/v1/classrooms` | 활성 강의실 목록 |
| `POST` | `/api/v1/classrooms` | 강의실 생성 |
| `GET` | `/api/v1/classrooms/{classroom_id}` | 한 강의실의 상세 정보 |
| `PUT` | `/api/v1/classrooms/{classroom_id}` | 한 강의실 수정 (전달한 필드만 갱신) |
| `DELETE` | `/api/v1/classrooms/{classroom_id}` | 한 강의실 삭제 (비활성화) |
| `GET` | `/api/v1/classrooms/{classroom_id}/occupancy` | 한 강의실의 좌석 지도와 현재 점유 |
| `GET` | `/api/v1/classrooms/{classroom_id}/occupancy-events` | SSE 좌석 점유 실시간 구독 |
| `PUT` | `/api/v1/classrooms/{classroom_id}/seats/{seat_id}/assignment` | 좌석에 학생 지정 (같은 강의실 내 이동·멱등) |
| `DELETE` | `/api/v1/classrooms/{classroom_id}/seats/{seat_id}/assignment` | 좌석-학생 지정 해제 |
| `GET` | `/api/v1/classrooms/{classroom_id}/seat-assignments` | 강의실의 좌석-학생 지정 현황 |
| `POST` | `/api/v1/students` | 학생 인적사항과 완료된 얼굴 등록 참조 저장 |
| `POST` | `/api/v1/classrooms/{classroom_id}/roi-reference-image` | ROI 기준 JPEG·PNG 이미지를 메모리에 첨부 |
| `GET` | `/api/v1/classrooms/{classroom_id}/roi-reference-image` | 현재 ROI 기준 이미지 조회 |
| `GET` | `/api/v1/classrooms/{classroom_id}/roi-connections` | 좌석별 ROI와 연결 학생 조회 |
| `PUT` | `/api/v1/classrooms/{classroom_id}/seats/{seat_id}/roi-connection` | 좌석 ROI와 학생 연결을 `roi_connections` 컬렉션에 저장 |
| `GET` | `/api/v1/video-streams` | 영상 source 목록. demo + 실제 source |
| `GET` | `/api/v1/video-streams/{stream_id}` | 한 source의 상태 |
| `POST` | `/api/v1/video-streams/{stream_id}/playback-sessions` | 실제·enabled·WebRTC source의 재생 세션 생성 (결정 0014) |
| `POST` | `/api/v1/video-streams/{stream_id}/playback-sessions/{session_id}` | WHEP offer signaling (MediaMTX proxy) |
| `PATCH` | `/api/v1/video-streams/{stream_id}/playback-sessions/{session_id}` | ACTIVE 세션 재협상 signaling |
| `DELETE` | `/api/v1/video-streams/{stream_id}/playback-sessions/{session_id}` | WHEP resource 종료·세션 CLOSED (idempotent) |
| `GET` | `/api/v1/video-streams/{stream_id}/detections` | 카메라별 탐지 이벤트 조회 |
| `GET` | `/api/v1/video-streams/{stream_id}/detection-events` | SSE 실시간 탐지 이벤트 구독 |
| `GET` | `/api/v1/video-segments` | 영상 세그먼트 메타데이터 조회 |
| `POST` | `/api/v1/video-searches` | 부작용 없는 데모 catalog 검색 실행 (규칙 기반) |
| `POST` | `/api/v1/llm-searches` | 자연어 질문을 검증된 조건으로 바꿔 탐지 기록 검색. 해석한 계획을 응답에 함께 싣는다 |
| `POST` | `/internal/inference/events` | worker 탐지 이벤트 수신 (멱등) |
| `POST` | `/internal/video-segments` | worker 영상 세그먼트 수신 (멱등) |
| `GET` | `/health` | 프로세스 기동 상태 |
| `GET` | `/health/ready` | 현재 저장소 준비 상태 |

**내부 쓰기 API가 있다.** worker가 탐지 이벤트(`/internal/inference/events`)와
영상 세그먼트(`/internal/video-segments`)를 보낼 수 있다. 로그인, 사용자 관리,
알림, 관리자 대시보드는 현재 구현되어 있지 않다.

### 좌석 상태 표기

| 저장 값 | 화면 문구 | 의미 |
| --- | --- | --- |
| `OCCUPIED` | 재석 | 좌석 점유 관측이 confidence 기준 이상이다. **학생 신원을 뜻하지 않는다** |
| `VACANT` | 부재 | 좌석 비점유 관측이 confidence 기준 이상이다. **지정 학생의 부재를 확정하지 않는다** |
| `UNKNOWN` | 확인 필요 | 미관측, 낮은 confidence 또는 신뢰할 수 없는 관측이다 |

색만으로 상태를 구분하지 않고 문구와 기호를 함께 쓴다.
**관측 실패나 영상 없음을 `VACANT`로 바꾸지 않는다.**

### 합성 데모

`APP_ENV=local|dev`와 `DEMO_MODE_ENABLED=true`를 함께 설정할 때만 합성 영상 source와
고정 검색 catalog를 제공하고 `/demo-assets/*`를 mount한다. demo 응답은 `is_demo=true`를
유지한다.

이 기능은 카메라·스트림·추론·영상 저장을 구현하지 않는다. 외부 미디어, 사용자 업로드,
운영 데이터도 사용하지 않는다. demo가 꺼져 있으면 `/monitoring`과 `/video-search`는
404가 아니라 빈 상태를 반환한다.

## 핵심 도메인 규칙

- 좌석 관측은 batch 전체를 먼저 검증하고 event ID로 멱등 처리한다. confidence 기준
  미만은 `UNKNOWN`이고 오래된 관측은 현재 상태를 되돌리지 않는다.
- 조회 GET은 저장된 상태를 바꾸지 않는다. 시간 기반 평가는 명시적인 쓰기 요청에서만
  수행한다.
- 신뢰도 등급 같은 해석은 서비스 계층이 계산해 템플릿에 넘긴다. 템플릿은 enum을
  새로 판정하지 않는다.
- 저장소를 사용할 수 없으면 부분 데이터나 demo로 대체하지 않고 503을 반환한다.

학생 식별과 상태 판정이 들어오면 지킬 규칙은
[MVP 명세](../../docs/specs/student-monitoring-mvp.md)와
[fastapi 에이전트 규칙](../../docs/agents/fastapi-agent.md#학생-상태-판정)에 있다.

## 내부 구조

기능별 디렉터리와 `router -> service -> port <- adapter` 호출 방향을 사용한다. 라우터는
HTTP 변환만, 서비스는 프레임워크와 분리된 판단만 담당한다. 저장소 구현의 조립은
`app/shared/dependencies.py` 한 곳에 둔다. 배경은
[결정 0001](../../docs/architecture/decisions.md#0001--fastapi-계층형-구조와-경계-포트)에 있다.

```text
app/
├─ main.py              앱 조립, 라우터 등록, 예외 처리
├─ classrooms/          강의실, 좌석, 좌석 점유 관측
├─ video_monitoring/    영상 source 목록과 검색 (local/dev 합성 catalog)
├─ face_enrollment/     능동형 얼굴 등록 세션·품질·pose 완료 판정
├─ student_monitoring/  탐지 이벤트 수신·SSE·영상 세그먼트 메타데이터
├─ llm_search/          자연어 질문 → 검증된 검색 조건 → 탐지 기록 조회
├─ shared/              설정, 저장소 조립, 공통 오류·템플릿·스키마
└─ demo_seed.py         demo fixture 멱등 생성

templates/              기능별 Jinja2 화면
static/                 CSS와 화면 보조 JavaScript
demo_assets/            합성 데모 SVG 자산
api-spec/               구현 전 API 설계 명세 JSON
tests/                  단위·API·템플릿·선택적 MongoDB 통합 테스트
```

`face_enrollment`는 memory
저장소와 SCRFD 중앙 분석 HTTP 어댑터를 사용하는 local MVP가 구현됐다.
`student_monitoring` 도메인이 구현되어 탐지 이벤트 수신·MongoDB 저장·SSE 발행이
동작한다. 
`students`에는 DB 연결 전 화면 흐름을 확인하기 위한 학생 등록 프로토타입이 있다.
`/students/new`에서 최소 인적사항을 입력하면 서버 전송 없이 브라우저 콘솔에 출력하고
페이지 상단 성공 배너를 표시한다. 같은 화면의 얼굴 등록 모달은 기존 얼굴 등록 API를
재사용하며 동의 확인, 촬영, 완료 순서로 진행된다. 학생 API와 MongoDB 저장은 후속 작업이다.
책임과 목표 계약은 [MVP 명세의 도메인 구조](../../docs/specs/student-monitoring-mvp.md#도메인-구조-예정)에 있다.

추론 연산, 스트림 연결·디코딩, 실제 영상 저장은 이 서비스에 포함하지 않는다.
영상과 얼굴 데이터의 저장 범위·보존 기간·권한은 여전히 결정이 필요하다.

## 환경변수와 설정

값은 두 파일에 나뉘어 있다. 환경마다 달라야 하는 값과 비밀값은 `.env.{local,dev,prod}`
(전체 이름은 [`.env.example`](./.env.example)이 기준), 환경과 무관한 값은 커밋된
[`config/settings.yml`](./config/settings.yml)에 있다. 어떤 `.env.*`를 읽을지는 실제
OS 환경변수 `APP_ENV`가 정한다(없으면 `local`).

### `.env.{local,dev,prod}`

| 이름 | 용도 | 제약 |
| --- | --- | --- |
| `APP_ENV` | 실행 환경 | `local` / `dev` / `prod` |
| `DATABASE_MODE` | 저장소 종류 | `memory` / `mongodb`. memory는 `local` 전용 |
| `DATABASE_URL`, `DATABASE_NAME` | MongoDB 접속 정보 | mongodb mode에서 필수. URL은 비밀값 |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | 연결 타임아웃 | 기본 5. `0 < x <= 60` |
| `DEMO_MODE_ENABLED` | 합성 영상·검색 demo | 기본 false. `local`/`dev` 전용. prod 금지 |
| `SEAT_OCCUPANCY_CONFIDENCE_THRESHOLD` | 이 값 미만의 좌석 관측은 `UNKNOWN` | 기본 0.6. `0 <= x <= 1` |
| `PAGE_SIZE_DEFAULT`, `PAGE_SIZE_MAX` | 목록 페이지 크기 | 최대 200 |
<<<<<<< HEAD
=======
| `ROI_REFERENCE_IMAGE_MAX_BYTES` | ROI 임시 기준 이미지 업로드 제한 | 기본 5MB, 최대 20MB |
>>>>>>> develop
| `FACE_ENROLLMENT_REQUIRED_SAMPLES` | 얼굴 등록 완료 최소 실제 촬영 유효본 수 | 기본 120 |
| `FACE_ENROLLMENT_AUGMENTED_SAMPLES` | local 데이터셋 완료 시 생성할 증강본 수 | 기본 180 |
| `FACE_POSE_*_QUOTA` | 방향별 실제 촬영 유효본 수 | 합계가 전체 필수 수와 같아야 함. 기본값은 정면 32, 좌·우 각 24, 위·아래 각 20장 |
| `FACE_*` 품질 설정 | 탐지·크기·roll·흐림·밝기·landmark·가림·중복·pose 기준 | 코드가 아닌 환경변수로 조정 |
| `FACE_MOTION_SPEED_DPS_MAX` | 프레임 간 허용 머리 각속도 | 기본 220도/초. 초과 프레임은 저장하지 않음 |
<<<<<<< HEAD
=======
| `FACE_PITCH_DOWN_DEGREES` | 아래 방향으로 분류하는 최소 pitch | 기본 5도. 위 방향 기준과 별도 적용 |
>>>>>>> develop
| `FACE_LOCAL_SAMPLE_STORAGE_ENABLED` | local 테스트의 유효 JPEG 파일 저장 | 기본 false, local 전용 |
| `FACE_LOCAL_SAMPLE_STORAGE_DIR` | local 얼굴 샘플 저장 위치 | 기본 `local_face_data`, Git 추적 제외 |
| `SSE_HEARTBEAT_INTERVAL_SECONDS` | SSE heartbeat 간격 | 기본 30 |
| `SSE_RECONNECTION_TIMEOUT_SECONDS` | SSE 재연결 타임아웃 | 기본 60 |
| `DETECTION_EVENT_MAX_DETECTIONS_PER_EVENT` | 탐지 이벤트당 최대 탐지 수 | 기본 100 |
| `DETECTION_EVENT_STALE_SECONDS` | 탐지 이벤트 stale 판정 기준 | 기본 300 |
| `WHEP_BASE_URL` | WHEP proxy target의 base URL (결정 0014). source의 camera_id로만 조립된다 | 기본 `http://127.0.0.1:8889` |
| `WHEP_TIMEOUT_SECONDS` | MediaMTX signaling 호출 타임아웃 | 기본 5 |
| `PLAYBACK_SESSION_TTL_SECONDS` | 재생 세션 TTL | 기본 300. 30~3600 |
| `PLAYBACK_SESSION_COOKIE_SECURE` | owner cookie Secure 플래그 | 기본 true. local/http에서는 false로 내려야 전송된다 |
| `PLAYBACK_SESSION_SDP_MAX_BYTES` | SDP 본문 최대 크기 | 기본 65536 |
| `FACE_ANALYZER_MODE`, `FACE_ANALYZER_URL` | 얼굴 분석 companion 방식과 주소 | local은 보통 `synthetic`, dev/prod는 `http` |
| `SNAPSHOT_STORAGE_BACKEND` | 탐지 스냅샷 저장소 | `memory` / `minio`. local은 보통 `memory` |
| `SNAPSHOT_STORAGE_ENDPOINT`, `_ACCESS_KEY`, `_SECRET_KEY` | MinIO 접속 정보 | `minio` backend에서만 필수. 비밀값 |
| `LLM_SEARCH_MODE` | 자연어 검색의 계획 생성 방식 | 기본 `stub`. `stub`은 LLM 없이 "오늘 하루"만 돌려주는 대역, `llama`는 llama-server 호출 |
| `LLM_SEARCH_URL` | llama-server의 OpenAI 호환 API 주소 | `llama` mode에서 필수. 기본 `http://127.0.0.1:8008` |
| `LLM_SEARCH_MODEL` | 요청에 넣을 모델 이름 | llama-server의 `LLAMA_ARG_ALIAS`와 같아야 한다. 기본 `gemma` |
| `TEST_DATABASE_URL` | 선택적 MongoDB 통합 테스트용 | database 이름이 `test_`로 시작해야 한다 |

### `config/settings.yml`

| 이름 | 용도 | 제약 |
| --- | --- | --- |
| `database_connect_timeout_seconds` | 연결 타임아웃 | 기본 5. `0 < x <= 60` |
| `demo_mode_enabled` | 합성 영상·검색 demo | 기본 false. `local`/`dev` 전용. prod 금지 |
| `seat_occupancy_confidence_threshold` | 이 값 미만의 좌석 관측은 `UNKNOWN` | 기본 0.6. `0 <= x <= 1` |
| `page_size_default`, `page_size_max` | 목록 페이지 크기 | 최대 200 |
| `face_enrollment_required_samples` | 얼굴 등록 완료 최소 실제 촬영 유효본 수 | 기본 120 |
| `face_enrollment_augmented_samples` | local 데이터셋 완료 시 생성할 증강본 수 | 기본 180 |
| `face_pose_*_quota` | 방향별 실제 촬영 유효본 수 | 합계가 전체 필수 수와 같아야 함. 기본값은 정면 32, 좌·우 각 24, 위·아래 각 20장 |
| `face_*` 품질 설정 | 탐지·크기·roll·흐림·밝기·landmark·가림·중복·pose 기준 | 코드가 아닌 설정 파일로 조정 |
| `face_motion_speed_dps_max` | 프레임 간 허용 머리 각속도 | 기본 220도/초. 초과 프레임은 저장하지 않음 |
| `face_local_sample_storage_enabled` | local 테스트의 유효 JPEG 파일 저장 | 기본 false, local 전용 |
| `face_local_sample_storage_dir` | local 얼굴 샘플 저장 위치 | 기본 `local_face_data`, Git 추적 제외 |
| `sse_heartbeat_interval_seconds` | SSE heartbeat 간격 | 기본 30 |
| `sse_reconnection_timeout_seconds` | SSE 재연결 타임아웃 | 기본 60 |
| `detection_event_max_detections_per_event` | 탐지 이벤트당 최대 탐지 수 | 기본 100 |
| `detection_event_stale_seconds` | 탐지 이벤트 stale 판정 기준 | 기본 300 |
| `snapshot_storage_bucket`, `_secure`, `_timeout_seconds` | 스냅샷 버킷 이름·TLS·타임아웃 | 접속 정보(`endpoint`·키)는 `.env.*`에 있다 |
| `llm_search_timeout_seconds` | 계획 생성 타임아웃 | 기본 20. `0 < x <= 120`. 생성은 조회보다 느리다 |
| `llm_search_max_span_days` | 조회 기간 상한 | 기본 7. 넘으면 거절하지 않고 줄인 뒤 응답에 알린다 |
| `llm_search_scan_limit` | 카메라 한 대에서 한 번에 읽는 탐지 이벤트 수 | 기본 500. 걸리면 응답의 `truncated`가 참이 된다 |

학생 식별·상태 판정에 필요한 설정(`IDENTITY_CONFIDENCE_THRESHOLD`,
`ABSENCE_GRACE_PERIOD_SECONDS` 등)은 아직 없다. 목록은
[MVP 명세의 설정](../../docs/specs/student-monitoring-mvp.md#설정-예정)에 있다.

환경변수·yml의 저장·명명 규칙은
[환경변수 규칙](../../docs/conventions/environment-convention.md)을 따른다.
실제 비밀값과 `.env.local`/`.env.dev`/`.env.prod`는 커밋하지 않는다.

## 얼굴 등록 API와 화면

- 화면: `/students/{student_id}/face-enrollment`
- 세션 생성: `POST /api/v1/students/{student_id}/face-enrollments`
- 상태 조회·취소: `GET`, `DELETE /api/v1/face-enrollments/{enrollment_id}`
- 실시간 프레임: `WS /api/v1/face-enrollments/{enrollment_id}/frames`
- 프로필 조회·삭제: `GET`, `DELETE /api/v1/students/{student_id}/face-profile`

현재 local 구현은 SCRFD 중앙 분석 서비스와 메모리 메타데이터 저장소를 사용한다.
실제 얼굴 원본을 운영에서 처리하려면 관리자 인증과 MongoDB·MinIO 접근 통제가 선행돼야 한다.

수집된 JPEG를 local에서 직접 확인하려면 `config/settings.yml`에서
`face_local_sample_storage_enabled: true`로 바꾼다. 완료된 세션은
`local_face_data/<YYYYMMDD-HHMMSS-student_id>/`에 카메라와 같은 해상도의 JPEG가
`originals/<student_id>_<pose>_<sequence>.jpg` 형식으로 남는다. 실제 촬영본 120장이
완료되면 `augmented/`에 교실 카메라의 저해상도·조명·흐림·압축·미세 회전을 모사한
파생본 180장을 만들고, `manifest.json`에 원본·파생 관계와 적용 파라미터를 기록한다.
증강본은 실제 촬영 진행률이나 프로필의 `sample_count`에 포함하지 않는다. 타원 바깥은
분석 전에 어두운 단색으로 제거되며 취소·연결 중단 세션 폴더는 즉시 삭제된다. 메뉴의 `demo-student`는
학생 원장·선택 화면이 구현되기 전의 local 테스트용 ID이며, 실제 학생 ID는
`/students/{student_id}/face-enrollment` 경로로 전달된다.

## 자연어 탐지 검색

- 화면: `/llm-search` (질문은 쿼리스트링 `q`)
- API: `POST /api/v1/llm-searches` — 본문 `{"question": "...", "limit": 20}`

책임 분리는
[결정 0016](../../docs/architecture/decisions.md#0016--자연어-검색에서-llm은-계획만-만들고-검증조회는-fastapi가-소유한다)이
정한다. **LLM은 질문을 검색 조건 JSON으로 바꾸는 데서 끝나고 DB에 접근하지 않는다.**
서버가 그 JSON을 검증한 뒤 기존 탐지 이벤트 저장소를 조회한다. 검증을 통과한 계획은
응답의 `plan`과 화면에 그대로 노출되므로 어떻게 해석되었는지 확인할 수 있다.

기본 `LLM_SEARCH_MODE=stub`은 LLM 없이 "오늘 하루 전체, 대상 지정 없음" 계획을
돌려준다. 계약과 화면 상태를 GPU 서버 없이 확인하기 위한 대역이며 자연어를 해석하지
않는다. 실제 해석은 `llama`로 바꾸고 `.docker/compose.llm.yml`의 llama-server를
띄운 뒤에 동작한다.

결과는 **탐지 인원이 직전과 달라진 시점만** 남긴다. 탐지 이벤트는 카메라당 프레임마다
한 건이라 전부 보여주면 거의 같은 줄이 수천 개가 된다.

주의할 점 두 가지가 있다.

- **탐지 이벤트를 수집하는 코드가 아직 연결되지 않았다.** `/internal/inference/events`를
  호출하는 worker 코드가 없어 저장소가 비어 있고, 따라서 검색 결과도 비어 있다.
  화면에 데이터를 넣어 보려면 그 엔드포인트로 이벤트를 직접 POST한다.
- **"누가"에는 아직 답하지 못한다.** `student_id`를 채우는 얼굴 인식이 구현되지
  않았다. 응답과 화면은 값을 그대로 통과시켜 식별된 학생이 있으면 보여주고, 없으면
  "식별 미연동"으로 표시한다. 인식이 붙으면 코드 변경 없이 이름이 나타난다.

## 검증

```bash
cd webapps/fastapi
python -m ruff check app tests
python -m ruff format --check app tests
python -m mypy app tests
python -m pytest -q
```

기본 테스트는 memory mode와 대역 저장소를 사용해 외부 서비스 없이 실행된다. MongoDB 통합
테스트는 `TEST_DATABASE_URL`이 없으면 skip한다. 실제 MongoDB를 검증할 때는 URL 경로에
`test_`로 시작하는 database 이름을 넣고 실행한다. fixture는 database나 collection을
삭제하지 않는다.

```bash
python -m pytest -q -m mongodb
```

## 관련 문서

- [학생 모니터링 MVP 명세](../../docs/specs/student-monitoring-mvp.md) — 앞으로 만들 것
- [FastAPI 에이전트 규칙](../../docs/agents/fastapi-agent.md)
- [API 규칙](../../docs/conventions/api-convention.md)
- [테스트 배치 기준](./tests/README.md)
- [아키텍처](../../docs/architecture/README.md)
- [결정 기록](../../docs/architecture/decisions.md)
