# 스마트 클래스 모니터링

어디있조 팀 프로젝트.

> **코드 안쪽 이름 일부가 아직 이전 주제를 따른다.** 이 프로젝트는 사무실 직원
> 모니터링으로 시작했다가 스마트 클래스 모니터링으로 주제가 바뀌었다. 저장소·조직 이름은
> 새 이름(`where-is-jo/classroom_monitoring`)으로 옮겼지만, `app/employees`,
> `app/interview_waits` 같은 기능 디렉터리 이름은 이전 주제에서 온 것이다.
> 디렉터리·도메인 이름 정리는 별도 과제다.

## 프로젝트 목적

강의실 카메라 영상에서 **학생을 식별**하고, 그 학생의 **현재 위치**를 **지정 좌석**과
**수업 시간 정책**에 결합해, 관리자가 강의실을 직접 돌지 않아도 학생 현황을 확인할 수
있게 한다. 확인된 상태를 이용한 반복 업무는 별도의 RPA 자동화로 처리한다.

**얼굴 인식 시스템이 아니다.** 얼굴 인식은 학생을 식별하는 수단이고, 제품이 내놓는
것은 출결 판단에 쓸 수 있는 학생 상태다.

| 상태 | 의미 | 범위 |
| --- | --- | --- |
| `PRESENT` | 학생이 식별됐고 지정 좌석에 있다 | MVP |
| `WRONG_SEAT` | 학생이 식별됐으나 지정 좌석이 아니다 | MVP |
| `ABSENT` | 수업 시간 중 유예 시간을 넘겨 미식별 | MVP |
| `IN_CLASSROOM` | 좌석에는 없지만 교실 안에 있다 | 최종 확장 |

무엇을 만들 것인지는 [학생 모니터링 MVP 명세](./docs/specs/student-monitoring-mvp.md)에 있다.

## 현재 단계

실행 코드가 있는 곳은 두 곳이다.

- **`webapps/fastapi`** — 강의실 좌석 현황, 실시간 모니터링, 자연어 검색 세 화면과
  그 API. **학생 식별·얼굴 등록·상태 판정은 아직 없다.** 현재 좌석 상태는 "자리가
  찼는지"를 뜻하며 "누가 앉았는지"가 아니다. local/dev에서는 실제 영상이나 개인정보
  없이 합성 모니터링·검색 흐름을 시연할 수 있다.
