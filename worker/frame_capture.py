import cv2
import os
from datetime import datetime


class FrameCapture:

    def __init__(
        self,
        save_path="data/frames",
        interval=30
    ):
        self.save_path = save_path
        self.interval = interval
        self.count = 0


    def start(self):

        os.makedirs(
            self.save_path,
            exist_ok=True
        )


    def save(self, frame):

        if self.count % self.interval == 0:

            filename = datetime.now().strftime(
                "%Y%m%d_%H%M%S_%f.jpg"
            )

            filepath = os.path.join(
                self.save_path,
                filename
            )


            result = cv2.imwrite(
                filepath,
                frame
            )


            if not result:
                print("프레임 저장 실패:", filepath)


        self.count += 1


    def stop(self):
        pass