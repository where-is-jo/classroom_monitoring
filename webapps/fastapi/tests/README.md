# tests

**목적**: 새 테스트를 어느 디렉터리에 둘지 판단하는 기준을 정한다.
**대상 독자**: 이 서비스에 테스트를 추가하는 팀원과 AI 에이전트.

실행 명령은 [개발 가이드](../../../docs/guides/README.md#fastapi-검증)에 있다.
여기서 반복하지 않는다.

## 배치 기준

**`app`의 기능 디렉터리를 그대로 따른다.** `app/employees`를 고쳤으면 테스트는
`tests/employees`에 있다. 기능 하나를 추가·삭제·리뷰할 때 디렉터리 하나만 보면
되게 하려는 것이며, [결정 0002](../../../docs/architecture/decisions.md#0002--fastapi-계층형-구조와-경계-포트)가
`app`을 기술 계층이 아니라 기능별로 나눈 이유와 같다.

```text
tests/
├── conftest.py          공통 픽스처와 환경변수 설정
├── helpers/             기능별 테스트 조립 헬퍼 (테스트 아님)
├── admin/               app/admin
├── auth/                app/auth
├── classrooms/          app/classrooms
├── employees/           app/employees
├── events/              app/events (단계적 폐기 중)
├── interview_waits/     app/interview_waits
├── notifications/       app/notifications
├── users/               app/users
├── video_monitoring/    app/video_monitoring
├── shared/              app/shared, app/main.py, app/demo_seed.py
└── integration/         실제 MongoDB가 필요한 통합 테스트
```

파일 이름에 기능 이름을 반복하지 않는다. 디렉터리가 이미 말하고 있다.
`app`의 파일 이름을 따라간다 — `test_routes.py`, `test_service.py`,
`test_mongo_adapter.py`.

## 어디에 둘지 애매할 때

| 상황 | 위치 |
| --- | --- |
| 기능 하나에 속한다 | 해당 기능 디렉터리 |
| 여러 기능을 가로지른다 (역할별 홈, 제품 탐색) | `shared/` |
| 설정·템플릿·오류 응답·health 같은 앱 전역 관심사 | `shared/` |
| `app/audit`처럼 다른 기능의 어댑터와 함께 검증된다 | 검증 대상이 주로 속한 기능 디렉터리 |
| 실제 MongoDB가 있어야 돈다 | `integration/` |

`audit`에 전용 디렉터리를 두지 않은 것은 감사 기록 어댑터가 인증·사용자 어댑터와
같은 문서·index 계약 테스트에서 함께 검증되기 때문이다. 감사 기록만 대상으로 하는
테스트가 생기면 그때 `audit/`을 만든다.

## 규칙

- **기본 테스트는 외부 서비스를 요구하지 않는다.** memory mode와 대역 저장소를 쓴다.
  실제 MongoDB가 필요한 테스트는 `integration/`에 두고 `mongodb` marker를 붙인다.
  marker가 붙은 테스트는 `TEST_DATABASE_URL`이 없으면 skip한다.
- **성공 경로만 검증하는 테스트는 절반이다.** 실패 케이스를 함께 둔다.
- **테스트를 통과시키려고 테스트를 약화시키지 않는다.** 실패는 실패로 보고한다.
- 새 디렉터리를 만들면 `__init__.py`를 함께 둔다. 없으면 서로 다른 디렉터리의
  같은 이름 모듈이 충돌한다.

## 관련 문서

- [코딩 규칙의 테스트 절](../../../docs/conventions/coding-convention.md#테스트)
- [개발 가이드](../../../docs/guides/README.md)
- [fastapi README](../README.md)
