# smart_office_monitoring

출근했조 팀 프로젝트: 스마트 오피스 모니터링

## 프로젝트 목적

오피스 공간의 CCTV·Jetson 영상을 받아 컴퓨터 비전 모델로 분석하고,
그 결과를 대시보드에서 확인할 수 있게 하는 모니터링 시스템을 만든다.
반복적인 사무 업무는 별도의 RPA 자동화로 처리한다.

## 현재 단계

**부트스트랩 단계다.** 서비스 코드는 아직 없다.
지금 저장소에 있는 것은 디렉터리 구조와 팀·AI 에이전트가 함께 쓸 문서 체계다.

문서 체계는 모두 갖춰졌다. 다음 단계는 서비스 구현이며,
그에 앞서 [미결정 항목](#아직-결정되지-않은-항목)을 확정해야 한다.

아직 정해지지 않은 항목은 [결정되지 않은 항목](#아직-결정되지-않은-항목)을 참고한다.

## 디렉터리 구조

```text
webapps/       웹 애플리케이션
deeplearning/  모델 추론 서비스
worker/        영상 수신·프레임 공급
monitoring/    Prometheus·Grafana 설정
docs/          문서, AI 에이전트 규칙, Skill, 프롬프트
RPAs/          업무 자동화 프로젝트
README.md      이 문서
```

최상위에는 위 항목과 `.gitignore`만 둔다. 빌드 설정, 인프라 파일, 에이전트 문서를
최상위에 새로 만들지 않는다. 자세한 예외 범위는
[AGENTS.md의 Repository Structure](./docs/agents/AGENTS.md#repository-structure)에 있다.

### 서비스

| 디렉터리 | 역할 |
| --- | --- |
| [webapps/fastapi](./webapps/fastapi/README.md) | FastAPI 웹 애플리케이션. API와 Jinja2 화면을 제공하는 유일한 외부 진입점. |
| [deeplearning](./deeplearning/README.md) | 영상 프레임 추론. 표준화된 탐지 결과를 반환한다. |
| [worker](./worker/README.md) | CCTV·Jetson 영상 수신과 프레임 공급. |
| [monitoring](./monitoring/README.md) | Prometheus·Grafana 설정 관리. |

`webapps/`는 웹 애플리케이션 전용이다. 웹 요청을 처리하지 않는 서비스는
최상위에 독립 디렉터리로 둔다.

### docs

| 디렉터리 | 역할 |
| --- | --- |
| `docs/agents` | AI 에이전트가 지켜야 할 작업 규칙 |
| `docs/skills` | 반복 작업의 실행 절차 |
| `docs/prompts` | 바로 복사해 쓰는 작업 프롬프트 |
| `docs/architecture` | 구조 설명과 결정 기록(ADR) |
| `docs/conventions` | Git·코딩·API·환경변수·문서 규칙 |
| `docs/guides` | [시작하기](./docs/guides/getting-started.md), [로컬 개발](./docs/guides/local-development.md), [테스트](./docs/guides/testing.md), [배포](./docs/guides/deployment.md) |
| `docs/templates` | 복사해 쓰는 문서 템플릿 — [서비스 README](./docs/templates/service-readme-template.md), [기능 명세](./docs/templates/feature-spec-template.md), [API 명세](./docs/templates/api-spec-template.md), [트러블슈팅](./docs/templates/troubleshooting-template.md), [ADR](./docs/templates/adr-template.md) |

### RPAs

업무 자동화 프로젝트를 프로젝트별 독립 디렉터리로 관리한다.
공통 규칙은 [RPAs/README.md](./RPAs/README.md)에 있다.

## 팀원이 가장 먼저 읽을 문서

1. 이 문서
2. 담당 서비스 디렉터리의 `README.md`
3. [아키텍처 개요](./docs/architecture/overview.md) — 서비스 관계와 미결정 항목
4. 개발 규칙 — [Git](./docs/conventions/git-convention.md) · [코딩](./docs/conventions/coding-convention.md) · [API](./docs/conventions/api-convention.md) · [환경변수](./docs/conventions/environment-convention.md) · [문서](./docs/conventions/documentation-convention.md)
5. [시작하기 가이드](./docs/guides/getting-started.md) — 무엇부터 읽고 어디서 시작할지

## AI 에이전트 사용 방법

이 저장소는 AI 코딩 에이전트와 함께 작업하는 것을 전제로 한다.

- 공통 작업 계약은 [docs/agents/AGENTS.md](./docs/agents/AGENTS.md)에 있다. 에이전트는 이 문서를 먼저 읽는다.
- 역할별 규칙: [FastAPI](./docs/agents/fastapi-agent.md) · [AI](./docs/agents/ai-agent.md) · [RPA](./docs/agents/rpa-agent.md) · [문서](./docs/agents/documentation-agent.md)
- 반복 작업은 [docs/skills/](./docs/skills/README.md)의 절차를 따른다. 새 Skill 추가 방법도 같은 문서에 있다.
- 작업 지시는 `docs/prompts/`의 템플릿을 복사해 변수를 채운 뒤 사용한다.
  [프로젝트 초기화](./docs/prompts/initialize-project.md) · [기능 구현](./docs/prompts/implement-feature.md) · [버그 조사](./docs/prompts/investigate-bug.md) · [PR 리뷰](./docs/prompts/review-pull-request.md) · [문서 갱신](./docs/prompts/update-project-docs.md)

에이전트 관련 문서는 모두 `docs/` 아래에 두며, 최상위에 `AGENTS.md`를 만들지 않는다.

## 새로 추가할 때 위치

| 추가할 것 | 위치 | 참고 |
| --- | --- | --- |
| 새 웹 애플리케이션 | `webapps/<service-name>/` | [서비스 README 템플릿](./docs/templates/service-readme-template.md) |
| 웹이 아닌 새 서비스 | 최상위 `<service-name>/` | 동일. AGENTS.md의 최상위 제약도 갱신한다 |
| 새 RPA | `RPAs/<rpa-name>/` | [RPA 규칙](./RPAs/README.md) |
| 아키텍처 결정 | `docs/architecture/decisions/` | [ADR 작성 방법](./docs/architecture/decisions/README.md) |
| 개발 규칙 | `docs/conventions/` | 기존 문서 수정을 우선 |
| 작업 절차 | `docs/skills/<skill-name>/SKILL.md` | [Skill 작성 방법](./docs/skills/README.md#새-skill-추가하기) |
| 문서 템플릿 | `docs/templates/` | |

## 기여 흐름

1. 작업 대상 디렉터리의 README와 관련 규칙 문서를 읽는다.
2. 브랜치를 만들어 작업한다.
3. 변경 범위를 작게 유지한다.
4. 가능한 검증을 실행하고, 실행하지 못한 검증은 그대로 밝힌다.
5. 문서 갱신이 필요한지 확인한다.
6. Pull Request로 리뷰를 요청한다.

비밀키와 실제 환경변수 값은 어떤 경우에도 커밋하지 않는다.
상세 규칙은 [Git 규칙](./docs/conventions/git-convention.md)에 있다.

## 아직 결정되지 않은 항목

아래는 확정되지 않았다. 임의로 구현하지 말고 결정 후 ADR로 남긴다.

- 실시간 화면 갱신 방식(폴링 / SSE / WebSocket)
- 서비스 간 통신 방식(동기 HTTP / 메시지 큐)
- 사용할 모델과 버전, GPU·Jetson 실행 범위
- 영상 수신 프로토콜(RTSP / WebRTC / HTTP 푸시)
- 캐시·큐 도입 여부(Redis는 후보)
- 영상 저장 범위·보존 기간·접근 권한 — 개인정보가 걸린 합의 사항
- 영상을 저장하는 주체(worker / fastapi)
- 배포 환경과 배포 방식
- 알림 채널

확정된 결정은 [ADR](./docs/architecture/decisions/README.md)에 기록되어 있다.
메타데이터 저장소(MongoDB), 영상 저장소(MinIO), fastapi 내부 구조가 여기에 해당한다.
