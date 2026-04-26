"""System Check module — verify environment readiness before task execution."""
import json
import socket
import urllib.request
import subprocess
from typing import List, Tuple

def run_preflight(selected_model: str = "qwen:7b") -> Tuple[bool, List[str]]:
    """
    Run a battery of system checks.
    Returns (success, messages).
    """
    messages = []
    import os
    
    # 1. Infrastructure (CRITICAL)
    ollama_ok, ollama_msg = _check_ollama_server()
    if not ollama_ok:
        messages.append(f"❌ Ollama: {ollama_msg}")
        return False, messages
    messages.append("✅ Ollama: Running")
        
    if not _check_model_present(selected_model):
        messages.append(f"❌ Model: '{selected_model}' not found.")
        return False, messages
    messages.append(f"✅ Model: {selected_model} available")

    # 2. Connectivity & Cloud (OPTIONAL)
    if _check_internet():
        messages.append("✅ Internet: Connected")
    else:
        messages.append("⚠️ Internet: Offline (Local tools only)")

    cloud_apis = []
    if os.environ.get("GEMINI_API_KEY"): cloud_apis.append("Gemini")
    if os.environ.get("OPENAI_API_KEY"): cloud_apis.append("OpenAI")
    if os.environ.get("ANTHROPIC_API_KEY"): cloud_apis.append("Anthropic")
    
    if cloud_apis:
        messages.append(f"✅ Cloud APIs: {', '.join(cloud_apis)}")
    else:
        messages.append("⚠️ Cloud APIs: None configured")

    # 3. Scraping & Tools (OPTIONAL)
    scraper_ok = _check_dependency("bs4") and _check_dependency("requests")
    if scraper_ok:
        messages.append("✅ Scraper: Ready (bs4/requests)")
    else:
        messages.append("⚠️ Scraper: Missing dependencies")
            
    return True, messages

def _check_internet(timeout=3) -> bool:
    """Check if internet is reachable."""
    try:
        # Check by connecting to a common DNS server
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except Exception:
        return False

def _check_ollama_server() -> Tuple[bool, str]:
    """Check if Ollama server is reachable, try to start if not."""
    url = "http://localhost:11434/api/tags"
    
    # Try once
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1) as resp:
            if resp.status == 200: return True, ""
    except Exception:
        pass

    # Try starting it
    try:
        import time
        import sys
        print("[SYSTEM CHECK] Ollama not found. Attempting auto-start...")
        subprocess.Popen(
            ["ollama", "serve"],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait up to 5 seconds for it to wake up
        for _ in range(5):
            time.sleep(1)
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=1) as resp:
                    if resp.status == 200: return True, ""
            except Exception:
                continue
    except Exception as e:
        return False, f"Failed to start Ollama: {e}"

    return False, "Ollama server is not running and could not be auto-started. Please open Ollama manually."

def _check_model_present(model_name: str) -> bool:
    """Check if a specific model is installed in Ollama."""
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Check for exact or partial match (ollama list often shows tags like qwen:7b)
            return model_name in result.stdout
    except Exception:
        pass
    return False

def _check_dependency(module_name: str) -> bool:
    """Check if a python module is importable."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False
