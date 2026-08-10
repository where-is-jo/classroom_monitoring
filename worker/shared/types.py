"""워커들이 함께 쓰는 타입.

Frame을 워커마다 따로 정의하면 같은 것에 이름이 여럿 생긴다. 여기에 한 번만 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

Frame = NDArray[np.uint8]


@dataclass(frozen=True)
class CapturedFrame:
    """카메라에서 읽어 추론 대상으로 고른 프레임 한 장.

    프레임 배열만 넘기면 "어느 카메라의 언제 프레임인가"를 소비자가 알 수 없다.
    탐지 결과를 나중에 직원·좌석과 이어붙이려면 이 정보가 결과에 따라다녀야 한다.
    """

    camera_id: str
    frame: Frame
    captured_at: datetime
    # 카메라별로 0부터 증가하는 원본 프레임 번호. 버퍼에서 몇 장이 버려졌는지
    # 소비자가 번호 간격으로 확인할 수 있다.
    sequence: int

    @property
    def shape(self) -> tuple[int, ...]:
        return self.frame.shape
