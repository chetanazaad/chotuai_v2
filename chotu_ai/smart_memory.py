"""Smart Memory module — persistent strategy knowledge store."""
import dataclasses
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclasses.dataclass
class MemoryLookupResult:
    hit: bool
    match_type: str
    signature: str
    best_strategy: dict
    alternatives: list
    confidence: float
    reason: str


_TOKEN_PATTERNS = {
    "missing_dependency": [
        (r"No module named '(\w+)'", "python"),
        (r"ModuleNotFoundError.*?'(\w+)'", "python"),
        (r"ImportError.*?'(\w+)'", "python"),
        (r"'(\w+)' is not recognized", "shell"),
        (r"command not found.*?(\w+)", "shell"),
    ],
    "syntax_error": [
        (r"File \"(.+?)\"", "file"),
        (r"SyntaxError", "python"),
        (r"IndentationError", "python"),
    ],
    "infrastructure": [
        (r"PermissionError", "permission"),
        (r"FileNotFoundError.*?'(.+?)'", "file"),
        (r"access denied", "permission"),
        (r"disk full", "disk"),
    ],
}

_MAX_ENTRIES = 200


def load_memory(base_dir: Optional[Path] = None) -> dict:
    """Load memory.json or create empty store."""
    if base_dir is None:
        base_dir = Path.cwd()

    runtime_dir = base_dir / ".chotu"
    memory_file = runtime_dir / "memory.json"
    if not memory_file.exists():
        return _create_empty_memory()

    try:
        with open(memory_file, "r", encoding="utf-8") as f:
            memory = json.load(f)
        if _validate_memory(memory):
            return memory
        return _create_empty_memory()
    except (json.JSONDecodeError, IOError):
        return _create_empty_memory()


def save_memory(memory: dict, base_dir: Optional[Path] = None) -> None:
    """Atomic write (temp -> rename)."""
    if base_dir is None:
        base_dir = Path.cwd()

    runtime_dir = base_dir / ".chotu"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    temp_file = runtime_dir / "memory.json.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2)
    os.replace(temp_file, runtime_dir / "memory.json")


def lookup(signature: str, context_tags: list, base_dir: Optional[Path] = None) -> MemoryLookupResult:
    """Main lookup entry point."""
    from . import logger

    if base_dir is None:
        base_dir = Path.cwd()

    memory = load_memory(base_dir)
    entries = memory.get("entries", [])

    memory["stats"]["total_lookups"] = memory["stats"].get("total_lookups", 0) + 1

    exact = _find_exact_match(entries, signature)
    if exact:
        best = _get_best_strategy(exact)
        if best and best.get("attempts", 0) > 0:
            alts = _rank_strategies(exact.get("strategies", []))
            alts = [s for s in alts if s.get("strategy_id") != best.get("strategy_id")]
            confidence = _compute_match_confidence("exact", exact, context_tags)

            memory["stats"]["total_hits"] = memory["stats"].get("total_hits", 0) + 1
            save_memory(memory, base_dir)
            try:
                logger.log_memory_hit(signature, "exact", confidence)
            except RuntimeError:
                pass

            sr = best.get("success_rate", 0)
            att = best.get("attempts", 0)
            return MemoryLookupResult(
                hit=True,
                match_type="exact",
                signature=signature,
                best_strategy=best,
                alternatives=alts[:3],
                confidence=confidence,
                reason=f"Exact match: {signature} ({sr:.0%} success rate, {att} attempts)"
            )

    category = signature.split(":")[0] if ":" in signature else signature
    partials = _find_partial_matches(entries, category, context_tags)
    if partials:
        best_entry = partials[0]
        best = _get_best_strategy(best_entry)
        if best and best.get("attempts", 0) > 0:
            confidence = _compute_match_confidence("partial", best_entry, context_tags)

            memory["stats"]["total_hits"] = memory["stats"].get("total_hits", 0) + 1
            save_memory(memory, base_dir)
            try:
                logger.log_memory_hit(signature, "partial", confidence)
            except RuntimeError:
                pass

            return MemoryLookupResult(
                hit=True,
                match_type="partial",
                signature=best_entry.get("signature", ""),
                best_strategy=best,
                alternatives=[],
                confidence=confidence,
                reason=f"Partial match: {best_entry.get('signature', '')}"
            )

    memory["stats"]["total_misses"] = memory["stats"].get("total_misses", 0) + 1
    save_memory(memory, base_dir)
    try:
        logger.log_memory_miss(signature)
    except RuntimeError:
        pass

    return MemoryLookupResult(
        hit=False,
        match_type="none",
        signature=signature,
        best_strategy={},
        alternatives=[],
        confidence=0.0,
        reason=f"No memory match for: {signature}"
    )


