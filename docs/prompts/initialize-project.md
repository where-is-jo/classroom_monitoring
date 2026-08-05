# 프로젝트 초기화 프롬프트

새 웹 서비스 또는 RPA 프로젝트를 저장소에 추가할 때 사용한다.

## 사용법

1. 아래 변수 값을 정한다. 비어 있는 항목이 있으면 먼저 확정한다.
2. `## 프롬프트` 블록을 복사해 변수를 치환한 뒤 에이전트에게 전달한다.

관련 문서: [AGENTS.md](../agents/AGENTS.md) · [Skill 목록](../skills/README.md) · [RPA 규칙](../../RPAs/README.md)

## 변수

| 변수 | 설명 | 예 |
| --- | --- | --- |
| `{{PROJECT_TYPE}}` | `webapp` 또는 `rpa` | `webapp` |
| `{{PROJECT_NAME}}` | 디렉터리 이름(소문자·하이픈) | `alert-dispatcher` |
| `{{PURPOSE}}` | 이 프로젝트가 해결할 문제 | `탐지 이벤트를 담당자에게 알림` |
| `{{RESPONSIBILITIES}}` | 담당할 책임 목록 | |
| `{{OUT_OF_SCOPE}}` | 담당하지 않을 것 | |
| `{{TECH_CANDIDATES}}` | 기술 후보와 확정 여부 | `Python / FastAPI (확정), 큐 (결정 필요)` |
| `{{RELATED_SERVICES}}` | 연동할 서비스 | `fastapi, monitoring` |

## 프롬프트

```text
스마트 오피스 모니터링 저장소에 새 프로젝트를 초기화한다.

## 작업 목적
{{PURPOSE}}
이번 작업의 산출물은 동작하는 기능이 아니라, 이후 구현을 시작할 수 있는
디렉터리와 문서다. 기능 구현을 앞서 진행하지 않는다.

## 입력 정보
- 프로젝트 종류: {{PROJECT_TYPE}}
- 프로젝트 이름: {{PROJECT_NAME}}
- 책임: {{RESPONSIBILITIES}}
- 담당하지 않을 것: {{OUT_OF_SCOPE}}
- 기술 후보: {{TECH_CANDIDATES}}
- 연동 대상: {{RELATED_SERVICES}}

## 참고할 Agent
- `docs/agents/AGENTS.md` (공통 계약, 반드시 먼저 읽는다)
- webapp이면 해당 영역의 Agent 문서, rpa이면 `docs/agents/rpa-agent.md`
- `docs/agents/documentation-agent.md` (README 작성 기준)

## 참고할 Skill
- rpa인 경우: `docs/skills/create-rpa-workflow/SKILL.md`
- 새 Skill이 필요하다고 판단되면: `docs/skills/README.md`

## 작업 절차
1. 루트 `README.md`와 최상위 구조 제약을 확인한다.
2. 생성 위치를 정한다.
   - webapp: `webapps/{{PROJECT_NAME}}/`
   - rpa: `RPAs/{{PROJECT_NAME}}/`
3. 유사한 기존 프로젝트의 구조와 README 형식을 조사해 맞춘다.
4. 프로젝트 디렉터리와 README를 먼저 만든다. README에는 다음을 포함한다.
   - 목적, 책임, 담당하지 않는 것
   - 예상 기술과 확정 여부
   - 다른 서비스와의 관계
   - 필요한 환경변수 이름과 용도(값은 제외)
   - 테스트 전략
   - 관련 문서 링크
5. 기술 선택 중 되돌리기 비용이 큰 것은 `docs/architecture/decisions/`에 ADR로 남긴다.
6. 최소한의 디렉터리 골격만 만든다. 실행 코드는 이번 범위가 아니다.
7. 루트 `README.md`에 새 프로젝트를 반영해야 하는지 확인한다.

## 금지 사항
- 최상위에 새 파일·디렉터리를 만들지 않는다. 허용된 최상위 항목은
  `webapps/`, `docs/`, `RPAs/`, `README.md`뿐이다.
- 기존 최상위 구조와 다른 프로젝트의 파일을 수정하지 않는다.
- 동작하지 않는 샘플 코드와 예제 엔드포인트를 만들지 않는다.
- 사용할지 확정되지 않은 의존성을 추가하지 않는다.
- 확정되지 않은 기술을 확정된 것처럼 문서에 쓰지 않는다. `결정 필요`로 표시한다.
- 실행해 보지 않은 명령을 README에 적지 않는다.
- 비밀값과 실제 환경변수 값을 저장소에 넣지 않는다.

## 검증 방법
- 최상위 항목이 4개 그대로인지 확인한다.
- 생성한 문서의 모든 상대 링크 대상이 실제로 존재하는지 확인한다.
- README에 적은 내용 중 확인하지 못한 항목이 없는지 점검한다.
- 실행 가능한 검증이 없다면 없다고 밝힌다. 지어내지 않는다.

## 완료 보고 형식
## 작업 요약
## 생성 또는 변경한 파일
## 주요 설계 판단
## 실행한 검증
## 실행하지 못한 검증
## 남은 위험 또는 후속 작업
```
