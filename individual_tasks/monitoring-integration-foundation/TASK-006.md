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
