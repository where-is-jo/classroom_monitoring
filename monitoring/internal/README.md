# monitoring/internal — 내부 모니터링

**운영자가 서비스 자체를 관찰하기 위한** Prometheus·Grafana 관측 설정을 관리한다.
대상은 우리 서비스의 상태와 성능이고, 보는 사람은 팀이다.

사용자에게 제품으로 제공하는 영상 실시간 모니터링은 이 디렉터리가 아니라
[`monitoring/external`](../external/README.md)이다. 둘은 이름만 같고 목적·대상·수요자가 다르다.

> 현재 상태: **추론 파이프라인과 자연어 검색 지표를 수집한다.** `worker`의 조립
> 실행(`python -m pipeline.main`)과 `fastapi`가 각각 `/metrics`를 노출하고, 로컬
> docker 스택의 Prometheus가 두 job을 긁는다. `deeplearning`은 아직 노출하지 않는다.
> Grafana 데이터소스·대시보드 provisioning은 이 디렉터리에서 관리한다.
> **알림 규칙은 아직 없다** — 알림 채널이 `결정 필요`이고, 임계값의 근거가 될
> 정상 범위 데이터가 아직 쌓이지 않았다.

## 디렉터리 구조

```text
monitoring/internal/grafana/
  provisioning/datasources/   Prometheus·Loki 데이터소스 자동 등록
  provisioning/dashboards/    대시보드 파일 provider 설정
  dashboards/                 대시보드 정의 JSON
```

**Prometheus 설정 파일(`prometheus.yml`)은 아직 여기 없다.** 로컬 docker 스택의
개인용 최소 설정(`.docker/prometheus/prometheus.yml`)으로만 존재하며, 지금은 그 파일이
`inference-worker` job 하나를 들고 있다. 통합 실행 수단이 공식화되면 이 디렉터리로
옮긴다(`결정 필요`).

### 대시보드

| 파일 | uid | 내용 |
| --- | --- | --- |
| `grafana/dashboards/stack-status.json` | `smart-office-stack` | 컨테이너 로그 발생량·오류 로그·로그 원문(Loki)과 스크랩 타겟 상태(Prometheus `up`) |

`uid`와 대시보드 안의 `project` label 값은 이전 주제 이름을 쓴다. 저장된 대시보드의
`uid`를 바꾸면 기존 링크와 즐겨찾기가 끊기므로, 저장소 이름 변경과 함께 다룬다.

**지금 존재하는 데이터만 쓴다.** 애플리케이션 지표 패널은 `/metrics`가 생긴 뒤에
별도 대시보드로 추가한다. 빈 패널을 미리 만들어 두지 않는다.

대시보드 JSON은 데이터소스를 `uid`(`prometheus`, `loki`)로 참조한다.
데이터소스 provisioning 파일의 `uid`를 바꾸면 대시보드도 함께 고쳐야 한다.

**Grafana UI에서 고친 내용은 컨테이너를 다시 만들면 사라진다.** 바꿨으면 JSON으로
export해서 `grafana/dashboards/` 아래 파일을 갱신해야 한다.

## 서비스 목적

각 서비스의 상태와 성능을 관찰할 수 있도록 수집·시각화·알림 설정을 한곳에서 관리한다.

## 책임

- Prometheus 수집 대상(scrape) 설정
- 알림 규칙 정의
- Grafana 대시보드 정의 파일 관리
- 지표 이름과 label 규칙의 실제 적용 여부 점검

## 포함해야 할 기능

- Prometheus 설정 파일
- 알림 규칙 파일
- Grafana 대시보드 정의(가능하면 코드로 관리)
- 각 설정의 목적을 설명하는 문서

## 포함하지 않아야 할 기능

- 애플리케이션 비즈니스 로직
- 각 서비스의 지표 노출 코드 자체(해당 서비스 디렉터리에 둔다)
- 실제 접속 자격 증명

## 지표 규칙

- 프로젝트 지표 이름은 `classroom_monitoring_` 접두사를 사용한다.
  이전 주제에서 쓰던 `smart_office_` 접두사는 더 이상 쓰지 않는다.
  지금 노출 중인 지표는 모두 이 접두사를 따른다.
- **값이 무한히 늘어나는 label을 사용하지 않는다.** 이 프로젝트에서는
  `student_id`, 요청 ID, 이벤트 ID가 여기 해당한다.
- **개인을 식별하는 값을 label에 넣지 않는다.** 학생별 지표가 필요하면 집계값으로
  만든다. 지표는 접근 통제가 약한 경로로 노출되기 쉽다.
- 지표 추가 절차는 `add-monitoring-metric` 스킬을 따른다.

### 지금 노출하는 지표 — worker

`worker`의 조립 실행(stream + inference)이 `METRICS_PORT`(기본 9101)에 노출한다.
정의는 [`worker/inference/metrics.py`](../../worker/inference/metrics.py)와
[`worker/shared/metrics.py`](../../worker/shared/metrics.py)에 있다.

