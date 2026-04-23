"""Knowledge Store — canonical long-term knowledge repository."""
import dataclasses
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclasses.dataclass
class QueryResult:
    hit: bool
    match_type: str
    results: list
    best_result: dict
    reason: str


def load_store(base_dir=None) -> dict:
    """Load knowledge_store.json or create empty."""
    if base_dir is None:
        base_dir = Path.cwd()

    store_file = base_dir / ".chotu" / "knowledge_store.json"
    if not store_file.exists():
        return _create_empty_store()

    try:
        with open(store_file, "r", encoding="utf-8") as f:
            store = json.load(f)
        if _validate_store(store):
            return store
        return _create_empty_store()
    except (json.JSONDecodeError, IOError):
        return _create_empty_store()


def save_store(store: dict, base_dir=None) -> None:
    """Atomic write (temp → rename)."""
    if base_dir is None:
        base_dir = Path.cwd()

    runtime_dir = base_dir / ".chotu"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    temp_file = runtime_dir / "knowledge_store.json.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
    os.replace(temp_file, runtime_dir / "knowledge_store.json")


def _create_empty_store() -> dict:
    return {
        "version": "1.0.0",
        "entries": [],
        "stats": {
            "total_entries": 0,
            "total_queries": 0,
            "total_hits": 0,
            "total_misses": 0,
            "total_ingestions": 0,
        }
    }


def _validate_store(store: dict) -> bool:
    return (isinstance(store, dict) and
            "version" in store and
            "entries" in store and
            isinstance(store["entries"], list))


def query(signature: str = "", tags: list = None, kind: str = "",
          text: str = "", base_dir=None) -> QueryResult:
    """Main query entry point. Never raises."""
    from . import logger

    if base_dir is None:
        base_dir = Path.cwd()
    if tags is None:
        tags = []

    store = load_store(base_dir)
    entries = store.get("entries", [])

    store["stats"]["total_queries"] = store["stats"].get("total_queries", 0) + 1

    if signature:
        exact = _find_exact_match(entries, signature, kind)
        if exact:
            exact["metrics"]["usage_count"] = exact.get("metrics", {}).get("usage_count", 0) + 1
            exact["last_used_at"] = datetime.now(timezone.utc).isoformat()
            store["stats"]["total_hits"] = store["stats"].get("total_hits", 0) + 1
            save_store(store, base_dir)
            logger.log_knowledge_hit(signature, "exact")
            return QueryResult(hit=True, match_type="exact", results=[exact], best_result=exact,
                           reason=f"Exact match: {signature}")

    if tags:
        tag_matches = _find_by_tags(entries, tags, kind)
        if tag_matches:
            ranked = _rank_entries(tag_matches)
            best = ranked[0]
            best["metrics"]["usage_count"] = best.get("metrics", {}).get("usage_count", 0) + 1
            best["last_used_at"] = datetime.now(timezone.utc).isoformat()
            store["stats"]["total_hits"] = store["stats"].get("total_hits", 0) + 1
            save_store(store, base_dir)
            logger.log_knowledge_hit(signature or str(tags), "partial")
            return QueryResult(hit=True, match_type="partial", results=ranked[:5], best_result=best,
                           reason=f"Tag match: {len(ranked)} results")

    if kind:
        kind_matches = _find_by_kind(entries, kind)
        if kind_matches:
            ranked = _rank_entries(kind_matches)
            store["stats"]["total_hits"] = store["stats"].get("total_hits", 0) + 1
            save_store(store, base_dir)
            return QueryResult(hit=True, match_type="partial", results=ranked[:5], best_result=ranked[0],
                           reason=f"Kind match: {len(ranked)} {kind} entries")

    if text:
        text_matches = _find_by_text(entries, text)
        if text_matches:
            ranked = _rank_entries(text_matches)
            store["stats"]["total_hits"] = store["stats"].get("total_hits", 0) + 1
            save_store(store, base_dir)
            return QueryResult(hit=True, match_type="partial", results=ranked[:5], best_result=ranked[0],
                           reason=f"Text match: {len(ranked)} results")

    store["stats"]["total_misses"] = store["stats"].get("total_misses", 0) + 1
    save_store(store, base_dir)
    logger.log_knowledge_miss(signature or text or str(tags))

    return QueryResult(hit=False, match_type="none", results=[], best_result={},
                   reason=f"No knowledge match for: {signature or text or str(tags)}")


