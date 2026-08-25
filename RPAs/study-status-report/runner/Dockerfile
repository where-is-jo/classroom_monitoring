# study-status-report 스크립트를 실행하는 사이드카.
#
# n8n 공식 이미지(2.33.5)에는 파이썬이 없고 apk도 제거돼 있어 그 안에서는 스크립트를
# 돌릴 수 없다. 그래서 파이썬만 있는 작은 컨테이너를 따로 두고 n8n이 HTTP로 부른다.
#
# 스크립트와 실행기 모두 표준 라이브러리만 쓴다 — pip install이 없다.
FROM python:3.12-alpine

# 코드는 이미지에 굽지 않고 compose에서 저장소를 /repo로 마운트한다.
# 스크립트를 고칠 때마다 이미지를 다시 만들지 않으려는 것이다.
WORKDIR /repo

ENV PYTHONUNBUFFERED=1 \
    REPO_DIR=/repo \
    RUNNER_PORT=8099

# 실행기 자신은 마운트와 무관하게 항상 있어야 해서 이미지에 넣는다.
COPY server.py /opt/rpa-runner/server.py

# root로 돌릴 이유가 없다. 마운트된 저장소에 쓰기만 하면 된다.
USER nobody

EXPOSE 8099
CMD ["python3", "/opt/rpa-runner/server.py"]
