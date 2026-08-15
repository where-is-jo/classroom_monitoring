# TASK-006 합성 E2E 검증과 모델 작업자 인계

**목적**: 실제 모델·얼굴·카메라 없이 전체 계약을 재현하고 다음 작업자가 같은 fixture로
자신의 출력을 검증하게 한다.
**대상 독자**: 통합 테스트 담당자와 모델·worker 작업자.

## 선행 의존성

[TASK-005](TASK-005.md).

## 예상 소유 파일

- `webapps/fastapi/tests/`의 통합 테스트
- `worker/inference/tests/` 또는 `worker/pipeline/tests/`의 payload 계약 테스트
- 구현과 불일치가 확인된 기존 기준 문서
- 최종 작업 결과 문서

## 합성 시나리오

1. 합성 강의실, 카메라 stream, 좌석 2개, ROI 2개, 학생 2명, 좌석 지정을 만든다.
2. 지정 좌석 ROI 안에 합성 학생 A의 탐지를 POST하고 `PRESENT`를 확인한다.
3. 다른 좌석 ROI 안에 같은 학생 A의 탐지를 POST하고 `WRONG_SEAT`를 확인한다.
4. 미식별·신뢰도 미달·ROI 밖·겹치는 ROI 이벤트가 `UNKNOWN`인지 확인한다.
5. 원시 detection 저장, REST 초기 상태, detection SSE, occupancy SSE, student-state SSE를
   각각 확인한다.
6. 같은 event_id를 다시 POST해 저장과 SSE가 중복되지 않는지 확인한다.
7. server restart를 모사해 REST가 MongoDB의 최근 이벤트로 초기 상태를 복원하는지 확인한다.

## 모델 작업자 handoff 자료

- 승인된 내부 이벤트 JSON 예제
- 필수·선택 필드와 좌표 단위
- 미식별 표현 방법
- 신뢰도 범위와 timestamp/timezone 규칙
- event_id 안정성·멱등성 규칙
- FastAPI가 반환하는 201/200/404/422/503 처리 기대
- 로컬 합성 fixture 실행 방법
- 모델이 만들면 안 되는 업무 상태 목록

## 검증 명령

FastAPI 변경이 있으면 프로젝트의 전체 pytest, Ruff check/format, mypy를 실행한다.
worker 변경이 있으면 해당 패키지의 전체 테스트와 설정 검증을 실행한다. 실제로 실행하지
못한 GPU·RTSP·WebRTC 검증은 결과 문서에 별도로 적는다.

## 완료 기준

- 실제 얼굴·영상 없이 전체 상태 흐름이 반복 가능하다.
- 모델 작업자가 자신의 payload를 fixture에 넣어 계약 적합성을 판정할 수 있다.
- 코드·SPEC·ADR·README의 계약이 일치한다.
- PR1 의존성과 최종 `develop` 기준 diff가 정리돼 있다.

## 구현 결과

- [x] `worker/inference/fixtures/identified_student_event.json`을 worker 직렬화와 FastAPI
  통합 검증이 함께 사용하는 승인 fixture로 추가했다.
- [x] worker `Detection`이 선택 `student_id`·`identity_confidence`·`face_bbox`를 표현하고,
  완전한 식별 조합만 HTTP payload에 싣도록 했다. 업무 상태는 추가하지 않았다.
- [x] FastAPI 요청 계약이 신뢰도 범위, 신원 필드 조합, frame 내부 bbox와 알 수 없는
  추가 필드를 422로 거부하도록 고정했다.
- [x] 합성 강의실·카메라·좌석 2개·ROI 2개·학생 2명·좌석 지정으로 `PRESENT`,
  `WRONG_SEAT`, 미식별·신뢰도 미달·ROI 밖·겹침의 `UNKNOWN`을 검증했다.
- [x] 원시 이벤트 저장, REST 초기 상태, detection·occupancy·student-state SSE payload와
  같은 event ID 재전송의 201→200 멱등성을 한 통합 테스트에서 확인했다.
- [x] 같은 영속 문서를 읽는 새 `MongoDetectionEventRepository`와 서비스 인스턴스를
  조립해 재시작 뒤 REST가 최근 `PRESENT`를 복원하는지 확인했다.
- [x] [`worker/inference/MODEL_INTEGRATION.md`](../../worker/inference/MODEL_INTEGRATION.md)에
  필드·좌표·시간·오류 처리·금지 상태·후보 fixture 검증 명령을 정리했다.

## 검증 결과

- FastAPI 전체 `pytest -q`: 763 passed.
- FastAPI `ruff check app tests`: 통과.
- FastAPI `ruff format --check app tests`: 190 files already formatted.
- FastAPI `mypy app tests`: 189 source files, 오류 없음.
- worker `pytest inference/tests pipeline/tests -q`: 57 passed.
- `classrooms.js`, `monitoring.js`, `roi-connections.js` Node 구문 검사: 통과.
- `INFERENCE_EVENT_FIXTURE`로 승인 JSON을 주입한 후보 계약 검사: 1 passed.
- 실제 GPU 모델, RTSP 카메라, WebRTC 재생은 합성 계약 작업에서 실행하지 않았다.

## PR1 의존성과 병합 순서

이 브랜치의 merge-base는 현재 `develop`의 `ef333f6`이며, PR1 브랜치
`feature/seat-student-registration`의 `d04b463`에서 직접 분기했다. 따라서
`develop...HEAD` diff에는 PR1의 학생·좌석 UI 커밋과 이 작업이 함께 보인다.

1. PR1 미병합 상태에서는 이 브랜치 PR base를 `feature/seat-student-registration`으로 둔다.
2. PR1이 변경되면 최신 PR1 tip 위로 이 브랜치를 재정렬한다.
3. PR1 병합 뒤 최신 `develop` 위로 재정렬하고 PR base를 `develop`으로 바꾼다.
4. 재정렬 뒤 `git diff --stat develop...HEAD`와 위 전체 검증을 다시 실행한다.

기존 미추적 `.docker` 파일은 이 작업의 diff나 커밋에 포함하지 않았다.
