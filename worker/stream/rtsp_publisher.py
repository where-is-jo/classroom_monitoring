import subprocess
import time


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

        if self.process:
            return


        command = [
            "ffmpeg",

            "-f",
            "dshow",

            "-rtbufsize",
            "200M",

            "-framerate",
            "20",

            "-i",
            f"video={self.camera_name}",

            "-c:v",
            "libx264",

            "-preset",
            "ultrafast",

            "-tune",
            "zerolatency",

            "-pix_fmt",
            "yuv420p",

            "-g",
            "40",

            "-rtsp_transport",
            "tcp",

            "-f",
            "rtsp",

            self.rtsp_url
        ]


        self.process = subprocess.Popen(
            command
        )


        print(
            "FFmpeg RTSP Stream 시작"
        )



    def is_running(self):

        if self.process is None:
            return False


        return self.process.poll() is None



    def restart(self):

        print(
            "FFmpeg 재시작"
        )


        self.stop()


        time.sleep(1)


        self.start()



    def stop(self):

        if self.process:

            self.process.terminate()

            self.process.wait()

            self.process = None

            print(
                "FFmpeg 종료"
            )