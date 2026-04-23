"""Goal Manager — persistent goal state."""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_GOAL_FILE = "goal.json"


def set_goal(goal_text: str, max_iterations: int = 20,
           max_runtime: int = 1800, base_dir=None) -> str:
    """Create or replace the active goal."""
    if base_dir is None:
        base_dir = Path.cwd()

    goal_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    goal = {
        "version": "1.0.0",
        "goal_id": goal_id,
        "goal": goal_text,
        "status": "active",
        "progress": 0.0,
        "created_at": now,
        "updated_at": now,
        "iterations": 0,
        "max_iterations": max_iterations,
        "max_runtime_seconds": max_runtime,
        "tasks_generated": 0,
        "tasks_completed": 0,
        "tasks_failed": 0,
        "history": [],
    }

    _save_goal(goal, base_dir)
    return goal_id


def get_goal(base_dir=None) -> Optional[dict]:
    """Load current goal from disk."""
    if base_dir is None:
        base_dir = Path.cwd()
    return _load_goal(base_dir)


def update_progress(progress: float, base_dir=None) -> None:
    """Update goal progress."""
    if base_dir is None:
        base_dir = Path.cwd()

    goal = _load_goal(base_dir)
    if not goal:
        return

    goal["progress"] = max(0.0, min(1.0, progress))
    goal["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_goal(goal, base_dir)


def add_history(entry: dict, base_dir=None) -> None:
    """Append an iteration record to history."""
    if base_dir is None:
        base_dir = Path.cwd()

    goal = _load_goal(base_dir)
    if not goal:
        return

    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    goal["history"].append(entry)
    goal["iterations"] = len(goal["history"])
    goal["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_goal(goal, base_dir)


def increment_stats(generated: int = 0, completed: int = 0,
                 failed: int = 0, base_dir=None) -> None:
    """Update task counters."""
    if base_dir is None:
        base_dir = Path.cwd()

    goal = _load_goal(base_dir)
    if not goal:
        return

    goal["tasks_generated"] = goal.get("tasks_generated", 0) + generated
    goal["tasks_completed"] = goal.get("tasks_completed", 0) + completed
    goal["tasks_failed"] = goal.get("tasks_failed", 0) + failed
    goal["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_goal(goal, base_dir)


def mark_complete(reason: str = "", base_dir=None) -> None:
    """Mark goal as completed."""
    if base_dir is None:
        base_dir = Path.cwd()

    goal = _load_goal(base_dir)
    if not goal:
        return

    goal["status"] = "completed"
    goal["progress"] = 1.0
    goal["updated_at"] = datetime.now(timezone.utc).isoformat()
    add_history({"action": "completed", "detail": reason}, base_dir)
    _save_goal(goal, base_dir)


def mark_failed(reason: str = "", base_dir=None) -> None:
    """Mark goal as failed."""
    if base_dir is None:
        base_dir = Path.cwd()

    goal = _load_goal(base_dir)
    if not goal:
        return

    goal["status"] = "failed"
    goal["updated_at"] = datetime.now(timezone.utc).isoformat()
    add_history({"action": "failed", "detail": reason}, base_dir)
    _save_goal(goal, base_dir)


def is_active(base_dir=None) -> bool:
    """Check if there's an active goal."""
    if base_dir is None:
        base_dir = Path.cwd()

    goal = _load_goal(base_dir)
    return goal is not None and goal.get("status") == "active"


def _load_goal(base_dir) -> Optional[dict]:
    """Load goal from disk."""
    goal_file = Path(str(base_dir)) / ".chotu" / _GOAL_FILE
    if not goal_file.exists():
        return None
    try:
        with open(goal_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _save_goal(goal: dict, base_dir) -> None:
    """Atomic write."""
    chotu_dir = Path(str(base_dir)) / ".chotu"
    chotu_dir.mkdir(parents=True, exist_ok=True)
    goal_file = chotu_dir / _GOAL_FILE
    temp_file = chotu_dir / f"{_GOAL_FILE}.tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(goal, f, indent=2)
    os.replace(str(temp_file), str(goal_file))