"""Task Classifier — deterministic, rule-based task profiling."""
import dataclasses
import re
from typing import Optional


_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must", "to", "of",
    "in", "for", "on", "with", "at", "by", "from", "as", "into", "about",
    "like", "through", "after", "over", "between", "out", "against",
    "during", "without", "before", "under", "around", "among",
    "it", "its", "this", "that", "these", "those", "i", "me", "my",
    "we", "our", "you", "your", "he", "she", "they", "them",
    "and", "but", "or", "nor", "not", "so", "if", "then", "else",
    "very", "just", "also", "more", "some", "any", "all", "each",
    "please", "help", "want", "lets", "let",
}

_TASK_TYPE_PATTERNS = {
    "search": {"keywords": ["find", "search", "look for", "get", "fetch", "locate",
        "recent", "latest", "discover", "look up", "where", "who", "what is", "show me"], "weight": 1.0},
    "build": {"keywords": ["make", "create", "build", "develop", "generate",
        "set up", "setup", "scaffold", "bootstrap", "app", "application", "website", "api", "tool",
        "project", "system", "platform", "dashboard"], "weight": 1.0},
    "coding": {"keywords": ["fix", "debug", "code", "script", "program",
        "python", "javascript", "function", "error", "bug", "implement", "refactor", "write code", "patch",
        "test", "compile", "run", "execute"], "weight": 1.0},
    "summary": {"keywords": ["summarize", "summary", "summarise", "brief",
        "tldr", "overview", "key points", "recap", "condense", "digest", "short version"], "weight": 1.0},
    "analysis": {"keywords": ["analyze", "analyse", "compare", "evaluate",
        "review", "assess", "examine", "inspect", "report", "metrics", "statistics", "trends",
        "breakdown", "audit"], "weight": 1.0},
    "automation": {"keywords": ["automate", "schedule", "repeat", "batch",
        "cron", "trigger", "workflow", "pipeline", "every", "daily", "weekly", "monitor"], "weight": 1.0},
    "cleanup": {"keywords": ["clean", "organize", "sort", "delete",
        "remove", "tidy", "restructure", "move", "rename", "archive", "backup", "deduplicate"], "weight": 1.0},
}

_DOMAIN_PATTERNS = {
    "jobs": ["job", "jobs", "career", "hiring", "recruiter", "resume", "cv", "interview", "work", "employment", "business development", "position"],
    "software": ["app", "application", "software", "code", "api", "website", "frontend", "backend", "database", "server", "deploy"],
    "data": ["data", "dataset", "csv", "excel", "spreadsheet", "sql", "database", "analytics", "visualization", "chart", "graph", "table"],
    "research": ["research", "paper", "article", "study", "academic", "journal", "literature", "findings", "hypothesis"],
    "documents": ["document", "pdf", "word", "report", "presentation", "slides", "write", "draft", "essay", "letter", "email"],
    "filesystem": ["file", "folder", "directory", "disk", "path", "rename", "move", "copy", "delete", "organize"],
}

_OUTPUT_MAP = {
    "search": "list", "build": "application", "coding": "file",
    "summary": "summary", "analysis": "report", "automation": "action",
    "cleanup": "file", "unknown": "unknown",
}

_EXECUTION_MODE_MAP = {
    ("search", "low"): "single_step", ("search", "medium"): "multi_step", ("search", "high"): "multi_step",
    ("summary", "low"): "single_step", ("summary", "medium"): "multi_step",
    ("coding", "low"): "single_step", ("coding", "medium"): "multi_step", ("coding", "high"): "multi_step",
    ("build", "low"): "multi_step", ("build", "medium"): "long_running", ("build", "high"): "long_running",
    ("analysis", "low"): "single_step", ("analysis", "medium"): "multi_step", ("analysis", "high"): "long_running",
    ("automation", "low"): "multi_step", ("automation", "medium"): "multi_step", ("automation", "high"): "long_running",
    ("cleanup", "low"): "single_step", ("cleanup", "medium"): "multi_step", ("cleanup", "high"): "multi_step",
}

