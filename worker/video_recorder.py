# 10분 단위 분할 저장용
import cv2
import os

from datetime import datetime

from config import (
    VIDEO_FPS, 
    VIDEO_PATH
)


class VideoRecorder:

    def __init__(
        self,
        save_path=VIDEO_PATH,
        fps=VIDEO_FPS,
        frame_size=(640, 480),
        segment_time=3600
    ):
        self.save_path = save_path
        self.fps = fps
        self.frame_size = frame_size
        self.segment_time = segment_time

        self.writer = None
        self.start_time = None


    def start(self):

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
            "%Y%m%d_%H%M%S.mp4"
        )

        filepath = os.path.join(
            folder_path,
            filename
        )

        fourcc = cv2.VideoWriter_fourcc(
            *"mp4v"
        )

        self.writer = cv2.VideoWriter(
            filepath,
            fourcc,
            self.fps,
            self.frame_size
        )

        self.start_time = datetime.now()

        print(
            f"영상 저장 시작 : {filepath}"
        )


    def write(self, frame):

        if self.writer:

            self.writer.write(frame)


            elapsed = (
                datetime.now()
                - self.start_time
            ).seconds


            if elapsed >= self.segment_time:

                self.writer.release()

                self.writer = None

                self.start()


    def stop(self):

        if self.writer:

            self.writer.release()

            self.writer = None