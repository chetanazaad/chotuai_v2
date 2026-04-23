"""Confidence Engine — cross-module confidence aggregation."""
import dataclasses
from typing import Optional


@dataclasses.dataclass
class ConfidenceReport:
    overall: float
    signals: dict
    recommendation: str
    breakdown: str


def aggregate(plan_confidence: float = 0.0,
             exec_result=None,
             val_result=None,
             step: dict = None,
             state: dict = None) -> ConfidenceReport:
    """Aggregate confidence from all phases into a single report."""
    signals = {}
    step = step or {}
    retry_count = step.get("retries", 0)
    max_retries = step.get("max_retries", 3)

    signals["plan"] = max(0.0, min(1.0, plan_confidence))

    if exec_result:
        if getattr(exec_result, "exit_code", None) == 0 and getattr(exec_result, "success", True):
            signals["execution"] = 1.0
        elif getattr(exec_result, "timed_out", False):
            signals["execution"] = 0.1
        else:
            signals["execution"] = 0.2
    else:
        signals["execution"] = 0.5

    if val_result:
        signals["validation"] = max(0.0, min(1.0, getattr(val_result, "confidence", 0.5)))
    else:
        signals["validation"] = 0.5

    retry_penalty = retry_count / max(max_retries, 1)
    signals["history"] = max(0.1, 1.0 - retry_penalty)

    weights = {
        "plan": 0.15,
        "execution": 0.35,
        "validation": 0.35,
        "history": 0.15,
    }
    overall = sum(signals[k] * weights[k] for k in weights)
    overall = max(0.0, min(1.0, overall))

    recommendation = get_recommendation(overall, retry_count)

    breakdown = (
        f"plan={signals['plan']:.2f} "
        f"exec={signals['execution']:.2f} "
        f"val={signals['validation']:.2f} "
        f"hist={signals['history']:.2f} "
        f"→ overall={overall:.2f} ({recommendation})"
    )

    return ConfidenceReport(
        overall=overall,
        signals=signals,
        recommendation=recommendation,
        breakdown=breakdown,
    )


def get_recommendation(overall: float, retry_count: int = 0) -> str:
    """Convert overall confidence to a recommendation."""
    if overall >= 0.8:
        return "proceed"
    elif overall >= 0.5:
        return "caution"
    elif overall >= 0.3:
        if retry_count >= 2:
            return "escalate"
        return "caution"
    else:
        if retry_count >= 2:
            return "abort"
        return "escalate"


def get_history_confidence(step: dict) -> float:
    """Get confidence based on step retry history."""
    step = step or {}
    retry_count = step.get("retries", 0)
    max_retries = step.get("max_retries", 3)
    retry_penalty = retry_count / max(max_retries, 1)
    return max(0.1, 1.0 - retry_penalty)


def get_signals_breakdown(signals: dict) -> str:
    """Format signals as a readable string."""
    return " | ".join(f"{k}={v:.2f}" for k, v in signals.items())