| 지표 | 타입 | label | 무엇을 답하는가 |
| --- | --- | --- | --- |
| `classroom_monitoring_inference_duration_seconds` | Histogram | 없음 | 프레임 한 장 추론에 얼마나 걸리는가 |
| `classroom_monitoring_frames_processed_total` | Counter | `camera_id`, `result` | 처리량과 실패율 |
| `classroom_monitoring_inference_consecutive_failures` | Gauge | 없음 | 파이프라인이 멈추기 전에 알 수 있는가 |
| `classroom_monitoring_detections_total` | Counter | `class_name` | 탐지 자체가 끊겼는가 |
| `classroom_monitoring_detection_confidence` | Histogram | `class_name` | 모델 품질이 조용히 나빠지고 있는가 |
| `classroom_monitoring_frames_buffered_total` | Counter | 없음 | 수신이 버퍼에 넣은 프레임 수 |
| `classroom_monitoring_frames_dropped_total` | Counter | `reason` | 추론이 수신을 못 따라간 양 |
| `classroom_monitoring_frames_consumed_total` | Counter | 없음 | 소비자가 가져간 프레임 수 |
| `classroom_monitoring_frame_buffer_depth` | Gauge | 없음 | 지금 버퍼에 남은 프레임 수 |

label 값 종류 수는 `camera_id`가 등록 카메라 대수, `result`가 2(`ok`·`failed`),
`class_name`이 2(`person`·`cell phone`), `reason`이 2(`dropped`·`skipped`)다.
카메라 1대·클래스 2종 기준으로 실측한 시계열 수는 46개다(`_created` 제외).

**신뢰도 분포를 재는 이유**는 지연과 처리량이 시스템이 도는지만 알려주기 때문이다.
정답 라벨이 없는 운영 환경에서 모델 품질 저하를 잡는 대리 지표가 신뢰도 분포이며,
촬영 환경이 바뀌면 탐지가 끊기기 전에 분포가 먼저 내려간다.

### PromQL 예시

```promql
# 추론 지연 p95
histogram_quantile(0.95, rate(classroom_monitoring_inference_duration_seconds_bucket[5m]))

# 추론 실패율
rate(classroom_monitoring_frames_processed_total{result="failed"}[5m])
  / rate(classroom_monitoring_frames_processed_total[5m])

# 탐지 신뢰도 중앙값 — 내려가면 모델이나 촬영 환경이 바뀐 것이다
histogram_quantile(0.5, rate(classroom_monitoring_detection_confidence_bucket{class_name="person"}[30m]))

# 프레임 드롭률 — 추론이 수신을 못 따라가는가
rate(classroom_monitoring_frames_dropped_total[5m])
  / rate(classroom_monitoring_frames_buffered_total[5m])

# 파이프라인이 멈추기 직전인가 (한계 기본값은 5)
classroom_monitoring_inference_consecutive_failures >= 3
```

### 지금 노출하는 지표 — fastapi

`fastapi`가 앱과 같은 포트의 `/metrics`에 노출한다. 정의는
[`app/llm_search/metrics.py`](../../webapps/fastapi/app/llm_search/metrics.py)에 있다.

| 지표 | 타입 | label | 무엇을 답하는가 |
| --- | --- | --- | --- |
| `classroom_monitoring_llm_plan_duration_seconds` | Histogram | `attempt`, `outcome` | 계획 생성이 얼마나 걸리고, **재시도가 얼마나 잦은가** |
| `classroom_monitoring_llm_search_duration_seconds` | Histogram | `outcome` | 사용자가 실제로 기다린 시간(실패 포함) |
| `classroom_monitoring_llm_schema_fallback_total` | Counter | 없음 | `json_schema` 폴백이 상시 발동 중인가 |
| `classroom_monitoring_llm_search_truncated_total` | Counter | 없음 | `LLM_SEARCH_SCAN_LIMIT`이 적정한가 |

`attempt`는 2(`first`·`retry`), `outcome`은 3(`success`·`invalid`·`unavailable`)이다.
**재시도 횟수를 별도 Counter로 두지 않는다** — Histogram의 `_count`가 label 조합마다
시도 횟수를 이미 내보낸다. 검색 한 건 기준으로 실측한 시계열 수는 26개다.

`outcome` 세 값을 합치지 않는 이유는 사용자가 할 일이 다르기 때문이다. `invalid`는
질문을 다시 쓰면 될 수 있고, `unavailable`은 질문을 고쳐도 소용없다.

**질문 원문과 그 해시는 label로 쓰지 않는다.** 값이 무한히 늘어나고 질문에는 사람이
찾는 대상이 담긴다. 개별 질문 추적이 필요하면 지표가 아니라 로그를 본다.

**uvicorn을 워커 여러 개로 띄우면 값이 갈라진다.** 프로세스마다 레지스트리가 따로
생기고 스크랩은 그중 하나에만 닿는다. 지금은 단일 프로세스로 실행하므로 문제가 없지만,
배포 방식이 `결정 필요`라 워커를 늘릴 때 `prometheus_client`의 multiprocess 모드를
켜야 한다.

