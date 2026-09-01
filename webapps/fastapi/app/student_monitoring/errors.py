"""Student monitoring domain errors."""

from ..shared.errors import DomainError


class InferenceEventConflictError(DomainError):
    """Same event_id with different body."""

    code = "INFERENCE_EVENT_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("Same event ID has different detection results.")


class RepositoryError(DomainError):
    """Storage access failure."""

    code = "REPOSITORY_ERROR"
    status_code = 503

    def __init__(self, message: str = "Storage is unavailable.") -> None:
        super().__init__(message)
