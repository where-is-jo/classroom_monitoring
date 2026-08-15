# TASK-003 ROI polygon 좌석 매핑

**목적**: 카메라 프레임의 사람 bbox를 강의실 좌석 ROI 하나로 결정적으로 매핑한다.
**대상 독자**: `roi_connections`, `classrooms`, `student_monitoring` 구현자.

## 선행 의존성

[TASK-002](TASK-002.md).

## 예상 소유 파일

- `webapps/fastapi/app/roi_connections/`
- `webapps/fastapi/app/student_monitoring/`
- 필요한 조립부와 대응 테스트

## 구현 범위

- TASK-001에서 승인된 카메라 범위에 맞는 ROI polygon만 상태 판정 입력으로 제공한다.
- 카메라별 ROI가 승인되면 모델·스키마·repository unique key·API·기존 데이터 호환 방식을
  함께 변경한다. 단일 카메라 불변식이 승인되면 stream 생성·수정에서 이를 강제한다.
- live revision 0과 기준 이미지 revision의 검토 가능 여부를 승인된 정책대로 판정한다.
- bbox 중심점을 프레임 크기로 0~1 정규화하고 polygon 포함 여부를 계산한다.
- 비활성·삭제 좌석, 다른 강의실·카메라 ROI, 승인 정책상 검토가 필요한 ROI는 제외한다.
- ROI가 없거나 2개 이상 겹치면 좌석을 선택하지 않고 `UNKNOWN` 근거로 돌린다.
- ROI의 `student_id`는 상태 판정에 사용하지 않는다.
- `seat_assignments`와 ROI 학생이 달라도 배정 정본은 `seat_assignments`다.
- 기존 직사각형 `seat.geometry` fallback을 추가하지 않는다.

## 검증

- 삼각형·사각형·경계점 polygon 포함 판정.
- 프레임 크기와 bbox 유효성 경계.
- ROI 없음, 검토 필요, 겹침, 다른 강의실, 비활성 좌석.
- 다른 카메라 화각, live revision 0, 서버 재시작 후 검토 상태.
- ROI legacy 학생과 assignment 학생 불일치에서도 assignment가 정본임을 확인.
- memory와 MongoDB repository에서 같은 결과.

## 구현 결과

- [x] ROI와 기준 이미지를 각각 `camera_id + seat_id`, `classroom_id + camera_id`로 분리했다.
- [x] ROI API·관리 화면이 명시적으로 카메라를 선택하고 `camera_id`를 저장한다.
- [x] memory/MongoDB 키와 unique index를 카메라 범위로 전환했다.
- [x] `camera_id` 없는 legacy 문서는 조회되지만 카메라별 유효 ROI에서 제외된다.
- [x] live revision 0은 재시작 뒤에도 유효하고 기준 이미지 ROI는 revision 불일치·재시작 시
  재검토 대상으로 제외된다.
- [x] bbox 중심점의 polygon 포함, 경계, ROI 없음, 겹침, 잘못된 프레임·bbox를 순수 함수로
  검증했다.
- [x] 학생 상태 판정에서 사용할 `list_valid_connections()`는 활성 좌석과 선택 카메라의
  검토 완료 ROI만 제공하며 `seat.geometry` fallback을 사용하지 않는다.

검증 결과: FastAPI 전체 `pytest -q` 739건, Ruff check·format check, mypy와
`node --check static/roi-connections.js`를 통과했다.
