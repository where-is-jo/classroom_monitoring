# 현재 구현 기준선

**확인일**: 2026-08-15
**기준 커밋**: `d04b463`

이 문서는 구현 전에 확인된 사실만 기록한다. 로컬 데이터는 다시 실행하면 달라질 수
있으므로 식별자·학생 이름 같은 개인정보성 값은 기록하지 않는다.

## 이미 구현된 경로

- worker의 프레임 수집·샘플링과 YOLO 기반 사람 탐지
- worker에서 `POST /internal/inference/events`로 전송하는 HTTP handler, 제한 재시도,
  `event_id` 기반 FastAPI 멱등 저장
- 탐지 이벤트 MongoDB 저장 모델과 `student_id`, `identity_confidence`, `face_bbox` 선택 필드
- 실시간 모니터링 화면의 카메라별 detection SSE 구독과 bbox overlay
- 탐지를 직사각형 `seat.geometry`에 매핑해 좌석 관측을 기록하는 코드
- 강의실 좌석 현황 화면의 occupancy SSE 구독
- 좌석 지정 `seat_assignments` MongoDB 영속화
- `PRESENT`와 `WRONG_SEAT`를 계산하는 순수 판정 함수 및 단위 테스트
- 학생 상태 REST 경로와 Jinja2 화면 골격

## 아직 끊긴 경로

- worker 탐지기는 사람·휴대폰만 내며 학생 식별 결과를 만들지 않는다.
- worker 이벤트 payload는 FastAPI가 받을 수 있는 `student_id`, `identity_confidence`,
  `face_bbox`를 보내지 않는다.
- `StudentMonitoringService.list_student_states()`는 좌석과 지정을 조회한 뒤 빈 목록을
  반환한다.
- 내부 이벤트 router는 요청에 없는 `stream_id`, `classroom_id`를 빈 문자열로 만든다.
  service는 stream을 다시 찾지만 저장하는 `DetectionEvent`에는 해석된 값을 채우지 않는다.
- 학생 상태를 위한 SSE 이벤트와 브라우저 소비 코드가 없다.
- `ABSENT`에 필요한 수업 시간, 유예 시간, 카메라 정상 여부, 상태 이력이 없다.
- FastAPI의 SSE broadcaster는 프로세스 메모리 기반이라 다중 프로세스 전달을 보장하지
  않는다.

## 로컬 MongoDB 진단

2026-08-15에 실행 중인 FastAPI API를 읽기 전용으로 확인한 결과다.

- 실제 video stream 2개는 `NO_VIDEO` 상태였다.
- 두 stream의 `classroom_id`는 현재 classroom 컬렉션의 실제 ID와 일치하지 않았다.
- 선택한 강의실의 좌석은 5개였고 `seat.geometry`가 있는 좌석은 0개였다.
- 해당 강의실에는 ROI 연결 1개와 좌석 지정 3개가 있었으나, 같은 좌석의 ROI 학생과
  `seat_assignments` 학생이 일치하지 않았다.
- 현재 ROI 모델에는 `camera_id`가 없어 같은 강의실의 서로 다른 카메라 화각을 구분할 수
  없다.
- 확인된 live ROI는 `reference_image_revision=0`이고 서버 재시작 뒤 `needs_review=true`로
  조회됐다. 검토 필요 ROI를 제외하는 규칙만 추가하면 현재 ROI는 전부 판정에서 빠진다.
- 두 카메라의 최근 detection event는 0건이었다.
- 학생 상태 API는 0건을 반환했다.

따라서 현재 상태에서는 모델 이벤트가 도착해도 카메라→강의실 검증 또는 ROI→좌석
매핑 단계에서 학생 상태 판정이 끊긴다.

## 구현 시 코드가 사실인 항목

- 내부 이벤트 수신: `webapps/fastapi/app/student_monitoring/router.py`
- 탐지 저장·SSE·좌석 매핑: `webapps/fastapi/app/student_monitoring/service.py`
- 현재 직사각형 매핑: `webapps/fastapi/app/classrooms/mapping.py`
- ROI 다각형: `webapps/fastapi/app/roi_connections/`
- 좌석 지정: `webapps/fastapi/app/classrooms/`
- 실시간 영상 SSE 소비: `webapps/fastapi/static/monitoring.js`
- 강의실 현황 SSE 소비: `webapps/fastapi/static/classrooms.js`
- worker 전송 payload: `worker/inference/handler.py`
