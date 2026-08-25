"""stream과 inference를 한 프로세스로 조립할 때만 쓰는 설정."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Self

from inference.identity_handover import (
    IdentityHandoverRoute,
    parse_identity_handover_routes,
)
from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from shared.settings_sources import customise_sources_with_yaml

_PIPELINE_DIR = Path(__file__).resolve().parent

_APP_ENV_FOR_FILE_SELECTION = os.environ.get("APP_ENV", "local")

# 조립 실행에서는 이 파일 하나만 읽는다. 워커별 .env.*를 각각 읽게 두면
# 같은 변수가 두 파일에 흩어져 어느 값이 적용됐는지 알 수 없게 된다.
# stream/inference의 "환경 의존 설정·비밀값"(APP_ENV, STREAM_SOURCES, MODEL_PATH,
# INFERENCE_DEVICE, OBJECT_STORAGE_* 등)만 여기 담긴다 — 일반 설정·판정 기준값은
# 각 워커 자신의 config/settings.yml을 그대로 읽는다.
PIPELINE_ENV_FILE = _PIPELINE_DIR / f".env.{_APP_ENV_FOR_FILE_SELECTION}"


class PipelineSettings(BaseSettings):
    """버퍼와 소비자 동작을 정하는 값. 전부 환경과 무관하게 같은 값이라 yml에만 둔다."""

    model_config = SettingsConfigDict(
        env_file=PIPELINE_ENV_FILE,
        yaml_file=_PIPELINE_DIR / "config" / "settings.yml",
        # PyYAML의 기본 파일 인코딩은 OS 로캘을 따른다. 한국어 Windows에서는 cp949라
        # yml의 한국어 주석을 읽다가 UnicodeDecodeError가 난다. 명시적으로 고정한다.
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    # 기본값이 1인 이유는 실시간 파이프라인에서 최신 한 장이면 충분하기 때문이다.
    # 2 이상은 추론 시간이 들쭉날쭉할 때 그 편차를 흡수하려는 경우에만 올린다.
    # 키울수록 오래된 프레임을 들고 있게 되어 지연이 커진다.
    frame_buffer_maxsize: int = Field(default=1, ge=1, le=32)

    # 버퍼가 비어 있어도 종료 신호를 확인할 수 있어야 해서 대기에 상한을 둔다.
    inference_poll_timeout_seconds: float = Field(default=0.5, gt=0, le=10)

    # 연속으로 이만큼 추론에 실패하면 파이프라인을 멈춘다. 계속 실패하는 상태로
    # 도는 것은 프레임을 버리면서 아무것도 만들지 않는 것과 같다.
    inference_max_consecutive_failures: int = Field(default=5, ge=1, le=100)

    # 역할별 처리량을 분리한다. 입구 HTTP가 느려도 CCTV track은 약 5FPS로 갱신한다.
    face_identity_sample_interval_frames: int = Field(default=20, ge=1, le=10000)
    person_tracking_sample_interval_frames: int = Field(default=4, ge=1, le=10000)

    # --- 지표 노출 ---
    # 워커는 웹 서버가 아니라서 Prometheus가 긁어갈 곳이 없다. 켜면 전용 포트에
    # /metrics만 여는 최소 HTTP 서버가 데몬 스레드로 뜬다.
    #
    # 기본값이 켜짐인 이유는 스냅샷·녹화와 성격이 다르기 때문이다. 저장 기능은
    # 개인정보를 남기므로 명시적으로 켜야 하지만, 여기서 나가는 것은 처리량과
    # 지연 같은 집계 숫자뿐이고 개인을 식별하는 값은 담기지 않는다.
    metrics_enabled: bool = True

    # **바인딩 주소는 노출 범위를 정한다.** 컨테이너 안에서 Prometheus가 다른
    # 컨테이너로 붙어야 해서 기본값이 0.0.0.0이다. 호스트에서 직접 돌릴 때 사설망에
    # 열고 싶지 않으면 127.0.0.1로 낮춘다. 앱 전체에 인증이 없는 상태(결정 0010)라
    # 이 포트를 공인 IP에 그대로 여는 것은 접근 통제 결정 전까지 피한다.
    metrics_host: str = Field(default="0.0.0.0")

    # 9090(Prometheus)·9100(node_exporter)과 겹치지 않는 값을 골랐다.
    metrics_port: int = Field(default=9101, ge=1024, le=65535)

    # 탐지 결과를 전송할 FastAPI URL. 조립 실행에서 worker는 HTTP POST로 여기에
    # 적재한다. 웹앱이 다른 주소로 뜨면 이 값만 바꾸면 된다.
    # 환경마다 다른 주소이므로 .env.{APP_ENV} 쪽에 둔다 — settings.yml에 넣지 않는다.
    fastapi_url: str = Field(default="http://127.0.0.1:8001")

    # 입구 얼굴 관측 내부 서비스 주소. worker는 모델 종류나 갤러리 구조를 모르고
    # 이 계약만 호출한다. 얼굴 카메라 목록과 함께 설정한다.
    face_identity_url: str = Field(default="")
    # 얼굴 인식은 입구 IDENTITY_ONLY 카메라에서만 한다. 쉼표 구분 camera_id 목록이다.
    face_identity_camera_ids: str = Field(default="")
    face_identity_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    face_identity_jpeg_quality: int = Field(default=95, ge=1, le=100)

    # --- 사람 ByteTrack ---
    person_tracking_enabled: bool = True
    # 비우면 STREAM_SOURCES에서 얼굴 전용 카메라를 뺀 나머지에 적용한다.
    person_tracking_camera_ids: str = Field(default="")
    bytetrack_high_confidence_threshold: float = Field(default=0.5, ge=0, le=1)
    bytetrack_low_confidence_threshold: float = Field(default=0.1, ge=0, le=1)
    bytetrack_new_track_threshold: float = Field(default=0.6, ge=0, le=1)
    bytetrack_first_match_iou_threshold: float = Field(default=0.3, ge=0, le=1)
    bytetrack_second_match_iou_threshold: float = Field(default=0.2, ge=0, le=1)
    bytetrack_buffer_frames: int = Field(default=30, ge=1, le=600)

    # --- 입구 신원 → 교실 CCTV track 인계 ---
    # 카메라 ID와 CCTV 문 영역은 배치마다 달라 .env에서 JSON으로 주입한다.
    identity_handover_routes: str = Field(default="")
    # FastAPI 관리 화면의 저장값을 주기적으로 읽어 실행 중에 ROI를 바꾼다. 조회가
    # 실패하면 위 정적 route를 포함한 직전 정상 설정을 계속 사용한다.
    identity_handover_dynamic_config_enabled: bool = True
    identity_handover_config_refresh_seconds: float = Field(default=5.0, gt=0, le=300)
    identity_handover_config_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    identity_handover_max_delay_seconds: float = Field(default=8.0, gt=0, le=120)
    identity_handover_clock_skew_seconds: float = Field(default=0.5, ge=0, le=10)
    identity_handover_track_stale_seconds: float = Field(default=30.0, gt=0, le=3600)
    # deeplearning이 similarity + margin threshold를 모두 통과시킨 경우에만 student_id를
    # 돌려준다. 여기서 임의의 cosine 임계값을 다시 적용하면 정상 식별을 버리므로 기본은
    # 추가 필터 없음이다. 운영에서 별도 정책이 필요할 때만 0보다 크게 올린다.
    identity_handover_min_confidence: float = Field(default=0.0, ge=0, le=1)

    @property
    def parsed_face_identity_camera_ids(self) -> frozenset[str]:
        return frozenset(
            value.strip()
            for value in self.face_identity_camera_ids.split(",")
            if value.strip()
        )

    @property
    def parsed_person_tracking_camera_ids(self) -> frozenset[str] | None:
        parsed = frozenset(
            value.strip()
            for value in self.person_tracking_camera_ids.split(",")
            if value.strip()
        )
        return parsed or None

    @property
    def parsed_identity_handover_routes(self) -> tuple[IdentityHandoverRoute, ...]:
        return parse_identity_handover_routes(self.identity_handover_routes)

    @model_validator(mode="after")
    def _validate_face_identity_contract(self) -> Self:
        if self.face_identity_url.strip() and not self.parsed_face_identity_camera_ids:
            raise ValueError(
                "FACE_IDENTITY_URL을 설정하면 FACE_IDENTITY_CAMERA_IDS가 필요합니다."
            )
        if self.parsed_face_identity_camera_ids and not self.face_identity_url.strip():
            raise ValueError(
                "FACE_IDENTITY_CAMERA_IDS를 설정하면 FACE_IDENTITY_URL이 필요합니다."
            )
        if (
            self.bytetrack_low_confidence_threshold
            > self.bytetrack_high_confidence_threshold
        ):
            raise ValueError(
                "BYTETRACK_LOW_CONFIDENCE_THRESHOLD는 HIGH보다 클 수 없습니다."
            )
        if (
            self.bytetrack_new_track_threshold
            < self.bytetrack_high_confidence_threshold
        ):
            raise ValueError("BYTETRACK_NEW_TRACK_THRESHOLD는 HIGH 이상이어야 합니다.")
        routes = self.parsed_identity_handover_routes
        if routes and not self.person_tracking_enabled:
            raise ValueError(
                "신원 인계를 켜려면 PERSON_TRACKING_ENABLED=true여야 합니다."
            )
        if routes and not self.face_identity_url.strip():
            raise ValueError(
                "신원 인계 route를 설정하면 FACE_IDENTITY_URL이 필요합니다."
            )
        missing_entry_ids = {
            route.entry_camera_id for route in routes
        } - self.parsed_face_identity_camera_ids
        if missing_entry_ids:
            raise ValueError(
                "신원 인계 route의 입구 카메라는 FACE_IDENTITY_CAMERA_IDS에 있어야 합니다: "
                + ", ".join(sorted(missing_entry_ids))
            )
        tracking_ids = self.parsed_person_tracking_camera_ids
        if tracking_ids is not None:
            overlap = tracking_ids & self.parsed_face_identity_camera_ids
            if overlap:
                raise ValueError(
                    "얼굴 전용 카메라와 사람 추적 카메라는 겹칠 수 없습니다: "
                    + ", ".join(sorted(overlap))
                )
            missing_classroom_ids = {
                route.classroom_camera_id for route in routes
            } - tracking_ids
            if missing_classroom_ids:
                raise ValueError(
                    "신원 인계 route의 교실 카메라는 PERSON_TRACKING_CAMERA_IDS에 "
                    "있어야 합니다: " + ", ".join(sorted(missing_classroom_ids))
                )
        if (
            routes
            and self.identity_handover_track_stale_seconds
            <= self.identity_handover_max_delay_seconds
            + self.identity_handover_clock_skew_seconds
        ):
            raise ValueError(
                "IDENTITY_HANDOVER_TRACK_STALE_SECONDS는 MAX_DELAY와 CLOCK_SKEW의 "
                "합보다 길어야 합니다."
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return customise_sources_with_yaml(
            settings_cls,
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )
