"""Orchestrate the entire Phase 1 loop. THE brain. The ONLY state writer."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import state_manager, logger, executor, evaluator, task_decomposer, planner, validator, decision_engine, smart_memory, filtered_search, feedback_learning, knowledge_store, task_classifier, output_formatter, artifact_manager, ui_renderer, loop_controller, task_graph


def handle_command(command: str, args: dict) -> bool:
    """Main dispatch for all CLI commands."""
    if command == "new":
        return _run_new(args.get("task", ""), args.get("working_dir", None), args.get("auto_run", False))
    elif command == "run":
        return _run_loop()
    elif command == "status":
        return _display_status()
    elif command == "plan":
        return _display_plan()
    elif command == "log":
        return _display_log(args.get("step_id"))
    elif command == "issues":
        return _display_issues()
    elif command == "skip":
        return _skip_step()
    elif command == "reset":
        return _reset_step()
    elif command == "abort":
        return _abort_task()
    else:
        print(f"Unknown command: {command}")
        return False


def _run_new(task: str, working_dir: Optional[str] = None, auto_run: bool = False) -> bool:
    """Create state, decompose, optionally auto-run."""
    state = None
    if not task:
        print("Error: Task description required")
        return False
    base_dir = Path.cwd()
    runtime_dir = state_manager.ensure_runtime_dirs(base_dir)
    logger.init(runtime_dir)
    state = state_manager.create_fresh_state(task, working_dir)
    artifact_manager.init(runtime_dir, state.get("core_task", {}).get("task_id", "default"))
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
    todo_list = task_decomposer.decompose(task)
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
    logger.log_task_start(state["core_task"]["description"], state["stats"]["total_steps"])

    import time as _time
    _task_start_time = _time.time()

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
                state["core_task"]["status"] = "completed"
                state_manager.save(state)
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

                return False
        step_num = _get_step_index(state, step["id"]) + 1
        total = state["stats"]["total_steps"]
        now_ts = datetime.now(timezone.utc).isoformat()
        state["current_step"] = {"id": step["id"], "phase": "generating", "started_at": now_ts}
        step["status"] = "generating"
        state_manager.save(state)
        logger.log_step_start(step["id"], step["description"])

        retry_context = None
        if step.get("retries", 0) > 0 and step.get("result"):
            retry_context = {
                "reason": step["result"].get("reason", ""),
                "suggestion": step["result"].get("suggestion", ""),
            }

        plan_result = planner.plan(step, state, retry_context)
        action = plan_result.action
        step["action"] = action

        step["plan_metadata"] = {
            "confidence": plan_result.confidence,
            "source": plan_result.source,
            "reason": plan_result.reason,
            "risk_notes": plan_result.risk_notes,
            "validation_passed": plan_result.validation_passed,
        }

        state_manager.save(state)
        action_type = action.get("type", "unknown")
        action_desc = action.get("command") or action.get("path") or ""
        logger.log_step_action(step["id"], action_type, action)
        ui_renderer.render_step_start(step_num, total, step["description"])
        ui_renderer.render_step_action(plan_result.source, plan_result.confidence, action_type, action_desc)
        state["current_step"]["phase"] = "executing"
        step["status"] = "executing"
        state_manager.save(state)
        exec_result = executor.execute(
            action,
            timeout=state["config"]["step_timeout_seconds"],
            working_dir=state["config"]["working_directory"]
        )
        logger.log_step_result(
            step["id"], exec_result.exit_code, exec_result.stdout,
            exec_result.stderr, exec_result.duration_ms
        )
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