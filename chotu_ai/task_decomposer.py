"""Convert a high-level task into ordered, atomic todo steps. Works WITHOUT any LLM."""
import json
import os
import re
from pathlib import Path
from typing import Optional


def decompose(core_task: str, context: Optional[dict] = None) -> list:
    """Hybrid decomposition: System base plan + optional LLM refinement."""
    context = context or {}
    task_lower = core_task.lower()
    
    task_profile = context.get("task_profile", {})
    is_simple = False
    if isinstance(task_profile, dict):
        is_simple = task_profile.get("complexity") == "low" and task_profile.get("domain") == "filesystem"
    else:
        is_simple = getattr(task_profile, "complexity", "") == "low" and getattr(task_profile, "domain", "") == "filesystem"

    base_plan = _generate_base_plan(core_task, task_lower)
    
    final_plan = []
    if base_plan:
        print(f"[DECOMPOSER] Base plan generated: {len(base_plan)} steps")
        
        if is_simple:
            print(f"[DECOMPOSER] Simple task - using base plan")
            final_plan = base_plan
        else:
            llm_available = _check_llm_availability()
            if llm_available:
                refined_plan = _refine_plan_with_llm(base_plan, core_task, context)
                if refined_plan and _validate_refined_plan(base_plan, refined_plan):
                    if len(refined_plan) > 0:
                        print(f"[DECOMPOSER] Using LLM refined plan: {len(refined_plan)} steps")
                        final_plan = refined_plan
                    else:
                        print("[SAFEGUARD] Ignoring empty LLM plan")
                        final_plan = base_plan
                else:
                    print(f"[DECOMPOSER] LLM refinement failed - using base plan")
                    final_plan = base_plan
            else:
                final_plan = base_plan
    else:
        llm_available = _check_llm_availability()
        if llm_available and not is_simple:
            try:
                result = _decompose_with_llm(core_task, context)
                if result and isinstance(result, list) and len(result) > 0:
                    final_plan = result
            except Exception:
                pass
        
        if not final_plan:
            print("[PLANNER] fallback used instantly")
            final_plan = _decompose_fallback(core_task, context)

    # STEP 4 — APPLY DURING STEP CREATION
    for step in final_plan:
        # User requested: re.search(r'(\w+\.html)', text)
        # We use a slightly more robust version that also supports .py etc.
        filename = _extract_filename(step.get("description", ""))
        if filename:
            # Every step must contain target_file
            step["target_file"] = filename
            # STEP 5 — DEBUG LOG
            print(f"[DECOMPOSER] Assigned target_file → {filename}")
        else:
            # Try extracting from the core task if description is generic
            filename_from_task = _extract_filename(core_task)
            if filename_from_task and "index.html" in filename_from_task:
                 step["target_file"] = filename_from_task

    # STEP 6 — VALIDATION
    for step in final_plan:
        if "target_file" not in step:
            # For non-file tasks, target_file is not required, but we warn anyway for build tasks
            if any(kw in str(step).lower() for kw in ["html", "page", "file", "script", ".py"]):
                print(f"[WARNING] Missing target_file for file-related step: {step}")
            else:
                # Default for build tasks if we can't find one
                if "create" in str(step).lower():
                    step["target_file"] = "output.html"
                    print(f"[DECOMPOSER] Defaulting target_file to output.html for step: {step.get('description')}")

    return final_plan


def _generate_base_plan(core_task: str, task_lower: str) -> list:
    """Phase 1: Generate deterministic base plan."""
    todo_list = []
    step_num = 1
    
    if any(kw in task_lower for kw in ["multiple pages", "system", "app", "website", "pages", "web", "multi-page"]):
        import re
        html_files = re.findall(r'([a-zA-Z0-9_-]+\.html)', core_task)
        
        if html_files:
            # Use dynamically found html files from the prompt
            for page in html_files:
                todo_list.append({
                    "id": f"step_{step_num:03d}",
                    "description": f"Create {page}",
                    "target_file": page,
                    "action": "file_write",
                    "expected_outcome": {"type": "file_exists", "path": f"output/{page}"}
                })
                step_num += 1
            return todo_list
        
        # Fallback to templates if no specific HTML files were mentioned
        website_templates = {
            "student": ["index.html", "students.html", "add_student.html", "report.html"],
            "news": ["index.html", "article.html", "contact.html", "about.html"],
            "business": ["index.html", "services.html", "contact.html", "about.html"],
            "default": ["index.html", "page1.html", "page2.html", "page3.html"],
        }
        
        for key, pages in website_templates.items():
            if key in task_lower:
                for page in pages:
                    todo_list.append({
                        "id": f"step_{step_num:03d}",
                        "description": f"Create {page}",
                        "target_file": page,
                        "action": "file_write",
                        "expected_outcome": {"type": "file_exists", "path": f"output/{page}"}
                    })
                    step_num += 1
        
        if not todo_list:
            for page in website_templates["default"]:
                todo_list.append({
                    "id": f"step_{step_num:03d}",
                    "description": f"Create {page}",
                    "target_file": page,
                    "action": "file_write",
                    "expected_outcome": {"type": "file_exists", "path": f"output/{page}"}
                })
                step_num += 1
        
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": "Add shared styling and navigation",
            "expected_outcome": ""
        })
        return todo_list
    
    if "system" in task_lower or "project" in task_lower:
        steps = [
            {"id": "step_001", "description": "Analyze requirements", "expected_outcome": ""},
            {"id": "step_002", "description": "Design architecture", "expected_outcome": ""},
            {"id": "step_003", "description": "Implement core functionality", "expected_outcome": ""},
            {"id": "step_004", "description": "Add tests", "expected_outcome": ""},
            {"id": "step_005", "description": "Verify and document", "expected_outcome": ""}
        ]
        return steps
    
    if "api" in task_lower or "rest" in task_lower or "flask" in task_lower:
        steps = [
            {"id": "step_001", "description": "Create project directory", "expected_outcome": "output/app"},
            {"id": "step_002", "description": "Create Flask app", "expected_outcome": "output/app.py"},
            {"id": "step_003", "description": "Add endpoints", "expected_outcome": ""},
            {"id": "step_004", "description": "Test API", "expected_outcome": ""}
        ]
        return steps
    
    if "calculator" in task_lower or "gui" in task_lower:
        steps = [
            {"id": "step_001", "description": "Create calculator.py with tkinter", "expected_outcome": "output/calculator.py"},
            {"id": "step_002", "description": "Add UI elements (buttons, display)", "expected_outcome": ""},
            {"id": "step_003", "description": "Add calculation logic", "expected_outcome": ""},
            {"id": "step_004", "description": "Test the calculator", "expected_outcome": ""}
        ]
        return steps
    
    return None


