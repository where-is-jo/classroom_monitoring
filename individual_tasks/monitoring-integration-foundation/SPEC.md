# 실시간 학생 상태 연동 기반

**목적**: 모델 작업자가 `student_id · 신뢰도 · bbox`를 공급하면 FastAPI가 카메라,
강의실, 좌석 ROI, 좌석 지정을 결합해 안전하게 학생 상태를 계산하고 실시간 영상 화면과
강의실 대시보드에 전달할 수 있는 기반을 완성한다.
**대상 독자**: FastAPI·worker·deeplearning 연동 구현자와 검토자.

## 범위

### 포함

- video stream의 강의실 참조 무결성 검증과 관리 가능한 연결 경로
- 좌석 위치 판정의 단일 정본 확정 및 ROI 다각형 매핑
- 기존 내부 추론 이벤트 계약의 선택 신원 필드 사용
- 최근 탐지 기반 `PRESENT`, `WRONG_SEAT`, `UNKNOWN` 상태 조회
- 학생 상태 SSE와 대시보드 실시간 갱신
- 실시간 영상 bbox에 FastAPI가 안전하게 보강한 식별 표시
- 얼굴 데이터 없는 합성 E2E 테스트와 모델 작업자용 handoff fixture

### 제외

- 실제 얼굴 인식 모델, embedding gallery 전달, 얼굴 원본 처리
- 사람 tracking과 `IN_CLASSROOM`
- 수업 시간표·유예 시간·카메라 건강도를 결합한 최종 `ABSENT`
- Redis 등 외부 pub/sub와 다중 FastAPI worker 지원
- 기존 공개 API 필드 삭제·이름 변경
- 승인 없는 운영·공용 MongoDB 데이터 수정

## 요구사항

| 번호 | 요구사항 | 필수 여부 |
| --- | --- | --- |
| R1 | 활성 video stream의 `classroom_id`는 존재하는 활성 강의실을 참조해야 한다. | 필수 |
| R2 | 카메라가 유효한 강의실과 연결되지 않으면 이벤트는 저장하되 좌석·학생 상태 판정을 건너뛰고 원인을 관측 가능하게 남긴다. | 필수 |
| R3 | 좌석 위치 판정은 이벤트 카메라의 좌표계에 속한 단일 ROI 정본만 사용하며 유효 ROI가 없으면 `UNKNOWN`으로 둔다. | 필수 |
| R4 | 학생 배정의 정본은 `seat_assignments`이며 ROI의 legacy `student_id`를 상태 판정에 사용하지 않는다. | 필수 |
| R5 | 모델·worker는 `PRESENT`, `WRONG_SEAT`, `ABSENT`를 만들지 않고 식별 결과까지만 전달한다. | 필수 |
| R6 | 신뢰도 미달·미식별·배정 없음·ROI 없음·오래된 관측은 이름을 추정하지 않고 `UNKNOWN`으로 처리한다. | 필수 |
| R7 | 학생 상태 REST는 지정 학생 전체를 반환하며 최근 유효 탐지가 없는 학생도 `UNKNOWN`으로 포함한다. | 필수 |
| R8 | 신규 추론 이벤트 반영 후 학생 상태 SSE를 발행하고, 브라우저는 초기 REST 상태 뒤 SSE 변경분을 적용한다. | 필수 |
| R9 | 실시간 영상 detection SSE는 bbox를 유지하고, 식별 표시는 FastAPI가 활성 학생 조회 결과로만 보강한다. | 필수 |
| R10 | 좌석 `VACANT`를 학생 `ABSENT`로 표현하지 않는다. | 필수 |
| R11 | 같은 `event_id` 재수신은 탐지 저장, 상태 계산, SSE 발행을 중복 수행하지 않는다. | 필수 |
| R12 | 실제 얼굴·embedding 없이 전체 연결을 자동 검증한다. | 필수 |

## 아키텍처 결정

