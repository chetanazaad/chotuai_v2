"""Browser Agent — Playwright-based browser automation."""
import dataclasses
import time
import urllib.parse
from typing import Optional


@dataclasses.dataclass
class BrowserResult:
    success: bool
    url: str
    title: str
    extracted_text: str
    extracted_links: list
    screenshot_path: str
    duration_ms: int
    error: str


_browser = None
_page = None
_nav_count = 0
_MAX_NAVIGATIONS = 20
_DEFAULT_TIMEOUT_MS = 15000
_MAX_TEXT_LENGTH = 50000


def _ensure_browser():
    global _browser, _page, _nav_count
    if _page is not None:
        return

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright not installed. Run: pip install playwright && playwright install chromium"
        )

    pw = sync_playwright().start()
    _browser = pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
        ]
    )
    _page = _browser.new_page(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 720},
    )
    _page.set_default_timeout(_DEFAULT_TIMEOUT_MS)
    _nav_count = 0


def close():
    global _browser, _page, _nav_count
    try:
        if _page:
            _page.close()
        if _browser:
            _browser.close()
    except Exception:
        pass
    _browser = None
    _page = None
    _nav_count = 0


def is_available() -> bool:
    try:
        import playwright
        return True
    except ImportError:
        return False


def _check_nav_limit():
    global _nav_count
    if _nav_count >= _MAX_NAVIGATIONS:
        raise RuntimeError(f"Navigation limit reached ({_MAX_NAVIGATIONS} pages). Close and restart.")


def _safe_title() -> str:
    try:
        return _page.title() if _page else ""
    except Exception:
        return ""


def _safe_extract_visible_text() -> str:
    try:
        return _page.inner_text("body")[:_MAX_TEXT_LENGTH]
    except Exception:
        return ""


def _safe_extract_links() -> list:
    links = []
    try:
        anchors = _page.query_selector_all("a[href]")
        for a in anchors[:100]:
            try:
                href = a.get_attribute("href") or ""
                text = a.inner_text() or ""
                if not href or href.startswith("#") or href.startswith("javascript:"):
                    continue
                if not text.strip():
                    text = href[:60]
                links.append({"text": text.strip()[:100], "href": href.strip()})
            except Exception:
                continue
    except Exception:
        pass
    return links


def open_url(url: str, timeout_ms: int = _DEFAULT_TIMEOUT_MS) -> BrowserResult:
    global _nav_count
    start = time.perf_counter()

    try:
        _ensure_browser()
        _check_nav_limit()

        _page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        _nav_count += 1

        title = _safe_title()
        text = _safe_extract_visible_text()
        links = _safe_extract_links()
        duration = int((time.perf_counter() - start) * 1000)

        return BrowserResult(
            success=True, url=_page.url, title=title,
            extracted_text=text[:_MAX_TEXT_LENGTH],
            extracted_links=links[:50],
            screenshot_path="", duration_ms=duration, error=""
        )
    except Exception as e:
        duration = int((time.perf_counter() - start) * 1000)
        return BrowserResult(
            success=False, url=url, title="",
            extracted_text="", extracted_links=[],
            screenshot_path="", duration_ms=duration, error=str(e)
        )


def search_google(query: str, timeout_ms: int = _DEFAULT_TIMEOUT_MS) -> BrowserResult:
    global _nav_count
    start = time.perf_counter()

    try:
        _ensure_browser()
        _check_nav_limit()

        encoded = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?q={encoded}&hl=en"

        _page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        _nav_count += 1

        try:
            _page.wait_for_selector("#search", timeout=5000)
        except Exception:
            pass

        results = _extract_google_results()
        links = _safe_extract_links()
        title = _safe_title()
        duration = int((time.perf_counter() - start) * 1000)

        result_text = _format_search_results(results, query)

        return BrowserResult(
            success=True, url=_page.url, title=title,
            extracted_text=result_text,
            extracted_links=links[:30],
            screenshot_path="", duration_ms=duration, error=""
        )
    except Exception as e:
        duration = int((time.perf_counter() - start) * 1000)
        return BrowserResult(
            success=False, url="", title="",
            extracted_text="", extracted_links=[],
            screenshot_path="", duration_ms=duration, error=str(e)
        )


def _extract_google_results() -> list:
    results = []
    try:
        containers = _page.query_selector_all("#search .g")
        for container in containers[:10]:
            try:
                title_el = container.query_selector("h3")
                title = title_el.inner_text() if title_el else ""
                link_el = container.query_selector("a")
                href = link_el.get_attribute("href") if link_el else ""
                snippet_els = container.query_selector_all("[data-sncf], .VwiC3b")
                snippet = snippet_els[0].inner_text() if snippet_els else ""
                if title and href:
                    results.append({
                        "title": title.strip(),
                        "link": href.strip(),
                        "snippet": (snippet or "").strip()[:300],
                    })
            except Exception:
                continue
    except Exception:
        pass
    return results


def _format_search_results(results: list, query: str) -> str:
    import json
    output = {
        "query": query,
        "result_count": len(results),
        "results": results[:10],
    }
    return json.dumps(output, indent=2, ensure_ascii=False)


