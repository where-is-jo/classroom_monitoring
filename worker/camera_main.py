import cv2
from camera_config import Camera

# 노트북 직접 연결일 경우 (내 노트북 기본 캠이 0번 연결한게 1번)
# camera = Camera(1)
# RTSP 일 경우 
# camera = Camera("rtsp://192.168.0.10:8554/camera")

camera = Camera(
    "rtsp://localhost:8554/camera"
)

camera.start()


while True:

    frame = camera.read()


    if frame is None:
        break


    cv2.imshow(
        "Smart Office Camera",
        frame
    )


    if cv2.waitKey(1) & 0xff == ord('q'):
        break



camera.stop()
cv2.destroyAllWindows()