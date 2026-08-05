# Skills

반복 작업의 **실행 절차**를 모아둔 디렉터리다.
사람과 AI 에이전트가 같은 순서로 작업하게 만드는 것이 목적이다.

이 문서는 두 가지를 담는다.

1. 현재 있는 Skill 목록
2. 새 Skill을 추가할 때 따를 작성 방법

## Skill 목록

| Skill | 언제 쓰는가 |
| --- | --- |
| [create-fastapi-feature](./create-fastapi-feature/SKILL.md) | API 엔드포인트나 화면을 추가할 때 |
| [create-rpa-workflow](./create-rpa-workflow/SKILL.md) | 새 업무 자동화를 만들 때 |
| [add-monitoring-metric](./add-monitoring-metric/SKILL.md) | Prometheus 지표를 추가할 때 |
| [review-code](./review-code/SKILL.md) | 변경된 코드를 검토할 때 |
| [update-documentation](./update-documentation/SKILL.md) | 문서를 실제 코드와 맞출 때 |

---

# 새 Skill 추가하기

## Skill과 다른 문서의 차이

같은 내용을 어디에 쓸지 헷갈리면 아래를 기준으로 판단한다.

| 문서 | 답하는 질문 | 예 |
| --- | --- | --- |
| [Agent](../agents/) | 이 역할은 무엇을 지켜야 하는가 | "라우터에 비즈니스 로직을 두지 않는다" |
| **Skill** | **이 작업을 어떤 순서로 하는가** | "계약 정의 → 스키마 → 서비스 → 라우터 → 테스트" |
| [Convention](../conventions/) | 결과물이 어떤 형식이어야 하는가 | "오류 응답은 이 형식을 따른다" |
| [Prompt](../prompts/) | 에이전트에게 어떻게 지시하는가 | 복사해서 쓰는 지시문 |
| [Guide](../guides/) | 처음 접하는 사람이 어떻게 시작하는가 | 로컬 개발 환경 준비 |

Skill은 **순서**가 핵심이다. 순서가 없고 규칙만 있다면 Convention이나 Agent 문서에 속한다.

## Skill을 만들 기준

다음을 모두 만족할 때 만든다.

- 앞으로 **두 번 이상** 반복될 작업이다
- 순서를 틀리면 손해가 생긴다(되돌리기 비용, 놓치는 단계)
- 사람마다 다르게 하고 있어서 결과가 갈린다

다음이면 만들지 않는다.

- 한 번만 할 작업
- 도구가 알아서 해주는 작업
- "잘 만들기", "깨끗하게 짜기"처럼 절차로 쓸 수 없는 것
- 이미 있는 Skill의 한 단계에 해당하는 것 → 기존 Skill을 보강한다

## 위치와 이름

```text
docs/skills/<skill-name>/SKILL.md
```

- 디렉터리 이름은 **동사로 시작**하고 소문자·하이픈을 쓴다: `create-`, `add-`, `review-`, `update-`, `investigate-`
- 파일 이름은 항상 `SKILL.md`다
- Skill 하나는 디렉터리 하나를 가진다. 예시 파일이나 체크리스트가 필요하면 같은 디렉터리에 둔다

## 작성 구조

아래 8개 절을 순서대로 사용한다. 빈 절을 남기지 않는다.

```markdown
# Skill Name

## Purpose

## When to Use

## Required Inputs

## Preconditions

## Procedure

## Validation

## Expected Output

## Failure Handling

## Completion Checklist
```

### 각 절에 쓸 내용

**Purpose** — 이 절차가 막으려는 실패가 무엇인지 한두 문장으로.
"API를 만든다"가 아니라 "계약을 먼저 정하지 않아 나중에 클라이언트를 깨는 일을 막는다"처럼 쓴다.

**When to Use** — 쓰는 경우와 **쓰지 않는 경우**를 함께 적는다.
쓰지 않는 경우가 없으면 그 Skill은 범위가 너무 넓다.

**Required Inputs** — 시작 전에 손에 있어야 할 정보를 표로. 값이 아니라 항목 이름을 적는다.
"이 중 비어 있으면 시작하지 않는다"를 명시한다.

**Preconditions** — 읽어야 할 문서, 조사해야 할 기존 구조를 체크 항목으로.

**Procedure** — 번호가 붙은 실행 단계. 이 Skill의 본체다.
- 각 단계는 **결과물이나 확정 사항**을 남겨야 한다
- 순서에 이유가 있어야 한다(왜 이 단계가 먼저인가)
- "잘 확인한다" 같은 문장을 쓰지 않는다. 무엇을 확인하는지 적는다

**Validation** — 실제로 실행해서 확인하는 방법.
확인할 수 없는 항목이 있으면 "확인하지 못한 것을 그대로 보고한다"를 적는다.
없는 명령을 지어내지 않는다.

**Expected Output** — 이 작업이 끝났을 때 저장소에 남는 것.
마지막에 [완료 보고](../agents/AGENTS.md#completion-report) 링크를 둔다.

**Failure Handling** — 자주 막히는 상황과 대응을 표로.
특히 **멈추고 물어봐야 하는 상황**을 명시한다. 이 절이 Skill의 실질적 가치인 경우가 많다.

**Completion Checklist** — 체크박스 목록. Procedure를 요약하지 말고,
**빠뜨리기 쉬운 것**을 고른다. 20개를 넘기지 않는다.

## 작성 규칙

- **한국어로 쓴다.**
- **기존 문서와 중복하지 않는다.** 규칙은 Convention·Agent 문서에 링크하고, 여기서는 순서만 쓴다.
- **검증하지 않은 실행 명령을 쓰지 않는다.** 서비스 구현이 없으면 "구현 후 해당 README에 기록한다"고 적는다.
- **미확정 사항을 확정처럼 쓰지 않는다.** `결정 필요`로 표시한다.
- **링크는 저장소 기준 상대 경로**로 쓴다.
- **일반론을 쓰지 않는다.** "좋은 코드를 작성한다" 같은 문장은 지운다.
- 분량보다 **실행 가능성**이 기준이다. 짧아도 그대로 따라 할 수 있으면 충분하다.

## 추가 절차

1. 위 [기준](#skill을-만들-기준)에 맞는지 확인한다. 애매하면 기존 Skill 보강을 먼저 검토한다.
2. `docs/skills/<skill-name>/SKILL.md`를 만들고 8개 절을 채운다.
3. **이 문서의 [Skill 목록](#skill-목록) 표에 한 줄 추가한다.**
4. 관련 [Agent 문서](../agents/)의 Required Context에 이 Skill 링크를 추가한다.
5. 관련 서비스 README에서 참조할 필요가 있으면 링크를 건다.
6. 상대 링크의 대상 파일이 존재하는지 확인한다.

## 유지 관리

- Skill대로 했는데 막혔다면 Skill을 고친다. 개인의 우회 방법으로 남기지 않는다.
- 실제로 쓰이지 않는 Skill은 지운다. 따르지 않는 절차가 남아 있으면 다른 문서의 신뢰도까지 떨어진다.
- 절차가 바뀌면 Failure Handling도 함께 본다. 대부분의 변경은 여기서 시작된다.
