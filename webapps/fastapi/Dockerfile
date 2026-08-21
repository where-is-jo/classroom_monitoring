FROM python:3.12-slim

# ROI 기준 화면 캡처가 RTSP 프레임을 디코딩하는 데 쓴다(결정 0031). CCTV가 HEVC를
# 내보내서 순수 파이썬으로는 읽을 수 없다. 없어도 앱은 뜨지만 캡처만 실패한다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# 환경 무관 공용값(타임아웃·판정 임계값). 없으면 Settings가 Python 기본값으로 조용히
# 대체되어, settings.yml을 고쳐도 컨테이너에는 반영되지 않는다.
COPY config ./config
COPY static ./static
COPY templates ./templates
COPY demo_assets ./demo_assets
COPY api-spec ./api-spec

RUN useradd --create-home --shell /usr/sbin/nologin appuser
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]