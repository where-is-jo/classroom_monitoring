# FastAPI 최소 학습 모니터링 전환 명세

**목적**: 현재 캠퍼스 운영 포털을 강의실 좌석 현황, 실시간 모니터링, 자연어 검색의
세 화면만 제공하는 최소 FastAPI 웹 애플리케이션으로 축소할 때 제거 범위, 잔존 계약,
구현 순서와 완료 조건을 정한다.
**대상 독자**: 축소 작업을 구현·검토하는 FastAPI 개발자와 AI 에이전트.

> 상태: 이 문서에서 목표 동작은 `예정`이다. 현재 구현 사실은 별도 표기하지 않고,
> 합의가 필요한 항목은 `결정 필요`로 표시한다.
>
> 문서 제약: 이 전환 명세를 새로 추가하는 것만 허용한다. 기존 README, 아키텍처 문서,
> ADR, 규칙 문서와 이전 명세는 수정하거나 삭제하지 않는다. 구현 뒤 기존 문서와 불일치가
> 생기더라도 별도 승인 없이는 고치지 않고 완료 보고에 불일치 목록만 남긴다.

## 개요

`webapps/fastapi`의 제품 사용자를 학생·직원·관리자의 세 역할로 나누지 않는다. 제품 탐색은
한 종류의 운영자를 위한 다음 세 화면으로 제한하고, 로그인·계정·학생 계정·직원·면담·알림·
관리자 대시보드와 관리 화면은 제거한다.

1. 강의실 좌석 현황
2. 실시간 모니터링
3. 자연어 검색

이번 작업은 **기존 FastAPI를 축소하는 작업**이다. 실제 카메라 수신, 모델 추론, 영상 저장,
LLM·임베딩 검색을 구현하는 작업이 아니다. 존재하지 않는 연동을 완료된 기능처럼 표시하지
않으며, 운영 데이터 공급원이 없으면 빈 상태 또는 연결 불가 상태를 보여 준다.

## 배경과 현재 사실

현재 앱은 `router → service → port ← adapter` 구조로 memory와 MongoDB 저장소를 지원한다.
축소 대상 기능이 좌석 서비스와 공통 화면에 직접 결합돼 있어 라우터만 등록 해제해서는 제거가
완료되지 않는다.

| 대상 | 현재 사실 | 전환에 필요한 변화 |
| --- | --- | --- |
| 강의실 좌석 | `/classrooms`, `/classrooms/{id}`와 좌석 API가 로그인 사용자를 요구한다. 좌석 상태는 `OCCUPIED`, `VACANT`, `UNKNOWN`이며 자동 화면 갱신은 없다. | 사용자·권한 인자를 제거하고 한 화면에서 강의실 선택과 좌석 지도를 제공한다. |
| 강의실 서비스 | 사용자 저장소, 알림 서비스, 감사 서비스, 담당 직원, 일정, 마감 후 경고와 결합돼 있다. | 조회·현재 점유·관측 이력에 필요한 코드만 남겨 독립시킨다. |
| 실시간 모니터링 | `/monitoring`은 local/dev의 `DEMO_MODE_ENABLED=true`에서만 등록되는 합성 영상 데모다. 실제 스트림이 아니다. | 화면은 항상 존재하게 하되, demo는 명확히 표시하고 실제 공급원이 없을 때 가짜 운영 영상을 만들지 않는다. |
| 자연어 검색 | `/video-search`는 고정 catalog의 한국어 토큰·별칭·시각을 규칙으로 매칭한다. LLM, 임베딩, 저장 영상은 사용하지 않는다. | 현재 규칙 기반 검색을 최소 기능으로 유지하고 검색 방식과 데이터 출처를 화면에 표시한다. |
| 공통 화면 | `base.html`이 역할별 메뉴, 계정, 비밀번호 변경, 로그아웃, 알림과 직원 상태 변경을 포함한다. | 세 메뉴와 공통 오류·빈 상태만 있는 운영자 shell로 교체한다. |
| 외부 서비스 | `worker`와 `deeplearning`에는 실행 코드가 없고 전달 계약도 정해지지 않았다. | 이번 작업에서 연동을 전제하지 않는다. |

## 목표

- 제품 페이지의 상위 메뉴와 실제 접근 가능한 제품 화면을 세 개로 제한한다.
- 인증·사용자·직원·면담·알림·관리 기능의 라우터뿐 아니라 서비스, 포트, 어댑터, 템플릿,
  설정, seed와 전용 테스트를 함께 제거한다.
