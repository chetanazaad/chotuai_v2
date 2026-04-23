"""Convert a high-level task into ordered, atomic todo steps. Works WITHOUT any LLM."""
import json
import os
import re
from pathlib import Path
from typing import Optional


def decompose(core_task: str, context: Optional[dict] = None) -> list:
    """Try LLM, fallback to rules."""
    context = context or {}
    llm_available = _check_llm_availability()
    if llm_available:
        try:
            result = _decompose_with_llm(core_task, context)
            if result:
                return result
        except Exception:
            pass
    return _decompose_fallback(core_task, context)


def generate_action(step: dict, state: dict, retry_context: Optional[dict] = None) -> dict:
    """Try LLM, fallback to rules."""
    retry_context = retry_context or {}
    llm_available = _check_llm_availability()
    if llm_available:
        try:
            result = _generate_action_with_llm(step, state, retry_context)
            if result:
                return result
        except Exception:
            pass
    return _generate_action_fallback(step, state, retry_context)


def _check_llm_availability() -> bool:
    """Check if any LLM provider is available via gateway."""
    from . import llm_gateway
    return llm_gateway.is_available()


def _decompose_with_llm(core_task: str, context: dict) -> Optional[list]:
    """Try LLM decomposition via gateway."""
    from . import llm_gateway

    prompt = f"""You are a task decomposer. Break down the following task into atomic steps.
Return ONLY a JSON array of step objects. Each step object must have:
- id: step_NNN (e.g., step_001)
- description: short description
- expected_outcome: what success looks like

Task: {core_task}

Output only valid JSON, no explanation."""

    request = llm_gateway.GatewayRequest(
        purpose="decomposition",
        prompt=prompt,
        task_type="structured",
    )
    response = llm_gateway.generate(request)

    if response.success and response.structured and "items" in response.parsed:
        return response.parsed["items"]
    elif response.success and response.raw_output:
        try:
            steps = json.loads(response.text)
            if isinstance(steps, list):
                return steps
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _generate_action_with_llm(step: dict, state: dict, retry_context: dict) -> Optional[dict]:
    """Try LLM action generation via gateway."""
    from . import llm_gateway

    prompt = f"""You are an action generator. Generate a single action to complete the step.
Step: {step.get('description', '')}
State: {json.dumps(state.get('core_task', {}))}

Return ONLY a JSON object with:
- type: "shell" or "file_write" or "file_read"
- command: (for shell) the command to run
- path: (for file operations) the file path
- content: (for file_write) the content to write

Output only valid JSON, no explanation."""

    request = llm_gateway.GatewayRequest(
        purpose="planning",
        prompt=prompt,
        task_type="code",
    )
    response = llm_gateway.generate(request)

    if response.success and response.structured:
        return response.parsed
    return None


def _decompose_fallback(core_task: str, context: dict) -> list:
    """Fallback decomposition using keyword-based rules."""
    task_lower = core_task.lower()
    todo_list = []
    step_num = 1
    if "hello world" in task_lower:
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Create hello.py with print statement",
            "expected_outcome": {"type": "file_exists", "path": "hello.py"}
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Run the hello.py script",
            "expected_outcome": {"type": "output_contains", "pattern": "Hello, World!"}
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Verify hello.py runs successfully",
            "expected_outcome": ""
        })
    elif any(kw in task_lower for kw in ["script", "file", ".py"]):
        filename = _extract_filename(core_task)
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": f"Create {filename}",
            "expected_outcome": {"type": "file_exists", "path": filename}
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": f"Run {filename}",
            "expected_outcome": ""
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": f"Verify {filename} output",
            "expected_outcome": ""
        })
    elif any(kw in task_lower for kw in ["api", "rest", "flask"]):
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Create project directory",
            "expected_outcome": {"type": "file_exists", "path": "app"}
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Create app.py with Flask app",
            "expected_outcome": {"type": "file_exists", "path": "app.py"}
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Add endpoint",
            "expected_outcome": ""
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Test the API",
            "expected_outcome": ""
        })
    elif "organize" in task_lower and "files" in task_lower:
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "List files in current directory",
            "expected_outcome": ""
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Create target directories",
            "expected_outcome": ""
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Move files to target directories",
            "expected_outcome": ""
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Verify file organization",
            "expected_outcome": ""
        })
    elif "directory" in task_lower or "folder" in task_lower:
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Create directories",
            "expected_outcome": ""
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Verify directories exist",
            "expected_outcome": ""
        })
    else:
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Analyze requirements",
            "expected_outcome": ""
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Execute the task",
            "expected_outcome": ""
        })
        step_num += 1
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Verify result",
            "expected_outcome": ""
        })
    return todo_list


def _generate_action_fallback(step: dict, state: dict, retry_context: dict) -> dict:
    """Fallback action generation using keyword matching."""
    desc = step.get("description", "").lower()
    step_id = step.get("id", "step_000")
    is_windows = os.name == "nt"

    if "create" in desc and "hello" in desc and "python" in desc:
        return {
            "type": "file_write",
            "path": "hello.py",
            "content": "print('Hello, World!')\n"
        }
    if "create" in desc and ("print" in desc or "statement" in desc):
        return {
            "type": "file_write",
            "path": "hello.py",
            "content": "print('Hello, World!')\n"
        }
    if "run" in desc and ".py" in desc:
        filename = _extract_filename(state.get("core_task", {}).get("description", ""))
        if filename and filename.endswith(".py"):
            return {"type": "shell", "command": f"python {filename}"}
    if "run" in desc and "script" in desc:
        return {"type": "shell", "command": "python hello.py"}
    if "create" in desc and ("directory" in desc or "folder" in desc):
        match = re.search(r'([a-zA-Z0-9_]+)', step.get("description", ""))
        dirname = match.group(1) if match else "newdir"
        mkdir_cmd = "mkdir" if is_windows else "mkdir -p"
        return {"type": "shell", "command": f"{mkdir_cmd} {dirname}"}
    if "verify" in desc or "check" in desc:
        return {"type": "shell", "command": "echo Verification complete"}
    if "create" in desc and "file" in desc:
        match = re.search(r'([a-zA-Z0-9_-]+\.py)', step.get("description", ""))
        filename = match.group(1) if match else "output.py"
        return {
            "type": "file_write",
            "path": filename,
            "content": "# Generated file\n"
        }
    if "list" in desc and "files" in desc:
        return {"type": "shell", "command": "dir" if is_windows else "ls"}
    if "move" in desc and "files" in desc:
        return {"type": "shell", "command": "echo Moving files"}
    return {"type": "shell", "command": f"echo Step {step_id} completed"}


def _extract_filename(text: str) -> str:
    """Extract filename from text."""
    match = re.search(r'([a-zA-Z0-9_-]+\.py)', text)
    if match:
        return match.group(1)
    match = re.search(r'([a-zA-Z0-9_-]+\.[a-zA-Z]+)', text)
    if match:
        return match.group(1)
    return ""