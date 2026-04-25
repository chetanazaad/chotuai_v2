import json
import os
from datetime import datetime
from pathlib import Path

TASKS_FILE = ".chotu/tasks.json"

def _load_tasks():
    if not os.path.exists(TASKS_FILE):
        return []
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save_tasks(tasks):
    os.makedirs(".chotu", exist_ok=True)
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2)

def add_task(task_id, task_name, output_dir, task_hash=""):
    """Add a new task to the index."""
    tasks = _load_tasks()
    
    # Check if task already exists
    for t in tasks:
        if t["task_id"] == task_id:
            return

    new_task = {
        "task_id": task_id,
        "task_name": task_name,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "output_dir": output_dir,
        "task_hash": task_hash,
        "status": "running"
    }
    tasks.append(new_task)
    _save_tasks(tasks)
    print(f"[TASK INDEX] Added task: {task_id}")


def get_task_by_hash(task_hash: str):
    """Find existing task by hash, return first match or None."""
    if not task_hash:
        return None
    tasks = _load_tasks()
    for t in tasks:
        if t.get("task_hash") == task_hash:
            return t
    return None

def update_status(task_id, status):
    """Update task status in the index."""
    tasks = _load_tasks()
    updated = False
    for t in tasks:
        if t["task_id"] == task_id:
            if t["status"] != status:
                t["status"] = status
                updated = True
            break
    
    if updated:
        _save_tasks(tasks)
        print(f"[TASK INDEX] Updated status: {task_id} -> {status}")

def list_tasks():
    """Return list of tasks."""
    return _load_tasks()

def get_task_by_index(index):
    """Return task at 1-based index."""
    tasks = _load_tasks()
    if 1 <= index <= len(tasks):
        return tasks[index-1]
    return None
