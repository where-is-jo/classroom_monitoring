import cv2
import os
from datetime import datetime


class VideoRecorder:

    def __init__(
        self,
        save_path="data/video",
        fps=30,
        frame_size=(640, 480)
    ):
        self.save_path = save_path
        self.fps = fps
        self.frame_size = frame_size
        self.writer = None


        # ===============================
        # [추후 10분 단위 분할 저장용]
        #
        # 사용 예정 변수
        #
        # self.start_time = None
        # self.record_time = 600  # 10분(초)
        #
        # ===============================



    def start(self):

        os.makedirs(
            self.save_path,
            exist_ok=True
        )


        filename = datetime.now().strftime(
            "%Y%m%d_%H%M%S.mp4"
        )


        filepath = os.path.join(
            self.save_path,
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


        # ===============================
        # [추후 10분 단위 분할 저장용]
        #
        # self.start_time = datetime.now()
        #
        # ===============================



    def write(self, frame):

        if self.writer:

            self.writer.write(frame)



            # ===============================
            # [추후 10분 단위 분할 저장용]
            #
            # 현재 시간 확인
            #
            # elapsed = (
            #     datetime.now()
            #     - self.start_time
            # ).seconds
            #
            # if elapsed >= self.record_time:
            #
            #     self.writer.release()
            #
            #     self.start()
            #
            # ===============================



    def stop(self):

        if self.writer:

            self.writer.release()