# TASK-001 연동 경계와 계약 승인

**목적**: 구현 전에 ROI 정본, 책임 경계, SSE 계약, 단일 프로세스 제약을 확정한다.
**대상 독자**: Planner와 Architecture Reviewer.

## 소유 파일

- `individual_tasks/monitoring-integration-foundation/`
- 승인 시 필요한 새 ADR과 기존 아키텍처 기준 문서

## 선행 의존성

없음.

## 수행 범위

- [SPEC.md](SPEC.md)의 요구사항, API 계약, 예외 정책, Data Flow를 실제 코드와 대조한다.
- `roi_connections.polygon`을 좌석 위치 정본으로 쓰고 `seat_assignments`를 학생 배정 정본으로
  쓰는 결정을 ADR로 남긴다.
- 강의실당 활성 카메라 하나를 강제할지, ROI를 `camera_id + seat_id`로 확장할지 확정한다.
  여러 화각을 현재 `classroom_id + seat_id` polygon 하나로 처리하는 선택은 허용하지 않는다.
- live ROI revision 0과 기준 이미지 revision의 재시작 후 검토 정책을 확정한다.
- `seat.geometry`와 ROI polygon을 동시에 fallback으로 쓰지 않는 이유를 기록한다.
- `student_monitoring`이 상태 판정을 소유하고 worker/deeplearning은 식별 결과까지만
  책임지는지 확인한다.
- 학생 상태 SSE 경로와 payload, 인메모리 broadcaster의 단일 프로세스 제약을 승인한다.
- 기존 ROI `student_id` 필드는 이번 작업에서 삭제하지 않되 판정 입력에서 제외하는
  호환 정책을 확인한다.
- 얼굴 gallery 전달과 실제 얼굴 처리, tracking, `ABSENT`가 이번 범위 밖인지 확인한다.

## Architecture Review 체크

- 브라우저가 FastAPI 외 서비스를 직접 호출하지 않는가.
- 새 외부 I/O가 없다면 불필요한 포트를 만들지 않았는가.
- 같은 프로세스 서비스 연결에 Facade·Observer·Strategy를 선제 도입하지 않았는가.
- FastAPI 내부 방향이 `router → service → port ← adapter`를 지키는가.
- 상태 판정 임계값과 stale 기준이 설정으로 주입되는가.
- 탐지 저장 성공과 상태 판정 실패의 경계가 분리되는가.
- 저장된 이벤트의 `stream_id`, `classroom_id`가 요청값이 아니라 서버 stream catalog에서
  해석되는가.
- 개인정보와 얼굴 데이터가 계약·로그·테스트에 노출되지 않는가.

## 완료 기준

- ADR 번호와 결론이 기록돼 있다.
- SPEC/TASK의 BLOCKER·MAJOR finding이 해소됐다.
- 구현 허용 또는 중단 사유가 이 문서 하단에 명시돼 있다.

## 리뷰 결과

`APPROVED` — [결정 0019](../../docs/architecture/decisions.md#0019--실시간-학생-상태-연동은-카메라별-roi와-fastapi-판정을-사용한다)에
따라 구현을 진행할 수 있다.

### Architecture Review 근거

- **배치**: HTTP·SSE와 학생 상태 업무 판단은 기존 `webapps/fastapi` 기능 디렉터리에 둔다.
  worker와 deeplearning에는 식별 결과 계약 외의 업무 상태를 추가하지 않는다.
- **호출 방향**: worker의 내부 HTTP 요청과 브라우저 REST·SSE는 FastAPI router로 들어오고,
  router는 service를 호출하며 외부 저장은 기존 repository port와 adapter를 사용한다.
- **포트·패턴**: 새 외부 I/O가 없으므로 새 종류의 포트가 필요하지 않다. 반응이 3개 미만이고
  순서가 중요하므로 Observer를 만들지 않고 service에서 저장 후 판정을 명시적으로 호출한다.
  Facade와 Strategy도 도입 조건을 충족하지 않는다.
- **정본**: 위치는 카메라별 ROI polygon, 배정은 `seat_assignments`, 카메라 소속은 video
  stream catalog로 분리돼 같은 사실을 두 저장소가 동시에 소유하지 않는다.
- **실패 경계**: 탐지 저장을 먼저 완료하고 ROI·상태 판정 실패는 해당 이벤트의 파생 처리를
  건너뛴다. 얼굴 원본과 embedding은 계약·로그·테스트에 들어가지 않는다.
- **제약**: SSE는 단일 FastAPI 프로세스에 한정하며, legacy ROI는 카메라를 다시 지정하기
  전까지 판정에서 제외한다. 실제 얼굴 인식, tracking, `ABSENT`는 구현하지 않는다.

BLOCKER와 MAJOR finding은 없다. 구현 중 이 경계를 바꿔야 하면 코드를 진행하지 않고 새
Architecture Review와 ADR을 먼저 수행한다.
