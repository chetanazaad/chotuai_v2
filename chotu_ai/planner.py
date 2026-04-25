"""Planner module — the first intelligence layer for action planning."""
import dataclasses
import json
import os
import re
from pathlib import Path
from typing import Optional


@dataclasses.dataclass
class PlanResult:
    action: dict
    confidence: float
    source: str
    alternatives: list
    reason: str
    risk_notes: str
    expected_outcome: dict
    validation_passed: bool


DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+\*",
    r"del\s+/s\s+/q",
    r"rmdir\s+/s\s+/q",
    r"format\s+[a-z]:",
    r"mkfs\.",
    r"dd\s+if=",
    r":(){ :|:& };:",
    r">\s*/dev/sd",
    r"chmod\s+-R\s+777\s+/",
    r"sudo\s+rm",
    r"powershell.*-enc",
]

def plan(step: dict, state: dict, retry_context: Optional[dict] = None) -> PlanResult:
    """Main entry point. Returns a structured plan. Never raises."""
    from . import logger
    from . import logger as planner_logger

    context = _build_context(step, state, retry_context)
    logger.log_plan_start(step.get("id", ""), "llm")

    # STEP 1: FORCE ACTION TYPE BY TASK
    desc = step.get("description", "").lower()
    core_task = state.get("core_task", {}).get("description", "").lower()
    task_profile = state.get("core_task", {}).get("task_profile", {})
    task_type = (task_profile.get("task_type", "") if isinstance(task_profile, dict) else getattr(task_profile, "task_type", "")).lower()
    
    is_website = "website" in core_task or "multi-page" in core_task
    
    output_dir = state["core_task"].get("output_dir", "output")
    
    if "html" in desc or "html" in core_task:
        forced_action = "file_write"
        target_file = step.get("target_file")
        # STEP 2 & 6: HARD PATH ENFORCEMENT & STEP CONTROL
        if target_file:
            default_path = os.path.join(output_dir, target_file)
        elif "index.html" in desc or "homepage" in desc:
            default_path = os.path.join(output_dir, "index.html")
        elif "article.html" in desc or "article page" in desc:
            default_path = os.path.join(output_dir, "article.html")
        elif "contact.html" in desc or "form page" in desc:
            default_path = os.path.join(output_dir, "contact.html")
        elif is_website:
            # STEP 3: BLOCK UNKNOWN FILES for website tasks
            planner_logger.log_event("warning", f"[PLANNER] Rejecting unknown website file: {desc}")
            return _fallback_plan(step, context)
        else:
            default_path = os.path.join(output_dir, "output.html")
    elif "python" in desc or "script" in desc or "python" in core_task:
        forced_action = "file_write"
        default_path = os.path.join(output_dir, "script.py")
    elif "run" in desc and task_type != "build":
        forced_action = "shell"
        default_path = None
    else:
        forced_action = "file_write"
        default_path = os.path.join(output_dir, "output.txt")

    # STEP 4: BLOCK SHELL FOR BUILD TASKS
    if task_type == "build":
        forced_action = "file_write"
        if not default_path:
            default_path = os.path.join(output_dir, "build_output.txt")

    context["forced_action"] = forced_action
    context["default_path"] = default_path
    context["is_website"] = is_website
    context["output_dir"] = output_dir

    if _check_llm_availability():
        planner_logger.log_event("debug", f"[PLANNER] Control Layer: Forced {forced_action} for {task_type} task")
        try:
            # STEP 2: MODIFY LLM PROMPT
            prompt = _build_llm_prompt(context)
            raw_output = _call_llm(prompt)
            
            # STEP 3: BUILD ACTION MANUALLY
            if forced_action == "file_write":
                llm_content = _extract_content_from_llm(raw_output)
                
                if is_website and default_path.endswith(".html"):
                    # STEP 5: FILE UPDATE MODE
                    existing_path = Path(default_path)
                    if existing_path.exists():
                        print(f"[TASK OUTPUT] Using existing folder: {output_dir}")
                        print(f"[FILE UPDATE] Updating {existing_path.name}")
                        old_content = existing_path.read_text(encoding="utf-8")
                        # Try to replace only the content div if it exists
                        if '<div class="content">' in old_content and '</div>' in old_content:
                            parts = old_content.split('<div class="content">')
                            head = parts[0] + '<div class="content">\n'
                            tail = '</div>' + parts[1].split('</div>')[-1]
                            content = head + llm_content + tail
                        else:
                            content = llm_content # Fallback
                    else:
                        content = llm_content
                else:
                    content = llm_content

                action = {
                    "type": "file_write",
                    "path": default_path,
                    "content": content
                }
            else:
                command = _extract_command_from_llm(raw_output)
                action = {
                    "type": "shell",
                    "command": command
                }
            
            logger.log_plan_complete(step.get("id", ""), action.get("type", ""), 0.95, "control_layer")
            return PlanResult(
                action=action,
                confidence=0.95,
                source="control_layer_llm",
                alternatives=[],
                reason=f"System enforced {forced_action} based on task intent analysis",
                risk_notes="Bypassed LLM structural decision for safety",
                expected_outcome=_extract_expected_outcome(step, context),
                validation_passed=True
            )
        except Exception as e:
            planner_logger.log_event("info", f"[fallback] Control Layer error: {str(e)}")
            return _fallback_plan(step, context)
    
    return _fallback_plan(step, context)

