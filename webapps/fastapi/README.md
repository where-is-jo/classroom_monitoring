# fastapi

학생·직원·관리자가 사용하는 캠퍼스 운영 포털의 HTTP API와 Jinja2 화면을 제공한다.
이 저장소에서 실행 가능한 유일한 서비스이자 브라우저의 단일 진입점이다.

현재 v2 범위는 인증·사용자 관리, 직원 상태, 면담 대기, 강의실 좌석과 마감 후 경고,
인앱 알림, 관리자 대시보드다. `local`·`dev`에서는 실제 영상이나 개인정보 없이 고정 합성
데이터로 모니터링·영상 검색 흐름을 시연할 수 있다.

## 빠른 시작

Python 3.12 환경에서 실행한다.

```bash
cd webapps/fastapi
python -m pip install -r requirements.txt
cp .env.example .env
# .env의 JWT_ACCESS_SECRET, JWT_REFRESH_SECRET, CSRF_SECRET,
# AUDIT_IP_HASH_SECRET을 각각 32자 이상 값으로 채운다.
python -m uvicorn app.main:app --reload --port 8000
```

기본 예제는 `APP_ENV=local`, `DATABASE_MODE=memory`라서 외부 서비스 없이 기동한다.
`WEB_ORIGIN`은 브라우저에서 접속하는 origin과 정확히 같아야 한다. 브라우저에서
`http://127.0.0.1:8000`을 열면 비로그인 사용자는 `/login`으로 이동한다.

개발용 계정이 필요하면 `.env`에서 `AUTH_SEED_ENABLED=true`로 바꾸고 세
`AUTH_SEED_*_PASSWORD` 값을 채운다. seed 계정은 바로 로그인할 수 있다. 여기에
`DEMO_MODE_ENABLED=true`를 함께 설정하면 memory 저장소에 직원·면담·강의실·좌석·경고·알림
fixture도 멱등하게 채워져 별도 입력 없이 역할별 흐름을 시연할 수 있다.

| 이메일                     | 역할      |
| -------------------------- | --------- |
| `student@example.invalid`  | `STUDENT` |
| `staff@example.invalid`    | `STAFF`   |
| `staff-02@example.invalid` | `STAFF`   |
| `admin@example.invalid`    | `ADMIN`   |

MongoDB를 사용하려면 `DATABASE_MODE=mongodb`와 `DATABASE_URL`, `DATABASE_NAME`을
주입한다. 앱은 요청을 받기 전에 연결을 확인하고 필요한 index를 idempotent하게 초기화한다.
`memory` mode는 `local`에서만 허용한다.

## 역할별 제품 흐름

| 역할      | 로그인 후 시작 화면      | 할 수 있는 일                                                                                     |
| --------- | ------------------------ | ------------------------------------------------------------------------------------------------- |
| `STUDENT` | `/employees`             | 직원 상태 조회, 본인 면담 신청·취소·완료, 강의실 좌석 조회                                        |
| `STAFF`   | `/staff/interview-waits` | 접수된 본인 면담 처리, 직원 상태 조회와 본인 근무 상태 override, 강의실 좌석 조회, 합성 영상 데모 |
| `ADMIN`   | `/admin`                 | 운영 요약·최근 활동·열린 경고 해결, 사용자·직원·강의실 관리, 합성 영상 데모                       |

알림은 별도 제품 페이지가 아니라 공통 화면의 알림 팝오버로 제공한다. 학생은 준비된 면담
알림, STAFF와 ADMIN은 담당 강의실의 마감 후 좌석 경고를 확인하고 개별 또는 전체 읽음
처리할 수 있다.

제품 역할은 위 세 가지뿐이다. 저장된 레거시 문서를 읽기 위해 `SYSTEM_OPERATOR` enum은
남아 있지만 로그인·토큰 인증·관리 권한·신규 생성·seed 대상이 아니다. 레거시 경계와 이전
정책은 [ADR-0006](../../docs/architecture/decisions/ADR-0006-v2-legacy-compatibility.md)에 있다.

## 화면과 API

실행 중인 설정에서 공개되는 정확한 JSON 계약은 `/docs`와 `/openapi.json`에서 확인한다.
Jinja2 화면과 form 경로, 레거시 호환 경로는 OpenAPI에 넣지 않는다. 모든 JSON API 오류는
`{"error": {"code", "message", "details"}}` envelope를 사용한다.

### 기본 화면

| 경로                              | 접근            | 설명                                                          |
| --------------------------------- | --------------- | ------------------------------------------------------------- |
| `/login`, `/account/password`     | 비로그인·로그인 | 로그인과 최초/본인 비밀번호 변경                              |
| `/employees`, `/employees/{id}`   | 로그인          | 직원 현재 상태와 이력 조회; 연결된 STAFF는 본인 상태 override |
| `/my/interview-waits*`            | `STUDENT`       | 본인 면담 대기 신청·상세·상태 전이                            |
| `/staff/interview-waits*`         | `STAFF`         | 본인에게 접수된 면담 대기 조회·완료                           |
| `/classrooms`, `/classrooms/{id}` | 로그인          | 운영 강의실과 좌석 점유 현황                                  |
| `/admin`                          | `ADMIN`         | 직원·강의실·경고 요약, 최근 핵심 활동, 열린 경고 해결         |
| `/admin/users`                    | `ADMIN`         | 세 제품 역할 사용자 생성·수정·soft deactivate                 |
| `/admin/employees`                | `ADMIN`         | 직원 프로필과 STAFF 계정 연결 관리                            |
| `/admin/classrooms`               | `ADMIN`         | 강의실·담당 STAFF·요일 일정·좌석 관리                         |

