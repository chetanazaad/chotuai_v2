"""Task Worker — Controller wrapper for queue execution."""
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path


def run_next(base_dir=None) -> bool:
    """Run the next task from the queue."""
    from . import scheduler, task_queue, task_registry, logger, ui_renderer

    if base_dir is None:
        base_dir = Path.cwd()

    decision = scheduler.select_next(base_dir)
    logger.log_scheduler_select(decision.task_id, decision.reason)

    if not decision.has_work:
        ui_renderer.render_message("info", f"No tasks to run: {decision.reason}")
        return False

    for skipped in decision.skipped:
        logger.log_scheduler_skip(skipped["task_id"], skipped["reason"])

    return _execute_task(decision.task_id, decision.description, base_dir)


def run_all(base_dir=None) -> dict:
    """Run all pending tasks sequentially."""
    from . import scheduler, task_queue, ui_renderer

    if base_dir is None:
        base_dir = Path.cwd()

    _check_stale_backup(base_dir)

    results = {"total": 0, "completed": 0, "failed": 0}

    while True:
        decision = scheduler.select_next(base_dir)
        if not decision.has_work:
            break

        results["total"] += 1
        ui_renderer.render_message("info",
            f"[{results['total']}] Running: {decision.description[:60]} [{decision.priority}]")

        success = _execute_task(decision.task_id, decision.description, base_dir)

        if success:
            results["completed"] += 1
        else:
            results["failed"] += 1

    return results


def run_task(task_id: str, base_dir=None) -> bool:
    """Run a specific task by its queue ID."""
    from . import task_queue

    if base_dir is None:
        base_dir = Path.cwd()

    tasks = task_queue.list_tasks(base_dir)
    task = next((t for t in tasks if t["task_id"] == task_id), None)

    if not task:
        return False

    return _execute_task(task_id, task["description"], base_dir)


def _check_stale_backup(base_dir) -> None:
    """Check for stale backup from crashed queue run."""
    from . import task_queue

    chotu_dir = Path(str(base_dir)) / ".chotu"
    backup_file = chotu_dir / "state.json.bak"
    state_file = chotu_dir / "state.json"

    if backup_file.exists():
        try:
            if state_file.exists():
                shutil.copy2(str(state_file), str(backup_file))
            else:
                shutil.copy2(str(backup_file), str(state_file))
            backup_file.unlink(missing_ok=True)
        except Exception:
            pass

        tasks = task_queue.list_tasks(base_dir, status_filter="running")
        for task in tasks:
            task_queue.update_status(task["task_id"], "failed",
                                    error="Interrupted by crash", base_dir=base_dir)


def _execute_task(task_id: str, description: str, base_dir) -> bool:
    """Execute a single task with full state isolation."""
    from . import controller, task_queue, task_registry, logger, ui_renderer

    chotu_dir = Path(str(base_dir)) / ".chotu"
    state_file = chotu_dir / "state.json"
    backup_file = chotu_dir / "state.json.bak"

    task_queue.update_status(task_id, "running", base_dir=base_dir)
    task_registry.update_task(task_id, {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }, base_dir)
    logger.log_queue_start(task_id, description)

    has_backup = False
    try:
        if state_file.exists():
            shutil.copy2(str(state_file), str(backup_file))
            has_backup = True
            logger.log_worker_isolate(task_id, "backup_state")
    except Exception:
        pass

    start_time = time.time()
    success = False
    error_msg = ""

    try:
        success = controller.handle_command("new", {
            "task": description,
            "working_dir": None,
            "auto_run": True,
            "step_id": None,
        })
    except Exception as e:
        success = False
        error_msg = str(e)

    duration = int(time.time() - start_time)

    result_summary = ""
    steps_total = 0
    steps_completed = 0
    steps_failed = 0

    try:
        if state_file.exists():
            with open(state_file, "r", encoding="utf-8") as f:
                task_state = json.load(f)

            stats = task_state.get("stats", {})
            steps_total = stats.get("total_steps", 0)
            steps_completed = stats.get("completed", 0)
            steps_failed = stats.get("failed", 0)
            task_status = task_state.get("core_task", {}).get("status", "unknown")

            if task_status == "completed":
                success = True
                result_summary = f"Completed: {steps_completed}/{steps_total} steps in {duration}s"
            else:
                success = False
                result_summary = f"Failed: {steps_completed}/{steps_total} steps, {steps_failed} failed"
                if not error_msg:
                    error_msg = f"Task ended with status: {task_status}"
    except Exception:
        pass

    try:
        archive_dir = chotu_dir / "queue" / "states" / task_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / "state.json"

        if state_file.exists():
            shutil.copy2(str(state_file), str(archive_path))
            logger.log_worker_archive(task_id, str(archive_path))
    except Exception:
        archive_path = ""

    try:
        if has_backup and backup_file.exists():
            shutil.copy2(str(backup_file), str(state_file))
            backup_file.unlink(missing_ok=True)
            logger.log_worker_isolate(task_id, "restore_state")
        elif state_file.exists():
            state_file.unlink(missing_ok=True)
            logger.log_worker_isolate(task_id, "clean_state")
    except Exception:
        pass

    final_status = "completed" if success else "failed"
    task_queue.update_status(task_id, final_status, result_summary, error_msg, base_dir)
    task_registry.update_task(task_id, {
        "status": final_status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration,
        "steps_total": steps_total,
        "steps_completed": steps_completed,
        "steps_failed": steps_failed,
        "state_archive_path": str(archive_path) if archive_path else "",
        "result_summary": result_summary,
    }, base_dir)

    if success:
        logger.log_queue_complete(task_id, "completed")
        ui_renderer.render_message("success",
            f"Task {task_id} completed: {result_summary}")
    else:
        logger.log_queue_failed(task_id, error_msg)
        ui_renderer.render_message("error",
            f"Task {task_id} failed: {error_msg[:100]}")

    return success