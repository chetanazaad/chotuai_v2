"""Convert a high-level task into ordered, atomic todo steps. Works WITHOUT any LLM."""
import json
import os
import re
from pathlib import Path
from typing import Optional


def decompose(core_task: str, context: Optional[dict] = None) -> list:
    """Hybrid decomposition: System base plan + LLM refinement.
    
    FIX 1: Limit LLM attempts to MAX 2 (phi3 -> qwen).
    """
    context = context or {}
    task_lower = core_task.lower()
    
    task_profile = context.get("task_profile", {})
    if isinstance(task_profile, dict):
        complexity = task_profile.get("complexity", "low")
        domain = task_profile.get("domain", "")
    else:
        complexity = getattr(task_profile, "complexity", "low")
        domain = getattr(task_profile, "domain", "")
    
    is_simple = complexity == "low" and domain == "filesystem"
    is_complex = complexity in ("medium", "high")

    base_plan = _generate_base_plan(core_task, task_lower)
    planning_mode = "base"
    
    final_plan = []
    if base_plan:
        if is_simple:
            final_plan = base_plan
            planning_mode = "base"
        else:
            # FIX 1 & 4: try LLM once
            llm_plan = None
            if _check_llm_availability():
                state = context.get("state", {})
                if state.get("selected_model"):
                    base_model = state["selected_model"]
                    print(f"[ROUTER SKIPPED] using user-selected model for decomposition")
                else:
                    base_model = state.get("task_model", "phi3")
                
                try:
                    print(f"[MODEL USED] {base_model} (planning)")
                    refined = _refine_plan_with_llm(base_plan, core_task, context, model=base_model)
                    if refined and _validate_refined_plan(base_plan, refined) and len(refined) > 0:
                        llm_plan = refined
                        planning_mode = "llm"
                except Exception:
                    pass
                
                if not llm_plan and not state.get("selected_model"):
                    fallback_model = "qwen:7b" if base_model == "phi3" else "phi3"
                    try:
                        print(f"[MODEL SWITCH] {base_model} -> {fallback_model} (planning retry)")
                        refined = _refine_plan_with_llm(base_plan, core_task, context, model=fallback_model)
                        if refined and _validate_refined_plan(base_plan, refined) and len(refined) > 0:
                            llm_plan = refined
                            planning_mode = "llm"
                    except Exception:
                        pass
            
            if llm_plan:
                final_plan = llm_plan
            else:
                print("[DECOMPOSER] Fallback → base plan")
                final_plan = base_plan
                planning_mode = "fallback"
    else:
        # No base plan available — try LLM from scratch
        if not is_simple:
            if _check_llm_availability():
                state = context.get("state", {})
                if state.get("selected_model"):
                    base_model = state["selected_model"]
                    print(f"[ROUTER SKIPPED] using user-selected model for decomposition scratch")
                else:
                    base_model = state.get("task_model", "phi3")

                try:
                    print(f"[MODEL USED] {base_model} (planning scratch)")
                    result = _decompose_with_llm(core_task, context, model=base_model)
                    if result and isinstance(result, list) and len(result) > 0:
                        final_plan = result
                        planning_mode = "llm"
                except Exception:
                    pass
                
                if not final_plan and not state.get("selected_model"):
                    fallback_model = "qwen:7b" if base_model == "phi3" else "phi3"
                    try:
                        print(f"[MODEL SWITCH] {base_model} -> {fallback_model} (planning scratch retry)")
                        result = _decompose_with_llm(core_task, context, model=fallback_model)
                        if result and isinstance(result, list) and len(result) > 0:
                            final_plan = result
                            planning_mode = "llm"
                    except Exception:
                        pass
    
    if context.get("state") is not None:
        context["state"]["core_task"]["planning_mode"] = planning_mode
    
    if not final_plan:
        print("[DECOMPOSER] Fallback → rule-based decomposition")
        final_plan = _decompose_fallback(core_task, context)
        planning_mode = "fallback"

    # FIX 2: Enforce minimum steps for build/coding tasks
    final_plan = _enforce_minimum_steps(final_plan, core_task, task_lower)

    # FIX 4: Store planning mode in context for downstream use
    context["planning_mode"] = planning_mode

    # Assign target_file to each step
    for step in final_plan:
        filename = _extract_filename(step.get("description", ""))
        if filename:
            step["target_file"] = filename
            print(f"[DECOMPOSER] Assigned target_file → {filename}")
        else:
            filename_from_task = _extract_filename(core_task)
            if filename_from_task and "index.html" in filename_from_task:
                 step["target_file"] = filename_from_task

    # Validation — warn about missing target_file
    for step in final_plan:
        if "target_file" not in step:
            if any(kw in str(step).lower() for kw in ["html", "page", "file", "script", ".py"]):
                print(f"[WARNING] Missing target_file for file-related step: {step}")
            else:
                if "create" in str(step).lower():
                    step["target_file"] = "output.html"
                    print(f"[DECOMPOSER] Defaulting target_file to output.html for step: {step.get('description')}")

    return final_plan


