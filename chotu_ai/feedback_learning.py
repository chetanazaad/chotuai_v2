"""Feedback Learning Engine — centralized outcome interpreter."""
import dataclasses
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclasses.dataclass
class LearningInput:
    step: dict
    val_result: object
    dec_result: object
    outcome: str
    base_dir: object


@dataclasses.dataclass
class LearningOutput:
    learning_event_id: str
    pattern_signature: str
    strategy_name: str
    source: str
    outcome: str
    confidence: float
    reason: str
    before: dict
    after: dict
    delta: dict
    recommendation: str
    notes: str


def learn(input: LearningInput) -> LearningOutput:
    """Single entry point for all learning. Never raises."""
    from . import logger, smart_memory

    try:
        step_id = input.step.get("id", "")
        logger.log_learning_start(step_id)

        outcome = _classify_outcome(input)

        strategy_info = _extract_strategy_info(input)
        strategy_name = strategy_info["strategy_name"]
        source = strategy_info["source"]
        action_hint = strategy_info["action_hint"]

        signature = smart_memory.normalize_signature(input.val_result, input.step, {})
        context_tags = smart_memory.get_context_tags(input.val_result, input.step, {})

        before = _get_before_snapshot(signature, strategy_name, input.base_dir)

        if outcome in ("success", "partial"):
            _update_memory(signature, strategy_name, action_hint, context_tags, "success", input.base_dir)
        elif outcome == "failure":
            _update_memory(signature, strategy_name, action_hint, context_tags, "failure", input.base_dir)

        after = _get_after_snapshot(signature, strategy_name, input.base_dir)
        delta = _compute_delta(before, after)

        search_note = ""
        if source == "search" and outcome == "success":
            search_note = _handle_search_derived(input, signature, context_tags)

        meta = {}
        if input.dec_result:
            meta = getattr(input.dec_result, "meta_reasoning", {}) or {}
        pattern = meta.get("pattern", "new_pattern")
        recommendation = _determine_recommendation(after, outcome, source, pattern)

        confidence = _compute_confidence(outcome, after, source)
        reason = _build_reason(outcome, strategy_name, source, recommendation)
        notes = search_note

        output = LearningOutput(
            learning_event_id=f"learn_{uuid.uuid4().hex[:8]}",
            pattern_signature=signature,
            strategy_name=strategy_name,
            source=source,
            outcome=outcome,
            confidence=confidence,
            reason=reason,
            before=before,
            after=after,
            delta=delta,
            recommendation=recommendation,
            notes=notes
        )

        output = _apply_guardrails(output)

        _persist_event(output, input.base_dir)

        try:
            from . import logger as _logger
            _logger.log_strategy_analyzed(
                output.pattern_signature,
                output.strategy_name,
                output.after.get("success_rate", 0.0),
            )
        except Exception:
            pass

        if outcome == "success":
            logger.log_learning_success(step_id, signature, strategy_name, recommendation)
        elif outcome == "failure":
            logger.log_learning_failure(step_id, signature, strategy_name, recommendation)
        elif outcome == "partial":
            logger.log_learning_partial(step_id, signature, strategy_name)

        if recommendation in ("promote", "demote"):
            logger.log_learning_recommendation(step_id, strategy_name, recommendation, after.get("success_rate", 0))

        try:
            from . import knowledge_store
            knowledge_store.ingest_from_learning(output, input.step, input.base_dir)
        except Exception:
            pass

        return output

    except Exception as e:
        return _build_neutral_output(str(e))


def _classify_outcome(input: LearningInput) -> str:
    """Map decision/verdict to a learning outcome."""
    decision = input.outcome

    if decision == "mark_complete":
        return "success"
    if decision == "skip":
        return "skip"
    if decision in ("fail", "escalate_later"):
        return "failure"
    if decision in ("retry", "fix", "simplify"):
        return "failure"

    return "failure"


def _extract_strategy_info(input: LearningInput) -> dict:
    """Extract the strategy that was used, and where it came from."""
    meta = {}
    if input.dec_result:
        meta = getattr(input.dec_result, "meta_reasoning", {}) or {}

    strategy_name = ""
    source = "planner"
    action_hint = ""

    if input.dec_result:
        strategy_name = getattr(input.dec_result, "strategy", "") or ""
        action_hint = getattr(input.dec_result, "action_hint", "") or ""

    if meta.get("memory_hit", False) and meta.get("memory_confidence", 0) >= 0.6:
        source = "memory"
    elif meta.get("search_hit", False) and meta.get("search_confidence", 0) >= 0.4:
        source = "search"
    elif action_hint.startswith("[MEMORY]"):
        source = "memory"
    elif action_hint.startswith("[SEARCH]"):
        source = "search"

    return {
        "strategy_name": strategy_name,
        "source": source,
        "action_hint": action_hint,
    }


def _get_before_snapshot(signature: str, strategy_name: str, base_dir) -> dict:
    """Read current strategy stats from memory."""
    from . import smart_memory

    memory = smart_memory.load_memory(base_dir)
    entries = memory.get("entries", [])

    for entry in entries:
        if entry.get("signature") == signature:
            for strat in entry.get("strategies", []):
                if strat.get("strategy_name") == strategy_name:
                    return {
                        "successes": strat.get("successes", 0),
                        "failures": strat.get("failures", 0),
                        "attempts": strat.get("attempts", 0),
                        "success_rate": strat.get("success_rate", 0.0),
                    }

    return {"successes": 0, "failures": 0, "attempts": 0, "success_rate": 0.0}


def _get_after_snapshot(signature: str, strategy_name: str, base_dir) -> dict:
    """Read updated strategy stats from memory."""
    return _get_before_snapshot(signature, strategy_name, base_dir)


