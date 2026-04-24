"""Decision Engine module — structured, decision-ready output for step outcomes."""
import dataclasses
from typing import Optional
from pathlib import Path


@dataclasses.dataclass
class DecisionResult:
    decision: str
    strategy: str
    reason: str
    meta_reasoning: dict
    action_hint: str
    confidence: float
    escalation_level: int
    retryable: bool
    update_step: dict
    notes: str


_SEVERITY_MAP = {
    "none": "low",
    "syntax_error": "medium",
    "missing_dependency": "medium",
    "runtime_error": "medium",
    "timeout": "high",
    "infrastructure": "critical",
    "incorrect_output": "medium",
    "unknown": "high",
}

_STRATEGY_HINTS = {
    "accept": "",
    "retry_basic": "Retry the same approach. The system will attempt the step again.",
    "retry_with_context": "Retry with the error context. Adjust command or content based on the failure.",
    "retry_with_timeout_increase": "Retry with a simpler or faster approach. Previous attempt timed out.",
    "fix_syntax": "The previous code had a syntax error. Regenerate with correct syntax. Pay close attention to indentation, colons, brackets, and string delimiters.",
    "fix_dependency": "The previous attempt failed due to a missing module/package. Add an install step (pip install) or use only standard library modules.",
    "fix_output": "The previous command ran but produced wrong output. Adjust the approach to produce the expected result.",
    "simplify_step": "Previous attempts have failed repeatedly. Use the simplest possible approach. Reduce complexity. Use the most basic method available.",
    "reduce_complexity": "The step is too heavy. Break it down or use a lighter approach.",
    "skip_non_critical": "",
    "fail_exhausted": "",
    "fail_infrastructure": "",
    "fail_not_retryable": "",
    "fail_low_confidence": "Overall confidence too low across all phases. Aborting step.",
    "needs_stronger_model": "Marked for future escalation. Current model insufficient to diagnose the issue.",
    "complex_diagnosis": "Marked for future escalation. Issue requires deeper analysis.",
}


