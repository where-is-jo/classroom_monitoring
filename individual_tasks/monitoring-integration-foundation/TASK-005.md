# TASK-005 학생 상태 SSE와 화면 갱신

**목적**: 신규 탐지로 계산된 상태를 실시간 영상과 강의실 대시보드에 반영한다.
**대상 독자**: FastAPI SSE·Jinja2·브라우저 JavaScript 구현자.

## 선행 의존성

[TASK-004](TASK-004.md).

## 예상 소유 파일

- `webapps/fastapi/app/student_monitoring/router.py`
- `webapps/fastapi/app/student_monitoring/service.py`
- `webapps/fastapi/templates/video_monitoring/`
- `webapps/fastapi/templates/classrooms/`
- `webapps/fastapi/static/monitoring.js`
- `webapps/fastapi/static/classrooms.js`
- 대응 브라우저 harness와 API 테스트

## 구현 범위

- 신규 `student-state-events` SSE endpoint를 추가한다.
- 신규 추론 이벤트에서 학생 상태를 계산한 뒤 강의실별 이벤트를 발행한다.
- 같은 `event_id` 재수신에서는 detection·occupancy·student-state SSE를 중복 발행하지 않는다.
- 실시간 영상 bbox 라벨은 FastAPI가 확인한 활성 학생 이름 또는 안전한 `사람` 표시를
  사용한다. 모델이 이름을 보내게 하지 않는다.
- 강의실 대시보드는 초기 REST 상태를 렌더링하고 SSE로 학생 이름, 지정 좌석, 현재 상태,
  신뢰도, 마지막 관측을 갱신한다.
- 기존 occupancy SSE는 좌석 점유 정보로 유지하며 `VACANT`를 학생 `ABSENT`라고 표시하지
  않는다.
- SSE parse 오류는 해당 이벤트만 무시하고 EventSource 기본 재연결을 유지한다.
- unload에서 EventSource를 닫는다.

## 제약

- 인메모리 broadcaster는 단일 FastAPI 프로세스 범위만 보장한다.
- 이벤트 replay와 외부 broker는 이번 작업에서 만들지 않는다.
- SSE payload에 embedding, 얼굴 bbox 이미지, 원본 이미지, 내부 Mongo 문서를 넣지 않는다.

## 검증

- 강의실별 SSE 필터링과 heartbeat.
- 신규/중복 event 발행 횟수.
- PRESENT→WRONG_SEAT→UNKNOWN DOM 갱신.
- 실시간 영상 bbox와 식별/미식별 라벨.
- malformed event, 재연결, unload cleanup.
- `VACANT`와 `ABSENT` 문구 분리.

## 구현 결과

- [x] `/api/v1/classrooms/{classroom_id}/student-state-events`를 추가하고 type·강의실별로
  필터링하며 설정 기반 retry·heartbeat와 연결 종료 구독 해제를 구현했다.
- [x] 신규 inference event에서 식별 후보 학생의 최신 상태를 발행하고, 같은 `event_id`
  재수신은 detection·occupancy·student-state를 모두 중복 발행하지 않는다.
- [x] detection SSE bbox에는 임계값 이상 활성 학생 이름 또는 안전한 `사람` 라벨만
  보강하며 identity confidence·face bbox·이미지·embedding을 내보내지 않는다.
- [x] 강의실 좌석 현황과 학생 상태 표는 REST 초기 상태를 적용하고, 그 사이 도착한 SSE를
  버퍼링한 뒤 `PRESENT`·`WRONG_SEAT`·`UNKNOWN` 변경분을 반영한다.
- [x] malformed SSE는 해당 이벤트만 무시하고 EventSource 자동 재연결을 유지하며,
  unload에서 occupancy·student-state EventSource를 모두 닫는다.
- [x] 좌석 `VACANT`의 `부재`와 학생 `UNKNOWN`의 `확인 필요`를 별도 DOM 상태로 유지하고
  학생 `ABSENT`를 만들지 않는다.

검증 결과: FastAPI 전체 `pytest -q` 746건, Ruff check·format check, mypy와
두 JavaScript 파일 각각의 `node --check`를 통과했다.
