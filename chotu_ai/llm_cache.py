"""LLM Response Cache - Fast, file-backed prompt→response cache."""
import json
import os
import hashlib
import time
from pathlib import Path
from typing import Optional

_CACHE_FILE = ".chotu/llm_cache.json"
_CACHE_LIMIT = 100

_stats = {"hits": 0, "misses": 0}


def _load_cache() -> dict:
    """Load cache from disk."""
    if not os.path.exists(_CACHE_FILE):
        return {}
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    """Save cache to disk."""
    Path(_CACHE_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def _hash_prompt(prompt: str) -> str:
    """Generate hash key for prompt."""
    return hashlib.md5(prompt.encode()).hexdigest()


def get_cached(prompt: str) -> Optional[str]:
    """Get cached response for prompt. Returns None if not found."""
    global _stats
    
    cache = _load_cache()
    key = _hash_prompt(prompt)
    
    if key in cache:
        _stats["hits"] += 1
        print(f"[LLM CACHE HIT]")
        return cache[key].get("response")
    
    _stats["misses"] += 1
    return None


def set_cached(prompt: str, response: str, model: str, tokens: int) -> None:
    """Store response in cache. Enforces FIFO limit."""
    cache = _load_cache()
    key = _hash_prompt(prompt)
    
    cache[key] = {
        "response": response,
        "model": model,
        "timestamp": int(time.time()),
        "tokens": tokens
    }
    
    if len(cache) > _CACHE_LIMIT:
        oldest = min(cache.items(), key=lambda x: x[1].get("timestamp", 0))
        del cache[oldest[0]]
    
    _save_cache(cache)


def get_stats() -> dict:
    """Get cache statistics."""
    cache = _load_cache()
    return {
        "entries": len(cache),
        "hits": _stats["hits"],
        "misses": _stats["misses"],
        "hit_rate": _stats["hits"] / max(1, _stats["hits"] + _stats["misses"])
    }


def clear() -> int:
    """Clear all cache entries. Returns count removed."""
    cache = _load_cache()
    count = len(cache)
    cache.clear()
    _save_cache(cache)
    _stats["hits"] = 0
    _stats["misses"] = 0
    return count
