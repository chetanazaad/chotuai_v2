"""Orchestrate the entire Phase 1 loop. THE brain. The ONLY state writer."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import state_manager, logger, executor, evaluator, task_decomposer, planner, validator, decision_engine, smart_memory, filtered_search, feedback_learning, knowledge_store, task_classifier, output_formatter, artifact_manager, ui_renderer, loop_controller, task_graph


def _generate_shared_layout(task: str, html_files: list) -> dict:
    """Generate shared layout for multi-page HTML apps via LLM."""
    from . import llm_gateway, llm_gateway as gateway

    pages_str = ", ".join(html_files)
    prompt = f"""Generate a shared HTML layout for a multi-page web application.

Task: {task}
Pages: {pages_str}

Generate a JSON object with the following keys:
- "header": HTML for the header (logo, title, etc.)
- "navbar": HTML navigation bar with links to ALL pages: {pages_str}
- "footer": HTML footer with copyright/info
- "css": Shared CSS styles (optional, can be inline)

Respond with ONLY valid JSON, no explanation."""

    try:
        req = gateway.GatewayRequest(
            purpose="layout_generation",
            prompt=prompt,
            preferred_provider="phi3",
            temperature=0.2,
            max_tokens=2048
        )
        resp = gateway.generate(req)
        if resp.success and resp.text:
            import json
            import re
            text = resp.text.strip()
            text = re.sub(r'^```json\s*', '', text, flags=re.MULTILINE)
            text = re.sub(r'^```\s*$', '', text, flags=re.MULTILINE)
            text = text.strip()
            layout = json.loads(text)
            return layout
    except Exception as e:
        print(f"[LAYOUT] Generation failed: {e}")
    return {}


def handle_command(command: str, args: dict) -> bool:
    """Main dispatch for all CLI commands."""
    if command == "new":
        return _run_new(args.get("task", ""), args.get("working_dir", None), args.get("auto_run", False), args.get("force_new", False))
    elif command == "run":
        return _run_loop()
    elif command == "status":
        return _display_status()
    elif command == "cache":
        from . import llm_cache
        stats = llm_cache.get_stats()
        print(f"[LLM CACHE] Entries: {stats['entries']}")
        print(f"[LLM CACHE] Hits: {stats['hits']} | Misses: {stats['misses']}")
        print(f"[LLM CACHE] Hit Rate: {stats['hit_rate']:.1%}")
        return True
    elif command == "plan":
        return _display_plan()
    elif command == "log":
        task_id = args.get("task_id")
        if task_id:
            return _display_task_log(task_id)
def _run_new(task: str, working_dir: Optional[str] = None, auto_run: bool = False, force_new: bool = False) -> bool:
    """Create state, decompose, optionally auto-run."""
    import re
    from . import system_check

    readiness = system_check.full_check()
    if not readiness["ready"]:
        print(f"[SYSTEM] Not ready: {readiness}")
        return False

    state = None
    if not task:
        print("Error: Task description required")
        return False
    base_dir = Path.cwd()
    runtime_dir = state_manager.ensure_runtime_dirs(base_dir)
    logger.init(runtime_dir)
    _cleanup_workspace()
    state = state_manager.create_fresh_state(task, working_dir)

    task_hash = state_manager.get_task_hash(task)
    existing_task = None
    if not force_new:
        from . import task_index
        existing_task = task_index.get_task_by_hash(task_hash)

    if existing_task:
        print(f"[TASK REUSED] Using existing folder: {existing_task['output_dir']}")
        state["core_task"]["task_id"] = existing_task["task_id"]
        state["core_task"]["output_dir"] = existing_task["output_dir"]
        state["core_task"]["status"] = "running"
        task_index.update_status(existing_task["task_id"], "running")
    else:
        task_dir = state["core_task"]["output_dir"]
        os.makedirs(task_dir, exist_ok=True)
        print(f"[TASK OUTPUT] Created folder: {task_dir}")
        task_index.add_task(
            state["core_task"]["task_id"],
            state["core_task"]["description"],
            state["core_task"]["output_dir"],
            task_hash=task_hash
        )

    artifact_manager.init(runtime_dir, state.get("core_task", {}).get("task_id", "default"))
    state_manager.save(state)

    logger.log_classify_start(task)
    profile = task_classifier.classify(task)
    if profile:
        logger.log_classify_result(profile.task_type, profile.domain, profile.complexity, profile.confidence)

    import dataclasses
    state["core_task"]["task_profile"] = dataclasses.asdict(profile) if profile else {}

    # FIX: MODEL LOCK & CONSISTENT MODEL
    forced_model = os.environ.get("CHOTU_FORCED_MODEL", "")
    if forced_model and forced_model != "auto":
        state["selected_model"] = forced_model
        print(f"[MODEL LOCKED] {forced_model}")
    else:
        state["selected_model"] = None
        from . import model_router
        decision = model_router.select_model(
            purpose="task_init",
            task_profile=state["core_task"]["task_profile"]
        )
        state["task_model"] = decision.model

    html_files = re.findall(r'([a-zA-Z0-9_-]+\.html)', task)
    if len(html_files) > 1 and force_new:
        print(f"[LAYOUT] Detected {len(html_files)} HTML pages, generating shared layout...")
        shared_layout = _generate_shared_layout(task, html_files)
        if shared_layout:
            state["core_task"]["shared_layout"] = shared_layout
            print("[LAYOUT] Shared layout generated successfully")

    try:
        ui_renderer.render_task_header(task, dataclasses.asdict(profile))
    except Exception:
        state["core_task"]["task_profile"] = {}

    state_manager.save(state)
    logger.log_task_start(task, 0)
    logger.log_task_decompose_start(task)
    context = {
        "task_profile": state["core_task"].get("task_profile", {}),
        "state": state
    }
    todo_list = task_decomposer.decompose(task, context=context)
    
    # FIX 4: Store planning mode so executor knows if LLM was used
    state["core_task"]["planning_mode"] = context.get("planning_mode", "unknown")
    print(f"[PLANNING MODE] {state['core_task']['planning_mode']}")

    for step_data in todo_list:
        step = state_manager.create_step(
            step_data.get("id", "step_000"),
            step_data.get("description", ""),
            step_data.get("depends_on", []),
            step_data.get("expected_outcome", ""),
            state["config"]["max_retries_per_step"]
        )
        # Preserve all extra fields from decomposer (e.g. target_file)
        for k, v in step_data.items():
            if k not in step:
                step[k] = v
        state["todo_list"].append(step)
    state["core_task"]["status"] = "decomposing"
    state_manager.save(state)
    logger.log_task_decompose_complete(len(todo_list))
    logger.log_event("task_created", f"Task decomposed into {len(todo_list)} steps")

    try:
        graph = task_graph.build(state["todo_list"])
        logger.log_graph_build(len(graph.nodes), sum(len(d) for d in graph.edges.values()), graph.is_valid)
        if not graph.is_valid:
            for err in graph.validation_errors:
                logger.log_event("graph_warning", err)
        logger.log_graph_order(graph.order)
    except Exception:
        pass

    state["core_task"]["status"] = "pending"
    state_manager.save(state)
    ui_renderer.render_plan(state["todo_list"])

    if auto_run:
        return _run_loop()
    else:
        ui_renderer.render_message("info", "Task planned. Run 'chotu run' to execute.")
        return True


def _display_task_log(task_id: str) -> bool:
    """Display task execution log."""
    from . import logger
    from pathlib import Path
    
    logger.init(Path(".chotu"))
    log_file = Path(".chotu/logs") / f"{task_id}.log"
    
    if not log_file.exists():
        print(f"[ERROR] No log file found for task: {task_id}")
        return False
    
    print(f"=== Task Log: {task_id} ===")
    with open(log_file, "r", encoding="utf-8") as f:
        print(f.read())
    return True


def _display_task_log(task_id: str) -> bool:
    """Display task execution log."""
    from . import logger
    from pathlib import Path
    
    logger.init(Path(".chotu"))
    log_file = Path(".chotu/logs") / f"{task_id}.log"
    
    if not log_file.exists():
        print(f"[ERROR] No log file found for task: {task_id}")
        return False
    
    print(f"=== Task Log: {task_id} ===")
    with open(log_file, "r", encoding="utf-8") as f:
        print(f.read())
    return True


def _display_issues() -> bool:
    """Display issues for the current task."""
    from . import logger
    from pathlib import Path
    
    logger.init(Path(".chotu"))
    events_file = Path(".chotu/events.jsonl")
    
    if not events_file.exists():
        print("No events file found.")
        return False
    
    print("=== Task Issues ===")
    issues = []
    with open(events_file, "r", encoding="utf-8") as f:
        for line in f:
            if '"event_type": "issue"' in line:
                issues.append(line)
    
    if not issues:
        print("No issues found.")
        return True
    
    for issue in issues[-10:]:
        print(issue.strip())
    return True


def _skip_step() -> bool:
    """Skip current step."""
    state_manager.save(state)

    logger.log_classify_start(task)
    try:
        import dataclasses
        profile = task_classifier.classify(task)
        state["core_task"]["task_profile"] = dataclasses.asdict(profile)
        logger.log_classify_result(profile.task_type, profile.domain, profile.complexity, profile.confidence)
        if profile.uncertainty_notes:
            logger.log_classify_uncertain(profile.task_type, profile.uncertainty_notes)
        time_est = profile.estimated_time
        time_str = f"~{time_est['min_seconds']}-{time_est['max_seconds']}s"
        import dataclasses
        ui_renderer.render_task_header(task, dataclasses.asdict(profile))
    except Exception:
        state["core_task"]["task_profile"] = {}

    state_manager.save(state)
    logger.log_task_start(task, 0)
    logger.log_task_decompose_start(task)
    context = {"task_profile": state["core_task"].get("task_profile", {})}
    todo_list = task_decomposer.decompose(task, context=context)
    for step_data in todo_list:
        step = state_manager.create_step(
            step_data.get("id", "step_000"),
            step_data.get("description", ""),
            step_data.get("depends_on", []),
            step_data.get("expected_outcome", ""),
            state["config"]["max_retries_per_step"]
        )
        state["todo_list"].append(step)
    state["core_task"]["status"] = "decomposing"
    state_manager.save(state)
    logger.log_task_decompose_complete(len(todo_list))
    logger.log_event("task_created", f"Task decomposed into {len(todo_list)} steps")

    try:
        graph = task_graph.build(state["todo_list"])
        logger.log_graph_build(len(graph.nodes), sum(len(d) for d in graph.edges.values()), graph.is_valid)
        if not graph.is_valid:
            for err in graph.validation_errors:
                logger.log_event("graph_warning", err)
        logger.log_graph_order(graph.order)
    except Exception:
        pass

    state["core_task"]["accepted_at"] = datetime.now(timezone.utc).isoformat()
    state["core_task"]["status"] = "pending"
    state["stats"] = state_manager.recompute_stats(state)
    state_manager.save(state)
    ui_renderer.render_plan(state.get("todo_list", []))
    if auto_run:
        return _run_loop()
    return True


def _cleanup_workspace():
    """Purge output/ and tmp/ before every new task."""
    import shutil
    import os
    from pathlib import Path
    
    # STEP 8: CLEAN OLD GLOBAL OUTPUT
    if os.path.exists("output"):
        for item in os.listdir("output"):
            path = os.path.join("output", item)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
    
    if os.path.exists("tmp"):
        try:
            shutil.rmtree("tmp")
            print("[CLEANUP] Purged tmp/ directory")
        except Exception:
            pass
    
    # Remove stray shell scripts from root
    try:
        for f in os.listdir("."):
            if f.endswith(".sh") or f.endswith(".bash"):
                try:
                    os.remove(f)
                    print(f"[CLEANUP] Removed stray script: {f}")
                except Exception:
                    pass
    except Exception:
        pass


def _run_loop() -> bool:
    """The core generate→execute→evaluate→retry loop."""
    base_dir = Path.cwd()
    state = state_manager.load(base_dir)
    if state is None:
        print("Error: No task found. Use 'chotu new <task>' first.")
        return False
    runtime_dir = state_manager.get_runtime_dir(base_dir)
    logger.init(runtime_dir)
    artifact_manager.init(runtime_dir, state.get("core_task", {}).get("task_id", "default"))
    state = _recover_state(state)
    from . import task_index
    task_index.update_status(state["core_task"]["task_id"], "running")
    
    logger.log_task_start(state["core_task"]["description"], state["stats"]["total_steps"])

    import time as _time
    _task_start_time = _time.time()

    todo_list = state.get("todo_list", [])
    if not todo_list or len(todo_list) == 0:
        print("[FATAL] Empty execution plan detected")
        print("[FATAL] Attempting fallback plan recovery")

        # fallback minimal plan
        todo_list = [{
            "id": "step_001",
            "description": "Execute basic file generation",
            "action": {"type": "file_write"},
            "status": "pending"
        }]
        state["todo_list"] = todo_list
        state_manager.save(state)

    print(f"[DEBUG] Total steps: {len(todo_list)}")
    state["core_task"]["status"] = "running"
    state_manager.save(state)

    while True:
        try:
            verdict = loop_controller.check(state, _task_start_time)
            if verdict.action != "continue":
                logger.log_loop_abort(verdict.reason, verdict.stats)
                state["core_task"]["status"] = "failed"
                state_manager.save(state)
                ui_renderer.render_message("error", f"Task stopped: {verdict.reason}")

                try:
                    from . import browser_agent
                    browser_agent.close()
                except Exception:
                    pass
                return False
        except Exception:
            pass

        step = _select_next_step(state)
        if step is None:
            if _all_steps_completed(state):
                # STEP 7 & 8: VALIDATE OUTPUT FOR COMPLEX TASKS
                core_desc = state["core_task"]["description"].lower()
                if any(kw in core_desc for kw in ["website", "multiple pages"]):
                    # STEP 7: VALIDATION
                    task_output_dir = state["core_task"]["output_dir"]
                    
                    if os.path.exists("workspace"):
                        state["core_task"]["status"] = "failed"
                        state_manager.save(state)
                        ui_renderer.render_message("error", "Validation failed: workspace/ directory exists. Use output/ only.")
                        return False

                    output_files = os.listdir(task_output_dir) if os.path.exists(task_output_dir) else []
                    html_files = [f for f in output_files if f.endswith(".html")]
                    
                    if "output.html" in html_files:
                        state["core_task"]["status"] = "failed"
                        state_manager.save(state)
                        ui_renderer.render_message("error", "Validation failed: output.html created. Only index, article, contact allowed.")
                        return False

                    if len(html_files) < 3:
                        state["core_task"]["status"] = "failed"
                        state_manager.save(state)
                        ui_renderer.render_message("error", f"Validation failed: Complex website task produced only {len(html_files)} files. Multi-step execution failed.")
                        return False
                    
                    # STEP 7: VALIDATION - CROSS-FILE CONSISTENCY
                    try:
                        from .planner import HEADER, STYLE
                        for f in html_files:
                            content = Path(os.path.join(task_output_dir, f)).read_text(encoding="utf-8")
                            if HEADER.strip() not in content or STYLE.strip() not in content:
                                state["core_task"]["status"] = "failed"
                                state_manager.save(state)
                                ui_renderer.render_message("error", f"Consistency failure: {f} is missing shared header or styles.")
                                
                                # Final status update
                                from . import task_index
                                final_status = state["core_task"]["status"]
                                task_index.update_status(state["core_task"]["task_id"], final_status)
                                return False
                    except ImportError:
                        pass

                # FIX 5: REAL COMPLETION CHECK
                all_done = _all_steps_completed(state)
                any_failed_steps = any(s.get("status") == "failed" for s in state.get("completed_steps", []))
                llm_usage = state.get("llm_usage", {"calls": 0, "success": 0})
                
                if not all_done:
                    state["core_task"]["status"] = "failed"
                    ui_renderer.render_message("error", "Task incomplete: not all steps were executed.")
                elif any_failed_steps:
                    state["core_task"]["status"] = "partial"
                    ui_renderer.render_message("warning", "Task completed with some failed steps.")
                elif llm_usage["success"] == 0 and state["core_task"].get("task_profile", {}).get("complexity") != "low":
                    state["core_task"]["status"] = "partial"
                    ui_renderer.render_message("warning", "Task finished but no high-quality LLM generation used.")
                else:
                    state["core_task"]["status"] = "completed"
                
                state_manager.save(state)
                
                total_time = _time.time() - _task_start_time
                llm_usage = state.get("llm_usage", {"calls": 0, "success": 0})
                llm_calls = llm_usage.get("calls", 0)
                llm_success = llm_usage.get("success", 0)
                llm_failed = llm_calls - llm_success
                
                summary = f"""
