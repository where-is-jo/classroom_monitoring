# recorder worker

원본 영상을 세그먼트 단위로 MinIO에 적재하는 워커다.

> 현재 상태: 구현 전.
> **저장 범위·보존 기간·접근 권한이 합의되기 전까지 이 워커를 만들지 않는다.**

## 만들기 전에 합의해야 할 것

영상에는 사무실 구성원의 얼굴이 담긴다. 저장 범위를 정하지 않은 채 기능을 먼저
만들면 지우기 어려운 데이터가 쌓인다. 아래는 기술 결정이 아니라 **합의 사항**이며,
[결정 0004](../../docs/architecture/decisions.md#0004--영상스냅샷-저장소로-minio-채택)에서
그대로 이어진다.

| 항목 | 상태 |
| --- | --- |
| 상시 녹화 여부(전체 저장 / 이벤트 구간만 / 스냅샷만) | `결정 필요` |
| 보존 기간과 자동 삭제 | `결정 필요` |
| 접근 권한 — 누가 영상을 열람할 수 있는가 | `결정 필요` |
| 개인정보 처리 근거와 고지 방법 | `결정 필요` |

합의 전까지 필요한 개발용 로컬 저장은 [`stream`](../stream/README.md)에 있고
기본값이 꺼져 있다. 그 기능은 운영 보관 수단이 아니다.

## 서비스 목적

영상 바이트를 메타데이터와 분리해 보관한다. 보존 기간·용량·접근 권한이
메타데이터와 완전히 달라 한 저장소에 넣으면 이들을 따로 정할 수 없다.

## 책임

- MediaMTX에서 영상을 직접 받아 세그먼트로 나눈다
- 세그먼트를 MinIO 버킷에 적재한다
- 적재 결과의 참조(버킷·객체 키)를 `fastapi`에 넘긴다
- 보존 기간에 따른 삭제 (정책 확정 후)

**`stream` worker의 프레임을 받지 않는다.** MediaMTX에서 직접 받는 이유는
저장 때문에 추론 경로가 느려지지 않게 하기 위해서다.

## 포함하지 않아야 할 기능

- 메타데이터 저장 → `fastapi`가 MongoDB에 기록한다. 여기서는 참조만 넘긴다
- 영상 조회 API와 접근 권한 판정 → `fastapi` 책임
- 프레임 샘플링·추론 → [`stream`](../stream/README.md), [`inference`](../inference/README.md) 책임

## 예상 기술

| 항목 | 상태 | 비고 |
| --- | --- | --- |
| 언어 | Python | |
| 세그먼트 분할 | 후보: FFmpeg | |
| 객체 저장소 | MinIO | [결정 0004](../../docs/architecture/decisions.md#0004--영상스냅샷-저장소로-minio-채택). S3 호환 범위에서만 쓴다 |
| 버킷 구성과 객체 키 규칙 | `결정 필요` | |

## 환경변수

> 구현 시 `.env.example`과 함께 채운다. MinIO 접속 정보는 비밀값이다.

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `OBJECT_STORAGE_ENDPOINT` | MinIO 주소 | 기본값을 주지 않는다 |
| `OBJECT_STORAGE_ACCESS_KEY` | 접근 키 | 비밀값. 커밋 금지 |
| `OBJECT_STORAGE_SECRET_KEY` | 비밀 키 | 비밀값. 커밋 금지 |
| `OBJECT_STORAGE_BUCKET` | 영상 버킷 이름 | |
| `RECORDING_SEGMENT_SECONDS` | 세그먼트 길이 | |
| `RECORDING_RETENTION_DAYS` | 보존 기간 | 정책 확정 후 |

## 관련 문서

- [worker 개요](../README.md)
- [결정 0004 · 영상·스냅샷 저장소로 MinIO 채택](../../docs/architecture/decisions.md#0004--영상스냅샷-저장소로-minio-채택)
- [아키텍처](../../docs/architecture/README.md)
