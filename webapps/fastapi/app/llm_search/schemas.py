"""자연어 검색 HTTP 스키마.

응답 래퍼를 쓰지 않는다. 목록은 `items/total/limit`이고 시각은 ISO 8601 UTC다
(docs/conventions/api-convention.md). 커서·offset을 두지 않는 이유는 카메라 여러
대의 결과를 합치기 때문이다 — 기존 커서는 이벤트 하나의 식별자라 병합한 목록에서는
의미를 잃는다.

**모델이 만든 계획을 응답에 그대로 싣는다.** 자연어 검색은 왜 이 결과가 나왔는지
사용자가 확인할 수 없으면 신뢰할 수 없다. 규칙 기반 검색이 `match_reason`을
노출하는 것과 같은 이유다. 단, 여기 실리는 것은 **검증을 통과한 값**뿐이고
모델 원문은 절대 나가지 않는다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import DetectionHit, IdentifiedStudent, SearchOutcome, SearchQuery
from .planning import MAX_LIMIT


class LlmSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=200)
    limit: int = Field(
        default=20,
        ge=1,
        le=MAX_LIMIT,
        description="결과 수의 상한. 모델이 더 큰 수를 내도 이 값을 넘지 않는다.",
    )


class SearchPlanResponse(BaseModel):
    """모델이 만들고 서버가 검증한 검색 조건."""

    intent: str
    camera_id: str | None
    classroom_id: str | None
    # 응답 전용 모델이라 `alias`가 아니라 `serialization_alias`를 쓴다. 응답에는
    # from/to로 나가고 파이썬 쪽에서는 예약어를 피한 이름을 그대로 쓸 수 있다.
    from_at: datetime = Field(serialization_alias="from")
    to_at: datetime = Field(serialization_alias="to")
    limit: int
    notes: list[str] = Field(
        description="요청을 조정했거나 대상을 찾지 못한 사유. 사용자에게 그대로 보여준다."
    )

    @classmethod
    def from_domain(cls, query: SearchQuery) -> SearchPlanResponse:
        return cls(
            intent="detection_search",
            camera_id=query.camera_id,
            classroom_id=query.classroom_id,
            from_at=query.from_at,
            to_at=query.to_at,
            limit=query.limit,
            notes=list(query.notes),
        )


class IdentifiedStudentResponse(BaseModel):
    student_id: str
    identity_confidence: float | None

    @classmethod
    def from_domain(cls, student: IdentifiedStudent) -> IdentifiedStudentResponse:
        return cls(
            student_id=student.student_id,
            identity_confidence=student.identity_confidence,
        )


class DetectionHitResponse(BaseModel):
    event_id: str
    camera_id: str
    resolved_classroom_id: str = Field(
        description="카메라 등록 정보로 지금 기준에서 되짚은 강의실. 탐지 시점의 값이 아니다."
    )
    captured_at: datetime
    detection_count: int
    identified: list[IdentifiedStudentResponse] = Field(
        description="신원이 붙은 탐지. 얼굴 인식이 연결되기 전에는 항상 비어 있다."
    )
    unidentified_count: int
    snapshot_key: str | None = Field(description="저장소에 실제로 있는 스냅샷 키. 없으면 null이다.")
    image_path: str | None

    @classmethod
    def from_domain(cls, hit: DetectionHit) -> DetectionHitResponse:
        return cls(
            event_id=hit.event_id,
            camera_id=hit.camera_id,
            resolved_classroom_id=hit.resolved_classroom_id,
            captured_at=hit.captured_at,
            detection_count=hit.detection_count,
            identified=[IdentifiedStudentResponse.from_domain(s) for s in hit.identified],
            unidentified_count=hit.unidentified_count,
            snapshot_key=hit.snapshot_key,
            image_path=(
                f"/api/v1/snapshots/image/{hit.snapshot_key}"
                if hit.snapshot_key is not None
                else None
            ),
        )


class LlmSearchResponse(BaseModel):
    question: str
    plan: SearchPlanResponse
    items: list[DetectionHitResponse]
    total: int = Field(
        description="돌려준 건수다. 조건에 맞는 전체 건수가 아니다 — truncated를 함께 본다."
    )
    limit: int
    truncated: bool = Field(description="상한에 걸려 결과가 잘렸는지. 참이면 이것이 전부가 아니다.")
    snapshot_lookup_failed: bool = Field(
        description="스냅샷 저장소 조회 실패 여부. 참이면 이미지 없음이 아니라 확인 실패다."
    )

    @classmethod
    def from_domain(cls, question: str, outcome: SearchOutcome) -> LlmSearchResponse:
        items = [DetectionHitResponse.from_domain(hit) for hit in outcome.hits]
        return cls(
            question=question,
            plan=SearchPlanResponse.from_domain(outcome.query),
            items=items,
            total=len(items),
            limit=outcome.query.limit,
            truncated=outcome.truncated,
            snapshot_lookup_failed=outcome.snapshot_lookup_failed,
        )
