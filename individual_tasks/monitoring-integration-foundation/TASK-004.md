# TASK-004 최근 탐지 기반 학생 상태 조회

**목적**: 기존 빈 학생 상태 API를 최근 탐지·ROI·좌석 지정 기반의 읽기 모델로 채운다.
**대상 독자**: `student_monitoring` 서비스·저장소·API 구현자.

## 선행 의존성

[TASK-003](TASK-003.md).

## 예상 소유 파일

- `webapps/fastapi/app/student_monitoring/`
- `webapps/fastapi/app/shared/config.py`
- `webapps/fastapi/app/shared/dependencies.py`
- 대응 테스트

## 구현 범위

- 최근 강의실 탐지 이벤트를 읽을 저장소 계약을 추가하거나 기존 계약을 확장한다.
- `list_student_states()`가 지정 학생 전체를 반환하도록 구현한다.
- 학생별 최근 유효 탐지를 고르고 `PRESENT`, `WRONG_SEAT`, `UNKNOWN`을 계산한다.
- 탐지가 없거나 stale이면 `UNKNOWN`으로 반환하고 `ABSENT`로 바꾸지 않는다.
- 신뢰도 임계값과 stale 기준은 설정으로 주입한다. 현재 하드코딩된 identity 임계값을
  설정으로 옮긴다.
- 학생 이름·학번은 활성 학생 조회 결과에서만 채운다.
- GET 조회는 상태 저장·전이·SSE 발행을 수행하지 않는다.
- 안정적인 좌석·학생 순서를 보장한다.

## 판정 우선순위

1. 유효한 assignment와 활성 학생이 없으면 목록에서 제외한다.
2. 최근 유효 식별이 없으면 `UNKNOWN`이다.
3. 유효 식별은 있으나 ROI 좌석을 결정할 수 없으면 `UNKNOWN`이다.
4. 현재 ROI 좌석과 지정 좌석이 같으면 `PRESENT`다.
5. 둘 다 존재하고 다르면 `WRONG_SEAT`다.

## 검증

- 지정 학생 전체와 미관측 `UNKNOWN` 포함.
- 지정석 일치·불일치·ROI 없음·stale·신뢰도 미달.
- 동일 학생 다중 탐지의 결정적 선택.
- 비활성 학생·좌석과 깨진 참조 처리.
- 기존 `StudentStateListResponse` 계약과 404 envelope 유지.

## 구현 결과

- [x] detection event 저장소에 강의실·stale 시각 범위·조회 상한을 받는 최신순 계약을
  추가하고 memory/MongoDB adapter를 동일하게 구현했다.
- [x] `list_student_states()`가 활성 좌석에 지정된 활성 학생 전체를 안정적인 좌석 코드
  순서로 반환하며, 미관측 학생도 `UNKNOWN`으로 포함한다.
- [x] 사람 탐지와 학생 식별 신뢰도 임계값을 모두 적용하고, 같은 이벤트에서는 식별
  신뢰도 → 탐지 신뢰도 → detection ID 순으로 결정적으로 선택한다.
- [x] 최근 유효 식별의 bbox를 해당 카메라의 검토 완료 ROI에만 매핑하고, assignment와
  일치하면 `PRESENT`, 다르면 `WRONG_SEAT`, 미매핑·겹침이면 `UNKNOWN`으로 둔다.
- [x] `roi_connections.student_id`와 `seat.geometry`는 학생 상태 판정에 사용하지 않는다.
- [x] identity 임계값, stale 기준, 최근 이벤트 조회 상한을 공통 설정으로 주입한다.
- [x] GET 조회는 상태 저장이나 SSE 발행을 하지 않고 기존 응답·404 envelope를 유지한다.

검증 결과: FastAPI 전체 `pytest -q` 739건, Ruff check·format check와 mypy를 통과했다.
