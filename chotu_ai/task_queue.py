"""Task Queue — persistent task queue management."""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_QUEUE_FILE = "task_queue.json"


def add_task(description: str, priority: str = "normal",
           source: str = "user", base_dir=None) -> str:
    """Add a task to the queue. Returns task_id."""
    if base_dir is None:
        base_dir = Path.cwd()

    if priority not in ("high", "normal", "low"):
        priority = "normal"

    task_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    entry = {
        "task_id": task_id,
        "description": description,
        "priority": priority,
        "status": "pending",
        "source": source,
        "created_at": now,
        "updated_at": now,
        "retries": 0,
        "max_retries": 2,
        "result_summary": "",
        "error": "",
    }

    queue = _load_queue(base_dir)
    queue["tasks"].append(entry)
    _save_queue(queue, base_dir)

    return task_id


def get_next_task(base_dir=None) -> Optional[dict]:
    """Get highest-priority pending task."""
    if base_dir is None:
        base_dir = Path.cwd()

    queue = _load_queue(base_dir)
    pending = [t for t in queue["tasks"] if t["status"] == "pending"]

    if not pending:
        return None

    priority_order = {"high": 0, "normal": 1, "low": 2}
    pending.sort(key=lambda t: (
        priority_order.get(t["priority"], 1),
        t["created_at"]
    ))

    return pending[0]


def update_status(task_id: str, status: str,
               result_summary: str = "", error: str = "",
               base_dir=None) -> bool:
    """Update task status in the queue."""
    if base_dir is None:
        base_dir = Path.cwd()

    queue = _load_queue(base_dir)
    now = datetime.now(timezone.utc).isoformat()

    for task in queue["tasks"]:
        if task["task_id"] == task_id:
            task["status"] = status
            task["updated_at"] = now
            if result_summary:
                task["result_summary"] = result_summary
            if error:
                task["error"] = error
            if status == "failed":
                task["retries"] = task.get("retries", 0) + 1
            _save_queue(queue, base_dir)
            return True
    return False


def list_tasks(base_dir=None, status_filter: str = "") -> list:
    """List all tasks, optionally filtered by status."""
    if base_dir is None:
        base_dir = Path.cwd()

    queue = _load_queue(base_dir)
    tasks = queue.get("tasks", [])

    if status_filter:
        tasks = [t for t in tasks if t["status"] == status_filter]

    return tasks


def remove_task(task_id: str, base_dir=None) -> bool:
    """Remove a task from the queue."""
    if base_dir is None:
        base_dir = Path.cwd()

    queue = _load_queue(base_dir)
    original_len = len(queue["tasks"])
    queue["tasks"] = [t for t in queue["tasks"] if t["task_id"] != task_id]

    if len(queue["tasks"]) < original_len:
        _save_queue(queue, base_dir)
        return True
    return False


def clear_completed(base_dir=None) -> int:
    """Remove completed and failed tasks. Returns count."""
    if base_dir is None:
        base_dir = Path.cwd()

    queue = _load_queue(base_dir)
    original = len(queue["tasks"])
    queue["tasks"] = [t for t in queue["tasks"] if t["status"] not in ("completed", "failed")]
    removed = original - len(queue["tasks"])
    _save_queue(queue, base_dir)
    return removed


def count_by_status(base_dir=None) -> dict:
    """Count tasks by status."""
    if base_dir is None:
        base_dir = Path.cwd()

    queue = _load_queue(base_dir)
    counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
    for t in queue.get("tasks", []):
        status = t.get("status", "pending")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _load_queue(base_dir) -> dict:
    """Load queue from disk."""
    queue_file = Path(str(base_dir)) / ".chotu" / _QUEUE_FILE
    if not queue_file.exists():
        return {"version": "1.0.0", "tasks": []}
    try:
        with open(queue_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"version": "1.0.0", "tasks": []}


def _save_queue(queue: dict, base_dir) -> None:
    """Atomic write."""
    chotu_dir = Path(str(base_dir)) / ".chotu"
    chotu_dir.mkdir(parents=True, exist_ok=True)
    queue_file = chotu_dir / _QUEUE_FILE
    temp_file = chotu_dir / f"{_QUEUE_FILE}.tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2)
    os.replace(str(temp_file), str(queue_file))