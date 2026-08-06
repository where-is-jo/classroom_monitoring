# fastapi

FastAPI 웹 애플리케이션 디렉터리다. API와 화면을 함께 제공한다.

> 현재 상태: **공통 저장소, 인증·사용자 관리, 직원 상태, 인앱 알림, 면담 대기, 강의실 좌석, 관리자 대시보드 동작**. 기존 이벤트 화면/API는
> 공개 범위를 유지한다. 직원 프로필, 저장된 현재 상태와 이력, 수동 override, 명시적 시간
> 정책 평가, 사용자별 알림함, 직원 복귀 연계 면담 대기와 mock 좌석 점유·마감 후 경고를 제공하며 local 인메모리 mode와 MongoDB mode가 같은
> 저장소 계약을 구현한다. 개발환경에서는 외부 네트워크 없는 mock delivery 결과를 기록한다.

## 실행 방법

```bash
cd webapps/fastapi
python -m pip install -r requirements.txt
cp .env.example .env
# .env의 네 보안 비밀값을 각각 32자 이상의 서로 다른 무작위 값으로 채운다.
python -m uvicorn app.main:app --reload --port 8000
```

브라우저에서 `http://127.0.0.1:8000`을 열면 이벤트 목록으로 이동한다.
예제 설정은 `APP_ENV=local`, `DATABASE_MODE=memory`를 명시하므로 보안 비밀값을 채우면
외부 서비스 없이 기동한다. `WEB_ORIGIN`은 브라우저에서 접속하는 origin과 정확히 같아야 한다.

MongoDB를 사용하려면 `DATABASE_MODE=mongodb`와 `DATABASE_URL`, `DATABASE_NAME`을
주입한다. 앱은 시작할 때 연결과 index 초기화를 확인하며 실패하면 요청을 받기 전에
종료한다. memory mode는 local에서만 허용한다.

### 엔드포인트