def record_success(signature: str, strategy_name: str, action_hint: str,
                  context_tags: list, base_dir: Optional[Path] = None) -> None:
    """Record a successful strategy."""
    from . import logger

    if base_dir is None:
        base_dir = Path.cwd()

    memory = load_memory(base_dir)
    category = signature.split(":")[0] if ":" in signature else signature

    entry = _upsert_entry(memory, signature, category, context_tags)
    strategy = _upsert_strategy(entry, strategy_name, action_hint)

    strategy["attempts"] += 1
    strategy["successes"] += 1
    strategy["success_rate"] = strategy["successes"] / strategy["attempts"]

    now = datetime.now(timezone.utc).isoformat()
    strategy["last_used_at"] = now
    strategy["last_outcome"] = "success"
    entry["last_used_at"] = now
    entry["updated_at"] = now
    entry["usage_count"] = entry.get("usage_count", 0) + 1

    _update_best_strategy(entry)
    _maybe_prune(memory)
    save_memory(memory, base_dir)
    logger.log_memory_update(signature, strategy_name, "success")


def record_failure(signature: str, strategy_name: str, action_hint: str,
                 context_tags: list, base_dir: Optional[Path] = None) -> None:
    """Record a failed strategy."""
    from . import logger

    if base_dir is None:
        base_dir = Path.cwd()

    memory = load_memory(base_dir)
    category = signature.split(":")[0] if ":" in signature else signature

    entry = _upsert_entry(memory, signature, category, context_tags)
    strategy = _upsert_strategy(entry, strategy_name, action_hint)

    strategy["attempts"] += 1
    strategy["failures"] += 1
    strategy["success_rate"] = strategy["successes"] / strategy["attempts"]

    now = datetime.now(timezone.utc).isoformat()
    strategy["last_used_at"] = now
    strategy["last_outcome"] = "failure"
    entry["last_used_at"] = now
    entry["updated_at"] = now
    entry["usage_count"] = entry.get("usage_count", 0) + 1

    _update_best_strategy(entry)
    save_memory(memory, base_dir)
    logger.log_memory_update(signature, strategy_name, "failure")


def normalize_signature(val_result, step: dict, state: dict) -> str:
    """Build stable error signature."""
    import re

    failure_type = val_result.failure_type

    if failure_type == "none":
        return "none"

    combined = val_result.reason or ""
    checks = val_result.details.get("checks", [])
    for c in checks:
        combined += " " + (c.get("detail") or "")

    key_token = "generic"
    patterns = _TOKEN_PATTERNS.get(failure_type, [])
    for pattern, token_type in patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            if match.groups():
                key_token = match.group(1).lower().strip("'\"")
            else:
                key_token = token_type
            break

    if key_token == "generic":
        step_desc = step.get("description", "").lower()
        if "python" in step_desc or ".py" in step_desc:
            key_token = "python"
        elif "pip" in step_desc or "install" in step_desc:
            key_token = "pip"
        elif "shell" in step_desc or "cmd" in step_desc:
            key_token = "shell"

    return f"{failure_type}:{key_token}"


def get_context_tags(val_result, step: dict, state: dict) -> list:
    """Extract context tags."""
    import platform

    tags = set()
    tags.add(val_result.failure_type)

    desc = step.get("description", "").lower()
    if "python" in desc or ".py" in desc:
        tags.add("python")
    if "pip" in desc or "install" in desc:
        tags.add("pip")
    if "shell" in desc or "cmd" in desc:
        tags.add("shell")
    if "file" in desc or "write" in desc or "create" in desc:
        tags.add("file")

    action = step.get("action", {})
    if action:
        action_type = action.get("type", "")
        if action_type:
            tags.add(action_type)

    tags.add(platform.system().lower())

    return sorted(tags)


def _create_empty_memory() -> dict:
    return {
        "version": "1.0.0",
        "entries": [],
        "stats": {
            "total_entries": 0,
            "total_lookups": 0,
            "total_hits": 0,
            "total_misses": 0
        }
    }


def _validate_memory(memory: dict) -> bool:
    return isinstance(memory, dict) and "version" in memory and "entries" in memory


def _find_exact_match(entries: list, signature: str) -> Optional[dict]:
    for entry in entries:
        if entry.get("signature") == signature:
            return entry
    return None


