"""탐지 결과를 카메라별 ROI에 대조해 좌석 점유 관측으로 바꾸는 순수 규칙.

좌석 위치의 정본은 `roi_connections.polygon` 하나다(결정 0020). `seat.geometry`는
배치도를 그리는 좌표라 카메라 화각과 무관하며 판정에 쓰지 않는다.

**관측 대상은 그 카메라에 ROI가 등록된 좌석뿐이다.** 카메라가 강의실의 일부만
보는 분할 관측에서, 보지도 못한 좌석을 "비어 있음"으로 기록하지 않기 위해서다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..classrooms.models import SeatObservation
from ..roi_connections.mapping import map_bbox_to_roi
from ..roi_connections.models import RoiConnection
from .models import Detection, FrameInfo


def map_detections_to_observations(
    detections: Sequence[Detection],
    connections: Sequence[RoiConnection],
    frame: FrameInfo,
    confidence_threshold: float,
    *,
    held: Mapping[str, float] | None = None,
) -> tuple[SeatObservation, ...]:
    """탐지 결과를 좌석별 점유 관측으로 변환한다.

    좌석마다 세 가지 중 하나를 내놓는다. 세 가지는 `occupied`와 `confidence` 두 값의
    조합으로 표현되며, 해석은 좌석 서비스가 같은 임계값으로 수행한다.

    | 이번 프레임에서 그 좌석에 매칭된 탐지 | 관측 | 좌석 상태 |
    | --- | --- | --- |
    | 임계값 이상 | `occupied=True`, 그 신뢰도 | `OCCUPIED` |
    | 임계값 미만만 | `occupied=True`, 그 낮은 신뢰도 | `UNKNOWN` |
    | 없음 (붙들고 있음) | `occupied=True`, 직전 신뢰도 | `OCCUPIED` |
    | 없음 | `occupied=False`, 0.0 | `VACANT` |

    - bbox 중심이 정확히 하나의 ROI에 들어갈 때만 그 좌석에 매칭한다.
      겹치는 ROI(AMBIGUOUS)와 어디에도 없는 bbox(NO_MATCH)는 증거가 되지 않는다.
    - 여러 사람이 같은 좌석에 겹치면 신뢰도가 높은 사람만 채택한다.
    - 반환 대상은 `connections`에 있는 좌석뿐이다. 매칭되지 않은 좌석은
      "관측했으나 비어 있음"으로 남는다 — 그 카메라가 실제로 보는 자리이기 때문이다.
    - `held`에 있는 좌석은 이번 프레임에서 잡히지 않아도 점유로 본다. 값은 직전에
      관측한 신뢰도다. 어느 좌석을 얼마나 오래 붙들지는 호출자가 정한다.

    **왜 임계값 미만 탐지를 버리지 않는가**: 버리면 그 좌석이 "매칭 없음"이 되어 빈
    자리로 기록된다. 흐릿하게라도 사람이 잡힌 자리를 비었다고 단정하는 것은 조용한
    오판이다. 확신이 없다는 사실을 낮은 신뢰도로 그대로 넘겨 `UNKNOWN`이 되게 한다.

    **왜 붙들어 주는가**: 앉아 있는 사람도 프레임마다 꾸준히 잡히지는 않는다. 실측에서
    좌석 13곳의 미탐 구간 24개 중 14개가 1프레임(1.3초)짜리였다. 이것을 그대로
    "비어 있음"으로 기록하면 좌석 상태가 몇 초마다 깜빡이고, 학생 상태도 함께 흔들린다.
    """
    best_by_seat: dict[str, Detection] = {}
    for detection in detections:
        matched = map_bbox_to_roi(
            detection.bbox,
            frame_width_pixels=frame.width_pixels,
            frame_height_pixels=frame.height_pixels,
            connections=connections,
        ).connection
        if matched is None:
            continue
        current = best_by_seat.get(matched.seat_id)
        if current is None or detection.confidence > current.confidence:
            best_by_seat[matched.seat_id] = detection

    observations: list[SeatObservation] = []
    seen: set[str] = set()
    for connection in connections:
        # 같은 좌석에 ROI가 여러 번 등록돼도 관측은 좌석당 하나만 만든다.
        if connection.seat_id in seen:
            continue
        seen.add(connection.seat_id)
        matched_detection = best_by_seat.get(connection.seat_id)
        if matched_detection is not None and matched_detection.confidence >= confidence_threshold:
            observations.append(
                SeatObservation(
                    seat_id=connection.seat_id,
                    occupied=True,
                    confidence=matched_detection.confidence,
                )
            )
            continue
        held_confidence = None if held is None else held.get(connection.seat_id)
        if held_confidence is not None:
            # 직전 관측을 그대로 잇는다. 새로 본 것이 아니므로 신뢰도를 올리지 않는다.
            # 임계값 미만 탐지보다 이쪽을 우선한다 — 직전에 실제로 본 근거가 더 세다.
            observations.append(
                SeatObservation(
                    seat_id=connection.seat_id,
                    occupied=True,
                    confidence=held_confidence,
                )
            )
            continue
        if matched_detection is not None:
            # 임계값 미만 탐지만 있다. 비었다고 단정하지 않고 낮은 신뢰도를 그대로 넘긴다.
            observations.append(
                SeatObservation(
                    seat_id=connection.seat_id,
                    occupied=True,
                    confidence=matched_detection.confidence,
                )
            )
            continue
        observations.append(
            SeatObservation(seat_id=connection.seat_id, occupied=False, confidence=0.0)
        )
    return tuple(observations)