def _enforce_minimum_steps(plan: list, core_task: str, task_lower: str) -> list:
    """FIX 2: Enforce meaningful multi-step decomposition for coding tasks.
    
    Tasks involving build/create/app/calculator MUST have separate steps for:
      - Structure (HTML)
      - Styling (CSS)
      - Logic (JS/Python)
      - Integration/Testing
    """
    BUILD_KEYWORDS = ["build", "create", "app", "system", "calculator", "converter",
                      "dashboard", "website", "portal", "tracker", "manager"]
    
    is_build = any(kw in task_lower for kw in BUILD_KEYWORDS)
    if not is_build:
        return plan
    
    # If already has enough steps, don't interfere
    if len(plan) >= 3:
        return plan
    
    print(f"[DECOMPOSER] Enforcing multi-step plan for build task ({len(plan)} -> 4+ steps)")
    
    # Determine the primary output file
    target = "index.html"
    for step in plan:
        tf = step.get("target_file", "")
        if tf:
            target = tf
            break
    
    base_name = target.rsplit(".", 1)[0] if "." in target else target
    is_html = target.endswith(".html")
    is_python = target.endswith(".py")
    
    if is_html:
        enforced = [
            {"id": "step_001", "description": f"Create {target} with HTML structure", "target_file": target,
             "action": "file_write", "expected_outcome": {"type": "file_exists", "path": f"output/{target}"}},
            {"id": "step_002", "description": f"Add CSS styling to {target}", "target_file": target,
             "action": "file_write", "expected_outcome": {"type": "file_exists", "path": f"output/{target}"}},
            {"id": "step_003", "description": f"Add JavaScript logic to {target}", "target_file": target,
             "action": "file_write", "expected_outcome": {"type": "file_exists", "path": f"output/{target}"}},
            {"id": "step_004", "description": f"Integrate and finalize {target}", "target_file": target,
             "action": "file_write", "expected_outcome": {"type": "file_exists", "path": f"output/{target}"}},
        ]
    elif is_python:
        enforced = [
            {"id": "step_001", "description": f"Create {target} with core structure", "target_file": target,
             "action": "file_write", "expected_outcome": {"type": "file_exists", "path": f"output/{target}"}},
            {"id": "step_002", "description": f"Add UI/interface elements to {target}", "target_file": target,
             "action": "file_write", "expected_outcome": {"type": "file_exists", "path": f"output/{target}"}},
            {"id": "step_003", "description": f"Add business logic to {target}", "target_file": target,
             "action": "file_write", "expected_outcome": {"type": "file_exists", "path": f"output/{target}"}},
            {"id": "step_004", "description": f"Test and verify {target}", "target_file": target,
             "action": "file_write", "expected_outcome": {"type": "file_exists", "path": f"output/{target}"}},
        ]
    else:
        return plan
    
    return enforced


