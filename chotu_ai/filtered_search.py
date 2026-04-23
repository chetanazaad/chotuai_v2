"""Filtered Search Engine — Knowledge retrieval module for error resolution."""
import dataclasses
import platform
import re
import urllib.parse
import urllib.request
from typing import Optional

_DDG_TIMEOUT = 5


@dataclasses.dataclass
class SearchRequest:
    query: str
    context: dict = None
    max_results: int = 5


@dataclasses.dataclass
class SearchResultItem:
    title: str
    snippet: str
    source: str
    relevance_score: float
    confidence: float
    actionable: bool
    extracted_solution: str


@dataclasses.dataclass
class SearchResponse:
    results: list
    best_result: dict
    confidence: float
    source: str
    query_used: str
    success: bool
    error: str


def search(request: SearchRequest) -> SearchResponse:
    """Main entry point. Searches, filters, ranks, extracts. Never raises."""
    from . import logger

    logger.log_search_start(request.query)

    all_results = []

    try:
        llm_results = _search_via_llm(request.query, request.context)
        all_results.extend(llm_results)
    except Exception:
        pass

    try:
        ddg_results = _search_via_duckduckgo(request.query)
        all_results.extend(ddg_results)
    except Exception:
        pass

    if not all_results:
        logger.log_search_failure(request.query, "No results from any source")
        return SearchResponse(
            results=[], best_result={}, confidence=0.0,
            source="none", query_used=request.query,
            success=False, error="No results found"
        )

    filtered = _filter_results(all_results, request.context)
    logger.log_search_filter(request.query, len(all_results), len(filtered))

    if not filtered:
        logger.log_search_failure(request.query, "All results filtered out")
        return SearchResponse(
            results=[], best_result={}, confidence=0.0,
            source="none", query_used=request.query,
            success=False, error="All results filtered as irrelevant"
        )

    scored = _rank_results(filtered, request.context)
    scored = scored[:request.max_results]
    scored = _apply_search_guardrails(scored)

    best = scored[0] if scored else None
    source = best.source if best else "none"
    if any(r.source == "llm" for r in scored) and any(r.source == "duckduckgo" for r in scored):
        source = "mixed"

    best_dict = dataclasses.asdict(best) if best else {}
    confidence = best.confidence if best else 0.0

    logger.log_search_success(request.query, len(scored), confidence)

    return SearchResponse(
        results=scored,
        best_result=best_dict,
        confidence=confidence,
        source=source,
        query_used=request.query,
        success=True,
        error=""
    )


def build_query(val_result, step: dict, state: dict) -> str:
    """Build optimized search query from error context."""
    failure_type = val_result.failure_type
    reason = val_result.reason[:150] if val_result.reason else ""
    step_desc = step.get("description", "")[:80]

    parts = [failure_type.replace("_", " ")]

    if "ModuleNotFoundError" in reason or "No module named" in reason:
        match = re.search(r"No module named '(\w+)'", reason)
        if match:
            parts.append(f"install {match.group(1)}")
            parts.append("python pip")

    elif "SyntaxError" in reason:
        parts.append("python syntax fix")

    elif "PermissionError" in reason or "access denied" in reason.lower():
        parts.append("permission denied fix")
        parts.append(platform.system())

    elif "FileNotFoundError" in reason:
        match = re.search(r"'(.+?)'", reason)
        if match:
            parts.append(f"file not found {match.group(1)}")

    elif "timeout" in failure_type:
        parts.append("command timeout solution")

    else:
        clean_reason = re.sub(r'[^\w\s]', ' ', reason[:100])
        parts.append(clean_reason.strip())

    if "python" in step_desc.lower() or ".py" in step_desc:
        parts.append("python")
    elif "shell" in step_desc.lower() or "cmd" in step_desc:
        parts.append(platform.system() + " command line")

    parts.append(platform.system())

    query = " ".join(parts)
    if len(query) > 200:
        query = query[:200]

    return query.strip()


def build_search_request(val_result, step: dict, state: dict) -> SearchRequest:
    """Build complete SearchRequest from error context."""
    query = build_query(val_result, step, state)
    context = {
        "task": state.get("core_task", {}).get("description", ""),
        "error_type": val_result.failure_type,
        "step_description": step.get("description", ""),
        "stderr_snippet": val_result.reason[:200] if val_result.reason else "",
        "previous_attempts": step.get("retries", 0),
    }
    return SearchRequest(query=query, context=context, max_results=5)


