# monitoring/external — 외부 모니터링

**사용자에게 제품으로 제공하는 실시간 영상 모니터링**을 다루는 디렉터리다.
관리자가 강의실 영상을 직접 보는 쪽이고, 보는 사람은 팀이 아니라 서비스 사용자다.

운영자가 서비스의 상태·성능을 보는 관측 설정은 이 디렉터리가 아니라
[`monitoring/internal`](../internal/README.md)이다. 둘은 이름만 같고 목적·대상·수요자가 다르다.

| | internal | external |
| --- | --- | --- |
| 보는 사람 | 팀·운영자 | 서비스 사용자(관리자) |
| 보는 대상 | 서비스 자체(지표·로그) | 강의실 영상 |
| 성격 | 운영 도구 | 제품 기능 |

> 현재 상태: **코드 없음. 이 README뿐이다.**
> 실시간 영상 경로는 아직 어느 서비스에도 구현되어 있지 않다.
> 아래 내용은 이 디렉터리가 무엇을 맡을지에 대한 것이며, 확정된 설계가 아니다.

## 지금 실제로 있는 것

**이 디렉터리가 담당할 일의 상당 부분은 이미 다른 곳에 있거나, 아직 어디에도 없다.**
새로 만들기 전에 아래를 먼저 봐야 한다.

| 조각 | 위치 | 상태 |
| --- | --- | --- |
| 모니터링 화면과 스트림 목록 API | `webapps/fastapi`의 `app/video_monitoring/` (`/monitoring`, `/api/v1/video-streams`) | **합성 데모까지만.** 고정 카탈로그를 반환한다. 실제 스트림이 아니다 |
| USB 카메라 → RTSP 송출 | [`worker`](../../worker/README.md) | 단일 카메라 기준 동작. 다른 서비스와 연결되어 있지 않다 |
| RTSP 수신·재배포(HLS/WebRTC) | 로컬 docker 스택의 MediaMTX | 컨테이너는 뜨지만 **등록된 스트림 경로도 인증도 없다** |
| 카메라 영상과 학생 상태의 연결 | 없음 | 미구현 |

즉 **카메라에서 브라우저까지 이어지는 경로가 아직 한 번도 연결된 적이 없다.**

## 결정되지 않은 것

미결정 항목의 정본은 [`docs/architecture/README.md`](../../docs/architecture/README.md)의 표다.
아래는 그중 이 디렉터리에 직접 걸리는 것들이다. **여기서 확정하지 않는다.**

| 항목 | 상태 |
| --- | --- |
| 브라우저 영상 재생 방식(WebRTC 중계 / HLS) | `결정 필요` |
| 실시간 화면 갱신 방식(폴링 / SSE / WebSocket) | `결정 필요` |
| 영상 저장 범위·보존 기간·접근 권한 | `결정 필요` (개인정보 관련 합의 사항) |
| 운영 접근 통제 방식 | `결정 필요`. **정해지기 전까지 실시간 영상을 운영에 노출하지 않는다** |
| worker의 영상 수신 프로토콜과 MediaMTX 경로·인증 | `결정 필요` |

**WebRTC는 현재 후보이지 확정이 아니다.** 팀이 WebRTC로 확정하면
[`docs/architecture/decisions.md`](../../docs/architecture/decisions.md)에 항목을 추가하고
위 표의 상태를 함께 갱신해야 한다. 그 전까지 이 문서는 WebRTC를 전제로 쓰지 않는다.

## 먼저 풀어야 할 경계 문제

이 디렉터리를 만들면서 기존 규칙과 부딪히는 지점이 생겼다. **코드를 넣기 전에 정해야 한다.**

### 1. 브라우저가 무엇을 직접 호출하는가

`docs/agents/AGENTS.md`의 아키텍처 규칙은 **"브라우저는 `fastapi`만 호출한다"** 이다.
그런데 WebRTC든 HLS든 실시간 영상은 보통 브라우저가 미디어 서버(MediaMTX)에
직접 붙는 형태가 된다. 영상 바이트를 fastapi로 전부 중계하면 규칙은 지켜지지만
fastapi가 대역폭 병목이 된다.

선택지는 세 가지다. **어느 쪽이든 규칙 문서를 함께 고쳐야 한다.**

- fastapi가 중계한다 — 규칙 유지, 성능 부담
- 미디어 서버에 직접 붙되 **접근 토큰은 fastapi가 발급한다** — 규칙에 예외를 명시해야 함
- reverse proxy(Caddy) 뒤에 두고 하나의 origin으로 노출 — 경로 설계 필요

### 2. 이 디렉터리에 코드가 들어가도 되는가

최상위 구조 제약은 `monitoring/`을 설정 디렉터리로 규정해 왔고,
`monitoring/internal`은 "애플리케이션 비즈니스 로직을 포함하지 않는다"를 명시한다.
external이 제품 기능이라면 성격이 다르다. 두 가지 중 하나를 골라야 한다.

- **설정·문서만 둔다** — 미디어 서버 설정(`mediamtx.yml` 등)과 경계 문서만 여기 두고,
  화면·API는 계속 `webapps/fastapi`의 `video_monitoring` 기능이 담당한다
- **서비스 코드를 둔다** — 그러면 `monitoring/external`은 웹 요청을 처리하지 않는
  독립 서비스가 되므로, 최상위 구조 제약과 `docs/architecture/README.md`의
  블록·디렉터리 대응표를 함께 갱신해야 한다

**아직 고르지 않았다.** 고르기 전에는 여기에 코드를 넣지 않는다.

## 포함하지 않아야 할 기능

- 지표 수집·대시보드·알림 규칙 — [`monitoring/internal`](../internal/README.md)이 맡는다
- 사람 탐지·얼굴 탐지·얼굴 인식 — `deeplearning`이 맡는다
- 탐지 결과의 비즈니스 해석(학생 상태 판정) — `fastapi`가 맡는다
- 실제 접속 자격 증명 — [환경변수 규칙](../../docs/conventions/environment-convention.md)을 따른다

## 다른 서비스와의 관계

- [`worker`](../../worker/README.md): 카메라 영상을 받아 스트림으로 내보내는 주체다.
  external은 그 스트림을 사용자에게 닿게 하는 쪽을 다룬다.
- `webapps/fastapi`: 화면과 접근 통제를 담당한다. **누가 어떤 카메라를 볼 수 있는지는
  fastapi가 판단한다.** 영상 저장 범위·접근 권한이 합의되기 전에는 이 판단 기준도 확정할 수 없다.
  현재 fastapi에는 인증이 없다.
- [`monitoring/internal`](../internal/README.md): 스트림이 살아 있는지를 지표로
  볼지는 `결정 필요`. 본다면 지표를 노출하는 쪽은 external이고 수집하는 쪽은 internal이다.

## 테스트 전략

**구현이 없어 실행 가능한 검증 명령이 없다.** 구현이 생긴 시점에 실제로 실행한 명령만
이 문서에 기록한다.

## 관련 문서

- [아키텍처](../../docs/architecture/README.md) — 미결정 항목의 정본
- [확정된 결정](../../docs/architecture/decisions.md)
- [worker README](../../worker/README.md)
- [fastapi README](../../webapps/fastapi/README.md)
