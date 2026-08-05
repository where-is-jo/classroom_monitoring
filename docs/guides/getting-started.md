# 시작하기

**목적**: 이 저장소에 처음 온 사람이 무엇을 읽고 어디서부터 손대야 할지 안내한다.
**대상 독자**: 새로 합류한 팀원.

## 지금 저장소에 있는 것

**서비스 코드는 아직 없다.** 지금 있는 것은 디렉터리 구조와 문서 체계다.

그래서 이 가이드는 "환경을 설치하고 서버를 띄우는" 안내가 아니다.
어디에 무엇을 만들어야 하는지, 만들 때 무엇을 지켜야 하는지를 찾는 방법을 안내한다.

## 읽는 순서

1. **[루트 README](../../README.md)** — 저장소 전체 구조와 아직 결정되지 않은 항목
2. **[아키텍처 개요](../architecture/overview.md)** — 서비스가 어떻게 나뉘고 왜 그렇게 나뉘었는지
3. **담당 영역의 서비스 README** — [frontend](../../webapps/frontend/README.md) · [backend](../../webapps/backend/README.md) · [inference](../../webapps/inference/README.md) · [stream-server](../../webapps/stream-server/README.md) · [monitoring](../../webapps/monitoring/README.md) · [RPAs](../../RPAs/README.md)
4. **[개발 규칙](../conventions/)** — 전부 읽을 필요는 없다. 아래 순서로 필요할 때 본다

| 상황 | 문서 |
| --- | --- |
| 커밋·브랜치를 만들 때 | [Git 규칙](../conventions/git-convention.md) |
| 코드를 쓸 때 | [코딩 규칙](../conventions/coding-convention.md) |
| API를 만들거나 쓸 때 | [API 규칙](../conventions/api-convention.md) |
| 설정값을 다룰 때 | [환경변수 규칙](../conventions/environment-convention.md) |
| 문서를 쓸 때 | [문서 작성 규칙](../conventions/documentation-convention.md) |

## AI 에이전트와 함께 작업한다면

이 저장소는 AI 코딩 에이전트 사용을 전제로 만들어졌다.

1. 에이전트에게 [`docs/agents/AGENTS.md`](../agents/AGENTS.md)를 먼저 읽히면 공통 규칙이 적용된다.
2. 작업 종류에 맞는 [프롬프트 템플릿](../prompts/)을 복사해 변수를 채워 지시한다.
3. 반복 작업이라면 해당 [Skill](../skills/README.md)의 절차를 따르게 한다.

사람이 직접 작업할 때도 Skill의 절차와 체크리스트를 그대로 쓸 수 있다.

## 첫 작업 흐름

1. 무엇을 만들지 정하고, 해당 서비스 README에서 그것이 그 서비스의 책임인지 확인한다.
2. `main`에서 브랜치를 딴다.
3. 작업 종류에 맞는 [Skill](../skills/README.md)을 연다.
4. 작업 범위를 작게 유지한다. 요청받지 않은 정리는 하지 않는다.
5. 검증을 실행하고, 실행하지 못한 것은 그대로 밝힌다.
6. 문서 갱신이 필요한지 확인한다.
7. Pull Request를 연다.

## 자주 하는 질문

**새 웹 서비스를 어디에 만드나?**
`webapps/<service-name>/`. [프로젝트 초기화 프롬프트](../prompts/initialize-project.md)를 사용한다.

**새 RPA는?**
`RPAs/<rpa-name>/`. [RPA 규칙](../../RPAs/README.md)을 먼저 읽는다.

**기술 스택이 왜 안 정해져 있나?**
실제 요구와 장비 사양을 확인한 뒤 정하기로 했다.
[미결정 항목](../architecture/overview.md#미결정-항목)에 목록이 있다.
확정하면 [ADR](../architecture/decisions/README.md)로 남긴다.

**문서와 코드가 다르면?**
코드가 사실이다. 임의로 맞추지 말고 불일치를 보고한다.

**실행 방법이 문서에 없다.**
아직 구현이 없어서다. 지어내지 말고, 구현한 사람이 해당 서비스 README에 기록한다.

## 관련 문서

- [로컬 개발](./local-development.md)
- [테스트](./testing.md)
- [배포](./deployment.md)
