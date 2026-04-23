"""LLM Gateway — centralized intelligence routing layer."""
import dataclasses
import json
import re
import time
import urllib.request
from typing import Optional


@dataclasses.dataclass
class GatewayRequest:
    purpose: str
    prompt: str
    task_type: str = ""
    context: dict = None
    strategy: str = ""
    escalation_level: int = 0
    preferred_provider: str = "auto"
    temperature: float = 0.1
    max_tokens: int = 2048
    retry_count: int = 0
    metadata: dict = None


@dataclasses.dataclass
class GatewayResponse:
    provider: str
    model: str
    success: bool
    confidence: float
    latency_ms: int
    tokens_used: int
    raw_output: str
    text: str
    structured: bool
    parsed: dict
    fallback_used: bool
    escalation_level: int
    error: str


_PROVIDERS = {
    "phi3": {
        "model": "phi3",
        "endpoint": "http://localhost:11434/api/generate",
        "type": "local",
        "strengths": ["fast", "simple", "classification", "lightweight"],
        "max_tokens": 2048,
        "timeout": 15,
    },
    "qwen:7b": {
        "model": "qwen:7b",
        "endpoint": "http://localhost:11434/api/generate",
        "type": "local",
        "strengths": ["reasoning", "planning", "code", "debugging", "structured"],
        "max_tokens": 4096,
        "timeout": 15,
    },
}

_provider_cache = {}
_CACHE_TTL_SECONDS = 30
_MAX_PROMPT_LENGTH = 8000
_usage_stats = {
    "total_requests": 0,
    "total_tokens": 0,
    "total_latency_ms": 0,
    "by_provider": {},
}


def generate(request: GatewayRequest) -> GatewayResponse:
    """Single entry point. Routes, calls, normalizes. Never raises."""
    from . import logger

    request = _apply_guardrails(request)
    provider_name = _select_provider(request)
    logger.log_gateway_start(request.purpose, provider_name)

    if not _check_provider(provider_name):
        alt_provider = "qwen:7b" if provider_name == "phi3" else "phi3"
        if _check_provider(alt_provider):
            logger.log_gateway_fallback(request.purpose, provider_name, alt_provider, "primary unavailable")
            provider_name = alt_provider
        else:
            logger.log_gateway_failure(request.purpose, "no_provider", "All local providers unavailable")
            return _build_unavailable_response("No local providers available")

    provider_config = _PROVIDERS[provider_name]
    try:
        raw_output, latency_ms, tokens_used = _call_ollama(
            model=provider_config["model"],
            prompt=request.prompt,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            timeout=provider_config["timeout"]
        )
    except Exception as e:
        error_msg = str(e)
        logger.log_gateway_failure(request.purpose, provider_name, error_msg)
        return _handle_fallback(request, error_msg, [provider_name])

    if not raw_output or not raw_output.strip():
        logger.log_gateway_failure(request.purpose, provider_name, "empty output")
        return _handle_fallback(request, "empty output", [provider_name])

    text = _normalize_text(raw_output)
    structured, parsed = _parse_response(raw_output)
    confidence = _estimate_confidence(raw_output, structured, request.purpose)

    response = GatewayResponse(
        provider=provider_name,
        model=provider_config["model"],
        success=True,
        confidence=confidence,
        latency_ms=latency_ms,
        tokens_used=tokens_used,
        raw_output=raw_output,
        text=text,
        structured=structured,
        parsed=parsed,
        fallback_used=False,
        escalation_level=request.escalation_level,
        error=""
    )

    _record_usage(provider_name, latency_ms, tokens_used, True)
    logger.log_gateway_success(request.purpose, provider_name, confidence, latency_ms)
    return response


def is_available() -> bool:
    """Quick check: is any local provider reachable?"""
    return _check_provider("phi3") or _check_provider("qwen:7b")


def get_provider_status() -> dict:
    """Returns {provider: available/unavailable} for all registered providers."""
    status = {}
    for name in _PROVIDERS:
        status[name] = "available" if _check_provider(name) else "unavailable"
    return status


def get_usage_stats() -> dict:
    """Return current usage statistics."""
    return dict(_usage_stats)


def _select_provider(request: GatewayRequest) -> str:
    """Select provider. Delegates to model_router, falls back to built-in logic."""
    if request.preferred_provider != "auto":
        if request.preferred_provider in _PROVIDERS:
            return request.preferred_provider

    try:
        from . import model_router, logger
        task_profile = request.metadata.get("task_profile", {}) if request.metadata else {}
        routing = model_router.select_model(
            purpose=request.purpose,
            task_profile=task_profile,
            retry_count=request.retry_count,
            confidence=1.0,
            escalation_level=request.escalation_level,
        )
        if routing.provider in _PROVIDERS:
            logger.log_model_route(request.purpose, routing.provider, routing.reason)
            return routing.provider
    except Exception:
        pass

    return _select_provider_builtin(request)


def _select_provider_builtin(request: GatewayRequest) -> str:
    """Original provider selection logic — kept as fallback."""
    if request.escalation_level >= 1:
        return "qwen:7b"

    purpose = request.purpose

    if purpose in ("planning", "debugging", "reasoning"):
        return "qwen:7b"

    if request.task_type in ("code", "structured"):
        return "qwen:7b"

    if request.strategy in ("fix_syntax", "fix_dependency", "fix_output", "simplify_step"):
        return "qwen:7b"

    if purpose == "decomposition":
        return "qwen:7b"

    if purpose in ("summarization", "fallback", "validation"):
        return "phi3"

    if request.retry_count >= 1:
        return "qwen:7b"

    return "phi3"


