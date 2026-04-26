"""LLM Gateway — centralized intelligence routing layer."""
import dataclasses
import json
import re
import subprocess
import sys
import time
import urllib.request
from typing import Optional, Dict, Any, Tuple


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


# Centralized Provider Configuration
_PROVIDERS = {
    "phi3": {
        "model": "phi3",
        "endpoint": "http://localhost:11434/api/generate",
        "type": "local",
        "strengths": ["fast", "simple", "classification", "lightweight"],
        "max_tokens": 2048,
        "timeout": 60,  # Increased for slow CPUs
    },
    "qwen:7b": {
        "model": "qwen:7b",
        "endpoint": "http://localhost:11434/api/generate",
        "type": "local",
        "strengths": ["reasoning", "planning", "code", "debugging", "structured"],
        "max_tokens": 4096,
        "timeout": 120,  # Increased significantly for slow CPUs
    },
}

_OLLAMA_PORT = 11434
_OLLAMA_URL = f"http://localhost:{_OLLAMA_PORT}"

# Global State
_provider_cache = {}
_CACHE_TTL_SECONDS = 30
_model_load_cache = {}
_MODEL_LOAD_TTL = 60
_consecutive_timeouts = 0
_usage_stats = {
    "total_requests": 0,
    "total_tokens": 0,
    "total_latency_ms": 0,
    "by_provider": {},
}


def _ensure_ollama_running() -> bool:
    """Ensures Ollama server is running. Auto-starts if needed."""
    try:
        req = urllib.request.Request(f"{_OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass

    print("[LLM] Ollama not running. Starting...")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for i in range(6):
            time.sleep(1)
            if check_ollama_health():
                print(f"[LLM] Ollama started after {i+1}s")
                return True
        return False
    except Exception as e:
        print(f"[LLM] Failed to start Ollama: {e}")
        return False


def _ensure_model_loaded(model_name: str) -> bool:
    """Ensures requested model is loaded in memory. Loads if needed."""
    global _model_load_cache
    now = time.time()
    
    if _model_load_cache.get(model_name) and (now - _model_load_cache[model_name]) < _MODEL_LOAD_TTL:
        return True
    
    try:
        result = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5)
        if model_name in result.stdout:
            _model_load_cache[model_name] = now
            return True
    except Exception:
        pass

    print(f"[LLM] Loading model: {model_name}...")
    try:
        subprocess.run(["ollama", "run", model_name, "--keepalive", "5m"], timeout=5, stdout=subprocess.DEVNULL)
        _model_load_cache[model_name] = now
        return True
    except Exception:
        return False