- 좌석 조회가 사용자, 직원, 알림, 감사 기능 없이 독립적으로 실행되게 한다.
- 합성 영상과 고정 검색 결과는 local/dev demo임을 응답과 화면에서 구분한다.
- 삭제된 페이지와 API는 라우터 등록만 숨기는 방식이 아니라 handler와 전용 코드까지 물리적으로
  제거한다. `include_in_schema=False`, `Deprecation` header 또는 직접 URL 호출용 호환 라우트로
  남기는 것은 완료로 인정하지 않는다.
- memory mode와 MongoDB mode의 잔존 기능을 기존 계층 규칙에 맞게 유지한다.

## 범위

**포함**

- `/classrooms`: 강의실 선택, 좌석 지도, 재석·부재·확인 필요 수와 마지막 관측 시각
- `/monitoring`: 영상 또는 합성 demo, 연결 상태, 마지막 상태 시각, 영상 없음·연결 실패 상태
- `/video-search`: 한국어 검색 문장과 선택 조건, 결과·빈 결과·입력 오류
- 세 화면이 사용하는 최소 JSON API와 `/health`, `/health/ready`
- 좌석의 current occupancy, geometry, 관측 batch와 관측 이력 저장
- local memory mode의 개인정보 없는 최소 강의실·좌석 demo fixture
- 제거 대상 API의 실제 삭제와 저장 데이터 보존 판단
- 제거·잔존 계약을 고정하는 단위·라우트·템플릿·MongoDB 어댑터 테스트

**제외**

- 앱 자체 로그인, 로그아웃, 비밀번호 변경, JWT, refresh token, CSRF, 제품 역할
- 학생 계정과 학생 원장 관리, 사용자 관리, 직원 프로필·상태 관리
- 면담 대기, 인앱 알림, mock delivery, 감사 로그 조회, 범용 이벤트
- 관리자 대시보드, 강의실·일정·좌석 CRUD 화면, 마감 후 경고와 해결 처리
- 학생 식별, 지정 좌석 불일치, `공부중` 판정과 오탐·미탐 피드백
- 실제 카메라 연결·재연결·디코딩, 실제 모델 추론, 화면 갱신 프로토콜 구현
- MinIO 영상·스냅샷 저장과 재생 URL 발급
- LLM, 임베딩, 벡터 저장소, 외부 검색 API
- RPA, 알림, 보고, 외부 학원 시스템 동기화
- 기존 MongoDB collection의 자동 삭제 또는 기존 문서의 파괴적 변환

## 제품 화면 계약

제품 shell의 탐색 링크는 아래 세 개만 표시한다. `/`는 `/classrooms`로 이동한다.
로그인 화면, 계정 영역, 알림 팝오버, 역할명, 관리자 메뉴와 demo 개발 도구 링크는 없다.

| 화면 | 경로 | 필수 내용 | 상태 처리 |
| --- | --- | --- | --- |
| 강의실 좌석 현황 | `/classrooms` | 강의실 선택, 전체·재석·부재·확인 필요 수, 좌석 label·상태·confidence·관측 시각, geometry 기반 좌석 지도 | 강의실 없음, 좌석 없음, 미관측, 저장소 실패를 서로 구분한다. |
| 실시간 모니터링 | `/monitoring` | 강의실·카메라 label, 영상 영역, `CONNECTED`·`NO_VIDEO` 등 연결 상태, 마지막 상태 시각, demo 여부 | 공급원 없음과 조회 실패를 영상 속 학생 부재로 표현하지 않는다. |
| 자연어 검색 | `/video-search` | 1~200자 한국어 문장, 선택 강의실·기간·결과 수, 일치 이유, 결과 시각과 demo 여부 | 최초 진입, 빈 결과, 잘못된 기간, 검색 실패를 구분한다. |

`/classrooms/{id}` 별도 페이지는 제거하고 `/classrooms?classroom_id={id}`에서 같은 정보를
선택해 본다. 세 화면 외의 상세 화면을 새로 만들지 않는다.

### 좌석 상태 표기

저장 계약의 enum은 이번 축소 단계에서 유지하고 화면 문구만 운영 용어로 매핑한다.