def decide(val_result, step: dict, state: dict) -> DecisionResult:
    """Main entry. Takes validation output + step + state. Returns DecisionResult. Never raises."""
    from . import logger
    step_id = step.get("id", "")

    if val_result.verdict == "pass":
        logger.log_decision_engine(step_id, "mark_complete", "accept", 1.0)
        return DecisionResult(
            decision="mark_complete",
            strategy="accept",
            reason="Validation passed — all checks satisfied",
            meta_reasoning={
                "failure_analysis": "none",
                "strategy_selected": "accept",
                "confidence": 1.0,
                "pattern": "none"
            },
            action_hint="",
            confidence=1.0,
            escalation_level=0,
            retryable=False,
            update_step={"status": "completed"},
            notes=""
        )

    analysis = _analyze_failure(val_result, step, state)
    retry_info = _check_retry_limit(step, state)

    try:
        from . import confidence_engine
        plan_conf = step.get("plan_metadata", {}).get("confidence", 0.5)
        confidence_report = confidence_engine.aggregate(
            plan_confidence=plan_conf,
            val_result=val_result,
            step=step,
            state=state,
        )
        analysis["overall_confidence"] = confidence_report.overall
        analysis["confidence_recommendation"] = confidence_report.recommendation
        logger.log_confidence_aggregate(step_id, confidence_report.overall, confidence_report.recommendation)
    except Exception:
        analysis["overall_confidence"] = 0.5
        analysis["confidence_recommendation"] = "caution"

    memory_result = _consult_memory(val_result, step, state)
    analysis["memory_hit"] = memory_result.hit
    analysis["memory_match_type"] = memory_result.match_type
    analysis["memory_confidence"] = memory_result.confidence

    search_result = None
    if _should_search(memory_result, analysis, retry_info):
        search_result = _consult_search(val_result, step, state)
        analysis["search_hit"] = search_result.success
        analysis["search_confidence"] = search_result.confidence
        analysis["search_source"] = search_result.source
    else:
        analysis["search_hit"] = False
        analysis["search_confidence"] = 0.0
        analysis["search_source"] = "none"

    knowledge_result = None
    if (not analysis.get("memory_hit") and
            not analysis.get("search_hit") and
            analysis["retry_count"] >= 1):
        knowledge_result = _consult_knowledge(val_result, step, state)
        analysis["knowledge_hit"] = knowledge_result.hit if knowledge_result else False
        analysis["knowledge_confidence"] = 0.0
        if knowledge_result and knowledge_result.hit and knowledge_result.best_result:
            analysis["knowledge_confidence"] = knowledge_result.best_result.get("metrics", {}).get("confidence", 0.0)
    else:
        analysis["knowledge_hit"] = False
        analysis["knowledge_confidence"] = 0.0

    decision, strategy = _choose_strategy(analysis, step, state, retry_info)
    decision = _apply_guardrails(decision, analysis, retry_info)

    from . import improvement_engine
    try:
        improvement_advice = improvement_engine.get_advice(
            analysis["failure_type"], step, state
        )
        analysis["improvement_advice"] = {
            "preferred": improvement_advice.preferred_strategy,
            "preferred_confidence": improvement_advice.preferred_confidence,
            "skip": improvement_advice.skip_strategies,
            "escalate_early": improvement_advice.escalate_early,
            "prefer_search": improvement_advice.prefer_search,
        }
        from . import logger as _logger
        _logger.log_improvement_advice(
            analysis["failure_type"],
            improvement_advice.preferred_strategy,
            improvement_advice.reasoning,
        )
    except Exception:
        analysis["improvement_advice"] = {}

    action_hint = _build_action_hint(strategy, analysis, memory_result, search_result, knowledge_result)
    pattern = _classify_pattern(analysis, step, state)
    confidence = _determine_confidence(analysis, val_result, retry_info)
    escalation_level = _compute_escalation_level(decision, analysis)
    meta_reasoning = _build_meta_reasoning(analysis, strategy, confidence, pattern)
    update_step = _build_update_step(decision, step)
    reason = _build_reason(decision, strategy, analysis, retry_info)

    result = DecisionResult(
        decision=decision,
        strategy=strategy,
        reason=reason,
        meta_reasoning=meta_reasoning,
        action_hint=action_hint,
        confidence=confidence,
        escalation_level=escalation_level,
        retryable=val_result.retryable,
        update_step=update_step,
        notes=analysis.get("notes", "")
    )

    logger.log_decision_engine(step_id, decision, strategy, confidence)
    logger.log_decision_engine_meta(step_id, meta_reasoning)
    return result


def _analyze_failure(val_result, step: dict, state: dict) -> dict:
    """Produce structured failure analysis."""
    failure_type = val_result.failure_type
    retry_count = step.get("retries", 0)

    prev_result = step.get("result") or {}
    prev_failure = prev_result.get("failure_type", "")
    has_repeated = (prev_failure == failure_type and retry_count > 0)

    severity = _assess_severity(failure_type, val_result)

    return {
        "failure_type": failure_type,
        "verdict": val_result.verdict,
        "is_retryable": val_result.retryable,
        "retry_count": retry_count,
        "stderr_snippet": val_result.details.get("exit_code", ""),
        "has_repeated": has_repeated,
        "suggestion": val_result.suggestion,
        "severity": severity,
        "checks_passed": sum(1 for c in val_result.details.get("checks", []) if c.get("passed")),
        "checks_total": len(val_result.details.get("checks", [])),
        "is_partial": val_result.verdict == "partial",
        "notes": "",
    }


def _assess_severity(failure_type: str, val_result) -> str:
    base = _SEVERITY_MAP.get(failure_type, "medium")
    if val_result.confidence >= 0.95 and base == "medium":
        return "high"
    return base


