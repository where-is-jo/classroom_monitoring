# 모델 결과 연동 계약

얼굴 식별 모델 작업자는 사람 탐지 결과에 선택 신원 필드를 채워 worker의
`InferenceResult`로 넘긴다. worker는 이를 기존
`POST /internal/inference/events` 요청으로 직렬화한다. 모델과 worker는 학생 이름, 학번,
좌석, 출결 상태를 판단하거나 보내지 않는다.

## 승인 fixture

기준 요청은 [`fixtures/identified_student_event.json`](./fixtures/identified_student_event.json)이다.
실제 얼굴·영상·embedding 없이 worker 직렬화와 FastAPI 상태 흐름 양쪽에서 같은 파일을
사용한다.

필수 최상위 필드는 `event_id`, `camera_id`, timezone이 있는 `captured_at`, 0 이상의
`sequence`, `frame`, `detections`다. `frame.width_pixels`와 `height_pixels`는 원본 추론
프레임의 양의 정수 크기다. 한 이벤트에는 탐지를 최대 100개까지 보낸다.

각 탐지의 필드는 다음과 같다.

| 필드 | 필수 | 규칙 |
| --- | --- | --- |
| `detection_id` | 예 | 이벤트 안에서 안정적인 ID. worker 기본값은 `<event_id>-det-<index>` |
| `class_id`, `class_name` | 예 | 사람은 현재 `0`, `person`. 학생 상태 판정은 `person`만 사용 |
| `confidence` | 예 | 사람 탐지 신뢰도, `0.0 <= value <= 1.0` |
| `bbox` | 예 | `[x_min, y_min, x_max, y_max]` 정수 픽셀. 원본 frame 안에서 좌상단보다 우하단이 커야 함 |
| `student_id` | 아니요 | FastAPI 학생 원장의 내부 ID. 이름이나 학번을 대신 보내지 않음 |
| `identity_confidence` | 아니요 | 얼굴 식별 신뢰도, `0.0 <= value <= 1.0` |
| `face_bbox` | 아니요 | 얼굴 영역의 같은 픽셀 좌표계 bbox. 이미지나 embedding은 보내지 않음 |
| `track_id` | 아니요 | 같은 카메라 안에서 같은 사람을 이어 보는 식별자([결정 0025](../../docs/architecture/decisions.md#0025--강의실-안-신원-유지를-bytetrack-트래킹으로-하고-인계-실패는-unknown으로-둔다)의 6번). 트래킹 미구현이라 지금은 항상 생략된다. 신원과 달리 단독으로도 뜻이 있으므로 식별 여부와 무관하게 보낸다 |

식별 성공이면 `student_id`와 `identity_confidence`를 함께 채우고 `face_bbox`는 선택으로
채운다. 미식별이거나 모델 기준 미달이면 세 필드를 모두 `null`로 두거나 생략한다. 가장
가까운 학생을 억지로 고르지 않는다. worker handler는 불완전한 신원 조합을 미식별로 낮춰
전송하며, FastAPI는 외부 필드와 불완전한 조합을 422로 거부한다.

## 모델을 붙였을 때 어디까지 자동으로 흐르는가

FastAPI 쪽 판정은 이미 구현돼 있다([결정 0032](../../docs/architecture/decisions.md#0032--학생-상태-판정을-좌석-근거-하나에서-파생시키고-수신-시점에-저장한다)).
**`student_id`와 `identity_confidence`를 채워 보내기 시작하면 그것만으로 학생 상태가
흐른다.** FastAPI에 고칠 것이 없다.

```text
탐지(+신원) → 좌석 ROI 대조 → SeatEvidence ┬→ 좌석 점유
                                           └→ 학생 상태 → 저장 + 이력 + SSE + 화면
```

붙이기 전에 확인할 것은 셋이다.

1. **`student_id`는 FastAPI 학생 원장의 내부 ID여야 한다.** 모델이 자체 라벨 인덱스를
   보내면 어느 학생과도 이어지지 않고 조용히 `UNKNOWN`으로 남는다.
2. **`identity_confidence`가 `STUDENT_IDENTITY_CONFIDENCE_THRESHOLD`(기본 0.5) 이상
   이어야 이름이 붙는다.** 미달은 `UNKNOWN`이다 — 오인식은 다른 학생의 출결을 바꾸는
   사고라 억지로 고르지 않는다.
3. **그 카메라에 좌석 ROI가 등록돼 있어야 좌석까지 이어진다.** ROI가 없으면 좌석 판정에
   참여하지 않는다. 입구 카메라처럼 신원만 만드는 카메라는 `role=IDENTITY_ONLY`로
   등록하며, 그 신원을 좌석까지 잇는 방법은 결정 0025의 3번에서 아직 `결정 필요`다.

판정이 왜 그렇게 나왔는지는 상태와 함께 저장되는 근거 코드(`reason`)와
`GET /api/v1/classrooms/{classroom_id}/students/{student_id}/state-history`로 확인한다.

`captured_at`은 실제 프레임 캡처 시각을 ISO 8601 timezone 포함 값으로 보낸다. UTC
`+00:00`을 권장한다. 처리·전송 시각으로 덮어쓰지 않는다. worker의 `event_id`는
`<camera_id>-<UTC 밀리초 시각>-<sequence>`이며 같은 프레임 재전송에서는 절대 바꾸지
않는다. 같은 ID와 같은 본문은 멱등 처리되지만, 같은 ID에 다른 본문을 붙이면 계약 오류다.

## 응답 처리

| HTTP | 의미 | 작업자 기대 동작 |
| --- | --- | --- |
| `201` | 새 이벤트 저장 완료 | 성공으로 종료 |
| `200` | 같은 이벤트가 이미 저장됨 | 멱등 성공으로 종료 |
| `404 VIDEO_STREAM_NOT_FOUND` | `camera_id`가 등록된 stream과 연결되지 않음 | 카메라 설정을 수정. 다른 ID로 같은 이벤트를 만들지 않음 |
| `409 INFERENCE_EVENT_CONFLICT` | 같은 `event_id`에 다른 본문 | event ID 안정성 버그를 수정 |
| `422 VALIDATION_ERROR` | 필드, 신뢰도, timezone, bbox 또는 추가 필드 오류 | payload 생성기를 수정 |
| `503 REPOSITORY_UNAVAILABLE` | FastAPI 저장소 일시 장애 | worker의 제한 재시도 뒤 다음 프레임으로 진행 |

현재 `FastAPIResultHandler`는 초기 전송 뒤 최대 두 번만 재시도하고 모두 실패하면 오류를
로그로 남긴다. 전송 실패 때문에 추론 루프를 멈추거나 무한 재시도하지 않는다.

## 로컬 검증

승인 fixture와 worker 직렬화가 같은지는 실제 모델 없이 확인할 수 있다.

```bash
cd worker
python -m pytest inference/tests/test_handler.py -q
python -m pytest inference/tests -q
```

모델이 만든 후보 JSON은 FastAPI의 실제 Pydantic 계약으로 검사한다. PowerShell에서는
다음처럼 후보 경로만 주입한다. 기준 fixture를 덮어쓰지 않는다.

```powershell
cd webapps/fastapi
$env:INFERENCE_EVENT_FIXTURE = 'C:\path\to\candidate-event.json'
python -m pytest tests/student_monitoring/test_monitoring_integration_foundation.py `
  -k candidate_worker_fixture_matches_fastapi_request_contract -q
Remove-Item Env:INFERENCE_EVENT_FIXTURE
```

전체 합성 흐름은 아래 한 파일로 검증한다. 강의실·카메라·좌석 2개·ROI 2개·학생 2명과
지정을 메모리에서 만들고, MongoDB adapter의 영속 문서로 서버 재시작도 모사한다.

```bash
cd webapps/fastapi
python -m pytest tests/student_monitoring/test_monitoring_integration_foundation.py -q
```

## 만들면 안 되는 출력

모델과 worker payload에는 `student_name`, `student_no`, `classroom_id`, `seat_id`,
`current_seat_id`, `PRESENT`, `WRONG_SEAT`, `UNKNOWN`, `ABSENT`, `IN_CLASSROOM`을 넣지
않는다. ROI 매핑, `seat_assignments` 대조, 활성 학생 이름 보강, stale 판단과 상태 SSE는
FastAPI 책임이다. 현 범위에서는 tracking과 시간표 기반 `ABSENT`가 아직 구현되지 않았다.
