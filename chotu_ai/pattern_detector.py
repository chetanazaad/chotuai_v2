"""Intelligence Evolution Layer - Pattern Detector.

System-wide pattern detection that reads from learning.jsonl
to identify repeated failures, bottlenecks, escalation patterns,
and search effectiveness.
"""
import json
from pathlib import Path
from typing import List, Optional
import dataclasses


@dataclasses.dataclass
class Pattern:
    pattern_type: str
    signature: str
    confidence: float
    detail: str
    recommendation: str
    evidence_count: int


def detect_all(base_dir=None) -> List[Pattern]:
    """Run all pattern detectors on learning history."""
    if base_dir is None:
        base_dir = Path.cwd()

    events = _load_learning_events(base_dir)
    if not events:
        return []

    patterns = []
    patterns.extend(_detect_repeated_failures(events))
    patterns.extend(_detect_bottlenecks(events))
    patterns.extend(_detect_escalation_patterns(events))
    patterns.extend(_detect_search_effectiveness(events))

    patterns.sort(key=lambda p: p.confidence, reverse=True)
    return patterns


def detect_for(failure_type: str, base_dir=None) -> List[Pattern]:
    """Detect patterns for a specific failure type."""
    if base_dir is None:
        base_dir = Path.cwd()

    events = _load_learning_events(base_dir)
    if not events:
        return []

    filtered = [e for e in events if e.get("pattern", "").startswith(failure_type)]

    patterns = []
    patterns.extend(_detect_repeated_failures(filtered))
    patterns.extend(_detect_search_effectiveness(filtered))

    patterns.sort(key=lambda p: p.confidence, reverse=True)
    return patterns


def detect_trends(base_dir=None) -> dict:
    """Compare first-half vs second-half performance."""
    if base_dir is None:
        base_dir = Path.cwd()

    events = _load_learning_events(base_dir)
    if len(events) < 6:
        return {"trend": "insufficient_data", "events": len(events)}

    mid = len(events) // 2
    first_half = events[:mid]
    second_half = events[mid:]

    first_sr = _compute_half_success_rate(first_half)
    second_sr = _compute_half_success_rate(second_half)

    delta = second_sr - first_sr
    if delta > 0.1:
        trend = "improving"
    elif delta < -0.1:
        trend = "declining"
    else:
        trend = "stable"

    return {
        "trend": trend,
        "first_half_success_rate": round(first_sr, 3),
        "second_half_success_rate": round(second_sr, 3),
        "delta": round(delta, 3),
        "total_events": len(events),
    }


def _load_learning_events(base_dir) -> list:
    """Load learning.jsonl."""
    learning_file = Path(str(base_dir)) / ".chotu" / "learning.jsonl"
    if not learning_file.exists():
        return []

    events = []
    try:
        with open(learning_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except IOError:
        pass
    return events


def _detect_repeated_failures(events: list) -> list:
    """Detect signatures with repeated failures."""
    patterns = []
    sig_failures = {}

    for event in events:
        sig = event.get("pattern", "")
        outcome = event.get("outcome", "")

        if not sig:
            continue

        if sig not in sig_failures:
            sig_failures[sig] = {"consecutive": 0, "total_failures": 0, "total": 0}

        sig_failures[sig]["total"] += 1
        if outcome == "failure":
            sig_failures[sig]["consecutive"] += 1
            sig_failures[sig]["total_failures"] += 1
        else:
            sig_failures[sig]["consecutive"] = 0

    for sig, data in sig_failures.items():
        if data["consecutive"] >= 3:
            patterns.append(Pattern(
                pattern_type="repeated_failure",
                signature=sig,
                confidence=min(0.95, 0.5 + data["consecutive"] * 0.1),
                detail=f"'{sig}' has failed {data['consecutive']} times consecutively",
                recommendation="escalate_early",
                evidence_count=data["consecutive"],
            ))

        if data["total"] >= 5:
            fail_rate = data["total_failures"] / data["total"]
            if fail_rate >= 0.7:
                patterns.append(Pattern(
                    pattern_type="repeated_failure",
                    signature=sig,
                    confidence=min(0.9, fail_rate),
                    detail=f"'{sig}' has {fail_rate:.0%} failure rate over {data['total']} events",
                    recommendation="avoid_current_strategy",
                    evidence_count=data["total"],
                ))

    return patterns


def _detect_bottlenecks(events: list) -> list:
    """Detect failure types that dominate."""
    patterns = []
    type_counts = {}

    for event in events:
        sig = event.get("pattern", "")
        category = sig.split(":")[0] if ":" in sig else sig
        if category and event.get("outcome") == "failure":
            type_counts[category] = type_counts.get(category, 0) + 1

    total_failures = sum(type_counts.values())
    for category, count in type_counts.items():
        if total_failures > 0:
            share = count / total_failures
            if share >= 0.3 and count >= 3:
                patterns.append(Pattern(
                    pattern_type="bottleneck",
                    signature=category,
                    confidence=min(0.9, share),
                    detail=f"'{category}' accounts for {share:.0%} of all failures ({count}/{total_failures})",
                    recommendation="prioritize_fix",
                    evidence_count=count,
                ))

    return patterns


def _detect_escalation_patterns(events: list) -> list:
    """Detect signatures that need escalation."""
    patterns = []
    sig_escalations = {}

    for event in events:
        sig = event.get("pattern", "")
        strategy = event.get("strategy", "")

        if not sig:
            continue
        if sig not in sig_escalations:
            sig_escalations[sig] = {"total": 0, "escalations": 0}

        sig_escalations[sig]["total"] += 1
        if "escalate" in strategy or "stronger_model" in strategy:
            sig_escalations[sig]["escalations"] += 1

    for sig, data in sig_escalations.items():
        if data["total"] >= 3 and data["escalations"] >= 2:
            rate = data["escalations"] / data["total"]
            patterns.append(Pattern(
                pattern_type="escalation_needed",
                signature=sig,
                confidence=min(0.85, rate),
                detail=f"'{sig}' escalated {data['escalations']}/{data['total']} times",
                recommendation="escalate_early",
                evidence_count=data["escalations"],
            ))

    return patterns


def _detect_search_effectiveness(events: list) -> list:
    """Detect when search-derived strategies are effective."""
    patterns = []
    sig_search = {}

    for event in events:
        sig = event.get("pattern", "")
        source = event.get("source", "")
        outcome = event.get("outcome", "")

        if source != "search" or not sig:
            continue

        if sig not in sig_search:
            sig_search[sig] = {"total": 0, "successes": 0}

        sig_search[sig]["total"] += 1
        if outcome == "success":
            sig_search[sig]["successes"] += 1

    for sig, data in sig_search.items():
        if data["total"] >= 2:
            rate = data["successes"] / data["total"]
            if rate >= 0.5:
                patterns.append(Pattern(
                    pattern_type="search_effective",
                    signature=sig,
                    confidence=min(0.85, rate),
                    detail=f"Search solves '{sig}' {rate:.0%} of the time ({data['successes']}/{data['total']})",
                    recommendation="prefer_search",
                    evidence_count=data["total"],
                ))

    return patterns


def _compute_half_success_rate(events: list) -> float:
    successes = sum(1 for e in events if e.get("outcome") == "success")
    return successes / max(len(events), 1)