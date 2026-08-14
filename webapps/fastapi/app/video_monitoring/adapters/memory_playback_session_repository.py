"""In-memory playback session repository."""

from __future__ import annotations

from ..models import PlaybackSession


class MemoryPlaybackSessionRepository:
    """In-memory playback session repository.

    단일 프로세스·local/개발용이다. 다중 fastapi 프로세스가 되면 세션 저장소
    공유 방식을 다시 정한다(결정 0014 남은 일).
    """

    def __init__(self) -> None:
        self._sessions: dict[str, PlaybackSession] = {}

    def save(self, session: PlaybackSession) -> PlaybackSession:
        """Save session (replace by session_id)."""
        self._sessions[session.session_id] = session
        return session

    def find_by_id(self, session_id: str) -> PlaybackSession | None:
        """Find session by ID."""
        return self._sessions.get(session_id)

    def delete_by_id(self, session_id: str) -> bool:
        """Delete session and return whether it existed."""
        return self._sessions.pop(session_id, None) is not None
