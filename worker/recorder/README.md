# recorder worker

원본 영상을 세그먼트 단위로 나눠 객체 저장소에 적재하고, 보존 기간이 지나면 지운다.

## 저장 정책이 아직 합의되지 않았다

**이 워커는 팀 합의 전에 만들어졌다.** [결정 0004](../../docs/architecture/decisions.md#0004--영상스냅샷-저장소로-minio-채택)가
"저장 범위·보존 기간·접근 권한이 정해지기 전까지 상시 저장 기능을 만들지 않는다"고
정했고, 그 항목들은 지금도 `결정 필요`다. 코드가 먼저 생긴 경위는
[결정 0009](../../docs/architecture/decisions.md#0009--recorder-worker의-저장-구조와-보존-정책)에 있다.

| 항목 | 상태 | 코드에서 |
| --- | --- | --- |
| 상시 녹화 여부 | `결정 필요` | 상시 세그먼트 녹화로 구현 |
| 보존 기간과 자동 삭제 | `결정 필요` | `RECORDING_RETENTION_DAYS` 기본 30일 |
| 접근 권한 | `결정 필요` | 미구현. 조회 경로가 아직 없다 |
| 개인정보 처리 근거와 고지 | `결정 필요` | 미구현. 코드로 풀 문제가 아니다 |

**기본값 30일은 팀이 합의한 값이 아니다.** 워커는 시작할 때 현재 보존 기간과
저장소를 경고 로그로 남겨, 합의 없이 운영에 쓰이는 것이 눈에 띄게 한다.
합의되면 이 표와 `.env.example`의 주석을 함께 갱신한다.

## 어떻게 동작하는가

```text
MediaMTX (RTSP)
     │
     ▼  FFmpeg -c copy -f segment   (디코딩하지 않는다)
로컬 세그먼트 파일
     │
     ▼  완성된 것만
Uploader ──ObjectStorage 포트──▶ local | minio
     │
     ▼  적재 성공 시
로컬 파일 삭제
                          RetentionPolicy ──▶ 보존 기간 지난 객체 삭제
```

**`stream` worker의 프레임을 받지 않는다.** MediaMTX에서 직접 받는 이유는 저장
때문에 추론 경로가 느려지지 않게 하기 위해서다. 그래서 프레임을 디코딩하지 않고
`-c copy`로 받은 그대로 파일에 쓴다. CPU를 쓰지 않고 원본 화질도 유지된다.

**쓰는 중인 세그먼트를 올리지 않는다.** FFmpeg은 가장 최근 파일에 계속 쓰고 있고
mp4는 moov atom을 마지막에 붙인다. 그 파일을 올리면 재생할 수 없는 객체가 남는다.
가장 최근 파일은 제외하되, 그 파일이 `RECORDING_STALE_AFTER_SECONDS` 동안 변하지
않으면 FFmpeg이 죽은 것으로 보고 올린다. 그러지 않으면 마지막 녹화분이 영원히
올라가지 않는다.

**적재에 실패하면 로컬 파일을 남긴다.** 지워버리면 재시도할 수 없어 그 시간대
영상이 사라진다. 같은 객체 키로 덮어쓰므로 두 번 올라가도 중복되지 않는다.

## 구성

| 파일 | 역할 |
| --- | --- |
| `config.py` | 환경변수 읽기와 시작 시 검증 |
| `errors.py` | `SegmentationError`, `ObjectStorageError` 등 도메인 예외 |
| `ports.py` | `ObjectStorage` 포트 |
| `object_keys.py` | 객체 키 규칙 |
| `adapters/local.py` | 로컬 디렉터리 어댑터 (개발용) |
| `adapters/minio_storage.py` | MinIO 어댑터. SDK를 아는 유일한 파일 |
| `segmenter.py` | FFmpeg으로 RTSP를 세그먼트 파일로 |
| `uploader.py` | 완성된 세그먼트 판별과 적재 |
| `retention.py` | 보존 기간이 지난 객체 삭제 |
| `worker.py` | 카메라별 녹화와 적재를 스레드로 관리 |
| `main.py` | 진입점 |

카메라 대수만큼 프로세스를 띄우지 않는다. `stream` worker와 같은 구조다.

**어댑터는 SDK가 어떤 예외를 던지든 `ObjectStorageError`로 바꾼다.** 접속이 끊겼을 때
`urllib3`의 예외가 그대로 새어 나가면 적재 루프가 잡지 못해 카메라 스레드가 조용히
죽는다. 그 예외는 `OSError`가 아니라 따로 잡아야 하며, 실제 MinIO를 내린 상태로
확인한 사실이다.

## 객체 키 규칙

```text
<카메라 식별자>/<YYYY-MM-DD>/<YYYYMMDDTHHMMSSZ>.mp4
예) camera-01/2026-08-10/20260810T090000Z.mp4
```

- **카메라가 맨 앞이다.** 카메라 단위로 권한을 나누거나 통째로 지우는 일이 잦다.
- **날짜 디렉터리를 둔다.** 보존 기간 삭제와 날짜 조회가 접두사 하나로 끝난다.
- **시각은 UTC다.** 로컬 시각으로 두면 서버 시각대가 바뀔 때 같은 순간의 객체가
  두 날짜에 걸친다. 콜론은 파일 시스템과 S3 도구에서 다루기 번거로워 기본형을 쓴다.

## 실행 방법

FFmpeg은 pip 패키지가 아니다. 시스템에 따로 설치한다.

```bash
cd worker
python -m pip install -r recorder/requirements.txt
cp recorder/.env.example recorder/.env    # STREAM_SOURCES를 채운다
python -m recorder.main
```

종료는 `Ctrl+C`다. FFmpeg을 먼저 정상 종료시켜 마지막 세그먼트를 완성한 뒤 올린다.
순서를 바꾸면 마지막 녹화분이 항상 유실된다.

> **실제 MinIO 서버(로컬 9000)로 확인한 것**: 버킷 자동 생성, 세그먼트 적재와
> 내려받기 대조, 접두사 조회, 같은 키 덮어쓰기, 객체 삭제, 보존 기간 경과분 삭제.
> `python -m recorder.main`을 그대로 돌려 세그먼트가 객체로 올라가고 로컬에서
> 지워지는 것, 쓰는 중인 세그먼트가 남는 것, FFmpeg이 없어도 워커가 죽지 않고
> 재시도하는 것까지 확인했다.
> **확인하지 못한 것**: 실제 카메라와 FFmpeg을 붙인 녹화. 이 환경에 FFmpeg이 없어
> 세그먼트 파일을 사람이 만들어 넣고 그 뒤 경로를 검증했다. FFmpeg이 있는 사람이
> 녹화까지 확인한 뒤 이 문단을 갱신한다.

## 환경변수

이름과 용도는 [`.env.example`](./.env.example)에 있다. **실제 값은 커밋하지 않는다.**
MinIO 접속 정보와 카메라 접속 정보는 비밀값이다.

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `APP_ENV` | 실행 환경 | `local` / `dev` / `prod`. 필수 |
| `STREAM_SOURCES` | 녹화할 영상 소스 목록 | stream과 같은 형식·같은 변수. 필수 |
| `RECORDING_SEGMENT_SECONDS` | 영상 파일 하나의 길이 | 기본 600 |
| `RECORDING_SEGMENT_DIR` | 세그먼트 임시 경로 | 기본 `recorder/data/segments` |
| `RECORDING_STALE_AFTER_SECONDS` | 녹화 중단 판정 시간 | 기본 900. 세그먼트 길이보다 커야 한다 |
| `RECORDING_UPLOAD_INTERVAL_SECONDS` | 적재 시도 주기 | 기본 30 |
| `RECORDING_RETENTION_DAYS` | 보존 기간 | 기본 30. **팀 합의값이 아니다** |
| `RECORDING_RETENTION_INTERVAL_SECONDS` | 보존 기간 정리 주기 | 기본 3600 |
| `OBJECT_STORAGE_BACKEND` | 저장소 종류 | `local` / `minio`. `prod`에서 `local` 금지 |
| `OBJECT_STORAGE_BUCKET` | 영상 버킷 이름 | 기본 `office-recordings` |
| `OBJECT_STORAGE_LOCAL_DIR` | local backend 경로 | 기본 `recorder/data/objects` |
| `OBJECT_STORAGE_ENDPOINT` | MinIO 주소 | `minio`일 때 필수. `host:port` |
| `OBJECT_STORAGE_ACCESS_KEY` | 접근 키 | 비밀값. `minio`일 때 필수 |
| `OBJECT_STORAGE_SECRET_KEY` | 비밀 키 | 비밀값. `minio`일 때 필수 |
| `OBJECT_STORAGE_SECURE` | TLS 사용 여부 | 기본 true |
| `LOG_LEVEL` | 로그 수준 | 기본 `INFO` |

### local 저장소는 개발용이다

MinIO 없이 적재 경로를 돌려보기 위한 것이다. 결정 0004가 로컬 파일 시스템을
기각한 이유(인스턴스가 늘면 파일 위치가 갈리고, 보존 기간과 접근 권한을 메타데이터와
분리할 수 없다)가 그대로 적용된다. `APP_ENV=prod`에서는 시작 시점에 거부한다.

## 포함하지 않는 것

- 메타데이터 저장 → `fastapi`가 MongoDB에 기록한다. 여기서는 객체만 만든다
- 영상 조회 API와 접근 권한 판정 → `fastapi` 책임
- 프레임 샘플링·추론 → [`stream`](../stream/README.md), [`inference`](../inference/README.md) 책임
- 적재한 객체의 참조를 `fastapi`에 알리는 일 → **`예정`.** 전달 방식이 `결정 필요`다

## 실패했을 때

| 상황 | 동작 |
| --- | --- |
| 필수 환경변수 없음 | 시작 시점에 변수 이름을 알리고 종료 코드 1 |
| `prod`에서 로컬 저장소 | 시작 시점에 거부 |
| MinIO 접속 실패 | 시작 시점에 알리고 종료 코드 1. 세그먼트를 쌓아두지 않는다 |
| 카메라 한 대의 FFmpeg 실패 | 그 카메라만 다음 주기에 재시작한다. 다른 카메라는 계속 녹화 |
| 적재 1회 실패 | 로컬 파일을 남기고 다음 주기에 재시도 |
| 객체 삭제 실패 | 그 객체만 건너뛰고 나머지를 계속 지운다 |

## 테스트

기본 테스트는 FFmpeg·카메라·MinIO 없이 돈다.

```bash
cd worker
python -m pytest recorder/tests -q
```

- `test_segmenter.py` — FFmpeg 명령 구성과 프로세스 수명
- `test_uploader.py` — 완성 세그먼트 판별, 적재 실패 시 로컬 보존
- `test_storage.py` — 객체 키 규칙과 로컬 어댑터
- `test_retention.py` — 보존 기간 경계와 부분 실패
- `test_worker.py` — 녹화 수명과 종료 순서
- `test_minio_storage.py` — SDK 예외를 `ObjectStorageError`로 바꾸는 계약
- `test_end_to_end.py` — 세그먼트 파일부터 보존 기간 삭제까지 실제 컴포넌트로 검증

## 관련 문서

- [worker 개요](../README.md)
- [결정 0004 · 영상·스냅샷 저장소로 MinIO 채택](../../docs/architecture/decisions.md#0004--영상스냅샷-저장소로-minio-채택)
- [결정 0009 · recorder worker의 저장 구조와 보존 정책](../../docs/architecture/decisions.md#0009--recorder-worker의-저장-구조와-보존-정책)
- [환경변수 규칙](../../docs/conventions/environment-convention.md)
