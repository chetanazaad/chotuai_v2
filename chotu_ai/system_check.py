"""System Check module — verify environment readiness before task execution."""
import json
import socket
import urllib.request
import subprocess
from typing import List, Tuple

def run_preflight(selected_model: str = "qwen:7b") -> Tuple[bool, List[str]]:
    """
    Run a battery of system checks.
    Returns (success, error_messages).
    """
    errors = []
    
    # 1. Check Internet Connection
    if not _check_internet():
        errors.append("Internet connection unavailable. Please check your network.")
        
    # 2. Check Ollama Server
    ollama_ok, ollama_msg = _check_ollama_server()
    if not ollama_ok:
        errors.append(ollama_msg)
    else:
        # 3. Check Selected Model
        if not _check_model_present(selected_model):
            errors.append(f"Selected model '{selected_model}' is not installed in Ollama. Run 'ollama pull {selected_model}'.")
            
    # 4. Check Key Dependencies
    deps = [
        ("bs4", "BeautifulSoup4 (pip install beautifulsoup4)"),
        ("requests", "Requests (pip install requests)")
    ]
    for module_name, install_name in deps:
        if not _check_dependency(module_name):
            errors.append(f"Missing dependency: {install_name}")
            
    return len(errors) == 0, errors

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
    """Check if Ollama server is reachable."""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                return True, ""
    except Exception:
        pass
    return False, "Ollama server is not running at http://localhost:11434. Please start it."

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
