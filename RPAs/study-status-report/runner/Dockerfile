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

# **root로 돌리지 않되, 마운트에 쓸 수 있는 UID로 돌린다.**
# 이 컨테이너는 마운트된 저장소의 logs/·reports/에 파일을 쓴다.
#
# 예전에는 `USER nobody`로 못 박혀 있었다. "Docker Desktop이 바인드 마운트 권한을
# 무시해서 어차피 써진다"고 봤는데 **사실이 아니다.** 실측하니 WSL2의 9p 마운트는
# metadata 옵션이 붙어 리눅스 권한을 그대로 적용했고, logs/가 `drwxr-xr-x 65534:65534`
# 였다 — 지금 써지는 이유는 nobody가 그 디렉터리를 만들어 소유하고 있어서다. 다른
# UID로 바꾸면 그 순간 쓰기가 막힌다.
#
# 그래서 **기본값은 현행(nobody, 65534)을 유지하고** UID/GID만 빌드 인자로 연다.
# 리눅스 호스트로 옮겨 저장소 파일이 사람 계정 소유일 때는 `id -u`/`id -g` 값을 넣거나,
# 이미 있는 logs/·reports/의 소유자를 맞춰 준다. 쓰기 가능 여부는 실행기의
# `GET /health`가 알려 준다 — 못 쓰면 503으로 답한다.
ARG RUNNER_UID=65534
ARG RUNNER_GID=65534
# 이미 같은 번호의 사용자·그룹이 베이스 이미지에 있을 수 있어 있으면 그대로 쓴다.
# USER에는 번호를 준다 — 이름이 무엇이든 의도한 UID로 돌게 하려는 것이다.
RUN set -eux; \
    if ! getent group "${RUNNER_GID}" >/dev/null; then addgroup -g "${RUNNER_GID}" rpa; fi; \
    if ! getent passwd "${RUNNER_UID}" >/dev/null; then \
        adduser -D -H -u "${RUNNER_UID}" -G "$(getent group "${RUNNER_GID}" | cut -d: -f1)" rpa; \
    fi
USER ${RUNNER_UID}:${RUNNER_GID}

EXPOSE 8099
CMD ["python3", "/opt/rpa-runner/server.py"]
