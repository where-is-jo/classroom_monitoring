# 로컬 개발

**목적**: 개발자 PC에서 작업할 때의 공통 규칙을 정한다.
**대상 독자**: 서비스를 구현하거나 수정하는 팀원.

> **실행 명령은 이 문서에 없다.** 서비스마다 다르기 때문이다.
> 각 서비스의 실행 방법은 구현한 사람이 해당 서비스 README에 기록한다.
> 이 문서는 공통으로 지킬 것만 다룬다.
>
> 지금 실행 가능한 것은 [`webapps/fastapi`](../../webapps/fastapi/README.md) 하나다.

## 실행 방법은 서비스 README에 있다

서비스마다 언어와 실행 방식이 다르므로, 실행 절차는 각 서비스가 소유한다.

| 서비스 | 실행 방법 위치 |
| --- | --- |
| fastapi | [webapps/fastapi/README.md](../../webapps/fastapi/README.md) |
| deeplearning | [deeplearning/README.md](../../deeplearning/README.md) |
| worker | [worker/README.md](../../worker/README.md) |
| monitoring | [monitoring/README.md](../../monitoring/README.md) |
| 각 RPA | `RPAs/<rpa-name>/README.md` |

여러 서비스를 한 번에 띄우는 통합 실행 수단(`docker-compose` 등)은 아직 없다.
도입 여부와 위치는 **결정 필요** 항목이다. 최상위에 파일을 두지 않는 제약을 함께 고려한다.

## 서비스 README에 실행 방법을 적을 때

구현을 마친 사람은 다음을 README에 남긴다.

1. **필요한 런타임과 버전** — "Python 3.12" 처럼 구체적으로
2. **의존성 설치 명령**
3. **`.env` 준비 방법** — `.env.example`을 복사해 값을 채운다
4. **실행 명령**
5. **동작 확인 방법** — 어디에 접속하면 무엇이 보이는지
6. **자주 겪는 문제** — 실제로 겪은 것만

**직접 실행해 확인한 명령만 적는다.** 될 것 같은 명령을 적지 않는다.

## 환경변수 준비

```bash
cp .env.example .env
```

- `.env`는 커밋하지 않는다. `.env.example`만 커밋한다.
- 비밀값은 팀에서 정한 방법으로 받는다. 채팅이나 문서에 붙여넣지 않는다.
- 새 환경변수를 추가하면 **같은 커밋에서** `.env.example`을 갱신한다.
  이걸 빠뜨리면 다른 사람의 실행이 깨진다.

자세한 규칙은 [환경변수 규칙](../conventions/environment-convention.md)에 있다.

## 다른 서비스가 필요할 때

작업 중인 서비스가 아직 없는 다른 서비스를 필요로 하는 경우가 많다.

- **없는 서비스를 흉내 내는 코드를 제품 코드에 넣지 않는다.**
  대역이 필요하면 테스트 코드나 로컬 설정으로 분리한다.
- 상대 서비스의 응답 형식을 추측해 굳히지 않는다.
  계약이 없으면 먼저 [API 규칙](../conventions/api-convention.md)에 따라 정의한다.
- 각 서비스는 다른 서비스 없이도 기동되어야 한다.
  의존 서비스가 없을 때 죽지 말고, 해당 기능만 실패로 처리한다.

## 영상과 개인정보

- **실제 사무실 영상을 개발용으로 로컬에 두지 않는다.**
- 테스트에는 공개 샘플이나 직접 촬영한 무인 영상을 쓴다.
- 화면 캡처를 문서·이슈에 올릴 때 사람이 찍히지 않았는지 확인한다.
- 운영 데이터를 로컬로 복사하지 않는다.

## 작업 전 확인

- [ ] `main`이 최신인지 확인하고 브랜치를 땄다
- [ ] 작업 대상 서비스의 README와 관련 [Skill](../skills/README.md)을 확인했다
- [ ] `.env`를 준비했고 커밋 대상에 들어가지 않는다
- [ ] 변경 범위가 하나의 목적에 한정된다

## 관련 문서

- [시작하기](./getting-started.md)
- [테스트](./testing.md)
- [Git 규칙](../conventions/git-convention.md)
- [환경변수 규칙](../conventions/environment-convention.md)
