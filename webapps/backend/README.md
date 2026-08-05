# backend

FastAPI 기반 API 서버 디렉터리다. 외부 클라이언트가 시스템에 접근하는 유일한 진입점이다.

> 현재 상태: 구현 전. 이 문서는 구현이 시작될 때 지켜야 할 책임 범위를 정의한다.
> 아직 존재하지 않는 엔드포인트와 실행 명령은 여기에 적지 않는다.

## 서비스 목적

프론트엔드와 외부 클라이언트의 요청을 받아 인증·권한을 판정하고,
비즈니스 로직을 수행한 뒤 저장된 데이터와 추론 결과를 표준화된 형태로 제공한다.

## 책임

- HTTP API 정의와 요청·응답 스키마 관리
- 인증 및 권한 판정
- 비즈니스 로직 조정(여러 서비스 호출의 순서와 실패 처리)
- inference·stream-server 등 내부 서비스 연동
- 데이터 저장소 접근
- 오류 처리와 오류 응답 형식 통일

## 디렉터리 구조

기술 계층별이 아니라 **기능(도메인)별**로 나눈다.
기능 하나를 추가·삭제·리뷰할 때 디렉터리 하나만 보면 된다.

```text
app/
├─ events/                 탐지 이벤트
│  ├─ router.py            HTTP 관심사만. FastAPI 의존 가능
│  ├─ service.py           비즈니스 로직. 포트에만 의존
│  ├─ schemas.py           Pydantic 요청·응답 스키마
│  ├─ models.py            도메인 모델(dataclass)
│  ├─ ports.py             외부 I/O 인터페이스(Protocol)
│  └─ adapters/            포트 구현체
│     └─ mongo_repository.py
├─ cameras/                카메라 관리 (동일 구조)
├─ auth/                   인증·권한 (동일 구조)
└─ shared/                 공통 설정·예외·의존성 조립
```

호출 방향은 `router → service → port ← adapter`다.
**서비스 계층은 어댑터를 직접 import하지 않는다.**
어댑터를 서비스에 연결하는 조립 코드는 `shared/`에 한 곳으로 모은다.

포트는 프로세스 외부 I/O 경계 4곳(저장소·추론 클라이언트·객체 저장소·알림)에만 만든다.
선택 배경과 포트 판단 기준은 [ADR-0002](../../docs/architecture/decisions/ADR-0002-backend-layered-with-ports.md)에 있다.

## 포함해야 할 기능

- 라우터 계층(HTTP 관심사만 담당)
- 서비스 계층(비즈니스 로직, 프레임워크 비의존)
- Pydantic 스키마(요청·응답 검증)
- 포트 정의와 어댑터 구현(저장소, 추론 클라이언트, 객체 저장소, 알림)
- 의존성 조립 지점
- 오류 핸들러와 로깅

## 포함하지 않아야 할 기능

- 모델 로딩과 추론 연산 자체(→ `inference`)
- 스트림 연결 유지와 프레임 디코딩(→ `stream-server`)
- 화면 렌더링 로직
- 라우터 함수 안에 직접 작성한 비즈니스 로직
- 비밀값의 소스 코드 내 하드코딩

## 예상 기술

| 항목 | 상태 | 비고 |
| --- | --- | --- |
| 언어 | Python | 타입 힌트 필수 |
| 웹 프레임워크 | FastAPI | 프로젝트 전제 |
| 검증 | Pydantic | 요청·응답 스키마 |
| 메타데이터 저장소 | MongoDB ([ADR-0003](../../docs/architecture/decisions/ADR-0003-metadata-store-mongodb.md)) | 저장소 포트 뒤에 격리 |
| 객체 저장소 | MinIO ([ADR-0004](../../docs/architecture/decisions/ADR-0004-object-storage-minio.md)) | S3 호환 범위에서만 사용 |
| 캐시·큐 | 결정 필요 | Redis 후보 |
| 내부 구조 | 계층형 + 경계 포트 ([ADR-0002](../../docs/architecture/decisions/ADR-0002-backend-layered-with-ports.md)) | |

## 다른 서비스와의 관계

- `frontend`: 유일한 API 소비자로 가정한다. 계약 변경 시 프론트엔드 영향을 먼저 확인한다.
- `inference`: 추론 요청을 보내고 결과를 받는다. 통신 방식(동기 HTTP / 큐)은 **결정 필요**.
- `stream-server`: 스트림 연결 상태와 메타데이터를 주고받는다. 영상 바이트 자체를 중계할지는 **결정 필요**.
- `monitoring`: 지표 노출 엔드포인트를 제공한다. 지표 이름은 `smart_office_` 접두사를 사용한다.
- `RPAs`: 자동화 워크플로가 API를 호출할 수 있다. 이 경우도 일반 클라이언트와 같은 계약을 따른다.

## 향후 구현 시 필요한 환경변수

값의 취급과 명명 규칙, 필수값 검증 방식은 [환경변수 규칙](../../docs/conventions/environment-convention.md)을 따른다.

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `APP_ENV` | 실행 환경 구분 | `local` / `dev` / `prod` |
| `DATABASE_URL` | MongoDB 접속 정보 | 비밀값. 기본값 없이 주입 |
| `INFERENCE_SERVICE_URL` | 추론 서비스 주소 | 통신 방식 확정 후 필요 여부 재검토 |
| `STREAM_SERVER_URL` | 스트림 서버 주소 | 동일 |
| `OBJECT_STORAGE_ENDPOINT` | MinIO 주소 | |
| `OBJECT_STORAGE_ACCESS_KEY` | MinIO 접근 키 | 비밀값 |
| `OBJECT_STORAGE_SECRET_KEY` | MinIO 비밀 키 | 비밀값 |
| `JWT_SECRET` | 토큰 서명 키 | 값은 항상 외부 주입 |

명명 규칙과 필수값 검증 방식은 [환경변수 규칙](../../docs/conventions/environment-convention.md)을 따른다.

## 테스트 전략

- 서비스 계층은 포트를 테스트 대역으로 대체해 단위 테스트한다. 실제 MongoDB와 MinIO가 필요 없다.
- 라우터는 상태 코드, 검증 실패, 오류 응답 형식을 API 테스트로 검증한다.
- 외부 서비스(추론, 저장소)는 테스트에서 대역으로 대체한다.
- 계약 변경 시 기존 응답 스키마 테스트를 함께 갱신한다.

## 관련 문서

- [백엔드 에이전트 규칙](../../docs/agents/backend-agent.md)
- [FastAPI 기능 추가 절차](../../docs/skills/create-fastapi-feature/SKILL.md)
- [API 규칙](../../docs/conventions/api-convention.md)
- [아키텍처 개요](../../docs/architecture/overview.md)
- [데이터 흐름](../../docs/architecture/data-flow.md)
