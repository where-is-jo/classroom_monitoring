# 개발 가이드

**목적**: 저장소를 받아서 실행하고, 검증하고, 변경분을 올릴 때까지 실제로 치는 명령을 모은다.
**대상 독자**: 새로 합류한 팀원.

여기 적힌 명령은 실제로 실행해 확인한 것이다. 확인하지 않은 명령은 넣지 않는다.

## 먼저 읽을 문서

1. [루트 README](../../README.md) — 저장소 전체 구조
2. [학생 모니터링 MVP 명세](../specs/student-monitoring-mvp.md) — 무엇을 만드는가
3. [아키텍처](../architecture/README.md) — 서비스 관계와 미결정 항목
4. 담당 영역의 서비스 README —
   [fastapi](../../webapps/fastapi/README.md) ·
   [worker](../../worker/README.md) ·
   [deeplearning](../../deeplearning/README.md) ·
   [monitoring/internal](../../monitoring/internal/README.md) ·
   [monitoring/external](../../monitoring/external/README.md) ·
   [RPAs](../../RPAs/README.md)
5. [개발 규칙](../conventions/) — 전부 읽을 필요는 없다. 필요할 때 본다

| 상황 | 문서 |
| --- | --- |
| 커밋·브랜치를 만들 때 | [Git 규칙](../conventions/git-convention.md) |
| 코드를 쓸 때 | [코딩 규칙](../conventions/coding-convention.md) |
| API를 만들거나 쓸 때 | [API 규칙](../conventions/api-convention.md) |
| 설정값을 다룰 때 | [환경변수 규칙](../conventions/environment-convention.md) |
| 문서를 쓸 때 | [문서 작성 규칙](../conventions/documentation-convention.md) |

## fastapi 실행

Python 3.12 이상. 외부 서비스 없이 기동한다.

```bash
cd webapps/fastapi
python -m pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --reload --port 8000
```

`http://127.0.0.1:8000`을 열면 `/classrooms`로 이동한다. **로그인은 없다.**
비밀값을 채우지 않아도 기동한다.

`.env`에서 `DEMO_MODE_ENABLED=true`로 바꾸면 개인정보 없는 합성 영상과 고정 검색
catalog가 붙어 `/monitoring`과 `/video-search`를 시연할 수 있다.
`APP_ENV=prod`에서는 활성화를 거부한다.

화면과 환경변수 전체는 [fastapi README](../../webapps/fastapi/README.md)가 기준이다.

