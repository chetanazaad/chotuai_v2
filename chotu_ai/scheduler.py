"""Scheduler — priority-based task selection."""
import dataclasses
from pathlib import Path


@dataclasses.dataclass
class SchedulerDecision:
    task_id: str
    description: str
    priority: str
    reason: str
    skipped: list
    has_work: bool


def select_next(base_dir=None) -> SchedulerDecision:
    """Select next task from queue."""
    from . import task_queue

    if base_dir is None:
        base_dir = Path.cwd()

    tasks = task_queue.list_tasks(base_dir, status_filter="pending")

    if not tasks:
        return SchedulerDecision(
            task_id="", description="", priority="",
            reason="No pending tasks in queue",
            skipped=[], has_work=False
        )

    priority_order = {"high": 0, "normal": 1, "low": 2}
    tasks.sort(key=lambda t: (
        priority_order.get(t["priority"], 1),
        t["created_at"]
    ))

    skipped = []
    for task in tasks:
        if task.get("retries", 0) >= task.get("max_retries", 2):
            skipped.append({
                "task_id": task["task_id"],
                "reason": f"Exceeded max retries ({task['retries']}/{task['max_retries']})"
            })
            task_queue.update_status(
                task["task_id"], "failed",
                error="Max retries exceeded", base_dir=base_dir
            )
            continue

        return SchedulerDecision(
            task_id=task["task_id"],
            description=task["description"],
            priority=task["priority"],
            reason=f"Highest priority pending task ({task['priority']})",
            skipped=skipped,
            has_work=True,
        )

    return SchedulerDecision(
        task_id="", description="", priority="",
        reason="All pending tasks exceeded retry limits",
        skipped=skipped, has_work=False,
    )