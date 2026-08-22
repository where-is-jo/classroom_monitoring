# 환경변수 규칙

**목적**: 설정값과 비밀값을 다루는 방식을 통일한다.
**대상 독자**: 서비스와 RPA를 구현하는 모든 사람과 AI 에이전트.

## 파일이 둘로 나뉜다: `.env.{local,dev,prod}`와 `config/settings.yml`

설정값은 "환경마다 달라야 하는가"에 따라 저장 위치가 갈린다. 아래 [비밀값 분리](#비밀값-분리)
표의 왼쪽 두 구분(일반 설정·판정 기준값)은 `config/settings.yml`에, 오른쪽 두 구분
(환경 의존 설정·비밀값)은 `.env.{local,dev,prod}`에 둔다.

각 서비스와 RPA는 `.env.example`과 `config/settings.yml`을 둔다.

- **`.env.example`은 커밋한다. 실제 값이 든 파일은 어떤 이름이든 커밋하지 않는다.**
  파일 이름의 `local`/`dev`/`prod`는 [환경 구분](#환경-구분)의 세 값과 같다.
  실제 값을 어느 파일에 적는지는 실행 방식에 따라 갈린다 — 바로 아래 절을 본다.
- `.env.example`에는 **변수 이름과 설명만** 넣는다. 실제 값은 넣지 않는다.
- **`config/settings.yml`은 커밋한다.** 환경과 무관하게 같은 값이므로 실제 값을 그대로
  적어 둔다 — 비밀값이 아니다.
- 새 환경변수나 yml 항목을 추가하면 같은 커밋에서 `.env.example`이나
  `config/settings.yml`을 갱신한다. 이걸 빠뜨리면 다른 사람의 실행이 깨진다.

```bash
# .env.example
# 실행 환경: local | dev | prod
APP_ENV=local

# fastapi API 주소 (예: http://localhost:8001)
API_BASE_URL=

# 메타데이터 저장소 접속 정보 — 비밀값, 운영에서는 별도 주입
DATABASE_URL=
```

```yaml
# config/settings.yml
# 재시도 횟수, 타임아웃처럼 어느 환경에서나 같은 값
request_timeout_seconds: 5
max_retry: 3
```

`.env.example`에서 값이 없는 항목은 비워둔다. 그럴듯한 가짜 값을 채워두면 실제 값으로
오인된다. 형식을 알려줘야 하면 주석에 예시로 적는다.

### 실행 방식에 따라 값을 채우는 파일이 다르다

**변수 이름의 기준은 언제나 커밋된 `.env.example` 하나다.** 값을 어디에 적는지만 갈린다.

| 실행 방식 | 값을 적는 곳 |
| --- | --- |
| 소스에서 직접 실행 (개발자 PC) | `<서비스>/.env.local` |
| 컨테이너로 실행 (로컬 compose 검증, dev·prod 서버) | `.docker/env/<서비스>.<환경>.env` |

컨테이너 쪽이 서비스 디렉터리 밖에 있는 이유는 **서버에 서비스 소스가 없기** 때문이다.
서버는 GHCR 이미지를 pull하고 `.docker/`만 받는다 ([결정 0017](../architecture/decisions.md)).

그래서 `<서비스>/.env.dev`와 `.env.prod`는 실제로 만들지 않는다. 로더는 계속 지원하므로
디버깅 목적으로 소스에서 dev 설정을 띄우고 싶으면 만들어 써도 된다.

### 두 계층을 섞지 않는다

컨테이너로 띄울 때 환경변수는 성격이 다른 두 계층으로 나뉜다. 어느 계층인지 먼저 정하고
파일을 고른다.

| 계층 | 예 | 컨테이너에 들어가나 | 파일 | 커밋 |
| --- | --- | --- | --- | --- |
| 컨테이너 앱 설정 | `DATABASE_URL`, `STREAM_SOURCES`, `SNAPSHOT_STORAGE_*` | 예 (`env_file`) | `.docker/env/<서비스>.<환경>.env` | 안 함 |
| 서드파티 자격증명 | MinIO root, Grafana admin, n8n | 예 (`env_file`) | `.docker/env/<서드파티>.<환경>.env` | 안 함 |

**compose 자체가 쓰는 값(이미지 태그, 호스트 경로, 대외 포트)은 파일로 빼지 않는다.**
[결정 0018](../architecture/decisions.md#0018--docker-compose-구성을-저장소에-커밋하고-localdev-파일을-나눈다)로
compose 파일을 커밋하면서 그 안에 직접 적기로 했다. `${...}` 치환에 쓰던
`.docker/.env.<환경>`은 `.env.*` 패턴에 걸려 커밋되지 않으므로, 치환에 의존하면
**저장소에서 받은 compose만으로는 실행할 수 없기 때문이다.**

그래서 `--env-file`도 쓰지 않는다. **환경을 고르는 것은 파일 이름이다.**

```bash
docker compose -f .docker/compose.main.dev.pc.yml  up -d   # dev · 개인 PC
docker compose -f .docker/compose.main.dev.gpu.yml up -d   # dev · GPU 서버
docker compose -f .docker/compose.main.local.yml   up -d   # local (한 대에서 전부)
```

**dev는 이름에 호스트가 하나 더 붙는다.**
[결정 0026](../architecture/decisions.md#0026--백엔드를-개인-pc에-두고-gpu가-필요한-것만-gpu-서버에-남긴다)으로
dev 환경이 기계 두 대에 걸치기 때문이다. 자세한 것은
[`.docker/README.md`](../../.docker/README.md)에 있다.

### 어떤 파일을 읽는지는 실제 OS 환경변수 `APP_ENV`가 정한다

소스에서 실행할 때는 셸에서 export한다(`export APP_ENV=dev`). 컨테이너로 띄울 때는
각 compose 파일이 `environment:`에 `APP_ENV`를 고정값으로 적고 `env_file` 경로도 그
환경으로 고정해 둔다 — 그래서 **고른 파일과 컨테이너 안의 `APP_ENV`가 어긋날 수 없다.**

**export하지 않으면 `local`로 본다** — 손이 덜 가는 local을 기본값으로 두는 원칙과 같다.

우선순위는 **실제 OS 환경변수 > `.env.{APP_ENV}` 파일 > `config/settings.yml`**이다.
`config/settings.yml`에 있는 값도 필요하면 `.env.*`나 실제 export로 즉석에서 재정의할
수 있다 — 다만 그렇게 임시로 바꾼 값을 커밋된 `config/settings.yml`에 반영할지는
별도로 판단한다.

Python 서비스는 Pydantic Settings의 `yaml_file`과 `settings_customise_sources`로
이 우선순위를 구현한다. 워커 네 개(`stream`·`inference`·`recorder`·`pipeline`)는
`worker/shared/settings_sources.py`의 공용 함수를 쓴다.

## 비밀값 분리

| 구분 | 예 | 취급 | 저장 위치 |
| --- | --- | --- | --- |
| 일반 설정 | 포트, 타임아웃, 로그 레벨, 샘플링 주기 | 기본값 허용 | `config/settings.yml` |
| 판정 기준값 | 유예 시간, 신뢰도 임계값, 좌석 판정 여유 | 기본값 허용. **코드에 박지 않는다** | `config/settings.yml` |
| 환경 의존 설정 | 서비스 주소, DB 이름, **로컬 대역과 실제 외부 연동을 고르는 스위치**(`DATABASE_MODE`, `INFERENCE_DEVICE`, `OBJECT_STORAGE_BACKEND`처럼 local/dev/prod에서 실제로 다른 값을 쓰는 것) | 기본값 없이 주입 | `.env.{local,dev,prod}` |
| 비밀값 | 비밀번호, API 키, 토큰, 인증서, 카메라 접속 정보, 객체 저장소 키 | 저장소에 어떤 형태로도 두지 않는다 | `.env.{local,dev,prod}` |

`APP_ENV` 자신은 파일 선택의 근거이므로 예외적으로 항상 `.env.{local,dev,prod}`에 둔다.

- **비밀값에 기본값을 주지 않는다.** 개발 편의로 넣은 기본값이 운영까지 따라간다.
- 비밀값을 로그·오류 메시지·응답에 출력하지 않는다.
- 설정 객체를 통째로 로그에 찍지 않는다.
- **비밀 관리 수단은 실행 호스트의 파일이다** ([결정 0017](../architecture/decisions.md)).
  dev/prod 값은 그 호스트의 `.docker/env/<서비스>.<환경>.env`에만 두고 소유자 전용
  권한으로 막는다(`chmod 600`). secret manager는 현 단계에서 도입하지 않는다 —
  prod가 아직 배포되지 않아 검증할 환경이 없다. prod 배포가 시작되면 재검토한다.
- 값을 저장소로 가져오지 않는다. 서버 값이 필요하면 그 호스트에서 직접 본다.

이미 커밋된 비밀값은 이력에서 지우는 것으로 해결되지 않는다.
해당 자격 증명을 폐기하고 팀에 알린다. [Git 규칙](./git-convention.md#커밋하지-않는-것) 참고.

## 이름 규칙

- **대문자 스네이크**를 쓴다: `STREAM_RECONNECT_MAX_RETRY`
- 접두사로 대상을 밝힌다: `DATABASE_`, `STREAM_`, `MODEL_`, `INFERENCE_`,
  `RECORDING_`, `OBJECT_STORAGE_`, `FACE_`
- **단위를 이름에 넣는다**: `REQUEST_TIMEOUT_SECONDS`, `MAX_UPLOAD_SIZE_BYTES`
- 불리언은 참일 때의 상태로 짓고 `true`/`false`를 쓴다: `ENABLE_GPU=true`
- 목록은 쉼표로 구분한다: `ALLOWED_ORIGINS=http://a,http://b`
- 같은 의미의 값을 서비스마다 다르게 부르지 않는다.

## 환경 구분

`APP_ENV`로 구분한다: `local` / `dev` / `prod`

| 환경 | 특징 |
| --- | --- |
| `local` | 개발자 PC. 외부 의존을 대역으로 대체할 수 있다 |
| `dev` | 공용 개발 환경. 실제 연동을 확인한다 |
| `prod` | 운영. 실제 학생 영상과 얼굴 데이터가 흐른다. **현재 배포하지 않는다** — 운영 접근 통제 방식이 `결정 필요`다 |

- **환경에 따라 코드 분기를 만들지 않는다.** 설정값만 다르게 한다.
  `if APP_ENV == "prod"` 같은 분기가 늘어나면 운영에서만 실행되는 경로가 생겨 검증이 어려워진다.
- 운영 데이터를 로컬로 복사하지 않는다.
- 각 환경의 실제 값은 저장소가 아니라 해당 환경에서 관리한다.
- 실행 전에 실제 OS 환경변수 `APP_ENV`를 export한다. 어떤 `.env.{local,dev,prod}` 파일을
  읽을지 이 값이 정한다. 자세한 내용은
  [파일이 둘로 나뉜다](#파일이-둘로-나뉜다-envlocaldevprod와-configsettingsyml) 참고.

## 기본값 허용 범위

**기본값을 줘도 되는 것**

- 어느 환경에서나 같은 값: 로그 레벨, 재시도 횟수, 페이지 크기 상한
- 없어도 안전하게 동작하는 선택 기능의 비활성 상태

**기본값을 주면 안 되는 것**

- 비밀값 전부
- 환경마다 달라야 하는 주소와 접속 정보
- 잘못된 값으로 조용히 동작하면 안 되는 것 (예: `MODEL_PATH`)

판단 기준은 "이 값이 틀린 채로 서비스가 뜨면 어떻게 되는가"다.
조용히 잘못 동작한다면 기본값을 두지 않는다.

이 구분은 [비밀값 분리](#비밀값-분리) 표의 저장 위치와 그대로 대응한다 — "기본값을
줘도 되는 것"은 `config/settings.yml`에, "기본값을 주면 안 되는 것"은
`.env.{local,dev,prod}`에 둔다.

## 필수 환경변수 검증

**프로세스 시작 시 필수 값을 검증하고, 없으면 즉시 종료한다.**

- 요청을 처리하다가 설정이 없어서 실패하는 상황을 만들지 않는다.
- 오류 메시지에 **어떤 변수가 없는지** 적는다. 값은 출력하지 않는다.
- 값의 형식(URL, 정수, 열거값)도 시작 시 확인한다.

Python 서비스는 Pydantic Settings 같은 수단으로 한 곳에서 검증한다.
설정을 읽는 코드가 여기저기 흩어지면 무엇이 필수인지 알 수 없게 된다.

## 관련 문서

- [Git 규칙](./git-convention.md)
- [코딩 규칙](./coding-convention.md)
- [RPA 규칙](../../RPAs/README.md)