### 기본 API

| 경로 묶음                                                               | 설명                                                           |
| ----------------------------------------------------------------------- | -------------------------------------------------------------- |
| `/api/v1/auth/*`                                                        | 로그인, refresh rotation, 로그아웃, 현재 사용자, 비밀번호 변경 |
| `/api/v1/users*`                                                        | ADMIN 사용자 조회·생성·수정·soft deactivate                    |
| `/api/v1/employees*`, `/api/v1/employee-status-evaluations`             | 직원·상태 이력·override·명시적 시간 정책 평가                  |
| `/api/v1/interview-waits*`, `/api/v1/interview-wait-expirations`        | 역할 범위 면담 대기와 명시적 만료 평가                         |
| `/api/v1/classrooms*`, `/api/v1/seats*`                                 | 강의실·일정·좌석·점유·점유 이력                                |
| `/api/v1/after-hours-alerts*`                                           | ADMIN 마감 후 경고 조회·해결                                   |
| `/api/v1/notifications*`, `/api/v1/notification-read-batches`           | 본인 알림 조회·읽음·전체 읽음                                  |
| `/api/v1/admin/dashboard-summary`, `/api/v1/admin/dashboard-activities` | ADMIN 운영 집계와 최근 핵심 활동                               |
| `/health`, `/health/ready`                                              | 프로세스 기동과 현재 저장소 준비 상태                          |

쓰기 API는 로그인 때 발급된 `som_csrf` cookie 값을 `X-CSRF-Token` header로 보내고,
`Origin`을 `WEB_ORIGIN`과 일치시켜야 한다. Jinja2 form은 hidden field로 같은 검증을 한다.

### 합성 영상 데모

`APP_ENV=local|dev`와 `DEMO_MODE_ENABLED=true`를 함께 설정할 때만 다음 경로를 등록한다.
`STUDENT`는 접근할 수 없고 `STAFF`, `ADMIN`만 사용할 수 있다.

| 경로                                               | 설명                                                            |
| -------------------------------------------------- | --------------------------------------------------------------- |
| `/monitoring`                                      | 연결된 합성 feed 2개와 영상 없음 상태 1개                       |
| `/video-search`                                    | 고정 한국어 metadata와 기간·강의실 조건을 이용한 영상 검색 시연 |
| `/api/v1/video-streams*`, `/api/v1/video-searches` | 같은 고정 catalog의 JSON API                                    |
| `/demo-assets/*`                                   | 개인정보 없는 SVG poster와 브라우저 합성 영상 자산              |

이 기능은 카메라·스트림·추론·영상 저장을 구현하지 않는다. 외부 미디어, 사용자 업로드,
운영 데이터도 사용하지 않는다. `APP_ENV=prod`에서는 설정 검증 단계에서 활성화를 거부한다.

### 테스트·개발 전용 입력

`APP_ENV=local|dev`와 `MOCK_INPUTS_ENABLED=true`일 때만 구조화 직원·좌석 관측과 mock
알림 delivery 라우터를 등록한다. 제품 탐색에는 링크하지 않으며 외부 네트워크나 SDK를
사용하지 않는다. `APP_ENV=prod`에서는 활성화를 거부한다.

### 레거시 직접 호출

`/events*`, `/api/v1/events*`는 제품 탐색과 OpenAPI에서 빠졌지만 이전 기간 동안 직접
호출을 유지한다. 이벤트 API 응답은 `Deprecation: true`를 보낸다. 감사 기록 저장은 내부
통제로 유지하며 `/admin/audit-logs` 화면은 404다. `/api/v1/admin/audit-logs` 직접 호출은
OpenAPI에서 숨기고 `Deprecation: true`를 보낸다.

## 핵심 도메인 규칙

- 직원 목록·상세 GET은 저장된 상태나 version을 바꾸지 않는다. 시간 정책은 명시적 평가
  요청 또는 관련 쓰기에서만 적용한다. 연결된 STAFF는 본인 상태를 `WORKING`, `ON_CALL`,
  `AWAY`, `OFFSITE`로 override하거나 자동 상태로 복귀할 수 있다.
- 면담 대기는 요청자와 직원 조합당 활성 건이 하나다. 직원이 재석이면 `READY`, 아니면
  `WAITING`으로 만들고 부재→재석 전이에서 준비 알림을 dedupe한다.
