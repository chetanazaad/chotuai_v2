"""Artifact Manager — per-task file registry tracking artifacts produced during execution."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class ArtifactRegistry:
    def __init__(self, task_id: str, runtime_dir: Path):
        self.task_id = task_id
        self.runtime_dir = runtime_dir
        self.artifacts = []
        self._metadata_file = runtime_dir / "artifacts.json"
        self._load()

    def _load(self) -> None:
        if self._metadata_file.exists():
            try:
                with open(self._metadata_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.artifacts = data.get("artifacts", [])
            except (json.JSONDecodeError, OSError):
                self.artifacts = []

    def _save(self) -> None:
        self._metadata_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "task_id": self.task_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "artifacts": self.artifacts
        }
        with open(self._metadata_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def register(
        self,
        file_path: str,
        artifact_type: str = "file",
        step_id: str = "",
        label: str = "",
        size_bytes: Optional[int] = None,
        mime_type: str = "",
        checksum: str = "",
    ) -> str:
        full_path = Path(file_path)
        if size_bytes is None and full_path.exists():
            try:
                size_bytes = full_path.stat().st_size
            except OSError:
                size_bytes = 0

        if not label:
            label = full_path.name

        artifact_id = f"artifact_{len(self.artifacts) + 1:04d}"

        artifact = {
            "artifact_id": artifact_id,
            "file_path": file_path,
            "artifact_type": artifact_type,
            "step_id": step_id,
            "label": label,
            "size_bytes": size_bytes or 0,
            "mime_type": mime_type,
            "checksum": checksum,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }

        existing = [a for a in self.artifacts if a.get("file_path") == file_path]
        if existing:
            idx = self.artifacts.index(existing[0])
            self.artifacts[idx] = artifact
        else:
            self.artifacts.append(artifact)

        self._save()
        return artifact_id

    def unregister(self, file_path: str) -> bool:
        original_len = len(self.artifacts)
        self.artifacts = [a for a in self.artifacts if a.get("file_path") != file_path]
        if len(self.artifacts) < original_len:
            self._save()
            return True
        return False

    def get(self, artifact_id: str) -> Optional[dict]:
        for a in self.artifacts:
            if a.get("artifact_id") == artifact_id:
                return a
        return None

    def get_by_path(self, file_path: str) -> Optional[dict]:
        for a in self.artifacts:
            if a.get("file_path") == file_path:
                return a
        return None

    def list_all(self) -> list:
        return list(self.artifacts)

    def list_by_step(self, step_id: str) -> list:
        return [a for a in self.artifacts if a.get("step_id") == step_id]

    def list_by_type(self, artifact_type: str) -> list:
        return [a for a in self.artifacts if a.get("artifact_type") == artifact_type]

    def clear(self) -> None:
        self.artifacts = []
        self._save()

    def get_stats(self) -> dict:
        total_size = sum(a.get("size_bytes", 0) for a in self.artifacts)
        by_type = {}
        for a in self.artifacts:
            t = a.get("artifact_type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "total_artifacts": len(self.artifacts),
            "total_size_bytes": total_size,
            "by_type": by_type,
        }


_runtime_dir: Optional[Path] = None
_current_registry: Optional[ArtifactRegistry] = None


def init(runtime_dir: Path, task_id: str = "default") -> ArtifactRegistry:
    """Initialize the artifact registry for a task."""
    global _runtime_dir, _current_registry
    _runtime_dir = runtime_dir
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _current_registry = ArtifactRegistry(task_id, runtime_dir)
    return _current_registry


def get_current_registry() -> ArtifactRegistry:
    """Get the current active registry."""
    global _current_registry
    if _current_registry is None:
        raise RuntimeError("Artifact registry not initialized. Call init() first.")
    return _current_registry


def register_artifact(
    file_path: str,
    artifact_type: str = "file",
    step_id: str = "",
    label: str = "",
    size_bytes: Optional[int] = None,
    mime_type: str = "",
    checksum: str = "",
) -> str:
    """Register a file artifact with the current registry."""
    registry = get_current_registry()
    return registry.register(
        file_path=file_path,
        artifact_type=artifact_type,
        step_id=step_id,
        label=label,
        size_bytes=size_bytes,
        mime_type=mime_type,
        checksum=checksum,
    )


def unregister_artifact(file_path: str) -> bool:
    """Remove an artifact from the registry."""
    registry = get_current_registry()
    return registry.unregister(file_path)


def list_artifacts() -> list:
    """List all registered artifacts."""
    registry = get_current_registry()
    return registry.list_all()


def list_artifacts_by_step(step_id: str) -> list:
    """List artifacts registered for a specific step."""
    registry = get_current_registry()
    return registry.list_by_step(step_id)


def get_artifact_stats() -> dict:
    """Get artifact statistics."""
    registry = get_current_registry()
    return registry.get_stats()


def load_for_task(task_id: str, runtime_dir: Path) -> ArtifactRegistry:
    """Load an existing registry for a task."""
    return ArtifactRegistry(task_id, runtime_dir)