[TASK SUMMARY]
Total steps: {total}
Time taken: {total_time:.1f}s

[LLM STATS]
  calls:   {llm_calls}
  success: {llm_success}
  failed:  {llm_failed}
"""
                print(summary)
                logger.log_visibility(task_id, summary)
                logger.log_task_complete(state["stats"])

                try:
                    formatted = ui_renderer.render_task_complete(state)
                    if formatted:
                        import dataclasses
                        state["core_task"]["formatted_output"] = dataclasses.asdict(formatted)
                        state_manager.save(state)
                        logger.log_format_complete(formatted.task_type, formatted.status, len(formatted.artifacts))
                except Exception:
                    ui_renderer.render_message("success", "Task completed.")

                try:
                    from . import browser_agent
                    browser_agent.close()
                except Exception:
                    pass

                from . import task_index
                task_index.update_status(state["core_task"]["task_id"], "completed")
                return True
            else:
                state["core_task"]["status"] = "failed"
                state_manager.save(state)
                logger.log_event("task_blocked", "No executable steps remain")

                try:
                    ui_renderer.render_task_failed(state)
                except Exception:
                    ui_renderer.render_message("error", "Task blocked. Run 'chotu issues'.")

                try:
                    from . import browser_agent
                    browser_agent.close()
                except Exception:
                    pass

                from . import task_index
                task_index.update_status(state["core_task"]["task_id"], "failed")
                return False
        step_num = _get_step_index(state, step["id"]) + 1
        total = state["stats"]["total_steps"]
        now_ts = datetime.now(timezone.utc).isoformat()
        state["current_step"] = {
            "id": step["id"], 
            "phase": "generating", 
            "started_at": now_ts,
            "target_file": step.get("target_file")
        }
        step["status"] = "generating"
        state_manager.save(state)
        logger.log_step_start(step["id"], step["description"])
        
        task_id = state["core_task"].get("task_id", "default")
        total = max(total, 1)
        pct = int((step_num / total) * 10)
        bar = "█" * pct + "░" * (10 - pct)
        msg = f"[STEP START] Step {step_num}/{total} {bar} {pct*10}% → {step['description']}"
        print(msg)
        logger.log_visibility(task_id, msg)

        retry_context = None
        if step.get("retries", 0) > 0 and step.get("result"):
            retry_context = {
                "reason": step["result"].get("reason", ""),
                "suggestion": step["result"].get("suggestion", ""),
            }

        plan_result = planner.plan(step, state, retry_context)
        action = plan_result.action
        
        # HARD BIND target_file from step
        if step.get("target_file"):
            action["target_file"] = step["target_file"]

        # DEBUG
        print(f"[CONTROLLER] Step target_file → {step.get('target_file')}")
            
        step["action"] = action

        step["plan_metadata"] = {
            "confidence": plan_result.confidence,
            "source": plan_result.source,
            "reason": plan_result.reason,
            "risk_notes": plan_result.risk_notes,
            "validation_passed": plan_result.validation_passed,
        }

        state_manager.save(state)
        if not isinstance(action, dict):
            # Preserve target_file if it was already bound
            prev_target = action.get("target_file") if isinstance(action, dict) else None
            action = {"type": "unknown", "command": str(action)}
            if prev_target: action["target_file"] = prev_target
            
        action_type = action.get("type", "unknown")
        action_desc = action.get("command") or action.get("path") or ""
        logger.log_step_action(step["id"], action_type, action)
        print(f"[ACTION] {action_type} → {action_desc}")
        logger.log_visibility(task_id, f"[ACTION] {action_type} → {action_desc}")
        ui_renderer.render_step_start(step_num, total, step["description"])
        ui_renderer.render_step_action(plan_result.source, plan_result.confidence, action_type, action_desc)
        state["current_step"]["phase"] = "executing"
        step["status"] = "executing"
        state_manager.save(state)
        exec_result = executor.execute(
            action,
            timeout=state["config"]["step_timeout_seconds"],
            working_dir=state["config"]["working_directory"],
            output_dir=state["core_task"]["output_dir"],
            state=state
        )
        logger.log_step_result(
            step["id"], exec_result.exit_code, exec_result.stdout,
            exec_result.stderr, exec_result.duration_ms
        )
        duration_s = exec_result.duration_ms / 1000.0
        msg = f"[STEP DONE] {step['description']} ({duration_s:.1f}s)"
        print(msg)
        logger.log_visibility(task_id, msg)
        state["current_step"]["phase"] = "evaluating"
        step["status"] = "evaluating"
        state_manager.save(state)
        expected_outcome = getattr(plan_result, "expected_outcome", step.get("expected_outcome"))
        val_result = validator.validate(exec_result, expected_outcome, step, state)
        logger.log_step_evaluate(step["id"], val_result.verdict, val_result.reason, val_result.suggestion)

        step["validation_metadata"] = {
            "verdict": val_result.verdict,
            "failure_type": val_result.failure_type,
            "confidence": val_result.confidence,
            "retryable": val_result.retryable,
            "checks": val_result.details.get("checks", []),
        }

        dec_result = decision_engine.decide(val_result, step, state)

        step["decision_metadata"] = {
            "decision": dec_result.decision,
            "strategy": dec_result.strategy,
            "confidence": dec_result.confidence,
            "action_hint": dec_result.action_hint,
            "escalation_level": dec_result.escalation_level,
            "meta_reasoning": dec_result.meta_reasoning,
            "search_used": dec_result.meta_reasoning.get("search_hit", False),
            "knowledge_used": dec_result.meta_reasoning.get("knowledge_hit", False),
        }

        if dec_result.decision == "mark_complete":
            step["status"] = "completed"
            step["result"] = {
                "verdict": val_result.verdict,
                "reason": val_result.reason,
                "exit_code": exec_result.exit_code,
                "duration_ms": exec_result.duration_ms,
                "completed_at": now_ts,
                "confidence": val_result.confidence,
            }
            state["completed_steps"].append(step["id"])

            action = step.get("action", {})
            action_type = action.get("type", "")
            if action_type == "file_write":
                file_path = action.get("path", "")
                if file_path:
                    try:
                        artifact_manager.register_artifact(
                            file_path=file_path,
                            artifact_type="file",
                            step_id=step["id"],
                            label=Path(file_path).name,
                        )
                        logger.log_artifact_register(file_path, "file", step["id"], Path(file_path).name)
                    except Exception:
                        pass
            elif action_type == "shell":
                cmd = action.get("command", "")
                if cmd and ".py" in cmd:
                    parts = cmd.split()
                    for part in parts:
                        if part.endswith(".py") and Path(part).exists():
                            try:
                                artifact_manager.register_artifact(
                                    file_path=part,
                                    artifact_type="file",
                                    step_id=step["id"],
                                    label=Path(part).name,
                                )
                                logger.log_artifact_register(part, "file", step["id"], Path(part).name)
                            except Exception:
                                pass

            state["current_step"] = None
            state["stats"] = state_manager.recompute_stats(state)
            state_manager.save(state)
            logger.log_step_complete(step["id"])

            try:
                learn_input = feedback_learning.LearningInput(
                    step=step,
                    val_result=val_result,
                    dec_result=dec_result,
                    outcome="mark_complete",
                    base_dir=Path.cwd(),
                )
                learn_output = feedback_learning.learn(learn_input)
                step["learning_metadata"] = {
                    "event_id": learn_output.learning_event_id,
                    "outcome": learn_output.outcome,
                    "recommendation": learn_output.recommendation,
                    "confidence": learn_output.confidence,
                }
            except Exception:
                pass

            ui_renderer.render_step_result("pass", exec_result.duration_ms, val_result.confidence)
            continue

        elif dec_result.decision in ("retry", "fix", "simplify"):
            issue_id = logger.log_issue(step["id"], val_result.failure_type, val_result.reason, exec_result.stderr)
            state["issues"].append({
                "id": issue_id,
                "step_id": step["id"],
                "type": val_result.failure_type,
                "description": val_result.reason,
                "occurred_at": now_ts,
                "resolved": False,
                "resolution_id": None
            })

            state["current_step"]["phase"] = "improving"
            step["status"] = dec_result.update_step.get("status", "generating")
            step["retries"] = dec_result.update_step.get("retries", step["retries"] + 1)
            step["result"] = {
                "verdict": val_result.verdict,
                "reason": val_result.reason,
                "suggestion": dec_result.action_hint or val_result.suggestion,
                "failure_type": val_result.failure_type,
                "decision": dec_result.decision,
                "strategy": dec_result.strategy,
            }
            state["stats"] = state_manager.recompute_stats(state)
            state_manager.save(state)
            logger.log_step_retry(step["id"], step["retries"])
            res_id = logger.log_resolution(step["id"], issue_id, dec_result.action_hint or val_result.suggestion)
            state["resolutions"].append({
                "id": res_id,
                "issue_id": issue_id,
                "action_taken": f"{dec_result.decision.title()} {step['retries']}: {dec_result.action_hint or val_result.suggestion}",
                "resolved_at": now_ts
            })
            state["issues"][-1]["resolved"] = True
            state["issues"][-1]["resolution_id"] = res_id
            state_manager.save(state)
            logger.log_decision(step["id"], f"{dec_result.decision}: {dec_result.strategy}", dec_result.reason)

            ui_renderer.render_step_result(
                val_result.verdict, exec_result.duration_ms,
                val_result.confidence, val_result.reason
            )
            ui_renderer.render_step_retry(
                step["retries"], step["max_retries"],
                dec_result.strategy, dec_result.decision,
                dec_result.action_hint
            )
            if dec_result.action_hint:
                logger.log_decision_engine_hint(step["id"], dec_result.action_hint)

            try:
                learn_input = feedback_learning.LearningInput(
                    step=step,
                    val_result=val_result,
                    dec_result=dec_result,
                    outcome=dec_result.decision,
                    base_dir=Path.cwd(),
                )
                feedback_learning.learn(learn_input)
            except Exception:
                pass
            continue

        elif dec_result.decision == "skip":
            step["status"] = "skipped"
            state["current_step"] = None
            state["stats"] = state_manager.recompute_stats(state)
            state_manager.save(state)
            logger.log_step_failed(step["id"], f"Skipped: {dec_result.reason}")
            print(f"[ERROR] Step skipped → {dec_result.reason}")
            logger.log_visibility(task_id, f"[ERROR] Step skipped → {dec_result.reason}")

            try:
                learn_input = feedback_learning.LearningInput(
                    step=step,
                    val_result=val_result,
                    dec_result=dec_result,
                    outcome="skip",
                    base_dir=Path.cwd(),
                )
                feedback_learning.learn(learn_input)
            except Exception:
                pass

            ui_renderer.render_step_result("skip", reason=dec_result.reason)
            continue

        else:
            issue_id = logger.log_issue(step["id"], val_result.failure_type, val_result.reason, exec_result.stderr)
            state["issues"].append({
                "id": issue_id,
                "step_id": step["id"],
                "type": val_result.failure_type,
                "description": val_result.reason,
                "occurred_at": now_ts,
                "resolved": False,
                "resolution_id": None
            })

            step["status"] = "failed"
            step["result"] = {
                "verdict": val_result.verdict,
                "reason": val_result.reason,
                "exit_code": exec_result.exit_code,
                "completed_at": now_ts,
                "failure_type": val_result.failure_type,
                "confidence": val_result.confidence,
                "decision": dec_result.decision,
                "strategy": dec_result.strategy,
            }
            state["current_step"] = None
            state["stats"] = state_manager.recompute_stats(state)
            state_manager.save(state)
            logger.log_step_failed(step["id"], dec_result.reason)
            print(f"[ERROR] Step failed → {dec_result.reason}")
            logger.log_visibility(task_id, f"[ERROR] Step failed → {dec_result.reason}")
            print(f"[RECOVERY] fallback applied: {dec_result.decision}")
            logger.log_visibility(task_id, f"[RECOVERY] fallback applied: {dec_result.decision}")
            logger.log_decision(step["id"], f"{dec_result.decision}: {dec_result.strategy}", dec_result.reason)

            try:
                learn_input = feedback_learning.LearningInput(
                    step=step,
                    val_result=val_result,
                    dec_result=dec_result,
                    outcome=dec_result.decision,
                    base_dir=Path.cwd(),
                )
                learn_output = feedback_learning.learn(learn_input)
                step["learning_metadata"] = {
                    "event_id": learn_output.learning_event_id,
                    "outcome": learn_output.outcome,
                    "recommendation": learn_output.recommendation,
                    "confidence": learn_output.confidence,
                }
            except Exception:
                pass

            verdict = "escalate" if dec_result.escalation_level >= 2 else "fail"
            ui_renderer.render_step_result(verdict, reason=dec_result.reason)
            continue


def _recover_state(state: dict) -> dict:
    """Roll back crashed steps to safe restart point."""
    if state["current_step"] is not None:
        step = _find_step(state, state["current_step"]["id"])
        if step is not None:
            old_phase = state["current_step"].get("phase", "")
            if old_phase in ["generating", "executing", "evaluating", "validating", "deciding", "improving"]:
                step["status"] = "generating"
                state["current_step"]["phase"] = "generating"
                state["current_step"]["started_at"] = datetime.now(timezone.utc).isoformat()
                logger.log_recovery(step["id"], f"Rolled back from '{old_phase}' to 'generating'")
                state_manager.save(state)
    return state


def _select_next_step(state: dict) -> Optional[dict]:
    """Select the next executable step. Uses task graph if available."""
    todo_list = state.get("todo_list", [])

    for step in todo_list:
        if step.get("status") == "generating":
            return step

    try:
        graph = task_graph.build(todo_list)
        if graph.is_valid:
            completed = state.get("completed_steps", [])
            failed_skipped = [s["id"] for s in todo_list if s.get("status") in ("failed", "skipped")]
            ready = task_graph.get_ready_steps(graph, completed, failed_skipped)
            if ready:
                step_id = ready[0]
                step = _find_step(state, step_id)
                if step and step.get("status") == "pending":
                    return step
    except Exception:
        pass

    for step in todo_list:
        if step.get("status") == "pending":
            deps = step.get("depends_on", [])
            if all(dep in state.get("completed_steps", []) for dep in deps):
                return step
    return None


def _all_steps_terminal(state: dict) -> bool:
    """Check if all steps are completed/failed/skipped."""
    for step in state.get("todo_list", []):
        if step["status"] not in ["completed", "failed", "skipped"]:
            return False
    return True


def _all_steps_completed(state: dict) -> bool:
    """Check if ALL steps are completed (not failed)."""
    if not state.get("todo_list"):
        return False
    for step in state.get("todo_list", []):
        if step["status"] != "completed":
            return False
    return True


def _get_step_index(state: dict, step_id: str) -> int:
    """Get index of step by ID."""
    for i, step in enumerate(state.get("todo_list", [])):
        if step["id"] == step_id:
            return i
    return -1


def _find_step(state: dict, step_id: str) -> Optional[dict]:
    """Find step by ID."""
    for step in state.get("todo_list", []):
        if step["id"] == step_id:
            return step
    return None


def _display_status() -> bool:
    """Print status."""
    base_dir = Path.cwd()
    state = state_manager.load(base_dir)
    if state is None:
        ui_renderer.render_message("error", "No task found. Use 'chotu new <task>' first.")
        return False
    ui_renderer.render_status_dashboard(state)
    return True


def _display_plan() -> bool:
    """Print todo list."""
    base_dir = Path.cwd()
    state = state_manager.load(base_dir)
    if state is None:
        ui_renderer.render_message("error", "No task found. Use 'chotu new <task>' first.")
        return False
    ui_renderer.render_plan(state.get("todo_list", []))
    return True


def _display_log(step_id: Optional[str] = None) -> bool:
    """Print logs."""
    base_dir = Path.cwd()
    state = state_manager.load(base_dir)
    if state is None:
        print("No task found.")
        return False
    runtime_dir = state_manager.get_runtime_dir(base_dir)
    if step_id:
        log_file = runtime_dir / "logs" / f"{step_id}.log"
        if log_file.exists():
            print(log_file.read_text(encoding="utf-8"))
        else:
            print(f"No log found for {step_id}")
    else:
        events_file = runtime_dir / "events.jsonl"
        if events_file.exists():
            print(events_file.read_text(encoding="utf-8"))
    return True


def _display_issues() -> bool:
    """Print issues."""
    base_dir = Path.cwd()
    state = state_manager.load(base_dir)
    if state is None:
        ui_renderer.render_message("error", "No task found.")
        return False
    ui_renderer.render_issues(state.get("issues", []))
    return True


def _skip_step() -> bool:
    """Skip current step."""
    base_dir = Path.cwd()
    state = state_manager.load(base_dir)
    if state is None:
        ui_renderer.render_message("error", "No task found.")
        return False
    step = _select_next_step(state)
    if step:
        step["status"] = "skipped"
        state["stats"] = state_manager.recompute_stats(state)
        state_manager.save(state)
        ui_renderer.render_message("info", f"Skipped {step['id']}")
        return True
    ui_renderer.render_message("warning", "No step to skip.")
    return False


def _reset_step() -> bool:
    """Reset current step."""
    base_dir = Path.cwd()
    state = state_manager.load(base_dir)
    if state is None:
        ui_renderer.render_message("error", "No task found.")
        return False
    step = _select_next_step(state)
    if step:
        step["status"] = "pending"
        step["retries"] = 0
        state["stats"] = state_manager.recompute_stats(state)
        state_manager.save(state)
        ui_renderer.render_message("info", f"Reset {step['id']}")
        return True
    ui_renderer.render_message("warning", "No step to reset.")
    return False


def _abort_task() -> bool:
    """Abort entire task."""
    base_dir = Path.cwd()
    state = state_manager.load(base_dir)
    if state is None:
        ui_renderer.render_message("error", "No task found.")
        return False
    state["core_task"]["status"] = "failed"
    state_manager.save(state)
    ui_renderer.render_message("warning", "Task aborted.")
    return True


def _display_tasks() -> bool:
    """Display list of all tasks."""
    from . import task_index, ui_renderer
    tasks = task_index.list_tasks()
    if not tasks:
        print("No tasks found.")
        return True
    
    print("\n[TASK LIST]")
    for i, t in enumerate(tasks, 1):
        status_color = "[completed]" if t["status"] == "completed" else f"({t['status']})"
        print(f"[{i}] {t['task_name'][:30]}... {status_color}")
    print()
    return True


def _open_task(task_index_num: Optional[str]) -> bool:
    """Show or open a task directory."""
    if not task_index_num:
        print("Error: Task index required. Use 'chotu open <index>'")
        return False
    
    try:
        idx = int(task_index_num)
    except ValueError:
        print("Error: Index must be a number.")
        return False
    
    from . import task_index
    task = task_index.get_task_by_index(idx)
    if not task:
        print(f"Error: No task found at index {idx}")
        return False
    
    path = task["output_dir"]
    print(f"\n[TASK VIEW] Task: {task['task_name']}")
    print(f"Path: {os.path.abspath(path)}")
    print(f"Status: {task['status']}")
    
    # Optional: Open in explorer on windows
    if os.name == "nt" and os.path.exists(path):
        os.system(f"start {path}")
    
    return True