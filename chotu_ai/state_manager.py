"""Pure state I/O and validation. Zero business logic."""
import json
import os
import re
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def get_runtime_dir(base_dir: Optional[Path] = None) -> Path:
    """Returns .chotu/ path."""
    if base_dir is None:
        base_dir = Path.cwd()
    return base_dir / ".chotu"


def ensure_runtime_dirs(base_dir: Optional[Path] = None) -> Path:
    """Creates .chotu/, .chotu/logs/, output/tasks/, output/shared/"""
    runtime_dir = get_runtime_dir(base_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "logs").mkdir(parents=True, exist_ok=True)
    output_dir = base_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (output_dir / "shared").mkdir(parents=True, exist_ok=True)
    return runtime_dir


def sanitize_task_name(task_text: str) -> str:
    """Sanitize task text for directory name (max 3 words)."""
    import re
    # Remove non-alphanumeric, keep spaces
    clean = re.sub(r'[^a-zA-Z0-9\s]', '', task_text)
    words = clean.split()[:3]
    return "_".join(words).lower()


def get_task_hash(task_description: str) -> str:
    """Compute deterministic hash for task deduplication."""
    normalized = re.sub(r'\s+', ' ', task_description.lower()).strip()
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def create_fresh_state(core_task: str, working_dir: Optional[str] = None) -> dict:
    """Factory for a brand-new state."""
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()
    
    # STEP 2: GENERATE TASK ID
    task_name = sanitize_task_name(core_task)
    timestamp = now.strftime("%Y%m%d_%H%M")
    task_id = f"{task_name}_{timestamp}"
    
    # STEP 3: DEFINE TASK DIRECTORY
    output_root = "output"
    task_output_dir = os.path.join(output_root, task_id)

    return {
        "version": "1.0.0",
        "project_id": str(uuid.uuid4()),
        "created_at": now_str,
        "updated_at": now_str,
        "core_task": {
            "description": core_task,
            "status": "pending",
            "accepted_at": None,
            "task_id": task_id,
            "output_dir": task_output_dir
        },
        "todo_list": [],
        "current_step": None,
        "completed_steps": [],
        "issues": [],
        "resolutions": [],
        "decisions": [],
        "stats": {
            "total_steps": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "in_progress": 0,
            "pending": 0,
            "total_retries": 0,
            "total_issues": 0,
            "total_resolutions": 0
        },
        "config": {
            "max_retries_per_step": 3,
            "max_total_retries": 15,
            "step_timeout_seconds": 60,
            "working_directory": working_dir or ".",
            "runtime_dir": ".chotu"
        },
        "llm_usage": {
            "calls": 0,
            "success": 0
        }
    }


def load(base_dir: Optional[Path] = None) -> Optional[dict]:
    """Load state from state.json. Returns None if no state exists."""
    runtime_dir = get_runtime_dir(base_dir)
    state_file = runtime_dir / "state.json"
    if not state_file.exists():
        return None
    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)
    valid, errors = validate(state)
    if not valid:
        raise ValueError(f"Loaded state failed validation: {errors}")
    return state


def save(state: dict, base_dir: Optional[Path] = None) -> None:
    """Validate + atomic write (temp → rename)."""
    valid, errors = validate(state)
    if not valid:
        raise ValueError(f"Cannot save invalid state: {errors}")
    runtime_dir = get_runtime_dir(base_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    temp_file = runtime_dir / "state.json.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(temp_file, runtime_dir / "state.json")


def validate(state: dict) -> tuple[bool, list[str]]:
    """Schema validation, returns (valid, errors)."""
    errors = []
    required_fields = ["version", "project_id", "created_at", "updated_at",
                       "core_task", "todo_list", "current_step", "completed_steps",
                       "issues", "resolutions", "decisions", "stats", "config"]
    for field in required_fields:
        if field not in state:
            errors.append(f"Missing required field: {field}")
    if "version" in state and state["version"] != "1.0.0":
        errors.append(f"Unsupported version: {state['version']}")
    if "core_task" in state:
        ct = state["core_task"]
        if not isinstance(ct, dict):
            errors.append("core_task must be a dict")
        elif "description" not in ct or "status" not in ct:
            errors.append("core_task must have description and status")
    if "todo_list" in state and not isinstance(state["todo_list"], list):
        errors.append("todo_list must be a list")
    if "stats" in state:
        stats = state["stats"]
        required_stats = ["total_steps", "completed", "failed", "skipped",
                          "in_progress", "pending", "total_retries",
                          "total_issues", "total_resolutions"]
        for field in required_stats:
            if field not in stats:
                errors.append(f"Missing stats field: {field}")
    return len(errors) == 0, errors


def recompute_stats(state: dict) -> dict:
    """Recompute stats from todo_list."""
    todo_list = state.get("todo_list", [])
    stats = {
        "total_steps": len(todo_list),
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "in_progress": 0,
        "pending": 0,
        "total_retries": 0,
        "total_issues": state.get("stats", {}).get("total_issues", 0),
        "total_resolutions": state.get("stats", {}).get("total_resolutions", 0)
    }
    for step in todo_list:
        status = step.get("status", "pending")
        if status == "completed":
            stats["completed"] += 1
        elif status == "failed":
            stats["failed"] += 1
        elif status == "skipped":
            stats["skipped"] += 1
        elif status == "executing" or status == "evaluating" or status == "generating":
            stats["in_progress"] += 1
        elif status == "pending":
            stats["pending"] += 1
        stats["total_retries"] += step.get("retries", 0)
    return stats


def create_step(step_id: str, description: str, depends_on: list = None,
              expected_outcome: str = "", max_retries: int = 3) -> dict:
    """Step factory."""
    return {
        "id": step_id,
        "description": description,
        "status": "pending",
        "depends_on": depends_on or [],
        "action": None,
        "result": None,
        "retries": 0,
        "max_retries": max_retries,
        "expected_outcome": expected_outcome,
        "estimated_effort": None
    }