"""워커 진입점의 로깅 설정.

로그 메시지가 한국어라 콘솔 인코딩을 맞추지 않으면 깨진다. 진입점마다 같은
코드를 복사하지 않도록 여기 한 번만 둔다.
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"


def use_utf8_console() -> None:
    """로그 출력을 UTF-8로 고정한다.

    Windows 콘솔 기본 코드페이지(cp949)로 한국어 로그가 나가면 깨진다.
    실행하는 사람이 PYTHONUTF8을 설정해야만 읽히는 상태로 두지 않는다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # 파이프로 넘길 때처럼 바꿀 수 없는 스트림이면 그대로 둔다.
            # 로그가 깨지는 것이 프로세스를 못 띄우는 것보다 낫다.
            pass


def configure_logging(log_level: str = "INFO") -> None:
    use_utf8_console()
    logging.basicConfig(level=log_level, format=_LOG_FORMAT)
