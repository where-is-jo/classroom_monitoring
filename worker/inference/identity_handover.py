"""입구에서 확인한 학생 신원을 교실 CCTV ByteTrack으로 인계한다.

두 카메라의 화각이 겹치지 않아 bbox 좌표를 직접 이어 붙이지 않는다. 대신 입구에서
확정된 신원을 짧은 시간 동안 대기시키고, 그 직후 교실 CCTV의 설정된 문 영역에서
처음 만들어지거나 영역 밖에서 안으로 진입한 사람 track과만 연결한다. 후보 학생이나
진입 track이 둘 이상이면 가까운 사람을 추측하지 않고 인계를 보류한다.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Protocol

import requests
from shared.types import CapturedFrame

from .consumer import ResultHandler
from .metrics import (
    IDENTITY_HANDOFF_ATTACH_SECONDS,
    IDENTITY_HANDOFF_TIMESTAMP_ISSUES_TOTAL,
    IDENTITY_HANDOFF_TOTAL,
)
from .types import (
    Detection,
    EntryFaceObservationBatch,
    EntryIdentityProcessingStatus,
    EntryIdentityStatus,
    InferenceResult,
)

logger = logging.getLogger(__name__)

HANDOVER_ROUTES_PATH = "/internal/identity-handover-routes"
HANDOFF_MEASUREMENT_RETENTION_SECONDS = 30.0


@dataclass(frozen=True)
class IdentityHandoverRoute:
    entry_camera_id: str
    classroom_camera_id: str
    # CCTV 프레임의 정규화 좌표 [left, top, right, bottom]. 사람 bbox의 발 위치가
    # 이 영역에서 처음 관측되거나 영역 밖에서 안으로 들어올 때 입구 신원 후보와
    # 연결한다.
    classroom_entry_zone: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        if not self.entry_camera_id or not self.classroom_camera_id:
            raise ValueError("인계 route에는 입구와 교실 camera_id가 필요합니다.")
        if self.entry_camera_id == self.classroom_camera_id:
            raise ValueError("입구와 교실 camera_id는 달라야 합니다.")
        left, top, right, bottom = self.classroom_entry_zone
        if (
            any(not 0.0 <= value <= 1.0 for value in self.classroom_entry_zone)
            or left >= right
            or top >= bottom
        ):
            raise ValueError("교실 문 영역은 0~1 정규화 사각형이어야 합니다.")


def parse_identity_handover_routes(value: str) -> tuple[IdentityHandoverRoute, ...]:
    """환경변수 JSON을 검증된 route로 바꾼다."""
    if not value.strip():
        return ()
    try:
        raw = json.loads(value)
        if not isinstance(raw, list) or not raw:
            raise TypeError
        routes: list[IdentityHandoverRoute] = []
        for item in raw:
            if not isinstance(item, dict):
                raise TypeError
            entry_camera_id = item["entry_camera_id"]
            classroom_camera_id = item["classroom_camera_id"]
            zone = item["classroom_entry_zone"]
            if (
                not isinstance(entry_camera_id, str)
                or not isinstance(classroom_camera_id, str)
                or not isinstance(zone, list)
                or len(zone) != 4
                or any(
                    not isinstance(coordinate, (int, float))
                    or isinstance(coordinate, bool)
                    for coordinate in zone
                )
            ):
                raise TypeError
            routes.append(
                IdentityHandoverRoute(
                    entry_camera_id.strip(),
                    classroom_camera_id.strip(),
                    tuple(float(coordinate) for coordinate in zone),  # type: ignore[arg-type]
                )
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(
            "IDENTITY_HANDOVER_ROUTES는 입구·교실 camera_id와 "
            "classroom_entry_zone을 가진 JSON 배열이어야 합니다."
        ) from error

    classroom_ids = [route.classroom_camera_id for route in routes]
    if len(classroom_ids) != len(set(classroom_ids)):
        raise ValueError("교실 camera_id마다 신원 인계 route는 하나만 둘 수 있습니다.")
    return tuple(routes)


class IdentityHandoverRouteProvider(Protocol):
    def load(self) -> tuple[IdentityHandoverRoute, ...]: ...


class HttpIdentityHandoverRouteProvider:
    """FastAPI 관리 화면에 저장된 현재 인계 route를 읽는다."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        get: Callable[..., requests.Response] = requests.get,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("인계 설정 조회 timeout은 0보다 커야 합니다.")
        self._url = base_url.rstrip("/") + HANDOVER_ROUTES_PATH
        self._timeout_seconds = timeout_seconds
        self._get = get

    def load(self) -> tuple[IdentityHandoverRoute, ...]:
        response = self._get(self._url, timeout=self._timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise TypeError("인계 설정 응답에는 items 배열이 필요합니다.")
        items = payload["items"]
        # 관리자가 마지막 route를 삭제한 정상 상태다. 환경변수 parser는 오타인 `[]`을
        # 거부하지만, API의 빈 목록은 실행 중 인계를 끄라는 명시적 설정으로 취급한다.
        if not items:
            return ()
        return parse_identity_handover_routes(json.dumps(items, ensure_ascii=False))


class RefreshingIdentityHandoverResultHandler:
    """관리 화면의 route를 주기적으로 다시 읽어 실행 중인 worker에 적용한다.

    조회 실패는 기존 route를 유지한다. 정상 응답이 빈 목록이면 관리자가 route를 모두
    지운 것이므로 인계만 끄고 사람 탐지 전송은 계속한다.
    """

    def __init__(
        self,
        initial_routes: tuple[IdentityHandoverRoute, ...],
        *,
        provider: IdentityHandoverRouteProvider,
        inner: ResultHandler,
        refresh_seconds: float,
        maximum_delay_seconds: float = 8.0,
        clock_skew_seconds: float = 0.5,
        track_stale_seconds: float = 30.0,
        minimum_identity_confidence: float = 0.0,
        available_camera_ids: frozenset[str] | None = None,
        entry_camera_ids: frozenset[str] = frozenset(),
        classroom_camera_ids: frozenset[str] = frozenset(),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if refresh_seconds <= 0:
            raise ValueError("인계 설정 갱신 주기는 0보다 커야 합니다.")
        self._routes = initial_routes
        self._provider = provider
        self._inner = inner
        self._refresh_seconds = refresh_seconds
        self._maximum_delay_seconds = maximum_delay_seconds
        self._clock_skew_seconds = clock_skew_seconds
        self._track_stale_seconds = track_stale_seconds
        self._minimum_identity_confidence = minimum_identity_confidence
        self._available_camera_ids = available_camera_ids
        self._entry_camera_ids = entry_camera_ids
        self._classroom_camera_ids = classroom_camera_ids
        self._monotonic = monotonic
        self._last_refresh = float("-inf")
        self._active = self._build(initial_routes)
        self._lock = RLock()

    def __call__(self, captured: CapturedFrame, result: InferenceResult) -> None:
        self._inner(captured, self.enrich_classroom(captured, result))

    def observe_entry(
        self,
        captured: CapturedFrame,
        batch: EntryFaceObservationBatch,
    ) -> None:
        with self._lock:
            self._refresh_if_due()
            if self._active is not None:
                self._active.observe_entry(captured, batch)

    def enrich_classroom(
        self,
        captured: CapturedFrame,
        result: InferenceResult,
    ) -> InferenceResult:
        with self._lock:
            self._refresh_if_due()
            if self._active is None:
                return result
            return self._active.enrich_classroom(captured, result)

    def observe_classroom_tracking(
        self,
        captured: CapturedFrame,
        result: InferenceResult,
    ) -> None:
        """외부에서 숨긴 tentative track도 문 영역 bookkeeping에는 반영한다."""
        with self._lock:
            self._refresh_if_due()
            if self._active is not None:
                self._active.observe_classroom_tracking(captured, result)

    def expire_classroom_tracks(
        self, camera_id: str, track_ids: tuple[str, ...]
    ) -> None:
        with self._lock:
            self._refresh_if_due()
            if self._active is not None:
                self._active.expire_classroom_tracks(camera_id, track_ids)

    def _refresh_if_due(self) -> None:
        now = self._monotonic()
        if now - self._last_refresh < self._refresh_seconds:
            return
        self._last_refresh = now
        try:
            routes = self._provider.load()
            self._validate_camera_ids(routes)
        except (requests.RequestException, ValueError, TypeError) as error:
            logger.warning(
                "신원 인계 설정을 갱신하지 못해 직전 설정을 유지합니다: %s",
                type(error).__name__,
            )
            return
        if routes == self._routes:
            return
        self._routes = routes
        self._active = self._build(routes)
        logger.info("신원 인계 route 동적 설정 %d개를 적용했습니다.", len(routes))

    def _validate_camera_ids(self, routes: tuple[IdentityHandoverRoute, ...]) -> None:
        if self._available_camera_ids is None:
            return
        route_camera_ids = {
            camera_id
            for route in routes
            for camera_id in (route.entry_camera_id, route.classroom_camera_id)
        }
        missing = route_camera_ids - self._available_camera_ids
        if missing:
            raise ValueError(
                "동적 신원 인계 route의 카메라가 STREAM_SOURCES에 없습니다: "
                + ", ".join(sorted(missing))
            )
        invalid_entries = {
            route.entry_camera_id for route in routes
        } - self._entry_camera_ids
        invalid_classrooms = {
            route.classroom_camera_id for route in routes
        } - self._classroom_camera_ids
        if invalid_entries:
            raise ValueError(
                "동적 신원 인계 route의 입구 카메라는 얼굴 전용이어야 합니다: "
                + ", ".join(sorted(invalid_entries))
            )
        if invalid_classrooms:
            raise ValueError(
                "동적 신원 인계 route의 교실 카메라는 사람 추적 대상이어야 합니다: "
                + ", ".join(sorted(invalid_classrooms))
            )

    def _build(
        self, routes: tuple[IdentityHandoverRoute, ...]
    ) -> IdentityHandoverResultHandler | None:
        if not routes:
            return None
        return IdentityHandoverResultHandler(
            routes,
            inner=self._inner,
            maximum_delay_seconds=self._maximum_delay_seconds,
            clock_skew_seconds=self._clock_skew_seconds,
            track_stale_seconds=self._track_stale_seconds,
            minimum_identity_confidence=self._minimum_identity_confidence,
        )


@dataclass
class _PendingIdentity:
    entry_track_id: str
    student_id: str
    confidence: float
    observed_at: float


@dataclass
class _UnmatchedClassroomTrack:
    track_id: str
    first_seen_at: float
    last_seen_at: float


@dataclass
class _ActiveIdentity:
    student_id: str
    confidence: float
    last_seen_at: float
    attach_measurement_pending: bool


@dataclass(frozen=True)
class _HandoffMeasurement:
    entry_observed_at: float | None
    started_at: float


@dataclass
class _RouteState:
    pending: dict[str, _PendingIdentity] = field(default_factory=dict)
    consumed_entry_tracks: dict[str, float] = field(default_factory=dict)
    known_classroom_tracks: dict[str, float] = field(default_factory=dict)
    classroom_tracks_in_entry_zone: dict[str, bool] = field(default_factory=dict)
    unmatched_classroom_tracks: dict[str, _UnmatchedClassroomTrack] = field(
        default_factory=dict
    )
    active_identities: dict[str, _ActiveIdentity] = field(default_factory=dict)
    handoff_measurements: dict[str, _HandoffMeasurement] = field(
        default_factory=dict
    )
    watermark: float = float("-inf")


def _observe_identity_handoff_attach(
    entry_observed_at: float | None,
    attached_at: float | None,
) -> None:
    """유효한 두 관측 시각만 지연 분포에 넣고 이상 원인을 따로 센다."""

    if (
        entry_observed_at is None
        or attached_at is None
        or not math.isfinite(entry_observed_at)
        or not math.isfinite(attached_at)
    ):
        IDENTITY_HANDOFF_TIMESTAMP_ISSUES_TOTAL.labels(reason="missing").inc()
        return
    delay_seconds = attached_at - entry_observed_at
    if delay_seconds < 0:
        IDENTITY_HANDOFF_TIMESTAMP_ISSUES_TOTAL.labels(reason="negative").inc()
        return
    IDENTITY_HANDOFF_ATTACH_SECONDS.observe(delay_seconds)


def _person_detections(result: InferenceResult) -> list[tuple[int, Detection]]:
    return [
        (index, detection)
        for index, detection in enumerate(result.detections)
        if detection.class_name.casefold() == "person"
        and detection.track_id is not None
    ]


def _foot_in_zone(
    detection: Detection,
    frame_shape: tuple[int, int, int],
    zone: tuple[float, float, float, float],
) -> bool:
    height, width = frame_shape[:2]
    if width <= 0 or height <= 0:
        return False
    foot_x = (detection.bbox[0] + detection.bbox[2]) / (2.0 * width)
    foot_y = detection.bbox[3] / height
    left, top, right, bottom = zone
    return left <= foot_x <= right and top <= foot_y <= bottom


class IdentityHandoverResultHandler:
    """입구 신원을 교실 track에 잠그고 같은 track의 이후 프레임에 유지한다."""

    def __init__(
        self,
        routes: tuple[IdentityHandoverRoute, ...],
        *,
        inner: ResultHandler,
        maximum_delay_seconds: float = 8.0,
        clock_skew_seconds: float = 0.5,
        track_stale_seconds: float = 30.0,
        minimum_identity_confidence: float = 0.0,
    ) -> None:
        if not routes:
            raise ValueError("신원 인계 route가 하나 이상 필요합니다.")
        if maximum_delay_seconds <= 0 or clock_skew_seconds < 0:
            raise ValueError("신원 인계 시간 설정이 올바르지 않습니다.")
        if track_stale_seconds <= maximum_delay_seconds + clock_skew_seconds:
            raise ValueError(
                "track stale 시간은 인계 최대 시간과 시각 오차의 합보다 길어야 합니다."
            )
        if not 0.0 <= minimum_identity_confidence <= 1.0:
            raise ValueError("신원 신뢰도 임계값은 0과 1 사이여야 합니다.")
        self._routes = routes
        self._inner = inner
        self._maximum_delay_seconds = maximum_delay_seconds
        self._clock_skew_seconds = clock_skew_seconds
        self._track_stale_seconds = track_stale_seconds
        self._minimum_identity_confidence = minimum_identity_confidence
        self._states = {route: _RouteState() for route in routes}
        self._route_locks = {route: RLock() for route in routes}
        self._entry_routes: dict[str, list[IdentityHandoverRoute]] = {}
        self._classroom_routes: dict[str, IdentityHandoverRoute] = {}
        for route in routes:
            self._entry_routes.setdefault(route.entry_camera_id, []).append(route)
            self._classroom_routes[route.classroom_camera_id] = route

    def __call__(self, captured: CapturedFrame, result: InferenceResult) -> None:
        self._inner(captured, self.enrich_classroom(captured, result))

    def observe_entry(
        self,
        captured: CapturedFrame,
        batch: EntryFaceObservationBatch,
    ) -> None:
        observed_at = captured.captured_at.timestamp()
        for route in self._entry_routes.get(captured.camera_id, ()):
            with self._route_locks[route]:
                self._observe_entry(route, batch, observed_at)

    def enrich_classroom(
        self,
        captured: CapturedFrame,
        result: InferenceResult,
    ) -> InferenceResult:
        route = self._classroom_routes.get(captured.camera_id)
        if route is None:
            return result
        with self._route_locks[route]:
            return self._observe_classroom(
                route,
                result,
                captured.captured_at.timestamp(),
            )

    def observe_classroom_tracking(
        self,
        captured: CapturedFrame,
        result: InferenceResult,
    ) -> None:
        """tentative를 외부에 내보내지 않고 진입 상태만 먼저 기록한다."""
        route = self._classroom_routes.get(captured.camera_id)
        if route is None:
            return
        with self._route_locks[route]:
            self._observe_classroom_tracks(
                route,
                result,
                captured.captured_at.timestamp(),
            )

    def expire_classroom_tracks(
        self, camera_id: str, track_ids: tuple[str, ...]
    ) -> None:
        """ByteTrack이 만료를 확정한 순간 인계 상태에서도 같은 track을 제거한다."""
        route = self._classroom_routes.get(camera_id)
        if route is None or not track_ids:
            return
        with self._route_locks[route]:
            state = self._states[route]
            for track_id in track_ids:
                state.known_classroom_tracks.pop(track_id, None)
                state.classroom_tracks_in_entry_zone.pop(track_id, None)
                state.unmatched_classroom_tracks.pop(track_id, None)
                state.active_identities.pop(track_id, None)
                measurement = state.handoff_measurements.pop(track_id, None)
                if measurement is not None:
                    IDENTITY_HANDOFF_TIMESTAMP_ISSUES_TOTAL.labels(
                        reason="expired"
                    ).inc()

    def _expire_handoff_measurements(
        self,
        state: _RouteState,
        observed_at: float,
    ) -> None:
        expired_track_ids = [
            track_id
            for track_id, measurement in state.handoff_measurements.items()
            if observed_at - measurement.started_at
            >= HANDOFF_MEASUREMENT_RETENTION_SECONDS
        ]
        for track_id in expired_track_ids:
            del state.handoff_measurements[track_id]
            active = state.active_identities.get(track_id)
            if active is not None:
                active.attach_measurement_pending = False
            IDENTITY_HANDOFF_TIMESTAMP_ISSUES_TOTAL.labels(reason="expired").inc()

    def _expire(self, state: _RouteState, observed_at: float) -> bool:
        """오래된 후보를 버리고 인계 입력 집합이 바뀌었는지 돌려준다.

        모호한 후보가 시간 만료로 하나만 남으면 다시 매칭해야 한다. 만료 여부를
        호출자에게 돌려주지 않으면 새 track이나 새 얼굴 식별이 들어오기 전까지
        인계가 영구히 멈춘다.
        """
        state.watermark = max(state.watermark, observed_at)
        now = state.watermark
        self._expire_handoff_measurements(state, now)
        pending_count = len(state.pending)
        unmatched_count = len(state.unmatched_classroom_tracks)
        state.pending = {
            track_id: identity
            for track_id, identity in state.pending.items()
            if now - identity.observed_at <= self._maximum_delay_seconds
        }
        state.unmatched_classroom_tracks = {
            track_id: track
            for track_id, track in state.unmatched_classroom_tracks.items()
            if now - track.first_seen_at
            <= self._maximum_delay_seconds + self._clock_skew_seconds
        }
        state.consumed_entry_tracks = {
            track_id: last_seen
            for track_id, last_seen in state.consumed_entry_tracks.items()
            if now - last_seen <= self._track_stale_seconds
        }
        state.known_classroom_tracks = {
            track_id: last_seen
            for track_id, last_seen in state.known_classroom_tracks.items()
            if now - last_seen <= self._track_stale_seconds
        }
        state.classroom_tracks_in_entry_zone = {
            track_id: inside
            for track_id, inside in state.classroom_tracks_in_entry_zone.items()
            if track_id in state.known_classroom_tracks
        }
        stale_active_track_ids = {
            track_id
            for track_id, identity in state.active_identities.items()
            if now - identity.last_seen_at > self._track_stale_seconds
        }
        for track_id in stale_active_track_ids:
            if state.handoff_measurements.pop(track_id, None) is not None:
                IDENTITY_HANDOFF_TIMESTAMP_ISSUES_TOTAL.labels(
                    reason="expired"
                ).inc()
        state.active_identities = {
            track_id: identity
            for track_id, identity in state.active_identities.items()
            if track_id not in stale_active_track_ids
        }
        state.handoff_measurements = {
            track_id: measurement
            for track_id, measurement in state.handoff_measurements.items()
            if track_id in state.active_identities
        }
        return (
            len(state.pending) != pending_count
            or len(state.unmatched_classroom_tracks) != unmatched_count
        )

    def _observe_entry(
        self,
        route: IdentityHandoverRoute,
        batch: EntryFaceObservationBatch,
        observed_at: float,
    ) -> None:
        state = self._states[route]
        match_inputs_expired = self._expire(state, observed_at)
        changed = False
        if batch.processing_status is not EntryIdentityProcessingStatus.SUCCEEDED:
            if match_inputs_expired:
                self._try_match(state)
            return
        for observation in batch.observations:
            if (
                observation.identity_status is not EntryIdentityStatus.REGISTERED
                or observation.student_id is None
                or observation.similarity is None
                or observation.similarity < self._minimum_identity_confidence
            ):
                continue
            if any(
                active.student_id == observation.student_id
                for active in state.active_identities.values()
            ):
                continue
            track_id = observation.face_track_id
            if track_id in state.consumed_entry_tracks:
                state.consumed_entry_tracks[track_id] = max(
                    state.consumed_entry_tracks[track_id], observed_at
                )
                continue
            existing = state.pending.get(track_id)
            if existing is not None and existing.student_id != observation.student_id:
                # 한 entry track의 신원은 첫 확정값에 잠근다. 프레임 하나의 다른 결과로
                # 바꾸면 CCTV에 잘못된 이름을 자신 있게 넘길 수 있다.
                continue
            state.pending[track_id] = _PendingIdentity(
                track_id,
                observation.student_id,
                observation.similarity,
                max(existing.observed_at, observed_at) if existing else observed_at,
            )
            changed = True
        if changed or match_inputs_expired:
            self._try_match(state)

    def _observe_classroom(
        self,
        route: IdentityHandoverRoute,
        result: InferenceResult,
        observed_at: float,
    ) -> InferenceResult:
        state, people = self._observe_classroom_tracks(
            route, result, observed_at
        )

        enriched = list(result.detections)
        for index, detection in people:
            assert detection.track_id is not None
            identity = state.active_identities.get(detection.track_id)
            if identity is None:
                continue
            if identity.attach_measurement_pending:
                self._record_handoff_attachment(
                    state,
                    detection.track_id,
                    identity,
                    observed_at,
                )
            enriched[index] = replace(
                detection,
                student_id=identity.student_id,
                identity_confidence=identity.confidence,
                # CCTV는 얼굴을 인식한 카메라가 아니므로 얼굴 bbox를 넘기지 않는다.
                face_bbox=None,
            )
        return InferenceResult(result.frame_shape, tuple(enriched))

    def _observe_classroom_tracks(
        self,
        route: IdentityHandoverRoute,
        result: InferenceResult,
        observed_at: float,
    ) -> tuple[_RouteState, list[tuple[int, Detection]]]:
        state = self._states[route]
        match_inputs_expired = self._expire(state, observed_at)
        people = _person_detections(result)
        entered_entry_zone: list[str] = []
        for _, detection in people:
            track_id = detection.track_id
            assert track_id is not None
            is_new = track_id not in state.known_classroom_tracks
            was_in_entry_zone = state.classroom_tracks_in_entry_zone.get(
                track_id, False
            )
            is_in_entry_zone = _foot_in_zone(
                detection, result.frame_shape, route.classroom_entry_zone
            )
            state.known_classroom_tracks[track_id] = max(
                state.known_classroom_tracks.get(track_id, observed_at), observed_at
            )
            state.classroom_tracks_in_entry_zone[track_id] = is_in_entry_zone
            active = state.active_identities.get(track_id)
            if active is not None:
                active.last_seen_at = max(active.last_seen_at, observed_at)
            unmatched = state.unmatched_classroom_tracks.get(track_id)
            if unmatched is not None:
                unmatched.last_seen_at = max(unmatched.last_seen_at, observed_at)

            entered = is_in_entry_zone and (is_new or not was_in_entry_zone)
            if active is None and unmatched is None and entered:
                state.unmatched_classroom_tracks[track_id] = _UnmatchedClassroomTrack(
                    track_id, observed_at, observed_at
                )
                entered_entry_zone.append(track_id)

        if entered_entry_zone and len(entered_entry_zone) > 1:
            IDENTITY_HANDOFF_TOTAL.labels(outcome="ambiguous_tracks").inc()

        if entered_entry_zone or match_inputs_expired:
            self._try_match(state)
        return state, people

    @staticmethod
    def _record_handoff_attachment(
        state: _RouteState,
        track_id: str,
        identity: _ActiveIdentity,
        attached_at: float,
    ) -> None:
        identity.attach_measurement_pending = False
        measurement = state.handoff_measurements.pop(track_id, None)
        if measurement is None:
            _observe_identity_handoff_attach(None, attached_at)
            return
        _observe_identity_handoff_attach(
            measurement.entry_observed_at,
            attached_at,
        )

    def _try_match(self, state: _RouteState) -> None:
        active_student_ids = {
            identity.student_id for identity in state.active_identities.values()
        }
        for entry_track_id, identity in list(state.pending.items()):
            if identity.student_id not in active_student_ids:
                continue
            del state.pending[entry_track_id]
            state.consumed_entry_tracks[entry_track_id] = identity.observed_at

        # 동일 학생이 얼굴 track ID 변경으로 여러 번 대기 중이어도 학생 후보는 하나다.
        # 학생과 CCTV track의 고유한 조합 수로 모호성을 판단한다.
        viable_by_pair: dict[
            tuple[str, str], tuple[_PendingIdentity, _UnmatchedClassroomTrack]
        ] = {}
        for identity in state.pending.values():
            for classroom_track in state.unmatched_classroom_tracks.values():
                delay = classroom_track.first_seen_at - identity.observed_at
                if -self._clock_skew_seconds <= delay <= self._maximum_delay_seconds:
                    key = (identity.student_id, classroom_track.track_id)
                    existing = viable_by_pair.get(key)
                    if (
                        existing is None
                        or identity.observed_at > existing[0].observed_at
                    ):
                        viable_by_pair[key] = (identity, classroom_track)

        viable = list(viable_by_pair.values())

        if not viable:
            if state.unmatched_classroom_tracks:
                IDENTITY_HANDOFF_TOTAL.labels(outcome="no_candidate").inc()
            return
        if len(viable) != 1:
            IDENTITY_HANDOFF_TOTAL.labels(outcome="ambiguous_candidates").inc()
            return

        identity, classroom_track = viable[0]
        state.active_identities[classroom_track.track_id] = _ActiveIdentity(
            identity.student_id,
            identity.confidence,
            classroom_track.last_seen_at,
            True,
        )
        state.handoff_measurements[classroom_track.track_id] = _HandoffMeasurement(
            identity.observed_at,
            max(identity.observed_at, classroom_track.last_seen_at),
        )
        # 같은 학생이 여러 얼굴 track으로 관측됐더라도 하나의 CCTV track에 인계한
        # 순간 같은 학생 후보를 모두 소비한다.
        for entry_track_id, pending in list(state.pending.items()):
            if pending.student_id != identity.student_id:
                continue
            del state.pending[entry_track_id]
            state.consumed_entry_tracks[entry_track_id] = pending.observed_at
        del state.unmatched_classroom_tracks[classroom_track.track_id]
        IDENTITY_HANDOFF_TOTAL.labels(outcome="accepted").inc()
        logger.info(
            "입구 신원을 교실 track으로 인계했습니다. entry_track=%s classroom_track=%s",
            identity.entry_track_id,
            classroom_track.track_id,
        )


__all__ = [
    "HANDOVER_ROUTES_PATH",
    "HttpIdentityHandoverRouteProvider",
    "IdentityHandoverResultHandler",
    "IdentityHandoverRoute",
    "RefreshingIdentityHandoverResultHandler",
    "parse_identity_handover_routes",
]
