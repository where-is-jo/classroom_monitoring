# tests

**목적**: 새 테스트를 어느 디렉터리에 둘지 판단하는 기준을 정한다.
**대상 독자**: 이 서비스에 테스트를 추가하는 팀원과 AI 에이전트.

실행 명령은 [개발 가이드](../../../docs/guides/README.md#fastapi-검증)에 있다.
여기서 반복하지 않는다.

> **현재 `python -m pytest -q`가 수집 단계에서 실패한다.**
> `tests/auth/test_password_change_v2.py`가 제거된 `app.auth`를 import한다.
> 이전 제품 범위 축소에서 남은 파일이며, `tests/admin`, `tests/employees`,
> `tests/events`, `tests/interview_waits`, `tests/notifications`, `tests/users`,
> `tests/helpers`도 `__init__.py`만 남은 빈 디렉터리다.
> 정리는 코드 변경이므로 별도 작업으로 처리한다.

## 배치 기준

**`app`의 기능 디렉터리를 그대로 따른다.** `app/classrooms`를 고쳤으면 테스트는
`tests/classrooms`에 있다. 기능 하나를 추가·삭제·리뷰할 때 디렉터리 하나만 보면
되게 하려는 것이며, [결정 0001](../../../docs/architecture/decisions.md#0001--fastapi-계층형-구조와-경계-포트)이
`app`을 기술 계층이 아니라 기능별로 나눈 이유와 같다.

```text
tests/
├── conftest.py          공통 픽스처와 환경변수 설정
├── classrooms/          app/classrooms
├── video_monitoring/    app/video_monitoring
├── shared/              app/shared, app/main.py, app/demo_seed.py
└── integration/         실제 MongoDB가 필요한 통합 테스트
```

`students`, `face_enrollment`, `student_monitoring` 도메인이 생기면 같은 이름의
디렉터리를 함께 만든다.

파일 이름에 기능 이름을 반복하지 않는다. 디렉터리가 이미 말하고 있다.
`app`의 파일 이름을 따라간다 — `test_routes.py`, `test_service.py`,
`test_mongo_adapter.py`.

## 어디에 둘지 애매할 때

| 상황 | 위치 |
| --- | --- |
| 기능 하나에 속한다 | 해당 기능 디렉터리 |
| 여러 기능을 가로지른다 (제품 탐색, 화면 shell) | `shared/` |
| 설정·템플릿·오류 응답·health 같은 앱 전역 관심사 | `shared/` |
| 실제 MongoDB가 있어야 돈다 | `integration/` |

## 규칙

- **기본 테스트는 외부 서비스를 요구하지 않는다.** memory mode와 대역 저장소를 쓴다.
  실제 MongoDB가 필요한 테스트는 `integration/`에 두고 `mongodb` marker를 붙인다.
  marker가 붙은 테스트는 `TEST_DATABASE_URL`이 없으면 skip한다.
- **성공 경로만 검증하는 테스트는 절반이다.** 실패 케이스를 함께 둔다.
  대상 없음, 잘못된 입력, 저장소 실패, 미관측이 여기 해당한다.
- **테스트를 통과시키려고 테스트를 약화시키지 않는다.** 실패는 실패로 보고한다.
- **테스트 자산에 실제 사람의 얼굴을 쓰지 않는다.** 합성 이미지나 고정 벡터를 쓴다.
- 새 디렉터리를 만들면 `__init__.py`를 함께 둔다. 없으면 서로 다른 디렉터리의
  같은 이름 모듈이 충돌한다.

## 관련 문서

- [코딩 규칙의 테스트 절](../../../docs/conventions/coding-convention.md#테스트)
- [개발 가이드](../../../docs/guides/README.md)
- [fastapi README](../README.md)