def click(selector_or_text: str, timeout_ms: int = _DEFAULT_TIMEOUT_MS) -> BrowserResult:
    start = time.perf_counter()
    try:
        _ensure_browser()

        try:
            _page.click(selector_or_text, timeout=timeout_ms)
        except Exception:
            _page.get_by_text(selector_or_text, exact=False).first.click(timeout=timeout_ms)

        _page.wait_for_load_state("domcontentloaded", timeout=5000)

        title = _safe_title()
        text = _safe_extract_visible_text()
        duration = int((time.perf_counter() - start) * 1000)

        return BrowserResult(
            success=True, url=_page.url, title=title,
            extracted_text=text[:_MAX_TEXT_LENGTH],
            extracted_links=[], screenshot_path="",
            duration_ms=duration, error=""
        )
    except Exception as e:
        duration = int((time.perf_counter() - start) * 1000)
        return BrowserResult(
            success=False, url=_page.url if _page else "", title="",
            extracted_text="", extracted_links=[], screenshot_path="",
            duration_ms=duration, error=str(e)
        )


def type_text(selector: str, text: str, timeout_ms: int = _DEFAULT_TIMEOUT_MS) -> BrowserResult:
    start = time.perf_counter()
    try:
        _ensure_browser()
        _page.fill(selector, text, timeout=timeout_ms)
        duration = int((time.perf_counter() - start) * 1000)

        return BrowserResult(
            success=True, url=_page.url, title=_safe_title(),
            extracted_text=f"Typed '{text}' into {selector}",
            extracted_links=[], screenshot_path="",
            duration_ms=duration, error=""
        )
    except Exception as e:
        duration = int((time.perf_counter() - start) * 1000)
        return BrowserResult(
            success=False, url=_page.url if _page else "", title="",
            extracted_text="", extracted_links=[], screenshot_path="",
            duration_ms=duration, error=str(e)
        )


def extract_text(selector: str = "body", timeout_ms: int = _DEFAULT_TIMEOUT_MS) -> BrowserResult:
    start = time.perf_counter()
    try:
        _ensure_browser()
        elements = _page.query_selector_all(selector)
        texts = []
        for el in elements[:20]:
            try:
                t = el.inner_text()
                if t and t.strip():
                    texts.append(t.strip())
            except Exception:
                continue

        combined = "\n".join(texts)
        duration = int((time.perf_counter() - start) * 1000)

        return BrowserResult(
            success=True, url=_page.url, title=_safe_title(),
            extracted_text=combined[:_MAX_TEXT_LENGTH],
            extracted_links=[], screenshot_path="",
            duration_ms=duration, error=""
        )
    except Exception as e:
        duration = int((time.perf_counter() - start) * 1000)
        return BrowserResult(
            success=False, url=_page.url if _page else "", title="",
            extracted_text="", extracted_links=[], screenshot_path="",
            duration_ms=duration, error=str(e)
        )


def extract_links(timeout_ms: int = _DEFAULT_TIMEOUT_MS) -> BrowserResult:
    start = time.perf_counter()
    try:
        _ensure_browser()
        links = _safe_extract_links()
        duration = int((time.perf_counter() - start) * 1000)

        link_text = "\n".join(f"{l['text']}: {l['href']}" for l in links[:50])

        return BrowserResult(
            success=True, url=_page.url, title=_safe_title(),
            extracted_text=link_text,
            extracted_links=links[:50], screenshot_path="",
            duration_ms=duration, error=""
        )
    except Exception as e:
        duration = int((time.perf_counter() - start) * 1000)
        return BrowserResult(
            success=False, url="", title="",
            extracted_text="", extracted_links=[], screenshot_path="",
            duration_ms=duration, error=str(e)
        )


def wait_for(selector: str, timeout_ms: int = _DEFAULT_TIMEOUT_MS) -> BrowserResult:
    start = time.perf_counter()
    try:
        _ensure_browser()
        _page.wait_for_selector(selector, timeout=timeout_ms)
        duration = int((time.perf_counter() - start) * 1000)

        return BrowserResult(
            success=True, url=_page.url, title=_safe_title(),
            extracted_text=f"Element '{selector}' found",
            extracted_links=[], screenshot_path="",
            duration_ms=duration, error=""
        )
    except Exception as e:
        duration = int((time.perf_counter() - start) * 1000)
        return BrowserResult(
            success=False, url=_page.url if _page else "", title="",
            extracted_text="", extracted_links=[], screenshot_path="",
            duration_ms=duration, error=str(e)
        )


def screenshot(path: str = "") -> BrowserResult:
    start = time.perf_counter()
    try:
        _ensure_browser()
        if not path:
            path = f"screenshot_{int(time.time())}.png"

        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        _page.screenshot(path=path, full_page=False)
        duration = int((time.perf_counter() - start) * 1000)

        return BrowserResult(
            success=True, url=_page.url, title=_safe_title(),
            extracted_text=f"Screenshot saved to {path}",
            extracted_links=[], screenshot_path=path,
            duration_ms=duration, error=""
        )
    except Exception as e:
        duration = int((time.perf_counter() - start) * 1000)
        return BrowserResult(
            success=False, url="", title="",
            extracted_text="", extracted_links=[], screenshot_path="",
            duration_ms=duration, error=str(e)
        )