| 경로 | 설명 |
| --- | --- |
| `GET /` | `/events`로 리다이렉트 |
| `GET /events?limit=&offset=` | 이벤트 목록 화면 |
| `GET /events/{event_id}` | 이벤트 상세 화면 |
| `GET /health` | 기동 확인 |
| `GET /health/ready` | 현재 저장소 mode 준비 상태 확인 |
| `GET /api/v1/events?limit=&offset=` | 이벤트 목록 JSON |
| `GET /api/v1/events/{event_id}` | 이벤트 상세 JSON |
| `GET/POST /login` | 로그인 화면·제출 |
| `POST /logout` | CSRF 검증 후 화면 세션 종료 |
| `GET /admin/users` | `ADMIN`, `SYSTEM_OPERATOR` 사용자 관리 화면 |
| `GET /employees` | 로그인 사용자 직원 상태 목록 화면 |
| `GET /employees/{employee_id}` | 현재 상태·override·최근 이력 화면 |
| `GET /admin/employees` | `ADMIN` 이상 직원 CRUD·명시적 정책 평가 화면 |
| `GET /admin/dev-tools` | mock 입력 허용 환경의 구조화 관측 화면 |
| `GET /notifications` | 로그인 사용자의 읽음 상태·유형 필터 인앱 알림함 |
| `GET/POST /my/interview-waits` | 본인 면담 대기 목록·신청 화면 |
| `GET /my/interview-waits/{wait_id}` | 본인 또는 권한 있는 담당자의 면담 대기 상세·이력 화면 |
| `GET /staff/interview-waits` | 연결된 STAFF 대상 면담 대기 화면 |
| `GET /classrooms` | 강의실별 점유·UNKNOWN·운영 여부 현황 |
| `GET /classrooms/{classroom_id}` | geometry 배치 또는 code 순 좌석 상세 |
| `GET /admin/classrooms` | `ADMIN` 이상 강의실·일정·좌석 관리 화면 |
| `GET /admin/alerts` | `ADMIN` 이상 마감 후 경고·해결 화면 |
| `GET /admin` | `ADMIN`, `SYSTEM_OPERATOR` 읽기 전용 운영 요약·최근 활동 화면 |
| `GET /admin/audit-logs` | `ADMIN`, `SYSTEM_OPERATOR` 마스킹된 감사 로그 조회 화면 |
| `GET /admin/mock-deliveries` | mock 입력 허용 환경의 정제된 delivery 기록·명시적 재시도 화면 |
| `POST /api/v1/auth/login` | access/refresh `HttpOnly` cookie 발급 |
| `POST /api/v1/auth/refresh` | refresh rotation |
| `POST /api/v1/auth/logout` | 현재 refresh family 폐기와 cookie 제거 |
| `GET /api/v1/auth/me` | 현재 사용자 조회 |
| `PATCH /api/v1/auth/me/password` | 본인 비밀번호 변경과 refresh 전체 폐기 |
| `GET/POST /api/v1/users` | `ADMIN` 이상 사용자 목록·생성 |
| `GET/PATCH/DELETE /api/v1/users/{user_id}` | `ADMIN` 이상 조회·수정·soft deactivate |
| `GET/POST /api/v1/employees` | 로그인 목록·`ADMIN` 이상 직원 생성 |
| `GET/PATCH/DELETE /api/v1/employees/{employee_id}` | 로그인 상세·`ADMIN` 이상 수정·비활성화 |
| `GET /api/v1/employees/{employee_id}/status-history` | 로그인 상태 이력 조회 |
| `PUT/DELETE /api/v1/employees/{employee_id}/status-override` | 본인 STAFF 또는 `ADMIN` 이상 override 설정·해제 |
| `POST /api/v1/employee-status-evaluations` | 모든 환경의 `ADMIN` 이상 명시적 시간 정책 평가 |
| `POST /api/v1/mock-employee-observations` | mock 입력 허용 환경의 `ADMIN` 이상 구조화 관측 |
| `GET /api/v1/notifications` | 본인 알림 목록, 읽음 상태·유형·페이지 필터 |
| `GET /api/v1/notifications/unread-count` | 본인 미읽음 알림 수 |
| `PATCH /api/v1/notifications/{notification_id}` | 본인 알림 읽음 처리 |
| `POST /api/v1/notification-read-batches` | 본인 알림 전체 읽음 처리 |
| `GET/POST /api/v1/interview-waits` | 역할 범위 면담 대기 목록·신청 |
| `GET/PATCH /api/v1/interview-waits/{wait_id}` | 권한 범위 상세·취소·완료 |
| `POST /api/v1/interview-wait-expirations` | 모든 환경의 `ADMIN` 이상 명시적 만료 평가 |
| `GET/POST /api/v1/classrooms` | 로그인 목록·`ADMIN` 이상 강의실 생성 |
| `GET/PATCH/DELETE /api/v1/classrooms/{classroom_id}` | 조회·`ADMIN` 이상 수정·비활성화 |
| `GET/PUT /api/v1/classrooms/{classroom_id}/schedules` | 조회·`ADMIN` 이상 요일 일정 전체 교체 |
| `GET/POST /api/v1/classrooms/{classroom_id}/seats` | 좌석 조회·`ADMIN` 이상 생성 |
| `PATCH/DELETE /api/v1/seats/{seat_id}` | `ADMIN` 이상 좌석 수정·비활성화 |
| `GET /api/v1/classrooms/{classroom_id}/occupancy` | 현재 좌석과 점유 summary |
| `GET /api/v1/classrooms/{classroom_id}/occupancy-history` | `ADMIN` 이상 기간·좌석 이력 |
| `POST /api/v1/mock-seat-observations` | mock 입력 허용 환경의 구조화 좌석 batch |
| `GET/PATCH /api/v1/after-hours-alerts[/{alert_id}]` | `ADMIN` 이상 경고 목록·해결 |
| `GET /api/v1/admin/dashboard-summary` | 부서·강의실 필터가 있는 읽기 전용 운영 요약 |
| `GET /api/v1/admin/dashboard-activities` | 유형·기간·페이지 필터가 있는 최근 활동 |
| `GET /api/v1/admin/audit-logs` | 작업자·작업·리소스·기간별 마스킹 감사 로그 |
| `GET /api/v1/admin/mock-deliveries` | mock 입력 허용 환경의 `ADMIN` 이상 delivery 기록 |
| `POST /api/v1/admin/mock-delivery-attempts` | 실패한 mock delivery의 명시적 멱등 재시도 |
| `GET /docs` | 자동 생성 API 문서 |
| `GET /openapi.json` | OpenAPI JSON |
| `GET /docs/api-spec` | 구현 전 API 설계 명세 Swagger UI |
| `GET /api-spec.json` | 구현 전 API 설계 명세 OpenAPI JSON |