| 저장 값 | 화면 문구 | 의미 |
| --- | --- | --- |
| `OCCUPIED` | 재석 | 좌석 점유 관측이 confidence 기준 이상이다. 학생 신원을 뜻하지 않는다. |
| `VACANT` | 부재 | 좌석 비점유 관측이 confidence 기준 이상이다. 지정 학생 부재를 확정하지 않는다. |
| `UNKNOWN` | 확인 필요 | 미관측, 낮은 confidence 또는 신뢰할 수 없는 관측이다. |

색만으로 상태를 구분하지 않고 문구와 기호를 함께 사용한다. 관측 실패나 영상 없음은
`VACANT`로 바꾸지 않는다.

### demo와 운영 데이터 구분

- `DEMO_MODE_ENABLED=true`인 local/dev에서만 합성 영상과 고정 검색 catalog를 사용한다.
- demo 카드, 검색 결과와 JSON 응답은 `is_demo=true`를 유지한다.
- demo가 꺼졌고 실제 공급원이 없으면 `/monitoring`과 `/video-search`는 404가 아니라 설명이
  있는 빈 상태를 반환한다.
- `APP_ENV=prod`에서는 demo 활성화를 계속 거부한다.
- 화면 제목에 “실시간” 또는 “자연어”가 있더라도 실제 데이터 경로가 없으면 실제 스트림,
  모델 또는 의미 검색이 동작한다고 설명하지 않는다.

## 최소 API 계약

페이지 라우트는 OpenAPI에서 제외하고, JSON API는 Pydantic 요청·응답 모델과 공통 오류
envelope를 사용한다. 아래 외의 제품 도메인 API는 제거한다.

