"""LLM Gateway — centralized intelligence routing layer."""
import dataclasses
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
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
    use_cache: bool = True


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
        "timeout": 60,
    },
    "qwen:7b": {
        "model": "qwen:7b",
        "endpoint": "http://localhost:11434/api/generate",
        "type": "local",
        "strengths": ["reasoning", "planning", "code", "debugging", "structured"],
        "max_tokens": 4096,
        "timeout": 15,
    },
    "gemini": {
        "model": "gemini-2.0-flash",
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
        "type": "cloud",
        "strengths": ["reasoning", "architecture", "complex", "high_quality"],
        "max_tokens": 8192,
        "timeout": 30,
    },
}

_CONFIG = {
    "use_cloud": False,
    "cloud_provider": "gemini",
    "api_key": "",
    "fallback_enabled": True,
}

def _load_config() -> dict:
    """Load configuration from .chotu/config.json."""
    import os
    config_path = Path(".chotu/config.json")
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                _CONFIG.update(loaded)
                print(f"[CONFIG] Loaded: use_cloud={_CONFIG['use_cloud']}, cloud={_CONFIG['cloud_provider']}")
        except Exception as e:
            print(f"[CONFIG] Failed to load: {e}")
    return _CONFIG


def _get_config() -> dict:
    """Get current config, loading if needed."""
    if not _CONFIG.get("_loaded"):
        _CONFIG["_loaded"] = True
        _load_config()
    return _CONFIG


_OLLAMA_PORT = 11434
_OLLAMA_URL = f"http://localhost:{_OLLAMA_PORT}"