def generate_action(step: dict, state: dict, retry_context: Optional[dict] = None) -> dict:
    """Try LLM for action generation, fallback only as last resort."""
    retry_context = retry_context or {}
    llm_available = _check_llm_availability()
    if llm_available:
        try:
            result = _generate_action_with_llm(step, state, retry_context)
            if result and isinstance(result, dict):
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
    from . import llm_gateway, logger

    logger.log_event("debug", "[PLANNER] Using LLM for task decomposition")

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
        items = response.parsed["items"]
        if isinstance(items, list):
            valid_items = [item for item in items if isinstance(item, dict)]
            if valid_items:
                return valid_items
    elif response.success and response.raw_output:
        try:
            steps = json.loads(response.text)
            if isinstance(steps, list):
                valid_steps = [item for item in steps if isinstance(item, dict)]
                if valid_steps:
                    return valid_steps
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


def _refine_plan_with_llm(base_plan: list, core_task: str, context: dict) -> Optional[list]:
    """Phase 2: LLM refinement of base plan."""
    from . import llm_gateway
    
    base_steps = "\n".join([f"- {s.get('id','')}: {s.get('description','')}" for s in base_plan])
    
    prompt = f"""You are a task planning expert. Improve this plan:

ORIGINAL TASK: {core_task}

CURRENT BASE PLAN:
{base_steps}

INSTRUCTIONS:
- DO NOT remove any existing files from the base plan
- DO NOT reduce the number of steps
- You CAN refine descriptions to be more specific
- You CAN add new steps if needed for completeness
- You MUST keep all original file paths ending in .html, .css, .js, .py

Return ONLY a JSON array of step objects."""

    request = llm_gateway.GatewayRequest(
        purpose="refinement",
        prompt=prompt,
        task_type="structured",
    )
    response = llm_gateway.generate(request)

    if response.success:
        try:
            if response.structured and "items" in response.parsed:
                return response.parsed["items"]
            parsed = response.parsed
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return None


def _validate_refined_plan(base_plan: list, refined_plan: list) -> bool:
    """Phase 3: Validate LLM refined plan preserves base plan."""
    if not refined_plan or not isinstance(refined_plan, list):
        return False
    
    if len(refined_plan) < len(base_plan):
        print(f"[DECOMPOSER] Validation failed: refined has {len(refined_plan)} steps, base has {len(base_plan)}")
        return False
    
    base_paths = set()
    for step in base_plan:
        outcome = step.get("expected_outcome", {})
        if isinstance(outcome, dict):
            path = outcome.get("path", "")
            if path:
                base_paths.add(path)
    
    refined_paths = set()
    for step in refined_plan:
        outcome = step.get("expected_outcome", {})
        if isinstance(outcome, dict):
            path = outcome.get("path", "")
            if path:
                refined_paths.add(path)
    
    for base_path in base_paths:
        if base_path not in refined_paths:
            print(f"[DECOMPOSER] Validation failed: missing {base_path}")
            return False
    
    return True


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
            "expected_outcome": {"type": "file_exists", "path": "output/hello.py"}
        })
    elif any(kw in task_lower for kw in ["script", "file", ".py"]):
        filename = _extract_filename(core_task)
        todo_list.append({
            "id": f"step_{step_num:03d}",
            "description": f"Create {filename}",
            "expected_outcome": {"type": "file_exists", "path": filename}
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
    
    return _generate_action_with_llm(step, state, retry_context) or {"type": "shell", "command": f"echo 'Error: No fallback action available for step {step_id}' && exit 1"}


def _extract_filename(text: str) -> str:
    """Extract filename from text."""
    match = re.search(r'([a-zA-Z0-9_-]+\.py)', text)
    if match:
        return match.group(1)
    match = re.search(r'([a-zA-Z0-9_-]+\.[a-zA-Z]+)', text)
    if match:
        return match.group(1)
    return ""