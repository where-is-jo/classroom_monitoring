import cv2
import time

from config import (
    MAX_RETRY,
    RETRY_DELAY
)

class Camera:

    def __init__(
        self,
        camera_source,
        max_retry=MAX_RETRY,
        retry_delay=RETRY_DELAY
    ):
        self.camera_source = camera_source
        self.cap = None

        # 재연결 설정
        self.max_retry = max_retry
        self.retry_delay = retry_delay



    def start(self):

        retry_count = 0

        while retry_count < self.max_retry:

            print(
                f"카메라 연결 시도 {retry_count + 1}/{self.max_retry}"
            )

            self.cap = cv2.VideoCapture(
                self.camera_source
            )

            self.cap.set(
                cv2.CAP_PROP_BUFFERSIZE,
                1
            )

            if self.cap.isOpened():
                print("카메라 연결 성공")
                return


            retry_count += 1

            if self.cap:
                self.cap.release()

            time.sleep(
                self.retry_delay
            )

        raise Exception(
            "카메라 연결 실패"
        )



    def read(self):

        if self.cap is None:
            return None

        ret, frame = self.cap.read()

        if ret:
            return frame

        # 영상 읽기 실패
        print(
            "영상 수신 실패 - 재연결 시도"
        )

        self.reconnect()

        return None



    def reconnect(self):

        self.stop()

        try:
            self.start()

        except Exception as e:
            print(
                "카메라 재연결 실패:",
                e
            )



    def stop(self):

        if self.cap:
            self.cap.release()
            self.cap = None