_TIME_ESTIMATES = {
    ("search", "low"): {"min_seconds": 5, "max_seconds": 30, "confidence": 0.8},
    ("search", "medium"): {"min_seconds": 15, "max_seconds": 90, "confidence": 0.6},
    ("search", "high"): {"min_seconds": 30, "max_seconds": 180, "confidence": 0.4},
    ("summary", "low"): {"min_seconds": 10, "max_seconds": 60, "confidence": 0.7},
    ("summary", "medium"): {"min_seconds": 30, "max_seconds": 120, "confidence": 0.5},
    ("summary", "high"): {"min_seconds": 60, "max_seconds": 300, "confidence": 0.4},
    ("coding", "low"): {"min_seconds": 5, "max_seconds": 30, "confidence": 0.8},
    ("coding", "medium"): {"min_seconds": 15, "max_seconds": 120, "confidence": 0.6},
    ("coding", "high"): {"min_seconds": 60, "max_seconds": 300, "confidence": 0.4},
    ("build", "low"): {"min_seconds": 30, "max_seconds": 120, "confidence": 0.6},
    ("build", "medium"): {"min_seconds": 60, "max_seconds": 300, "confidence": 0.4},
    ("build", "high"): {"min_seconds": 120, "max_seconds": 600, "confidence": 0.3},
    ("analysis", "low"): {"min_seconds": 10, "max_seconds": 60, "confidence": 0.7},
    ("analysis", "medium"): {"min_seconds": 30, "max_seconds": 180, "confidence": 0.5},
    ("analysis", "high"): {"min_seconds": 60, "max_seconds": 300, "confidence": 0.4},
    ("automation", "low"): {"min_seconds": 15, "max_seconds": 60, "confidence": 0.6},
    ("automation", "medium"): {"min_seconds": 30, "max_seconds": 180, "confidence": 0.5},
    ("automation", "high"): {"min_seconds": 60, "max_seconds": 300, "confidence": 0.4},
    ("cleanup", "low"): {"min_seconds": 5, "max_seconds": 30, "confidence": 0.8},
    ("cleanup", "medium"): {"min_seconds": 15, "max_seconds": 90, "confidence": 0.6},
    ("cleanup", "high"): {"min_seconds": 30, "max_seconds": 180, "confidence": 0.4},
}

_COMPLEXITY_DEFAULTS = {
    "search": "low", "summary": "low", "cleanup": "medium",
    "coding": "medium", "analysis": "medium", "automation": "medium",
    "build": "high", "unknown": "medium",
}


@dataclasses.dataclass
class TaskProfile:
    task_type: str
    intent: str
    domain: str
    keywords: list
    complexity: str
    expected_output: str
    execution_mode: str
    estimated_time: dict
    priority: str
    risk_level: str
    confidence: float
    routing_hint: str
    reason: str
    uncertainty_notes: str


def classify(user_input: str) -> TaskProfile:
    """Classify user input into a structured task profile."""
    if not user_input or not user_input.strip():
        return _build_unknown_profile("Empty input")

    normalized = _normalize_input(user_input)
    keywords = _extract_keywords(normalized)
    task_type, type_confidence = _detect_task_type(normalized, keywords)
    domain = _detect_domain(normalized, keywords)
    intent = _build_intent(user_input, task_type, domain)
    complexity = _estimate_complexity(task_type, keywords, normalized)
    expected_output = _infer_expected_output(task_type, domain)
    execution_mode = _determine_execution_mode(task_type, complexity)
    estimated_time = _estimate_time(task_type, complexity)
    priority = _assess_priority(normalized, task_type)
    risk_level = _assess_risk(task_type, normalized)
    confidence = _compute_confidence(type_confidence, len(keywords), len(normalized))
    routing_hint = _build_routing_hint(task_type)
    reason = f"Classified as '{task_type}' (domain: {domain}) based on {len(keywords)} keywords. Complexity: {complexity}."

    uncertainty_notes = ""
    if confidence < 0.5:
        uncertainty_notes = "Low confidence classification. Input is ambiguous."
    elif task_type == "unknown":
        uncertainty_notes = "Could not determine task type from input."

    return TaskProfile(
        task_type=task_type, intent=intent, domain=domain, keywords=keywords,
        complexity=complexity, expected_output=expected_output,
        execution_mode=execution_mode, estimated_time=estimated_time,
        priority=priority, risk_level=risk_level, confidence=confidence,
        routing_hint=routing_hint, reason=reason, uncertainty_notes=uncertainty_notes,
    )


def _normalize_input(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r'\s+', ' ', text)
    return text


def _extract_keywords(text: str) -> list:
    words = re.findall(r'[a-z]+', text)
    keywords = [w for w in words if w not in _STOPWORDS and len(w) > 2]
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique


