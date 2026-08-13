# stream + inference 조립 실행(`python -m pipeline.main`)을 담는 이미지.
#
# torch가 어느 CUDA로 빌드된 휠을 쓸지 정한다. 이미지 안의 CUDA는 이 값 하나로 결정된다.
# 기본값을 cu126으로 둔 이유는 아래 "CUDA 베이스 이미지를 쓰지 않는다" 절에 있다.
# 서버 GPU가 Blackwell(sm_100/sm_120)이면 이 값을 cu128 이상으로 바꿔야 한다.
#   docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128 .
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126


FROM python:3.12-slim AS builder

ARG TORCH_INDEX_URL
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /worker

# requirements를 먼저 복사해 레이어 캐시를 살린다. pipeline/requirements.txt가
# stream·inference 것을 -r로 참조하므로 세 파일이 모두 있어야 한다.
COPY stream/requirements.txt stream/requirements.txt
COPY inference/requirements.txt inference/requirements.txt
COPY pipeline/requirements.txt pipeline/requirements.txt

# torch를 먼저 지정한 CUDA 빌드로 깔아 둔다. 그래야 뒤이은 ultralytics 설치가
# torch>=1.8.0을 이미 충족된 것으로 보고 PyPI 기본 휠을 덮어쓰지 않는다.
#
# triton은 torch.compile(inductor) 전용이라 추론 경로에서 쓰이지 않는다. 688 MB다.
# 지운 뒤 실제로 GPU 추론이 되는 것을 확인했다.
# nccl·cusparselt·nvshmem도 단일 GPU 추론에는 필요 없어 보이지만 지우면 안 된다 —
# 셋 다 `import torch` 시점에 dlopen되어 ImportError로 죽는다. 확인하고 남긴 것이다.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir \
        --index-url "${TORCH_INDEX_URL}" \
        torch torchvision \
    && /opt/venv/bin/pip install --no-cache-dir -r pipeline/requirements.txt \
    && /opt/venv/bin/pip uninstall -y triton \
    && find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} + \
    && find /opt/venv -name '*.pyc' -delete


# --- 실행 이미지 -------------------------------------------------------------
#
# ## CUDA 베이스 이미지를 쓰지 않는다
#
# 이전에는 nvidia/cuda:12.8.1-runtime-ubuntu24.04를 베이스로 썼다. 그럴 이유가 없다.
# **torch 휠이 자기 CUDA 런타임을 통째로 번들해 온다**(site-packages/nvidia, 3.6 GB).
# 베이스의 CUDA는 쓰이지 않으면서 이미지만 키웠고, 버전도 어긋나 있었다 —
# 베이스는 12.8인데 그 위에 깔린 torch는 CUDA 13 휠이었다.
#
# 게다가 CUDA 베이스는 NVIDIA_REQUIRE_CUDA=cuda>=12.8을 이미지에 박아 넣는다.
# 그래서 드라이버가 그보다 낮은 호스트에서는 **컨테이너가 시작조차 못 했다**:
#   nvidia-container-cli: requirement error: unsatisfied condition: cuda>=12.8
# 코드가 요구한 제약이 아니라 베이스 이미지가 만든 제약이다.
#
# GPU는 CUDA 베이스 없이도 넘어온다. 필요한 것은 아래 두 환경변수와
# 호스트의 NVIDIA Container Toolkit뿐이다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

# ffmpeg·libGL은 pip 패키지가 아니다. opencv-python이 libGL.so.1과 libglib을 찾고,
# stream worker의 RTSP 송출 경로가 ffmpeg 바이너리를 직접 부른다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 빌드 단계의 pip 잔여물을 가져오지 않도록 venv만 통째로 옮긴다.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /worker
COPY shared ./shared
COPY stream ./stream
COPY inference ./inference
COPY pipeline ./pipeline

# 모델 가중치는 이미지에 넣지 않는다(.gitignore가 *.pt를 막는 이유와 같다).
# MODEL_PATH로 마운트한 경로를 가리킨다.
RUN useradd --create-home --shell /usr/sbin/nologin worker \
    && chown -R worker:worker /worker
USER worker

CMD ["python", "-m", "pipeline.main"]
