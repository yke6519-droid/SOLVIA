"""Short-lived in-process storage for the first DatasetArtifact closure."""

from threading import RLock

from backend.app.charting.errors import ChartValidationError
from backend.app.charting.schemas import DatasetArtifact


class ArtifactStore:
    def __init__(self):
        self._items: dict[str, DatasetArtifact] = {}
        self._lock = RLock()

    def save(self, artifact: DatasetArtifact) -> DatasetArtifact:
        with self._lock:
            self._items[artifact.artifact_id] = artifact
        return artifact

    def get(self, artifact_id: str, *, user_id: int, session_id: str) -> DatasetArtifact:
        with self._lock:
            artifact = self._items.get(artifact_id)
        if artifact is None:
            raise ChartValidationError("ARTIFACT_NOT_FOUND", "图表数据制品不存在或已过期")
        if artifact.owner_user_id != user_id:
            raise ChartValidationError("ARTIFACT_FORBIDDEN", "无权访问该图表数据制品")
        if artifact.session_id != session_id:
            raise ChartValidationError("ARTIFACT_SESSION_MISMATCH", "图表数据制品不属于当前会话")
        return artifact

    def clear_session(self, session_id: str, user_id: int) -> None:
        with self._lock:
            self._items = {
                key: value
                for key, value in self._items.items()
                if value.session_id != session_id or value.owner_user_id != user_id
            }


artifact_store = ArtifactStore()