def _check_retry_limit(step: dict, state: dict) -> dict:
    current = step.get("retries", 0)
    
    task_profile = state.get("core_task", {}).get("task_profile", {})
    is_simple = False
    if isinstance(task_profile, dict):
        is_simple = task_profile.get("complexity") == "low" and task_profile.get("domain") == "filesystem"
    else:
        is_simple = getattr(task_profile, "complexity", "") == "low" and getattr(task_profile, "domain", "") == "filesystem"
        
    if is_simple:
        max_retries = 1
    else:
        max_retries = step.get("max_retries", 3)
    config = state.get("config", {})
    total_retries = state.get("stats", {}).get("total_retries", 0)
    max_total = config.get("max_total_retries", 15)

    return {
        "exhausted": current >= max_retries or total_retries >= max_total,
        "current": current,
        "max": max_retries,
        "total_retries": total_retries,
        "max_total": max_total,
    }


def _choose_strategy(analysis: dict, step: dict, state: dict, retry_info: dict) -> tuple:
    """Returns (decision, strategy)."""
    ft = analysis["failure_type"]
    retryable = analysis["is_retryable"]
    exhausted = retry_info["exhausted"]
    retry_count = analysis["retry_count"]
    has_repeated = analysis["has_repeated"]
    is_partial = analysis["is_partial"]

    confidence_rec = analysis.get("confidence_recommendation", "")
    if confidence_rec == "abort" and not exhausted:
        return ("fail", "fail_low_confidence")
    if confidence_rec == "escalate" and retry_count >= 1 and not exhausted:
        return ("escalate_later", "needs_stronger_model")

    if exhausted:
        return ("fail", "fail_exhausted")

    task_profile = state.get("core_task", {}).get("task_profile", {})
    complexity = task_profile.get("complexity", "") if isinstance(task_profile, dict) else getattr(task_profile, "complexity", "")
    
    action = step.get("action", {})
    if not isinstance(action, dict):
        action = {"type": "unknown", "command": str(action)}
    is_dummy_echo = action.get("type") == "shell" and action.get("command", "").strip().startswith("echo ")
    
    if complexity == "high" and is_dummy_echo and (ft == "incorrect_output" or is_partial or not retryable):
        return ("escalate_later", "needs_stronger_model")

    advice = analysis.get("improvement_advice", {})
    if advice.get("escalate_early") and retry_count >= 1 and not exhausted:
        return ("escalate_later", "needs_stronger_model")
    if advice.get("prefer_search") and retry_count >= 1 and not exhausted:
        analysis["force_search"] = True

    if not retryable:
        if ft == "infrastructure":
            return ("fail", "fail_infrastructure")
        has_dependents = _step_has_dependents(step, state)
        if not has_dependents:
            return ("skip", "skip_non_critical")
        return ("fail", "fail_not_retryable")

    if ft == "syntax_error":
        return ("fix", "fix_syntax")

    if ft == "missing_dependency":
        return ("fix", "fix_dependency")

    if has_repeated and retry_count >= 2:
        return ("simplify", "simplify_step")

    if ft == "timeout":
        if retry_count >= 1:
            return ("simplify", "reduce_complexity")
        return ("retry", "retry_with_timeout_increase")

    if ft == "incorrect_output" or is_partial:
        if retry_count >= 2:
            return ("simplify", "simplify_step")
        return ("fix", "fix_output")

    if ft in ("unknown", "runtime_error"):
        if retry_count >= 2:
            return ("escalate_later", "needs_stronger_model")
        if retry_count >= 1:
            return ("retry", "retry_with_context")
        return ("retry", "retry_basic")

    return ("retry", "retry_basic")


def _step_has_dependents(step: dict, state: dict) -> bool:
    """Check if any other step depends on this step."""
    step_id = step.get("id", "")
    for other_step in state.get("todo_list", []):
        if step_id in other_step.get("depends_on", []):
            return True
    return False


def _apply_guardrails(decision: str, analysis: dict, retry_info: dict) -> str:
    """Override unsafe decisions."""
    if analysis["failure_type"] == "infrastructure" and decision in ("retry", "fix"):
        return "fail"

    if retry_info["exhausted"] and decision in ("retry", "fix", "simplify"):
        return "fail"

    if decision == "mark_complete" and analysis["verdict"] != "pass":
        return "fail"

    if decision == "escalate_later" and analysis["retry_count"] == 0:
        return "retry"

    return decision


