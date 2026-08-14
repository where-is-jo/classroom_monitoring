# fastapi

관리자가 사용하는 강의실 모니터링 화면과 HTTP API를 제공한다.
이 저장소에서 실행 가능한 웹 서비스이자 브라우저의 단일 진입점이다.

**이 서비스가 학생 상태 판정을 소유한다.** 탐지 결과를 `PRESENT` / `WRONG_SEAT` /
`ABSENT`로 바꾸는 규칙은 여기 있고, `worker`나 `deeplearning`에 두지 않는다
([결정 0008](../../docs/architecture/decisions.md#0008--학생-상태-판정을-rule-engine으로-분리하고-fastapi가-소유한다)).

> **현재 범위는 강의실 좌석 현황, 실시간 모니터링, 자연어 검색과 학생 등록 프로토타입이다.**
> 학생 정보의 DB 저장, 지정 좌석, 학생 상태 판정은 **아직 구현되지 않았다.** 얼굴 데이터 수집은 구현되어 있다.
> 현재 좌석 상태는 "자리가 찼는지"를 뜻하며 "누가 앉았는지"가 아니다.
> 앞으로 만들 도메인과 계약은 [학생 모니터링 MVP 명세](../../docs/specs/student-monitoring-mvp.md)에 있다.

## 빠른 시작

Python 3.12 환경에서 실행한다.

```bash
cd webapps/fastapi
python -m pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --reload --port 8001
```

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

`.env`에서 `DEMO_MODE_ENABLED=true`로 바꾸면 개인정보 없는 합성 영상 source와 고정
검색 catalog가 붙고, memory 저장소에 강의실·좌석 fixture가 멱등하게 채워진다.
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
| `/monitoring` | 영상 source 목록과 연결 상태. demo가 꺼져 있으면 빈 상태 |
| `/video-search` | 한국어 문장과 강의실·기간·결과 수 조건으로 검색. demo가 꺼져 있으면 빈 결과 |

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
| `GET` | `/api/v1/video-streams` | 영상 source 목록. demo + 실제 source |
| `GET` | `/api/v1/video-streams/{stream_id}` | 한 source의 상태 |
| `POST` | `/api/v1/video-streams/{stream_id}/playback-sessions` | 실제·enabled·WebRTC source의 재생 세션 생성 (결정 0014) |
| `POST` | `/api/v1/video-streams/{stream_id}/playback-sessions/{session_id}` | WHEP offer signaling (MediaMTX proxy) |
| `PATCH` | `/api/v1/video-streams/{stream_id}/playback-sessions/{session_id}` | ACTIVE 세션 재협상 signaling |
| `DELETE` | `/api/v1/video-streams/{stream_id}/playback-sessions/{session_id}` | WHEP resource 종료·세션 CLOSED (idempotent) |
| `GET` | `/api/v1/video-streams/{stream_id}/detections` | 카메라별 탐지 이벤트 조회 |
| `GET` | `/api/v1/video-streams/{stream_id}/detection-events` | SSE 실시간 탐지 이벤트 구독 |
| `GET` | `/api/v1/video-segments` | 영상 세그먼트 메타데이터 조회 |
| `POST` | `/api/v1/video-searches` | 부작용 없는 검색 실행 |
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

## 환경변수

전체 이름과 기본값은 [`.env.example`](./.env.example)이 기준이다.

| 이름 | 용도 | 제약 |
| --- | --- | --- |
| `APP_ENV` | 실행 환경 | `local` / `dev` / `prod` |
| `DATABASE_MODE` | 저장소 종류 | `memory` / `mongodb`. memory는 `local` 전용 |
| `DATABASE_URL`, `DATABASE_NAME` | MongoDB 접속 정보 | mongodb mode에서 필수. URL은 비밀값 |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | 연결 타임아웃 | 기본 5. `0 < x <= 60` |
| `DEMO_MODE_ENABLED` | 합성 영상·검색 demo | 기본 false. `local`/`dev` 전용. prod 금지 |
| `SEAT_OCCUPANCY_CONFIDENCE_THRESHOLD` | 이 값 미만의 좌석 관측은 `UNKNOWN` | 기본 0.6. `0 <= x <= 1` |
| `PAGE_SIZE_DEFAULT`, `PAGE_SIZE_MAX` | 목록 페이지 크기 | 최대 200 |
| `FACE_ENROLLMENT_REQUIRED_SAMPLES` | 얼굴 등록 완료 최소 실제 촬영 유효본 수 | 기본 120 |
| `FACE_ENROLLMENT_AUGMENTED_SAMPLES` | local 데이터셋 완료 시 생성할 증강본 수 | 기본 180 |
| `FACE_POSE_*_QUOTA` | 방향별 실제 촬영 유효본 수 | 합계가 전체 필수 수와 같아야 함. 기본값은 정면 32, 좌·우 각 24, 위·아래 각 20장 |
| `FACE_*` 품질 설정 | 탐지·크기·roll·흐림·밝기·landmark·가림·중복·pose 기준 | 코드가 아닌 환경변수로 조정 |
| `FACE_MOTION_SPEED_DPS_MAX` | 프레임 간 허용 머리 각속도 | 기본 220도/초. 초과 프레임은 저장하지 않음 |
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
| `TEST_DATABASE_URL` | 선택적 MongoDB 통합 테스트용 | database 이름이 `test_`로 시작해야 한다 |

학생 식별·상태 판정에 필요한 설정(`IDENTITY_CONFIDENCE_THRESHOLD`,
`ABSENCE_GRACE_PERIOD_SECONDS` 등)은 아직 없다. 목록은
[MVP 명세의 설정](../../docs/specs/student-monitoring-mvp.md#설정-예정)에 있다.

환경변수의 저장·명명 규칙은
[환경변수 규칙](../../docs/conventions/environment-convention.md)을 따른다.
실제 비밀값과 `.env`는 커밋하지 않는다.

## 얼굴 등록 API와 화면

- 화면: `/students/{student_id}/face-enrollment`
- 세션 생성: `POST /api/v1/students/{student_id}/face-enrollments`
- 상태 조회·취소: `GET`, `DELETE /api/v1/face-enrollments/{enrollment_id}`
- 실시간 프레임: `WS /api/v1/face-enrollments/{enrollment_id}/frames`
- 프로필 조회·삭제: `GET`, `DELETE /api/v1/students/{student_id}/face-profile`

현재 local 구현은 SCRFD 중앙 분석 서비스와 메모리 메타데이터 저장소를 사용한다.
실제 얼굴 원본을 운영에서 처리하려면 관리자 인증과 MongoDB·MinIO 접근 통제가 선행돼야 한다.

수집된 JPEG를 local에서 직접 확인하려면 `.env`에
`FACE_LOCAL_SAMPLE_STORAGE_ENABLED=true`를 설정한다. 완료된 세션은
`local_face_data/<YYYYMMDD-HHMMSS-student_id>/`에 카메라와 같은 해상도의 JPEG가
`originals/<student_id>_<pose>_<sequence>.jpg` 형식으로 남는다. 실제 촬영본 120장이
완료되면 `augmented/`에 교실 카메라의 저해상도·조명·흐림·압축·미세 회전을 모사한
파생본 180장을 만들고, `manifest.json`에 원본·파생 관계와 적용 파라미터를 기록한다.
증강본은 실제 촬영 진행률이나 프로필의 `sample_count`에 포함하지 않는다. 타원 바깥은
분석 전에 어두운 단색으로 제거되며 취소·연결 중단 세션 폴더는 즉시 삭제된다. 메뉴의 `demo-student`는
학생 원장·선택 화면이 구현되기 전의 local 테스트용 ID이며, 실제 학생 ID는
`/students/{student_id}/face-enrollment` 경로로 전달된다.

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
