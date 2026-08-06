# fastapi

FastAPI 웹 애플리케이션 디렉터리다. API와 화면을 함께 제공한다.

> 현재 상태: **최소 골격 동작**. 이벤트 목록·상세 화면과 JSON API가 실행된다.
> 데이터는 인메모리 샘플이며 MongoDB·MinIO 어댑터는 아직 없다.
> 인증·권한, 실시간 갱신, 추론 연동도 구현되지 않았다.

## 실행 방법

```bash
cd webapps/fastapi
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

브라우저에서 `http://127.0.0.1:8000`을 열면 이벤트 목록으로 이동한다.
외부 의존 서비스가 필요 없다. `.env`도 없어도 기동한다(모든 설정에 기본값이 있다).

설정을 바꾸려면 `.env.example`을 `.env`로 복사해 값을 채운다.

### 엔드포인트

| 경로 | 설명 |
| --- | --- |
| `GET /` | `/events`로 리다이렉트 |
| `GET /events?limit=&offset=` | 이벤트 목록 화면 |
| `GET /events/{event_id}` | 이벤트 상세 화면 |
| `GET /health` | 기동 확인 |
| `GET /api/v1/events?limit=&offset=` | 이벤트 목록 JSON |
| `GET /api/v1/events/{event_id}` | 이벤트 상세 JSON |
| `GET /docs` | 자동 생성 API 문서 |

### 테스트

```bash
cd webapps/fastapi
python -m pytest
```

외부 의존성 없이 실행된다.

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
인증과 권한을 판정하고, 비즈니스 로직을 수행하고, 저장된 데이터와 추론 결과를
API 응답 또는 Jinja2 템플릿으로 렌더링한 화면으로 제공한다.

## 책임

- HTTP API 정의와 요청·응답 스키마 관리
- Jinja2 템플릿 렌더링과 화면 구성
- 인증 및 권한 판정
- 비즈니스 로직 조정(여러 서비스 호출의 순서와 실패 처리)
- deeplearning·worker 등 내부 서비스 연동
- 데이터 저장소 접근
- 오류 처리와 오류 응답·오류 화면

## 디렉터리 구조

기술 계층별이 아니라 **기능(도메인)별**로 나눈다.
기능 하나를 추가·삭제·리뷰할 때 디렉터리 하나만 보면 된다.

```text
app/
├─ main.py                 앱 조립. 라우터 등록, 정적 마운트, 예외 핸들러
├─ events/                 탐지 이벤트
│  ├─ router.py            HTTP 관심사. page_router(HTML) + api_router(JSON)
│  ├─ service.py           비즈니스 로직. 포트에만 의존
│  ├─ rules.py             탐지 결과를 업무 의미로 바꾸는 순수 함수
│  ├─ schemas.py           Pydantic 요청·응답 스키마
│  ├─ models.py            도메인 모델(dataclass)
│  ├─ ports.py             외부 I/O 인터페이스(Protocol)
│  └─ adapters/            포트 구현체
│     └─ memory_repository.py    인메모리. mongo_repository.py는 다음 단계
└─ shared/                 공통 설정·예외·의존성 조립
   ├─ config.py            pydantic-settings
   ├─ errors.py            도메인 예외와 오류 응답 형식
   ├─ dependencies.py      어댑터 조립. 저장소 교체 시 고치는 유일한 파일
   └─ templating.py        Jinja2 설정

templates/                 Jinja2 템플릿
├─ base.html               공통 레이아웃
├─ events/                 기능별 템플릿
└─ errors/                 오류 화면

static/                    css, 브라우저 스크립트, 이미지
tests/                     외부 의존성 없이 실행되는 테스트
```

기능이 늘어나면 `app/cameras/`, `app/auth/`처럼 같은 구조의 디렉터리를 추가한다.

호출 방향은 `router → service → port ← adapter`다.
**서비스 계층은 어댑터를 직접 import하지 않는다.**
어댑터를 서비스에 연결하는 조립 코드는 `shared/`에 한 곳으로 모은다.

포트는 프로세스 외부 I/O 경계 4곳(저장소·추론 클라이언트·객체 저장소·알림)에만 만든다.
선택 배경과 포트 판단 기준은 [ADR-0002](../../docs/architecture/decisions/ADR-0002-fastapi-layered-with-ports.md)에 있다.

