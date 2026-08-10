"""프레임 샘플링 판단.

모든 프레임을 추론에 보내지 않는다. 샘플링 없이 전량을 보내면 추론이 병목이 된다.
"""

from __future__ import annotations


def should_sample(frame_index: int, interval_frames: int) -> bool:
    """이 프레임을 샘플링 대상으로 고를지 판단한다.

    프레임 번호만 보는 순수 함수라 실제 카메라 없이 검증할 수 있다.
    저장 대상과 추론 대상이 같은 판단을 쓰게 해서, 디스크에 남은 학습용 이미지와
    추론에 들어간 프레임이 어긋나지 않게 한다.
    """
    if interval_frames < 1:
        raise ValueError("샘플링 주기는 1 이상이어야 합니다.")
    return frame_index % interval_frames == 0