> **`APP_ENV=prod`로 배포하지 않는다.** 운영 접근 통제 방식이 아직 정해지지 않았다
> ([결정 0010](../architecture/decisions.md#0010--mvp-제품-사용자를-관리자-하나로-한정한다)).

MongoDB를 붙이려면 `DATABASE_MODE=mongodb`와 `DATABASE_URL`, `DATABASE_NAME`을 준다.
`memory` mode는 `APP_ENV=local`에서만 허용된다.

## fastapi 검증

병합 전에 네 명령이 모두 통과해야 한다.

```bash
cd webapps/fastapi
python -m ruff check app tests
python -m ruff format --check app tests
python -m mypy app tests
python -m pytest -q
```

- 설정은 `webapps/fastapi/pyproject.toml`에 있다. **저장소 최상위가 아니다.**
- **mypy는 `strict`다.** 공개 함수에 타입 힌트가 없으면 실패한다.
- 기본 테스트는 memory mode와 대역 저장소를 쓰므로 외부 서비스가 필요 없다.

단일 테스트 실행:

```bash
python -m pytest tests/classrooms                               # 기능 단위
python -m pytest tests/classrooms/test_mongo_adapter.py         # 파일 단위
python -m pytest -k "임계값"                                     # 이름으로 필터
python -m pytest -q -m mongodb                                  # MongoDB 통합 테스트
```

테스트는 `app`과 같은 기능별 디렉터리로 나뉜다. 배치 기준은
[tests README](../../webapps/fastapi/tests/README.md)에 있다.

MongoDB 통합 테스트는 `TEST_DATABASE_URL`이 없으면 skip한다. 실제 MongoDB로 돌릴 때는
URL 경로에 `test_`로 시작하는 database 이름을 넣는다.

## worker 실행

`worker`는 파이프라인 단계별 워커로 나뉜다
([결정 0005](../architecture/decisions.md#0005--worker를-역할별-워커로-분리)).
`stream`과 `inference`는 프레임 버퍼로 이어져 `pipeline` 진입점으로 함께 돌고,
`recorder`는 별도 진입점으로 돈다. USB 카메라를 직접 쓰면 FFmpeg와 MediaMTX가 필요하다.

```bash
cd worker
python -m pytest -q                       # 장비도 모델도 MinIO도 없이 도는 전체 테스트

python -m pip install -r pipeline/requirements.txt
cp pipeline/.env.example pipeline/.env    # STREAM_SOURCES를 채운다
python -m pipeline.main                   # 수신 + 추론
```

`recorder`는 별도 진입점으로 영상을 세그먼트로 나눠 객체 저장소에 적재한다.
FFmpeg과 MinIO가 필요하다.

```bash
cd worker
python -m pip install -r recorder/requirements.txt
cp recorder/.env.example recorder/.env    # STREAM_SOURCES와 저장소 접속 정보를 채운다
python -m recorder.main
```

실행 절차와 환경변수는 [stream README](../../worker/stream/README.md)와
[recorder README](../../worker/recorder/README.md)에,
설정값의 근거와 겪은 문제는 [카메라 수집 구성](../../worker/stream/camera-guides.md)에 있다.

`deeplearning`, `monitoring`, `RPAs`에는 아직 실행 코드가 없다.
실행 방법을 지어내지 말고, 구현한 사람이 해당 서비스 README에 실제로 확인한 명령을 적는다.

## 환경변수

- `.env`는 커밋하지 않는다. `.env.example`만 커밋한다.
- 새 환경변수를 추가하면 **같은 커밋에서** `.env.example`을 갱신한다.
  빠뜨리면 다른 사람의 실행이 깨진다.
- 비밀값은 팀에서 정한 방법으로 받는다. 채팅이나 문서에 붙여넣지 않는다.

자세한 규칙은 [환경변수 규칙](../conventions/environment-convention.md)에 있다.

## 통합 실행과 배포

여러 서비스를 한 번에 띄우는 공식 수단은 아직 없다. docker compose 구성을 개인 로컬에서
쓰고 있으나 팀 공식 실행 수단으로 확정되지 않아 저장소에 포함되지 않는다.
배포 환경과 방식도 정해지지 않았다.
둘 다 [미결정 항목](../architecture/README.md#미결정-항목)이며, 확정하면
[결정 기록](../architecture/decisions.md)에 남기고 이 절을 절차로 바꾼다.

## 작업 흐름

1. 무엇을 만들지 정하고, 해당 서비스 README에서 그것이 그 서비스의 책임인지 확인한다.
2. `develop`에서 `<타입>/<설명>` 브랜치를 딴다.
   ([Git 규칙](../conventions/git-convention.md#브랜치))
3. 작업 종류에 맞는 규칙 문서와 절차를 연다.
   ([Agent 문서](../agents/AGENTS.md) · [프롬프트 양식](../prompts/))
4. 요청받은 범위만 구현한다. 눈에 띈 다른 문제는 고치지 말고 기록해 둔다.
5. 위 검증을 실행한다. 실행하지 못한 것은 그대로 밝힌다.
6. 문서 갱신이 필요한지 확인한다.
7. `develop`으로 Pull Request를 연다.

## 작업할 때 지킬 것

- **학생이 찍힌 영상을 개발용으로 로컬에 두지 않는다.** 테스트에는 공개 샘플이나
  직접 촬영한 무인 영상을 쓴다. 화면 캡처를 문서·이슈에 올릴 때 사람이 찍히지 않았는지 확인한다.
- **테스트 자산에 실제 사람의 얼굴을 쓰지 않는다.** 합성 이미지나 고정 벡터를 쓴다.
- **운영 데이터를 로컬로 복사하지 않는다.**
- **없는 서비스를 흉내 내는 코드를 제품 코드에 넣지 않는다.** 대역이 필요하면
  테스트 코드나 로컬 설정으로 분리한다.
- **각 서비스는 다른 서비스 없이도 기동되어야 한다.** 의존 서비스가 없을 때 죽지 말고
  해당 기능만 실패로 처리한다.

## 자주 나오는 질문

**새 웹 서비스는 어디에 만드나?**
`webapps/<service-name>/`. [프로젝트 초기화 프롬프트](../prompts/initialize-project.md)를 쓴다.

**새 RPA는?**
`RPAs/<rpa-name>/`. [RPA 규칙](../../RPAs/README.md)을 먼저 읽는다.

**문서와 코드가 다르면?**
코드가 사실이다. 임의로 맞추지 말고 불일치를 보고한다.

**AI 에이전트로 작업하려면?**
[docs/agents/AGENTS.md](../agents/AGENTS.md)를 에이전트에게 먼저 읽힌다.
GPT처럼 저장소를 직접 읽지 못하는 도구는
[GPT 코딩 프롬프트](../prompts/gpt-agent.md)를 쓴다.