### 구현 전 API 설계 명세

`individual_tasks/API명세서.md`를 OpenAPI 3.1로 옮긴 문서를 `api-spec/openapi.json`에 두고
`GET /docs/api-spec`으로 제공한다. 스마트 오피스 직원 상태 모니터링(대시보드 요약, 직원 상태,
좌석·카메라, 탐지 기록, 내부 추론 연동)의 **아직 구현되지 않은** 계약이다.

- 위 표의 API와 `/docs`는 화면 mockup 용도이며 이 설계 명세와 별개다. 둘을 섞지 않으려고
  경로를 나눴고, 설계 명세는 `/openapi.json`에 넣지 않는다. 경로가 겹치더라도
  명세 쪽 정의를 목표 계약으로 본다.
- 구현되지 않았으므로 이 화면에서는 요청 실행(Try it out)을 끈다.
- 구현이 끝나면 자동 생성 OpenAPI가 정본이 되고 이 명세 문서는 폐기한다.

### 테스트

```bash
cd webapps/fastapi
python -m pytest
```

기본 실행은 memory mode를 명시해 외부 의존성 없이 동작하며 MongoDB 통합 테스트는
`TEST_DATABASE_URL`이 없으면 건너뛴다.

실제 MongoDB 통합 테스트를 실행하려면 `TEST_DATABASE_URL` 경로에 `test_`로 시작하는
database 이름을 명시한 뒤 다음 marker를 사용한다. fixture는 이름을 검증하며 database나
컬렉션을 삭제하지 않는다.

```bash
python -m pytest -m mongodb
```

### 린트와 타입 검사

```bash
cd webapps/fastapi
python -m ruff check .        # 린트
python -m ruff format .       # 포매팅
python -m mypy                # 타입 검사 (대상은 pyproject.toml에 있다)
```

`ruff check --fix`로 자동 수정할 수 있는 지적은 `[*]` 표시가 붙는다.
병합 전에는 세 명령과 `python -m pytest`가 모두 통과해야 한다.

설정은 [`pyproject.toml`](./pyproject.toml)에 있다. 저장소 최상위가 아니라 이 디렉터리에
두는 이유와 각 규칙을 켠 이유는 그 파일의 주석에 적혀 있다.

