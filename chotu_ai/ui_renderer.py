"""UI Renderer — dedicated display module for rich terminal output."""
import sys
from typing import Optional


_TASK_ICONS = {
    "search": "🔍",
    "build": "🏗️",
    "coding": "💻",
    "summary": "📝",
    "analysis": "📊",
    "automation": "⚙️",
    "cleanup": "🧹",
    "unknown": "🔧",
}

_STEP_ICONS = {
    "pending": "○",
    "generating": "◐",
    "executing": "◑",
    "evaluating": "◒",
    "completed": "●",
    "failed": "✗",
    "skipped": "⊘",
}

_RESULT_CONFIG = {
    "pass": {"icon": "✅", "label": "PASS"},
    "fail": {"icon": "❌", "label": "FAIL"},
    "partial": {"icon": "⚠️", "label": "PARTIAL"},
    "skip": {"icon": "⏭", "label": "SKIP"},
    "escalate": {"icon": "🚨", "label": "ESCALATE"},
    "unknown": {"icon": "❓", "label": "RESULT"},
}


def render_task_header(task: str, profile: dict) -> None:
    """Display the task header box with classification info."""
    try:
        task_type = profile.get("task_type", "unknown")
        domain = profile.get("domain", "unknown")
        complexity = profile.get("complexity", "medium")
        time_est = profile.get("estimated_time", {})
        min_s = time_est.get("min_seconds", 10)
        max_s = time_est.get("max_seconds", 120)

        type_icon = _TASK_ICONS.get(task_type, "🔧")
        task_display = task[:48] if len(task) > 48 else task

        print()
        print(f"┌{'─'*56}┐")
        print(f"│  {type_icon} TASK: {task_display:<{48 - len(type_icon)}}│")
        print(f"│{' '*56}│")
        print(f"│  Type: {task_type.title():<10}│  Domain: {domain.title():<10}│  {complexity.title():<9}│")
        est = f"{min_s}-{max_s}s"
        print(f"│  ⏱  Estimated: ~{est}{' '*(37 - len(est))}│")
        print(f"└{'─'*56}┘")

    except Exception:
        print(f"\n  Task: {task}")


def render_plan(steps: list) -> None:
    """Display the task plan with numbered steps."""
    try:
        print(f"\n  📋 Plan ({len(steps)} steps):")
        for i, step in enumerate(steps, 1):
            status = step.get("status", "pending")
            icon = _STEP_ICONS.get(status, "○")
            desc = step.get("description", "")[:60]
            print(f"     {i}. {icon} {desc}")
        print(f"\n  {'─'*54}")
    except Exception:
        print(f"\n  Plan: {len(steps)} steps")


def render_step_start(step_num: int, total: int, description: str) -> None:
    """Display the step start with an animated-style progress bar."""
    try:
        pct = step_num / max(total, 1)
        filled = int(pct * 20)
        bar = "▓" * filled + "░" * (20 - filled)
        pct_str = f"{int(pct * 100)}%"

        print(f"\n  Step {step_num}/{total} {bar} {pct_str}")

        desc_short = description[:55] if len(description) > 55 else description
        print(f"  → {desc_short}")

    except Exception:
        print(f"\n  Step {step_num}/{total}: {description}")


def render_step_action(source: str, confidence: float, action_type: str, action_desc: str) -> None:
    """Display the action about to be executed."""
    try:
        source_tag = f"[{source}|{confidence:.0%}]"
        desc_short = action_desc[:70] if len(action_desc) > 70 else action_desc
        print(f"     {source_tag} {action_type}: {desc_short}")
    except Exception:
        print(f"     {action_type}: {action_desc}")


def render_step_result(verdict: str, duration_ms: int = 0,
                 confidence: float = 0.0, reason: str = "") -> None:
    """Display step result with appropriate icon and detail."""
    try:
        result_display = _RESULT_CONFIG.get(verdict, _RESULT_CONFIG["unknown"])
        icon = result_display["icon"]
        label = result_display["label"]

        parts = [f"  {icon} {label}"]
        if duration_ms > 0:
            parts.append(f"({duration_ms}ms)")
        if confidence > 0:
            parts.append(f"confidence={confidence:.0%}")

        print(" ".join(parts))

        if verdict in ("fail", "partial", "skip", "escalate") and reason:
            reason_short = reason[:80] if len(reason) > 80 else reason
            print(f"     └─ {reason_short}")

    except Exception:
        print(f"  [{verdict.upper()}] {reason}")