- STAFF는 본인에게 접수된 면담만 조회·완료한다. ADMIN은 면담 목록·상세·변경에 접근하지 않는다.
- 좌석 관측은 batch 전체를 먼저 검증하고 event ID로 멱등 처리한다. confidence 기준 미만은
  `UNKNOWN`이고 오래된 관측은 현재 상태를 되돌리지 않는다.
- 마감과 grace 이후 `OCCUPIED` 전이만 영업일별 경고를 만든다. 알림은 담당 활성 STAFF와
  모든 활성 ADMIN에게 수신자별로 dedupe한다.
- 사용자 비활성화 또는 STAFF 역할 해제 시 직원·강의실 연결을 명시적으로 해제한다.
- 관리자 대시보드는 별도 materialized view 없이 원본 컬렉션을 bounded query로 집계한다.
  하나의 원본 조회라도 실패하면 부분 수치를 섞지 않고 요청 전체를 503으로 처리한다.

## 내부 구조

기능별 디렉터리와 `router → service → port ← adapter` 호출 방향을 사용한다. 라우터는
HTTP 변환만, 서비스는 프레임워크와 분리된 판단만 담당한다. 저장소 구현의 조립은
`app/shared/dependencies.py` 한 곳에 둔다. 배경은
[ADR-0002](../../docs/architecture/decisions/ADR-0002-fastapi-layered-with-ports.md)에 있다.

```text
app/
├─ main.py              앱 조립, 조건부 라우터, 예외 처리
├─ auth/                로그인, JWT, refresh, CSRF, 인증 dependency
├─ users/               세 제품 역할 사용자 관리와 레거시 읽기 호환
├─ employees/           직원 프로필, 상태 정책, override
├─ interview_waits/     학생 신청과 STAFF 처리 흐름
├─ classrooms/          강의실, 담당자, 일정, 좌석, 마감 후 경고
├─ notifications/       사용자별 알림, 읽음, dedupe, mock delivery
├─ video_monitoring/    local/dev 고정 합성 catalog와 검색
├─ admin/               운영 집계, 최근 활동, 내부 감사 조회 호환
├─ audit/               민감정보를 제거한 감사 기록
├─ events/              단계적 폐기 중인 이벤트 조회 호환
└─ shared/              설정, 저장소 조립, 보안, 공통 오류·템플릿

templates/              기능별 Jinja2 화면
static/                 CSS와 화면 보조 JavaScript
demo_assets/            합성 데모 SVG 자산
tests/                  단위·API·템플릿·선택적 MongoDB 통합 테스트
```

추론 연산, 스트림 연결·디코딩, 실제 영상 저장은 이 서비스에 포함하지 않는다. 영상 저장
범위·보존 기간·권한은 여전히 결정이 필요하다.

## 환경변수

전체 이름과 기본값은 [`.env.example`](./.env.example)이 기준이다.

| 묶음                | 주요 변수                                                                                 | 제약                                                           |
| ------------------- | ----------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| 실행·저장소         | `APP_ENV`, `DATABASE_MODE`, `DATABASE_URL`, `DATABASE_NAME`                               | memory는 local 전용; MongoDB mode는 URL·이름 필수              |
| 보안                | `JWT_*_SECRET`, `CSRF_SECRET`, `AUDIT_IP_HASH_SECRET`, `WEB_ORIGIN`                       | 비밀값 32자 이상; origin exact match                           |
| 인증 정책           | `AUTH_*_TTL_SECONDS`, 실패·잠금 제한, `AUTH_PASSWORD_MIN_LENGTH`                          | 시작 시 범위 검증                                              |
| 가상 사용자·fixture | `AUTH_SEED_ENABLED`, 세 `AUTH_SEED_*_PASSWORD`, `DEMO_MODE_ENABLED`                       | local memory의 전체 fixture는 두 mode를 함께 활성화; prod 금지 |
| 직원·면담·좌석      | `EMPLOYEE_*`, `INTERVIEW_WAIT_EXPIRES_AFTER_HOURS`, `SEAT_OCCUPANCY_CONFIDENCE_THRESHOLD` | 시간·confidence 범위 검증                                      |
| 개발 경계           | `MOCK_INPUTS_ENABLED`, `DEMO_MODE_ENABLED`, `NOTIFICATION_MOCK_*`                         | local/dev opt-in; prod 금지                                    |
| 목록·레거시 이벤트  | `PAGE_SIZE_*`, `HIGH_CONFIDENCE_THRESHOLD`, `MEDIUM_CONFIDENCE_THRESHOLD`                 | 페이지 최대 200; medium ≤ high                                 |

환경변수의 저장·명명 규칙은
[환경변수 규칙](../../docs/conventions/environment-convention.md)을 따른다. 실제 비밀값과 `.env`는
커밋하지 않는다.

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

- [FastAPI 에이전트 규칙](../../docs/agents/fastapi-agent.md)
- [API 규칙](../../docs/conventions/api-convention.md)
- [아키텍처 개요](../../docs/architecture/overview.md)
- [v2 레거시 호환성 결정](../../docs/architecture/decisions/ADR-0006-v2-legacy-compatibility.md)
