"""Model Router — complexity-aware model selection."""
import dataclasses
from typing import Optional

# --- SAFE MODE OVERRIDE ---
# Set to True for low-VRAM systems that cannot handle qwen:7b reliably
# Set to False to restore original intelligent routing
SAFE_MODE = False


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
               escalation_level: int = 0,
               failure_type: str = None) -> RoutingDecision:
    """Select the best model based on task complexity, confidence, and retry state."""
    task_profile = task_profile or {}
    complexity = task_profile.get("complexity", "medium")

    # --- SAFE MODE OVERRIDE ---
    if SAFE_MODE:
        print(f"[MODEL ROUTER] SAFE MODE ACTIVE -> Using phi3 for {complexity} task")
        return RoutingDecision(
            provider="phi3",
            model="phi3",
            reason="safe_mode_override",
            escalated=False,
            max_tokens=1024,
            temperature=0.1,
        )

    # --- HYBRID ROUTING LOGIC ---
    model = "phi3"
    
    # --- LOW COMPLEXITY ---
    if complexity == "low":
        model = "phi3"
    
    # --- MEDIUM COMPLEXITY ---
    elif complexity == "medium":
        if retry_count == 0:
            model = "phi3"
        else:
            model = "qwen:7b"
    
    # --- HIGH COMPLEXITY ---
    elif complexity == "high":
        if retry_count == 0:
            model = "phi3"
        else:
            model = "qwen:7b"
    
    # --- FAILURE ESCALATION ---
    if failure_type in ["invalid_action", "bad_output"]:
        model = "qwen:7b"
        
    print(f"[MODEL ROUTER] complexity={complexity} retry={retry_count} → {model}")
    
    escalated = (model == "qwen:7b" and retry_count > 0)
    reason = f"complexity={complexity} retry={retry_count}"
    
    _routing_stats["total"] += 1
    _routing_stats["by_provider"][model] = _routing_stats["by_provider"].get(model, 0) + 1
    if escalated:
        _routing_stats["escalations"] += 1

    return RoutingDecision(
        provider=model,
        model=model,
        reason=reason,
        escalated=escalated,
        max_tokens=4096 if model == "qwen:7b" else 2048,
        temperature=0.1 if retry_count == 0 else 0.2,
    )


def get_routing_stats() -> dict:
    """Return routing usage statistics."""
    return dict(_routing_stats)