| 메서드 | 경로 | 용도 | 현재 계약 영향 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/classrooms` | 활성 강의실 목록 | 응답 필드 축소 시 **깨는 변경** |
| `GET` | `/api/v1/classrooms/{classroom_id}/occupancy` | 한 강의실의 좌석 지도와 현재 상태 | 인증 제거와 응답 필드 축소는 **깨는 변경** |
| `GET` | `/api/v1/video-streams` | 모니터링 source 목록과 상태 | demo 전용 등록 조건 변경 |
| `GET` | `/api/v1/video-streams/{stream_id}` | 한 source의 상태 | demo 전용 등록 조건 변경 |
| `POST` | `/api/v1/video-searches` | 부작용 없는 검색 실행 | 인증·CSRF 제거, 기존 요청 형태 유지 |
| `GET` | `/health` | 프로세스 기동 상태 | 변경 없음 |
| `GET` | `/health/ready` | 잔존 저장소 준비 상태 | index 대상 축소 |

좌석 요약 응답은 최소한 아래 정보를 포함한다.

- 강의실: `id`, `code`, `name`, `location`
- 좌석: `id`, `code`, `label`, `geometry`, `current_occupancy`
- 현재 점유: `state`, `source`, `confidence`, `observed_at`, `event_id`
- 집계: `total`, `occupied_count`, `vacant_count`, `unknown_count`, `last_observed_at`

일정, 마감 grace, 담당 직원 식별자, 생성·수정용 version과 operation ID는 공개 조회 응답에서
제거한다. 검색 요청의 `query`, `classroom_id`, `from`, `to`, `limit`과 현재 범위 검증은 유지한다.

## 제거 대상

### 제품 페이지와 form

아래 경로는 redirect나 권한 오류가 아니라 404가 돼야 한다. 경로를 제공하던 템플릿과 form
처리 코드도 함께 삭제한다.

- `/login`, `/logout`, `/account/password`
- `/employees*`, `/my/interview-waits*`, `/staff/interview-waits*`
- `/admin*`, `/notifications*`, `/events*`
- `/classrooms/{classroom_id}`

### JSON API

다음 API 묶음은 화면과 같은 변경에서 제거한다. OpenAPI 제외, 메뉴 제거, 권한 차단만으로는
충분하지 않다. `main.py`의 router 등록, router handler, schema, service, port, adapter와 해당
테스트가 잔존 기능에서 사용되지 않으면 함께 삭제한다. 제거된 URL은 직접 호출해도 404여야 한다.

- `/api/v1/auth*`, `/api/v1/users*`
- `/api/v1/employees*`, `/api/v1/employee-status-evaluations*`
- `/api/v1/interview-waits*`, `/api/v1/interview-wait-expirations*`
- `/api/v1/notifications*`, `/api/v1/notification-read-batches*`
- `/api/v1/admin*`, `/api/v1/events*`
- `/api/v1/after-hours-alerts*`, `/api/v1/mock-seat-observations*`
- 강의실·일정·좌석 생성, 수정, 비활성화 API
- 좌석 관측 이력 공개 조회 API

### 코드와 자산

| 처리 | 대상 |
| --- | --- |
| 디렉터리 제거 | `app/admin`, `app/audit`, `app/auth`, `app/employees`, `app/events`, `app/interview_waits`, `app/notifications`, `app/users` |
| 선택 축소 | `app/classrooms`: 조회, 점유 계산, 관측 저장만 유지; 담당 직원·CRUD·일정·경고·알림·감사 결합 제거 |
| 선택 축소 | `app/video_monitoring`: 합성 catalog와 규칙 검색을 demo adapter로 한정; 사용자·역할·CSRF 의존 제거 |
| 조립 축소 | `app/main.py`, `app/shared/dependencies.py`: 잔존 라우터·저장소만 import·초기화 |
| 설정 축소 | `app/shared/config.py`, `.env.example`: 인증·직원·면담·알림·범용 이벤트 전용 변수 제거 |
| 보안 코드 제거 | `app/shared/security.py`와 인증 cookie·token·password helper |
| seed 재작성 | `app/demo_seed.py`: 사용자와 직원 없이 강의실·좌석만 멱등 생성 |
| 템플릿 제거 | `templates/account`, `admin`, `auth`, `employees`, `events`, `interview_waits`, `notifications`, `users` |
| 공통 shell 축소 | `templates/base.html`: 세 메뉴, skip link, 본문, 공통 footer만 유지 |
| 스크립트 축소 | `static/forms.js`: 알림·CSRF·쓰기 form 코드 제거; 필요한 탐색·접근성 보조만 유지 |
| 테스트 제거·대체 | 삭제 도메인 전용 helper와 테스트를 제거하고 3화면·잔존 API 회귀 테스트로 대체 |

빈 `__init__.py`, 사용되지 않는 error·schema·helper와 제거된 기능 전용 CSS selector도 남기지
않는다. 단, 제거 작업과 무관한 전면 CSS 재설계는 하지 않는다.

## 잔존 FastAPI 구조

`예정` 구조는 기존 기능별 디렉터리와 포트·어댑터 규칙을 유지한다.

```text
webapps/fastapi/
├─ app/
│  ├─ main.py
│  ├─ classrooms/
│  │  ├─ adapters/
│  │  ├─ errors.py
│  │  ├─ models.py
│  │  ├─ ports.py
│  │  ├─ router.py
│  │  ├─ schemas.py
│  │  └─ service.py
│  ├─ video_monitoring/
│  ├─ shared/
│  └─ demo_seed.py
├─ demo_assets/
├─ static/
├─ templates/
│  ├─ classrooms/
│  ├─ video_monitoring/
│  └─ errors/
└─ tests/
```

실제 스트림, 검색 저장소 또는 추론 서비스가 생기기 전에는 구현체 하나를 위해 새 포트나
클래스 계층을 만들지 않는다. 프로세스 밖 I/O가 실제로 추가될 때만 기존 ADR 기준으로 포트를
추가한다.

## 서비스와 저장소 축소 규칙

`ClassroomService` 생성자는 `ClassroomRepository`, 좌석 confidence 기준, clock만 받는다.
공개 서비스 메서드는 다음 책임으로 제한한다.

- 활성 강의실 목록 조회
- 선택한 강의실과 활성 좌석 조회
- 현재 점유 집계
- 좌석 관측 batch의 전체 선검증, event ID 멱등 처리, 최신 관측만 current 상태에 적용
- 관측 이력 append

actor·role 판정, 담당 STAFF 연결, 감사 기록, 경고 생성, 알림 생성과 강의실·좌석 관리 메서드는
제거한다. 라우터는 서비스 호출과 HTTP 변환만 하고, 템플릿은 enum을 새로 판정하지 않는다.

memory와 MongoDB 어댑터는 아래 collection만 읽고 쓴다.

| 처리 | collection | 정책 |
| --- | --- | --- |
| 유지 | `classrooms`, `seats` | 기존 문서의 불필요한 legacy 필드는 읽을 때 무시하고 자동 삭제하지 않는다. |
| 유지 | `seat_observation_batches`, `seat_occupancy_history` | 멱등성과 최신 관측 판정을 위해 유지한다. |
| 사용 중지 | `after_hours_alerts` | index 초기화와 코드 참조를 제거하되 저장 데이터를 자동 삭제하지 않는다. |
| 사용 중지 | `users`, `refresh_tokens`, `employees`, `employee_status_history`, `employee_observations` | 앱에서 읽고 쓰지 않는다. 물리 삭제는 별도 승인 대상이다. |
| 사용 중지 | `interview_waits`, `interview_wait_history`, `notifications`, `notification_deliveries`, `audit_logs`, `events` | 앱에서 읽고 쓰지 않는다. 물리 삭제는 별도 승인 대상이다. |

## 설정 계약

**유지**

- `APP_ENV`
- `DATABASE_MODE`, `DATABASE_URL`, `DATABASE_NAME`, `DATABASE_CONNECT_TIMEOUT_SECONDS`
- `DEMO_MODE_ENABLED`
- `SEAT_OCCUPANCY_CONFIDENCE_THRESHOLD`
- `PAGE_SIZE_DEFAULT`, `PAGE_SIZE_MAX`
- 선택적 MongoDB 테스트의 `TEST_DATABASE_URL`

**제거**

- 모든 `JWT_*`, `CSRF_SECRET`, `AUDIT_IP_HASH_SECRET`, `WEB_ORIGIN`
- 모든 `AUTH_*`
- `EMPLOYEE_*`, `INTERVIEW_WAIT_*`
- `NOTIFICATION_*`, `MOCK_INPUTS_ENABLED`
- 범용 이벤트만 사용하던 `HIGH_CONFIDENCE_THRESHOLD`, `MEDIUM_CONFIDENCE_THRESHOLD`

앱은 local memory mode에서 인증 비밀값 없이 시작돼야 한다. MongoDB mode의 URL·database 검증,
memory mode의 local 전용 제한, prod의 demo 금지는 유지한다.

## 요구사항

| 번호 | 요구사항 | 필수 여부 |
| --- | --- | --- |
| R1 | 제품 shell은 세 메뉴만 렌더링하고 `/`는 `/classrooms`로 이동한다. | 필수 |
| R2 | 로그인·계정·역할·알림 UI와 관련 cookie를 생성하지 않는다. | 필수 |
| R3 | 삭제 대상 페이지와 API는 handler·router 등록·전용 구현이 제거돼 직접 호출 시 404이며 OpenAPI에도 나타나지 않는다. | 필수 |
| R4 | `/classrooms` 한 화면에서 강의실을 선택하고 좌석 지도와 상태 집계를 볼 수 있다. | 필수 |
| R5 | 좌석 상태는 색 외의 문구와 기호로 표시하고 미관측을 부재로 처리하지 않는다. | 필수 |
| R6 | `/monitoring`은 source별 영상·연결 상태·마지막 상태 시각과 정상·빈·오류 상태를 표시한다. | 필수 |
| R7 | demo 영상과 검색 결과는 local/dev에서만 제공하고 항상 demo임을 표시한다. | 필수 |
| R8 | `/video-search`는 1~200자 검색어와 강의실·기간·limit 조건을 검증한다. | 필수 |
| R9 | 규칙 기반 검색은 일치 이유를 반환하고 빈 결과를 오류와 구분한다. | 필수 |
| R10 | `ClassroomService`와 `video_monitoring`은 `auth`, `users`, `employees`, `notifications`, `audit`를 import하지 않는다. | 필수 |
| R11 | MongoDB 시작 시 잔존 collection index만 초기화한다. | 필수 |
| R12 | 사용 중지 collection을 시작·seed·migration 과정에서 자동 삭제하지 않는다. | 필수 |
| R13 | 라우터 응답과 오류는 Pydantic schema와 공통 오류 envelope를 사용한다. | 필수 |
| R14 | 세 화면은 키보드로 사용할 수 있고 정상·빈·오류 상태를 갖는다. | 필수 |
| R15 | 제거 대상 문자열·import·라우트가 코드와 테스트에 남지 않는다. | 필수 |

## 사용자 흐름

1. 운영자가 `/`에 접근하면 로그인 없이 `/classrooms`로 이동한다.
2. 좌석 현황에서 강의실을 선택하고 재석·부재·확인 필요 상태와 마지막 관측을 확인한다.
3. 실시간 모니터링에서 같은 강의실의 영상 source와 연결 상태를 확인한다.
4. 자연어 검색에 문장을 입력하고 필요한 경우 강의실과 기간을 좁힌다.
5. 결과가 있으면 일치 이유와 시각을 확인하고, 없으면 검색 조건을 바꾼다.

세 화면에는 상태 확인 외의 생성·수정·해결·알림·외부 전송 작업이 없다.

## 예외와 실패 상황

| 상황 | 기대 동작 |
| --- | --- |
| 활성 강의실이 없음 | 좌석 현황에 빈 상태를 표시하고 다른 제품 기능으로 유도하지 않는다. |
| 강의실은 있으나 좌석이 없음 | 선택한 강의실 안에 좌석 없음 상태를 표시한다. |
| 좌석 관측이 없음 | `UNKNOWN`·확인 필요와 관측 없음 문구를 표시한다. |
| 카메라 source가 없음 | 영상 없음 또는 미연동 상태를 표시하고 학생 부재로 해석하지 않는다. |
| demo가 비활성이고 실제 provider가 없음 | 모니터링과 검색 화면은 유지하되 운영 데이터 없음 상태를 표시한다. |
| 검색어가 비었거나 200자를 넘음 | 페이지는 입력 오류, API는 422 `VALIDATION_ERROR`를 반환한다. |
| 시작 시각이 종료 시각보다 늦음 | 검색을 실행하지 않고 422 오류를 반환한다. |
| 검색 결과가 없음 | 200과 빈 결과를 반환한다. |
| 저장소를 사용할 수 없음 | 부분 데이터나 demo로 대체하지 않고 503을 반환한다. |
| 알 수 없는 경로 또는 제거된 경로 | 404를 반환하며 로그인으로 redirect하지 않는다. |
| 예상하지 못한 오류 | 내부 경로·쿼리·비밀값 없이 공통 500 오류를 반환한다. |

## API 제거와 데이터 보존

이번 작업은 기존 공개 계약과 ADR-0006의 단계적 호환 결정을 깨는 **API 제거 배포**다. 화면만
제거한 뒤 API를 숨겨 두는 중간 상태를 최종 결과로 허용하지 않는다. 외부 소비자 조사와 전환
준비는 API를 남겨 두기 위한 조건이 아니라 삭제 배포 전에 끝내야 하는 선행 작업이다.

1. 배포 환경의 access log, 호출 주체와 운영 문서를 조사해 제거 API 소비자 목록을 만든다.
2. 소비자가 있으면 같은 삭제 배포 전에 호출을 종료하거나 잔존 최소 API로 이전한다.
3. 새 제품 경계를 결정하는 ADR을 새로 추가해 ADR-0006을 대체할 근거를 남긴다.
4. rollback 시점과 이전 배포 artifact 또는 branch를 확보한다.
5. 화면과 API를 같은 변경에서 삭제하고, 직접 URL 호출이 404인지 회귀 테스트한다.

소비자 이전이나 breaking-change 승인이 끝나지 않으면 API를 숨겨서 배포하지 않고 변경 전체를
merge하지 않는다. 반면 승인이 끝난 삭제 배포에는 deprecated API나 임시 호환 handler를 남기지
않는다.

API 코드 제거와 MongoDB collection 데이터 삭제는 별개다. 사용 중지 collection은 애플리케이션이
더 이상 읽거나 쓰지 않지만, 보존·export·삭제 책임자가 정해질 때까지 자동 삭제하지 않는다.
이 명세는 collection 데이터 삭제를 승인하지 않는다.

## 구현 순서

1. **계약 고정**: 현재 라우트와 import 목록을 스냅샷 테스트로 남기고 제거 API 소비자 이전과
   breaking-change 승인을 완료한다.
2. **최소 shell 구성**: `base.html`, `/`, 예외 처리에서 역할·계정·알림 의존을 제거하고 세
   화면을 로그인 없이 렌더링한다.
3. **좌석 분리**: `ClassroomService`, schema, repository에서 사용자·직원·일정·경고·알림·감사
   결합과 관리 API를 제거한다.
4. **demo 분리**: `video_monitoring`에서 사용자·CSRF 의존을 제거하고 demo off의 빈 상태를
   추가한다.
5. **조립 축소**: `main.py`, `shared/dependencies.py`, database index 초기화, config와 seed를
   잔존 기능만 사용하도록 줄인다.
6. **물리 코드 제거**: 삭제 대상 API handler와 더 이상 import되지 않는 기능 디렉터리, schema,
   service, port, adapter, 템플릿, 정적 코드와 전용 테스트를 삭제한다.
7. **검증**: 라우트·OpenAPI·화면 상태, memory/MongoDB adapter, lint, format, type, 전체 테스트를
   실행한다.

각 단계가 끝날 때 import graph를 확인한다. 삭제 대상 모듈을 잔존 서비스가 하나라도 import하면
다음 단계로 넘어가지 않는다.

## 완료 조건

- [ ] `/`, `/classrooms`, `/monitoring`, `/video-search`와 health 이외의 제품 페이지가 없다.
- [ ] 제품 탐색에는 강의실 좌석 현황, 실시간 모니터링, 자연어 검색만 보인다.
- [ ] 삭제 대상 페이지와 API의 handler·router 등록·전용 구현이 없고 직접 호출 결과가 모두 404다.
- [ ] 삭제 대상 API가 `include_in_schema=False`, deprecation 응답 또는 호환 라우트로 남아 있지 않다.
- [ ] `app`에서 삭제 대상 기능 디렉터리와 그 import가 사라졌다.
- [ ] local memory mode가 인증·보안 비밀값 없이 시작된다.
- [ ] 좌석 화면의 정상·강의실 없음·좌석 없음·미관측·저장소 오류를 검증했다.
- [ ] 모니터링 화면의 연결됨·영상 없음·provider 없음·조회 오류를 검증했다.
- [ ] 자연어 검색의 결과 있음·빈 결과·잘못된 검색어·잘못된 기간을 검증했다.
- [ ] demo가 prod에서 거부되고 local/dev 응답은 `is_demo=true`다.
- [ ] 좌석 관측 batch 멱등성과 오래된 관측의 current 미적용이 유지된다.
- [ ] MongoDB 어댑터가 잔존 네 collection만 초기화하고 읽고 쓴다.
- [ ] 사용 중지 collection을 삭제하는 코드나 migration이 없다.
- [ ] `python -m ruff check app tests`가 통과한다.
- [ ] `python -m ruff format --check app tests`가 통과한다.
- [ ] `python -m mypy app tests`가 통과한다.
- [ ] `python -m pytest -q`가 통과한다.
- [ ] 기존 문서는 수정·삭제되지 않았고 새 명세 파일만 문서 변경으로 남는다.

## 미결정 사항

| 항목 | 구현을 막는가 |
| --- | --- |
| 제품 로그인 없이 운영 접근을 통제할 방식: 내부망, trusted reverse proxy, 상위 시스템 인증 위임 | local 축소는 막지 않지만 prod 배포를 막는다. |
| 기존 API 소비자의 이전과 제거 승인 | 삭제 배포의 merge를 막는다. API를 숨겨 유지하는 우회는 허용하지 않는다. |
| ADR-0006을 대체할 새 제품 경계 결정 | 삭제 배포의 merge를 막는다. 최종 구현에는 호환 API를 남기지 않는다. |
| 실제 stream의 브라우저 전달 방식과 화면 상태 갱신 방식 | 실제 실시간 영상 완료를 막지만 이번 축소는 막지 않는다. |
| worker→deeplearning→FastAPI 관측 전달 계약 | 실제 좌석 자동 갱신을 막지만 이번 축소는 막지 않는다. |
| 자연어 검색의 운영 metadata source와 규칙 검색 이후 방식 | 운영 검색 완료를 막지만 demo 유지와 이번 축소는 막지 않는다. |
| 사용 중지 MongoDB 데이터의 보존 기간과 삭제 책임자 | 데이터 물리 삭제를 막는다. |

## 관련 문서

- [공통 에이전트 계약](../agents/AGENTS.md)
- [FastAPI 에이전트 규칙](../agents/fastapi-agent.md)
- [문서 작성 규칙](../conventions/documentation-convention.md)
- [API 규칙](../conventions/api-convention.md)
- [아키텍처 개요](../architecture/overview.md)
- [데이터 흐름](../architecture/data-flow.md)
- [FastAPI 계층형 구조와 경계 포트](../architecture/decisions/ADR-0002-fastapi-layered-with-ports.md)
- [설계 패턴 적용 범위](../architecture/decisions/ADR-0005-design-pattern-scope.md)
- [v2 제품 경계와 레거시 호환성](../architecture/decisions/ADR-0006-v2-legacy-compatibility.md)
- [FastAPI 현재 구현](../../webapps/fastapi/README.md)
- [기능 명세 템플릿](../templates/feature-spec-template.md)
