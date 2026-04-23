"""Intelligence Evolution Layer - Adaptive Planner.

Memory-informed planning that checks if a known-good approach
exists before calling the LLM. Skips LLM when confidence is high.
"""
from pathlib import Path
from typing import Optional
import dataclasses

from . import improvement_engine


@dataclasses.dataclass
class AdaptiveHint:
    has_hint: bool
    known_command: str
    preferred_action_type: str
    avoid_patterns: list
    confidence: float
    source: str
    reasoning: str


def enhance_plan(step: dict, state: Optional[dict] = None,
               base_dir=None) -> AdaptiveHint:
    """
    Check memory for a known-good approach to this step.
    Returns an AdaptiveHint that the planner can use.
    """
    if base_dir is None:
        base_dir = Path.cwd()

    step = step or {}
    desc = step.get("description", "").lower()
    retries = step.get("retries", 0)

    known_command = ""
    preferred_type = ""
    avoid = []
    confidence = 0.0
    source = "none"
    reasoning = "No adaptive hint available"

    try:
        advice = improvement_engine.get_planning_advice(step, state, base_dir)
        if advice.known_action_hint and advice.preferred_confidence >= 0.7:
            known_command = advice.known_action_hint
            confidence = advice.preferred_confidence
            source = "memory"
            reasoning = f"Known approach ({confidence:.0%} success): {known_command[:80]}"
    except Exception:
        pass

    if not known_command and retries > 0:
        try:
            from . import smart_memory
            last_result = step.get("result", {})
            failure_type = last_result.get("failure_type", "")

            if failure_type:
                memory = smart_memory.load_memory(base_dir)
                for entry in memory.get("entries", []):
                    sig = entry.get("signature", "")
                    if sig.startswith(failure_type):
                        best = entry.get("best_strategy_id", "")
                        for s in entry.get("strategies", []):
                            if (s.get("strategy_id") == best and
                                    s.get("success_rate", 0) >= 0.7 and
                                    s.get("attempts", 0) >= 2):
                                hint = s.get("action_hint", "")
                                if hint:
                                    known_command = hint
                                    confidence = s["success_rate"]
                                    source = "memory"
                                    reasoning = f"Memory match: {sig} → {hint[:60]}"
                                    break
                        if known_command:
                            break
        except Exception:
            pass

    if not preferred_type:
        if any(kw in desc for kw in ["search", "find", "look up", "google"]):
            preferred_type = "browser"
        elif any(kw in desc for kw in ["create file", "write", "save"]):
            preferred_type = "file_write"
        elif any(kw in desc for kw in ["install", "run", "execute", "pip"]):
            preferred_type = "shell"

    try:
        if retries > 0:
            last_result = step.get("result", {})
            ft = last_result.get("failure_type", "")
            if ft:
                advice = improvement_engine.get_advice(ft, step, state, base_dir)
                avoid = advice.skip_strategies
    except Exception:
        pass

    has_hint = bool(known_command) or bool(preferred_type) or bool(avoid)

    return AdaptiveHint(
        has_hint=has_hint,
        known_command=known_command,
        preferred_action_type=preferred_type,
        avoid_patterns=avoid,
        confidence=confidence,
        source=source,
        reasoning=reasoning,
    )