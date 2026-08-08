# monitoring/internal — 내부 모니터링

**운영자가 서비스 자체를 관찰하기 위한** Prometheus·Grafana 관측 설정을 관리한다.
대상은 우리 서비스의 상태와 성능이고, 보는 사람은 팀이다.

사용자에게 제품으로 제공하는 영상 실시간 모니터링은 이 디렉터리가 아니라
[`monitoring/external`](../external/README.md)이다. 둘은 이름만 같고 목적·대상·수요자가 다르다.

> 현재 상태: **Grafana 설정만 있다.** 데이터소스(Prometheus·Loki) provisioning과
> 대시보드 하나를 이 디렉터리에서 관리한다. **Prometheus 수집 설정과 알림 규칙은 아직 없다** —
> 어떤 서비스도 `/metrics`를 노출하지 않아 지금 수집할 지표가 없기 때문이다.
> 알림 규칙은 알림 채널이 `결정 필요` 상태라 시작하지 않았다.

## 디렉터리 구조

```text
monitoring/internal/grafana/
  provisioning/datasources/   Prometheus·Loki 데이터소스 자동 등록
  provisioning/dashboards/    대시보드 파일 provider 설정
  dashboards/                 대시보드 정의 JSON
```

**Prometheus 설정 파일(`prometheus.yml`)은 아직 여기 없다.** 로컬 docker 스택의 개인용
최소 설정으로만 존재하며, 수집할 지표가 정해지면 이 디렉터리로 옮긴다.

### 대시보드

| 파일 | uid | 내용 |
| --- | --- | --- |
| `grafana/dashboards/stack-status.json` | `smart-office-stack` | 컨테이너 로그 발생량·오류 로그·로그 원문(Loki)과 스크랩 타겟 상태(Prometheus `up`) |

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

- 프로젝트 지표 이름은 가능한 경우 `smart_office_` 접두사를 사용한다.
- 사용자 ID, 요청 ID처럼 값이 무한히 늘어나는 label은 사용하지 않는다.
- 지표 추가 절차는 `add-monitoring-metric` 스킬을 따른다.

## 예상 기술

| 항목 | 상태 | 비고 |
| --- | --- | --- |
| 수집 | Prometheus | 프로젝트 전제 |
| 시각화 | Grafana | 프로젝트 전제 |
| 알림 채널 | 결정 필요 | 이메일 / 메신저 후보 |
| 배포 방식 | 결정 필요 | 최상위에 인프라 파일을 두지 않는 제약 고려 |

## 다른 서비스와의 관계

- `fastapi`, `deeplearning`, `worker`: 지표 노출 주체다. monitoring은 이를 수집만 한다.
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