def check_ollama_health() -> bool:
    """Quick health check for Ollama."""
    try:
        req = urllib.request.Request(f"{_OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def is_available() -> bool:
    """Quick check: is any local provider reachable?"""
    return check_ollama_health()


def generate(request: GatewayRequest) -> GatewayResponse:
    """Single entry point. Routes, calls, normalizes."""
    from . import logger, llm_cache

    if not _ensure_ollama_running():
        logger.log_gateway_failure(request.purpose, "ollama", "Ollama unavailable")
        return _build_unavailable_response("Ollama server unavailable")

    request = _apply_guardrails(request)
    provider_name = _select_provider(request)
    provider_config = _PROVIDERS[provider_name]
    
    # Caching Layer
    use_cache = request.retry_count == 0
    if use_cache:
        cached = llm_cache.get_cached(request.prompt)
        if cached:
            print(f"[LLM CACHE HIT]")
            return _build_response_from_raw(provider_name, provider_config["model"], cached, request.purpose, 0, 0, False)

    # Provider Availability & Fallback
    if not _check_provider(provider_name):
        return _handle_fallback(request, "Primary provider unavailable", [provider_name])

    # Model Loading
    _ensure_model_loaded(provider_config["model"])

    # Execution
    try:
        logger.log_gateway_start(request.purpose, provider_name)
        raw_output, latency_ms, tokens_used = _call_ollama(
            model=provider_config["model"],
            prompt=request.prompt,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            timeout=provider_config["timeout"]
        )
        
        response = _build_response_from_raw(
            provider_name, provider_config["model"], raw_output, 
            request.purpose, latency_ms, tokens_used, False
        )
        
        _record_usage(provider_name, latency_ms, tokens_used, True)
        logger.log_gateway_success(request.purpose, provider_name, response.confidence, latency_ms)
        llm_cache.set_cached(request.prompt, raw_output, provider_config["model"], tokens_used)
        return response

    except Exception as e:
        print(f"[LLM] Error with {provider_name}: {e}")
        return _handle_fallback(request, str(e), [provider_name])


def _handle_fallback(request: GatewayRequest, error: str, tried: list) -> GatewayResponse:
    """Try alternative providers in order."""
    from . import logger

    fallback_order = ["qwen:7b", "phi3"]
    for alt in fallback_order:
        if alt in tried or not _check_provider(alt):
            continue

        print(f"[LLM] Falling back to {alt}...")
        try:
            config = _PROVIDERS[alt]
            _ensure_model_loaded(config["model"])
            raw_output, latency_ms, tokens_used = _call_ollama(
                model=config["model"],
                prompt=request.prompt,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                timeout=config["timeout"]
            )
            
            response = _build_response_from_raw(
                alt, config["model"], raw_output, 
                request.purpose, latency_ms, tokens_used, True
            )
            
            logger.log_gateway_fallback(request.purpose, tried[-1], alt, error)
            _record_usage(alt, latency_ms, tokens_used, True)
            return response
        except Exception:
            tried.append(alt)
            continue

    logger.log_gateway_failure(request.purpose, "all", "All providers failed")
    return _build_unavailable_response(f"All providers failed. Last error: {error}")


def _call_ollama(model: str, prompt: str, temperature: float, max_tokens: int, timeout: int) -> Tuple[str, int, int]:
    """Raw HTTP call to Ollama."""
    # Map max_tokens to Ollama's num_predict
    options = {"temperature": temperature, "num_predict": max_tokens}
    payload = {"model": model, "prompt": prompt, "stream": False, "options": options}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{_OLLAMA_URL}/api/generate", data=data, headers={"Content-Type": "application/json"})

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        if "timeout" in str(e).lower():
            raise TimeoutError(f"Ollama timeout after {timeout}s")
        raise

    latency_ms = int((time.perf_counter() - start) * 1000)
    raw_output = result.get("response", "")
    if not raw_output.strip():
        raise ValueError("Empty response from Ollama")

    tokens_used = result.get("eval_count", len(raw_output.split()))
    return raw_output, latency_ms, tokens_used


def _build_response_from_raw(provider: str, model: str, raw: str, purpose: str, latency: int, tokens: int, fallback: bool) -> GatewayResponse:
    """Helper to construct GatewayResponse with parsing and confidence."""
    text = _normalize_text(raw)
    structured, parsed = _parse_response(raw)
    confidence = _estimate_confidence(raw, structured, purpose)
    
    return GatewayResponse(
        provider=provider, model=model, success=True, confidence=confidence,
        latency_ms=latency, tokens_used=tokens, raw_output=raw, text=text,
        structured=structured, parsed=parsed, fallback_used=fallback,
        escalation_level=0, error=""
    )


def _select_provider(request: GatewayRequest) -> str:
    """Selection logic with environment overrides."""
    import os
    env_model = os.environ.get("CHOTU_MODEL")
    if env_model in _PROVIDERS:
        return env_model

    if request.preferred_provider in _PROVIDERS:
        return request.preferred_provider

    if request.purpose in ("planning", "debugging", "reasoning", "decomposition"):
        return "qwen:7b"
    
    if request.retry_count > 0:
        return "qwen:7b"

    return "phi3"


def _check_provider(provider_name: str) -> bool:
    """Check if a provider is reachable (cached for 30s)."""
    now = time.time()
    if provider_name in _provider_cache and (now - _provider_cache[provider_name]["time"]) < _CACHE_TTL_SECONDS:
        return _provider_cache[provider_name]["available"]

    available = check_ollama_health()
    _provider_cache[provider_name] = {"available": available, "time": now}
    return available


def _apply_guardrails(request: GatewayRequest) -> GatewayRequest:
    """Cleanup and truncate prompts."""
    request.prompt = re.sub(r'\n\s*\n', '\n', request.prompt)
    request.prompt = re.sub(r'  +', ' ', request.prompt)
    
    # Global Language Guardrail (Critical for Qwen)
    if "USE ONLY ENGLISH" not in request.prompt.upper():
         request.prompt = "IMPORTANT: USE ONLY ENGLISH FOR ALL OUTPUTS. No non-English characters.\n" + request.prompt

    if len(request.prompt) > 6000:
        request.prompt = request.prompt[:6000] + "\n[TRUNCATED]"

    request.temperature = 0.2
    return request


def _normalize_text(raw_output: str) -> str:
    """Strip markdown fences."""
    text = raw_output
    text = re.sub(r'^```json\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^```\w+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^```\s*$', '', text, flags=re.MULTILINE)
    return text.replace("```json", "").replace("```", "").strip()


def _parse_response(raw_output: str) -> Tuple[bool, dict]:
    """Parse JSON from response."""
    text = _normalize_text(raw_output)
    try:
        # Try direct parse
        return True, json.loads(text)
    except json.JSONDecodeError:
        # Try block extraction
        match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        if match:
            try:
                return True, json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    return False, {}


def _estimate_confidence(raw: str, structured: bool, purpose: str) -> float:
    """Heuristic for response quality."""
    if not raw.strip(): return 0.0
    score = 0.5
    if structured: score += 0.3
    if 50 < len(raw) < 5000: score += 0.1
    return min(0.95, score)


def _record_usage(provider: str, latency: int, tokens: int, success: bool):
    """Update global stats."""
    _usage_stats["total_requests"] += 1
    _usage_stats["total_tokens"] += tokens
    _usage_stats["total_latency_ms"] += latency
    if provider not in _usage_stats["by_provider"]:
        _usage_stats["by_provider"][provider] = {"requests": 0, "tokens": 0, "failures": 0}
    _usage_stats["by_provider"][provider]["requests"] += 1
    if not success: _usage_stats["by_provider"][provider]["failures"] += 1


def _build_unavailable_response(error: str) -> GatewayResponse:
    """Standard failure object."""
    return GatewayResponse(
        provider="unavailable", model="none", success=False, confidence=0.0,
        latency_ms=0, tokens_used=0, raw_output="", text="",
        structured=False, parsed={}, fallback_used=True, escalation_level=0, error=error
    )