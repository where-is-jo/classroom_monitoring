import cv2
import time

from camera import Camera
from camera_stream import CameraStream
from video_recorder import VideoRecorder
from frame_capture import FrameCapture

from config import (
    CAMERA_NAME,
    RTSP_URL,
    STREAM_WAIT,
    VIDEO_PATH,
    VIDEO_FPS,
    VIDEO_FRAME_SIZE,
    FRAME_PATH,
    FRAME_INTERVAL
)


# ============================
# RTSP 송출 시작
# ============================

stream = CameraStream(
    CAMERA_NAME,
    RTSP_URL
)

stream.start()


# RTSP Stream 생성 대기
time.sleep(
    STREAM_WAIT
)


# ============================
# Camera 연결
# ============================

camera = Camera(
    RTSP_URL
)

camera.start()


# ============================
# Video Recorder
# ============================

# 고정된 해상도 사용
video_recorder = VideoRecorder(
    save_path=VIDEO_PATH,
    fps=VIDEO_FPS,
    frame_size=VIDEO_FRAME_SIZE
)

video_recorder.start()


# 첫 프레임에서 자동으로 해상도 가져오기
# frame = camera.read()

# if frame is None:
#     raise Exception("첫 프레임을 가져오지 못했습니다.")

# height, width = frame.shape[:2]

# video_recorder = VideoRecorder(
#     save_path=VIDEO_PATH,
#     fps=VIDEO_FPS,
#     frame_size=(width, height)
# )

# video_recorder.start()


# ============================
# Frame Capture
# ============================

frame_capture = FrameCapture(
    save_path=FRAME_PATH,
    interval=FRAME_INTERVAL
)

frame_capture.start()


# ============================
# Main Loop
# ============================

while True:

    # FFmpeg 상태 확인
    if not stream.is_running():

        print("FFmpeg 종료 감지")
        stream.restart()
        time.sleep(
            STREAM_WAIT
        )


    # 영상 읽기
    frame = camera.read()

    if frame is None:
        time.sleep(1)
        continue


    # 원본 영상 저장
    video_recorder.write(
        frame
    )


    # 프레임 저장
    frame_capture.save(
        frame
    )


    # 화면 출력
    cv2.imshow(
        "Smart Office Camera",
        frame
    )

    if cv2.waitKey(1) & 0xff == ord("q"):
        break


# ============================
# 종료
# ============================

camera.stop()

video_recorder.stop()

frame_capture.stop()

stream.stop()

cv2.destroyAllWindows()