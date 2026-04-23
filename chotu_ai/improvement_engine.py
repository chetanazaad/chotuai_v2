"""Intelligence Evolution Layer - Improvement Engine.

Advisory recommendations that combine strategy_analyzer and pattern_detector
to produce actionable advice for decision engine and planner.
"""
from pathlib import Path
from typing import Optional
import dataclasses

from . import strategy_analyzer
from . import pattern_detector


@dataclasses.dataclass
class ImprovementAdvice:
    preferred_strategy: str
    preferred_confidence: float
    skip_strategies: list
    escalate_early: bool
    prefer_search: bool
    known_action_hint: str
    reasoning: str


def get_advice(failure_type: str, step: Optional[dict] = None,
              state: Optional[dict] = None, base_dir=None) -> ImprovementAdvice:
    """
    Get improvement advice for a specific failure.
    Combines strategy analysis + pattern detection.
    """
    if base_dir is None:
        base_dir = Path.cwd()

    preferred = ""
    preferred_conf = 0.0
    skip = []
    known_hint = ""

    try:
        best = strategy_analyzer.get_best_for(failure_type, base_dir)
        if best and best.recommendation == "use":
            preferred = best.strategy_name
            preferred_conf = best.success_rate

        reports = strategy_analyzer.analyze_by_type(failure_type, base_dir)
        for r in reports:
            if r.recommendation == "avoid":
                skip.append(r.strategy_name)
    except Exception:
        pass

    try:
        from . import smart_memory
        memory = smart_memory.load_memory(base_dir)
        for entry in memory.get("entries", []):
            if entry.get("signature", "").startswith(failure_type):
                for s in entry.get("strategies", []):
                    if s.get("success_rate", 0) >= 0.7 and s.get("attempts", 0) >= 2:
                        hint = s.get("action_hint", "")
                        if hint and not hint.startswith("["):
                            known_hint = hint
                            break
                if known_hint:
                    break
    except Exception:
        pass

    escalate_early = False
    prefer_search = False
    reasoning_parts = []

    try:
        patterns = pattern_detector.detect_for(failure_type, base_dir)
        for p in patterns:
            if p.recommendation == "escalate_early":
                escalate_early = True
                reasoning_parts.append(f"Escalate early: {p.detail}")
            elif p.recommendation == "prefer_search":
                prefer_search = True
                reasoning_parts.append(f"Search effective: {p.detail}")
            elif p.recommendation == "avoid_current_strategy":
                reasoning_parts.append(f"Pattern: {p.detail}")
    except Exception:
        pass

    if preferred:
        reasoning_parts.insert(0, f"Preferred: {preferred} ({preferred_conf:.0%} success)")
    if skip:
        reasoning_parts.append(f"Avoid: {', '.join(skip)}")

    reasoning = "; ".join(reasoning_parts) if reasoning_parts else "No historical data"

    return ImprovementAdvice(
        preferred_strategy=preferred,
        preferred_confidence=preferred_conf,
        skip_strategies=skip,
        escalate_early=escalate_early,
        prefer_search=prefer_search,
        known_action_hint=known_hint,
        reasoning=reasoning,
    )


def get_planning_advice(step: Optional[dict] = None, state: Optional[dict] = None,
                       base_dir=None) -> ImprovementAdvice:
    """
    Get advice for the planning stage (before any failure).
    Looks for known-good approaches for similar steps.
    """
    if base_dir is None:
        base_dir = Path.cwd()

    step = step or {}
    desc = step.get("description", "").lower()

    known_hint = ""
    preferred_conf = 0.0

    try:
        from . import smart_memory
        memory = smart_memory.load_memory(base_dir)

        for entry in memory.get("entries", []):
            for strat in entry.get("strategies", []):
                hint = strat.get("action_hint", "")
                sr = strat.get("success_rate", 0.0)
                attempts = strat.get("attempts", 0)

                if sr >= 0.8 and attempts >= 3 and hint:
                    hint_lower = hint.lower()
                    desc_words = [w for w in desc.split() if len(w) > 3]
                    overlap = sum(1 for w in desc_words if w in hint_lower)
                    if overlap >= 2:
                        known_hint = hint
                        preferred_conf = sr
                        break
            if known_hint:
                break
    except Exception:
        pass

    return ImprovementAdvice(
        preferred_strategy="",
        preferred_confidence=preferred_conf,
        skip_strategies=[],
        escalate_early=False,
        prefer_search=False,
        known_action_hint=known_hint,
        reasoning=f"Known approach: {known_hint[:60]}" if known_hint else "No known approach",
    )