def _detect_task_type(text: str, keywords: list) -> tuple:
    scores = {}
    for task_type, config in _TASK_TYPE_PATTERNS.items():
        pattern_keywords = config["keywords"]
        weight = config["weight"]
        phrase_matches = sum(1 for pk in pattern_keywords if pk in text)
        word_matches = sum(1 for kw in keywords if kw in pattern_keywords)
        score = (phrase_matches * 2 + word_matches) * weight
        if score > 0:
            scores[task_type] = score
    if not scores:
        return ("unknown", 0.3)
    best_type = max(scores, key=scores.get)
    best_score = scores[best_type]
    max_possible = len(keywords) * 3
    confidence = min(0.95, 0.4 + (best_score / max(max_possible, 1)) * 0.55)
    sorted_scores = sorted(scores.values(), reverse=True)
    if len(sorted_scores) >= 2 and sorted_scores[1] >= best_score * 0.7:
        confidence *= 0.8
    return (best_type, confidence)


def _detect_domain(text: str, keywords: list) -> str:
    scores = {}
    for domain, patterns in _DOMAIN_PATTERNS.items():
        score = sum(1 for p in patterns if p in text)
        word_score = sum(1 for kw in keywords if kw in patterns)
        total = score * 2 + word_score
        if total > 0:
            scores[domain] = total
    if not scores:
        return "general"
    return max(scores, key=scores.get)


def _build_intent(text: str, task_type: str, domain: str) -> str:
    clean = text.strip()
    if len(clean) > 100:
        clean = clean[:100] + "..."
    if task_type == "unknown":
        return f"Process user request: {clean}"
    return f"{task_type.title()} task in {domain} domain: {clean}"


def _estimate_complexity(task_type: str, keywords: list, text: str) -> str:
    base = _COMPLEXITY_DEFAULTS.get(task_type, "medium")
    word_count = len(text.split())
    upgrade_keywords = ["complex", "advanced", "full", "complete", "entire", "multiple", "multi", "database", "deploy", "production", "integration", "api", "authentication", "auth"]
    upgrade_count = sum(1 for kw in keywords if kw in upgrade_keywords)
    downgrade_keywords = ["simple", "basic", "quick", "small", "tiny", "minimal", "hello", "easy", "single", "one"]
    downgrade_count = sum(1 for kw in keywords if kw in downgrade_keywords)
    if base == "low":
        if upgrade_count >= 2 or word_count > 30:
            return "medium"
    elif base == "medium":
        if upgrade_count >= 2 or word_count > 50:
            return "high"
        if downgrade_count >= 2:
            return "low"
    elif base == "high":
        if downgrade_count >= 2 and word_count < 15:
            return "medium"
    return base


def _infer_expected_output(task_type: str, domain: str) -> str:
    return _OUTPUT_MAP.get(task_type, "unknown")


def _determine_execution_mode(task_type: str, complexity: str) -> str:
    return _EXECUTION_MODE_MAP.get((task_type, complexity), "multi_step")


def _estimate_time(task_type: str, complexity: str) -> dict:
    return _TIME_ESTIMATES.get((task_type, complexity), {"min_seconds": 10, "max_seconds": 120, "confidence": 0.3})


def _assess_priority(text: str, task_type: str) -> str:
    urgent_words = ["urgent", "asap", "immediately", "critical", "emergency", "now"]
    if any(w in text for w in urgent_words):
        return "high"
    return "normal"


def _assess_risk(task_type: str, text: str) -> str:
    if task_type in ("build", "automation"):
        return "medium"
    if task_type == "cleanup":
        dangerous = ["delete", "remove", "format", "destroy"]
        if any(d in text for d in dangerous):
            return "high"
        return "medium"
    return "low"


def _compute_confidence(type_confidence: float, keyword_count: int, text_length: int) -> float:
    base = type_confidence
    if keyword_count >= 5:
        base = min(0.95, base + 0.1)
    elif keyword_count >= 3:
        base = min(0.95, base + 0.05)
    elif keyword_count <= 1:
        base = max(0.2, base - 0.15)
    if text_length < 10:
        base = max(0.2, base - 0.1)
    return max(0.1, min(0.95, base))


def _build_routing_hint(task_type: str) -> str:
    if task_type in ("search", "build", "analysis", "automation", "coding"):
        return task_type
    return "unknown"


def _build_unknown_profile(reason: str) -> TaskProfile:
    return TaskProfile(
        task_type="unknown", intent="Unknown task", domain="unknown", keywords=[],
        complexity="medium", expected_output="unknown", execution_mode="multi_step",
        estimated_time={"min_seconds": 10, "max_seconds": 120, "confidence": 0.2},
        priority="normal", risk_level="low", confidence=0.1, routing_hint="unknown",
        reason=reason, uncertainty_notes="Could not classify input.",
    )