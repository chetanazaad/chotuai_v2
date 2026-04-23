"""Task Registry — historical task records."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_REGISTRY_FILE = "task_registry.json"


def register_task(task_id: str, description: str, priority: str = "normal",
              source: str = "user", base_dir=None) -> None:
    """Register a new task in the history."""
    if base_dir is None:
        base_dir = Path.cwd()

    registry = _load_registry(base_dir)
    now = datetime.now(timezone.utc).isoformat()

    entry = {
        "task_id": task_id,
        "description": description,
        "status": "registered",
        "priority": priority,
        "source": source,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "duration_seconds": 0,
        "steps_total": 0,
        "steps_completed": 0,
        "steps_failed": 0,
        "state_archive_path": "",
        "result_summary": "",
    }

    registry["tasks"].append(entry)
    _save_registry(registry, base_dir)


def update_task(task_id: str, updates: dict, base_dir=None) -> bool:
    """Update task fields in the registry."""
    if base_dir is None:
        base_dir = Path.cwd()

    registry = _load_registry(base_dir)
    for task in registry["tasks"]:
        if task["task_id"] == task_id:
            for key, value in updates.items():
                task[key] = value
            _save_registry(registry, base_dir)
            return True
    return False


def get_task(task_id: str, base_dir=None) -> Optional[dict]:
    """Get a single task record."""
    if base_dir is None:
        base_dir = Path.cwd()

    registry = _load_registry(base_dir)
    for task in registry["tasks"]:
        if task["task_id"] == task_id:
            return task
    return None


def list_history(base_dir=None, limit: int = 20) -> list:
    """List recent tasks, newest first."""
    if base_dir is None:
        base_dir = Path.cwd()

    registry = _load_registry(base_dir)
    tasks = registry.get("tasks", [])
    tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return tasks[:limit]


def get_stats(base_dir=None) -> dict:
    """Overall statistics."""
    if base_dir is None:
        base_dir = Path.cwd()

    registry = _load_registry(base_dir)
    tasks = registry.get("tasks", [])

    total = len(tasks)
    completed = sum(1 for t in tasks if t["status"] == "completed")
    failed = sum(1 for t in tasks if t["status"] == "failed")
    success_rate = (completed / total * 100) if total > 0 else 0

    return {
        "total_tasks": total,
        "completed": completed,
        "failed": failed,
        "success_rate": round(success_rate, 1),
    }


def _load_registry(base_dir) -> dict:
    """Load registry from disk."""
    registry_file = Path(str(base_dir)) / ".chotu" / _REGISTRY_FILE
    if not registry_file.exists():
        return {"version": "1.0.0", "tasks": []}
    try:
        with open(registry_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"version": "1.0.0", "tasks": []}


def _save_registry(registry: dict, base_dir) -> None:
    """Atomic write."""
    chotu_dir = Path(str(base_dir)) / ".chotu"
    chotu_dir.mkdir(parents=True, exist_ok=True)
    registry_file = chotu_dir / _REGISTRY_FILE
    temp_file = chotu_dir / f"{_REGISTRY_FILE}.tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
    os.replace(str(temp_file), str(registry_file))