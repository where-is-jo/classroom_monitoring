import cv2


class Camera:

    def __init__(self, camera_source):
        self.camera_source = camera_source
        self.cap = None


    def start(self):

        self.cap = cv2.VideoCapture(
            self.camera_source
        )

        if not self.cap.isOpened():
            raise Exception("카메라 연결 실패")


    def read(self):

        ret, frame = self.cap.read()

        if not ret:
            return None

        return frame


    def stop(self):

        if self.cap:
            self.cap.release()