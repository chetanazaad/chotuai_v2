"""Task Generator — Goal to task descriptions."""
import json
import re
from typing import Optional


_MAX_TASKS_PER_ITERATION = 5


def generate_tasks(goal: dict, context: dict = None,
                  base_dir=None) -> list:
    """Generate task descriptions from goal."""
    context = context or {}
    goal_text = goal.get("goal", "")

    if not goal_text:
        return []

    try:
        tasks = _generate_via_llm(goal_text, context)
        if tasks:
            return tasks[:_MAX_TASKS_PER_ITERATION]
    except Exception:
        pass

    return _generate_fallback(goal_text, context)


def should_generate_more(goal: dict, base_dir=None) -> bool:
    """Check if more tasks should be generated."""
    if not goal or goal.get("status") != "active":
        return False

    progress = goal.get("progress", 0.0)
    if progress >= 0.95:
        return False

    try:
        from . import task_queue
        pending = task_queue.list_tasks(base_dir, status_filter="pending")
        if pending:
            return False
    except Exception:
        pass

    return True


def _generate_via_llm(goal_text: str, context: dict) -> list:
    """Use LLM to generate task descriptions."""
    from . import llm_gateway

    completed = context.get("completed_tasks", [])
    progress = context.get("progress", 0.0)
    iteration = context.get("iteration", 0)

    completed_text = ""
    if completed:
        completed_lines = [f"  - {t}" for t in completed[-5:]]
        completed_text = f"\nAlready completed:\n" + "\n".join(completed_lines)

    prompt = f"""You are a goal planner. Generate the NEXT set of tasks to work toward this goal.

GOAL: {goal_text}

Current progress: {progress:.0%}
Iteration: {iteration + 1}
{completed_text}

Generate 2-5 specific, actionable tasks that bring the goal closer to completion.
Each task should be a standalone instruction that can be executed independently.

Return ONLY a JSON array of task description strings:
["task 1 description", "task 2 description", ...]

Rules:
- Do NOT repeat completed tasks
- Each task must be specific and actionable
- If progress is high (>80%), generate finishing/cleanup tasks
- Output ONLY valid JSON array, no other text
"""

    request = llm_gateway.GatewayRequest(
        purpose="planning",
        prompt=prompt,
        task_type="structured",
        max_tokens=2048,
        temperature=0.2,
    )

    response = llm_gateway.generate(request)
    if not response.success:
        return []

    return _parse_task_list(response.raw_output)


def _parse_task_list(raw_output: str) -> list:
    """Parse LLM output into a list of task strings."""
    try:
        raw_output = raw_output.strip()
        if raw_output.startswith("```"):
            raw_output = raw_output.split("```")[1]
            if raw_output.startswith("json"):
                raw_output = raw_output[4:]

        tasks = json.loads(raw_output.strip())
        if isinstance(tasks, list):
            return [str(t).strip() for t in tasks if t and str(t).strip()]
    except (json.JSONDecodeError, AttributeError):
        pass

    match = re.search(r'\[.*\]', raw_output, re.DOTALL)
    if match:
        try:
            tasks = json.loads(match.group())
            if isinstance(tasks, list):
                return [str(t).strip() for t in tasks if t and str(t).strip()]
        except json.JSONDecodeError:
            pass

    return []


def _generate_fallback(goal_text: str, context: dict) -> list:
    """Rule-based task generation."""
    goal_lower = goal_text.lower()
    progress = context.get("progress", 0.0)
    tasks = []

    if any(kw in goal_lower for kw in ["find", "search", "get", "look", "collect"]):
        if progress < 0.3:
            tasks.append(f"Search for information about: {goal_text}")
            tasks.append(f"Extract relevant data from search results")
        elif progress < 0.7:
            tasks.append(f"Gather additional results for: {goal_text}")
            tasks.append(f"Organize collected data")
        else:
            tasks.append(f"Create final summary of findings for: {goal_text}")

    elif any(kw in goal_lower for kw in ["build", "create", "make", "develop"]):
        if progress < 0.3:
            tasks.append(f"Plan the structure for: {goal_text}")
            tasks.append(f"Create the initial files and setup")
        elif progress < 0.7:
            tasks.append(f"Implement core functionality for: {goal_text}")
            tasks.append(f"Test the implementation")
        else:
            tasks.append(f"Finalize and verify: {goal_text}")

    else:
        if progress < 0.5:
            tasks.append(f"Analyze and plan approach for: {goal_text}")
            tasks.append(f"Execute first phase of: {goal_text}")
        else:
            tasks.append(f"Continue progress toward: {goal_text}")
            tasks.append(f"Verify and finalize: {goal_text}")

    return tasks[:_MAX_TASKS_PER_ITERATION]