포트 외의 설계 패턴(파사드·Strategy·Observer 등)을 언제 만들어도 되는지는
[ADR-0005](../../docs/architecture/decisions/ADR-0005-design-pattern-scope.md)의 판정 질문으로 정한다.
현재는 모두 "아직 만들지 않는다" 상태이며, `service.py`가 라우터에 대한 파사드 역할을 겸한다.

템플릿은 기능별로 나누되 `app/` 밖에 둔다. Python 코드와 템플릿 파일을 섞지 않는다.

## 포함해야 할 기능

- 라우터 계층(HTTP 관심사만 담당)
- 서비스 계층(비즈니스 로직, 프레임워크 비의존)
- Pydantic 스키마(요청·응답 검증)
- 포트 정의와 어댑터 구현(저장소, 추론 클라이언트, 객체 저장소, 알림)
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
| 메타데이터 저장소 | MongoDB ([ADR-0003](../../docs/architecture/decisions/ADR-0003-metadata-store-mongodb.md)) | 저장소 포트 뒤에 격리 |
| 객체 저장소 | MinIO ([ADR-0004](../../docs/architecture/decisions/ADR-0004-object-storage-minio.md)) | S3 호환 범위에서만 사용 |
| 캐시·큐 | 결정 필요 | Redis 후보 |
| 내부 구조 | 계층형 + 경계 포트 ([ADR-0002](../../docs/architecture/decisions/ADR-0002-fastapi-layered-with-ports.md)) | |
| 실시간 화면 갱신 | 결정 필요 | 폴링 / SSE / WebSocket 후보 |

## 다른 서비스와의 관계

- [deeplearning](../../deeplearning/README.md): 추론 요청을 보내고 결과를 받는다. 통신 방식은 **결정 필요**.
- [worker](../../worker/README.md): 스트림 연결 상태와 메타데이터를 주고받는다.
- [monitoring](../../monitoring/README.md): 지표 노출 엔드포인트를 제공한다. 지표 이름은 `smart_office_` 접두사를 사용한다.
- [RPAs](../../RPAs/README.md): 자동화 워크플로가 API를 호출할 수 있다. 일반 클라이언트와 같은 계약을 따른다.

브라우저는 이 서비스만 호출한다. deeplearning과 worker를 직접 호출하지 않는다.

## 향후 구현 시 필요한 환경변수

값의 취급과 명명 규칙, 필수값 검증 방식은 [환경변수 규칙](../../docs/conventions/environment-convention.md)을 따른다.

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `APP_ENV` | 실행 환경 구분 | `local` / `dev` / `prod` |
| `DATABASE_URL` | MongoDB 접속 정보 | 비밀값. 기본값 없이 주입 |
| `INFERENCE_SERVICE_URL` | deeplearning 서비스 주소 | 통신 방식 확정 후 필요 여부 재검토 |
| `WORKER_URL` | worker 주소 | 동일 |
| `OBJECT_STORAGE_ENDPOINT` | MinIO 주소 | |
| `OBJECT_STORAGE_ACCESS_KEY` | MinIO 접근 키 | 비밀값 |
| `OBJECT_STORAGE_SECRET_KEY` | MinIO 비밀 키 | 비밀값 |
| `JWT_SECRET` | 토큰 서명 키 | 값은 항상 외부 주입 |

## 테스트 전략

- 서비스 계층은 포트를 테스트 대역으로 대체해 단위 테스트한다. 실제 MongoDB와 MinIO가 필요 없다.
- 라우터는 상태 코드, 검증 실패, 오류 응답 형식을 API 테스트로 검증한다.
- 템플릿 렌더링은 정상·빈 상태·오류 상태가 각각 렌더링되는지 확인한다.
- 계약 변경 시 기존 응답 스키마 테스트를 함께 갱신한다.

## 관련 문서

- [FastAPI 에이전트 규칙](../../docs/agents/fastapi-agent.md)
- `create-fastapi-feature` 스킬
- [API 규칙](../../docs/conventions/api-convention.md)
- [아키텍처 개요](../../docs/architecture/overview.md)
- [데이터 흐름](../../docs/architecture/data-flow.md)