def render_step_retry(attempt: int, max_retries: int,
                    strategy: str, decision: str, reason: str = "") -> None:
    """Display retry indicator with strategy context."""
    try:
        decision_upper = decision.upper()
        print(f"  🔄 [{decision_upper}] {strategy} (attempt {attempt}/{max_retries})")
        if reason:
            reason_short = reason[:80] if len(reason) > 80 else reason
            print(f"     └─ {reason_short}")
    except Exception:
        print(f"  [RETRY] attempt {attempt}/{max_retries}")


def render_task_complete(state: dict) -> Optional[object]:
    """Render the full task completion output. Uses output_formatter internally."""
    try:
        from . import output_formatter
        formatted = output_formatter.format_output(state)
        output_formatter.render_cli(formatted)
        return formatted
    except Exception as e:
        print("\n  ✅ Task completed.")
        return None


def render_task_failed(state: dict) -> None:
    """Render task failure with issue summary."""
    try:
        from . import output_formatter
        formatted = output_formatter.format_output(state)
        output_formatter.render_cli(formatted)

        issues = state.get("issues", [])
        unresolved = [i for i in issues if not i.get("resolved", False)]
        if unresolved:
            print(f"\n  🔴 Unresolved Issues ({len(unresolved)}):")
            for issue in unresolved[:5]:
                print(f"     • [{issue.get('type', '')}] {issue.get('description', '')[:60]}")

    except Exception:
        print("\n  ❌ Task failed. Run 'chotu issues' for details.")


def render_status_dashboard(state: dict) -> None:
    """Rich status display for 'chotu status'. Replaces the bare-bones _display_status()."""
    try:
        task_desc = state.get("core_task", {}).get("description", "Unknown")
        task_status = state.get("core_task", {}).get("status", "unknown")
        profile = state.get("core_task", {}).get("task_profile", {})
        stats = state.get("stats", {})

        status_icons = {
            "pending": "⏳",
            "completed": "✅",
            "failed": "❌",
            "decomposing": "🔧",
        }
        status_icon = status_icons.get(task_status, "❓")

        task_type = profile.get("task_type", "")
        domain = profile.get("domain", "")
        type_line = ""
        if task_type:
            type_line = f"  Type: {task_type.title()}"
            if domain:
                type_line += f" | Domain: {domain.title()}"

        completed = stats.get("completed", 0)
        total = stats.get("total_steps", 0)
        failed = stats.get("failed", 0)
        retries = stats.get("total_retries", 0)

        print()
        print(f"┌{'─'*56}┐")
        status_str = str(status_icon)
        status_display = f"{task_status.upper()}"
        status_spaces = ' ' * (45 - len(status_str) - len(status_display))
        print(f"│  {status_icon} STATUS: {status_display}{status_spaces}│")
        print(f"├{'─'*56}┤")
        print(f"│  Task: {task_desc[:47]:<47}│")
        if type_line:
            print(f"│  {type_line:<54}│")
        print(f"├{'─'*56}┤")

        if total > 0:
            pct = completed / total
            filled = int(pct * 20)
            bar = "▓" * filled + "░" * (20 - filled)
            completed_str = str(completed)
            total_str = str(total)
            progress_str = f"{completed}/{total} ({int(pct*100)}%)"
            spaces_count = 14 - len(completed_str) - len(total_str)
            spaces = ' ' * spaces_count
            print(f"│  Progress: {bar} {progress_str}{spaces}│")
            print(f"│{' '*56}│")
        else:
            print(f"│  Progress: No steps yet{' '*32}│")

        print(f"│{' '*56}│")
        print(f"│  Completed: {completed:<5} Failed: {failed:<5} Retries: {retries:<5}   │")
        print(f"│  Pending:   {stats.get('pending', 0):<5} In Progress: {stats.get('in_progress', 0):<5}             │")
        print(f"└{'─'*56}┘")

        formatted = state.get("core_task", {}).get("formatted_output", {})
        if formatted and formatted.get("summary"):
            print(f"\n  📝 {formatted['summary']}")
            artifacts = formatted.get("artifacts", [])
            if artifacts:
                print(f"  📦 {len(artifacts)} artifacts")
                for a in artifacts[:3]:
                    print(f"     📄 {a.get('label', '')}")

    except Exception:
        print(f"\nTask: {state.get('core_task', {}).get('description', '?')}")
        print(f"Status: {state.get('core_task', {}).get('status', '?')}")


