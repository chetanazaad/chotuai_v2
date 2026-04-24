"""LLM Gateway — centralized intelligence routing layer."""
import dataclasses
import json
import re
import subprocess
import sys
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
}


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


def safe_llm_call(model: str, prompt: str, temp: float, max_tok: int, timeout: int = None) -> tuple:
    """Safe LLM call with timeout and error handling."""
    global _consecutive_timeouts

    if timeout is None:
        timeout = _get_timeout_for_model(model)

    try:
        result = _call_ollama(model, prompt, temp, max_tok, timeout)
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


def generate(request: GatewayRequest) -> GatewayResponse:
    """Single entry point. Routes, calls, normalizes. Never blocks."""
    from . import logger

    print("[LLM] attempt start")
    
    if not _ensure_ollama_running():
        logger.log_gateway_failure(request.purpose, "ollama", "Failed to start Ollama")
        return _build_unavailable_response("Ollama server unavailable")

    request = _apply_guardrails(request)
    
    provider_name = _select_provider(request)
    logger.log_gateway_start(request.purpose, provider_name)

    provider_config = _PROVIDERS[provider_name]
    _ensure_model_loaded(provider_config["model"])
    
    from . import llm_cache
    cached = llm_cache.get_cached(request.prompt)
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
    print(f"[LLM] using model: {provider_config['model']}")
    
    provider_config = _PROVIDERS[provider_name]
    _ensure_model_loaded(provider_config["model"])

    if not _check_provider(provider_name):
        alt_provider = "qwen:7b" if provider_name == "phi3" else "phi3"
        if _check_provider(alt_provider):
            logger.log_gateway_fallback(request.purpose, provider_name, alt_provider, "primary unavailable")
            provider_name = alt_provider
            provider_config = _PROVIDERS[provider_name]
        else:
            logger.log_gateway_failure(request.purpose, "no_provider", "All local providers unavailable")
            return _build_unavailable_response("No local providers available")

    provider_config = _PROVIDERS[provider_name]
    try:
        raw_output, latency_ms, tokens_used = safe_llm_call(
            model=provider_config["model"],
            prompt=request.prompt,
            temp=request.temperature,
            max_tok=request.max_tokens,
            timeout=provider_config["timeout"]
        )
    except Exception as e:
        error_msg = str(e)
        alt_model = "qwen:7b" if provider_name == "phi3" else "phi3"
        print(f"[LLM] Timeout → switching to {alt_model}")
        try:
            alt_config = _PROVIDERS[alt_model]
            raw_output, latency_ms, tokens_used = safe_llm_call(
                model=alt_config["model"],
                prompt=request.prompt,
                temp=request.temperature,
                max_tok=request.max_tokens,
                timeout=alt_config["timeout"]
            )
            provider_name = alt_model
            provider_config = alt_config
        except Exception as e2:
            logger.log_gateway_failure(request.purpose, provider_name, error_msg)
            print(f"[LLM] All models timed out")
            return _build_unavailable_response(f"LLM timeout: {error_msg}")

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
    
    llm_cache.set_cached(request.prompt, raw_output, provider_config["model"], tokens_used)
    
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


def _parse_response(raw_output: str) -> tuple:
    """Parse raw text -> (structured: bool, parsed: dict)."""
    parsed = extract_json(raw_output)
    if parsed:
        if isinstance(parsed, dict):
            return True, parsed
        if isinstance(parsed, list):
            return True, {"items": parsed}

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