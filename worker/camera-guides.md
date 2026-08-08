# Smart Office Monitoring System
## Camera Streaming & Data Collection Architecture

## 1. 개요
스마트 오피스 모니터링 시스템의 영상 수집 단계 구현 내용이다.

목표:
- USB Camera 영상 수집
- RTSP 기반 영상 스트리밍
- 원본 영상 저장
- 객체 탐지 학습용 프레임 이미지 저장
- 추후 Jetson Nano 기반 다중 카메라 확장

현재 USB Camera 1대를 기준으로 개발 및 테스트 진행.

## 2. 전체 구조


USB Camera → FFmpeg → MediaMTX → OpenCV → 저장
├ Video Recorder
└ Frame Capture


구성 역할:
- USB Camera: 영상 입력
- FFmpeg: USB Camera 영상을 RTSP Stream으로 변환
- MediaMTX: RTSP Stream 관리 서버
- OpenCV: RTSP 영상 수신 및 Frame 처리
- Video Recorder: 원본 영상 저장
- Frame Capture: AI 학습용 이미지 저장

## 3. 사용 기술

### Python
전체 시스템 개발 언어.

### OpenCV
사용 목적:
- RTSP 영상 수신
- Frame 처리
- 이미지 저장
- 영상 저장

### FFmpeg
USB Camera 입력을 RTSP Stream으로 변환.

구조:

USB Camera → FFmpeg → RTSP Stream


### MediaMTX
RTSP Stream 관리 서버.

역할:
- FFmpeg Stream 수신
- RTSP Endpoint 제공
- Client 연결 관리

## 4. 통신 방식

### USB Camera → FFmpeg
방식: DirectShow

Windows 환경에서 USB Camera 접근.

예:

video=ABKO APC480 SD WEBCAM


### FFmpeg → MediaMTX
통신: RTSP TCP

사용 이유:
- 영상 저장 목적
- AI 분석 목적
- UDP 대비 안정적인 데이터 전달

### MediaMTX → OpenCV
통신: RTSP

현재 URL:

rtsp://localhost:8554/camera


## 5. 프로젝트 구조


worker
├── config.py
├── camera.py
├── camera_stream.py
├── camera_run.py
├── video_recorder.py
├── frame_capture.py
└── data
├── video
└── frames


## 6. 파일별 역할

### config.py
전체 설정 관리.

관리 항목:
- Camera 이름
- RTSP URL
- 저장 경로
- FPS
- Frame Size

현재 설정:

VIDEO_FPS = 20
VIDEO_FRAME_SIZE = (640,480)
FRAME_INTERVAL = 20


### camera_stream.py
USB Camera → RTSP Stream 변환 담당.

사용:
FFmpeg subprocess 실행.

구조:

USB Camera → FFmpeg → MediaMTX RTSP


목적:
- 실시간 스트리밍
- FPS 유지
- Jetson Nano 확장 대비

### camera.py
RTSP Client 역할.

기능:
- RTSP 연결
- Frame 읽기
- Camera Release

입력:

rtsp://localhost:8554/camera


출력:
Frame 데이터.

### video_recorder.py
원본 영상 저장 담당.

저장 위치:

data/video


현재:
- MP4 저장
- FPS 기준 저장

추후:
- 10분 단위 영상 분할 저장
- 장시간 녹화 관리

### frame_capture.py
객체 탐지 학습용 이미지 저장 담당.

저장 위치:

data/frames


현재 설정:

FRAME_INTERVAL = 20


20 FPS 기준:
약 1초당 1장 저장.

### camera_run.py
전체 실행 담당.

실행 순서:
1. FFmpeg Stream 시작
2. RTSP Camera 연결
3. Video Recorder 실행
4. Frame Capture 실행
5. Frame Loop 실행
6. 저장 처리
7. 종료 처리

## 7. 현재 영상 설정

Camera Input:

20 FPS


Recording:

20 FPS


Frame Capture:

20 frame 당 1장


목표:
- 영상 안정성 확보
- AI 학습 데이터 확보

## 8. 발생했던 문제 및 해결 방향

### H264 corrupted macroblock

원인:
RTSP Stream Packet Loss.

발생:
FFmpeg → MediaMTX 전달 과정에서 영상 데이터 손실 발생.

대응:
- RTSP TCP 사용
- FPS 30 → 20 감소
- Buffer 조정
- GOP 조절

### Frame duplicated 증가

원인:
입력 Frame 부족 또는 Stream 지연.

대응:
- FPS 고정
- RTSP 안정화
- Buffer 증가

### reader is too slow

원인:
RTSP Client 처리 속도 부족.

대응:
- TCP 사용
- 추후 Thread 기반 처리 적용

## 9. 현재 안정화 상태

현재 설정:
- FPS: 20
- 해상도: 640x480
- RTSP TCP 사용
- USB Camera 1대 테스트

목표:
- 장시간 녹화 안정화
- Frame Loss 최소화
- 자동 복구 구조 추가

## 10. 향후 확장 계획

### Phase 3: 장시간 안정화
목표:
- 30분 이상 무중단 녹화
- RTSP 오류 최소화

### Phase 4: 자동 복구
추가 예정:
- FFmpeg Watchdog
- Stream 종료 감지
- 자동 재시작

### Phase 5: Jetson Nano 적용

현재:

USB Camera → PC


변경:

USB Camera → Jetson Nano → RTSP Network → Server PC


### Phase 6: 다중 카메라 지원

목표:
카메라 3대 운영.

예상:

camera1
rtsp://host:8554/camera1

camera2
rtsp://host:8554/camera2

camera3
rtsp://host:8554/camera3


## 현재 상태

완료:
- USB Camera 연결
- FFmpeg RTSP Streaming
- MediaMTX 구성
- OpenCV RTSP 수신
- 원본 영상 저장
- Frame 이미지 저장

진행 중:
- 장시간 안정화 테스트
- RTSP Stream 안정성 개선
- Jetson Nano 적용 준비