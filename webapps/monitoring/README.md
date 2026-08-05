# monitoring

Prometheus와 Grafana 기반 관측 설정을 관리하는 디렉터리다.

> 현재 상태: 구현 전. 아직 수집 대상 서비스가 존재하지 않으므로
> 대시보드와 알림 규칙은 서비스 구현과 함께 추가한다.

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
- 지표 추가 절차는 [모니터링 지표 추가 Skill](../../docs/skills/add-monitoring-metric/SKILL.md)을 따른다.

## 예상 기술

| 항목 | 상태 | 비고 |
| --- | --- | --- |
| 수집 | Prometheus | 프로젝트 전제 |
| 시각화 | Grafana | 프로젝트 전제 |
| 알림 채널 | 결정 필요 | 이메일 / 메신저 후보 |
| 배포 방식 | 결정 필요 | 최상위에 인프라 파일을 두지 않는 제약 고려 |

## 다른 서비스와의 관계

- `backend`, `inference`, `stream-server`: 지표 노출 주체다. monitoring은 이를 수집만 한다.
- `frontend`: 지표를 직접 조회하지 않는다.
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
- 실행 가능한 검증 명령은 도구가 도입된 시점에 이 문서에 추가한다.

## 관련 문서

- [모니터링 지표 추가 절차](../../docs/skills/add-monitoring-metric/SKILL.md)
- [아키텍처 개요](../../docs/architecture/overview.md)
- [코딩 규칙](../../docs/conventions/coding-convention.md)
