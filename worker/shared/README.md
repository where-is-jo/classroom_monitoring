# shared

**워커가 아니다.** 워커들이 함께 쓰는 계약을 담는다.

## 왜 있는가

stream이 만든 프레임을 inference가 받으려면 둘 사이에 공통 타입과 전달 수단이
필요하다. 어느 한쪽에 두면 다른 쪽이 그쪽을 import하게 되고, 나중에 추론만
따로 배포할 때 상대 워커의 코드가 딸려온다.

```text
stream ───┐
inference ┼──▶ shared     (양방향 의존 없음)
recorder ─┘
```

`stream`과 `recorder`는 같은 `STREAM_SOURCES` 값을 읽는다. 형식을 워커마다 따로
파싱하면 같은 설정이 워커에 따라 다르게 해석될 수 있다.

## 무엇을 넣는가

**두 워커 이상이 실제로 함께 쓰는 것만** 넣는다. "나중에 공용이 될 것 같다"는
근거가 아니다. 한 워커만 쓰는 코드는 그 워커 안에 둔다.

| 파일 | 역할 |
| --- | --- |
| `types.py` | `Frame`, `CapturedFrame` — 프레임과 그 출처 정보 |
| `frame_buffer.py` | `FrameBuffer` — stream과 inference 사이의 프레임 큐 |
| `sampling.py` | `should_sample` — 몇 프레임마다 한 장을 고를지 |
| `camera_sources.py` | `STREAM_SOURCES` 형식 파싱과 자격 증명 가리기 |
| `object_keys.py` | 객체 키 규칙 — `<카메라>/<날짜>/<시각><확장자>` |
| `object_storage/` | 객체 저장소 포트와 어댑터. `inference`(스냅샷)와 `recorder`(영상)가 함께 쓴다 |
| `logging_setup.py` | 진입점의 로깅·콘솔 인코딩 설정 |
| `metrics.py` | Prometheus 노출 경로와 프레임 버퍼 지표. 워커별 지표는 각 워커에 둔다 |
| `config_errors.py` | 설정 오류를 값 노출 없이 사람이 읽을 형태로 |
| `settings_sources.py` | `.env.{APP_ENV}`·`config/settings.yml` 소스 우선순위를 네 워커가 같게 쓰게 한다 |

## object_storage

원래 `recorder` 안에 있었다. [결정 0028](../../docs/architecture/decisions.md#0028--영상-원본을-저장하지-않고-스냅샷만-남긴다)로
적재 주체가 `inference`(탐지 스냅샷)로 옮겨가면서 두 워커가 함께 쓰게 되어 여기로 옮겼다.

| 파일 | 역할 |
| --- | --- |
| `ports.py` | `ObjectStorage` Protocol, `StoredObject` |
| `errors.py` | `ObjectStorageError` |
| `local.py` | 로컬 디렉터리 어댑터(개발용). `APP_ENV=prod`에서 거부된다 |
| `minio.py` | MinIO 어댑터. **SDK import는 이 파일에만 있다** |
| `settings.py` | `OBJECT_STORAGE_*` 설정 mixin. 두 워커가 같은 변수를 같게 읽는다 |
| `factory.py` | 설정에서 저장소를 만든다 |

**`ObjectStorageError`는 `RecorderError`를 상속하지 않는다.** shared가 특정 워커의 예외
계층을 알 수 없어서다. `RecorderError`만 잡던 곳은 둘을 함께 잡아야 한다
(`recorder/main.py`가 그렇게 되어 있다).

## FrameBuffer

`stream`이 넣고 `inference`가 꺼내는 유한 크기 큐다. 단일 카메라 기본 모드와
다중 카메라 pipeline의 `per_camera=True` 모드를 제공한다.

- **가득 차면 가장 오래된 프레임을 버린다.** 생산자는 절대 기다리지 않는다.
  수신 루프가 추론을 기다리면 카메라 쪽에 지연이 쌓인다.
- **소비자는 가장 최근 프레임만 가져간다.** 밀린 프레임을 추론하면 결과가
  계속 과거를 가리킨다. 실시간 파이프라인에서 오래된 프레임은 처리할 가치가 없다.
- **다중 카메라 모드는 카메라별 최신 한 장을 보존한다.** 같은 카메라의 새 프레임은
  그 카메라의 대기 프레임만 교체하고, 대기 카메라는 공정한 순서로 소비한다. 프레임이
  빠른 CCTV가 입구 카메라 프레임을 계속 덮어 신원 인계를 굶기지 않게 한다.

`queue.Queue`를 쓰지 않은 이유는 오래된 항목을 버리는 동작이 없기 때문이다.
꺼내고 다시 넣는 방식으로 흉내 내면 생산자가 여럿일 때 어느 프레임이 남는지
보장할 수 없다. 자세한 배경은
[결정 0006](../../docs/architecture/decisions.md#0006--워커-사이-프레임-전달을-최신-우선-버퍼로-한다)에 있다.

버린 프레임 수는 `stats`로 드러난다. `dropped`가 계속 늘면 추론이 수신을
못 따라가고 있다는 뜻이다. `per_camera=True`의 `maxsize`는 프레임 수가 아니라 동시에
대기할 수 있는 카메라 수이며 pipeline은 설정된 stream 수 이상으로 잡는다.

## 테스트

```bash
cd worker
python -m pytest shared/tests -q
```

동시성 동작(생산자·소비자 여럿, 종료 시 대기 중인 소비자 깨우기)을 함께 검증한다.

## 관련 문서

- [worker 개요](../README.md)
- [조립 진입점](../pipeline/README.md)
- [결정 0006](../../docs/architecture/decisions.md#0006--워커-사이-프레임-전달을-최신-우선-버퍼로-한다)