def _consult_memory(val_result, step: dict, state: dict):
    """Consult Smart Memory for known strategies."""
    from pathlib import Path

    try:
        from . import smart_memory
        base_dir = Path.cwd()
        runtime_dir = state.get("config", {}).get("runtime_dir", ".chotu")
        if runtime_dir and runtime_dir != ".chotu":
            base_dir = Path(str(runtime_dir)).parent
        elif runtime_dir == ".chotu":
            base_dir = Path.cwd()

        signature = smart_memory.normalize_signature(val_result, step, state)
        context_tags = smart_memory.get_context_tags(val_result, step, state)
        result = smart_memory.lookup(signature, context_tags, base_dir)
        result.signature = signature
        return result
    except Exception:
        from . import smart_memory
        return smart_memory.MemoryLookupResult(
            hit=False, match_type="none", signature="",
            best_strategy={}, alternatives=[],
            confidence=0.0, reason="Memory unavailable"
        )


def _should_search(memory_result, analysis: dict, retry_info: dict) -> bool:
    """Determine if search should be triggered."""
    if analysis.get("force_search"):
        return True
    if memory_result.hit and memory_result.confidence >= 0.6:
        return False
    if analysis["retry_count"] < 1:
        return False
    if retry_info["exhausted"]:
        return False
    searchable_types = ("unknown", "runtime_error", "incorrect_output", "missing_dependency")
    if analysis["failure_type"] in searchable_types:
        return True
    if analysis["has_repeated"] and analysis["retry_count"] >= 2:
        return True
    return False


def _consult_search(val_result, step: dict, state: dict):
    """Consult Filtered Search for solutions."""
    try:
        from . import filtered_search
        request = filtered_search.build_search_request(val_result, step, state)
        return filtered_search.search(request)
    except Exception:
        from . import filtered_search
        return filtered_search.SearchResponse(
            results=[], best_result={}, confidence=0.0,
            source="none", query_used="", success=False, error="Search unavailable"
        )


def _consult_knowledge(val_result, step: dict, state: dict):
    """Consult Knowledge Store as third source. Only called on memory + search miss."""
    try:
        from . import knowledge_store, smart_memory
        signature = smart_memory.normalize_signature(val_result, step, state)
        context_tags = smart_memory.get_context_tags(val_result, step, state)
        result = knowledge_store.query(signature=signature, tags=context_tags, base_dir=Path.cwd())
        return result
    except Exception:
        from . import knowledge_store
        return knowledge_store.QueryResult(
            hit=False, match_type="none", results=[], best_result={},
            reason="Knowledge Store unavailable"
        )


def _build_action_hint(strategy: str, analysis: dict, memory_result=None, search_result=None, knowledge_result=None) -> str:
    """Build planner instruction. Memory > Search > Static hints."""
    base = _STRATEGY_HINTS.get(strategy, "")

    if memory_result and memory_result.hit and memory_result.confidence >= 0.7:
        mem_hint = memory_result.best_strategy.get("action_hint", "")
        if mem_hint:
            sr = memory_result.best_strategy.get("success_rate", 0)
            base = (f"[MEMORY] Previously successful approach ({sr:.0%} success rate): {mem_hint}")
            if analysis.get("suggestion"):
                base += f" Current suggestion: {analysis['suggestion']}"
            return base.strip()

    if search_result and search_result.success and search_result.confidence >= 0.5:
        best = search_result.best_result
        if best:
            solution = best.get("extracted_solution", "")
            if solution:
                base = f"[SEARCH] Found solution: {solution}"
                if analysis.get("suggestion"):
                    base += f" Original error: {analysis['suggestion']}"
                return base.strip()

    if knowledge_result and knowledge_result.hit and knowledge_result.best_result:
        best_k = knowledge_result.best_result
        k_desc = best_k.get("description", "")
        k_status = best_k.get("status", "candidate")
        if k_desc and k_status in ("promoted", "active"):
            k_sr = best_k.get("metrics", {}).get("success_rate", 0)
            base = f"[KNOWLEDGE] Known solution ({k_status}, {k_sr:.0%} sr): {k_desc}"
            if analysis.get("suggestion"):
                base += f" Current error: {analysis['suggestion']}"
            return base.strip()

    if analysis.get("suggestion"):
        if base:
            base += f" Previous suggestion: {analysis['suggestion']}"
        else:
            base = analysis["suggestion"]

    return base.strip()