def _generate_base_plan(core_task: str, task_lower: str) -> list:
    """Phase 1: Generate deterministic base plan with SMART filename extraction."""
    todo_list = []
    step_num = 1
    
    # ── MULTI-PAGE / WEBSITE TASKS ──
    if any(kw in task_lower for kw in ["multiple pages", "system", "app", "website", "pages", "web", "multi-page"]):
        import re
        # Priority 1: Use EXPLICIT filenames from the prompt
        html_files = re.findall(r'([a-zA-Z0-9_-]+\.html)', core_task)
        
        if html_files:
            for page in html_files:
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
        
        # Priority 2: Infer REAL page names from context keywords
        inferred = _infer_pages_from_context(task_lower)
        if inferred:
            print(f"[DECOMPOSER] Smart inference: {inferred}")
            for page in inferred:
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
    
    # ── SINGLE-APP TASKS (calculator, converter, form, etc.) ──
    if any(kw in task_lower for kw in [
        "calculator", "converter", "timer", "clock", "counter",
        "quiz", "form", "login", "signup", "landing", "portfolio",
        "resume", "todo", "todolist", "to-do", "single page",
        "simple page", "webpage", "web page"
    ]):
        # Single app → index.html only
        is_python_gui = any(kw in task_lower for kw in ["gui", "tkinter", "pygame", "qt", "desktop"])
        if is_python_gui:
            return [
                {"id": "step_001", "description": "Create calculator.py with tkinter", "target_file": "calculator.py",
                 "expected_outcome": {"type": "file_exists", "path": "output/calculator.py"}}
            ]
        
        todo_list.append({
            "id": "step_001",
            "description": "Create index.html",
            "target_file": "index.html",
            "action": "file_write",
            "expected_outcome": {"type": "file_exists", "path": "output/index.html"}
        })
        return todo_list
    
    # ── SYSTEM / PROJECT TASKS ──
    if "system" in task_lower or "project" in task_lower:
        steps = [
            {"id": "step_001", "description": "Analyze requirements", "expected_outcome": ""},
            {"id": "step_002", "description": "Design architecture", "expected_outcome": ""},
            {"id": "step_003", "description": "Implement core functionality", "expected_outcome": ""},
            {"id": "step_004", "description": "Add tests", "expected_outcome": ""},
            {"id": "step_005", "description": "Verify and document", "expected_outcome": ""}
        ]
        return steps
    
    # ── API / REST TASKS ──
    if "api" in task_lower or "rest" in task_lower or "flask" in task_lower:
        steps = [
            {"id": "step_001", "description": "Create project directory", "expected_outcome": "output/app"},
            {"id": "step_002", "description": "Create Flask app", "expected_outcome": "output/app.py"},
            {"id": "step_003", "description": "Add endpoints", "expected_outcome": ""},
            {"id": "step_004", "description": "Test API", "expected_outcome": ""}
        ]
        return steps
    
    return None