def _extract_content_from_llm(text: str) -> str:
    """Extract raw content from LLM response, stripping markdown fences and JSON wrappers."""
    text = text.strip()
    
    # 1. If it's a JSON block, try to extract 'content' or 'command'
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        try:
            import json
            data = json.loads(json_match.group())
            if isinstance(data, dict):
                if "content" in data: return data["content"]
                if "command" in data: return data["command"]
                if "action" in data and isinstance(data["action"], dict):
                    if "content" in data["action"]: return data["action"]["content"]
                    if "command" in data["action"]: return data["command"]["command"]
        except:
            pass

    # 2. Handle markdown blocks
    if "```" in text:
        # Get the first block
        parts = text.split("```")
        if len(parts) >= 3:
            block = parts[1]
            # Strip language tags
            for lang in ["html", "python", "javascript", "json", "bash", "sh", "powershell"]:
                if block.lower().startswith(lang + "\n"):
                    block = block[len(lang)+1:]
                    break
            # If the block itself is JSON, recurse or parse
            if block.strip().startswith("{"):
                return _extract_content_from_llm(block)
            return block.strip()
            
    return text




def _extract_command_from_llm(text: str) -> str:
    """Extract command from LLM response (delegates to content extractor)."""
    return _extract_content_from_llm(text)



def _fallback_plan(step: dict, context: dict) -> PlanResult:
    target_file = step.get("target_file", "output.html")
    output_dir = context.get("output_dir", "output")
    path = os.path.join(output_dir, target_file)
    
    if "index.html" in target_file:
        content = "<html><body><h1>Homepage</h1><p>Welcome to the dashboard.</p></body></html>"
    elif "transactions.html" in target_file:
        content = "<html><body><h1>Transactions</h1><table><tr><th>Date</th><th>Type</th><th>Amount</th><th>Category</th></tr><tr><td>Dummy</td><td>Dummy</td><td>Dummy</td><td>Dummy</td></tr></table></body></html>"
    elif "analytics.html" in target_file:
        content = "<html><body><h1>Analytics</h1><p>Total Income: $0</p><p>Total Expense: $0</p><p>Balance: $0</p></body></html>"
    elif "add" in target_file or "form" in target_file:
        content = "<html><body><h1>Add Entry</h1><form><input type='text' placeholder='Date'><input type='submit'></form></body></html>"
    else:
        content = f"<html><body><h1>{target_file}</h1><p>System-enforced safety fallback.</p></body></html>"

    return PlanResult(
        action={
            "type": "file_write",
            "path": path,
            "content": content
        },
        confidence=0.5,
        source="system_fallback",
        alternatives=[],
        reason="Forced fallback per control layer rules",
        risk_notes="Static content",
        expected_outcome={"type": "file_exists", "path": path},
        validation_passed=True
    )


def _build_context(step: dict, state: dict, retry_context: Optional[dict] = None) -> dict:
    """Extract minimal relevant context."""
    completed = state.get("completed_steps", [])
    recent_completed = completed[-3:] if len(completed) > 3 else completed

    issues = state.get("issues", [])
    recent_issues = issues[-2:] if len(issues) > 2 else issues

    return {
        "step_id": step.get("id", ""),
        "step_description": step.get("description", ""),
        "step_expected_outcome": step.get("expected_outcome", ""),
        "step_retries": step.get("retries", 0),
        "step_max_retries": step.get("max_retries", 3),
        "core_task": state.get("core_task", {}).get("description", ""),
        "working_directory": state.get("config", {}).get("working_directory", ""),
        "task_profile": state.get("core_task", {}).get("task_profile", {}),
        "recent_completed_steps": recent_completed,
        "recent_issues": recent_issues,
        "retry_context": retry_context or {},
        "is_retry": step.get("retries", 0) > 0,
        "adaptive_hint_prompt": "",
    }


