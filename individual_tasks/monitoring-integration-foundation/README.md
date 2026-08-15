# 실시간 학생 상태 연동 기반 작업 인계

**상태**: `TASK-001`~`TASK-006` 구현·검증 완료. 실제 모델 연결은 후속 작업이다.
**작업 브랜치**: `feature/monitoring-integration-foundation`
**기준 브랜치**: `feature/seat-student-registration` (`d04b463`)
**최종 PR 대상**: PR1 병합 후 `develop`

`individual_tasks/`는 저장소 기본 설정상 개인 작업 자료로 ignore되지만, 이 폴더는 다음
작업자 인계를 위해 사용자가 명시적으로 요청한 문서이므로 예외적으로 추적한다. 다른
`individual_tasks` 자료의 ignore 정책은 변경하지 않는다.

## 이 폴더의 목적

모델 작업자가 사람 탐지·얼굴 인식 결과를 연결하기 전에 FastAPI 쪽에서 반드시 고정해야
하는 카메라·강의실·ROI·좌석 지정·학생 상태·SSE 계약을 한곳에 모은다. 구현자가 현재
코드의 임시 동작을 최종 계약으로 오해하거나, 모델이 `PRESENT` 같은 업무 상태를 직접
결정하는 방향으로 흐르는 것을 방지한다.

## 브랜치와 PR 운영

현재 브랜치는 PR1이 병합되기 전에 `feature/seat-student-registration`에서 분기한 stacked
브랜치다.

1. PR1 검토 중에는 이 브랜치의 PR base를 `feature/seat-student-registration`으로 둔다.
2. PR1에 수정 커밋이 추가되면 그 최신 커밋 위로 이 브랜치를 재정렬한다.
3. PR1이 `develop`에 병합되면 이 브랜치를 최신 `develop` 위로 재정렬하고 PR base를
   `develop`으로 변경한다.
4. PR1과 무관한 기존 미추적 `.docker` 파일은 이 브랜치에서 stage·수정·삭제하지 않는다.

## 문서 읽기 순서

1. [BASELINE.md](BASELINE.md) — 현재 코드와 로컬 MongoDB 진단 사실
2. [SPEC.md](SPEC.md) — 목표, 경계, 계약, 예외 정책, 완료 조건
3. [TASK.md](TASK.md) — 구현 순서와 의존성
4. `TASK-001.md`부터 번호 순서대로 수행

모델 작업자는 구현 결과를 먼저 읽은 뒤
[`worker/inference/MODEL_INTEGRATION.md`](../../worker/inference/MODEL_INTEGRATION.md)의
fixture와 검증 명령을 사용한다.

## 고정된 책임 경계

- `deeplearning`: 사람·얼굴 탐지와 학생 식별 결과만 만든다.
- `worker`: 프레임을 공급하고 추론을 실행하며 결과를 FastAPI 내부 API로 전달한다.
- `webapps/fastapi`: 카메라와 강의실을 연결하고 ROI·좌석 지정·시간 정책을 결합해 학생
  상태를 판정하며 REST와 SSE로 제공한다.
- 브라우저: FastAPI만 호출한다. 모델 서비스나 worker를 직접 호출하지 않는다.

## 이 작업에서 하지 않는 것

- 실제 얼굴 이미지·embedding·학생 영상 추가
- 얼굴 인식 모델 선택·학습·가중치 배포
- ByteTrack 등 tracking 구현과 `IN_CLASSROOM` 판정
- 수업 시간표와 최종 `ABSENT` 정책 구현
- 외부 메시지 브로커 도입과 다중 FastAPI worker 보장
- 현재 로컬 MongoDB 데이터를 승인 없이 직접 수정·삭제하는 작업