**mypy는 `strict` 모드다.** 공개 함수의 인자와 반환값에 타입 힌트를 붙이라는
[코딩 규칙](../../docs/conventions/coding-convention.md#python)을 도구로 강제하는 것이며,
새 제약을 더하는 것이 아니다.

Scripts 디렉터리가 PATH에 없을 수 있어 `ruff`·`mypy`를 직접 부르지 않고
`python -m` 형태로 적었다. `python -m pytest`, `python -m uvicorn`과 같은 방식이다.

## 서비스 목적

외부 클라이언트 요청의 유일한 진입점이다.
인증과 권한을 판정하고, 비즈니스 로직을 수행하고, 저장된 데이터를
API 응답 또는 Jinja2 템플릿으로 렌더링한 화면으로 제공한다.

## 책임

- HTTP API 정의와 요청·응답 스키마 관리
- Jinja2 템플릿 렌더링과 화면 구성
- 인증 및 권한 판정
- 비즈니스 로직 조정(여러 서비스 호출의 순서와 실패 처리)
- 데이터 저장소 접근
- 오류 처리와 오류 응답·오류 화면

## 디렉터리 구조

기술 계층별이 아니라 **기능(도메인)별**로 나눈다.
기능 하나를 추가·삭제·리뷰할 때 디렉터리 하나만 보면 된다.

```text
app/
├─ main.py                 앱 조립. 라우터 등록, 정적 마운트, 예외 핸들러
├─ auth/                   로그인, JWT 검증, refresh rotation, 공통 인증 dependency
├─ users/                  사용자 CRUD, RBAC, memory/MongoDB 저장소
├─ audit/                  민감정보를 제거한 사용자·역할·상태 변경 감사 로그
├─ employees/              직원 CRUD, 상태 정책, override, memory/MongoDB 저장소
├─ notifications/          사용자별 인앱 알림, 읽음, dedupe, mock delivery 저장소
├─ interview_waits/        면담 대기 상태·이력, 직원 복귀 연계, memory/MongoDB 저장소
├─ classrooms/             강의실·일정·좌석 점유·마감 후 경고, memory/MongoDB 저장소
├─ admin/                  기존 원본 컬렉션의 읽기 전용 운영 집계·감사 로그 조회
├─ events/                 탐지 이벤트
│  ├─ router.py            HTTP 관심사. page_router(HTML) + api_router(JSON)
│  ├─ service.py           비즈니스 로직. 포트에만 의존
│  ├─ rules.py             탐지 결과를 업무 의미로 바꾸는 순수 함수
│  ├─ schemas.py           Pydantic 요청·응답 스키마
│  ├─ models.py            도메인 모델(dataclass)
│  ├─ ports.py             외부 I/O 인터페이스(Protocol)
│  └─ adapters/            포트 구현체
│     ├─ memory_repository.py    외부 의존 없는 local·테스트 구현체
│     └─ mongo_repository.py     같은 포트를 구현하는 PyMongo 구현체
└─ shared/                 공통 설정·예외·의존성 조립
   ├─ config.py            pydantic-settings
   ├─ database.py          PyMongo client·database 선택, ping, index 초기화
   ├─ errors.py            도메인 예외와 오류 응답 형식
   ├─ dependencies.py      어댑터 조립. 저장소 교체 시 고치는 유일한 파일
   ├─ schemas.py           health/readiness 응답 스키마
   ├─ security.py          Argon2, JWT, CSRF, IP HMAC fingerprint
   └─ templating.py        Jinja2 설정

templates/                 Jinja2 템플릿
├─ base.html               공통 레이아웃
├─ auth/                   로그인 화면
├─ users/                  사용자 관리 화면
├─ employees/              직원 목록·상세 화면
├─ admin/employees/        직원 관리·명시적 정책 평가 화면
├─ admin/dev_tools/        환경별로 등록되는 구조화 mock 관측 화면
├─ admin/mock_deliveries/  환경별로 등록되는 정제된 mock delivery 화면
├─ notifications/          사용자별 알림함
├─ interview_waits/        본인·STAFF 면담 대기 목록과 상세
├─ classrooms/             강의실 목록과 geometry/code 순 좌석 상세
├─ admin/classrooms/       강의실·요일 일정·좌석 숫자 geometry 관리
├─ admin/alerts/           OPEN 우선 마감 후 경고와 해결 action
├─ admin/dashboard.html    운영 요약 카드와 최근 활동
├─ admin/audit_logs.html   필터·페이지가 있는 마스킹 감사 로그
├─ events/                 기능별 템플릿
└─ errors/                 오류 화면

static/                    css, 브라우저 스크립트, 이미지
tests/                     기본 테스트와 선택적 integration/ MongoDB 테스트
```

기능이 늘어나면 같은 구조의 기능 디렉터리를 추가한다.

호출 방향은 `router → service → port ← adapter`다.
**서비스 계층은 어댑터를 직접 import하지 않는다.**
어댑터를 서비스에 연결하는 조립 코드는 `shared/`에 한 곳으로 모은다.

현재 포트는 프로세스 밖 I/O인 저장소 경계에만 만든다. mock HTTP 입력과 같은 프로세스의
서비스 호출에는 포트를 만들지 않는다.
선택 배경과 포트 판단 기준은 [ADR-0002](../../docs/architecture/decisions/ADR-0002-fastapi-layered-with-ports.md)에 있다.

포트 외의 설계 패턴(파사드·Strategy·Observer 등)을 언제 만들어도 되는지는
[ADR-0005](../../docs/architecture/decisions/ADR-0005-design-pattern-scope.md)의 판정 질문으로 정한다.
현재는 모두 "아직 만들지 않는다" 상태이며, `service.py`가 라우터에 대한 파사드 역할을 겸한다.

템플릿은 기능별로 나누되 `app/` 밖에 둔다. Python 코드와 템플릿 파일을 섞지 않는다.

## 포함해야 할 기능

- 라우터 계층(HTTP 관심사만 담당)
- 서비스 계층(비즈니스 로직, 프레임워크 비의존)
- Pydantic 스키마(요청·응답 검증)
- 포트 정의와 어댑터 구현(현재 범위에서는 저장소)
- Jinja2 템플릿과 정적 자산
- 의존성 조립 지점
- 오류 핸들러와 로깅

## 포함하지 않아야 할 기능

- 모델 로딩과 추론 연산 자체(→ [deeplearning](../../deeplearning/README.md))
- 스트림 연결 유지와 프레임 디코딩(→ [worker](../../worker/README.md))
- 라우터 함수 안에 직접 작성한 비즈니스 로직
- 템플릿 안의 비즈니스 판단(임계값 해석, 이벤트 분류)
- 비밀값의 소스 코드 내 하드코딩

## 화면 작업 규칙

- **템플릿에는 표시 로직만 둔다.** 판단은 서비스 계층에서 끝내고 템플릿에는 결과만 넘긴다.
- **정상·빈 상태·오류 상태를 모두 다룬다.** "데이터 없음"과 "조회 실패"를 구분해 표시한다.
- **상태를 색으로만 구분하지 않는다.** 문구나 아이콘을 함께 쓴다.
- 이미지와 아이콘에 대체 텍스트를 넣고, 키보드로 모든 조작이 가능하게 한다.
- 좁은 화면에서 레이아웃이 깨지지 않는지 확인한다.
- 브라우저 스크립트는 화면 보조에 한정한다. 비즈니스 로직을 클라이언트로 옮기지 않는다.

## 예상 기술

| 항목 | 상태 | 비고 |
| --- | --- | --- |
| 언어 | Python | 타입 힌트 필수 |
| 웹 프레임워크 | FastAPI | 프로젝트 전제 |
| 템플릿 | Jinja2 | 화면을 서버에서 렌더링 |
| 검증 | Pydantic | 요청·응답 스키마 |
| 메타데이터 저장소 | MongoDB ([ADR-0003](../../docs/architecture/decisions/ADR-0003-metadata-store-mongodb.md)) | 동기 PyMongo, 저장소 포트 뒤에 격리 |
| 내부 구조 | 계층형 + 경계 포트 ([ADR-0002](../../docs/architecture/decisions/ADR-0002-fastapi-layered-with-ports.md)) | |

동기 service/repository 계약과 맞는 공식 드라이버가 필요해 `pymongo>=4.17,<5`를 사용한다.
현재 4.x 범위 안의 호환 업데이트는 허용하고, breaking change 검토가 필요한 다음 major는
자동으로 설치하지 않는다. ORM이나 별도 MongoDB mock 패키지는 사용하지 않는다.

비밀번호는 FastAPI의 현재 보안 가이드와 맞는 `pwdlib[argon2]`, JWT는 고정 `HS256`
algorithm과 필수 claim을 검증하는 `PyJWT`를 사용한다. 원문 비밀번호와 refresh token은
저장하지 않으며 브라우저 JavaScript에 access/refresh token을 노출하지 않는다.

## 다른 서비스와의 관계

현재 MVP 직원 상태 기능은 다른 서비스와 연동하지 않는다. mock 관측은 사람이 입력한
boolean, confidence, UTC 시각만 받고 카메라·영상·AI·RPA 계약을 정의하지 않는다.

## 환경변수

값의 취급과 명명 규칙, 필수값 검증 방식은 [환경변수 규칙](../../docs/conventions/environment-convention.md)을 따른다.

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `APP_ENV` | 실행 환경 구분 | `local` / `dev` / `prod` |
| `DATABASE_MODE` | 저장소 구현 선택 | `memory` / `mongodb`, memory는 local 전용 |
| `DATABASE_URL` | MongoDB 접속 정보 | MongoDB mode 필수, 비밀값, 응답·로그 비노출 |
| `DATABASE_NAME` | MongoDB database 이름 | MongoDB mode 필수 |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | MongoDB 연결 제한 시간 | 기본 5초 |
| `MOCK_INPUTS_ENABLED` | mock 직원 관측 API·개발 도구 등록 | 기본 false, prod에서 true 금지 |
| `EMPLOYEE_AWAY_AFTER_SECONDS` | 마지막 사람 있음 후 AWAY 기준 | 기본 180초 |
| `EMPLOYEE_OFFSITE_AFTER_SECONDS` | 마지막 사람 있음 후 OFFSITE 기준 | 기본 3600초, AWAY보다 커야 함 |
| `NOTIFICATION_MOCK_DELIVERY_MODE` | mock 입력 허용 환경의 기록 결과 | `success` / `fail_once` / `always_fail` |
| `NOTIFICATION_MOCK_DELIVERY_MAX_ATTEMPTS` | 명시적 mock delivery 최대 시도 | 기본 3, 최대 10 |
| `INTERVIEW_WAIT_EXPIRES_AFTER_HOURS` | 면담 대기 만료 기준 | 기본 24시간, 1~168시간 |
| `SEAT_OCCUPANCY_CONFIDENCE_THRESHOLD` | 좌석 UNKNOWN 판정 confidence 기준 | 기본 0.6, 0~1 |
| `JWT_ACCESS_SECRET` | access JWT 서명 | 필수 비밀값, 32자 이상, 기본값 없음 |
| `JWT_REFRESH_SECRET` | refresh JWT 서명 | 필수 비밀값, 32자 이상, access와 분리 |
| `CSRF_SECRET` | CSRF token 서명 | 필수 비밀값, 32자 이상 |
| `AUDIT_IP_HASH_SECRET` | audit·rate limit IP HMAC | 필수 비밀값, 32자 이상 |
| `WEB_ORIGIN` | 브라우저 쓰기 요청 허용 origin | 필수, exact match |
| `AUTH_ACCESS_TOKEN_TTL_SECONDS` | access token 유효기간 | 기본 900초 |
| `AUTH_REFRESH_TOKEN_TTL_SECONDS` | refresh token 유효기간 | 기본 604800초 |
| `AUTH_LOGIN_MAX_FAILURES` / `AUTH_LOCKOUT_SECONDS` | 계정 실패 제한·잠금 | 기본 5회·900초 |
| `AUTH_IP_MAX_FAILURES` / `AUTH_IP_WINDOW_SECONDS` | IP 지문 실패 제한 | 기본 20회·300초 |
| `AUTH_PASSWORD_MIN_LENGTH` | 비밀번호 최소 길이 | 기본 12자, 대·소문자/숫자/기호 필요 |
| `AUTH_SEED_ENABLED` | 네 역할 가상 사용자 seed | 기본 false, local/dev에서 명시적으로 사용 |
| `AUTH_SEED_*_PASSWORD` | 역할별 seed 비밀번호 | seed 활성화 시 환경 주입, 문서·로그 비노출 |
| `HIGH_CONFIDENCE_THRESHOLD` | 기존 이벤트 신뢰도 high 기준 | 0.0~1.0 |
| `MEDIUM_CONFIDENCE_THRESHOLD` | 기존 이벤트 신뢰도 medium 기준 | 0.0~1.0, high 이하 |
| `PAGE_SIZE_DEFAULT` | 목록 기본 크기 | 기본 50 |
| `PAGE_SIZE_MAX` | 목록 최대 크기 | 최대 200 |
| `TEST_DATABASE_URL` | 선택적 MongoDB 통합 테스트 접속 정보 | URL 경로 DB 이름은 `test_` 접두사 필수 |

MongoDB mode는 시작 시 ping한 뒤 events, users, refresh_tokens, audit_logs, employees,
employee_status_history, employee_observations, notifications, notification_deliveries,
interview_waits, interview_wait_history, classrooms, seats, seat_observation_batches,
seat_occupancy_history, after_hours_alerts의
고정 이름 index를 초기화한다. email, employee_no, STAFF 연결, refresh hash,
operation/event ID, 알림 dedupe key와 notification+attempt는 unique index로 중복을 막고
사용자·직원 갱신은 compare-and-set으로 경합을 감지한다. multi-document transaction을
가정하지 않는다.

관리자 대시보드는 별도 제품 컬렉션이나 materialized view를 만들지 않고 위 원본 컬렉션을
제한된 쿼리로 집계한다. 최근 활동은 시각 내림차순·ID 오름차순으로 안정 정렬하며 기본 범위는
최근 24시간이다. 원본 쿼리 하나라도 실패하면 불완전한 수치를 섞지 않고 요청 전체를 503으로
처리한다. 조회 API와 화면에는 자동 polling, background 갱신, 쓰기 동작이 없다.

`NotificationService.create()`는 후속 같은-process 기능이 호출하는 생성 API다. 일반 사용자가
알림을 임의 생성하는 HTTP API는 없다. 알림 `data`는 제한된 구조화 값과 허용된 내부 route만
저장하며 token·cookie·비밀번호·영상·이미지 관련 키를 거부한다. mock delivery는
`MOCK_INPUTS_ENABLED=true`에서만 기록·조회하고 외부 SDK, 네트워크, background worker를
사용하지 않는다. `fail_once`와 `always_fail` 재시도는 HTTP 요청 안에서만 명시적으로 실행한다.

직원 조회 GET은 저장된 상태만 반환하고 상태·이력·version을 변경하지 않는다. 3분 부재와
60분 외근, override 만료는 `POST /api/v1/employee-status-evaluations` 또는 관련 쓰기에서만
평가한다. override는 `AWAY`와 `OFFSITE`만 허용하며 해제 시 최신 유효 mock 관측으로 즉시
재평가한다. 시각은 API에서 ISO 8601 UTC로 주고받고 화면에서는 KST로 표시한다.

면담 대기는 요청자와 직원 조합당 활성 `WAITING` 또는 `READY` 한 건만 허용한다. 직원이 이미
재석 중이면 즉시 `READY`, 부재 중이면 `WAITING`으로 만들며 부재→재석 전이에서만 `READY`
알림을 한 번 생성한다. 목록·상세 GET은 상태와 이력을 변경하지 않고, 만료는 관리자 전용
`POST /api/v1/interview-wait-expirations` 또는 관련 상태 변경 요청에서만 평가한다. 요청자와
관리자는 활성 대기를 취소할 수 있고, `READY` 대기는 요청자·관리자·연결된 STAFF가 완료할 수 있다.

강의실 일정은 IANA timezone과 월요일 0~일요일 6의 당일 운영시간으로 저장한다. 좌석 geometry는
0~1 범위의 `x/y/width/height` 숫자만 허용하고 도면이나 영상은 저장하지 않는다. 구조화 mock batch는
좌석 소속·활성을 전부 검증한 뒤 처리하며 event_id 재전송은 멱등하다. confidence가 설정 기준보다
낮으면 `UNKNOWN`, 이상이면 boolean에 따라 `OCCUPIED/VACANT`로 판정하고 오래된 관측은 current를
되돌리지 않는다. 마감+grace 이후 실제 `OCCUPIED` 전이만 영업일별 경고·관리자 알림을 한 번 만들며,
해결된 같은 영업일 경고는 다시 열지 않는다.

### 가상 사용자 seed

`AUTH_SEED_ENABLED=true`일 때만 아래 네 가상 이메일을 idempotent하게 만든다. 비밀번호는
대응하는 `AUTH_SEED_*_PASSWORD` 환경변수에서만 받으며 소스·문서·로그에 기록하지 않는다.

| 이메일 | 역할 |
| --- | --- |
| `student@example.invalid` | `STUDENT` |
| `staff@example.invalid` | `STAFF` |
| `admin@example.invalid` | `ADMIN` |
| `operator@example.invalid` | `SYSTEM_OPERATOR` |

쓰기 API는 로그인 뒤 발급된 `som_csrf` cookie 값을 `X-CSRF-Token` header로 보내고
`Origin`을 `WEB_ORIGIN`과 일치시켜야 한다. Jinja2 form은 같은 검증을 hidden field로 처리한다.

## 테스트 전략

- 서비스 계층은 포트를 memory fake로 대체해 단위 테스트한다. 실제 MongoDB가 필요 없다.
- 라우터는 상태 코드, 검증 실패, 오류 응답 형식을 API 테스트로 검증한다.
- 템플릿 렌더링은 정상·빈 상태·오류 상태가 각각 렌더링되는지 확인한다.
- 인증 테스트는 hash/policy, 잠금·rate limit, refresh rotation·재사용, CSRF와 네 역할 권한표를 확인한다.
- 사용자 여정 테스트는 관리자 로그인→사용자 생성→수정→비활성화를 수행한다.
- 직원 테스트는 CRUD·STAFF 연결, 2분59초/3분/59분59초/60분 경계, GET 무부작용,
  중복·역전 관측, CAS 재시도, override 권한·만료·해제 사용자 여정을 확인한다.
- 알림 테스트는 사용자 격리, dedupe, 개별·전체 읽음, badge, 민감 데이터 제거,
  mock delivery 실패·최대 시도·동일 attempt 방지와 production 라우터 미등록을 확인한다.
- 면담 대기 테스트는 중복 방지, 상태 전이표, 역할별 조회·변경, 명시적 만료, 직원 복귀 연계,
  알림 dedupe와 신청→복귀→알림 읽음→완료 사용자 여정을 확인한다.
- 강의실 테스트는 일정·timezone·geometry 검증, confidence 0.599/0.6, 오래된 관측 보호,
  batch 멱등·전체 검증, 마감·grace 경계, 경고 dedupe·해결과 좌석→경고→알림 사용자 여정을 확인한다.
- 관리자 대시보드 테스트는 비활성 원본 제외, 0건, 안정 정렬, 역할 권한, 전체 503,
  감사 필드 마스킹, 경고 해결 후 요약 감소와 MongoDB bounded query/index explain을 확인한다.
- MongoDB 연결·index는 `mongodb` marker 통합 테스트로 분리한다.
- 계약 변경 시 기존 응답 스키마 테스트를 함께 갱신한다.

## 관련 문서

- [FastAPI 에이전트 규칙](../../docs/agents/fastapi-agent.md)
- `create-fastapi-feature` 스킬
- [API 규칙](../../docs/conventions/api-convention.md)
- [아키텍처 개요](../../docs/architecture/overview.md)
- [데이터 흐름](../../docs/architecture/data-flow.md)