def render_issues(issues: list) -> None:
    """Display issues in a structured format."""
    try:
        if not issues:
            print("\n  ✅ No issues.")
            return

        unresolved = [i for i in issues if not i.get("resolved", False)]
        resolved = [i for i in issues if i.get("resolved", False)]

        if unresolved:
            print(f"\n  🔴 Unresolved Issues ({len(unresolved)}):")
            for issue in unresolved:
                print(f"     • [{issue.get('type', '')}] {issue.get('description', '')[:70]}")
                print(f"       Step: {issue.get('step_id', '')} | {issue.get('occurred_at', '')[:19]}")

        if resolved:
            print(f"\n  🟢 Resolved Issues ({len(resolved)}):")
            for issue in resolved[:5]:
                print(f"     • [{issue.get('type', '')}] {issue.get('description', '')[:70]}")

    except Exception:
        print(f"\n  Issues: {len(issues)}")


def render_message(level: str, text: str) -> None:
    """Display a general message."""
    icons = {"error": "❌", "warning": "⚠️", "info": "ℹ️", "success": "✅"}
    icon = icons.get(level, "")
    print(f"  {icon} {text}")


def render_queue_list(tasks: list) -> None:
    """Display queued tasks in a formatted table."""
    try:
        if not tasks:
            print("\n  📭 Queue is empty.")
            return

        priority_icons = {"high": "🔴", "normal": "🟡", "low": "🟢"}
        status_icons = {"pending": "⏳", "running": "🔄", "completed": "✅", "failed": "❌"}

        print()
        print(f"  📋 Task Queue ({len(tasks)} tasks)")
        print(f"  {'─'*56}")

        for i, task in enumerate(tasks, 1):
            p_icon = priority_icons.get(task.get("priority", ""), "⚪")
            s_icon = status_icons.get(task.get("status", ""), "❓")
            desc = task.get("description", "")[:45]
            task_id = task.get("task_id", "")[:8]
            priority = task.get("priority", "normal")

            print(f"  {i:2}. {s_icon} {p_icon} [{priority:>6}] {desc}")
            print(f"      ID: {task_id}  |  Status: {task.get('status', '')}")

            if task.get("result_summary"):
                print(f"      📝 {task['result_summary'][:50]}")

        print(f"  {'─'*56}")
    except Exception:
        print(f"\n  Queue: {len(tasks)} tasks")


def render_queue_status(counts: dict) -> None:
    """Display queue status summary."""
    try:
        total = sum(counts.values())
        pending = counts.get("pending", 0)
        running = counts.get("running", 0)
        completed = counts.get("completed", 0)
        failed = counts.get("failed", 0)

        print()
        print(f"  ┌{'─'*40}┐")
        print(f"  │  📊 QUEUE STATUS{' '*22}│")
        print(f"  ├{'─'*40}┤")
        print(f"  │  Total:     {total:<26}│")
        print(f"  │  ⏳ Pending:   {pending:<23}│")
        print(f"  │  🔄 Running:   {running:<23}│")
        print(f"  │  ✅ Completed: {completed:<23}│")
        print(f"  │  ❌ Failed:    {failed:<23}│")
        print(f"  └{'─'*40}┘")
    except Exception:
        print(f"\n  Queue: {counts}")