def _compute_delta(before: dict, after: dict) -> dict:
    """Compute the difference between snapshots."""
    return {
        "successes": after.get("successes", 0) - before.get("successes", 0),
        "failures": after.get("failures", 0) - before.get("failures", 0),
        "attempts": after.get("attempts", 0) - before.get("attempts", 0),
    }


def _update_memory(signature: str, strategy_name: str, action_hint: str, context_tags: list, outcome: str, base_dir) -> None:
    """Update smart memory stats."""
    from . import smart_memory

    if outcome == "success":
        smart_memory.record_success(signature, strategy_name, action_hint, context_tags, base_dir)
    elif outcome == "failure":
        smart_memory.record_failure(signature, strategy_name, action_hint, context_tags, base_dir)


def _handle_search_derived(input: LearningInput, signature: str, context_tags: list) -> str:
    """If search-sourced strategy succeeded, store as candidate in memory."""
    from . import smart_memory

    action_hint = ""
    if input.dec_result:
        action_hint = getattr(input.dec_result, "action_hint", "") or ""

    clean_hint = action_hint
    if clean_hint.startswith("[SEARCH] Found solution: "):
        clean_hint = clean_hint[len("[SEARCH] Found solution: "):]
        if " Original error:" in clean_hint:
            clean_hint = clean_hint[:clean_hint.index(" Original error:")]

    if clean_hint:
        search_strategy_name = f"search_{input.dec_result.strategy}" if input.dec_result and hasattr(input.dec_result, "strategy") else "search_derived"
        smart_memory.record_success(signature, search_strategy_name, clean_hint.strip(), context_tags, input.base_dir)
        return f"Search-derived strategy stored as '{search_strategy_name}' in memory"

    return ""


def _determine_recommendation(after: dict, outcome: str, source: str, pattern: str) -> str:
    """Determine promote/demote/keep/review recommendation."""
    sr = after.get("success_rate", 0.0)
    attempts = after.get("attempts", 0)

    if source == "search" and outcome == "success":
        return "create_candidate"

    if sr >= 0.8 and attempts >= 3:
        return "promote"

    if sr < 0.3 and attempts >= 3:
        return "demote"

    if pattern == "repeated_failure" and outcome == "failure":
        return "review"

    if outcome == "success":
        return "keep"

    if attempts < 3:
        return "keep"

    if sr < 0.5 and attempts >= 3:
        return "demote"

    return "keep"


def _compute_confidence(outcome: str, after: dict, source: str) -> float:
    """Confidence in the learning conclusion."""
    attempts = after.get("attempts", 0)
    sr = after.get("success_rate", 0.0)

    if outcome == "skip":
        return 0.3

    base = 0.5

    if attempts >= 5:
        base += 0.2
    elif attempts >= 3:
        base += 0.1

    if sr >= 0.9 or sr <= 0.1:
        base += 0.1

    if source == "memory":
        base += 0.05

    if source == "search":
        base -= 0.05

    return max(0.1, min(0.95, base))


def _build_reason(outcome: str, strategy_name: str, source: str, recommendation: str) -> str:
    """Human-readable learning explanation."""
    if outcome == "success":
        return f"Strategy '{strategy_name}' (source: {source}) succeeded. Recommendation: {recommendation}."
    if outcome == "failure":
        return f"Strategy '{strategy_name}' (source: {source}) failed. Recommendation: {recommendation}."
    if outcome == "partial":
        return f"Strategy '{strategy_name}' (source: {source}) partially succeeded. Recommendation: {recommendation}."
    if outcome == "skip":
        return f"Step skipped. Strategy '{strategy_name}' was not fully tested. No ranking change."
    return f"Learning recorded for '{strategy_name}'. Recommendation: {recommendation}."


def _apply_guardrails(output: LearningOutput) -> LearningOutput:
    """Prevent overclaiming and clamp values."""
    if output.after.get("attempts", 0) < 2 and output.recommendation == "promote":
        output.recommendation = "keep"
        output.notes += " Guardrail: too few attempts to promote."

    if output.after.get("attempts", 0) < 2 and output.recommendation == "demote":
        output.recommendation = "keep"
        output.notes += " Guardrail: too few attempts to demote."

    output.confidence = max(0.1, min(0.95, output.confidence))

    return output


def _persist_event(output: LearningOutput, base_dir) -> None:
    """Append learning event to .chotu/learning.jsonl."""
    if base_dir is None:
        base_dir = Path.cwd()

    runtime_dir = Path(str(base_dir)) / ".chotu"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    log_file = runtime_dir / "learning.jsonl"

    event = {
        "id": output.learning_event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pattern": output.pattern_signature,
        "strategy": output.strategy_name,
        "source": output.source,
        "outcome": output.outcome,
        "confidence": output.confidence,
        "recommendation": output.recommendation,
        "before": output.before,
        "after": output.after,
        "delta": output.delta,
        "reason": output.reason,
        "notes": output.notes,
    }

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def _build_neutral_output(error: str) -> LearningOutput:
    """Return a safe, neutral output when learning fails."""
    return LearningOutput(
        learning_event_id=f"learn_{uuid.uuid4().hex[:8]}",
        pattern_signature="unknown",
        strategy_name="unknown",
        source="unknown",
        outcome="unknown",
        confidence=0.0,
        reason=f"Learning failed: {error}",
        before={"successes": 0, "failures": 0, "attempts": 0, "success_rate": 0.0},
        after={"successes": 0, "failures": 0, "attempts": 0, "success_rate": 0.0},
        delta={"successes": 0, "failures": 0, "attempts": 0},
        recommendation="keep",
        notes=f"Error: {error}"
    )