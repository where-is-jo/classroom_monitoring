import cv2
import os

from datetime import datetime

from config import (
    FRAME_INTERVAL, 
    FRAME_PATH
)

class FrameCapture:

    def __init__(
        self,
        save_path=FRAME_PATH,
        interval=FRAME_INTERVAL
    ):

        self.save_path = save_path
        self.interval = interval
        self.count = 0


    def start(self):

        pass
    

    def save(self, frame):

        if self.count % self.interval == 0:
            date_folder = datetime.now().strftime(
                "%Y-%m-%d"
            )

            folder_path = os.path.join(
                self.save_path,
                date_folder
            )

            os.makedirs(
                folder_path,
                exist_ok=True
            )

            filename = datetime.now().strftime(
                "%Y%m%d_%H%M%S_%f.jpg"
            )

            filepath = os.path.join(
                folder_path,
                filename
            )

            cv2.imwrite(
                filepath,
                frame
            )

        self.count += 1



    def stop(self):

        pass