### PromQL 예시 — fastapi

```promql
# 계획 생성 지연 p95
histogram_quantile(0.95, rate(classroom_monitoring_llm_plan_duration_seconds_bucket[5m]))

# 첫 시도 규격 위반율 — 모델·프롬프트를 고칠지 판단하는 근거
rate(classroom_monitoring_llm_plan_duration_seconds_count{attempt="first",outcome="invalid"}[30m])
  / rate(classroom_monitoring_llm_plan_duration_seconds_count{attempt="first"}[30m])

# 검색이 LLM 때문에 느린가, 저장소 때문에 느린가
histogram_quantile(0.95, rate(classroom_monitoring_llm_search_duration_seconds_bucket[5m]))
  - histogram_quantile(0.95, rate(classroom_monitoring_llm_plan_duration_seconds_bucket[5m]))

# json_schema 폴백이 상시 발동 중인가
rate(classroom_monitoring_llm_schema_fallback_total[30m]) > 0
```

**검색과 추론을 함께 봐야 하는 이유**는 llama-server와 inference-worker가 같은 GPU를
나눠 쓰기 때문이다. 검색이 몰려 탐지가 느려지는지는 두 지연을 겹쳐 봐야 알 수 있다.

### 아직 노출하지 않는 것

`예정`이며 코드가 없다. **없는 지표의 패널을 미리 만들지 않는다.**

| 대상 | 담당 | 비고 |
| --- | --- | --- |
| 얼굴 분석 단계별 지연·세션 수 | `deeplearning` | |
| 탐지 결과 HTTP 전달 실패 건수 | `worker/inference` | 지금은 로그로만 남는다 |
| 카메라 연결 상태·재연결 횟수 | `worker/stream` | |
| 식별 성공률 | — | **얼굴 인식이 미구현이라 지금 만들면 항상 0이다** |

## 예상 기술

| 항목 | 상태 | 비고 |
| --- | --- | --- |
| 수집 | Prometheus | 프로젝트 전제 |
| 시각화 | Grafana | 프로젝트 전제 |
| 알림 채널 | 결정 필요 | 이메일 / 메신저 후보 |
| 배포 방식 | 결정 필요 | 최상위에 인프라 파일을 두지 않는 제약 고려 |

## 다른 서비스와의 관계

- `fastapi`, `worker`: 지표를 노출한다. monitoring은 이를 수집만 한다.
- `deeplearning`: 지표 노출은 `예정`이다.
- 브라우저: 지표를 직접 조회하지 않는다. 필요하면 `fastapi`를 통한다.
- `RPAs`: 자동화 실행 결과를 지표로 다룰지는 **결정 필요**.

## 향후 구현 시 필요한 환경변수

값의 취급과 명명 규칙은 [환경변수 규칙](../../docs/conventions/environment-convention.md)을 따른다.

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `PROMETHEUS_SCRAPE_INTERVAL` | 수집 주기 | |
| `GRAFANA_ADMIN_USER` | Grafana 관리자 계정 | 값은 외부 주입 |
| `ALERT_WEBHOOK_URL` | 알림 전송 대상 | 채널 확정 후 |

## 테스트 전략

- 설정 파일은 문법 검증(예: `promtool` 계열 도구)을 우선한다.
- 알림 규칙은 표현식이 의도한 조건에서 발화하는지 확인한 뒤 반영한다.

### 대시보드 검증

`monitoring/internal`은 Grafana를 직접 실행하지 않는다. 실행 수단은 컨테이너 스택이며,
그 구성은 아직 개인 로컬 전용이다(공식 실행 수단은 `결정 필요`).
아래는 그 스택에서 실제로 확인한 명령이다. 관리자 자격 증명은 외부에서 주입한다.

```bash
# 대시보드 JSON 문법
python -c "import json;json.load(open('monitoring/internal/grafana/dashboards/stack-status.json',encoding='utf-8'))"

# provisioning 반영 확인 (데이터소스 uid, 대시보드 등록 여부)
curl -s -u "$GF_USER:$GF_PW" http://127.0.0.1:3000/api/datasources
curl -s -u "$GF_USER:$GF_PW" 'http://127.0.0.1:3000/api/search?type=dash-db'
```

**패널이 그려지는지는 JSON 문법과 별개다.** 데이터소스 프록시로 각 패널의 쿼리를
직접 실행해 결과가 비어 있지 않은지 확인한다.

```bash
curl -s -u "$GF_USER:$GF_PW" --get \
  'http://127.0.0.1:3000/api/datasources/proxy/uid/loki/loki/api/v1/query' \
  --data-urlencode 'query=sum by (container) (rate({project="smart-office-monitoring"}[5m]))'
```

## 관련 문서

- `add-monitoring-metric` 스킬
- [아키텍처](../../docs/architecture/README.md)
- [코딩 규칙](../../docs/conventions/coding-convention.md)
