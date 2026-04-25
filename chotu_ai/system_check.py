"""System readiness checker — probes infrastructure without controller dependency."""
import urllib.request
import json
import time
from typing import Dict, Any


def check_internet(timeout: float = 3.0) -> Dict[str, Any]:
    """Check internet connectivity via HTTP HEAD to Google."""
    start = time.perf_counter()
    try:
        req = urllib.request.Request(
            "https://www.google.com",
            method="HEAD"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return {"status": "ok", "latency_ms": latency_ms}
    except Exception as e:
        return {"status": "fail", "error": str(e)[:50]}


def check_ollama_running(timeout: float = 2.0) -> Dict[str, Any]:
    """Check if Ollama server is running."""
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/tags",
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", [])
                model_names = [m.get("name", "") for m in models]
                return {"status": "ok", "models": model_names}
            return {"status": "fail", "error": f"HTTP {resp.status}"}
    except Exception as e:
        return {"status": "fail", "error": str(e)[:50]}


def check_models_available() -> Dict[str, bool]:
    """Check if phi3 and qwen models are available."""
    ollama = check_ollama_running()
    models = ollama.get("models", [])
    return {
        "phi3": any("phi3" in m.lower() for m in models),
        "qwen": any("qwen" in m.lower() for m in models),
    }


def check_tools_available() -> Dict[str, bool]:
    """Check if browser_agent and filtered_search modules can import."""
    tools = {"browser": False, "scraper": False}
    try:
        from . import browser_agent
        tools["browser"] = True
    except ImportError:
        pass
    try:
        from . import filtered_search
        tools["scraper"] = True
    except ImportError:
        pass
    return tools


def full_check() -> Dict[str, Any]:
    """Run all system checks and return combined status."""
    internet = check_internet()
    ollama = check_ollama_running()
    models = check_models_available() if ollama["status"] == "ok" else {"phi3": False, "qwen": False}
    tools = check_tools_available()

    ready = (
        internet["status"] == "ok" and
        ollama["status"] == "ok" and
        (models["phi3"] or models["qwen"])
    )

    return {
        "internet": internet,
        "ollama": ollama,
        "models": models,
        "tools": tools,
        "ready": ready,
    }