def _search_via_llm(query: str, context: dict) -> list:
    """Ask the LLM to generate solutions for the given error."""
    from . import llm_gateway

    prompt = _build_llm_search_prompt(query, context)
    request = llm_gateway.GatewayRequest(
        purpose="reasoning",
        prompt=prompt,
        task_type="structured",
        escalation_level=1,
        max_tokens=2048,
    )

    response = llm_gateway.generate(request)
    if not response.success:
        return []

    return _parse_llm_search_results(response.text, response.raw_output)


def _build_llm_search_prompt(query: str, context: dict) -> str:
    error_type = context.get("error_type", "unknown")
    step_desc = context.get("step_description", "")
    stderr = context.get("stderr_snippet", "")

    return f"""You are a technical troubleshooter. A command has failed.

ERROR TYPE: {error_type}
STEP: {step_desc}
ERROR DETAILS: {stderr}
SEARCH QUERY: {query}

Provide exactly 3 possible solutions. For each solution, give:
1. A short title
2. A brief explanation
3. The exact command or code fix

Format your response as a JSON array:
[
  {{"title": "...", "explanation": "...", "command": "..."}},
  {{"title": "...", "explanation": "...", "command": "..."}},
  {{"title": "...", "explanation": "...", "command": "..."}}
]

Output ONLY the JSON array. No other text."""


def _parse_llm_search_results(text: str, raw: str) -> list:
    """Parse LLM response into SearchResultItem list."""
    import json

    results = []

    try:
        items = json.loads(text)
        if not isinstance(items, list):
            items = [items]
    except json.JSONDecodeError:
        arr_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if arr_match:
            try:
                items = json.loads(arr_match.group())
            except json.JSONDecodeError:
                items = []
        else:
            items = [{"title": "LLM suggestion", "explanation": text[:300], "command": ""}]

    for item in items:
        if not isinstance(item, dict):
            continue

        title = item.get("title", "Solution")
        explanation = item.get("explanation", "")
        command = item.get("command", "")
        snippet = f"{explanation}\n{command}".strip()

        results.append(SearchResultItem(
            title=title,
            snippet=snippet,
            source="llm",
            relevance_score=0.0,
            confidence=0.0,
            actionable=bool(command),
            extracted_solution=command if command else explanation[:200]
        ))

    return results