def _check_provider(provider_name: str) -> bool:
    """Check if a provider is reachable. Caches result for 30s."""
    config = _PROVIDERS.get(provider_name)
    if not config:
        return False
    if config.get("enabled") is False:
        return False

    if config["type"] == "local":
        now = time.time()
        cached = _provider_cache.get(provider_name)
        if cached and (now - cached["time"]) < _CACHE_TTL_SECONDS:
            return cached["available"]

        try:
            req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                available = resp.status == 200
        except Exception:
            available = False

        _provider_cache[provider_name] = {"available": available, "time": now}
        return available

    return False


def _call_ollama(model: str, prompt: str, temperature: float, max_tokens: int, timeout: int) -> tuple:
    """Raw HTTP call to Ollama."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=data,
        headers={"Content-Type": "application/json"}
    )

    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    latency_ms = int((time.perf_counter() - start) * 1000)

    raw_output = result.get("response", "")
    tokens_used = result.get("eval_count", len(raw_output.split()))

    return raw_output, latency_ms, tokens_used


def _parse_response(raw_output: str) -> tuple:
    """Parse raw text → (structured: bool, parsed: dict)."""
    text = raw_output.strip()

    if text.startswith("```"):
        lines = text.split("```")
        if len(lines) >= 2:
            text = lines[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return True, parsed
        if isinstance(parsed, list):
            return True, {"items": parsed}
    except json.JSONDecodeError:
        pass

    json_match = re.search(r'\{(?:[^{}]|\{[^{}]*\})*\}', raw_output, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            return True, parsed
        except json.JSONDecodeError:
            pass

    arr_match = re.search(r'\[(?:[^\[\]]|\[.*?\])*\]', raw_output, re.DOTALL)
    if arr_match:
        try:
            parsed = json.loads(arr_match.group())
            return True, {"items": parsed}
        except json.JSONDecodeError:
            pass

    return False, {}


def _normalize_text(raw_output: str) -> str:
    """Strip markdown fences, cleanup whitespace."""
    text = raw_output.strip()
    if text.startswith("```"):
        lines = text.split("```")
        if len(lines) >= 2:
            text = lines[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
    return text


def _estimate_confidence(raw_output: str, structured: bool, purpose: str) -> float:
    """Estimate response quality confidence."""
    if not raw_output or not raw_output.strip():
        return 0.0

    base = 0.5
    if structured:
        base += 0.25

    length = len(raw_output.strip())
    if 10 < length < 5000:
        base += 0.1
    elif length < 10:
        base -= 0.2

    if purpose in ("planning", "decomposition") and structured:
        base += 0.1

    return max(0.1, min(0.95, base))


def _apply_guardrails(request: GatewayRequest) -> GatewayRequest:
    """Truncate oversized prompts, set safe defaults."""
    if request.context is None:
        request.context = {}
    if request.metadata is None:
        request.metadata = {}

    if len(request.prompt) > _MAX_PROMPT_LENGTH:
        request.prompt = request.prompt[:_MAX_PROMPT_LENGTH] + "\n[TRUNCATED]"

    request.temperature = max(0.0, min(1.0, request.temperature))
    request.max_tokens = max(64, min(8192, request.max_tokens))

    return request


def _handle_fallback(request: GatewayRequest, error: str, tried: list) -> GatewayResponse:
    """Try alternative providers in order."""
    from . import logger

    fallback_order = ["phi3", "qwen:7b"]

    for alt in fallback_order:
        if alt in tried:
            continue
        if not _check_provider(alt):
            continue

        try:
            config = _PROVIDERS[alt]
            raw_output, latency_ms, tokens_used = _call_ollama(
                model=config["model"],
                prompt=request.prompt,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                timeout=config["timeout"]
            )

            if raw_output and raw_output.strip():
                text = _normalize_text(raw_output)
                structured, parsed = _parse_response(raw_output)
                confidence = _estimate_confidence(raw_output, structured, request.purpose)
                logger.log_gateway_fallback(request.purpose, tried[-1], alt, "primary failed")
                _record_usage(alt, latency_ms, tokens_used, True)
                return GatewayResponse(
                    provider=alt, model=config["model"], success=True,
                    confidence=max(0.3, confidence - 0.1),
                    latency_ms=latency_ms, tokens_used=tokens_used,
                    raw_output=raw_output, text=text,
                    structured=structured, parsed=parsed,
                    fallback_used=True, escalation_level=request.escalation_level,
                    error=""
                )
        except Exception:
            tried.append(alt)
            continue

    logger.log_gateway_failure(request.purpose, "all", "All providers failed")
    return _build_unavailable_response(f"All providers failed. Last error: {error}")


def _record_usage(provider: str, latency_ms: int, tokens: int, success: bool) -> None:
    """Track usage metrics in memory."""
    _usage_stats["total_requests"] += 1
    _usage_stats["total_tokens"] += tokens
    _usage_stats["total_latency_ms"] += latency_ms

    if provider not in _usage_stats["by_provider"]:
        _usage_stats["by_provider"][provider] = {"requests": 0, "tokens": 0, "failures": 0}
    _usage_stats["by_provider"][provider]["requests"] += 1
    _usage_stats["by_provider"][provider]["tokens"] += tokens
    if not success:
        _usage_stats["by_provider"][provider]["failures"] += 1


def _build_unavailable_response(error: str) -> GatewayResponse:
    """Controlled failure when no provider is available."""
    return GatewayResponse(
        provider="unavailable", model="none",
        success=False, confidence=0.0,
        latency_ms=0, tokens_used=0,
        raw_output="", text="",
        structured=False, parsed={},
        fallback_used=True, escalation_level=0,
        error=error
    )