다음 선택은 [결정 0019](../../docs/architecture/decisions.md#0019--실시간-학생-상태-연동은-카메라별-roi와-fastapi-판정을-사용한다)와
[TASK-001](TASK-001.md)의 Architecture Review에서 확정했다.

1. **좌석 위치 정본**: `roi_connections.polygon`을 카메라 프레임 내 좌석 영역의 정본으로
   사용한다. `seat.geometry`는 이 판정 경로에서 사용하지 않는다.
2. **ROI 카메라 범위**: `roi_connections`를 `camera_id + seat_id` 범위로 확장하고 기준
   이미지도 `classroom_id + camera_id`로 관리한다. 여러 카메라를 허용하면서 현재의
   `classroom_id + seat_id` polygon 하나를 공유하지 않는다.
3. **ROI revision 수명**: live ROI의 revision 0은 재시작 뒤에도 유효하다. 기준 이미지
   ROI는 같은 카메라의 현재 in-memory revision과 일치할 때만 유효하며, 재시작으로 이미지를
   잃었거나 revision이 다르면 `needs_review`로 판정에서 제외한다.
4. **학생 배정 정본**: `seat_assignments`만 사용한다. 기존 ROI 응답의 `student_id`는 이번
   작업에서 삭제하지 않지만 상태 판정 입력에서는 제외한다.
5. **좌석 판정점**: 첫 구현은 기존 동작과 회귀 위험을 줄이기 위해 사람 bbox 중심점을
   정규화한 뒤 polygon 포함 여부로 판정한다. 다른 방식은 실제 촬영 근거와 새 결정 후
   추가한다.
6. **학생 상태 소유권**: 상태 판정은 `webapps/fastapi/app/student_monitoring`이 소유한다.
7. **실시간 전달**: 기존 FastAPI 인메모리 broadcaster를 재사용하되 단일 프로세스 범위임을
   명시한다. 외부 broker는 후속 결정으로 남긴다.
8. **기존 ROI 호환**: `camera_id`가 없는 legacy 문서는 조회 가능하게 유지하지만 상태
   판정에는 사용하지 않는다. 카메라를 추측해 자동 이관하지 않고 관리 화면에서 다시 저장한다.

## API 계약

### worker → FastAPI 내부 이벤트

기존 `POST /internal/inference/events`를 유지한다. 새 endpoint를 만들지 않는다.

```json
{
  "event_id": "camera-01-...",
  "camera_id": "camera-01",
  "captured_at": "2026-08-15T12:00:00+00:00",
  "sequence": 42,
  "frame": {
    "width_pixels": 1920,
    "height_pixels": 1080
  },
  "detections": [
    {
      "detection_id": "camera-01-...-det-0",
      "class_id": 0,
      "class_name": "person",
      "confidence": 0.91,
      "bbox": [100, 120, 300, 700],
      "student_id": "synthetic-student-001",
      "identity_confidence": 0.88,
      "face_bbox": [150, 130, 220, 230]
    }
  ]
}
```

- 신원 필드는 선택이다. 미식별이면 `student_id`, `identity_confidence`, `face_bbox`는
  `null` 또는 생략할 수 있다.
- 모델은 이름·학번·학생 상태를 보내지 않는다.
- 응답은 기존 신규 `201`, 중복 `200` 계약을 유지한다.
- 등록되지 않은 `camera_id`는 기존 오류 계약을 유지한다.

### 학생 상태 REST

기존 `GET /api/v1/classrooms/{classroom_id}/student-states`를 채운다.

- 성공: `200 StudentStateListResponse`
- 강의실 없음: 기존 `404` envelope
- 상태: 이번 범위에서 `PRESENT`, `WRONG_SEAT`, `UNKNOWN`
- 지정 학생 전체를 안정적인 순서로 반환한다.
- 조회 요청은 상태를 변경하거나 SSE를 발행하지 않는다.

### 학생 상태 SSE

신규 후보 경로:
`GET /api/v1/classrooms/{classroom_id}/student-state-events`

```json
{
  "type": "student-state",
  "event_id": "camera-01-...",
  "classroom_id": "classroom-uuid",
  "student_id": "synthetic-student-001",
  "student_name": "합성 학생",
  "student_no": "SYNTHETIC-001",
  "assigned_seat_id": "seat-uuid",
  "assigned_seat_label": "좌석 S01",
  "current_seat_id": "seat-uuid",
  "current_state": "PRESENT",
  "confidence": 0.88,
  "observed_at": "2026-08-15T12:00:00+00:00"
}
```

- 첫 연결은 REST로 현재 상태를 렌더링하고 SSE는 이후 변경분만 전달한다.
- heartbeat와 EventSource 자동 재연결을 유지한다.
- 이름·학번은 FastAPI의 활성 학생 조회 결과만 사용한다.
- embedding, 얼굴 이미지, 원본 모델 출력은 포함하지 않는다.

## 상태 판정 규칙

1. 카메라가 유효한 강의실과 연결되어 있는지 확인하고 해석된 `stream_id`, `classroom_id`를
   저장할 이벤트에 채운다.
2. 이벤트 카메라에 속한 활성 좌석 ROI, 좌석 지정, 활성 학생을 읽는다.
3. 탐지 신뢰도와 식별 신뢰도가 설정 임계값 이상인지 확인한다.
4. bbox 중심점을 정규화해 검토 완료 ROI polygon 하나에 매핑한다.
5. 식별 학생의 지정 좌석과 현재 좌석이 같으면 `PRESENT`다.
6. 둘 다 존재하지만 다르면 `WRONG_SEAT`다.
7. 신원·배정·ROI·관측 중 하나라도 부족하면 `UNKNOWN`이다.
8. 탐지되지 않았다는 사실만으로 `ABSENT`를 만들지 않는다.

여러 ROI가 겹치면 조용히 하나를 선택하지 않고 `UNKNOWN`으로 두고 진단 가능한 로그를
남긴다. 같은 학생이 한 이벤트에 여러 번 나오면 `identity_confidence`, 그다음 사람 탐지
`confidence`, 마지막으로 `detection_id` 순으로 결정적으로 하나를 고른다.

## 예외·실패 정책

- 탐지 이벤트 저장은 좌석·학생 상태 매핑보다 먼저 완료한다.
- 카메라 강의실 참조, ROI 또는 좌석 지정 문제가 있어도 저장된 원시 탐지 이벤트를
  되돌리지 않는다.
- 상태 판정 실패는 해당 이벤트의 상태 반영만 건너뛰며 다음 이벤트 처리를 막지 않는다.
- API에 내부 예외, MongoDB query, 얼굴 정보, embedding을 노출하지 않는다.
- worker는 전송 실패 시 기존 제한 재시도를 유지하며 무한 재시도하지 않는다.
- 브라우저는 SSE 재연결 외에 상태 변경 요청을 자동 재시도하지 않는다.

## Data Flow

`camera → worker/stream → worker/inference → deeplearning 결과(student_id·confidence·bbox)
→ worker HTTP handler → FastAPI 탐지 저장 → camera/classroom 검증 → ROI polygon 좌석 매핑
→ seat_assignments 대조 → 학생 상태 계산 → REST 초기 상태 + SSE 변경분 → 두 화면`

## 완료 조건

- [ ] `TASK-001` 결정이 승인되고 구현 파일 배치와 호출 방향이 확정됐다.
- [ ] 유효하지 않은 camera→classroom 연결을 저장·기동 전에 발견할 수 있다.
- [ ] 강의실당 카메라 수와 ROI의 camera scope·revision 정책이 ADR로 확정됐다.
- [ ] 저장된 detection event에 서버가 해석한 실제 `stream_id`, `classroom_id`가 있다.
- [ ] ROI와 좌석 지정이 서로 다른 학생을 가리켜도 상태 판정은 `seat_assignments`만 따른다.
- [ ] ROI 없는 좌석과 겹치는 ROI는 `UNKNOWN`이며 `VACANT/ABSENT`로 오판하지 않는다.
- [ ] 합성 `student_id` 이벤트가 MongoDB에 저장되고 REST에서 `PRESENT` 또는
  `WRONG_SEAT`로 조회된다.
- [ ] 실시간 영상 화면에 bbox와 안전한 식별 라벨이 SSE로 갱신된다.
- [ ] 강의실 대시보드의 학생 이름·좌석·상태·마지막 관측이 SSE로 갱신된다.
- [ ] 같은 이벤트 재전송에서 상태 SSE가 중복 발행되지 않는다.
- [ ] memory와 MongoDB adapter 계약 테스트가 모두 통과한다.
- [ ] 실제 얼굴·영상·embedding을 테스트 자산이나 로그에 남기지 않는다.

## 작업 의존성

`TASK-001 → TASK-002 → TASK-003 → TASK-004 → TASK-005 → TASK-006`