def _classify_pattern(analysis: dict, step: dict, state: dict) -> str:
    """Label the failure pattern."""
    if analysis["failure_type"] == "none":
        return "none"

    issues = state.get("issues", [])
    same_type_count = sum(
        1 for issue in issues
        if issue.get("type") == analysis["failure_type"]
        and issue.get("step_id") == step.get("id")
    )

    if analysis["has_repeated"]:
        return "repeated_failure"

    if same_type_count >= 2:
        return "known_pattern"

    return "new_pattern"


def _determine_confidence(analysis: dict, val_result, retry_info: dict) -> float:
    """Confidence in the decision."""
    if val_result.verdict == "pass":
        return 1.0

    base = 0.7
    if analysis["failure_type"] in ("syntax_error", "missing_dependency"):
        base = 0.85
    elif analysis["failure_type"] == "infrastructure":
        base = 0.95
    elif analysis["failure_type"] == "unknown":
        base = 0.5

    base -= (analysis["retry_count"] * 0.1)

    return max(0.2, min(1.0, base))


def _compute_escalation_level(decision: str, analysis: dict) -> int:
    if decision == "escalate_later":
        return 2
    if analysis["severity"] == "critical":
        return 2
    if analysis["severity"] == "high":
        return 1
    return 0


def _build_meta_reasoning(analysis: dict, strategy: str, confidence: float, pattern: str) -> dict:
    return {
        "failure_analysis": analysis["failure_type"],
        "severity": analysis["severity"],
        "strategy_selected": strategy,
        "confidence": confidence,
        "pattern": pattern,
        "is_partial": analysis.get("is_partial", False),
        "checks_passed": analysis.get("checks_passed", 0),
        "checks_total": analysis.get("checks_total", 0),
        "has_repeated": analysis.get("has_repeated", False),
        "memory_hit": analysis.get("memory_hit", False),
        "memory_match_type": analysis.get("memory_match_type", "none"),
        "memory_confidence": analysis.get("memory_confidence", 0.0),
        "search_hit": analysis.get("search_hit", False),
        "search_confidence": analysis.get("search_confidence", 0.0),
        "search_source": analysis.get("search_source", "none"),
        "knowledge_hit": analysis.get("knowledge_hit", False),
        "knowledge_confidence": analysis.get("knowledge_confidence", 0.0),
    }


def _build_update_step(decision: str, step: dict) -> dict:
    if decision == "mark_complete":
        return {"status": "completed"}
    elif decision in ("retry", "fix", "simplify"):
        return {"status": "generating", "retries": step.get("retries", 0) + 1}
    elif decision == "skip":
        return {"status": "skipped"}
    elif decision in ("fail", "escalate_later"):
        return {"status": "failed"}
    return {"status": "failed"}


def _build_reason(decision: str, strategy: str, analysis: dict, retry_info: dict) -> str:
    ft = analysis["failure_type"]
    retry_count = analysis["retry_count"]

    if decision == "mark_complete":
        return "Validation passed — step completed successfully."

    if decision == "fail" and retry_info["exhausted"]:
        return f"Step failed after {retry_count} retries (max: {retry_info['max']}). Failure type: {ft}."

    if decision == "fail":
        return f"Step failed — not retryable. Failure type: {ft}."

    if decision == "fix":
        return f"Fixable error detected ({ft}). Strategy: {strategy}. Retrying with fix instructions."

    if decision == "simplify":
        return f"Repeated failures detected. Simplifying approach. Strategy: {strategy}."

    if decision == "retry":
        return f"Retrying step. Attempt {retry_count + 1}/{retry_info['max']}. Strategy: {strategy}."

    if decision == "skip":
        return "Non-critical step skipped �� no dependent steps rely on this."

    if decision == "escalate_later":
        return f"Marked for future escalation. Current strategy insufficient for {ft}."

    return f"Decision: {decision}. Strategy: {strategy}."