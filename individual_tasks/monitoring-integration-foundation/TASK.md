# 실시간 학생 상태 연동 기반 작업 목록

**목적**: 모델 구현과 분리해 FastAPI 연동 기반을 검토 가능한 단위로 구현한다.
**대상 독자**: 계획·아키텍처·FastAPI·테스트·문서 담당자.

| 작업 | 범위 | 선행 의존성 | 완료 기준 |
| --- | --- | --- | --- |
| [TASK-001](TASK-001.md) | ADR·계약·아키텍처 승인 | 없음 | BLOCKER/MAJOR 없이 구현 허용 |
| [TASK-002](TASK-002.md) | camera→classroom 참조 무결성 | TASK-001 | 유효 연결만 상태 판정에 사용 |
| [TASK-003](TASK-003.md) | ROI polygon 좌석 매핑과 데이터 정합성 | TASK-002 | 단일 ROI 정본으로 결정적 매핑 |
| [TASK-004](TASK-004.md) | 최근 탐지 기반 학생 상태 조회 | TASK-003 | 지정 학생 전체 상태 REST 반환 |
| [TASK-005](TASK-005.md) | 학생 상태 SSE와 두 화면 갱신 | TASK-004 | bbox·학생 상태가 실시간 반영 |
| [TASK-006](TASK-006.md) | 합성 E2E·회귀·인계 문서 | TASK-005 | 모델 작업자가 fixture로 계약 검증 |

## 진행 상태

- [x] TASK-001 아키텍처 결정과 계약 승인
- [x] TASK-002 camera→classroom 참조 무결성
- [x] TASK-003 카메라별 ROI polygon 매핑
- [x] TASK-004 최근 탐지 기반 학생 상태 REST
- [x] TASK-005 학생 상태 SSE와 화면 갱신
- [x] TASK-006 합성 E2E, worker 계약 fixture, 인계 문서와 전체 회귀

## 공통 구현 규율

- 제품 코드는 `TASK-001` 승인 전 변경하지 않는다.
- 한 작업은 소유 파일과 테스트를 함께 커밋한다.
- 실제 얼굴·학생 영상·embedding을 사용하지 않는다.
- MongoDB 데이터 수정이 필요하면 dry-run 결과와 정확한 대상을 먼저 확인하고 별도 승인을
  받는다.
- 기존 미추적 `.docker` 파일을 stage하지 않는다.
- 모델·worker에 학생 상태 업무 어휘를 넣지 않는다.
- API 계약 변경은 소비자 테스트와 문서를 같은 작업에서 갱신한다.