- **`worker`** — 카메라 영상을 받아(`stream`) 프레임을 골라 탐지하고(`inference`),
  탐지 인원 수가 바뀌면 스냅샷을 객체 저장소에 올린다.
  **영상 원본은 저장하지 않는다**
  ([결정 0011](./docs/architecture/decisions.md#0011--영상-원본을-저장하지-않고-스냅샷만-남긴다)).
  세그먼트 적재용 `recorder`는 코드가 남아 있으나 공용 서버에서 실행하지 않는다.
  **탐지 결과 자체를 fastapi로 넘기는 경로는 아직 없어 로그로만 나간다** — fastapi는
  스냅샷을 저장소에서 직접 읽는다.

```bash
cd webapps/fastapi
python -m pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --reload --port 8000
```

FastAPI는 외부 의존 없는 local memory mode와 MongoDB metadata mode를 지원한다.
실행 방법, 화면, 환경변수와 API 경계는 [fastapi README](./webapps/fastapi/README.md)가
기준이고, 서비스를 실행하고 검증하는 명령은 [개발 가이드](./docs/guides/README.md)에
모여 있다.

`deeplearning`에는 추론 코드가 없고, 모델 학습용 Jupyter 노트북만 있다
([결정 0012](./docs/architecture/decisions.md#0012--deeplearning에-모델-학습용-jupyter-노트북-도구를-둔다)).
`monitoring`, `RPAs`에는 아직 실행 코드가 없다.
얼굴 탐지·인식, 학생 상태 판정, 실시간 갱신은 구현되지 않았다.
아직 정해지지 않은 항목은 [결정되지 않은 항목](#아직-결정되지-않은-항목)을 참고한다.

## 디렉터리 구조

```text
webapps/       웹 애플리케이션
deeplearning/  모델 추론 (사람 탐지 · 얼굴 탐지 · 얼굴 인식)
worker/        영상 수신 · 프레임 공급 · 추론 실행 · 녹화
monitoring/    Prometheus·Grafana 설정 / 사용자용 실시간 영상
docs/          문서, AI 에이전트 규칙, 프롬프트
RPAs/          업무 자동화 프로젝트
README.md      이 문서
```

최상위에는 위 항목과 `.gitignore`만 둔다. 빌드 설정, 인프라 파일, 에이전트 문서를
최상위에 새로 만들지 않는다. 자세한 예외 범위는
[AGENTS.md의 Repository Structure](./docs/agents/AGENTS.md#repository-structure)에 있다.

### 서비스

| 디렉터리 | 역할 | 상태 |
| --- | --- | --- |
| [webapps/fastapi](./webapps/fastapi/README.md) | FastAPI 웹 애플리케이션. API와 Jinja2 화면을 제공하는 유일한 외부 진입점. 학생 상태 판정을 소유한다. | 세 화면까지 동작 |
| [worker](./worker/README.md) | 영상 파이프라인 워커 묶음(`stream`·`inference`·`recorder`). | 동작 |
| [deeplearning](./deeplearning/README.md) | 사람 탐지, 얼굴 탐지, 얼굴 인식 모델. 모델을 아는 유일한 곳. | 추론 코드 없음. 학습 노트북 있음 |
| [monitoring/internal](./monitoring/internal/README.md) | **내부 모니터링.** 운영자가 서비스 자체를 보는 Prometheus·Grafana 설정. | Grafana 설정만 있음 |
| [monitoring/external](./monitoring/external/README.md) | **외부 모니터링.** 사용자에게 제품으로 제공하는 실시간 영상. | 코드 없음. 경계 미확정 |

`webapps/`는 웹 애플리케이션 전용이다. 웹 요청을 처리하지 않는 서비스는
최상위에 독립 디렉터리로 둔다.

### docs

| 디렉터리 | 역할 |
| --- | --- |
| `docs/agents` | AI 에이전트가 지켜야 할 작업 규칙 |
| `docs/prompts` | 바로 복사해 쓰는 작업 프롬프트 |
| `docs/architecture` | [구조 설명](./docs/architecture/README.md)과 [결정 기록](./docs/architecture/decisions.md) |
| `docs/specs` | 구현 전 합의한 기능 명세 — [학생 모니터링 MVP](./docs/specs/student-monitoring-mvp.md) |
| `docs/conventions` | Git·코딩·API·환경변수·문서 규칙 |
| `docs/guides` | [개발 가이드](./docs/guides/README.md) — 실행·검증 명령과 작업 흐름 |
| `docs/templates` | 복사해 쓰는 문서 템플릿 — [서비스 README](./docs/templates/service-readme-template.md), [기능 명세](./docs/templates/feature-spec-template.md), [API 명세](./docs/templates/api-spec-template.md), [트러블슈팅](./docs/templates/troubleshooting-template.md) |

### RPAs

업무 자동화 프로젝트를 프로젝트별 독립 디렉터리로 관리한다.
공통 규칙은 [RPAs/README.md](./RPAs/README.md)에 있다.

**AI 판정만으로 보호자나 담당자에게 메시지를 보내지 않는다.**
관리자 확인과 승인을 거친 뒤 RPA가 실행한다.

## 팀원이 가장 먼저 읽을 문서

1. 이 문서
2. [학생 모니터링 MVP 명세](./docs/specs/student-monitoring-mvp.md) — 무엇을 만드는가
3. 담당 서비스 디렉터리의 `README.md`
4. [아키텍처](./docs/architecture/README.md) — 서비스 관계와 미결정 항목
5. 개발 규칙 — [Git](./docs/conventions/git-convention.md) · [코딩](./docs/conventions/coding-convention.md) · [API](./docs/conventions/api-convention.md) · [환경변수](./docs/conventions/environment-convention.md) · [문서](./docs/conventions/documentation-convention.md)
6. [개발 가이드](./docs/guides/README.md) — 실행하고 검증하는 명령

## AI 에이전트 사용 방법

이 저장소는 AI 코딩 에이전트와 함께 작업하는 것을 전제로 한다.

- 공통 작업 계약은 [docs/agents/AGENTS.md](./docs/agents/AGENTS.md)에 있다. 에이전트는 이 문서를 먼저 읽는다.
- 역할별 규칙: [FastAPI](./docs/agents/fastapi-agent.md) · [AI](./docs/agents/ai-agent.md) · [RPA](./docs/agents/rpa-agent.md) · [문서](./docs/agents/documentation-agent.md)
- 반복 작업은 Claude Code 스킬을 따른다. `/skills`로 목록을 볼 수 있다.
  스킬은 `.claude/skills/`에 있으며 **저장소에 포함되지 않는다.** 팀원과는 따로 공유한다.
- 작업 지시는 `docs/prompts/`의 템플릿을 복사해 변수를 채운 뒤 사용한다.
  [프로젝트 초기화](./docs/prompts/initialize-project.md) · [기능 구현](./docs/prompts/implement-feature.md) · [버그 조사](./docs/prompts/investigate-bug.md) · [PR 리뷰](./docs/prompts/review-pull-request.md) · [문서 갱신](./docs/prompts/update-project-docs.md)
- 저장소를 직접 읽지 못하는 도구(웹 GPT 등)로 작업할 때는
  [GPT 코딩 프롬프트](./docs/prompts/gpt-agent.md)의 규칙 블록을 대화에 붙여넣는다.

에이전트 관련 문서는 모두 `docs/` 아래에 두며, 최상위에 `AGENTS.md`를 만들지 않는다.

## 새로 추가할 때 위치

| 추가할 것 | 위치 | 참고 |
| --- | --- | --- |
| 새 웹 애플리케이션 | `webapps/<service-name>/` | [서비스 README 템플릿](./docs/templates/service-readme-template.md) |
| 웹이 아닌 새 서비스 | 최상위 `<service-name>/` | 동일. AGENTS.md의 최상위 제약도 갱신한다 |
| fastapi의 새 기능 | `webapps/fastapi/app/<기능>/` | `create-fastapi-feature` 스킬 |
| 새 RPA | `RPAs/<rpa-name>/` | [RPA 규칙](./RPAs/README.md) |
| 구현 전 기능 명세 | `docs/specs/` | [기능 명세 템플릿](./docs/templates/feature-spec-template.md) |
| 아키텍처 결정 | `docs/architecture/decisions.md` | [기록 방법](./docs/architecture/decisions.md#어떻게-기록하는가). 결정마다 파일을 만들지 않는다 |
| 개발 규칙 | `docs/conventions/` | 기존 문서 수정을 우선 |
| 작업 절차 | `.claude/skills/<skill-name>/` | 저장소 밖. 작성법은 `.claude/skills/README.md` |
| 문서 템플릿 | `docs/templates/` | |

## 기여 흐름

1. 작업 대상 디렉터리의 README와 관련 규칙 문서를 읽는다.
2. `develop`에서 브랜치를 만들어 작업한다.
3. 변경 범위를 작게 유지한다.
4. 가능한 검증을 실행하고, 실행하지 못한 검증은 그대로 밝힌다.
5. 문서 갱신이 필요한지 확인한다.
6. `develop`으로 Pull Request를 열어 리뷰를 요청한다.

비밀키와 실제 환경변수 값은 어떤 경우에도 커밋하지 않는다.
**학생 얼굴이 담긴 영상·이미지·캡처도 커밋하지 않는다.**
상세 규칙은 [Git 규칙](./docs/conventions/git-convention.md)에 있다.

## 아직 결정되지 않은 항목

아래는 확정되지 않았다. 임의로 구현하지 말고 결정 후 [결정 기록](./docs/architecture/decisions.md)에 남긴다.
전체 목록과 각 항목의 영향 범위는
[아키텍처의 미결정 항목](./docs/architecture/README.md#미결정-항목)이 정본이다.

- **얼굴 데이터 정책** — 동의 절차, 원본 보관 여부, 보존 기간, 접근 권한, 삭제 절차
- **스냅샷 접근 권한** — 개인정보가 걸린 합의 사항. 지금은 root 키로 붙는다
- **운영 접근 통제 방식** — 정해지기 전까지 `APP_ENV=prod` 배포를 하지 않는다
- 탐지 결과를 fastapi로 넘기는 방식(동기 HTTP / 메시지 큐)
- 얼굴 탐지·인식 모델과 사람 탐지 모델 버전
- 결석 유예 시간 값과 좌석 판정 방식 — 실제 촬영이 선행되어야 한다
- 카메라 배치(대수·높이·화각·거리)
- 수업 시간표의 원본 관리 주체
- 실시간 화면 갱신 방식과 브라우저 영상 재생 방식
- Tracking 도입과 `IN_CLASSROOM` 지원
- 자연어 검색 방식, 캐시·큐 도입 여부, 알림 채널
- 통합 실행 수단(docker compose) 공식화, 배포 환경과 방식

확정된 결정은 [결정 기록](./docs/architecture/decisions.md)에 있다.
fastapi 내부 구조, 설계 패턴 판단 기준, 메타데이터 저장소(MongoDB),
영상·얼굴 이미지 저장소(MinIO), worker 분리와 프레임 전달, 상태 판정 소유 서비스,
추론 책임 경계, MVP 제품 사용자 범위가 여기에 해당한다.