def _ensure_ollama_running() -> bool:
    """Ensures Ollama server is running. Auto-starts if needed."""
    try:
        req = urllib.request.Request(f"{_OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                print("[LLM] Ollama is running")
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
            try:
                req = urllib.request.Request(f"{_OLLAMA_URL}/api/tags", method="GET")
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        print(f"[LLM] Ollama started after {i+1}s")
                        return True
            except Exception:
                continue
        print("[LLM] Failed to start Ollama")
        return False
    except Exception as e:
        print(f"[LLM] Failed to start Ollama: {e}")
        return False


def _ensure_model_loaded(model_name: str) -> bool:
    """Ensures requested model is loaded in memory. Loads if needed."""
    global _model_load_cache
    now = time.time()
    
    cached = _model_load_cache.get(model_name)
    if cached and (now - cached) < _MODEL_LOAD_TTL:
        print(f"[LLM] Model {model_name} already loaded (cached)")
        return True
    
    try:
        result = subprocess.run(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if model_name in result.stdout:
            print(f"[LLM] Model {model_name} is loaded")
            _model_load_cache[model_name] = now
            return True
    except Exception:
        pass

    print(f"[LLM] Loading model: {model_name}...")

    try:
        proc = subprocess.Popen(
            ["ollama", "run", model_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for i in range(10):
            time.sleep(1)
            try:
                result = subprocess.run(
                    ["ollama", "ps"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if model_name in result.stdout:
                    print(f"[LLM] Model {model_name} loaded after {i+1}s")
                    proc.terminate()
                    return True
            except Exception:
                continue
        proc.terminate()
        print(f"[LLM] Failed to load model {model_name}")
        return False
    except Exception as e:
        print(f"[LLM] Failed to load model: {e}")
        return False


def test_llm():
    """Test LLM connectivity."""
    print("Testing direct call:")
    print(_call_ollama("phi3", "Say hello", 0.1, 100, 15))

_provider_cache = {}
_CACHE_TTL_SECONDS = 30
_MAX_PROMPT_LENGTH = 8000
MAX_LLM_TIME = 5
_TIMEOUT_MAP = {"phi3": 5, "qwen:7b": 10}
_consecutive_timeouts = 0

_model_load_cache = {}
_MODEL_LOAD_TTL = 60


_usage_stats = {
    "total_requests": 0,
    "total_tokens": 0,
    "total_latency_ms": 0,
    "by_provider": {},
}


def check_ollama_health() -> bool:
    """Quick health check for Ollama."""
    try:
        req = urllib.request.Request(f"{_OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        print("[INFRA] Ollama health check failed")
        return False


def check_llm_status() -> dict:
    """Full LLM status check with detailed diagnostics."""
    import subprocess
    
    status = {
        "ollama_running": False,
        "api_reachable": False,
        "phi3_available": False,
        "qwen_available": False,
        "phi3_loaded": False,
        "qwen_loaded": False,
        "llm_working": False,
    }
    
    print("\n[LLM STATUS CHECK]")
    
    try:
        req = urllib.request.Request(f"{_OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                status["ollama_running"] = True
                status["api_reachable"] = True
                print("  [X] Ollama running")
    except Exception as e:
        print("  [_] Ollama not running:", str(e)[:30])
        return status
    
    try:
        result = subprocess.check_output(["ollama", "list"], text=True, timeout=5)
        if "phi3" in result:
            status["phi3_available"] = True
            print("  [X] phi3 installed")
        if "qwen" in result:
            status["qwen_available"] = True
            print("  [X] qwen:7b installed")
    except Exception as e:
        print("  [_] Could not list models:", str(e)[:30])
    
    try:
        result = subprocess.check_output(["ollama", "ps"], text=True, timeout=5)
        if "phi3" in result:
            status["phi3_loaded"] = True
            print("  [X] phi3 loaded")
        if "qwen" in result:
            status["qwen_loaded"] = True
            print("  [X] qwen:7b loaded")
    except Exception:
        pass
    
    if not status["phi3_loaded"] and not status["qwen_loaded"]:
        print("  [!] No models currently loaded")
    
    if status["api_reachable"]:
        try:
            req = urllib.request.Request(
                _OLLAMA_URL + "/api/generate",
                data=b'{"model": "phi3", "prompt": "hi", "stream": false}',
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    status["llm_working"] = True
                    print("  [X] LLM responding")
        except Exception as e:
            print("  [_] LLM not responding:", str(e)[:30])
    
    if status["llm_working"]:
        print("  [=] LLM READY")
    else:
        print("  [=] LLM NOT READY")
    
    return status


def _get_timeout_for_model(model: str) -> int:
    """Get timeout with overload protection."""
    global _consecutive_timeouts
    if _consecutive_timeouts >= 2:
        print("[INFRA] Overload detected, using phi3 fallback")
        return 20
    return _TIMEOUT_MAP.get(model, 15)


def _call_provider(provider_name: str, model: str, prompt: str, temp: float, max_tok: int, timeout: int) -> tuple:
    """Route to appropriate LLM call based on provider type."""
    config = _PROVIDERS.get(provider_name)
    if not config:
        raise ValueError(f"Unknown provider: {provider_name}")

    if config["type"] == "local":
        return _call_ollama(model, prompt, temp, max_tok, timeout)
    elif config["type"] == "cloud":
        return _call_cloud(provider_name, model, prompt, temp, max_tok, timeout)
    else:
        raise ValueError(f"Unknown provider type: {config['type']}")


def safe_llm_call(model: str, prompt: str, temp: float, max_tok: int, timeout: int = None) -> tuple:
    """Safe LLM call with timeout and error handling."""
    global _consecutive_timeouts

    if timeout is None:
        timeout = _get_timeout_for_model(model)

    provider_type = "local"
    for name, cfg in _PROVIDERS.items():
        if cfg.get("model") == model:
            provider_type = cfg.get("type", "local")
            model = name
            break

    try:
        result = _call_provider(model, model, prompt, temp, max_tok, timeout)
        _consecutive_timeouts = 0
        return result
    except TimeoutError:
        _consecutive_timeouts += 1
        print(f"[INFRA] LLM timeout for {model}")
        raise
    except Exception as e:
        _consecutive_timeouts += 1
        print(f"[INFRA] LLM crash: {e}")
        raise


def _get_cloud_fallback_provider() -> str:
    """Get available cloud provider for failover."""
    cfg = _get_config()
    if cfg.get("use_cloud") and cfg.get("api_key"):
        return cfg.get("cloud_provider", "gemini")
    return None


def _get_local_fallback_provider() -> str:
    """Get available local provider for failover."""
    if _check_provider("phi3"):
        return "phi3"
    if _check_provider("qwen:7b"):
        return "qwen:7b"
    return None


def generate(request: GatewayRequest) -> GatewayResponse:
    """Single entry point. Routes, calls, normalizes. Never blocks."""
    from . import logger

    cfg = _get_config()
    print(f"[LLM] attempt start (cloud_enabled={cfg.get('use_cloud')})")

    provider_name = _select_provider(request)
    logger.log_gateway_start(request.purpose, provider_name)
    print(f"[MODEL ROUTER] → selected {provider_name}")

    request = _apply_guardrails(request)

    provider_config = _PROVIDERS[provider_name]

    if provider_config["type"] == "local":
        if not _ensure_ollama_running():
            fallbacks = _try_fallback(request, provider_name, "ollama_unavailable")
            if fallbacks:
                return fallbacks
            logger.log_gateway_failure(request.purpose, "ollama", "Failed to start Ollama")
            return _build_unavailable_response("Ollama server unavailable")
        _ensure_model_loaded(provider_config["model"])

    from . import llm_cache
    cached = None
    if request.use_cache:
        cached = llm_cache.get_cached(request.prompt)
    else:
        print("[LLM RETRY] cache bypassed")

    if cached:
        from . import logger
        print(f"[LLM CACHE HIT]")
        text = cached
        structured, parsed = _parse_response(cached)
        confidence = 0.9
        response = GatewayResponse(
            provider=provider_name,
            model=provider_config["model"],
            success=True,
            confidence=confidence,
            latency_ms=0,
            tokens_used=len(cached.split()),
            raw_output=cached,
            text=cached,
            structured=structured,
            parsed=parsed,
            fallback_used=False,
            escalation_level=request.escalation_level,
            error=""
        )
        return response

    logger.log_gateway_start(request.purpose, provider_name)
    print(f"[LLM] using {provider_config['type']}: {provider_config['model']}")

    if not _check_provider(provider_name):
        fallbacks = _try_fallback(request, provider_name, "provider_unavailable")
        if fallbacks:
            return fallbacks

    provider_config = _PROVIDERS[provider_name]
    try:
        # FIX 1: Enforce hard 10s timeout for all LLM calls
        hard_timeout = 10
        print(f"[LLM CALL START] model={provider_config['model']} purpose={request.purpose}")
        raw_output, latency_ms, tokens_used = _call_provider(
            provider_name=provider_name,
            model=provider_config["model"],
            prompt=request.prompt,
            temp=request.temperature,
            max_tok=request.max_tokens,
            timeout=hard_timeout
        )
        print(f"[LLM CALL END] model={provider_config['model']} latency={latency_ms}ms")

        # FIX 2: Validate response quality - force fallback on bad output
        if not raw_output or len(raw_output.strip()) < 50:
            print(f"[LLM] bad_output: response too short ({len(raw_output)} chars)")
            raise ValueError("bad_output: response too short")

        text = _normalize_text(raw_output)
        structured, parsed = _parse_response(raw_output)
        if request.purpose in ("planning", "code_action") and not structured:
            print(f"[LLM] bad_output: no structured output for {request.purpose}")
            raise ValueError("bad_output: no structured output")

    except (ValueError, Exception) as e:
        print(f"[LLM CALL END] FAILED: {str(e)[:50]}")
        error_msg = str(e)
        # Handle ValueError("bad_output") - this triggers fallback attempt
        if "bad_output" in error_msg.lower():
            print(f"[MODEL SWITCH] {provider_name} -> fallback (reason: bad_output)")
        elif "timeout" in error_msg.lower() or isinstance(e, TimeoutError):
            print(f"[LLM TIMEOUT] model={provider_config['model']} after {hard_timeout}s")
            error_msg = f"[LLM ERROR] timeout"
        else:
            print(f"[LLM] {provider_name} failed: {error_msg}")

        print(f"[RETRY REASON] {error_msg}")
        fallbacks = _try_fallback(request, provider_name, error_msg)
        if fallbacks:
            return fallbacks
        
        return GatewayResponse(
            provider=provider_name,
            model=provider_config["model"],
            success=False,
            confidence=0.0,
            latency_ms=0,
            tokens_used=0,
            raw_output="",
            text="",
            structured=False,
            parsed={},
            fallback_used=False,
            escalation_level=request.escalation_level,
            error=error_msg
        )
        
        logger.log_gateway_failure(request.purpose, provider_name, error_msg)
        print(f"[LLM] All providers failed")
        return _build_unavailable_response(f"LLM error: {error_msg}")

    text = _normalize_text(raw_output)
    structured, parsed = _parse_response(raw_output)
    
    # FIX: STRICT RETRY LOGIC (LLM level)
    # Retry ONLY if: LLM timeout, no response (empty)
    is_bad = False
    fail_reason = ""
    
    if not raw_output or not raw_output.strip():
        is_bad = True
        fail_reason = "empty_response"
            
    if is_bad:
        print(f"[RETRY REASON] {fail_reason}")
        print(f"[LLM] Bad output detected ({fail_reason}) → triggering fallback")
        fallbacks = _try_fallback(request, provider_name, fail_reason)
        if fallbacks:
            return fallbacks
        return GatewayResponse(
            provider=provider_name,
            model=provider_config["model"],
            success=False,
            confidence=0.0,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            raw_output=raw_output,
            text=text,
            structured=structured,
            parsed=parsed,
            fallback_used=False,
            escalation_level=request.escalation_level,
            error=f"bad_output:{fail_reason}"
        )

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

    # Only cache if it was good
    llm_cache.set_cached(request.prompt, raw_output, provider_config["model"], tokens_used)

    return response


def _try_fallback(request: GatewayRequest, failed_provider: str, reason: str) -> Optional[GatewayResponse]:
    """Try cross-type failover when primary provider fails."""
    from . import logger
    cfg = _get_config()
    if not cfg.get("fallback_enabled"):
        return None

    failed_config = _PROVIDERS.get(failed_provider)
    if not failed_config:
        return None

    failed_type = failed_config.get("type", "local")

    if failed_type == "local" and cfg.get("use_cloud") and cfg.get("api_key"):
        cloud_provider = cfg.get("cloud_provider", "gemini")
        if _check_provider(cloud_provider):
            print(f"[FAILOVER] → switching to {cloud_provider}")
            logger.log_gateway_fallback(request.purpose, failed_provider, cloud_provider, reason)
            try:
                cloud_config = _PROVIDERS[cloud_provider]
                raw_output, latency_ms, tokens_used = _call_provider(
                    provider_name=cloud_provider,
                    model=cloud_config["model"],
                    prompt=request.prompt,
                    temp=request.temperature,
                    max_tok=request.max_tokens,
                    timeout=cloud_config["timeout"]
                )
                text = _normalize_text(raw_output)
                structured, parsed = _parse_response(raw_output)
                confidence = _estimate_confidence(raw_output, structured, request.purpose)
                _record_usage(cloud_provider, latency_ms, tokens_used, True)
                return GatewayResponse(
                    provider=cloud_provider,
                    model=cloud_config["model"],
                    success=True,
                    confidence=max(0.3, confidence - 0.1),
                    latency_ms=latency_ms,
                    tokens_used=tokens_used,
                    raw_output=raw_output,
                    text=text,
                    structured=structured,
                    parsed=parsed,
                    fallback_used=True,
                    escalation_level=request.escalation_level,
                    error=""
                )
            except Exception:
                pass

    if failed_type == "cloud":
        local_provider = _get_local_fallback_provider()
        if local_provider:
            print(f"[FAILOVER] → switching to {local_provider}")
            logger.log_gateway_fallback(request.purpose, failed_provider, local_provider, reason)
            if not _ensure_ollama_running():
                return None
            _ensure_model_loaded(_PROVIDERS[local_provider]["model"])
            try:
                local_config = _PROVIDERS[local_provider]
                raw_output, latency_ms, tokens_used = _call_provider(
                    provider_name=local_provider,
                    model=local_config["model"],
                    prompt=request.prompt,
                    temp=request.temperature,
                    max_tok=request.max_tokens,
                    timeout=local_config["timeout"]
                )
                text = _normalize_text(raw_output)
                structured, parsed = _parse_response(raw_output)
                confidence = _estimate_confidence(raw_output, structured, request.purpose)
                _record_usage(local_provider, latency_ms, tokens_used, True)
                return GatewayResponse(
                    provider=local_provider,
                    model=local_config["model"],
                    success=True,
                    confidence=max(0.3, confidence - 0.1),
                    latency_ms=latency_ms,
                    tokens_used=tokens_used,
                    raw_output=raw_output,
                    text=text,
                    structured=structured,
                    parsed=parsed,
                    fallback_used=True,
                    escalation_level=request.escalation_level,
                    error=""
                )
            except Exception:
                pass

    return None


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

    if config["type"] == "cloud":
        cfg = _get_config()
        if cfg.get("use_cloud") and cfg.get("api_key"):
            return True
        return False

    return False


def _call_ollama(model: str, prompt: str, temperature: float, max_tokens: int, timeout: int) -> tuple:
    """Raw HTTP call to Ollama with timeout handling."""
    print("[LLM] Sending request to Ollama...")
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=data,
        headers={"Content-Type": "application/json"}
    )

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            response_text = resp.read().decode("utf-8")
            print("[LLM] Raw response:", response_text[:100])
            result = json.loads(response_text)
    except Exception as e:
        print(f"[INFRA] LLM call failed: {e}")
        if "timeout" in str(e).lower():
            raise TimeoutError(str(e))
        raise

    latency_ms = int((time.perf_counter() - start) * 1000)

    raw_output = result["response"]
    if not raw_output or not raw_output.strip():
        print("[INFRA] Empty LLM response")
        raise ValueError("Empty output")

    tokens_used = result.get("eval_count", len(raw_output.split()))

    return raw_output, latency_ms, tokens_used


def _call_cloud(provider_name: str, model: str, prompt: str, temperature: float, max_tokens: int, timeout: int) -> tuple:
    """Raw HTTP call to cloud LLM provider."""
    config = _PROVIDERS.get(provider_name)
    if not config:
        raise ValueError(f"Unknown provider: {provider_name}")

    cfg = _get_config()
    api_key = cfg.get("api_key", "")
    if not api_key:
        raise ValueError(f"No API key configured for {provider_name}")

    print(f"[LLM] Calling cloud provider: {provider_name}...")
    endpoint = config["endpoint"]
    if "gemini" in provider_name:
        endpoint += f"?key={api_key}"

    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json"
    }
    req = urllib.request.Request(endpoint, data=data, headers=headers)

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            response_text = resp.read().decode("utf-8")
            result = json.loads(response_text)
    except Exception as e:
        print(f"[INFRA] Cloud call failed: {e}")
        raise

    latency_ms = int((time.perf_counter() - start) * 1000)

    try:
        raw_output = result["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raw_output = str(result)

    if not raw_output or not raw_output.strip():
        print("[INFRA] Empty cloud response")
        raise ValueError("Empty output")

    tokens_used = len(raw_output.split())
    return raw_output, latency_ms, tokens_used


def _parse_response(raw_output: str) -> tuple:
    """Parse raw text -> (structured: bool, parsed: dict).
    
    FIX 1: Fallback parsing for non-JSON responses.
    If valid JSON not found, extract code blocks and assume file_write.
    """
    parsed = extract_json(raw_output)
    if parsed:
        if isinstance(parsed, dict):
            return True, parsed
        if isinstance(parsed, list):
            return True, {"items": parsed}

    # Fallback Parsing (FIX 1)
    if "```" in raw_output:
        # Extract content from first code block
        parts = raw_output.split("```")
        if len(parts) >= 3:
            content = parts[1]
            # Strip potential language tag
            lines = content.split("\n")
            if lines and not lines[0].startswith(("{", "[")):
                # Probably a language tag like "html" or "python"
                content = "\n".join(lines[1:]).strip()
            
            print(f"[LLM PARSER] Non-JSON detected -> Fallback: file_write (index.html)")
            return True, {
                "action": {
                    "type": "file_write",
                    "path": "index.html",
                    "content": content
                }
            }

    return False, {}


def extract_json(raw_output: str) -> Optional[dict]:
    """Strict JSON extraction from any LLM response."""
    text = _normalize_text(raw_output)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find('{')
    if start == -1:
        start = text.find('[')
    if start == -1:
        return None

    bracket_count = 0
    for i, char in enumerate(text[start:], start):
        if char in '{[':
            bracket_count += 1
        elif char in ']}':
            bracket_count -= 1
            if bracket_count == 0:
                try:
                    return json.loads(text[start:i+1])
                except json.JSONDecodeError:
                    pass

    return None


def _normalize_text(raw_output: str) -> str:
    """Strip markdown fences, labels, and cleanup whitespace."""
    text = raw_output
    text = re.sub(r'^```json\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^```\w+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^```\s*$', '', text, flags=re.MULTILINE)
    text = text.replace("```json", "").replace("```", "").strip()
    text = re.sub(r'^json\s*', '', text, flags=re.MULTILINE | re.IGNORECASE)
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


def validate_action(action: dict) -> tuple[bool, str]:
    """Validate action for safety and Windows compatibility."""
    valid, err = validate_action_schema(action)
    if not valid:
        return valid, err
    return validate_python_content(action)


def validate_action_schema(action: dict) -> tuple[bool, str]:
    """Strict schema validation - checks type and required fields."""
    if not isinstance(action, dict):
        return False, "not_dict"

    action_type = action.get("type", "")
    if not action_type:
        return False, "missing_type"

    valid_types = ["file_write", "shell", "file_read", "browser", "multi", "python"]
    if action_type not in valid_types:
        return False, f"invalid_type:{action_type}"

    if action_type == "file_write":
        path = action.get("path", "")
        content = action.get("content", "")
        if not path or not path.strip():
            return False, "empty_path"
        if not content or not content.strip():
            return False, "empty_content"

    elif action_type == "shell":
        cmd = action.get("command", "")
        if not cmd or not cmd.strip():
            return False, "empty_command"

    elif action_type == "file_read":
        path = action.get("path", "")
        if not path or not path.strip():
            return False, "empty_path"

    elif action_type == "python":
        code = action.get("code", "")
        if not code or not code.strip():
            return False, "empty_code"

    elif action_type == "browser":
        browser_action = action.get("browser_action", "")
        if not browser_action:
            return False, "empty_browser_action"

    elif action_type == "multi":
        steps = action.get("steps", [])
        if not steps or not isinstance(steps, list):
            return False, "invalid_steps"

    print(f"[ACTION VALIDATED] {action_type}")
    return True, None


def validate_python_content(action: dict) -> tuple[bool, str]:
    """Additional validation for Python content safety."""
    if action.get("type") != "file_write":
        return True, None

    path = action.get("path", "")
    if not path.endswith(".py"):
        return True, None

    content = action.get("content", "")

    bash_indicators = ["#!/", "#!/bin/bash", "bash ", "sudo ", "apt-get"]
    for ind in bash_indicators:
        if ind in content.lower():
            return False, f"bash_syntax_found:{ind}"

    if any(ord(c) < 32 and c not in '\n\r\t' for c in content):
        return False, "invalid_characters"

    return True, None


def correct_action(action: dict, error: str) -> dict:
    """Attempt to correct a bad action."""
    print(f"[ACTION CORRECTING] {error}")

    action_type = action.get("type", "")

    if action_type == "file_write":
        return {
            "type": "file_write",
            "path": "workspace/error_fallback.txt",
            "content": f"Action generation failed: {error}"
        }

    if action_type == "shell":
        return {
            "type": "shell",
            "command": "echo Action generation failed"
        }

    return {
        "type": "shell",
        "command": "echo fallback"
    }


def _apply_guardrails(request: GatewayRequest) -> GatewayRequest:
    """Truncate oversized prompts, set safe defaults."""
    import re
    
    request.prompt = re.sub(r'\n\s*\n', '\n', request.prompt)
    request.prompt = re.sub(r'  +', ' ', request.prompt)
    
    if request.context is None:
        request.context = {}
    if request.metadata is None:
        request.metadata = {}

    if len(request.prompt) > 2000:
        request.prompt = request.prompt[:2000] + "\n[TRUNCATED]"

    request.temperature = 0.2

    request.max_tokens = max(64, min(8192, request.max_tokens))

    base_prompt = """You are an execution planner inside an autonomous agent.
Your job:
- Generate ONLY valid actions
- Follow task EXACTLY
- Be precise and minimal

Environment: Windows PowerShell
Tools: python, file_write, basic shell

RULES:
- Use Python for coding tasks
- Use tkinter for GUI tasks
- Use file_write for creating files
- NEVER use bash, gcc, or Linux commands

OUTPUT FORMAT:
Return ONLY valid JSON.
No explanation.
No markdown.
No extra text."""

    examples = """
EXAMPLES:
Task: create hello world python file
Output: {"action": {"type": "file_write", "path": "hello.py", "content": "print('Hello World')"}}

Task: create calculator using tkinter
Output: {"action": {"type": "file_write", "path": "workspace/calculator.py", "content": "import tkinter ...", "confidence": 0.9}}
"""

    env_str = """
Environment: Windows PowerShell.
Allowed:
* python
* file_write
* basic commands

STRICTLY FORBIDDEN:
* bash
* gcc
* linux paths
* shell scripts"""

    gui_keywords = ["calculator", "gui", "tkinter", "pygame", "qt"]
    prompt_lower = request.prompt.lower()
    if any(kw in prompt_lower for kw in gui_keywords):
        env_str += """
Create Python GUI using tkinter.
Include buttons, input field, and basic operations (+, -, *, /).
Output full working code."""

    if "calculator" in prompt_lower:
        env_str += """
For calculator: Use tkinter.Entry for display, tkinter.Button for digits.
Include number pad (0-9), operators (+, -, *, /), equals (=), clear (C).
Handle divide by zero properly.
Format numbers nicely."""

    full_prompt = base_prompt + "\n\n" + examples + "\n\n" + env_str + "\n\nTASK: " + request.prompt + "\nOutput:"
    request.prompt = full_prompt

    return request


def validate_content_quality(action: dict) -> tuple[bool, str]:
    """Validate content quality for file_write actions."""
    action_type = action.get("type", "")
    if action_type != "file_write":
        return True, None

    content = action.get("content", "")
    path = action.get("path", "")

    if not content or len(content) < 10:
        return False, "content_too_short"

    if path.endswith(".py"):
        indicators = ["def ", "class ", "import ", "print(", "return "]
        if not any(ind in content for ind in indicators):
            return False, "invalid_python"

    if path.endswith(".html"):
        if "<" not in content or ">" not in content:
            return False, "invalid_html"

    return True, None


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