def _find_exact_match(entries: list, signature: str, kind: str = "") -> dict:
    for entry in entries:
        if entry.get("signature") == signature:
            if kind and entry.get("kind") != kind:
                continue
            if entry.get("status") == "deprecated":
                continue
            return entry
    return None


def _find_by_tags(entries: list, tags: list, kind: str = "") -> list:
    query_tags = set(tags)
    matches = []
    for entry in entries:
        if entry.get("status") == "deprecated":
            continue
        if kind and entry.get("kind") != kind:
            continue
        entry_tags = set(entry.get("tags", []))
        overlap = len(entry_tags & query_tags)
        total = max(len(entry_tags), len(query_tags), 1)
        if overlap >= 2 or overlap / total >= 0.5:
            matches.append(entry)
    return matches


def _find_by_kind(entries: list, kind: str) -> list:
    return [e for e in entries if e.get("kind") == kind and e.get("status") != "deprecated"]


def _find_by_text(entries: list, text: str) -> list:
    text_lower = text.lower()
    matches = []
    for entry in entries:
        if entry.get("status") == "deprecated":
            continue
        searchable = " ".join([entry.get("title", ""), entry.get("description", ""),
                          entry.get("summary", ""), entry.get("signature", "")]).lower()
        if text_lower in searchable:
            matches.append(entry)
    return matches


_STATUS_WEIGHT = {"promoted": 4, "active": 3, "candidate": 2, "deprecated": 0}


