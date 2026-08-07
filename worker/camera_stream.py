import subprocess


class CameraStream:

    def __init__(
        self,
        camera_name,
        rtsp_url
    ):
        self.camera_name = camera_name
        self.rtsp_url = rtsp_url
        self.process = None


    def start(self):

        command = [
            "ffmpeg",
            "-f",
            "dshow",
            "-i",
            f"video={self.camera_name}",
            "-vcodec",
            "libx264",
            "-preset",
            "ultrafast",
            "-f",
            "rtsp",
            self.rtsp_url
        ]

        self.process = subprocess.Popen(command)


    def stop(self):

        if self.process:
            self.process.terminate()
            self.process.wait()