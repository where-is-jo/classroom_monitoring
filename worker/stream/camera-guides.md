# 카메라 수집 구성

**목적**: stream worker가 어떤 구성 요소로 영상을 받는지, 설정값을 왜 그 값으로
정했는지, 수집을 붙이면서 겪은 문제와 대응을 남긴다.
**대상 독자**: 실제 카메라를 붙여 stream worker를 돌리는 팀원.

실행 절차와 환경변수 목록은 [stream README](./README.md)에 있다. 여기서 반복하지 않는다.

## 1. 전체 구조

```text
USB 카메라 → FFmpeg → MediaMTX → OpenCV → stream worker
                                              ├→ 프레임 샘플링 (→ inference, 예정)
                                              ├→ 원본 영상 저장 (개발용, 기본 꺼짐)
                                              └→ 학습용 프레임 저장 (개발용, 기본 꺼짐)
```

| 구성 요소 | 역할 |
| --- | --- |
| USB 카메라 | 영상 입력 |
| FFmpeg | USB 카메라 영상을 RTSP 스트림으로 변환 |
| MediaMTX | RTSP 스트림 관리 서버. 엔드포인트 제공과 클라이언트 연결 관리 |
| OpenCV | RTSP 수신과 프레임 처리 |

Jetson이나 CCTV가 직접 RTSP를 내보내는 구성에서는 FFmpeg 단계가 필요 없다.
`RTSP_PUBLISH_ENABLED=false`로 두고 `STREAM_SOURCES`만 채운다.

## 2. 통신 방식

| 구간 | 방식 | 비고 |
| --- | --- | --- |
| USB 카메라 → FFmpeg | DirectShow(`dshow`) | Windows 기준. Linux는 `v4l2`, macOS는 `avfoundation` |
| FFmpeg → MediaMTX | RTSP over TCP | UDP 대비 손실이 적다. 아래 5절 참고 |
| MediaMTX → OpenCV | RTSP | |

입력 형식은 OS를 코드에서 판별하지 않고 `RTSP_PUBLISH_INPUT_FORMAT` 설정으로 고른다.

dshow는 장치를 이름으로 지정한다. 장치 이름은 다음으로 확인한다.

```bash
ffmpeg -list_devices true -f dshow -i dummy
```

## 3. 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `config.py` | 환경변수 읽기와 시작 시 검증, `STREAM_SOURCES` 파싱 |
| `errors.py` | 도메인 예외 |
| `camera_reader.py` | RTSP 클라이언트. 연결·재연결·프레임 읽기와 연결 상태 |
| `rtsp_publisher.py` | FFmpeg subprocess로 USB 카메라를 RTSP로 송출 |
| `video_recorder.py` | 원본 영상 세그먼트 저장 |
| `frame_capture.py` | 샘플링한 프레임을 JPEG으로 저장 |
| `worker.py` | 카메라별 파이프라인을 스레드로 관리 |
| `main.py` | 진입점 |

저장 경로는 `stream/data/` 아래이며 `.gitignore` 대상이다.

```text
stream/data/
├── video/<카메라 식별자>/<YYYY-MM-DD>/<YYYYMMDD_HHMMSS>.mp4
└── frames/<카메라 식별자>/<YYYY-MM-DD>/<YYYYMMDD_HHMMSS_ffffff>.jpg
```

## 4. 현재 설정값과 근거

| 값 | 설정 이름 | 근거 |
| --- | --- | --- |
| 20 FPS | `RTSP_PUBLISH_FRAMERATE`, `RECORDING_FPS` | 30에서 낮췄다. 5절 참고 |
| 20프레임마다 1장 | `FRAME_SAMPLE_INTERVAL_FRAMES` | 20 FPS 기준 약 1초에 한 장 |
| RTSP TCP | 고정 | 5절 참고 |
| GOP = 프레임률 × 2 | 고정 | 5절 참고 |
| 버퍼 크기 1 | 고정 | 읽기가 느릴 때 지연이 쌓이지 않게 한다 |

해상도는 설정이 아니라 **실제 프레임에서 가져온다.** 설정한 해상도와 실제 해상도가
어긋나면 OpenCV `VideoWriter`가 오류 없이 빈 파일을 만들기 때문이다.

## 5. 겪은 문제와 대응

### H264 corrupted macroblock

- **원인**: RTSP 스트림 패킷 손실. FFmpeg → MediaMTX 전달 과정에서 영상 데이터가 유실됐다.
- **대응**: RTSP를 TCP로 보내고, 프레임률을 30에서 20으로 낮추고, 버퍼와 GOP를 조정했다.

### Frame duplicated 증가

- **원인**: 입력 프레임 부족 또는 스트림 지연.
- **대응**: 프레임률을 고정하고 `-rtbufsize`를 키웠다.

### reader is too slow

- **원인**: RTSP 클라이언트 처리 속도 부족.
- **대응**: TCP를 쓰고 `CAP_PROP_BUFFERSIZE`를 1로 두어 오래된 프레임이 쌓이지 않게 했다.
  카메라별 스레드 처리는 이후 `worker.py`에 반영했다.

## 6. 확장 계획

| 단계 | 내용 | 상태 |
| --- | --- | --- |
| 장시간 안정화 | 30분 이상 무중단 수집 | `예정`. 측정하지 않았다 |
| 자동 복구 | FFmpeg 종료 감지와 재시작, 카메라 재연결 | 구현됨 |
| 다중 카메라 | 워커 하나가 여러 소스를 스레드로 관리 | 구현됨. 실제 3대 검증은 `예정` |
| Jetson 적용 | `USB 카메라 → Jetson → RTSP 네트워크 → 서버` | `예정` |
| 추론 연결 | 샘플링한 프레임을 inference worker에 공급 | `예정`. 전달 방식 `결정 필요` |

다중 카메라는 `STREAM_SOURCES`에 소스를 늘려 설정한다.

```bash
STREAM_SOURCES=camera-01=rtsp://host:8554/camera1,camera-02=rtsp://host:8554/camera2
```

## 7. 확인하지 못한 것

이 문서의 설정값과 문제 대응은 **USB 카메라 1대 기준**으로 얻은 것이다.
다중 카메라 구성과 Jetson 구성에서 같은 값이 통하는지는 확인하지 않았다.
장시간 수집의 안정성도 측정하지 않았다. 측정한 사람이 이 절을 갱신한다.

## 관련 문서

- [stream README](./README.md) — 실행 절차, 환경변수, 테스트
- [worker 개요](../README.md) — 워커 구성과 경계