def _rank_entries(entries: list) -> list:
    def score(e):
        status_w = _STATUS_WEIGHT.get(e.get("status", "candidate"), 1)
        metrics = e.get("metrics", {})
        sr = metrics.get("success_rate", 0.0)
        usage = metrics.get("usage_count", 0)
        recency = 0
        last_used = e.get("last_used_at", "")
        if last_used:
            try:
                dt = datetime.fromisoformat(last_used.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                age_hours = (now - dt).total_seconds() / 3600
                recency = max(0, 1.0 - (age_hours / 720))
            except (ValueError, TypeError):
                pass
        return (status_w, sr, usage, recency)
    return sorted(entries, key=score, reverse=True)


def ingest_from_learning(learn_output, step: dict = None, base_dir=None) -> str:
    """Ingest learning output into Knowledge Store."""
    from . import logger

    if base_dir is None:
        base_dir = Path.cwd()
    if step is None:
        step = {}

    store = load_store(base_dir)
    kind = _classify_kind(learn_output)
    status = _map_recommendation_to_status(learn_output.recommendation)
    title = _build_title(learn_output.pattern_signature, kind)
    provenance = {"source": "learning", "source_ids": [learn_output.learning_event_id],
                 "task_id": step.get("task_id", ""), "step_id": step.get("id", "")}
    metrics = {"successes": learn_output.after.get("successes", 0),
             "failures": learn_output.after.get("failures", 0),
             "attempts": learn_output.after.get("attempts", 0),
             "success_rate": learn_output.after.get("success_rate", 0.0),
             "confidence": learn_output.confidence, "usage_count": 1}
    tags = _build_tags_from_signature(learn_output.pattern_signature)
    description = learn_output.notes or learn_output.reason
    summary = f"{learn_output.outcome}: {learn_output.strategy_name} (source: {learn_output.source})"

    entry = upsert_entry(store=store, kind=kind, title=title,
                    signature=learn_output.pattern_signature,
                    description=description, summary=summary, tags=tags,
                    provenance=provenance, metrics=metrics, status=status)

    store["stats"]["total_ingestions"] = store["stats"].get("total_ingestions", 0) + 1
    _maybe_prune(store)
    save_store(store, base_dir)
    logger.log_knowledge_ingest(learn_output.pattern_signature, kind, status)

    return entry.get("id", "")


def _classify_kind(learn_output) -> str:
    outcome = learn_output.outcome
    source = learn_output.source

    if source == "search" and outcome == "success":
        return "solution"
    if outcome == "failure":
        return "pattern"
    if outcome == "success":
        return "strategy"
    if outcome == "partial":
        return "lesson"
    if outcome == "skip":
        return "issue"
    return "pattern"


def _map_recommendation_to_status(recommendation: str) -> str:
    mapping = {"promote": "promoted", "demote": "deprecated",
            "create_candidate": "candidate", "review": "active", "keep": "active"}
    return mapping.get(recommendation, "candidate")


def _build_title(signature: str, kind: str) -> str:
    parts = signature.replace(":", " ").replace("_", " ").title()
    return f"{kind.title()}: {parts}"


def _build_tags_from_signature(signature: str) -> list:
    parts = signature.replace(":", " ").replace("_", " ").split()
    return [p.lower() for p in parts if len(p) > 2]


def upsert_entry(store: dict, kind: str, title: str, signature: str,
              description: str, summary: str, tags: list,
              provenance: dict, metrics: dict, status: str) -> dict:
    entries = store.get("entries", [])
    existing = None
    for entry in entries:
        if entry.get("signature") == signature and entry.get("kind") == kind:
            existing = entry
            break

    now = datetime.now(timezone.utc).isoformat()

    if existing:
        existing = _merge_into_existing(existing, {"title": title, "description": description,
                                    "summary": summary, "tags": tags,
                                    "provenance": provenance, "metrics": metrics,
                                    "status": status})
        existing["updated_at"] = now
        existing["last_used_at"] = now
        return existing

    new_entry = {"id": f"know_{uuid.uuid4().hex[:8]}", "kind": kind, "title": title,
              "signature": signature, "description": description, "summary": summary,
              "tags": sorted(set(tags)), "provenance": provenance, "metrics": metrics,
              "alternatives": [], "status": status, "created_at": now, "updated_at": now,
              "last_used_at": now, "notes": ""}
    entries.append(new_entry)
    store["entries"] = entries
    store["stats"]["total_entries"] = len(entries)
    return new_entry


def _merge_into_existing(existing: dict, new_data: dict) -> dict:
    old_tags = set(existing.get("tags", []))
    new_tags = set(new_data.get("tags", []))
    existing["tags"] = sorted(old_tags | new_tags)

    old_metrics = existing.get("metrics", {})
    new_metrics = new_data.get("metrics", {})
    existing["metrics"] = {
        "successes": max(old_metrics.get("successes", 0), new_metrics.get("successes", 0)),
        "failures": max(old_metrics.get("failures", 0), new_metrics.get("failures", 0)),
        "attempts": max(old_metrics.get("attempts", 0), new_metrics.get("attempts", 0)),
        "success_rate": new_metrics.get("success_rate", old_metrics.get("success_rate", 0)),
        "confidence": max(old_metrics.get("confidence", 0), new_metrics.get("confidence", 0)),
        "usage_count": old_metrics.get("usage_count", 0) + 1,
    }

    old_prov = existing.get("provenance", {})
    new_prov = new_data.get("provenance", {})
    old_ids = set(old_prov.get("source_ids", []))
    new_ids = set(new_prov.get("source_ids", []))
    merged_ids = sorted(old_ids | new_ids)
    existing["provenance"] = {"source": new_prov.get("source", old_prov.get("source", "unknown")),
                          "source_ids": merged_ids[-20:],
                          "task_id": new_prov.get("task_id", old_prov.get("task_id", "")),
                          "step_id": new_prov.get("step_id", old_prov.get("step_id", ""))}

    old_status_w = _STATUS_WEIGHT.get(existing.get("status", "candidate"), 1)
    new_status_w = _STATUS_WEIGHT.get(new_data.get("status", "candidate"), 1)
    if new_status_w > old_status_w:
        existing["status"] = new_data["status"]

    if new_data.get("description"):
        existing["description"] = new_data["description"]
    if new_data.get("summary"):
        existing["summary"] = new_data["summary"]

    return existing


def ingest_from_memory(memory_entry: dict, base_dir=None) -> str:
    """Import a strong memory entry into Knowledge Store."""
    from . import logger

    if base_dir is None:
        base_dir = Path.cwd()

    store = load_store(base_dir)
    signature = memory_entry.get("signature", "")
    if not signature:
        return ""

    strategies = memory_entry.get("strategies", [])
    if not strategies:
        return ""

    best = strategies[0]
    for s in strategies:
        if s.get("success_rate", 0) > best.get("success_rate", 0):
            best = s

    sr = best.get("success_rate", 0)
    attempts = best.get("attempts", 0)

    if sr < 0.6 or attempts < 2:
        return ""

    status = "active"
    if sr >= 0.8 and attempts >= 3:
        status = "promoted"

    provenance = {"source": "memory", "source_ids": [memory_entry.get("id", "")],
                 "task_id": "", "step_id": ""}
    metrics = {"successes": best.get("successes", 0), "failures": best.get("failures", 0),
             "attempts": attempts, "success_rate": sr,
             "confidence": min(0.9, 0.5 + sr * 0.4),
             "usage_count": memory_entry.get("usage_count", 0)}

    title = _build_title(signature, "strategy")
    tags = memory_entry.get("context_tags", [])
    description = best.get("action_hint", "")
    summary = f"Memory strategy: {best.get('strategy_name', '')} (sr={sr:.0%})"

    entry = upsert_entry(store=store, kind="strategy", title=title,
                    signature=signature, description=description, summary=summary,
                    tags=tags, provenance=provenance, metrics=metrics, status=status)

    for s in strategies:
        if s.get("strategy_name") != best.get("strategy_name"):
            _add_alternative(entry, s.get("strategy_name", ""), s.get("action_hint", ""),
                         s.get("success_rate", 0), s.get("attempts", 0))

    store["stats"]["total_ingestions"] = store["stats"].get("total_ingestions", 0) + 1
    save_store(store, base_dir)
    logger.log_knowledge_ingest(signature, "strategy", status)

    return entry.get("id", "")


def _add_alternative(entry: dict, strategy_name: str, action_hint: str,
                   success_rate: float, attempts: int) -> None:
    alts = entry.get("alternatives", [])
    for alt in alts:
        if alt.get("strategy_name") == strategy_name:
            alt["success_rate"] = success_rate
            alt["attempts"] = attempts
            if action_hint:
                alt["action_hint"] = action_hint
            return
    alts.append({"strategy_name": strategy_name, "action_hint": action_hint,
                "success_rate": success_rate, "attempts": attempts})
    if len(alts) > 5:
        alts.sort(key=lambda a: a.get("success_rate", 0), reverse=True)
        entry["alternatives"] = alts[:5]
    else:
        entry["alternatives"] = alts


def promote_entry(entry_id: str, base_dir=None) -> bool:
    if base_dir is None:
        base_dir = Path.cwd()
    store = load_store(base_dir)
    for entry in store.get("entries", []):
        if entry.get("id") == entry_id:
            entry["status"] = "promoted"
            entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_store(store, base_dir)
            return True
    return False


def demote_entry(entry_id: str, base_dir=None) -> bool:
    if base_dir is None:
        base_dir = Path.cwd()
    store = load_store(base_dir)
    for entry in store.get("entries", []):
        if entry.get("id") == entry_id:
            entry["status"] = "deprecated"
            entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_store(store, base_dir)
            return True
    return False


def summarize(base_dir=None) -> dict:
    if base_dir is None:
        base_dir = Path.cwd()
    store = load_store(base_dir)
    entries = store.get("entries", [])
    by_kind, by_status = {}, {}
    for e in entries:
        kind = e.get("kind", "unknown")
        status = e.get("status", "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    return {"total_entries": len(entries), "by_kind": by_kind, "by_status": by_status,
            "stats": store.get("stats", {})}


_MAX_ENTRIES = 500


def _maybe_prune(store: dict) -> None:
    entries = store.get("entries", [])
    if len(entries) <= _MAX_ENTRIES:
        return
    deprecated = [e for e in entries if e.get("status") == "deprecated"]
    if not deprecated:
        return
    deprecated.sort(key=lambda e: e.get("metrics", {}).get("usage_count", 0))
    excess = len(entries) - _MAX_ENTRIES
    to_remove = set()
    for e in deprecated[:excess]:
        to_remove.add(e.get("id"))
    store["entries"] = [e for e in entries if e.get("id") not in to_remove]
    store["stats"]["total_entries"] = len(store["entries"])