def _search_via_duckduckgo(query: str) -> list:
    """Query DuckDuckGo Instant Answers API."""
    import json

    results = []

    try:
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://api.duckduckgo.com/?q={encoded_query}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={"User-Agent": "chotu_ai/1.6"})

        with urllib.request.urlopen(req, timeout=_DDG_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        abstract = data.get("AbstractText", "")
        if abstract and len(abstract) > 20:
            results.append(SearchResultItem(
                title=data.get("Heading", "DuckDuckGo Answer"),
                snippet=abstract,
                source="duckduckgo",
                relevance_score=0.0,
                confidence=0.0,
                actionable=False,
                extracted_solution=abstract[:300]
            ))

        related = data.get("RelatedTopics", [])
        for topic in related[:5]:
            if isinstance(topic, dict) and "Text" in topic:
                text = topic.get("Text", "")
                if len(text) > 20:
                    results.append(SearchResultItem(
                        title=topic.get("FirstURL", "Related"),
                        snippet=text,
                        source="duckduckgo",
                        relevance_score=0.0,
                        confidence=0.0,
                        actionable=False,
                        extracted_solution=text[:200]
                    ))

    except Exception:
        pass

    return results


_NOISE_PATTERNS = [
    "advertisement", "sponsored", "click here", "subscribe",
    "sign up", "buy now", "free trial", "limited offer",
    "cookie", "privacy policy", "terms of service",
]

_GENERIC_PATTERNS = [
    "it depends", "there are many ways", "this can vary",
    "please contact support", "visit our website",
    "for more information see",
]


def _filter_results(results: list, context: dict) -> list:
    """Remove noise, keep relevant results."""
    filtered = []
    for item in results:
        if _is_noise(item):
            continue
        if _is_too_short(item):
            continue
        if _is_too_generic(item):
            continue
        filtered.append(item)
    return filtered


def _is_noise(item) -> bool:
    """Detect ads, spam, irrelevant content."""
    text = (item.snippet + " " + item.title).lower()
    return any(pat in text for pat in _NOISE_PATTERNS)


def _is_too_short(item) -> bool:
    """Reject snippets under 20 characters."""
    return len(item.snippet.strip()) < 20


def _is_too_generic(item) -> bool:
    """Reject vague, unhelpful answers."""
    text = item.snippet.lower()
    return any(pat in text for pat in _GENERIC_PATTERNS)


def _rank_results(results: list, context: dict) -> list:
    """Score and sort results by relevance."""
    for item in results:
        item.relevance_score = _score_relevance(item, context)
        item.confidence = _compute_result_confidence(item)

    results.sort(key=lambda r: r.relevance_score, reverse=True)
    return results


def _score_relevance(item, context: dict) -> float:
    """Weighted relevance score."""
    text = item.snippet.lower()
    error_type = context.get("error_type", "").lower().replace("_", " ")
    step_desc = context.get("step_description", "").lower()

    keywords = error_type.split() + step_desc.split()[:5]
    keywords = [k for k in keywords if len(k) > 2]
    if keywords:
        matches = sum(1 for k in keywords if k in text)
        keyword_score = min(1.0, matches / max(len(keywords), 1))
    else:
        keyword_score = 0.3

    code_score = 1.0 if _has_code_snippet(item.snippet) else 0.0

    actionable_score = 1.0 if _has_actionable_steps(item.snippet) else 0.0
    item.actionable = actionable_score > 0

    clarity_score = _compute_clarity_score(item.snippet)

    source_score = 0.8 if item.source == "llm" else 0.5

    total = (keyword_score * 0.30 +
             code_score * 0.25 +
             actionable_score * 0.25 +
             clarity_score * 0.10 +
             source_score * 0.10)

    return min(1.0, total)


def _has_code_snippet(text: str) -> bool:
    """Check if text contains code or commands."""
    indicators = [
        "```", "pip install", "npm install",
        "import ", "from ", "def ", "class ",
        "$ ", "> ", "cmd ", "powershell",
    ]
    if any(ind in text for ind in indicators):
        return True
    if re.search(r'[a-z_]+\([^)]*\)', text):
        return True
    return False


def _has_actionable_steps(text: str) -> bool:
    """Check for numbered/bulleted steps."""
    if re.search(r'(?:^|\n)\s*(?:\d+[.)]|Step \d+|[-*•])\s+', text):
        return True
    if re.search(r'(?:^|\n)\s*(?:Run|Execute|Install|Type|Enter|Use)\s+', text, re.IGNORECASE):
        return True
    return False


def _compute_clarity_score(text: str) -> float:
    """Score based on length and structure."""
    length = len(text)
    if length < 30:
        return 0.2
    if length < 100:
        return 0.5
    if length < 500:
        return 0.8
    if length < 1500:
        return 1.0
    return 0.7


def _compute_result_confidence(item) -> float:
    """Confidence in the individual result."""
    base = item.relevance_score

    if item.actionable:
        base = min(1.0, base + 0.1)
    if item.source == "llm":
        base = min(1.0, base + 0.05)

    return max(0.1, min(0.95, base))


def _extract_solution(text: str) -> str:
    """Extract the most actionable content from result text."""
    code_blocks = re.findall(r'```(?:\w+)?\n?(.*?)```', text, re.DOTALL)
    if code_blocks:
        return code_blocks[0].strip()

    commands = re.findall(r'(?:^|\n)\s*[$>]\s*(.+)', text)
    if commands:
        return "\n".join(commands).strip()

    installs = re.findall(r'((?:pip|npm|yarn|apt|brew)\s+install\s+\S+)', text, re.IGNORECASE)
    if installs:
        return installs[0].strip()

    run_cmds = re.findall(r'(?:Run|Execute|Type|Enter|Use)[:\s]+[`"]?([^`"\n]+)[`"]?', text, re.IGNORECASE)
    if run_cmds:
        return run_cmds[0].strip()

    sentences = text.split(".")
    for s in sentences:
        s = s.strip()
        if len(s) > 20:
            return s + "."

    return text[:200].strip()


def _apply_search_guardrails(results: list) -> list:
    """Safety checks — never let dangerous content through."""
    _DANGEROUS_PATTERNS = [
        r'rm\s+-rf\s+/',
        r'format\s+[a-zA-Z]:',
        r'del\s+/[sS]',
        r'DROP\s+TABLE',
        r'powershell.*-enc',
        r'curl.*\|\s*(?:bash|sh)',
    ]

    safe_results = []
    for item in results:
        text = item.extracted_solution + " " + item.snippet
        is_dangerous = any(re.search(p, text, re.IGNORECASE) for p in _DANGEROUS_PATTERNS)
        if is_dangerous:
            continue
        safe_results.append(item)

    return safe_results