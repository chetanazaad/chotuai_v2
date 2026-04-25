"""Parse user commands, call controller, display output."""
import argparse
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from . import controller


def main():
    parser = argparse.ArgumentParser(
        prog="chotu",
        description="chotu_ai — Deterministic autonomous execution engine"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="Start a new task")
    new_parser.add_argument("task", type=str, help="Task description")
    new_parser.add_argument("--working-dir", type=str, default=None, help="Working directory")
    new_parser.add_argument("--auto-run", action="store_true", help="Auto-run after creating task")
    
    append_parser = subparsers.add_parser("append", help="Append new steps to active task")
    append_parser.add_argument("task", type=str, help="Additional task description")
    append_parser.add_argument("--auto-run", action="store_true", help="Auto-run after appending task")

    subparsers.add_parser("run", help="Resume execution")

    subparsers.add_parser("status", help="Show task status")

    subparsers.add_parser("cache", help="LLM cache stats")

    subparsers.add_parser("plan", help="Show task plan")

    log_parser = subparsers.add_parser("log", help="Show logs")
    log_parser.add_argument("task_id", nargs="?", default=None, help="Task ID (optional)")
    log_parser.add_argument("step_id", nargs="?", default=None, help="Step ID (optional)")

    subparsers.add_parser("issues", help="Show issues")

    subparsers.add_parser("skip", help="Skip current step")

    subparsers.add_parser("reset", help="Reset current step")

    subparsers.add_parser("abort", help="Abort task")

    queue_parser = subparsers.add_parser("queue", help="Task queue management")
    queue_sub = queue_parser.add_subparsers(dest="queue_command", required=True)

    queue_add = queue_sub.add_parser("add", help="Add task to queue")
    queue_add.add_argument("task", type=str, help="Task description")
    queue_add.add_argument("--priority", type=str, default="normal",
                          choices=["low", "normal", "high"], help="Task priority")

    queue_sub.add_parser("list", help="List queued tasks")

    queue_sub.add_parser("run", help="Run all queued tasks")

    queue_sub.add_parser("status", help="Queue status summary")

    queue_sub.add_parser("clear", help="Clear completed tasks")

    goal_parser = subparsers.add_parser("goal", help="Goal management")
    goal_sub = goal_parser.add_subparsers(dest="goal_command", required=True)

    goal_set = goal_sub.add_parser("set", help="Set a new goal")
    goal_set.add_argument("goal", type=str, help="Goal description")
    goal_set.add_argument("--max-iterations", type=int, default=20)
    goal_set.add_argument("--max-runtime", type=int, default=1800,
                          help="Max runtime in seconds")

    goal_sub.add_parser("status", help="Show goal status")

    auto_parser = subparsers.add_parser("auto", help="Autonomous mode")
    auto_sub = auto_parser.add_subparsers(dest="auto_command", required=True)

    auto_sub.add_parser("start", help="Start autonomous execution")
    auto_sub.add_parser("stop", help="Stop autonomous execution")

    subparsers.add_parser("clean", help="System cleanup engine")
    
    subparsers.add_parser("tasks", help="List all tasks")
    
    open_parser = subparsers.add_parser("open", help="Open task folder")
    open_parser.add_argument("task_id", type=str, help="Task index or ID")

    args = parser.parse_args()
    command = args.command

    args_dict = {
        "task": getattr(args, "task", ""),
        "working_dir": getattr(args, "working_dir", None),
        "auto_run": getattr(args, "auto_run", False),
        "step_id": getattr(args, "step_id", None),
        "task_id": getattr(args, "task_id", None),
    }

    try:
        if command == "status":
            from . import llm_gateway
            llm_gateway.check_llm_status()
            sys.exit(0)
        
        if command == "queue":
            from . import task_queue, task_registry, logger, ui_renderer
            queue_cmd = args.queue_command

            if queue_cmd == "add":
                task_id = task_queue.add_task(args.task, args.priority)
                task_registry.register_task(task_id, args.task, args.priority)
                logger.log_queue_add(task_id, args.task, args.priority)
                ui_renderer.render_message("success",
                    f"Added to queue: [{args.priority.upper()}] {args.task[:50]} (id: {task_id})")
                sys.exit(0)

            elif queue_cmd == "list":
                tasks = task_queue.list_tasks()
                ui_renderer.render_queue_list(tasks)
                sys.exit(0)

            elif queue_cmd == "run":
                from . import task_worker
                results = task_worker.run_all()
                ui_renderer.render_message("info",
                    f"Queue complete: {results['completed']}/{results['total']} succeeded, "
                    f"{results['failed']} failed")
                sys.exit(0 if results["failed"] == 0 else 1)

            elif queue_cmd == "status":
                ui_renderer.render_queue_status(task_queue.count_by_status())
                sys.exit(0)

            elif queue_cmd == "clear":
                removed = task_queue.clear_completed()
                ui_renderer.render_message("success", f"Cleared {removed} completed/failed tasks")
                sys.exit(0)

        elif command == "goal":
            goal_cmd = args.goal_command
            if goal_cmd == "set":
                from . import goal_manager, logger, ui_renderer
                goal_id = goal_manager.set_goal(
                    args.goal,
                    max_iterations=args.max_iterations,
                    max_runtime=args.max_runtime,
                )
                logger.log_goal_set(goal_id, args.goal)
                ui_renderer.render_message("success",
                    f"Goal set: {args.goal[:60]} (id: {goal_id})")
                sys.exit(0)

            elif goal_cmd == "status":
                from . import goal_manager, ui_renderer
                goal = goal_manager.get_goal()
                if goal:
                    ui_renderer.render_goal_status(goal)
                else:
                    ui_renderer.render_message("info", "No goal set. Use 'chotu goal set \"...\"'")
                sys.exit(0)

        elif command == "auto":
            auto_cmd = args.auto_command
            if auto_cmd == "start":
                from . import autonomous_runner
                result = autonomous_runner.start()
                sys.exit(0 if result.get("success") else 1)

            elif auto_cmd == "stop":
                from . import autonomous_runner
                autonomous_runner.stop()
                ui_renderer.render_message("info", "Stop signal sent.")
                sys.exit(0)

        success = controller.handle_command(command, args_dict)
        sys.exit(0 if success else 1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()