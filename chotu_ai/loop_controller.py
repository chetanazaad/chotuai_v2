"""Loop Controller — global execution safety limits."""
import dataclasses
import time
from typing import Optional


_DEFAULT_LIMITS = {
    "global_timeout_seconds": 600,
    "consecutive_failure_threshold": 5,
    "max_total_loops": 50,
    "stuck_threshold": 3,
}


@dataclasses.dataclass
class LoopVerdict:
    action: str
    reason: str
    elapsed_seconds: float
    stats: dict


def check(state: dict, task_start_time: float) -> LoopVerdict:
    """Check all global limits. Called at top of each loop iteration."""
    limits = get_limits(state)
    stats = _gather_stats(state)

    elapsed = time.time() - task_start_time

    if elapsed > limits["global_timeout_seconds"]:
        return LoopVerdict(
            action="timeout",
            reason=f"Global timeout exceeded ({int(elapsed)}s > {limits['global_timeout_seconds']}s)",
            elapsed_seconds=elapsed,
            stats=stats,
        )

    consec_failures = _count_consecutive_failures(state)
    if consec_failures >= limits["consecutive_failure_threshold"]:
        return LoopVerdict(
            action="threshold_exceeded",
            reason=f"{consec_failures} consecutive step failures (threshold: {limits['consecutive_failure_threshold']})",
            elapsed_seconds=elapsed,
            stats=stats,
        )

    total_loops = stats.get("total_loop_iterations", 0)
    if total_loops > limits["max_total_loops"]:
        return LoopVerdict(
            action="abort",
            reason=f"Max loop iterations exceeded ({total_loops} > {limits['max_total_loops']})",
            elapsed_seconds=elapsed,
            stats=stats,
        )

    return LoopVerdict(
        action="continue",
        reason="All limits OK",
        elapsed_seconds=elapsed,
        stats=stats,
    )


def is_stuck(step: dict, state: dict) -> bool:
    """Check if a step is stuck repeating the same error."""
    limits = get_limits(state)
    issues = state.get("issues", [])
    step_id = step.get("id", "")

    step_issues = [i for i in issues if i.get("step_id") == step_id]
    if len(step_issues) < limits["stuck_threshold"]:
        return False

    recent = step_issues[-limits["stuck_threshold"]:]
    types = set(i.get("type", "") for i in recent)
    return len(types) == 1


def get_limits(state: dict) -> dict:
    """Get current limits, merging defaults with config overrides."""
    config = state.get("config", {})
    limits = dict(_DEFAULT_LIMITS)
    for key in limits:
        if key in config:
            limits[key] = config[key]
    return limits


def _count_consecutive_failures(state: dict) -> int:
    """Count how many recent steps ended in failure consecutively."""
    todo = state.get("todo_list", [])
    terminal_steps = [s for s in todo if s.get("status") in ("completed", "failed", "skipped")]
    if not terminal_steps:
        return 0

    count = 0
    for step in reversed(terminal_steps):
        if step.get("status") == "failed":
            count += 1
        else:
            break
    return count


def _gather_stats(state: dict) -> dict:
    """Gather loop-relevant stats."""
    stats = state.get("stats", {})
    return {
        "total_steps": stats.get("total_steps", 0),
        "completed": stats.get("completed", 0),
        "failed": stats.get("failed", 0),
        "total_retries": stats.get("total_retries", 0),
        "total_loop_iterations": stats.get("total_retries", 0) + stats.get("completed", 0) + stats.get("failed", 0),
        "consecutive_failures": _count_consecutive_failures(state),
    }


def reset_limits() -> None:
    """Reset to default limits. Returns nothing as limits are constants."""
    pass