# 무엇을 왜 했나

`feature/monitoring-integration-foundation`은 PR1의 좌석·학생 관리 결과 위에 카메라,
강의실, 카메라별 좌석 ROI, 좌석 지정, 학생 식별 이벤트를 연결했다. 실제 얼굴 인식
모델이 아직 없어도 FastAPI가 식별 결과를 안전하게 저장하고 `PRESENT`,
`WRONG_SEAT`, `UNKNOWN`을 판정해 REST와 SSE로 전달할 수 있게 하는 것이 목적이다.

작업 범위는 PR1 tip `d04b463` 다음의 `09037ea`부터 `e8490e7`까지다. 계획·결정,
카메라 참조 무결성, ROI 매핑, 상태 REST, 상태 SSE·화면, 합성 E2E 순으로 다음 커밋을
분리했다.

- `09037ea`: 구현 기준선, SPEC, TASK-001~006 작성
- `24a91b5`: ADR 0019와 카메라별 ROI·FastAPI 판정 경계 확정
- `c0845fe`: camera→classroom 참조 무결성과 저장 이벤트 보강
- `7e52e47`: 카메라별 ROI 저장·조회와 bbox 중심점 polygon 매핑
- `c956131`: 최근 MongoDB 탐지 기반 지정 학생 전체 상태 REST
- `4182fda`: 학생 상태 SSE, 안전한 detection 라벨, 두 화면 실시간 갱신
- `e8490e7`: worker 선택 신원 필드, 승인 fixture, 합성 E2E와 모델 인계 계약

# 무엇이 바뀌었나

활성 video stream은 존재하는 활성 강의실만 참조한다. 이벤트의 `stream_id`와
`classroom_id`는 worker 입력을 신뢰하지 않고 FastAPI가 등록된 stream에서 해석해 저장한다.
깨진 강의실 참조는 원시 이벤트를 보존하되 좌석·학생 상태 판정을 건너뛴다.

좌석 위치 판정의 정본은 `camera_id + seat_id` 범위의 `roi_connections.polygon`이다.
학생 배정 정본은 `seat_assignments`이며 ROI의 legacy `student_id`와 `seat.geometry`는 학생
상태 판정에 사용하지 않는다. 유효 ROI가 없거나 둘 이상 겹치면 임의 좌석을 고르지 않고
`UNKNOWN`으로 둔다.

`GET /api/v1/classrooms/{classroom_id}/student-states`는 활성 좌석에 지정된 활성 학생
전체를 안정적인 좌석 순서로 반환한다. 임계값 이상의 최신 사람 탐지와 학생 식별만 사용해
지정 좌석과 같으면 `PRESENT`, 다른 ROI면 `WRONG_SEAT`, 근거가 부족하거나 stale이면
`UNKNOWN`을 반환한다. 탐지가 없다는 이유만으로 `ABSENT`를 만들지 않는다.

`GET /api/v1/classrooms/{classroom_id}/student-state-events`와 기존 detection·occupancy
SSE를 브라우저가 함께 구독한다. 초기 REST가 완료되기 전에 도착한 상태 이벤트는 버퍼링한
뒤 적용한다. 실시간 bbox에는 FastAPI가 확인한 활성 학생 이름 또는 `사람`만 표시하며 얼굴
좌표·식별 신뢰도·embedding은 SSE에 노출하지 않는다. broadcaster는 현재 단일 FastAPI
프로세스 범위다.

모델 작업자는 [`worker/inference/MODEL_INTEGRATION.md`](../worker/inference/MODEL_INTEGRATION.md)와
`worker/inference/fixtures/identified_student_event.json`을 기준으로 작업한다. worker가
보내는 선택 필드는 `student_id`, `identity_confidence`, `face_bbox`까지다. 이름, 학번,
좌석 ID와 `PRESENT`·`WRONG_SEAT`·`UNKNOWN`·`ABSENT`는 모델 출력에 넣지 않는다.
`INFERENCE_EVENT_FIXTURE`는 후보 JSON을 FastAPI의 실제 요청 스키마로 검사할 때만 쓰는
테스트용 환경변수다. 새 runtime 환경변수나 저장소 의존성은 추가하지 않았다.

# 검증

## 실행한 것

- `cd webapps/fastapi && python -m pytest -q`: 763 passed.
- `python -m ruff check app tests`: 통과.
- `python -m ruff format --check app tests`: 190 files already formatted.
- `python -m mypy app tests`: 189 source files, 오류 없음.
- `cd worker && python -m pytest inference/tests pipeline/tests -q`: 57 passed.
- `node --check`로 `classrooms.js`, `monitoring.js`, `roi-connections.js`: 통과.
- 승인 fixture를 `INFERENCE_EVENT_FIXTURE`로 주입한 FastAPI 후보 계약 검사: 1 passed.
- 합성 E2E에서 새 이벤트 201, 중복 200, 원시 저장 1건, 세 SSE payload,
  `PRESENT`·`WRONG_SEAT`·다섯 UNKNOWN 경로, Mongo adapter 문서 기반 재시작 복원 확인.

FastAPI pytest에는 `httpx`/TestClient deprecation 경고와 sandbox의 `.pytest_cache` 쓰기
권한 경고가 있었지만 테스트 실패는 없었다.

## 실행하지 못한 것

- 실제 얼굴 인식 가중치와 GPU 추론: 모델이 이번 작업 범위에 없어서 실행하지 않음.
- 실제 RTSP 카메라와 WebRTC 재생: 합성 이벤트 계약 작업이라 장비 경로를 실행하지 않음.
- 실제 MongoDB 프로세스 재시작: Mongo repository adapter를 같은 영속 문서 대역에 다시
  조립해 복원 계약을 검증했으며 외부 DB 프로세스는 실행하지 않음.
- 다중 FastAPI 프로세스 SSE fan-out·replay: 현재 in-memory broadcaster가 지원하지 않음.

# 이어받을 때 알아야 할 것

이 브랜치는 `develop`이 아니라 PR1의 `feature/seat-student-registration` tip `d04b463`에서
분기한 stacked branch다. 현재 `develop` merge-base는 `ef333f6`이므로 PR1 병합 전에는 PR
base를 PR1 브랜치로 둔다. PR1 병합 뒤 최신 `develop` 위로 재정렬하고
`git diff --stat develop...HEAD`와 전체 검증을 다시 실행해야 한다.

다음 작업의 가장 작은 연결점은 얼굴 식별 모델이 worker `Detection`의
`student_id`·`identity_confidence`·선택 `face_bbox`를 채우게 하는 것이다. 같은 프레임을
재전송할 때 `event_id`를 바꾸면 안 된다. 미식별 또는 식별 기준 미달이면 신원 필드를 모두
비우고 가장 가까운 학생을 고르지 않는다. 후보 JSON은 모델 인계 문서의 PowerShell 명령으로
검사한다.

`ABSENT`, tracking 기반 `IN_CLASSROOM`, 수업 시간표·유예 시간, 실제 카메라 배치에 따른
ROI 판정점 조정은 이 작업에 포함되지 않았다. 이 기능을 추가할 때도 업무 상태는 FastAPI가
소유하고 모델·worker에는 넣지 않는다.

기존 미추적 `.docker` 파일은 이 브랜치에서 수정·stage·커밋하지 않았다.
