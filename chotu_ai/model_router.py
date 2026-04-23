"""Model Router — complexity-aware model selection."""
import dataclasses
from typing import Optional


@dataclasses.dataclass
class RoutingDecision:
    provider: str
    model: str
    reason: str
    escalated: bool
    max_tokens: int
    temperature: float


_ROUTING_TABLE = {
    ("low", "first"): {"provider": "phi3", "max_tokens": 1024, "temp": 0.1},
    ("low", "retry"): {"provider": "phi3", "max_tokens": 1536, "temp": 0.15},
    ("low", "escalate"): {"provider": "qwen:7b", "max_tokens": 2048, "temp": 0.1},

    ("medium", "first"): {"provider": "qwen:7b", "max_tokens": 2048, "temp": 0.1},
    ("medium", "retry"): {"provider": "qwen:7b", "max_tokens": 3072, "temp": 0.15},
    ("medium", "escalate"): {"provider": "qwen:7b", "max_tokens": 4096, "temp": 0.2},

    ("high", "first"): {"provider": "qwen:7b", "max_tokens": 3072, "temp": 0.1},
    ("high", "retry"): {"provider": "qwen:7b", "max_tokens": 4096, "temp": 0.15},
    ("high", "escalate"): {"provider": "qwen:7b", "max_tokens": 4096, "temp": 0.2},
}

_routing_stats = {"total": 0, "by_provider": {}, "escalations": 0}


def select_model(purpose: str = "",
               task_profile: dict = None,
               retry_count: int = 0,
               confidence: float = 1.0,
               escalation_level: int = 0) -> RoutingDecision:
    """Select the best model based on task complexity, confidence, and retry state."""
    task_profile = task_profile or {}
    complexity = task_profile.get("complexity", "medium")

    if retry_count == 0:
        retry_bucket = "first"
    elif retry_count <= 2:
        retry_bucket = "retry"
    else:
        retry_bucket = "escalate"

    if confidence < 0.4 and retry_bucket == "retry":
        retry_bucket = "escalate"

    if escalation_level >= 2:
        retry_bucket = "escalate"

    if purpose in ("planning", "debugging", "reasoning"):
        complexity = max(complexity, "medium")

    key = (complexity, retry_bucket)
    config = _ROUTING_TABLE.get(key, _ROUTING_TABLE[("medium", "first")])

    provider = config["provider"]
    escalated = retry_bucket == "escalate" and retry_count > 0

    reason = (
        f"complexity={complexity} retry={retry_count} "
        f"confidence={confidence:.2f} bucket={retry_bucket}"
    )

    _routing_stats["total"] += 1
    _routing_stats["by_provider"][provider] = _routing_stats["by_provider"].get(provider, 0) + 1
    if escalated:
        _routing_stats["escalations"] += 1

    return RoutingDecision(
        provider=provider,
        model=provider,
        reason=reason,
        escalated=escalated,
        max_tokens=config["max_tokens"],
        temperature=config["temp"],
    )


def get_routing_stats() -> dict:
    """Return routing usage statistics."""
    return dict(_routing_stats)