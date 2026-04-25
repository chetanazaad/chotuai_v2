"""Model Router — complexity-aware model selection."""
import dataclasses
import os
from typing import Optional

# --- SAFE MODE OVERRIDE ---
# Set to True for low-VRAM systems that cannot handle qwen:7b reliably
# Set to False to restore original intelligent routing
SAFE_MODE = False


def _get_forced_model() -> Optional[str]:
    """Check for forced model from environment or config."""
    forced = os.environ.get("CHOTU_FORCED_MODEL", "")
    if forced in ("phi3", "qwen:7b", "gemini"):
        return forced
    return None


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

    ("high", "first"): {"provider": "gemini", "max_tokens": 4096, "temp": 0.1},
    ("high", "retry"): {"provider": "gemini", "max_tokens": 8192, "temp": 0.15},
    ("high", "escalate"): {"provider": "gemini", "max_tokens": 8192, "temp": 0.2},
}

_routing_stats = {"total": 0, "by_provider": {}, "escalations": 0}


def _get_cloud_config() -> dict:
    """Get config from llm_gateway."""
    try:
        from . import llm_gateway
        return llm_gateway._get_config()
    except Exception:
        return {"use_cloud": False, "cloud_provider": "gemini", "api_key": ""}


def select_model(purpose: str = "",
               task_profile: dict = None,
               retry_count: int = 0,
               confidence: float = 1.0,
               escalation_level: int = 0,
               failure_type: str = None,
               forced_model: str = None) -> RoutingDecision:
    """Select the best model based on task complexity, confidence, and retry state."""
    task_profile = task_profile or {}
    complexity = task_profile.get("complexity", "medium")
    task_type = task_profile.get("task_type", "").lower()
    domain = task_profile.get("domain", "").lower()

    cloud_cfg = _get_cloud_config()
    use_cloud = cloud_cfg.get("use_cloud", False) and cloud_cfg.get("api_key", "")
    cloud_provider = cloud_cfg.get("cloud_provider", "gemini")

    # --- FORCED MODEL OVERRIDE (FIX 5) ---
    forced = forced_model or _get_forced_model()
    if forced:
        print(f"[MODEL LOCKED] {forced} (reason: forced)")
        return RoutingDecision(
            provider=forced,
            model=forced,
            reason=f"forced_model={forced}",
            escalated=False,
            max_tokens=4096 if forced == "qwen:7b" else 2048,
            temperature=0.1,
        )

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

    # --- SMART MODEL ROUTING (FIX 1) ---
    # Detect coding tasks: build, code, website, html, css, js, script, app, system, calculator
    task_desc = task_profile.get("description", "").lower()
    is_coding_task = (
        task_type in ("build", "code", "script") or
        any(kw in domain for kw in ("website", "web", "html", "css", "js", "app", "system")) or
        any(kw in task_desc for kw in ("build", "create", "app", "system", "calculator", "web", "html", "js", "css"))
    )

    # --- HYBRID ROUTING LOGIC ---
    model = "phi3"

    # --- CODING TASK FIRST ---
    if is_coding_task:
        model = "qwen:7b"
        print(f"[MODEL ROUTER] selected qwen (reason: coding task)")
    # --- LOW COMPLEXITY ---
    elif complexity == "low":
        model = "phi3"

    # --- MEDIUM COMPLEXITY ---
    elif complexity == "medium":
        if retry_count == 0:
            model = "phi3"
        else:
            model = "qwen:7b"

    # --- HIGH COMPLEXITY ---
    elif complexity == "high":
        if use_cloud and retry_count >= 1:
            model = cloud_provider
        elif retry_count == 0:
            model = "phi3"
        else:
            model = "qwen:7b"

    # --- FAILURE ESCALATION ---
    if failure_type in ["invalid_action", "bad_output"]:
        if use_cloud:
            model = cloud_provider
        else:
            model = "qwen:7b"

    if not is_coding_task:
        print(f"[MODEL ROUTER] complexity={complexity} retry={retry_count} → {model}")

    escalated = (model != "phi3")
    reason = f"complexity={complexity} retry={retry_count} cloud={use_cloud}" + (", coding" if is_coding_task else "")

    _routing_stats["total"] += 1
    _routing_stats["by_provider"][model] = _routing_stats["by_provider"].get(model, 0) + 1
    if escalated:
        _routing_stats["escalations"] += 1

    max_tokens = 4096 if model == "qwen:7b" else (8192 if model == "gemini" else 2048)

    return RoutingDecision(
        provider=model,
        model=model,
        reason=reason,
        escalated=escalated,
        max_tokens=max_tokens,
        temperature=0.1 if retry_count == 0 else 0.2,
    )


def get_routing_stats() -> dict:
    """Return routing usage statistics."""
    return dict(_routing_stats)