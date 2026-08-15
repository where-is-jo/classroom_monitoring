# TASK-002 카메라와 강의실 참조 무결성

**목적**: 탐지 이벤트의 `camera_id`가 실제 활성 강의실 하나로 안정적으로 해석되게 한다.
**대상 독자**: `video_monitoring`과 `student_monitoring` 구현자.

## 선행 의존성

[TASK-001](TASK-001.md) 승인.

## 예상 소유 파일

- `webapps/fastapi/app/video_monitoring/`
- `webapps/fastapi/app/student_monitoring/service.py`
- `webapps/fastapi/app/shared/dependencies.py`
- 대응 테스트

## 구현 범위

- video stream 생성·수정·seed 시 `classroom_id`가 존재하는 활성 강의실인지 검증한다.
- 현재 repository에 update 경로가 없다면 기존 계층 방향을 지키는 최소 계약을 추가한다.
- 이벤트 수신 시 stream의 저장된 `classroom_id`를 사용하고 요청 payload가 강의실을
  덮어쓰지 못하게 한다.
- router가 만든 빈 `stream_id`, `classroom_id`를 그대로 저장하지 않는다. service가 찾은
  stream의 실제 ID와 강의실 ID로 불변 이벤트를 보강한 뒤 저장한다.
- 연결이 없거나 참조가 깨졌으면 원시 이벤트 저장 후 상태 판정을 건너뛴다.
- 오류 원인은 식별자와 event_id까지만 구조화 로그로 남기고 학생 정보는 남기지 않는다.
- memory/MongoDB에서 같은 참조 규칙을 검증한다.

## 데이터 정리 정책

- 구현 테스트는 합성 ID만 사용한다.
- 현재 로컬 MongoDB repair는 제품 코드 커밋과 분리한다.
- repair 전 정확한 stream 수, 이전 classroom_id, 대상 classroom_id, 참조 존재 여부를
  dry-run으로 확인한다.
- 자동 추측으로 강의실을 연결하지 않는다. 동일한 코드·이름이 있어도 운영자 선택이
  필요하다.

## 검증

- 유효한 camera→classroom 연결 이벤트는 다음 매핑 단계로 진행한다.
- 저장된 detection event의 `stream_id`, `classroom_id`가 catalog 값과 일치한다.
- 존재하지 않거나 비활성 강의실 연결은 원시 이벤트만 저장한다.
- 같은 event_id 재수신은 추가 상태 처리를 하지 않는다.
- 기존 video stream 목록·재생 API에 회귀가 없다.

## 구현 결과

- [x] stream 저장 경로가 활성 실제 stream의 강의실 참조를 검증한다.
- [x] memory demo seed의 stream이 실제 seed 강의실 UUID를 참조한다.
- [x] MongoDB 시작 시 기존 활성 stream의 깨진 참조를 자동 수정 없이 식별자 로그로 남긴다.
- [x] 추론 이벤트를 stream catalog의 `stream_id`, `classroom_id`로 보강해 저장한다.
- [x] 깨진 강의실 참조에서도 원시 탐지를 저장하고 좌석 파생 처리를 건너뛴다.
- [x] 중복 이벤트는 파생 처리와 SSE를 다시 실행하지 않는다.

검증 결과: FastAPI 전체 `pytest -q` 720건, Ruff check·format check, mypy를 통과했다.
