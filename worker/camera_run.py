import cv2

from camera import Camera
from camera_stream import CameraStream
from config import CAMERA_NAME, RTSP_URL


stream = CameraStream(
    CAMERA_NAME,
    RTSP_URL
)

stream.start()


camera = Camera(
    RTSP_URL
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


    if cv2.waitKey(1) & 0xff == ord("q"):
        break



camera.stop()
stream.stop()

cv2.destroyAllWindows()