def _find_partial_matches(entries: list, problem_category: str, context_tags: list) -> list:
    matches = []
    for entry in entries:
        if entry.get("problem_category") != problem_category:
            continue

        entry_tags = set(entry.get("context_tags", []))
        query_tags = set(context_tags)

        if not entry_tags or not query_tags:
            continue

        overlap = len(entry_tags & query_tags)
        total = max(len(entry_tags), len(query_tags), 1)
        overlap_ratio = overlap / total

        if overlap >= 2 or overlap_ratio >= 0.5:
            matches.append((entry, overlap_ratio))

    matches.sort(key=lambda x: (x[1], x[0].get("usage_count", 0)), reverse=True)
    return [m[0] for m in matches]


def _rank_strategies(strategies: list) -> list:
    def score(s):
        sr = s.get("success_rate", 0.0)
        attempts = s.get("attempts", 0)
        last_outcome = 1 if s.get("last_outcome") == "success" else 0

        if attempts < 2:
            sr = sr * 0.5

        recency = 0
        last_used = s.get("last_used_at", "")
        if last_used:
            try:
                dt = datetime.fromisoformat(last_used.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                age_hours = (now - dt).total_seconds() / 3600
                recency = max(0, 1.0 - (age_hours / 720))
            except (ValueError, TypeError):
                pass

        return (sr, attempts, recency, last_outcome)

    return sorted(strategies, key=score, reverse=True)


def _get_best_strategy(entry: dict) -> Optional[dict]:
    strategies = entry.get("strategies", [])
    if not strategies:
        return None

    ranked = _rank_strategies(strategies)
    best_id = entry.get("best_strategy_id")
    for s in ranked:
        if s.get("strategy_id") == best_id:
            return s
    return ranked[0] if ranked else None


def _upsert_entry(memory: dict, signature: str, problem_category: str, context_tags: list) -> dict:
    entries = memory.get("entries", [])

    for entry in entries:
        if entry["signature"] == signature:
            existing_tags = set(entry.get("context_tags", []))
            existing_tags.update(context_tags)
            entry["context_tags"] = sorted(existing_tags)
            return entry

    now = datetime.now(timezone.utc).isoformat()
    new_entry = {
        "id": f"mem_{uuid.uuid4().hex[:8]}",
        "signature": signature,
        "problem_category": problem_category,
        "context_tags": sorted(context_tags),
        "strategies": [],
        "best_strategy_id": None,
        "created_at": now,
        "updated_at": now,
        "last_used_at": now,
        "usage_count": 0
    }
    entries.append(new_entry)
    memory["entries"] = entries
    memory["stats"]["total_entries"] = len(entries)
    return new_entry


def _upsert_strategy(entry: dict, strategy_name: str, action_hint: str) -> dict:
    strategies = entry.get("strategies", [])

    for strat in strategies:
        if strat["strategy_name"] == strategy_name:
            if action_hint:
                strat["action_hint"] = action_hint
            return strat

    new_strat = {
        "strategy_id": f"strat_{uuid.uuid4().hex[:8]}",
        "action_hint": action_hint,
        "strategy_name": strategy_name,
        "successes": 0,
        "failures": 0,
        "attempts": 0,
        "success_rate": 0.0,
        "last_used_at": "",
        "last_outcome": "",
        "notes": ""
    }
    strategies.append(new_strat)
    entry["strategies"] = strategies
    return new_strat


def _update_best_strategy(entry: dict) -> None:
    strategies = entry.get("strategies", [])
    if not strategies:
        entry["best_strategy_id"] = None
        return

    ranked = _rank_strategies(strategies)
    entry["best_strategy_id"] = ranked[0].get("strategy_id")


def _maybe_prune(memory: dict) -> None:
    entries = memory.get("entries", [])
    if len(entries) <= _MAX_ENTRIES:
        return

    entries.sort(key=lambda e: e.get("usage_count", 0))
    excess = len(entries) - _MAX_ENTRIES
    memory["entries"] = entries[excess:]
    memory["stats"]["total_entries"] = len(memory["entries"])


def _compute_match_confidence(match_type: str, entry: dict, context_tags: list) -> float:
    if match_type == "exact":
        base = 0.85
        usage = entry.get("usage_count", 0)
        if usage >= 5:
            base += 0.05
        if usage >= 10:
            base += 0.05

        best = _get_best_strategy(entry)
        if best:
            sr = best.get("success_rate", 0)
            if sr >= 0.8:
                base += 0.05
            elif sr < 0.3:
                base -= 0.15

        return min(0.95, max(0.1, base))

    elif match_type == "partial":
        entry_tags = set(entry.get("context_tags", []))
        query_tags = set(context_tags)
        overlap = len(entry_tags & query_tags)
        total = max(len(entry_tags), len(query_tags), 1)

        return min(0.70, 0.40 + (overlap / total) * 0.30)

    return 0.0