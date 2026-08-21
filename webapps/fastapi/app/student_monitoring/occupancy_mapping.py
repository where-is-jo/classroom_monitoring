"""탐지 결과를 카메라별 ROI에 대조해 좌석 근거로 바꾸는 순수 규칙.

좌석 위치의 정본은 `roi_connections.polygon` 하나다(결정 0020). `seat.geometry`는
배치도를 그리는 좌표라 카메라 화각과 무관하며 판정에 쓰지 않는다.

**관측 대상은 그 카메라에 ROI가 등록된 좌석뿐이다.** 카메라가 강의실의 일부만
보는 분할 관측에서, 보지도 못한 좌석을 "비어 있음"으로 기록하지 않기 위해서다.

이 모듈이 만든 `SeatEvidence` 하나를 좌석 점유와 학생 상태가 **함께** 쓴다. 예전에는
좌석은 관측 batch를, 학생은 원본 탐지를 각자 훑어 같은 프레임을 두 번 다르게
해석했다(결정 0020의 남은 일).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..classrooms.models import SeatObservation
from ..roi_connections.mapping import RoiMappingReason, map_bbox_to_roi
from ..roi_connections.models import RoiConnection
from .models import Detection, FrameInfo, SeatEvidence


def map_detections_to_evidence(
    detections: Sequence[Detection],
    connections: Sequence[RoiConnection],
    frame: FrameInfo,
    confidence_threshold: float,
    *,
    held: Mapping[str, float] | None = None,
) -> tuple[SeatEvidence, ...]:
    """탐지 결과를 좌석별 근거로 변환한다.

    좌석마다 세 가지 중 하나를 내놓는다. 세 가지는 `occupied`와 `confidence` 두 값의
    조합으로 표현되며, 해석은 좌석 서비스가 같은 임계값으로 수행한다.

    | 이번 프레임에서 그 좌석에 매칭된 탐지 | 근거 | 좌석 상태 |
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
    - 좌석에 매칭된 탐지가 신원을 들고 있으면 그대로 옮겨 담는다. 붙들려서 점유가 된
      좌석에는 신원을 붙이지 않는다 — 그 좌석에서 이번에 아무도 못 봤기 때문이다.

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

    evidence: list[SeatEvidence] = []
    seen: set[str] = set()
    for connection in connections:
        # 같은 좌석에 ROI가 여러 번 등록돼도 근거는 좌석당 하나만 만든다.
        if connection.seat_id in seen:
            continue
        seen.add(connection.seat_id)
        seat_id = connection.seat_id
        matched_detection = best_by_seat.get(seat_id)
        if matched_detection is not None and matched_detection.confidence >= confidence_threshold:
            evidence.append(_from_detection(seat_id, matched_detection))
            continue
        held_confidence = None if held is None else held.get(seat_id)
        if held_confidence is not None:
            # 직전 관측을 그대로 잇는다. 새로 본 것이 아니므로 신뢰도를 올리지 않고,
            # 신원도 붙이지 않는다. 임계값 미만 탐지보다 이쪽을 우선한다 —
            # 직전에 실제로 본 근거가 더 세다.
            evidence.append(
                SeatEvidence(
                    seat_id=seat_id,
                    occupied=True,
                    confidence=held_confidence,
                    student_id=None,
                    identity_confidence=None,
                )
            )
            continue
        if matched_detection is not None:
            # 임계값 미만 탐지만 있다. 비었다고 단정하지 않고 낮은 신뢰도를 그대로 넘긴다.
            evidence.append(_from_detection(seat_id, matched_detection))
            continue
        evidence.append(
            SeatEvidence(
                seat_id=seat_id,
                occupied=False,
                confidence=0.0,
                student_id=None,
                identity_confidence=None,
            )
        )
    return tuple(evidence)


def to_seat_observations(evidence: Sequence[SeatEvidence]) -> tuple[SeatObservation, ...]:
    """좌석 근거를 강의실 서비스가 받는 관측으로 옮긴다.

    신원은 좌석 점유 판정에 쓰이지 않으므로 여기서 떨어져 나간다. 강의실 도메인이
    학생 신원을 알 필요가 없다는 경계를 그대로 유지하기 위한 투영이다.
    """
    return tuple(
        SeatObservation(
            seat_id=item.seat_id,
            occupied=item.occupied,
            confidence=item.confidence,
        )
        for item in evidence
    )


def unseated_identities(
    detections: Sequence[Detection],
    connections: Sequence[RoiConnection],
    frame: FrameInfo,
    confidence_threshold: float,
) -> dict[str, float]:
    """어느 좌석 ROI에도 들지 않은 **신원 있는** 탐지를 학생별로 모은다.

    "누군지 아는 사람이 강의실 안에 있는데 좌석에는 없다"(`IN_CLASSROOM`, 결정 0025)를
    판정하는 근거다. 신원이 없는 미매칭 탐지는 판정 근거가 되지 않는다 — 좌석 밖에
    사람이 있다는 사실만으로는 누구의 상태도 바꿀 수 없기 때문이다.

    **`NO_MATCH`만 좌석 밖으로 본다.** 겹치는 ROI에 걸린 `AMBIGUOUS`는 "좌석에 없다"가
    아니라 "어느 좌석인지 못 정하겠다"이다. 그것을 좌석 밖으로 세면 자리에 앉아 있는
    학생이 강의실을 서성이는 것으로 기록된다 — 조용한 오판이다.

    같은 학생이 여러 번 잡히면 신원 신뢰도가 가장 높은 것만 남긴다.
    """
    result: dict[str, float] = {}
    for detection in detections:
        if (
            detection.student_id is None
            or detection.identity_confidence is None
            or detection.confidence < confidence_threshold
        ):
            continue
        mapping = map_bbox_to_roi(
            detection.bbox,
            frame_width_pixels=frame.width_pixels,
            frame_height_pixels=frame.height_pixels,
            connections=connections,
        )
        if mapping.reason != RoiMappingReason.NO_MATCH:
            continue
        current = result.get(detection.student_id)
        if current is None or detection.identity_confidence > current:
            result[detection.student_id] = detection.identity_confidence
    return result


def _from_detection(seat_id: str, detection: Detection) -> SeatEvidence:
    return SeatEvidence(
        seat_id=seat_id,
        occupied=True,
        confidence=detection.confidence,
        student_id=detection.student_id,
        identity_confidence=detection.identity_confidence,
    )