def render_goal_status(goal: dict) -> None:
    """Display current goal with progress."""
    try:
        goal_text = goal.get("goal", "Unknown")
        status = goal.get("status", "unknown")
        progress = goal.get("progress", 0.0)
        iterations = goal.get("iterations", 0)
        max_iter = goal.get("max_iterations", 20)
        completed = goal.get("tasks_completed", 0)
        failed = goal.get("tasks_failed", 0)
        generated = goal.get("tasks_generated", 0)

        status_icons = {
            "active": "🟢", "completed": "✅",
            "failed": "❌", "paused": "⏸️",
        }
        s_icon = status_icons.get(status, "❓")

        pct = int(progress * 100)
        filled = int(progress * 25)
        bar = "█" * filled + "░" * (25 - filled)

        print()
        print(f"  ┌{'─'*56}┐")
        print(f"  │  🎯 GOAL{' '*47}│")
        print(f"  ├{'─'*56}┤")

        goal_display = goal_text[:50]
        print(f"  │  {goal_display:<54}│")
        print(f"  │{' '*56}│")
        print(f"  │  {s_icon} Status: {status.upper():<44}│")
        print(f"  │  Progress: {bar} {pct}%{' '*(17 - len(str(pct)))}│")
        print(f"  │{' '*56}│")
        print(f"  │  Iterations: {iterations}/{max_iter:<5}  Tasks: {completed}✅ {failed}❌ ({generated} total)  │")

        history = goal.get("history", [])
        if history:
            print(f"  ├{'─'*56}┤")
            print(f"  │  📜 Recent History{' '*37}│")
            for entry in history[-3:]:
                it = entry.get("iteration", "?")
                prog = entry.get("progress", 0)
                act = entry.get("action", entry.get("status", ""))
                line = f"    Iter {it}: {prog:.0%} — {act}"
                print(f"  │  {line:<54}│")

        print(f"  └{'─'*56}┘")

    except Exception:
        print(f"\n  Goal: {goal.get('goal', '?')}")
        print(f"  Status: {goal.get('status', '?')}")
        print(f"  Progress: {goal.get('progress', 0):.0%}")


def render_autonomous_iteration(iteration: int, max_iterations: int,
                                progress: float, goal: str) -> None:
    """Display iteration header during autonomous execution."""
    try:
        pct = int(progress * 100)
        filled = int(progress * 15)
        bar = "▓" * filled + "░" * (15 - filled)

        print()
        print(f"  ═══ 🤖 ITERATION {iteration}/{max_iterations} ═══ {bar} {pct}%")
        print(f"  Goal: {goal[:55]}")
        print(f"  {'─'*50}")
    except Exception:
        print(f"\n  --- Iteration {iteration}/{max_iterations} ---")


def render_autonomous_complete(goal: dict, summary: dict) -> None:
    """Display final autonomous execution summary."""
    try:
        status = goal.get("status", "unknown")
        progress = goal.get("progress", 0.0)
        goal_text = goal.get("goal", "")

        is_success = status == "completed"
        icon = "🎯" if is_success else "❌"
        label = "GOAL ACHIEVED" if is_success else "GOAL FAILED"

        print()
        print(f"  ╔{'═'*56}╗")
        print(f"  ║  {icon} {label}{' '*(52 - len(label) - len(icon))}║")
        print(f"  ╠{'═'*56}╣")
        print(f"  ║  Goal: {goal_text[:48]:<48}║")
        print(f"  ║  Progress: {progress:.0%}{' '*(44 - len(f'{progress:.0%}'))}║")
        print(f"  ║{' '*56}║")
        print(f"  ║  Iterations:  {summary.get('iterations', 0):<41}║")
        print(f"  ║  Tasks total: {summary.get('tasks_total', 0):<41}║")
        print(f"  ║  Completed:   {summary.get('tasks_completed', 0):<41}║")
        print(f"  ║  Failed:      {summary.get('tasks_failed', 0):<41}║")
        print(f"  ╚{'═'*56}╝")
    except Exception:
        print(f"\n  Autonomous mode ended: {goal.get('status', '?')}")