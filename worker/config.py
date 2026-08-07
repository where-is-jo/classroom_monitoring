# =========================
# Camera
# =========================

MAX_RETRY = 10

RETRY_DELAY = 1

CAMERA_NAME = "ABKO APC480 SD WEBCAM"

RTSP_URL = "rtsp://localhost:8554/camera"

# jetson nano에서 RTSP 서버를 실행할 경우
# RTSP_URL = "rtsp://192.168.0.20:8554/camera"

# =========================
# Stream
# =========================

STREAM_WAIT = 3


# =========================
# Video
# =========================

VIDEO_PATH = "data/video"

VIDEO_FPS = 20

VIDEO_FRAME_SIZE = (640, 480)


# =========================
# Frame Capture
# =========================

FRAME_PATH = "data/frames"

FRAME_INTERVAL = 20


# =========================
# Camera Retry
# =========================

MAX_RETRY = 10

RETRY_DELAY = 1