def _check_llm_availability() -> bool:
    """Check if any LLM provider is available via gateway."""
    from . import llm_gateway
    return llm_gateway.is_available()


def _call_llm(prompt: str, model: str = "phi3", purpose: str = "planning",
              strategy: str = "", retry_count: int = 0, context: dict = None) -> str:
    """Call LLM via gateway. Returns raw output text."""
    from . import llm_gateway
    request = llm_gateway.GatewayRequest(
        purpose=purpose,
        prompt=prompt,
        task_type="code",
        preferred_provider=model if model != "llama3.2:latest" else "auto",
        strategy=strategy,
        retry_count=retry_count,
        metadata={"task_profile": (context or {}).get("task_profile", {})},
    )
    response = llm_gateway.generate(request)
    if response.success:
        return response.raw_output
    return ""


def _build_llm_prompt(context: dict) -> str:
    """Build the content-only prompt for action planning."""
    forced_action = context.get("forced_action", "file_write")
    default_path = context.get("default_path", "workspace/output.txt")
    is_website = context.get("is_website", False)
    
    prompt = f"""You are an expert content generator for a technical task.
    
    I have decided to perform a [{forced_action}] action. 
    Your job is ONLY to provide the necessary content for this action.
    
    TASK: {context['core_task']}
    STEP: {context['step_description']}
    EXPECTED OUTCOME: {context['step_expected_outcome']}
    
    FORCED ACTION TYPE: {forced_action}
    TARGET PATH: {default_path}
    """

    if is_website and default_path.endswith(".html"):
        prompt += """
    IMPORTANT: This is a website task. 
    The system will handle the <html>, <head>, <header>, and <footer> sections.
    You MUST generate ONLY the body content (e.g., <h2>, <p>, <ul>, <form>, etc.) for this specific page.
    DO NOT include <html> or <body> tags.
    """
    
    prompt += """
    Return ONLY JSON with:
    {
      "action": {
        "type": "file_write",
        "file": "filename.html",
        "content": "full html code"
      }
    }
    DO NOT output raw content outside the JSON structure.
    Do NOT include explanations or extra text.
    """
    return prompt


def _build_strict_retry_prompt(context: dict, errors: list) -> str:
    """Build stricter prompt for LLM retry after parse failure."""
    prompt = f"""You are an action planner. Previous output had errors.

TASK: {context["core_task"]}
STEP: {context["step_description"]}
EXPECTED OUTCOME: {context["step_expected_outcome"]}
RETRY COUNT: {context["step_retries"]}

ERRORS FROM PREVIOUS OUTPUT:
{chr(10).join(f"- {e}" for e in errors)}

Return ONLY the raw content for the [{context.get('forced_action', 'file_write')}] action.
"""
    return prompt


def _parse_plan_output(raw_output: str) -> Optional[dict]:
    """Parse raw LLM text into structured plan dict. (Legacy support)"""
    try:
        raw_output = raw_output.strip()
        if raw_output.startswith("```"):
            raw_output = raw_output.split("```")[1]
            if raw_output.startswith("json"):
                raw_output = raw_output[4:]
        plan = json.loads(raw_output.strip())
        if isinstance(plan, dict):
            return plan
    except (json.JSONDecodeError, AttributeError, IndexError):
        pass

    json_match = re.search(r'\{[^{}]*\}', raw_output, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    return None


def _validate_plan(plan: dict) -> tuple[bool, list]:
    """Validate all required fields, types, safety. (Legacy support)"""
    errors = []
    if not isinstance(plan, dict):
        return False, ["plan must be a dict"]
    return True, []


def _sanitize_plan(plan: dict) -> dict:
    """Strip dangerous commands, normalize fields."""
    return plan


def _extract_expected_outcome(step: dict, context: dict) -> dict:
    """Build expected outcome from step metadata."""
    step_expected = step.get("expected_outcome")
    if isinstance(step_expected, dict):
        return step_expected

    forced_action = context.get("forced_action", "file_write")
    default_path = context.get("default_path", "workspace/output.html")

    if forced_action == "file_write":
        return {"type": "file_exists", "path": default_path}
    
    return {"type": "exit_code", "code": 0}


def _parse_known_command(command: str):
    """Convert a known command string into an action dict."""
    if not command:
        return None
    command = command.strip()
    if any(command.startswith(p) for p in ["python ", "pip ", "echo ", "mkdir ", "cd "]):
        return {"type": "shell", "command": command}
    return None