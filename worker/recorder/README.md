# recorder worker

원본 영상을 세그먼트 단위로 나눠 객체 저장소에 적재하고, 보존 기간이 지나면 지운다.

## 공용 서버에서는 이 워커를 실행하지 않는다

**[결정 0011](../../docs/architecture/decisions.md#0011--영상-원본을-저장하지-않고-스냅샷만-남긴다)로
영상 원본을 저장하지 않기로 했다.** 공용 GPU 서버의 가용 용량이 약 48 GB인데
1080p 카메라 한 대가 시간당 약 0.9 GB라 상시 녹화가 성립하지 않는다.
탐지 시점 스냅샷은 `inference`가 남긴다.

**코드는 지우지 않는다.** 저장 용량이 충분한 환경에서는 그대로 유효하고,
아래 저장 구조(`ObjectStorage` 포트, `minio`·`local` 어댑터, 객체 키 규칙)는
스냅샷 적재에서 재사용한다. 폐기된 것은 세그먼트 생성과 보존 기간 기본값 30일이다.

아래 절은 0011 이전의 배경 기록이다.

## 저장 정책이 아직 합의되지 않았다

**이 워커는 팀 합의 전에 만들어졌다.** [결정 0004](../../docs/architecture/decisions.md#0004--영상과-얼굴-이미지-저장소로-minio-채택)가
"저장 범위·보존 기간·접근 권한이 정해지기 전까지 저장 범위를 넓히는 기능을 만들지
않는다"고 정했고, 그 항목들은 지금도 `결정 필요`다. 코드가 먼저 생긴 경위는
[결정 0007](../../docs/architecture/decisions.md#0007--recorder-worker의-저장-구조와-보존-정책)에 있다.

| 항목 | 상태 | 코드에서 |
| --- | --- | --- |
| 상시 녹화 여부 | `결정 필요` | 상시 세그먼트 녹화로 구현 |
| 보존 기간과 자동 삭제 | `결정 필요` | `recording_retention_days` 기본 30일(`config/settings.yml`) |
| 접근 권한 | `결정 필요` | 미구현. 조회 경로가 아직 없다 |
| 개인정보 처리 근거와 고지 | `결정 필요` | 미구현. 코드로 풀 문제가 아니다 |

**기본값 30일은 팀이 합의한 값이 아니다.** 워커는 시작할 때 현재 보존 기간과
저장소를 경고 로그로 남겨, 합의 없이 운영에 쓰이는 것이 눈에 띄게 한다.
합의되면 이 표와 `config/settings.yml`의 주석을 함께 갱신한다.

**강의실 영상에는 학생의 얼굴이 담기고 미성년자가 포함될 수 있다.**
기술 부채가 아니라 미해결 합의 사항이다.

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

**종료할 때는 예외다.** FFmpeg을 먼저 세운 뒤 쓰던 세그먼트까지 올린다. 세우지 않고
올리면 미완성 파일이 올라가고, 올리지 않으면 마지막 녹화분이 로컬에만 남는다.

**올리기 전에 moov atom이 있는지 본다.** 없으면 어떤 재생기도 열지 못하므로 적재하지
않고 로컬에 남긴다. 저장소 목록만 봐서는 깨진 객체를 알아챌 수 없기 때문이다.
ffprobe를 부르지 않고 최상위 box만 훑는다.

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

규칙의 근거는 [결정 0007](../../docs/architecture/decisions.md#0007--recorder-worker의-저장-구조와-보존-정책)에 있다.

- **카메라가 맨 앞이다.** 카메라 단위로 권한을 나누거나 통째로 지우는 일이 잦다.
- **날짜 디렉터리를 둔다.** 보존 기간 삭제와 날짜 조회가 접두사 하나로 끝난다.
- **시각은 UTC다.** 로컬 시각으로 두면 서버 시각대가 바뀔 때 같은 순간의 객체가
  두 날짜에 걸친다. 콜론은 파일 시스템과 S3 도구에서 다루기 번거로워 기본형을 쓴다.

**로컬 세그먼트 파일 이름은 로컬 시각이다**(`20260811_101706.mp4`). FFmpeg의
`-strftime`은 localtime을 쓰고 UTC로 바꾸는 옵션이 없다. `TZ=UTC`로 바뀌는 빌드도
있지만 서드파티 바이너리의 C 런타임 동작에 기대는 것이라 다른 빌드에서 조용히
틀린다. 그래서 이름에는 시각대를 표시하지 않고, 읽을 때 시스템 시각대를 붙여
객체 키에서 UTC로 변환한다.

## 실행 방법

FFmpeg은 pip 패키지가 아니다. 시스템에 따로 설치한다.

```bash
cd worker
python -m pip install -r recorder/requirements.txt
cp recorder/.env.example recorder/.env.local    # STREAM_SOURCES를 채운다
export APP_ENV=local   # 생략하면 어차피 local로 동작한다
python -m recorder.main
```

종료는 `Ctrl+C`다. FFmpeg의 stdin에 `q`를 보내 쓰던 세그먼트를 완성하게 한 뒤 올린다.

**`terminate`로 끝내지 않는다.** Windows에서 `Popen.terminate()`는 `TerminateProcess`라
정리할 틈을 주지 않아 마지막 세그먼트가 moov atom 없이 48바이트로 남는다. POSIX의
SIGTERM과 동작이 달라 개발 환경에서만 조용히 깨진다. 실제 FFmpeg으로 확인한 차이다.
`q`에 응답하지 않으면 `terminate` → `kill` 순으로 넘어간다.

> **실제 FFmpeg·MediaMTX·MinIO로 확인한 것**: RTSP에서 10초 세그먼트를 받아
> 객체 저장소에 적재하고 로컬에서 지우는 전 구간. 적재된 mp4를 내려받아 ffprobe로
> 열어 h264 640x480 · 정확히 200프레임 · 10.00초임을 확인했고 전체 디코딩에도
> 오류가 없었다. 종료 시 쓰던 세그먼트가 완성되어(4.65초) 함께 올라가고 로컬에
> 남는 파일이 없는 것, FFmpeg이 없을 때 워커가 죽지 않고 재시도하는 것,
> MinIO가 꺼져 있을 때 접속 오류가 처리되는 것도 확인했다.
>
> **확인하지 못한 것**: 실제 카메라(USB·CCTV·Jetson)를 붙인 녹화. 위 검증은
> FFmpeg이 만든 합성 영상(`testsrc`)을 MediaMTX에 올려 대신했다. 장시간 녹화의
> 안정성과 카메라 3대 동시 녹화도 측정하지 않았다.

## 환경변수와 설정

환경마다 달라야 하는 값·비밀값은 `.env.{local,dev,prod}`([`.env.example`](./.env.example)이
기준)에, 환경과 무관한 일반 설정은 커밋된 [`config/settings.yml`](./config/settings.yml)에
있다. **실제 값이 든 `.env.*`는 커밋하지 않는다.** MinIO 접속 정보와 카메라 접속
정보는 비밀값이다.

### `.env.{local,dev,prod}`

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `APP_ENV` | 실행 환경 | `local` / `dev` / `prod`. 필수 |
| `STREAM_SOURCES` | 녹화할 영상 소스 목록 | stream과 같은 형식·같은 변수. 필수 |
| `OBJECT_STORAGE_BACKEND` | 저장소 종류 | `local` / `minio`. `prod`에서 `local` 금지 |
| `OBJECT_STORAGE_ENDPOINT` | MinIO 주소 | `minio`일 때 필수. `host:port` |
| `OBJECT_STORAGE_ACCESS_KEY` | 접근 키 | 비밀값. `minio`일 때 필수 |
| `OBJECT_STORAGE_SECRET_KEY` | 비밀 키 | 비밀값. `minio`일 때 필수 |

### `config/settings.yml`

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `recording_segment_seconds` | 영상 파일 하나의 길이 | 기본 600 |
| `recording_segment_dir` | 세그먼트 임시 경로 | 기본 `recorder/data/segments` |
| `recording_stale_after_seconds` | 녹화 중단 판정 시간 | 기본 900. 세그먼트 길이보다 커야 한다 |
| `recording_upload_interval_seconds` | 적재 시도 주기 | 기본 30 |
| `recording_retention_days` | 보존 기간 | 기본 30. **팀 합의값이 아니다** |
| `recording_retention_interval_seconds` | 보존 기간 정리 주기 | 기본 3600 |
| `object_storage_bucket` | 영상 버킷 이름 | 기본 `office-recordings`. **이전 주제에서 온 이름이며 변경 검토 대상** |
| `object_storage_local_dir` | local backend 경로 | 기본 `recorder/data/objects` |
| `object_storage_secure` | TLS 사용 여부 | 기본 true |
| `log_level` | 로그 수준 | 기본 `INFO` |

### local 저장소는 개발용이다

MinIO 없이 적재 경로를 돌려보기 위한 것이다. 결정 0004가 로컬 파일 시스템을
기각한 이유(인스턴스가 늘면 파일 위치가 갈리고, 보존 기간과 접근 권한을 메타데이터와
분리할 수 없다)가 그대로 적용된다. `APP_ENV=prod`에서는 시작 시점에 거부한다.

## 포함하지 않는 것

- 메타데이터 저장 → `fastapi`가 MongoDB에 기록한다. 여기서는 객체만 만든다
- 얼굴 등록 이미지 적재 → `fastapi`가 별도 버킷에 넣는다(`예정`). 이 워커는 영상만 다룬다
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
| 세그먼트가 완성되지 않음(moov 없음) | 적재하지 않고 로컬에 남긴다. 오류로 로그를 남긴다 |
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
- [결정 0004 · 영상과 얼굴 이미지 저장소로 MinIO 채택](../../docs/architecture/decisions.md#0004--영상과-얼굴-이미지-저장소로-minio-채택)
- [결정 0007 · recorder worker의 저장 구조와 보존 정책](../../docs/architecture/decisions.md#0007--recorder-worker의-저장-구조와-보존-정책)
- [환경변수 규칙](../../docs/conventions/environment-convention.md)
