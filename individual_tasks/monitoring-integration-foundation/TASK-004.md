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
