# fastapi

관리자가 사용하는 강의실 모니터링 화면과 HTTP API를 제공한다.
이 저장소에서 실행 가능한 웹 서비스이자 브라우저의 단일 진입점이다.

**이 서비스가 학생 상태 판정을 소유한다.** 탐지 결과를 `PRESENT` / `WRONG_SEAT` /
`ABSENT`로 바꾸는 규칙은 여기 있고, `worker`나 `deeplearning`에 두지 않는다
([결정 0008](../../docs/architecture/decisions.md#0008--학생-상태-판정을-rule-engine으로-분리하고-fastapi가-소유한다)).

> **현재 범위는 강의실 좌석 현황, 실시간 모니터링, 자연어 검색 세 화면이다.**
> 학생 원장, 얼굴 등록, 지정 좌석, 학생 상태 판정은 **아직 구현되지 않았다.**
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
| `/monitoring` | 영상 source 목록과 연결 상태. demo가 꺼져 있으면 빈 상태 |
| `/video-search` | 한국어 문장과 강의실·기간·결과 수 조건으로 검색. demo가 꺼져 있으면 빈 결과 |

`/classrooms/{id}` 상세 페이지는 없다. `/classrooms?classroom_id={id}`에서 같은
정보를 선택해 본다.

### API

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `GET` | `/api/v1/classrooms` | 활성 강의실 목록 |
| `GET` | `/api/v1/classrooms/{classroom_id}/occupancy` | 한 강의실의 좌석 지도와 현재 점유 |
| `GET` | `/api/v1/video-streams` | 영상 source 목록. `q`·`classroom_id`·`status` 필터 |
| `GET` | `/api/v1/video-streams/{stream_id}` | 한 source의 상태 |
| `POST` | `/api/v1/video-searches` | 부작용 없는 검색 실행 |
| `GET` | `/health` | 프로세스 기동 상태 |
| `GET` | `/health/ready` | 현재 저장소 준비 상태 |

**쓰기 API가 없다.** 로그인, 사용자 관리, 강의실·좌석 CRUD, 알림, 관리자 대시보드는
현재 구현되어 있지 않다.

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

기능별 디렉터리와 `router → service → port ← adapter` 호출 방향을 사용한다. 라우터는
HTTP 변환만, 서비스는 프레임워크와 분리된 판단만 담당한다. 저장소 구현의 조립은
`app/shared/dependencies.py` 한 곳에 둔다. 배경은
[결정 0001](../../docs/architecture/decisions.md#0001--fastapi-계층형-구조와-경계-포트)에 있다.

```text
app/
├─ main.py              앱 조립, 라우터 등록, 예외 처리
├─ classrooms/          강의실, 좌석, 좌석 점유 관측
├─ video_monitoring/    영상 source 목록과 검색 (local/dev 합성 catalog)
├─ shared/              설정, 저장소 조립, 공통 오류·템플릿·스키마
└─ demo_seed.py         demo fixture 멱등 생성

templates/              기능별 Jinja2 화면
static/                 CSS와 화면 보조 JavaScript
demo_assets/            합성 데모 SVG 자산
api-spec/               구현 전 API 설계 명세 JSON
tests/                  단위·API·템플릿·선택적 MongoDB 통합 테스트
```

추가 예정 도메인은 `students`, `face_enrollment`, `student_monitoring` 셋이다.
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
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | 연결 타임아웃 | 기본 5. `0 < x ≤ 60` |
| `DEMO_MODE_ENABLED` | 합성 영상·검색 demo | 기본 false. `local`/`dev` 전용. prod 금지 |
| `SEAT_OCCUPANCY_CONFIDENCE_THRESHOLD` | 이 값 미만의 좌석 관측은 `UNKNOWN` | 기본 0.6. `0 ≤ x ≤ 1` |
| `PAGE_SIZE_DEFAULT`, `PAGE_SIZE_MAX` | 목록 페이지 크기 | 최대 200 |
| `TEST_DATABASE_URL` | 선택적 MongoDB 통합 테스트용 | database 이름이 `test_`로 시작해야 한다 |

학생 식별·상태 판정에 필요한 설정(`IDENTITY_CONFIDENCE_THRESHOLD`,
`ABSENCE_GRACE_PERIOD_SECONDS` 등)은 아직 없다. 목록은
[MVP 명세의 설정](../../docs/specs/student-monitoring-mvp.md#설정-예정)에 있다.

환경변수의 저장·명명 규칙은
[환경변수 규칙](../../docs/conventions/environment-convention.md)을 따른다.
실제 비밀값과 `.env`는 커밋하지 않는다.

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
