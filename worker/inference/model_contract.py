"""호환 import: 실제 계약 구현은 여러 worker가 쓰는 shared에 둔다."""

from shared.person_model_contract import (
    ORIGINAL_FRAME_METHOD,
    verify_person_model_contract,
)

__all__ = ["ORIGINAL_FRAME_METHOD", "verify_person_model_contract"]
