"""Autonomous Runner — The main autonomous loop."""
import time
from datetime import datetime, timezone
from pathlib import Path


_stop_flag = False
_SLEEP_BETWEEN_ITERATIONS = 2


def start(base_dir=None) -> dict:
    """Start the autonomous execution loop."""
    global _stop_flag
    _stop_flag = False

    from . import (goal_manager, task_generator, task_queue,
                   task_worker, progress_evaluator, logger, ui_renderer)

    if base_dir is None:
        base_dir = Path.cwd()

    goal = goal_manager.get_goal(base_dir)
    if not goal or goal.get("status") != "active":
        ui_renderer.render_message("error", "No active goal. Use 'chotu goal set \"...\"' first.")
        return {"success": False, "reason": "No active goal"}

    goal_text = goal["goal"]
    goal_id = goal["goal_id"]
    max_iterations = goal.get("max_iterations", 20)
    max_runtime = goal.get("max_runtime_seconds", 1800)

    logger.log_auto_start(goal_id)
    ui_renderer.render_goal_status(goal)
    ui_renderer.render_message("info", "Autonomous mode started")

    start_time = time.time()
    iteration = goal.get("iterations", 0)
    no_progress_count = 0
    completed_summaries = []
    summary = {"iterations": 0, "tasks_total": 0, "tasks_completed": 0, "tasks_failed": 0}

    while True:
        iteration += 1

        if _stop_flag:
            logger.log_auto_stop("User requested stop")
            goal_manager.mark_failed("User stopped", base_dir)
            ui_renderer.render_message("warning", "Autonomous mode stopped by user")
            break

        if iteration > max_iterations:
            logger.log_auto_stop(f"Max iterations reached ({max_iterations})")
            goal_manager.mark_failed(f"Max iterations ({max_iterations}) exceeded", base_dir)
            ui_renderer.render_message("error", f"Stopped: max iterations ({max_iterations}) exceeded")
            break

        elapsed = time.time() - start_time
        if elapsed > max_runtime:
            logger.log_auto_stop(f"Runtime timeout ({int(elapsed)}s > {max_runtime}s)")
            goal_manager.mark_failed(f"Runtime timeout ({int(elapsed)}s)", base_dir)
            ui_renderer.render_message("error", f"Stopped: runtime timeout ({int(elapsed)}s)")
            break

        if no_progress_count >= 3:
            logger.log_auto_stop(f"Stalled — no progress for {no_progress_count} iterations")
            goal_manager.mark_failed("Stalled — no progress", base_dir)
            ui_renderer.render_message("error", "Stopped: no progress for 3 iterations")
            break

        goal = goal_manager.get_goal(base_dir)
        if not goal or goal.get("status") != "active":
            break

        current_progress = goal.get("progress", 0.0)
        logger.log_auto_iteration(iteration, current_progress)
        ui_renderer.render_autonomous_iteration(iteration, max_iterations, current_progress, goal_text)

        if task_generator.should_generate_more(goal, base_dir):
            context = {
                "progress": current_progress,
                "iteration": iteration - 1,
                "completed_tasks": completed_summaries[-10:],
            }

            tasks = task_generator.generate_tasks(goal, context, base_dir)
            logger.log_auto_generate(len(tasks), iteration)

            if not tasks:
                ui_renderer.render_message("warning", "No tasks generated")
                no_progress_count += 1
                time.sleep(_SLEEP_BETWEEN_ITERATIONS)
                continue

            for task_desc in tasks:
                task_queue.add_task(task_desc, priority="normal",
                                  source="autonomous", base_dir=base_dir)

            goal_manager.increment_stats(generated=len(tasks), base_dir=base_dir)
            ui_renderer.render_message("info",
                f"Generated {len(tasks)} tasks for iteration {iteration}")

        results = task_worker.run_all(base_dir)

        iter_completed = results.get("completed", 0)
        iter_failed = results.get("failed", 0)
        iter_total = results.get("total", 0)

        summary["iterations"] = iteration
        summary["tasks_total"] += iter_total
        summary["tasks_completed"] += iter_completed
        summary["tasks_failed"] += iter_failed

        try:
            recent = task_queue.list_tasks(base_dir, status_filter="completed")
            for t in recent[-5:]:
                s = t.get("result_summary", t.get("description", ""))
                if s and s not in completed_summaries:
                    completed_summaries.append(s[:100])
        except Exception:
            pass

        goal_manager.increment_stats(completed=iter_completed,
                                      failed=iter_failed, base_dir=base_dir)

        eval_context = {
            "total": iter_total,
            "completed": iter_completed,
            "failed": iter_failed,
            "completed_summaries": completed_summaries[-8:],
        }

        report = progress_evaluator.evaluate(goal, eval_context, base_dir)
        logger.log_auto_progress(report.progress, report.status, report.reason)
        goal_manager.update_progress(report.progress, base_dir)

        goal_manager.add_history({
            "iteration": iteration,
            "tasks_generated": iter_total,
            "tasks_completed": iter_completed,
            "tasks_failed": iter_failed,
            "progress": report.progress,
            "status": report.status,
            "reason": report.reason,
        }, base_dir)

        ui_renderer.render_message("info",
            f"Iteration {iteration}: {iter_completed}/{iter_total} tasks, "
            f"progress={report.progress:.0%} ({report.status})")

        if report.status == "completed" or report.progress >= 0.95:
            logger.log_auto_stop("Goal completed")
            goal_manager.mark_complete(report.reason, base_dir)
            ui_renderer.render_message("success", f"Goal achieved! {report.reason}")
            summary["success"] = True
            break

        if report.status in ("failed", "stalled"):
            if report.status == "stalled":
                no_progress_count += 1
            else:
                logger.log_auto_stop(f"Evaluator reports: {report.status}")
                goal_manager.mark_failed(report.reason, base_dir)
                ui_renderer.render_message("error", f"Goal failed: {report.reason}")
                summary["success"] = False
                break

        if not report.should_continue:
            logger.log_auto_stop(f"Evaluator says stop: {report.reason}")
            goal_manager.mark_failed(report.reason, base_dir)
            summary["success"] = False
            break

        if report.progress > current_progress:
            no_progress_count = 0
        else:
            no_progress_count += 1

        try:
            task_queue.clear_completed(base_dir)
        except Exception:
            pass

        time.sleep(_SLEEP_BETWEEN_ITERATIONS)

    goal = goal_manager.get_goal(base_dir)
    if goal:
        ui_renderer.render_autonomous_complete(goal, summary)

    return summary


def stop() -> None:
    """Signal the autonomous loop to stop."""
    global _stop_flag
    _stop_flag = True


def status(base_dir=None) -> dict:
    """Get current autonomous status."""
    from . import goal_manager

    if base_dir is None:
        base_dir = Path.cwd()

    goal = goal_manager.get_goal(base_dir)
    if not goal:
        return {"active": False, "goal": None}

    return {
        "active": goal.get("status") == "active",
        "goal": goal.get("goal", ""),
        "progress": goal.get("progress", 0.0),
        "iterations": goal.get("iterations", 0),
        "status": goal.get("status", "unknown"),
        "tasks_completed": goal.get("tasks_completed", 0),
        "tasks_failed": goal.get("tasks_failed", 0),
    }