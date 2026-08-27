# 얼굴 검출·식별·자세·품질 분석 내부 HTTP 서비스(app.py)를 담는 이미지.
#
# **worker 이미지와 달리 torch가 없다.** 추론은 onnxruntime으로 돌고, CUDA는 pip로만
# 가져온다. `nvidia/cuda` 베이스 이미지가 필요 없다는 것을 GPU 서버에서 확인했다 —
# python:3.12-slim에 onnxruntime-gpu와 CUDA 의존만 넣어도 CUDAExecutionProvider가
# 실제로 동작했다(SCRFD 10g 기준 33.6ms → 2.3ms).

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /deeplearning

# insightface는 sdist로 배포되어 설치 시점에 C 확장을 빌드한다. 빌드 도구는
# 이 단계에만 두고 실행 이미지로 넘기지 않는다 — worker Dockerfile과 같은 이유다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} + \
    && find /opt/venv -name '*.pyc' -delete


# --- 실행 이미지 -------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH

# **pip로 받은 CUDA 라이브러리를 찾게 한다.** 이 경로가 없으면 onnxruntime이
# libcublasLt.so.13을 못 찾아 CUDA provider 로드에 실패하고, `get_available_providers()`
# 목록에는 CUDA가 남은 채 세션만 조용히 CPU로 내려간다. 지표에는 "느리다"로만 보이는
# 실패라 app.py가 실제 provider를 세션에 물어 확인한다.
# CUDA 13 휠은 라이브러리를 `nvidia/cu13/lib` 하나로 합쳐 두었다. 12.x 시절처럼
# cublas·cufft·curand가 각각의 디렉터리를 갖지 않는다 — 서버에서 실제 경로를 확인한 값이다.
ENV NVIDIA_LIB=/opt/venv/lib/python3.12/site-packages/nvidia
ENV LD_LIBRARY_PATH=$NVIDIA_LIB/cu13/lib:$NVIDIA_LIB/cudnn/lib

# requirements는 opencv-python-headless를 적지만, **insightface가 non-headless
# opencv-python과 opencv-contrib-python을 함께 끌어온다.** 실제로 import되는 cv2는
# 그쪽이라 GUI 라이브러리를 찾는다. 빠지면 `import cv2`에서 죽는다:
#   ImportError: libxcb.so.1: cannot open shared object file
# 컨테이너 안에서 ldd로 누락 목록을 확인하고 넣은 것이다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgl1 \
        libxcb1 \
    && rm -rf /var/lib/apt/lists/*

# 빌드 단계의 pip 잔여물과 build-essential을 가져오지 않도록 venv만 통째로 옮긴다.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /deeplearning
COPY app.py ./app.py
# 실행 provider 판단. app.py가 `import execution_provider`로 찾는다.
COPY execution_provider.py ./execution_provider.py
COPY face_identity.py ./face_identity.py
COPY face_identification.py ./face_identification.py
# 지표 정의. app.py가 `import metrics`로 찾는다.
COPY metrics.py ./metrics.py

# 모델 가중치는 이미지에 넣지 않는다. .models/는 .gitignore 대상이고 448MB다.
# 경로는 FACE_DETECTION_MODEL_PATH·FACE_LANDMARKER_MODEL_PATH·
# FACE_RECOGNITION_MODEL_PATH로 주입한다. 셋 다 없으면 기동 시점에 죽는다 —
# app.py가 lifespan에서 파일 존재를 확인한다.
RUN useradd --create-home --shell /usr/sbin/nologin analyzer \
    && chown -R analyzer:analyzer /deeplearning
USER analyzer

EXPOSE 8100

# start-period가 긴 것은 lifespan에서 ONNX 모델 셋을 올리기 때문이다.
# 그전에 죽은 것으로 판정하면 기동에 성공한 컨테이너가 계속 재시작된다.
# MongoDB 조회 timeout 기본값이 5초이므로 healthcheck timeout은 그보다 길어야 한다.
HEALTHCHECK --interval=10s --timeout=8s --start-period=90s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health/ready', timeout=7)" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
