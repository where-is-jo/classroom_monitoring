"""deeplearning 테스트의 import 경로.

`app.py`는 컨테이너에서 `uvicorn app:app`으로 뜨는 최상위 모듈이라 `metrics`를
절대 이름으로 찾는다(Dockerfile). 테스트는 저장소 최상위에서 `deeplearning.app`으로
부르므로, 같은 이름이 풀리도록 이 디렉터리를 경로에 넣는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_DEEPLEARNING_DIR = Path(__file__).resolve().parent.parent
if str(_DEEPLEARNING_DIR) not in sys.path:
    sys.path.insert(0, str(_DEEPLEARNING_DIR))
