"""Progress Evaluator — Goal completion evaluation."""
import dataclasses
import json
import re
from typing import Optional


@dataclasses.dataclass
class ProgressReport:
    progress: float
    status: str
    reason: str
    should_continue: bool


def evaluate(goal: dict, completed_results: dict = None,
            base_dir=None) -> ProgressReport:
    """Evaluate progress toward the goal."""
    completed_results = completed_results or {}

    try:
        report = _evaluate_via_llm(goal, completed_results)
        if report:
            return report
    except Exception:
        pass

    return _evaluate_fallback(goal, completed_results)


def _evaluate_via_llm(goal: dict, results: dict) -> Optional[ProgressReport]:
    """Use LLM to evaluate progress."""
    from . import llm_gateway

    goal_text = goal.get("goal", "")
    current_progress = goal.get("progress", 0.0)
    iterations = goal.get("iterations", 0)
    completed_tasks = results.get("completed_summaries", [])

    completed_text = ""
    if completed_tasks:
        lines = [f"  - {t}" for t in completed_tasks[-8:]]
        completed_text = "\n".join(lines)

    prompt = f"""You are a progress evaluator. Assess how close we are to completing this goal.

GOAL: {goal_text}

Previous progress: {current_progress:.0%}
Iterations completed: {iterations}
Tasks completed so far:
{completed_text}

Latest results:
  Total tasks this iteration: {results.get('total', 0)}
  Completed: {results.get('completed', 0)}
  Failed: {results.get('failed', 0)}

Evaluate the progress. Return ONLY this JSON:
{{
  "progress": 0.0 to 1.0,
  "status": "in_progress" or "completed" or "failed" or "stalled",
  "reason": "brief explanation"
}}

Rules:
- progress=1.0 means goal is fully achieved
- status="completed" if progress >= 0.95
- status="stalled" if no meaningful progress in this iteration
- status="failed" if tasks keep failing and goal cannot be achieved
- Output ONLY valid JSON
"""

    request = llm_gateway.GatewayRequest(
        purpose="reasoning",
        prompt=prompt,
        task_type="structured",
        max_tokens=512,
        temperature=0.1,
    )

    response = llm_gateway.generate(request)
    if not response.success:
        return None

    return _parse_progress_report(response.raw_output)


def _parse_progress_report(raw: str) -> Optional[ProgressReport]:
    """Parse LLM output into ProgressReport."""
    try:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        data = json.loads(raw.strip())
        if isinstance(data, dict):
            progress = float(data.get("progress", 0.0))
            progress = max(0.0, min(1.0, progress))
            status = data.get("status", "in_progress")
            reason = data.get("reason", "")

            if status not in ("in_progress", "completed", "failed", "stalled"):
                status = "in_progress"

            should_continue = status == "in_progress"

            return ProgressReport(
                progress=progress,
                status=status,
                reason=reason,
                should_continue=should_continue,
            )
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            progress = max(0.0, min(1.0, float(data.get("progress", 0.0))))
            status = data.get("status", "in_progress")
            return ProgressReport(
                progress=progress, status=status,
                reason=data.get("reason", ""),
                should_continue=status == "in_progress",
            )
        except Exception:
            pass

    return None


def _evaluate_fallback(goal: dict, results: dict) -> ProgressReport:
    """Heuristic-based progress evaluation."""
    current_progress = goal.get("progress", 0.0)
    iterations = goal.get("iterations", 0)
    total_completed = goal.get("tasks_completed", 0)
    total_failed = goal.get("tasks_failed", 0)

    iter_total = results.get("total", 0)
    iter_completed = results.get("completed", 0)
    iter_failed = results.get("failed", 0)

    if iter_total > 0:
        iter_success_rate = iter_completed / iter_total
        progress_increment = iter_success_rate * 0.3
    else:
        progress_increment = 0.0

    new_progress = min(1.0, current_progress + progress_increment)

    if new_progress >= 0.95:
        return ProgressReport(
            progress=new_progress, status="completed",
            reason="Progress reached completion threshold (≥95%)",
            should_continue=False,
        )

    if iter_total > 0 and iter_failed == iter_total:
        if total_failed > total_completed:
            return ProgressReport(
                progress=new_progress, status="failed",
                reason="All tasks failed. More failures than successes overall.",
                should_continue=False,
            )
        return ProgressReport(
            progress=new_progress, status="stalled",
            reason="All tasks failed this iteration.",
            should_continue=False,
        )

    if progress_increment < 0.01 and iterations >= 2:
        return ProgressReport(
            progress=new_progress, status="stalled",
            reason="No meaningful progress in this iteration.",
            should_continue=False,
        )

    return ProgressReport(
        progress=new_progress, status="in_progress",
        reason=f"Progress: {new_progress:.0%}. {iter_completed}/{iter_total} tasks completed.",
        should_continue=True,
    )