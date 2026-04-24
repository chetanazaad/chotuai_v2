"""Append-only structured logging for every event. Never overwrites."""
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_runtime_dir: Optional[Path] = None


def init(runtime_dir: Path) -> None:
    """Set the runtime directory for all log files."""
    global _runtime_dir
    _runtime_dir = runtime_dir


def _get_runtime_dir() -> Path:
    if _runtime_dir is None:
        raise RuntimeError("Logger not initialized. Call init() first.")
    return _runtime_dir


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _count_lines(file_path: Path) -> int:
    if not file_path.exists():
        return 0
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except (OSError, IOError):
        return 0


def _open_append(file_path: Path):
    """Open file in append mode, creating parent dirs if needed."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    return open(file_path, "a", encoding="utf-8")


def log_event(event_type: str, message: str, payload: dict = None, task_id: str = None, step_id: str = None) -> None:
    """Append to events.jsonl."""
    record = {
        "timestamp": _timestamp(),
        "event_type": event_type,
        "task_id": task_id or "",
        "step_id": step_id or "",
        "message": message,
        "payload": payload or {}
    }
    events_file = _get_runtime_dir() / "events.jsonl"
    with _open_append(events_file):
        with open(events_file, "a", encoding="utf-8") as f:
            import json
            f.write(json.dumps(record) + "\n")


def log_issue(step_id: str, error_type: str, description: str, stderr: str = "") -> str:
    """Append to issues.jsonl, return issue_id."""
    issues_file = _get_runtime_dir() / "issues.jsonl"
    issue_num = _count_lines(issues_file) + 1
    issue_id = f"issue_{issue_num:03d}"
    record = {
        "timestamp": _timestamp(),
        "issue_id": issue_id,
        "step_id": step_id,
        "error_type": error_type,
        "description": description,
        "stderr": stderr
    }
    with open(issues_file, "a", encoding="utf-8") as f:
        import json
        f.write(json.dumps(record) + "\n")
    return issue_id


def log_decision(step_id: str, description: str, rationale: str = "") -> str:
    """Append to decisions.jsonl, return decision_id."""
    decisions_file = _get_runtime_dir() / "decisions.jsonl"
    decision_num = _count_lines(decisions_file) + 1
    decision_id = f"dec_{decision_num:03d}"
    record = {
        "timestamp": _timestamp(),
        "decision_id": decision_id,
        "step_id": step_id,
        "description": description,
        "rationale": rationale
    }
    with open(decisions_file, "a", encoding="utf-8") as f:
        import json
        f.write(json.dumps(record) + "\n")
    return decision_id


def log_resolution(step_id: str, issue_id: str, action_taken: str) -> str:
    """Append to resolutions.jsonl, return resolution_id."""
    resolutions_file = _get_runtime_dir() / "resolutions.jsonl"
    res_num = _count_lines(resolutions_file) + 1
    resolution_id = f"res_{res_num:03d}"
    record = {
        "timestamp": _timestamp(),
        "resolution_id": resolution_id,
        "step_id": step_id,
        "issue_id": issue_id,
        "action_taken": action_taken
    }
    with open(resolutions_file, "a", encoding="utf-8") as f:
        import json
        f.write(json.dumps(record) + "\n")
    return resolution_id


def log_step(step_id: str, message: str) -> None:
    """Append to logs/step_NNN.log (human-readable)."""
    step_log = _get_runtime_dir() / "logs" / f"{step_id}.log"
    timestamp = _timestamp()
    with _open_append(step_log):
        with open(step_log, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {step_id} {message}\n")


def log_task_start(task: str, total_steps: int = 0, task_id: str = None) -> None:
    """Log task start."""
    log_event("task_start", f"Task started: {task}", {"total_steps": total_steps}, task_id=task_id)


def log_task_complete(stats: dict, task_id: str = None) -> None:
    """Log task complete."""
    log_event("task_complete", "Task completed", stats, task_id=task_id)


def log_task_decompose_start(task: str, task_id: str = None) -> None:
    """Log task decomposition start."""
    log_event("decompose_start", f"Decomposing task: {task}", {}, task_id=task_id)


def log_task_decompose_complete(count: int, task_id: str = None) -> None:
    """Log task decomposition complete."""
    log_event("decompose_complete", f"Decomposed into {count} steps", {"step_count": count}, task_id=task_id)


def log_step_start(step_id: str, description: str) -> None:
    """Log step start."""
    log_event("step_start", f"Starting step: {description}", {}, step_id=step_id)
    log_step(step_id, f"STEP_START {step_id} \"{description}\"")


def log_step_action(step_id: str, action_type: str, action: dict) -> None:
    """Log step action."""
    log_event("step_action", f"Executing {action_type}", action, step_id=step_id)
    command = action.get("command", action.get("content", ""))
    log_step(step_id, f"ACTION {action_type}: {command}")


def log_step_result(step_id: str, exit_code: int, stdout: str, stderr: str, duration_ms: int) -> None:
    """Log step result."""
    log_event("step_result", f"Exit code: {exit_code}",
             {"exit_code": exit_code, "duration_ms": duration_ms, "stdout": stdout[:500], "stderr": stderr[:500]},
             step_id=step_id)
    log_step(step_id, f"EXIT_CODE: {exit_code}")
    log_step(step_id, f"DURATION: {duration_ms}ms")


def log_step_evaluate(step_id: str, verdict: str, reason: str, suggestion: str = "") -> None:
    """Log step evaluation."""
    log_event("step_evaluate", f"Evaluation: {verdict}", {"reason": reason, "suggestion": suggestion}, step_id=step_id)
    log_step(step_id, f"EVAL: {verdict} — \"{reason}\"")


def log_step_complete(step_id: str) -> None:
    """Log step complete."""
    log_event("step_complete", "Step completed", {}, step_id=step_id)
    log_step(step_id, f"STEP_COMPLETE {step_id}")


def log_step_failed(step_id: str, reason: str) -> None:
    """Log step failed."""
    log_event("step_failed", reason, {}, step_id=step_id)
    log_step(step_id, f"STEP_FAILED {step_id}: {reason}")


def log_step_retry(step_id: str, retry_count: int) -> None:
    """Log step retry."""
    log_event("step_retry", f"Retrying step (attempt {retry_count})", {}, step_id=step_id)
    log_step(step_id, f"RETRY {step_id} attempt {retry_count}")


def log_recovery(step_id: str, message: str) -> None:
    """Log recovery."""
    log_event("recovery", message, {}, step_id=step_id)
    log_step(step_id, f"RECOVERY: {message}")


def log_plan_start(step_id: str, source: str) -> None:
    """Log planner start."""
    log_event("plan_start", f"Planning action for step (source: {source})", {}, step_id=step_id)
    log_step(step_id, f"PLAN_START source={source}")


def log_plan_complete(step_id: str, action_type: str, confidence: float, source: str) -> None:
    """Log planner success."""
    log_event("plan_complete", f"Plan generated: {action_type} (confidence={confidence:.2f}, source={source})",
              {"action_type": action_type, "confidence": confidence, "source": source},
              step_id=step_id)
    log_step(step_id, f"PLAN_COMPLETE type={action_type} confidence={confidence:.2f} source={source}")


def log_plan_validation_failed(step_id: str, errors: list) -> None:
    """Log planner validation failure."""
    log_event("plan_validation_failed", f"Plan validation failed: {errors}",
              {"errors": errors}, step_id=step_id)
    log_step(step_id, f"PLAN_VALIDATION_FAILED errors={errors}")


def log_plan_fallback(step_id: str, reason: str) -> None:
    """Log planner fallback used."""
    log_event("plan_fallback", f"Using fallback planning: {reason}",
              {"reason": reason}, step_id=step_id)
    log_step(step_id, f"PLAN_FALLBACK reason={reason}")


def log_plan_llm_failed(step_id: str, error: str) -> None:
    """Log LLM planning failure."""
    log_event("plan_llm_failed", f"LLM planning failed: {error}",
              {"error": error}, step_id=step_id)
    log_step(step_id, f"PLAN_LLM_FAILED error={error}")


def log_validation_start(step_id: str) -> None:
    """Log validation start."""
    log_event("validation_start", "Starting multi-layer validation", {}, step_id=step_id)
    log_step(step_id, "VALIDATION_START")


def log_validation_complete(step_id: str, verdict: str, failure_type: str, confidence: float) -> None:
    """Log validation result."""
    log_event("validation_complete",
              f"Validation: {verdict} (type={failure_type}, confidence={confidence:.2f})",
              {"verdict": verdict, "failure_type": failure_type, "confidence": confidence},
              step_id=step_id)
    log_step(step_id, f"VALIDATION: {verdict} type={failure_type} confidence={confidence:.2f}")


def log_validation_checks(step_id: str, checks: list) -> None:
    """Log individual validation checks."""
    for check in checks:
        status = "PASS" if check.get("passed") else "FAIL"
        log_step(step_id, f"  CHECK [{status}] {check.get('check', '')}: {check.get('detail', '')}")


def log_validation_partial(step_id: str, passed_count: int, total_count: int) -> None:
    """Log partial success detection."""
    log_event("validation_partial",
              f"Partial success: {passed_count}/{total_count} checks passed",
              {"passed": passed_count, "total": total_count},
              step_id=step_id)
    log_step(step_id, f"VALIDATION_PARTIAL {passed_count}/{total_count} checks passed")


def log_decision_engine(step_id: str, decision: str, strategy: str, confidence: float) -> None:
    """Log decision engine result."""
    log_event("decision_engine",
              f"Decision: {decision} (strategy={strategy}, confidence={confidence:.2f})",
              {"decision": decision, "strategy": strategy, "confidence": confidence},
              step_id=step_id)
    log_step(step_id, f"DECISION: {decision} strategy={strategy} confidence={confidence:.2f}")


def log_decision_engine_meta(step_id: str, meta_reasoning: dict) -> None:
    """Log decision engine meta-reasoning."""
    log_event("decision_meta",
              f"Meta: failure={meta_reasoning.get('failure_analysis', '')} "
              f"pattern={meta_reasoning.get('pattern', '')} "
              f"severity={meta_reasoning.get('severity', '')}",
              meta_reasoning,
              step_id=step_id)
    log_step(step_id, f"DECISION_META failure={meta_reasoning.get('failure_analysis', '')} "
                       f"pattern={meta_reasoning.get('pattern', '')} "
                       f"severity={meta_reasoning.get('severity', '')}")


def log_decision_engine_hint(step_id: str, action_hint: str) -> None:
    """Log the action hint sent to planner."""
    if action_hint:
        log_event("decision_hint", f"Action hint: {action_hint[:200]}",
                  {"action_hint": action_hint}, step_id=step_id)
        log_step(step_id, f"DECISION_HINT: {action_hint[:200]}")


def log_gateway_start(purpose: str, provider: str) -> None:
    """Log gateway request start."""
    log_event("gateway_start", f"Gateway request: purpose={purpose} provider={provider}",
              {"purpose": purpose, "provider": provider})


def log_gateway_success(purpose: str, provider: str, confidence: float, latency_ms: int) -> None:
    """Log gateway request success."""
    log_event("gateway_success",
              f"Gateway success: {provider} (confidence={confidence:.2f}, latency={latency_ms}ms)",
              {"purpose": purpose, "provider": provider, "confidence": confidence, "latency_ms": latency_ms})


def log_gateway_fallback(purpose: str, from_provider: str, to_provider: str, reason: str) -> None:
    """Log gateway provider fallback."""
    log_event("gateway_fallback",
              f"Gateway fallback: {from_provider} → {to_provider} ({reason})",
              {"purpose": purpose, "from": from_provider, "to": to_provider, "reason": reason})


def log_gateway_failure(purpose: str, provider: str, error: str) -> None:
    """Log gateway request failure."""
    log_event("gateway_failure",
              f"Gateway failed: {provider} — {error}",
              {"purpose": purpose, "provider": provider, "error": error})


def log_memory_hit(signature: str, match_type: str, confidence: float) -> None:
    """Log memory lookup hit."""
    log_event("memory_hit",
              f"Memory hit: {signature} (type={match_type}, confidence={confidence:.2f})",
              {"signature": signature, "match_type": match_type, "confidence": confidence})


def log_memory_miss(signature: str) -> None:
    """Log memory lookup miss."""
    log_event("memory_miss", f"Memory miss: {signature}", {"signature": signature})


def log_memory_update(signature: str, strategy: str, outcome: str) -> None:
    """Log memory strategy update."""
    log_event("memory_update",
              f"Memory update: {signature} strategy={strategy} outcome={outcome}",
              {"signature": signature, "strategy": strategy, "outcome": outcome})


def log_memory_load(entries_count: int) -> None:
    """Log memory store load."""
    log_event("memory_load", f"Memory loaded: {entries_count} entries", {"entries_count": entries_count})


def log_memory_save(entries_count: int) -> None:
    """Log memory store save."""
    log_event("memory_save", f"Memory saved: {entries_count} entries", {"entries_count": entries_count})


def log_search_start(query: str) -> None:
    """Log search request."""
    log_event("search_start", f"Search: {query[:100]}",
              {"query": query[:200]})


def log_search_success(query: str, results_count: int, confidence: float) -> None:
    """Log search success."""
    log_event("search_success",
              f"Search success: {results_count} results (confidence={confidence:.2f})",
              {"query": query[:200], "results": results_count, "confidence": confidence})


def log_search_filter(query: str, before: int, after: int) -> None:
    """Log search filtering."""
    log_event("search_filter",
              f"Search filter: {before} → {after} results",
              {"query": query[:200], "before": before, "after": after})


def log_search_failure(query: str, reason: str) -> None:
    """Log search failure."""
    log_event("search_failure",
              f"Search failed: {reason}",
              {"query": query[:200], "reason": reason})


def log_learning_start(step_id: str) -> None:
    """Log learning engine activation."""
    log_event("learning_start", f"Learning: analyzing step {step_id}",
              {"step_id": step_id}, step_id=step_id)


def log_learning_success(step_id: str, signature: str, strategy: str, recommendation: str) -> None:
    """Log successful learning outcome."""
    log_event("learning_success",
              f"Learning: {signature} strategy={strategy} -> {recommendation}",
              {"signature": signature, "strategy": strategy, "recommendation": recommendation},
              step_id=step_id)


def log_learning_failure(step_id: str, signature: str, strategy: str, recommendation: str) -> None:
    """Log failed learning outcome."""
    log_event("learning_failure",
              f"Learning: {signature} strategy={strategy} failed -> {recommendation}",
              {"signature": signature, "strategy": strategy, "recommendation": recommendation},
              step_id=step_id)


def log_learning_partial(step_id: str, signature: str, strategy: str) -> None:
    """Log partial success learning."""
    log_event("learning_partial",
              f"Learning: {signature} strategy={strategy} partial success",
              {"signature": signature, "strategy": strategy},
              step_id=step_id)


def log_learning_recommendation(step_id: str, strategy: str, recommendation: str, success_rate: float) -> None:
    """Log strategy promotion/demotion recommendation."""
    log_event("learning_recommendation",
              f"Learning recommendation: {strategy} -> {recommendation} (sr={success_rate:.0%})",
              {"strategy": strategy, "recommendation": recommendation, "success_rate": success_rate},
              step_id=step_id)


def log_knowledge_hit(query: str, match_type: str) -> None:
    """Log knowledge store query hit."""
    log_event("knowledge_hit",
              f"Knowledge hit: {query[:100]} (type={match_type})",
              {"query": query[:200], "match_type": match_type})


def log_knowledge_miss(query: str) -> None:
    """Log knowledge store query miss."""
    log_event("knowledge_miss",
              f"Knowledge miss: {query[:100]}",
              {"query": query[:200]})


def log_knowledge_ingest(signature: str, kind: str, status: str) -> None:
    """Log knowledge ingestion."""
    log_event("knowledge_ingest",
              f"Knowledge ingest: {signature} kind={kind} status={status}",
              {"signature": signature, "kind": kind, "status": status})


def log_knowledge_promote(entry_id: str, signature: str) -> None:
    """Log knowledge entry promotion."""
    log_event("knowledge_promote",
              f"Knowledge promoted: {entry_id} ({signature})",
              {"entry_id": entry_id, "signature": signature})


def log_knowledge_demote(entry_id: str, signature: str) -> None:
    """Log knowledge entry demotion."""
    log_event("knowledge_demote",
              f"Knowledge demoted: {entry_id} ({signature})",
              {"entry_id": entry_id, "signature": signature})


def log_knowledge_save(entries_count: int) -> None:
    """Log knowledge store save."""
    log_event("knowledge_save",
              f"Knowledge saved: {entries_count} entries",
              {"entries_count": entries_count})


def log_classify_start(user_input: str) -> None:
    """Log classification start."""
    log_event("classify_start",
              f"Classifying: {user_input[:100]}",
              {"input": user_input[:200]})


def log_classify_result(task_type: str, domain: str, complexity: str, confidence: float) -> None:
    """Log classification result."""
    log_event("classify_result",
              f"Classified: type={task_type} domain={domain} complexity={complexity} confidence={confidence:.2f}",
              {"task_type": task_type, "domain": domain, "complexity": complexity, "confidence": confidence})


def log_classify_uncertain(task_type: str, uncertainty: str) -> None:
    """Log classification uncertainty."""
    log_event("classify_uncertain",
              f"Classification uncertain: type={task_type} — {uncertainty}",
              {"task_type": task_type, "uncertainty": uncertainty})


def log_format_start(task_type: str) -> None:
    """Log output formatting start."""
    log_event("format_start",
              f"Formatting output: type={task_type}",
              {"task_type": task_type})


def log_format_complete(task_type: str, status: str, artifact_count: int) -> None:
    """Log output formatting success."""
    log_event("format_complete",
              f"Formatted: type={task_type} status={status} artifacts={artifact_count}",
              {"task_type": task_type, "status": status, "artifacts": artifact_count})


def log_format_error(error: str) -> None:
    """Log output formatting error."""
    log_event("format_error",
              f"Format error: {error}",
              {"error": error})


def log_ui_render(component: str, details: str = "") -> None:
    """Log UI rendering event."""
    log_event("ui_render",
              f"UI render: {component}",
              {"component": component, "details": details})


def log_ui_error(component: str, error: str) -> None:
    """Log UI rendering error."""
    log_event("ui_error",
              f"UI error in {component}: {error}",
              {"component": component, "error": error})


def log_ui_interaction(action: str) -> None:
    """Log UI user interaction."""
    log_event("ui_interaction",
              f"UI interaction: {action}",
              {"action": action})


def log_artifact_register(file_path: str, artifact_type: str, step_id: str = "", label: str = "") -> None:
    """Log artifact registration."""
    log_event("artifact_register",
              f"Artifact registered: {label or file_path}",
              {"file_path": file_path, "artifact_type": artifact_type, "step_id": step_id, "label": label},
              step_id=step_id)


def log_artifact_unregister(file_path: str, step_id: str = "") -> None:
    """Log artifact removal."""
    log_event("artifact_unregister",
              f"Artifact unregistered: {file_path}",
              {"file_path": file_path},
              step_id=step_id)


def log_artifact_list(artifacts: list) -> None:
    """Log artifact list."""
    for a in artifacts:
        step_id = a.get("step_id", "")
        log_event("artifact_list",
                  f"Artifact: {a.get('label', '')} ({a.get('artifact_type', '')})",
                  a,
                  step_id=step_id if step_id else None)


def log_artifact_stats(total: int, total_size: int, by_type: dict) -> None:
    """Log artifact statistics."""
    log_event("artifact_stats",
              f"Artifacts: {total} ({total_size} bytes) by_type={by_type}",
              {"total": total, "total_size_bytes": total_size, "by_type": by_type})


def log_confidence_aggregate(step_id: str, overall: float, recommendation: str) -> None:
    """Log confidence aggregation."""
    log_event("confidence_aggregate",
              f"Confidence: {overall:.2f} ({recommendation}) step={step_id}",
              {"step_id": step_id, "overall": overall, "recommendation": recommendation},
              step_id=step_id)


def log_confidence_signals(step_id: str, signals: dict) -> None:
    """Log confidence signals."""
    log_event("confidence_signals",
              f"Signals: {signals} step={step_id}",
              {"step_id": step_id, "signals": signals},
              step_id=step_id)


def log_loop_check(action: str, reason: str, elapsed: float) -> None:
    """Log loop controller check."""
    log_event("loop_check",
              f"Loop: {action} — {reason} ({int(elapsed)}s)",
              {"action": action, "reason": reason, "elapsed": elapsed})


def log_loop_abort(reason: str, stats: dict) -> None:
    """Log loop abort."""
    log_event("loop_abort",
              f"Loop ABORT: {reason}",
              {"reason": reason, "stats": stats})


def log_loop_stuck(step_id: str) -> None:
    """Log stuck detection."""
    log_event("loop_stuck",
              f"Step stuck: {step_id}",
              {"step_id": step_id})


def log_model_route(purpose: str, provider: str, reason: str) -> None:
    """Log model routing."""
    log_event("model_route",
              f"Model: {provider} for {purpose} — {reason}",
              {"purpose": purpose, "provider": provider, "reason": reason})


def log_model_escalate(from_provider: str, to_provider: str, reason: str) -> None:
    """Log model escalation."""
    log_event("model_escalate",
              f"Model escalation: {from_provider} → {to_provider} — {reason}",
              {"from": from_provider, "to": to_provider, "reason": reason})


def log_graph_build(node_count: int, edge_count: int, valid: bool) -> None:
    """Log task graph build."""
    log_event("graph_build",
              f"Graph: {node_count} nodes, {edge_count} edges, valid={valid}",
              {"nodes": node_count, "edges": edge_count, "valid": valid})


def log_graph_ready(ready_steps: list) -> None:
    """Log ready steps."""
    log_event("graph_ready",
              f"Ready steps: {ready_steps}",
              {"ready": ready_steps})


def log_graph_blocked(step_id: str, blocking: list) -> None:
    """Log blocked steps."""
    log_event("graph_blocked",
              f"Step {step_id} blocked by: {blocking}",
              {"step_id": step_id, "blocking": blocking})


def log_graph_order(order: list) -> None:
    """Log execution order."""
    log_event("graph_order",
              f"Execution order: {order}",
              {"order": order})


def log_graph_cycle(cycle_path: list) -> None:
    """Log cycle detection."""
    log_event("graph_cycle",
              f"CYCLE detected: {cycle_path}",
              {"cycle": cycle_path})


def log_browser_navigate(url: str, duration_ms: int) -> None:
    """Log browser navigation."""
    log_event("browser_navigate",
              f"Browser: navigated to {url[:100]} ({duration_ms}ms)",
              {"url": url[:200], "duration_ms": duration_ms})


def log_browser_extract(action: str, data_size: int) -> None:
    """Log browser extraction."""
    log_event("browser_extract",
              f"Browser: {action} ({data_size} chars extracted)",
              {"action": action, "size": data_size})


def log_browser_error(action: str, error: str) -> None:
    """Log browser error."""
    log_event("browser_error",
              f"Browser error in {action}: {error[:200]}",
              {"action": action, "error": error[:500]})


def log_browser_close() -> None:
    """Log browser close."""
    log_event("browser_close",
              "Browser session closed",
              {})


def log_queue_add(task_id: str, description: str, priority: str) -> None:
    """Log queue add."""
    log_event("queue_add",
              f"Queue: added '{description[:50]}' [{priority}] as {task_id}",
              {"task_id": task_id, "priority": priority})


def log_queue_start(task_id: str, description: str) -> None:
    """Log queue start."""
    log_event("queue_start",
              f"Queue: starting task {task_id} '{description[:50]}'",
              {"task_id": task_id})


def log_queue_complete(task_id: str, status: str) -> None:
    """Log queue complete."""
    log_event("queue_complete",
              f"Queue: task {task_id} → {status}",
              {"task_id": task_id, "status": status})


def log_queue_failed(task_id: str, error: str) -> None:
    """Log queue failed."""
    log_event("queue_failed",
              f"Queue: task {task_id} FAILED: {error[:100]}",
              {"task_id": task_id, "error": error[:500]})


def log_scheduler_select(task_id: str, reason: str) -> None:
    """Log scheduler select."""
    log_event("scheduler_select",
              f"Scheduler: selected {task_id} — {reason}",
              {"task_id": task_id, "reason": reason})


def log_scheduler_skip(task_id: str, reason: str) -> None:
    """Log scheduler skip."""
    log_event("scheduler_skip",
              f"Scheduler: skipped {task_id} — {reason}",
              {"task_id": task_id, "reason": reason})


def log_worker_isolate(task_id: str, action: str) -> None:
    """Log worker isolate."""
    log_event("worker_isolate",
              f"Worker: {action} state for task {task_id}",
              {"task_id": task_id, "action": action})


def log_worker_archive(task_id: str, archive_path: str) -> None:
    """Log worker archive."""
    log_event("worker_archive",
              f"Worker: archived state to {archive_path}",
              {"task_id": task_id, "path": archive_path})


def log_goal_set(goal_id: str, goal_text: str) -> None:
    """Log goal set."""
    log_event("goal_set",
              f"Goal set: '{goal_text[:60]}' (id: {goal_id})",
              {"goal_id": goal_id, "goal": goal_text[:200]})


def log_goal_complete(goal_id: str, reason: str) -> None:
    """Log goal complete."""
    log_event("goal_complete",
              f"Goal completed: {goal_id} — {reason[:80]}",
              {"goal_id": goal_id, "reason": reason})


def log_goal_failed(goal_id: str, reason: str) -> None:
    """Log goal failed."""
    log_event("goal_failed",
              f"Goal failed: {goal_id} — {reason[:80]}",
              {"goal_id": goal_id, "reason": reason})


def log_auto_iteration(iteration: int, progress: float) -> None:
    """Log auto iteration."""
    log_event("auto_iteration",
              f"Autonomous iteration {iteration} — progress: {progress:.0%}",
              {"iteration": iteration, "progress": progress})


def log_auto_generate(task_count: int, iteration: int) -> None:
    """Log auto generate."""
    log_event("auto_generate",
              f"Generated {task_count} tasks for iteration {iteration}",
              {"count": task_count, "iteration": iteration})


def log_auto_progress(progress: float, status: str, reason: str) -> None:
    """Log auto progress."""
    log_event("auto_progress",
              f"Progress: {progress:.0%} ({status}) — {reason[:80]}",
              {"progress": progress, "status": status, "reason": reason})


def log_auto_stop(reason: str) -> None:
    """Log auto stop."""
    log_event("auto_stop",
              f"Autonomous mode stopped: {reason}",
              {"reason": reason})


def log_auto_start(goal_id: str) -> None:
    """Log auto start."""
    log_event("auto_start",
              f"Autonomous mode started for goal {goal_id}",
              {"goal_id": goal_id})


def log_strategy_analyzed(signature: str, best: str, sr: float) -> None:
    log_event("strategy_analyzed",
              f"Strategy analysis: {signature} → best={best} ({sr:.0%})",
              {"signature": signature, "best": best, "success_rate": sr})


def log_pattern_detected(pattern_type: str, signature: str, detail: str) -> None:
    log_event("pattern_detected",
              f"Pattern: {pattern_type} for {signature} — {detail[:80]}",
              {"type": pattern_type, "signature": signature, "detail": detail[:200]})


def log_improvement_advice(failure_type: str, preferred: str, reasoning: str) -> None:
    log_event("improvement_advice",
              f"Advice for {failure_type}: {preferred or 'none'} — {reasoning[:80]}",
              {"failure_type": failure_type, "preferred": preferred, "reasoning": reasoning[:300]})


def log_adaptive_plan(step_id: str, action: str, source: str) -> None:
    log_event("adaptive_plan",
              f"Adaptive plan for {step_id}: {action} (source: {source})",
              {"step_id": step_id, "action": action[:100], "source": source})


def log_intelligence_trend(trend: str, delta: float) -> None:
    log_event("intelligence_trend",
              f"Performance trend: {trend} (delta: {delta:+.1%})",
              {"trend": trend, "delta": delta})


def log_improvement_applied(step_id: str, change: str, reason: str) -> None:
    log_event("improvement_applied",
              f"Improvement applied to {step_id}: {change} — {reason[:80]}",
              {"step_id": step_id, "change": change, "reason": reason[:200]})


def log_visibility(task_id: str, message: str) -> None:
    """Append real-time visibility log to task-specific file."""
    timestamp = _timestamp()
    log_line = f"[{timestamp}] {message}\n"
    log_file = _get_runtime_dir() / "logs" / f"{task_id}.log"
    with _open_append(log_file):
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_line)