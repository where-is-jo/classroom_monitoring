"""활성 얼굴 모델의 readiness와 실제 입구 식별 5초 계약을 검증한다.

출력에는 이미지 경로, 얼굴 관측 세부값, 학생 식별자를 포함하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin
from urllib.request import Request, urlopen

FaceModelName = Literal["arcface", "adaface"]


@dataclass(frozen=True)
class CutoverProbeResult:
    model_name: FaceModelName
    elapsed_seconds: float
    observation_count: int


def _read_json_response(response: Any) -> dict[str, Any]:
    try:
        value = json.loads(response.read())
    except (UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise RuntimeError(
            "얼굴 식별 서버가 유효한 JSON을 반환하지 않았습니다."
        ) from error
    if not isinstance(value, dict):
        raise RuntimeError("얼굴 식별 서버 JSON 형식이 올바르지 않습니다.")
    return value


def verify_cutover_endpoint(
    *,
    base_url: str,
    image_path: Path,
    expected_model: FaceModelName,
    camera_id: str = "entry-camera",
    maximum_elapsed_seconds: float = 5.0,
    opener: Callable[..., Any] = urlopen,
    clock: Callable[[], float] = time.perf_counter,
) -> CutoverProbeResult:
    """readiness가 완전한 활성 모델에 실제 JPEG 요청을 보내 5초 계약을 확인한다."""

    if expected_model not in ("arcface", "adaface"):
        raise ValueError("expected model은 arcface 또는 adaface여야 합니다.")
    if maximum_elapsed_seconds <= 0.0:
        raise ValueError("최대 요청 시간은 0초보다 커야 합니다.")
    if not camera_id.strip() or len(camera_id) > 128:
        raise ValueError("카메라 ID가 올바르지 않습니다.")
    if not image_path.is_file() or image_path.stat().st_size == 0:
        raise ValueError("종단 검증용 JPEG 파일이 없거나 비어 있습니다.")

    normalized_base = base_url.rstrip("/") + "/"
    ready_request = Request(urljoin(normalized_base, "health/ready"), method="GET")
    with opener(ready_request, timeout=maximum_elapsed_seconds) as response:
        ready = _read_json_response(response)
    if (
        ready.get("status") != "ready"
        or ready.get("face_identification") != "ready"
        or ready.get("active_face_model") != expected_model
        or ready.get("missing_gallery_entries") != "0"
    ):
        raise RuntimeError(
            "활성 얼굴 모델 readiness·갤러리 완전성이 전환 조건을 만족하지 않습니다."
        )

    identify_request = Request(
        urljoin(normalized_base, "internal/face-identifications"),
        data=image_path.read_bytes(),
        headers={"Content-Type": "image/jpeg", "X-Camera-ID": camera_id},
        method="POST",
    )
    started = clock()
    with opener(identify_request, timeout=maximum_elapsed_seconds) as response:
        result = _read_json_response(response)
    elapsed = clock() - started
    observations = result.get("observations")
    if not isinstance(observations, list):
        raise RuntimeError("얼굴 식별 응답의 observations 형식이 올바르지 않습니다.")
    if elapsed > maximum_elapsed_seconds:
        raise RuntimeError(
            f"얼굴 식별 요청이 {maximum_elapsed_seconds:.3f}초 제한을 초과했습니다."
        )
    return CutoverProbeResult(expected_model, elapsed, len(observations))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="deeplearning 서버 기준 URL")
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument(
        "--expected-model", required=True, choices=("arcface", "adaface")
    )
    parser.add_argument("--camera-id", default="entry-camera")
    parser.add_argument("--maximum-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)

    result = verify_cutover_endpoint(
        base_url=args.url,
        image_path=args.image,
        expected_model=args.expected_model,
        camera_id=args.camera_id,
        maximum_elapsed_seconds=args.maximum_seconds,
    )
    print(
        "얼굴 식별 종단 검증 통과: "
        f"모델={result.model_name}, "
        f"요청초={result.elapsed_seconds:.3f}, "
        f"관측수={result.observation_count}"
    )
    print("이미지 경로·얼굴 관측 세부값·학생 식별자는 출력하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
