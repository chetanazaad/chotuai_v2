"""Intelligence Evolution Layer - Strategy Analyzer.

Per-strategy analytics that reads from memory.json and produces
strategy reports with success rates, trends, and recommendations.
"""
import json
from pathlib import Path
from typing import Optional, List
import dataclasses


@dataclasses.dataclass
class StrategyReport:
    signature: str
    strategy_name: str
    success_rate: float
    attempts: int
    successes: int
    failures: int
    trend: str
    last_outcome: str
    rank: int
    recommendation: str


@dataclasses.dataclass
class SystemStats:
    total_signatures: int
    total_strategies: int
    total_attempts: int
    overall_success_rate: float
    top_failure_types: List[dict]
    most_effective_strategies: List
    least_effective_strategies: List


def analyze_all(base_dir=None) -> List[StrategyReport]:
    """Analyze all strategies from memory."""
    if base_dir is None:
        base_dir = Path.cwd()

    from . import smart_memory
    memory = smart_memory.load_memory(base_dir)
    entries = memory.get("entries", [])

    reports = []
    for entry in entries:
        signature = entry.get("signature", "")
        strategies = entry.get("strategies", [])

        ranked = sorted(strategies, key=lambda s: (
            s.get("success_rate", 0), s.get("attempts", 0)
        ), reverse=True)

        for rank, strategy in enumerate(ranked, 1):
            sr = strategy.get("success_rate", 0.0)
            attempts = strategy.get("attempts", 0)
            successes = strategy.get("successes", 0)
            failures = strategy.get("failures", 0)

            trend = _compute_trend(strategy)
            recommendation = _compute_recommendation(sr, attempts, rank)

            reports.append(StrategyReport(
                signature=signature,
                strategy_name=strategy.get("strategy_name", ""),
                success_rate=sr,
                attempts=attempts,
                successes=successes,
                failures=failures,
                trend=trend,
                last_outcome=strategy.get("last_outcome", ""),
                rank=rank,
                recommendation=recommendation,
            ))

    reports.sort(key=lambda r: r.attempts, reverse=True)
    return reports


def analyze_by_type(failure_type: str, base_dir=None) -> List[StrategyReport]:
    """Analyze strategies for a specific failure type."""
    if base_dir is None:
        base_dir = Path.cwd()

    from . import smart_memory
    memory = smart_memory.load_memory(base_dir)
    entries = memory.get("entries", [])

    reports = []
    for entry in entries:
        sig = entry.get("signature", "")
        cat = sig.split(":")[0] if ":" in sig else sig

        if cat != failure_type:
            continue

        strategies = entry.get("strategies", [])
        ranked = sorted(strategies, key=lambda s: (
            s.get("success_rate", 0), s.get("attempts", 0)
        ), reverse=True)

        for rank, strategy in enumerate(ranked, 1):
            sr = strategy.get("success_rate", 0.0)
            attempts = strategy.get("attempts", 0)
            successes = strategy.get("successes", 0)
            failures = strategy.get("failures", 0)

            trend = _compute_trend(strategy)
            recommendation = _compute_recommendation(sr, attempts, rank)

            reports.append(StrategyReport(
                signature=sig,
                strategy_name=strategy.get("strategy_name", ""),
                success_rate=sr,
                attempts=attempts,
                successes=successes,
                failures=failures,
                trend=trend,
                last_outcome=strategy.get("last_outcome", ""),
                rank=rank,
                recommendation=recommendation,
            ))

    return reports


def get_best_for(signature: str, base_dir=None) -> Optional[StrategyReport]:
    """Get the single best proven strategy for a signature."""
    failure_type = signature.split(":")[0] if ":" in signature else signature
    reports = analyze_by_type(failure_type, base_dir)

    proven = [r for r in reports if r.attempts >= 2 and r.success_rate >= 0.5]
    if not proven:
        return None

    proven.sort(key=lambda r: (r.success_rate, r.attempts), reverse=True)
    return proven[0]


def _compute_trend(strategy: dict) -> str:
    """Determine if strategy is improving, declining, or stable."""
    attempts = strategy.get("attempts", 0)
    if attempts < 3:
        return "new"

    sr = strategy.get("success_rate", 0.5)
    last = strategy.get("last_outcome", "")

    if sr >= 0.7 and last == "success":
        return "improving"
    elif sr <= 0.3 and last == "failure":
        return "declining"
    return "stable"


def _compute_recommendation(sr: float, attempts: int, rank: int) -> str:
    """Recommend use/avoid/try based on stats."""
    if attempts < 2:
        return "insufficient_data"
    if sr >= 0.7 and rank == 1:
        return "use"
    if sr >= 0.5:
        return "try"
    if sr < 0.3 and attempts >= 3:
        return "avoid"
    return "try"


def get_system_stats(base_dir=None) -> SystemStats:
    """Compute system-wide analytics."""
    if base_dir is None:
        base_dir = Path.cwd()

    from . import smart_memory
    memory = smart_memory.load_memory(base_dir)
    entries = memory.get("entries", [])

    total_sigs = len(entries)
    total_strats = 0
    total_attempts = 0
    total_successes = 0
    type_counts = {}

    for entry in entries:
        sig = entry.get("signature", "")
        category = sig.split(":")[0] if ":" in sig else sig
        strategies = entry.get("strategies", [])
        total_strats += len(strategies)

        for s in strategies:
            a = s.get("attempts", 0)
            sc = s.get("successes", 0)
            total_attempts += a
            total_successes += sc

            if category not in type_counts:
                type_counts[category] = {"attempts": 0, "successes": 0}
            type_counts[category]["attempts"] += a
            type_counts[category]["successes"] += sc

    overall_sr = total_successes / max(total_attempts, 1)

    top_failures = []
    for ft, data in type_counts.items():
        failures = data["attempts"] - data["successes"]
        rate = data["successes"] / max(data["attempts"], 1)
        top_failures.append({"type": ft, "count": failures, "rate": round(rate, 2)})
    top_failures.sort(key=lambda x: x["count"], reverse=True)

    return SystemStats(
        total_signatures=total_sigs,
        total_strategies=total_strats,
        total_attempts=total_attempts,
        overall_success_rate=round(overall_sr, 3),
        top_failure_types=top_failures[:5],
        most_effective_strategies=[],
        least_effective_strategies=[],
    )