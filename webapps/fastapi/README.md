# fastapi

관리자가 사용하는 강의실 모니터링 화면과 HTTP API를 제공한다.
이 저장소에서 실행 가능한 웹 서비스이자 브라우저의 단일 진입점이다.

**이 서비스가 학생 상태 판정을 소유한다.** 현재 탐지 결과를 `PRESENT` / `WRONG_SEAT` /
`UNKNOWN`으로 바꾸는 규칙은 여기 있고, `worker`나 `deeplearning`에 두지 않는다.
시간표·유예 시간·카메라 건강도가 필요한 `ABSENT`도 후속 구현 시 이 서비스가 소유한다
([결정 0008](../../docs/architecture/decisions.md#0008--학생-상태-판정을-rule-engine으로-분리하고-fastapi가-소유한다)).

> **현재 범위는 강의실 좌석 현황, 실시간 모니터링, 자연어 검색, 학생 등록, 입구 얼굴
> 관측 이력과 학생 상태 연동이다.**
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

### 얼굴 등록을 확인할 때

SCRFD·MediaPipe 분석을 담당하는 `deeplearning`이 떠 있어야 한다.
**컨테이너로 띄운다**([결정 0022](../../docs/architecture/decisions.md)).

**그 컨테이너는 GPU 서버에 있다**([결정 0026](../../docs/architecture/decisions.md#0026--백엔드를-개인-pc에-두고-gpu가-필요한-것만-gpu-서버에-남긴다)).
가중치 346MB가 거기 있고, 개인 PC에서 따로 띄우지 않는다. 소스로 fastapi를 돌릴 때도
Tailscale 주소로 부른다.

```bash
# GPU 서버에서 (이미 떠 있으면 생략)
docker compose -f .docker/compose.main.dev.gpu.yml up -d deeplearning
```

그리고 `webapps/fastapi/.env.local`에서 분석기를 실제 서비스로 돌린다.

```
FACE_ANALYZER_MODE=http
FACE_ANALYZER_URL=http://100.85.0.72:18100
```

**`http://deeplearning:8100`은 이제 닿지 않는다.** 같은 compose network가 아니다.

**`synthetic`으로 두면 안 된다.** 대역 분석기는 이미지를 읽지 않고 호출 횟수에 따라
정해진 순서대로 자세를 돌려주므로, 가만히 정면만 봐도 등록이 완주된다. 검사가 있는
것처럼 보이지만 아무것도 검사하지 않는 상태다.

> 소스에서 두 서버를 직접 띄우던 `run-face-enrollment.ps1`은 삭제했다. 실행 수단이
> 둘이면 한쪽만 고쳐지고 다른 쪽이 낡는다 — 그 스크립트는 `FACE_ANALYZER_MODE`를
> 주입하지 않아, 분석 서버를 띄워 놓고 정작 `synthetic`으로 도는 상태였다.

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
| `/classrooms/{id}/seats` | 좌석 배치 관리와 좌석-학생 지정·해제 |
| `/classrooms/{id}/seats/create` | 좌석 추가 (배치도 위치 비율 입력) |
| `/classrooms/{id}/seats/{seat_id}/edit` | 좌석 수정 |
| `/roi-connections` | 강의실 카메라의 **현재 화면을 캡처**해 그 위에 좌석별 다각형 ROI를 그리고 MongoDB에 저장. 이미 등록된 ROI를 좌석 이름과 함께 겹쳐 보여주고, 클릭해서 다시 그리거나 지울 수 있다. ROI 자동 생성 경로가 둘이다 — **탐지 기록에서 사람이 앉았던 자리를 찾거나**([결정 0041](../../docs/architecture/decisions.md#0041--좌석-roi를-탐지-밀도에서-찾고-좌석-지정은-사람이-한다)), **좌석 구역 네 모서리를 찍어 행·열 격자를 사영한다**([결정 0039](../../docs/architecture/decisions.md#0039--좌석-roi-자동-생성을-좌석-격자와-네-모서리-호모그래피로-한다)). 둘 다 미리보기 → 저장 → 확정을 거치며 확정 전에는 좌석 판정에 쓰이지 않는다. 캡처에는 `CAMERA_RTSP_SOURCES`가 필요하다([결정 0031](../../docs/architecture/decisions.md#0031--roi-기준-화면을-fastapi가-rtsp에서-직접-캡처한다)) |
| `/identity-handover` | 입구 얼굴 신원을 CCTV 사람 track에 넘길 **CCTV 문 사각형 ROI** 관리. 현재 CCTV 화면을 캡처해 저장 영역을 겹쳐 보고 다시 그리며, 저장값은 worker가 주기적으로 읽어 재시작 없이 반영한다 |
| `/students` | 학생 목록·등록과 얼굴 등록 상태 관리 |
| `/monitoring` | 영상 source 목록과 연결 상태. demo가 꺼져 있으면 빈 상태 |
| `/llm-search` | **자연어 탐지 검색.** 질문을 LLM이 검색 조건으로 바꾸고 서버가 검증한 뒤 탐지 기록을 찾는다. 탐지 인원이 바뀐 시점만 보여준다. **`LLM_SEARCH_MODE=disabled`(기본값)에서는 검색 폼 없이 안내만 나온다** — GPU가 있는 환경에서만 동작한다 |

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
| `GET` | `/api/v1/classrooms/{classroom_id}/student-states` | 지정 학생 전체의 저장된 상태 조회. 여기서 판정하지 않는다([결정 0032](../../docs/architecture/decisions.md#0032--학생-상태-판정을-좌석-근거-하나에서-파생시키고-수신-시점에-저장한다)) |
| `GET` | `/api/v1/classrooms/{classroom_id}/students/{student_id}/state-history` | 한 학생의 상태 전이 이력을 최신순으로 조회 |
| `GET` | `/api/v1/classrooms/{classroom_id}/student-state-events` | 강의실별 학생 상태 SSE 변경분 구독 |
| `PUT` | `/api/v1/classrooms/{classroom_id}/seats/{seat_id}/assignment` | 좌석에 학생 지정 (같은 강의실 내 이동·멱등) |
| `DELETE` | `/api/v1/classrooms/{classroom_id}/seats/{seat_id}/assignment` | 좌석-학생 지정 해제 |
| `GET` | `/api/v1/classrooms/{classroom_id}/seat-assignments` | 강의실의 좌석-학생 지정 현황 |
| `POST` | `/api/v1/students` | 학생 인적사항 저장. 생성 리소스는 `Location` 헤더로 반환 |
| `POST` | `/api/v1/classrooms/{classroom_id}/roi-reference-image?camera_id=...` | 카메라별 ROI 기준 JPEG·PNG 이미지를 메모리에 첨부 |
| `POST` | `/api/v1/classrooms/{classroom_id}/roi-reference-image/capture?camera_id=...` | **카메라의 현재 화면을 RTSP로 잡아 ROI 기준 이미지로 저장.** 실측 1.5~4.2초가 걸리고, 실패는 502 `CAMERA_FRAME_UNAVAILABLE`이다 |
| `GET` | `/api/v1/classrooms/{classroom_id}/roi-reference-image?camera_id=...` | 카메라별 현재 ROI 기준 이미지 조회 |
| `GET` | `/api/v1/classrooms/{classroom_id}/roi-connections?camera_id=...` | 카메라·좌석별 ROI 조회. query를 생략하면 legacy 포함 전체 조회 |
| `POST` | `/api/v1/classrooms/{classroom_id}/roi-connections/auto` | 좌석 구역 네 모서리에서 좌석 행·열 격자를 사영해 좌석마다 ROI를 만든다. `dry_run=true`면 계산만 하고 저장하지 않는다. 저장분은 `auto_generated=true`라 확정 전까지 좌석 판정에서 빠진다 |
| `POST` | `/api/v1/classrooms/{classroom_id}/roi-connections/auto/from-detections` | **탐지 기록에서 사람이 앉았던 자리를 찾는다.** 저장하지 않고 자리 목록만 돌려준다 — 어느 자리가 몇 번 좌석인지는 카메라가 알 수 없어 관리자가 지정한다([결정 0041](../../docs/architecture/decisions.md#0041--좌석-roi를-탐지-밀도에서-찾고-좌석-지정은-사람이-한다)) |
| `POST` | `/api/v1/classrooms/{classroom_id}/roi-connections/auto/from-detections/apply` | 관리자가 좌석을 지정한 자리를 ROI로 저장한다. 좌표의 근거가 캡처 화면이 아니라 탐지 기록이라 `reference_image_revision=0`으로 저장돼 **재시작·재캡처에도 남는다** |
| `POST` | `/api/v1/classrooms/{classroom_id}/roi-connections/auto/confirm` | 자동 생성분을 확정해 좌석 판정에 넣는다. 기준 화면이 바뀐 것은 확정하지 않고 `stale_count`로 알린다 |
| `PUT` | `/api/v1/classrooms/{classroom_id}/seats/{seat_id}/roi-connection` | body의 `camera_id` 좌표계에 좌석 ROI를 저장 |
| `DELETE` | `/api/v1/classrooms/{classroom_id}/seats/{seat_id}/roi-connection?camera_id=...` | 좌석 하나의 ROI를 삭제. 그 카메라는 해당 좌석을 관측하지 않게 된다. 지울 것이 없으면 404 |
| `PUT` | `/api/v1/classrooms/{classroom_id}/roi-connection` | `camera_id`·좌석·legacy 학생 연결과 ROI를 기준 이미지 없이 저장(`revision=0`). **화면은 더 이상 이 경로를 쓰지 않는다** |
| `GET` | `/api/v1/classrooms/{classroom_id}/identity-handover-routes` | 강의실의 입구→CCTV 인계 route와 저장 좌표 조회 |
| `PUT` | `/api/v1/classrooms/{classroom_id}/identity-handover-routes/{classroom_camera_id}` | `IDENTITY_ONLY` 입구 카메라와 `SEAT_JUDGING` CCTV 사이의 정규화 사각형 문 ROI 저장 |
| `DELETE` | `/api/v1/classrooms/{classroom_id}/identity-handover-routes/{classroom_camera_id}` | CCTV 인계 route 삭제. 다음 worker 갱신부터 새 인계를 중단 |
| `POST` | `/api/v1/classrooms/{classroom_id}/identity-handover-reference-image/capture?camera_id=...` | CCTV 현재 RTSP 프레임을 인계 ROI 전용 기준 이미지로 캡처. 좌석 ROI 기준 이미지 revision에는 영향을 주지 않는다 |
| `GET` | `/api/v1/classrooms/{classroom_id}/identity-handover-reference-image?camera_id=...` | 캡처한 인계 ROI 기준 JPEG 조회 |
| `GET` | `/api/v1/video-streams` | 영상 source 목록. demo + 실제 source |
| `POST` | `/api/v1/video-streams` | 실제 카메라 source 등록. MongoDB mode에는 seed가 없어 이 경로로 넣으며, 입구 카메라는 `role=IDENTITY_ONLY`로 등록한다 |
| `GET` | `/api/v1/video-streams/{stream_id}` | 한 source의 상태. 목록의 `id`로 조회하며 `camera_id`도 받는다 |
| `POST` | `/api/v1/video-streams/{stream_id}/playback-sessions` | 실제·enabled·WebRTC source의 재생 세션 생성 (결정 0014) |
| `POST` | `/api/v1/video-streams/{stream_id}/playback-sessions/{session_id}` | WHEP offer signaling (MediaMTX proxy) |
| `PATCH` | `/api/v1/video-streams/{stream_id}/playback-sessions/{session_id}` | ACTIVE 세션 재협상 signaling |
| `DELETE` | `/api/v1/video-streams/{stream_id}/playback-sessions/{session_id}` | WHEP resource 종료·세션 CLOSED (idempotent) |
| `GET` | `/api/v1/video-streams/{stream_id}/detections` | 카메라별 탐지 이벤트 조회 |
| `GET` | `/api/v1/video-streams/{stream_id}/detection-events` | SSE 실시간 탐지 이벤트 구독 |
| `GET` | `/api/v1/video-streams/{stream_id}/entry-identity-events` | 입구 얼굴 관측 이벤트 조회. 상태·학생·시간·limit·cursor 필터, 기본 최신 50건 |
| `GET` | `/api/v1/video-streams/{stream_id}/entry-identity-events/stream` | 활성 `IDENTITY_ONLY` 입구 카메라의 얼굴 관측 SSE 구독. `entry-identity` 이벤트로 bbox·화면용 이름·분석 상태를 전달 |
| `GET` | `/api/v1/video-segments` | 영상 세그먼트 메타데이터 조회 |
| `POST` | `/api/v1/llm-searches` | 자연어 질문을 검증된 조건으로 바꿔 탐지 기록 검색. 해석한 계획을 응답에 함께 싣는다 |
| `POST` | `/internal/inference/events` | worker 탐지 이벤트 수신 (멱등) |
| `POST` | `/internal/entry-identity-events` | worker 입구 얼굴 관측 이벤트 수신. 신규 201, 동일 재전송 200, 충돌 409 |
| `GET` | `/internal/identity-handover-routes` | 활성 강의실의 인계 route를 worker 형식으로 조회. worker가 기본 5초마다 갱신 |
| `POST` | `/internal/video-segments` | worker 영상 세그먼트 수신 (멱등) |
| `GET` | `/health` | 프로세스 기동 상태 |
| `GET` | `/health/ready` | 현재 저장소 준비 상태 |

**내부 쓰기 API가 있다.** worker가 CCTV 탐지 이벤트(`/internal/inference/events`), 입구
얼굴 관측 이벤트(`/internal/entry-identity-events`)와 영상 세그먼트
(`/internal/video-segments`)를 보낼 수 있다. 로그인, 사용자 관리,
알림, 관리자 대시보드는 현재 구현되어 있지 않다.

실시간 모니터링 화면은 카메라 역할별 채널을 분리한다. `IDENTITY_ONLY` 입구캠은
얼굴 관측 SSE에서 얼굴 bbox와 화면용 라벨을 받고, `SEAT_JUDGING` CCTV는 기존 객체
탐지 SSE를 그대로 사용한다. 등록 학생은 활성 학생 조회에 성공할 때만 이름을 표시하며,
그 외에는 `등록 얼굴`·`미등록 얼굴`·`판정 보류`로 표시한다. 실시간 응답에는
`student_id`·학번·유사도·embedding·얼굴 이미지 등 내부 식별자나 생체 원본을 싣지 않는다.

### 좌석 상태 표기

| 저장 값 | 화면 문구 | 의미 |
| --- | --- | --- |
| `OCCUPIED` | 재석 | 좌석 점유 관측이 confidence 기준 이상이다. **학생 신원을 뜻하지 않는다** |
| `VACANT` | 부재 | 좌석 비점유 관측이 confidence 기준 이상이다. **지정 학생의 부재를 확정하지 않는다** |
| `UNKNOWN` | 확인 필요 | 미관측, 낮은 confidence 또는 신뢰할 수 없는 관측이다 |

색만으로 상태를 구분하지 않고 문구와 기호를 함께 쓴다.
**관측 실패나 영상 없음을 `VACANT`로 바꾸지 않는다.**

좌석 점유도 학생 상태와 같은 카메라별 ROI로 판정한다
([결정 0020](../../docs/architecture/decisions.md#0020--좌석-위치-판정의-정본을-roi-하나로-통일한다)).
**관측 대상은 그 카메라에 ROI가 등록된 좌석뿐이다** — 강의실을 나눠 보는 구성에서 다른
카메라 담당 좌석을 덮어쓰지 않기 위해서다. 그래서 **ROI를 등록하지 않은 카메라의 좌석은
계속 `UNKNOWN`으로 남는다.** `seat.geometry`는 배치도를 그리는 좌표이며 판정에 쓰지 않는다.

### 합성 데모

`APP_ENV=local|dev`와 `DEMO_MODE_ENABLED=true`를 함께 설정할 때만 강의실·좌석 seed를
넣는다(`app/demo_seed.py`). 개인정보가 없는 고정 fixture다.

**데모 영상 검색(`/video-search`)과 합성 영상 catalog는 걷어냈다.** 실제 카메라가
붙은 뒤로는 쓰이지 않았고, `/api/v1/video-streams`가 합성 source를 실제 source와 섞어
돌려주고 있었다. 지금은 등록된 실제 카메라만 나온다.

영상 원본은 저장하지 않는다. 등록된 카메라가 없으면 `/monitoring`은 404가 아니라
빈 상태를 반환한다.

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
├─ students/            학생 원장 등록·조회와 memory/MongoDB 저장소
├─ video_monitoring/    영상 source 목록과 검색 (local/dev 합성 catalog)
├─ face_enrollment/     능동형 얼굴 등록 세션·품질·pose 완료 판정
├─ entry_identity_events/ 입구 얼굴 관측 이벤트 저장·관리 조회
├─ student_monitoring/  탐지 이벤트 수신·SSE·영상 세그먼트 메타데이터
├─ llm_search/          자연어 질문 → 검증된 검색 조건 → 탐지 기록 조회
├─ shared/              설정, 저장소 조립, 공통 오류·템플릿·스키마
└─ demo_seed.py         demo fixture 멱등 생성

templates/              기능별 Jinja2 화면
static/                 CSS와 화면 보조 JavaScript
api-spec/               구현 전 API 설계 명세 JSON
tests/                  단위·API·템플릿·선택적 MongoDB 통합 테스트
```

`face_enrollment`는 memory
저장소와 SCRFD 중앙 분석 HTTP 어댑터를 사용하는 local MVP가 구현됐다.
`student_monitoring` 도메인이 구현되어 탐지 이벤트 수신·MongoDB 저장, 학생 상태 판정과
REST·SSE 발행이 동작한다. 탐지 SSE의 bbox 라벨은 FastAPI가 확인한 활성 학생 이름만
사용하고 그 외에는 `사람`으로 표시한다. 학생 상태 SSE는 현재 in-memory broadcaster를
사용하므로 단일 FastAPI 프로세스에서만 전달되며 replay와 다중 프로세스 fan-out은 지원하지
않는다.

**판정 구조는 [결정 0032](../../docs/architecture/decisions.md#0032--학생-상태-판정을-좌석-근거-하나에서-파생시키고-수신-시점에-저장한다)를
따른다.** 요약하면 이렇다.

```text
탐지 이벤트 → SeatEvidence(좌석별 근거) ┬→ 좌석 점유 (classrooms)
                                        └→ 학생 상태 (state_rules) → 저장 + 이력 + SSE
```

- 좌석 점유와 학생 상태가 **같은 근거**에서 갈라진다. 두 화면이 어긋날 수 없다.
- 판정 규칙은 `app/student_monitoring/state_rules.py`의 **순수 함수**다. 저장소·HTTP·
  시계에 의존하지 않으므로 입력만 놓고 판정을 재현할 수 있다.
- **판정은 탐지 이벤트를 받을 때만 한다.** 조회는 저장된 값을 읽기만 하고, 근거가 오래된
  판정은 화면에서 `UNKNOWN`으로 가릴 뿐 저장된 값을 바꾸지 않는다.
- 상태는 `PRESENT` / `WRONG_SEAT` / `IN_CLASSROOM` / `ABSENT` / `UNKNOWN` 다섯이고,
  모든 판정에 근거 코드(`StudentStateReason`)가 붙는다. `UNKNOWN` 하나로는 "좌석을 못
  봤다"와 "누군가 있는데 누군지 모른다"가 구분되지 않기 때문이다.
- **`ABSENT`는 지정 좌석이 비어 있는 것을 유예 시간 동안 계속 본 경우에만 나온다.**
  카메라가 죽거나 ROI가 없어 관측이 끊기면 `UNKNOWN`이다 — 미관측은 부재가 아니다.

입구 SCRFD·ArcFace 결과는 좌석 탐지와 분리된 얼굴 관측 이벤트로 저장되고, worker가
CCTV 문 ROI의 유일한 ByteTrack에 인계한 `student_id`·`identity_confidence`만 학생 상태
판정으로 들어온다. 세부 계약은
[worker/inference/MODEL_INTEGRATION.md](../../worker/inference/MODEL_INTEGRATION.md)를 본다.
`students`는 학생 인적사항을 memory 또는 MongoDB 저장소에 영속화한다. 학생 등록은
`/students`의 등록 dialog가 `POST /api/v1/students` 계약을 사용해 처리한다. 좌석 화면은
등록된 학생을 선택한 뒤 `PUT /api/v1/classrooms/{classroom_id}/seats/{seat_id}/assignment`로
지정한다. 학생 관리 화면의 얼굴 등록 모달은 기존 얼굴 등록 API를 재사용하며 동의 확인,
촬영, 완료 순서로 진행된다.
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
| `SEAT_OCCUPANCY_CONFIDENCE_THRESHOLD` | 이 값 미만의 좌석 관측은 `UNKNOWN` | 기본 0.3(실측으로 정했다). `0 <= x <= 1` |
| `SEAT_OCCUPANCY_HOLD_SECONDS` | 마지막 점유 관측 뒤 좌석을 점유로 붙들어 두는 시간 | 기본 5초(실측으로 정했다). 0이면 붙들지 않는다 |
| `PAGE_SIZE_DEFAULT`, `PAGE_SIZE_MAX` | 목록 페이지 크기 | 최대 200 |
| `ROI_REFERENCE_IMAGE_MAX_BYTES` | ROI 임시 기준 이미지 업로드 제한 | 기본 5MB, 최대 20MB |
| `CAMERA_RTSP_SOURCES` | ROI 기준 화면을 캡처할 카메라 접속 정보 | `<카메라 식별자>=<RTSP URL>`을 쉼표로 잇는다. worker의 `STREAM_SOURCES`와 같은 형식. **비밀값이다.** 비우면 캡처만 꺼진다 |
| `CAMERA_FRAME_CAPTURE_TIMEOUT_SECONDS` | 캡처 한 번의 제한 시간 | 기본 15초 |
| `FACE_ENROLLMENT_REQUIRED_SAMPLES` | 얼굴 등록 완료 최소 실제 촬영 유효본 수 | 기본 120 |
| `FACE_ENROLLMENT_AUGMENTED_SAMPLES` | local 데이터셋 완료 시 생성할 증강본 수 | 기본 180 |
| `FACE_POSE_*_QUOTA` | 방향별 실제 촬영 유효본 수 | 합계가 전체 필수 수와 같아야 함. 기본값은 정면 32, 좌·우 각 24, 위·아래 각 20장 |
| `FACE_*` 품질 설정 | 탐지·크기·roll·흐림·밝기·landmark·가림·중복·pose 기준 | 코드가 아닌 환경변수로 조정 |
| `FACE_MOTION_SPEED_DPS_MAX` | 프레임 간 허용 머리 각속도 | 기본 220도/초. 초과 프레임은 저장하지 않음 |
| `FACE_PITCH_DOWN_DEGREES` | 아래 방향으로 분류하는 최소 pitch | 기본 5도. 위 방향 기준과 별도 적용 |
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
| `LLM_SEARCH_MODE` | 자연어 검색의 계획 생성 방식 | 기본 `disabled`(기능 차단). `stub`은 질문을 읽지 않고 "오늘 하루"만 돌려주는 **테스트 전용** 대역, `llama`는 llama-server 호출. [결정 0021](../../docs/architecture/decisions.md#0021--자연어-검색을-gpu-서버에서만-켜고-그-밖의-환경에서는-기능을-끈다) |
| `LLM_SEARCH_URL` | llama-server의 OpenAI 호환 API 주소 | `llama` mode에서 필수. 기본 `http://127.0.0.1:8008` |
| `LLM_SEARCH_MODEL` | 요청에 넣을 모델 이름 | llama-server의 `LLAMA_ARG_ALIAS`와 같아야 한다. 기본 `gemma` |
| `TEST_DATABASE_URL` | 선택적 MongoDB 통합 테스트용 | database 이름이 `test_`로 시작해야 한다 |

### `config/settings.yml`

| 이름 | 용도 | 제약 |
| --- | --- | --- |
| `database_connect_timeout_seconds` | 연결 타임아웃 | 기본 5. `0 < x <= 60` |
| `demo_mode_enabled` | 합성 영상·검색 demo | 기본 false. `local`/`dev` 전용. prod 금지 |
| `seat_occupancy_confidence_threshold` | 이 값 미만의 좌석 관측·학생 사람 탐지는 `UNKNOWN` | **기본 0.3.** 3A컴퓨터실 CCTV 실측으로 정했다 — 이전 0.6은 실제 6명 중 1명만 통과시켰다. 근거는 `config/settings.yml` 주석에 있다 |
| `seat_occupancy_hold_seconds` | 마지막 점유 관측 뒤 좌석을 점유로 유지하는 시간 | **기본 5초.** 앉은 사람도 프레임마다 잡히지는 않아 이것이 없으면 좌석이 몇 초마다 깜빡인다. 정책 비교와 근거는 `config/settings.yml` 주석에 있다 |
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
| `student_identity_confidence_threshold` | 학생 상태 판정에 사용할 최소 식별 신뢰도 | 기본 0.5. `0 <= x <= 1` |
| `student_identity_hold_seconds` | 마지막 식별 뒤 직전 판정을 이어받는 시간 | 기본 15. **실측 근거 없는 기본값이다** |
| `student_absent_grace_seconds` | 지정 좌석이 비어 있는 것을 이만큼 계속 본 뒤 `ABSENT` | 기본 300. **팀 합의값이 아니다** |
| `student_state_history_limit` | 상태 전이 이력 조회 개수 | 기본 50. 최대 200 |
| `entry_identity_event_retention_days` | 입구 얼굴 관측 메타데이터 보존일 | 기본 7일. MongoDB TTL과 memory 지연 만료에 동일 적용 |
| `snapshot_storage_bucket`, `_secure`, `_timeout_seconds` | 스냅샷 버킷 이름·TLS·타임아웃 | 접속 정보(`endpoint`·키)는 `.env.*`에 있다 |
| `llm_search_timeout_seconds` | 계획 생성 타임아웃 | 기본 20. `0 < x <= 120`. 생성은 조회보다 느리다. **호출 한 번의 상한이다** — 모델이 규격을 벗어나면 한 번 더 물으므로 최악의 경우 두 배까지 걸린다 |
| `llm_search_max_span_days` | 조회 기간 상한 | 기본 7. 넘으면 거절하지 않고 줄인 뒤 응답에 알린다 |
| `llm_search_scan_limit` | 카메라 한 대에서 한 번에 읽는 탐지 이벤트 수 | 기본 500. 걸리면 응답의 `truncated`가 참이 된다 |

최종 `ABSENT` 판정에 필요한 수업 시간표·유예 시간 설정은 아직 없다. 예정 목록은
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

계획 생성 지연과 **첫 시도 규격 위반율**은 지표로 나간다([지표 노출](#지표-노출)).
모델이 규격을 어기면 서비스가 한 번 더 묻는데, 사용자에게는 "조금 느리네"로만 보여서
로그로는 재시도가 잦아지는 것을 알아채기 어렵다.

기본 `LLM_SEARCH_MODE=stub`은 LLM 없이 "오늘 하루 전체, 대상 지정 없음" 계획을
돌려준다. 계약과 화면 상태를 GPU 서버 없이 확인하기 위한 대역이며 자연어를 해석하지
않는다. 실제 해석은 `llama`로 바꾸고 `.docker/compose.llm.yml`의 llama-server를
띄운 뒤에 동작한다.

결과는 **탐지 인원이 직전과 달라진 시점만** 남긴다. 탐지 이벤트는 카메라당 프레임마다
한 건이라 전부 보여주면 거의 같은 줄이 수천 개가 된다.

주의할 점 두 가지가 있다.

- worker `pipeline`에 `FASTAPI_URL`을 설정하면 탐지 이벤트가
  `/internal/inference/events`로 전달된다. 설정하지 않으면 로그만 남기므로 저장소와 검색
  결과가 비어 있는 것이 정상이다. 실제 영상 없이 확인할 때는
  [`worker/inference` 계약 fixture](../../worker/inference/MODEL_INTEGRATION.md)를 사용한다.
- `FACE_IDENTITY_URL`과 입구 카메라 ID를 설정한 worker는 deeplearning의 얼굴 식별
  결과로 `student_id`를 채운다. 문 영역·통과 시간 route가 설정되면 신원을 CCTV
  ByteTrack에 보수적으로 인계한다. route는 `/identity-handover`에서 관리하며 worker가
  `/internal/identity-handover-routes`를 주기적으로 읽는다. 조회 장애 때는 마지막 정상
  설정을 유지하고, 정상 응답이 빈 목록이면 새 인계를 끈다. 응답과 화면은 값을 그대로
  통과시켜 이름과 `track_id`를 보여주며, 미식별이면 "식별 미연동"으로 표시한다. 실제
  카메라에서는 얼굴 가중치·갤러리·문 영역·인계 시간 창을 별도로 검증해야 한다.

## 지표 노출

`METRICS_ENABLED`가 켜져 있으면(기본) 앱과 같은 포트에 `/metrics`를 연다.
**끄면 라우트 자체를 만들지 않는다** — 500이나 404를 돌려주는 경로를 남기면 "지표가
있는데 지금 실패한 것"과 "이 배포에는 없는 것"이 구분되지 않는다. 값은 import 시점에
읽으므로 바꾸면 앱을 다시 띄워야 한다.

```bash
curl -s http://127.0.0.1:8000/metrics | grep classroom_monitoring_
```

지금 나오는 것은 **자연어 검색 지표뿐**이다(계획 생성 지연·재시도, 검색 지연,
`json_schema` 폴백, 조회 상한 도달). HTTP 요청 일반 지표는 아직 없다.

정의는 [`app/llm_search/metrics.py`](./app/llm_search/metrics.py)에 있고, 무엇을 왜
재는지와 PromQL 예시는
[`monitoring/internal/README.md`](../../monitoring/internal/README.md#지금-노출하는-지표--fastapi)가
정본이다.

**uvicorn을 워커 여러 개로 띄우면 값이 갈라진다.** 프로세스마다 레지스트리가 따로
생기고 스크랩은 그중 하나에만 닿는다. 배포 방식이 `결정 필요`라 워커를 늘릴 때
`prometheus_client`의 multiprocess 모드를 켜야 한다.

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