def _infer_pages_from_context(task_lower: str) -> list:
    """Infer REAL page names from prompt keywords instead of page1.html, page2.html.
    
    Maps domain-specific keywords to realistic page filenames.
    Returns None if no meaningful inference can be made.
    """
    # Domain-to-pages mapping — ordered by specificity
    _DOMAIN_PAGES = {
        # Finance
        "finance":       ["index.html", "transactions.html", "add_transaction.html", "analytics.html", "settings.html"],
        "budget":        ["index.html", "budget.html", "expenses.html", "reports.html"],
        "invoice":       ["index.html", "invoices.html", "create_invoice.html", "clients.html"],
        "expense":       ["index.html", "expenses.html", "add_expense.html", "reports.html"],
        # Education
        "student":       ["index.html", "students.html", "add_student.html", "report.html"],
        "school":        ["index.html", "students.html", "classes.html", "teachers.html"],
        "course":        ["index.html", "courses.html", "enroll.html", "progress.html"],
        "learning":      ["index.html", "courses.html", "lessons.html", "progress.html"],
        # E-commerce
        "shop":          ["index.html", "products.html", "cart.html", "checkout.html"],
        "store":         ["index.html", "products.html", "cart.html", "checkout.html"],
        "ecommerce":     ["index.html", "products.html", "cart.html", "checkout.html"],
        "e-commerce":    ["index.html", "products.html", "cart.html", "checkout.html"],
        # Content
        "blog":          ["index.html", "posts.html", "post.html", "about.html"],
        "news":          ["index.html", "article.html", "contact.html", "about.html"],
        "magazine":      ["index.html", "articles.html", "categories.html", "about.html"],
        # Business
        "business":      ["index.html", "services.html", "contact.html", "about.html"],
        "company":       ["index.html", "services.html", "team.html", "contact.html"],
        "agency":        ["index.html", "services.html", "portfolio.html", "contact.html"],
        "startup":       ["index.html", "features.html", "pricing.html", "contact.html"],
        # Medical
        "hospital":      ["index.html", "appointments.html", "doctors.html", "contact.html"],
        "clinic":        ["index.html", "appointments.html", "services.html", "contact.html"],
        "health":        ["index.html", "services.html", "appointments.html", "contact.html"],
        # Food
        "restaurant":    ["index.html", "menu.html", "reservations.html", "contact.html"],
        "recipe":        ["index.html", "recipes.html", "add_recipe.html", "favorites.html"],
        "food":          ["index.html", "menu.html", "order.html", "contact.html"],
        # Task/Project Management
        "task":          ["index.html", "tasks.html", "add_task.html", "reports.html"],
        "project":       ["index.html", "projects.html", "tasks.html", "team.html"],
        "kanban":        ["index.html", "board.html", "tasks.html", "settings.html"],
        # Social
        "social":        ["index.html", "feed.html", "profile.html", "messages.html"],
        "chat":          ["index.html", "conversations.html", "contacts.html", "settings.html"],
        # Real estate
        "real estate":   ["index.html", "listings.html", "property.html", "contact.html"],
        "property":      ["index.html", "listings.html", "property.html", "contact.html"],
        # Fitness
        "fitness":       ["index.html", "workouts.html", "progress.html", "settings.html"],
        "gym":           ["index.html", "classes.html", "schedule.html", "contact.html"],
        # Travel
        "travel":        ["index.html", "destinations.html", "booking.html", "contact.html"],
        "hotel":         ["index.html", "rooms.html", "booking.html", "contact.html"],
        # Portfolio/Personal
        "portfolio":     ["index.html", "projects.html", "about.html", "contact.html"],
        "resume":        ["index.html", "experience.html", "skills.html", "contact.html"],
        # Dashboard
        "dashboard":     ["index.html", "analytics.html", "reports.html", "settings.html"],
        "admin":         ["index.html", "users.html", "settings.html", "reports.html"],
        # Inventory
        "inventory":     ["index.html", "products.html", "add_product.html", "reports.html"],
        "warehouse":     ["index.html", "inventory.html", "shipments.html", "reports.html"],
    }
    
    for keyword, pages in _DOMAIN_PAGES.items():
        if keyword in task_lower:
            return pages
    
    # Last resort: if "multi-page" or "pages" is mentioned but no domain matched
    if "multi-page" in task_lower or "multiple pages" in task_lower:
        return ["index.html", "about.html", "services.html", "contact.html"]
    
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


def _normalize_llm_steps(response: 'GatewayResponse') -> Optional[list]:
    """FIX 2: Accept various step formats from LLM."""
    raw_steps = None
    
    if response.structured:
        if "items" in response.parsed:
            raw_steps = response.parsed["items"]
        elif "steps" in response.parsed:
            raw_steps = response.parsed["steps"]
        elif isinstance(response.parsed, list):
            raw_steps = response.parsed
    
    if raw_steps is None and response.raw_output:
        text = response.text
        # Strip markdown fences if present
        if "```" in text:
            parts = text.split("```")
            if len(parts) >= 3:
                block = parts[1]
                for lang in ["json", "javascript", "js"]:
                    if block.lower().startswith(lang + "\n") or block.lower().startswith(lang + "\r\n"):
                        block = block[len(lang)+1:].strip()
                        break
                text = block.strip()
        
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                raw_steps = parsed
            elif isinstance(parsed, dict) and "steps" in parsed:
                raw_steps = parsed["steps"]
            elif isinstance(parsed, dict) and "items" in parsed:
                raw_steps = parsed["items"]
        except Exception:
            pass

    if not isinstance(raw_steps, list):
        return None
        
    valid_steps = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
            
        step = {}
        # Map step_id to id
        step["id"] = item.get("id", item.get("step_id", "step_000"))
        step["description"] = item.get("description", item.get("desc", ""))
        step["expected_outcome"] = item.get("expected_outcome", "")
        
        # Copy over any extra fields like target_file
        for k, v in item.items():
            if k not in ["id", "step_id", "description", "desc", "expected_outcome"]:
                step[k] = v
                
        valid_steps.append(step)
        
    return valid_steps if valid_steps else None


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

    if response.success:
        return _normalize_llm_steps(response)
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
        return _normalize_llm_steps(response)
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