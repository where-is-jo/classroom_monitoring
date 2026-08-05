# Add Monitoring Metric

## Purpose

서비스에 Prometheus 지표를 추가할 때, 이름·타입·label을 잘못 골라
나중에 되돌리기 어려운 문제(고카디널리티, 이름 충돌)를 만들지 않기 위한 절차다.

## When to Use

- 새 지표를 노출해야 할 때
- 기존 지표에 label을 추가해야 할 때
- 알림 규칙에 필요한 관측값이 없을 때

다음에는 사용하지 않는다.

- Grafana 대시보드 배치만 바꾸는 작업
- 로그로 충분한 일회성 디버깅

## Required Inputs

| 입력 | 설명 |
| --- | --- |
| 관측 목적 | 이 지표로 어떤 질문에 답하려 하는가 |
| 대상 서비스 | 어느 서비스가 노출하는가 |
| 측정 대상 | 횟수인가, 현재 값인가, 분포인가 |
| 구분 축 | 어떤 기준으로 나눠 봐야 하는가 |
| 사용처 | 대시보드 / 알림 / 둘 다 |

"일단 넣어두자"는 이유로 지표를 추가하지 않는다. 답할 질문이 없으면 만들지 않는다.

## Preconditions

- [`monitoring/README.md`](../../../monitoring/README.md)를 읽었다
- 기존 지표 목록을 조회해 같은 것이 없는지 확인했다
- 대상 서비스가 이미 지표 노출 경로를 가지고 있는지 확인했다

## Procedure

1. **기존 지표 조사**
   - 같은 질문에 답할 수 있는 지표가 이미 있으면 추가하지 않는다.
   - 유사 지표가 있으면 이름·label 형식을 맞춘다.

2. **타입 선택**

   | 타입 | 사용 시점 | 예 |
   | --- | --- | --- |
   | Counter | 누적되며 줄지 않는 횟수 | 처리한 프레임 수, 요청 수, 실패 수 |
   | Gauge | 오르내리는 현재 값 | 활성 스트림 연결 수, 큐 길이 |
   | Histogram | 값의 분포와 분위수 | 추론 지연 시간, 응답 시간 |

   평균만 필요해 보여도 지연 시간은 Histogram을 쓴다. 평균은 느린 요청을 가린다.

3. **이름 결정**
   - 프로젝트 지표는 가능한 경우 `smart_office_` 접두사를 사용한다.
   - 형식: `smart_office_<대상>_<측정값>_<단위>`
   - 단위는 기본 단위를 쓴다: `_seconds`, `_bytes`, `_total`(Counter)
   - 예시
     - `smart_office_deeplearning_duration_seconds` (Histogram)
     - `smart_office_frames_processed_total` (Counter)
     - `smart_office_stream_connections_active` (Gauge)

4. **label 검토**
   - label은 값의 종류가 적고 미리 알 수 있는 것만 쓴다: `service`, `camera_id`(대수가 고정인 경우), `status`.
   - **고카디널리티 label을 쓰지 않는다.** 사용자 ID, 요청 ID, 타임스탬프, 파일 경로, 원본 URL, 오류 메시지 전문이 해당한다.
   - label 값 종류 수를 예상해 적는다. 수백 개를 넘길 가능성이 있으면 label로 두지 않는다.
   - 판단이 서지 않으면 label 없이 시작한다. 나중에 추가하는 편이 제거보다 쉽다.

5. **구현**
   - 지표 등록을 한 곳에 모으고, 비즈니스 로직에 계측 코드를 흩뿌리지 않는다.
   - 지표 수집 실패가 기능을 중단시키지 않게 한다.

6. **PromQL 예시 작성**
   - 이 지표로 답하려던 질문을 실제 쿼리로 적어 문서에 남긴다.
   - 예:
     - 초당 처리량: `rate(smart_office_frames_processed_total[5m])`
     - 지연 95분위: `histogram_quantile(0.95, rate(smart_office_deeplearning_duration_seconds_bucket[5m]))`
     - 실패율: `rate(smart_office_requests_total{status="error"}[5m]) / rate(smart_office_requests_total[5m])`

7. **Grafana 반영 검토**
   - 기존 대시보드에 넣을지, 새 패널이 필요한지 판단한다.
   - 알림이 필요하면 임계값과 지속 시간의 근거를 함께 적는다.

## Validation

- 지표 노출 경로에서 새 지표가 나오는지 확인한다.
- Prometheus가 수집하는지 확인한다.
- 작성한 PromQL 쿼리를 실제로 실행해 값이 의도대로 나오는지 확인한다.
- label 값 종류 수가 예상 범위인지 확인한다.

수집 환경이 아직 없으면 확인하지 못한 항목을 그대로 보고한다.

## Expected Output

- 지표 등록·계측 코드
- 지표 이름, 타입, label, 의미를 적은 문서
- PromQL 예시 쿼리
- 대시보드·알림 변경분(해당 시)
- [완료 보고](../../agents/AGENTS.md#completion-report)

## Failure Handling

| 상황 | 대응 |
| --- | --- |
| 비슷한 지표가 이미 있다 | 새로 만들지 말고 기존 지표를 사용하거나 확장한다 |
| 필요한 구분 축이 고카디널리티다 | 지표 label 대신 로그로 남기고, 지표는 집계값만 둔다 |
| 이름을 바꿔야 한다 | 이미 사용 중이면 즉시 바꾸지 말고 영향(대시보드·알림)을 먼저 확인한다 |
| 지표 계측이 성능에 영향을 준다 | 측정 결과와 함께 보고하고 샘플링 여부를 결정한다 |
| 수집 환경이 아직 없다 | 코드만 반영하고, 검증하지 못했음을 명시한다 |

## Completion Checklist

- [ ] 이 지표로 답할 질문이 명확하다
- [ ] 같은 지표가 이미 있는지 확인했다
- [ ] 타입(Counter/Gauge/Histogram) 선택 근거가 있다
- [ ] 이름에 `smart_office_` 접두사와 단위가 들어 있다
- [ ] 고카디널리티 label이 없다
- [ ] label 값 종류 수를 예상해 기록했다
- [ ] PromQL 예시를 문서에 남겼다
- [ ] 지표 수집 실패가 기능을 중단시키지 않는다
- [ ] 확인하지 못한 검증 항목을 보고에 밝혔다
