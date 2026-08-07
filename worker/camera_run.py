import time
import cv2

from camera import Camera
from camera_stream import CameraStream
from video_recorder import VideoRecorder
from frame_capture import FrameCapture
from config import CAMERA_NAME, RTSP_URL


stream = CameraStream(
    CAMERA_NAME,
    RTSP_URL
)

stream.start()

# FFmpeg가 RTSP 스트림을 생성할 시간 확보
time.sleep(3)


camera = Camera(
    RTSP_URL
)

camera.start()


video_recorder = VideoRecorder(
    save_path="data/video",
    fps=30,
    frame_size=(640,480)
)

video_recorder.start()


frame_capture = FrameCapture(
    save_path="data/frames",
    interval=30
)

frame_capture.start()



while True:

    frame = camera.read()

    if frame is None:
        break


    # 원본 영상 저장
    video_recorder.write(frame)


    # 30프레임당 1장 저장
    frame_capture.save(frame)


    cv2.imshow(
        "Smart Office Camera",
        frame
    )


    if cv2.waitKey(1) & 0xff == ord("q"):
        break



camera.stop()

video_recorder.stop()

frame_capture.stop()

stream.stop()

cv2.destroyAllWindows()