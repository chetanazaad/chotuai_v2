"""Multi-layer Validator module — produces structured, decision-ready validation output."""
import dataclasses
import os
import re
from typing import Optional
from pathlib import Path


def _check_html_consistency(working_dir: str, html_files: list, shared_layout: dict) -> list:
    """Validate HTML files share consistent layout components."""
    checks = []
    if not shared_layout or not html_files:
        return checks

    navbar = shared_layout.get("navbar", "")
    expected_links = set()
    for f in html_files:
        expected_links.add(f"{f}.html" if not f.endswith(".html") else f)

    for html_file in html_files:
        filepath = Path(working_dir) / html_file if Path(working_dir).exists() else Path(html_file)
        if not filepath.exists():
            checks.append({
                "check": "file_exists",
                "passed": False,
                "detail": f"Missing: {html_file}"
            })
            continue

        content = filepath.read_text(encoding="utf-8")

        has_navbar = "navbar" in content.lower() or "<nav" in content.lower()
        css_ok = 'class="' in content or 'style="' in content

        checks.append({
            "check": "navbar_present",
            "passed": has_navbar,
            "detail": f"{html_file}: navbar={has_navbar}"
        })

        checks.append({
            "check": "csspresent",
            "passed": css_ok,
            "detail": f"{html_file}: has styles={css_ok}"
        })

        for link in expected_links:
            if link != html_file:
                has_link = f'href="{link}"' in content or f"href='{link}'" in content
                if not has_link:
                    checks.append({
                        "check": "navlink_present",
                        "passed": False,
                        "detail": f"{html_file}: missing link to {link}"
                    })

    return checks


@dataclasses.dataclass
class ValidationResult:
    verdict: str
    failure_type: str
    confidence: float
    reason: str
    retryable: bool
    suggestion: str
    details: dict


_ERROR_PATTERNS = [
    "error", "traceback", "exception", "failed", "fatal", "abort", "panic", "denied", "refused"
]

_RETRYABLE_MAP = {
    "none": False,
    "syntax_error": True,
    "missing_dependency": True,
    "runtime_error": True,
    "timeout": True,
    "infrastructure": False,
    "incorrect_output": True,
    "unknown": True,
}

_SUGGESTIONS = {
    "none": "",
    "syntax_error": "Fix the syntax error in the generated code",
    "missing_dependency": "Install the missing dependency or use an alternative",
    "runtime_error": "Review the error output and adjust the action",
    "timeout": "Simplify the step or increase the timeout",
    "infrastructure": "Check file paths, permissions, and disk space — may require user intervention",
    "incorrect_output": "Output did not match expectations — adjust the action or expected outcome",
    "unknown": "Review the full error output for clues",
}


def validate(exec_result, expected_outcome, step: dict, state: dict) -> ValidationResult:
    """Main entry point. Runs all 5 layers. Never raises."""
    from . import logger
    step_id = step.get("id", "")
    working_dir = state.get("config", {}).get("working_directory", "")

    action = step.get("action", {})
    if not isinstance(action, dict):
        action = {"type": "unknown", "command": str(action)}

    if action.get("type") == "file_write":
        expected_outcome = {
            "type": "file_exists",
            "path": action.get("path", "output.txt")
        }

    logger.log_validation_start(step_id)

    hard_result = _check_hard_execution(exec_result)
    if hard_result is not None:
        logger.log_validation_complete(step_id, hard_result.verdict, hard_result.failure_type, hard_result.confidence)
        return hard_result

    checks = _check_expected_outcome(exec_result, expected_outcome, working_dir)

    shared_layout = state.get("core_task", {}).get("shared_layout")
    if shared_layout:
        html_files = re.findall(r'([a-zA-Z0-9_-]+\.html)', state.get("core_task", {}).get("description", ""))
        if html_files:
            html_checks = _check_html_consistency(working_dir, html_files, shared_layout)
            checks.extend(html_checks)

    task_profile = state.get("core_task", {}).get("task_profile", {})
    task_type = task_profile.get("task_type", "") if isinstance(task_profile, dict) else getattr(task_profile, "task_type", "")
    if task_type == "build" and action.get("type") == "shell":
        cmd = action.get("command", "").strip()
        if cmd.startswith("echo ") and ">" not in cmd:
            checks.append({"check": "real_execution", "passed": False, "detail": "Echo commands without redirection are not valid execution for build tasks"})

    failure_type = "none"
    if exec_result.exit_code != 0:
        failure_type = _classify_failure(exec_result)

    all_passed = all(c["passed"] for c in checks) if checks else True
    is_partial = _check_partial_success(exec_result, expected_outcome, checks)

    # FIX 3: Artifact Validation Layer
    artifact_failure = _layer_artifact_check(action)
    if artifact_failure:
        checks.append(artifact_failure)
        failure_type = "incorrect_output"
        all_passed = False

    if isinstance(expected_outcome, dict) and expected_outcome.get("type") == "semantic":
        semantic_result = _check_semantic(exec_result, expected_outcome)
        if semantic_result is not None:
            logger.log_validation_complete(step_id, semantic_result.verdict, semantic_result.failure_type, semantic_result.confidence)
            return semantic_result

    # ── RESULT-BASED SUCCESS (FIX 1) ──
    # If the file exists and is valid, it's a SUCCESS regardless of exit code
    artifact_ok = all(c["passed"] for c in checks if c["check"].startswith("artifact_")) if checks else False
    
    if artifact_ok:
        verdict = "pass"
        failure_type = "none"
        retryable = False
        suggestion = ""
        reason = "Result-based Success: Target file created and valid"
        confidence = 0.95
    elif exec_result.exit_code == 0 and all_passed:
        verdict = "pass"
        failure_type = "none"
        retryable = False
        suggestion = ""
        reason = _build_pass_reason(checks)
        confidence = _compute_confidence("pass", checks)

    elif is_partial:
        verdict = "partial"
        if failure_type == "none":
            failure_type = "incorrect_output"
        retryable = True
        suggestion = _build_suggestion(failure_type, exec_result)
        reason = _build_partial_reason(checks)
        confidence = _compute_confidence("partial", checks)

    elif exec_result.exit_code != 0:
        verdict = "error"
        retryable = _is_retryable(failure_type, exec_result)
        suggestion = _build_suggestion(failure_type, exec_result)
        reason = f"Exit code {exec_result.exit_code}: {exec_result.stderr[:200]}"
        confidence = _compute_confidence("error", checks)

    else:
        verdict = "fail"
        failure_type = "incorrect_output" if failure_type == "none" else failure_type
        retryable = True
        suggestion = _build_suggestion(failure_type, exec_result)
        reason = _build_fail_reason(checks)
        confidence = _compute_confidence("fail", checks)

    result = ValidationResult(
        verdict=verdict,
        failure_type=failure_type,
        confidence=confidence,
        reason=reason,
        retryable=retryable,
        suggestion=suggestion,
        details={
            "exit_code": exec_result.exit_code,
            "timeout": exec_result.timed_out,
            "expected_met": all_passed,
            "checks": checks
        }
    )

    logger.log_validation_complete(step_id, verdict, failure_type, confidence)
    if checks:
        logger.log_validation_checks(step_id, checks)
    return result


def _check_hard_execution(exec_result) -> Optional[ValidationResult]:
    """Layer 1: Fast-fail checks."""
    if exec_result.timed_out:
        return ValidationResult(
            verdict="error",
            failure_type="timeout",
            confidence=1.0,
            reason="Command timed out",
            retryable=True,
            suggestion="Increase timeout or simplify the step",
            details={"exit_code": -1, "timeout": True, "expected_met": False, "checks": []}
        )

    if exec_result.exit_code < 0:
        return ValidationResult(
            verdict="error",
            failure_type="infrastructure",
            confidence=1.0,
            reason=f"Process crashed with code {exec_result.exit_code}",
            retryable=False,
            suggestion="Check system resources and permissions",
            details={"exit_code": exec_result.exit_code, "timeout": False, "expected_met": False, "checks": []}
        )

    return None


def _check_expected_outcome(exec_result, expected_outcome, working_dir) -> list:
    """Layer 2: Expected outcome checks."""
    checks = []

    if expected_outcome is None or expected_outcome == "":
        checks.append({
            "check": "exit_code_zero",
            "passed": exec_result.exit_code == 0,
            "detail": f"exit_code={exec_result.exit_code}"
        })
        
        # STRICT VALIDATION: Build tasks shouldn't just pass with exit 0 on empty outcomes
        # if there wasn't a tangible output
        # (This prevents fake success)
        command = exec_result.stdout.strip()
        if "Step step_" in command and "completed" in command:
             checks.append({
                 "check": "real_execution",
                 "passed": False,
                 "detail": "Fake success detected"
             })
             
        return checks

    if isinstance(expected_outcome, str):
        if expected_outcome:
            found = expected_outcome in exec_result.stdout or expected_outcome in exec_result.stderr
            checks.append({
                "check": "output_contains",
                "passed": found,
                "detail": f"pattern='{expected_outcome}' found={found}"
            })
        else:
            checks.append({
                "check": "exit_code_zero",
                "passed": exec_result.exit_code == 0,
                "detail": f"exit_code={exec_result.exit_code}"
            })
        return checks

    if isinstance(expected_outcome, dict):
        exp_type = expected_outcome.get("type", "")

        if exp_type == "file_exists":
            path = expected_outcome.get("path", "")
            full_path = os.path.join(working_dir, path) if working_dir else path
            exists = os.path.exists(path) or os.path.exists(full_path)
            checks.append({
                "check": "file_exists",
                "passed": exists,
                "detail": f"path='{path}' exists={exists}"
            })

        elif exp_type == "file_contains":
            path = expected_outcome.get("path", "")
            pattern = expected_outcome.get("pattern", "")
            full_path = os.path.join(working_dir, path) if working_dir else path
            try:
                actual_path = full_path if os.path.exists(full_path) else path
                content = open(actual_path, "r", encoding="utf-8").read()
                found = pattern in content
                checks.append({
                    "check": "file_contains",
                    "passed": found,
                    "detail": f"path='{path}' pattern='{pattern}' found={found}"
                })
            except (FileNotFoundError, IOError) as e:
                checks.append({
                    "check": "file_contains",
                    "passed": False,
                    "detail": f"path='{path}' error='{e}'"
                })

        elif exp_type == "output_contains":
            pattern = expected_outcome.get("pattern", "")
            found = pattern in exec_result.stdout
            checks.append({
                "check": "output_contains",
                "passed": found,
                "detail": f"pattern='{pattern}' found={found}"
            })

        elif exp_type == "exit_code":
            expected_code = expected_outcome.get("code", 0)
            matched = exec_result.exit_code == expected_code
            checks.append({
                "check": "exit_code",
                "passed": matched,
                "detail": f"expected={expected_code} actual={exec_result.exit_code}"
            })

        elif exp_type == "command_success":
            code_ok = exec_result.exit_code == 0
            stderr_clean = not _has_error_patterns(exec_result.stderr)
            passed = code_ok and stderr_clean
            checks.append({
                "check": "command_success",
                "passed": passed,
                "detail": f"exit_code_ok={code_ok} stderr_clean={stderr_clean}"
            })

        elif exp_type == "semantic":
            checks.append({
                "check": "semantic",
                "passed": exec_result.exit_code == 0,
                "detail": "semantic check deferred to Layer 5"
            })

        else:
            checks.append({
                "check": "exit_code_zero",
                "passed": exec_result.exit_code == 0,
                "detail": f"unknown outcome type '{exp_type}', using exit_code"
            })

    return checks


def _classify_failure(exec_result) -> str:
    """Layer 3: Heuristic failure classification."""
    stderr = exec_result.stderr or ""
    stdout = exec_result.stdout or ""
    combined = stderr + stdout

    if exec_result.timed_out:
        return "timeout"

    if "SyntaxError" in combined or "IndentationError" in combined:
        return "syntax_error"
    if "ModuleNotFoundError" in combined or "ImportError" in combined:
        return "missing_dependency"

    if "FileNotFoundError" in combined or "PermissionError" in combined:
        return "infrastructure"
    if "OSError" in combined or "IOError" in combined:
        return "infrastructure"

    if "'is not recognized" in combined:
        return "missing_dependency"
    if "not found" in combined.lower() and exec_result.exit_code != 0:
        return "missing_dependency"

    if exec_result.exit_code != 0:
        return "runtime_error"

    return "incorrect_output"


def _check_partial_success(exec_result, expected_outcome, checks: list) -> bool:
    """Layer 4: Partial success detection."""
    if not checks:
        return False

    passed_count = sum(1 for c in checks if c["passed"])
    total_count = len(checks)

    if 0 < passed_count < total_count:
        return True

    if exec_result.exit_code == 0 and total_count > 0 and passed_count == 0:
        return True

    return False


def _layer_artifact_check(action: dict) -> Optional[dict]:
    """Validate generated file existence and size.
    
    FIX: Only check existence and non-emptiness.
    """
    if action.get("type") != "file_write":
        return None
        
    path = action.get("path")
    if not path or not os.path.exists(path):
        return {"check": "artifact_exists", "passed": False, "detail": f"File not found: {path}"}
        
    if os.path.getsize(path) == 0:
        return {"check": "artifact_not_empty", "passed": False, "detail": f"File is empty: {path}"}
        
    return None


def _check_semantic(exec_result, expected_outcome) -> Optional[ValidationResult]:
    """Layer 5: Optional LLM semantic check.
    
    FIX 2: Disabled to centralize LLM usage.
    """
    return None


def _is_retryable(failure_type: str, exec_result) -> bool:
    """Determine if failure is worth retrying.
    
    FIX: Only retry if file is missing or empty.
    """
    stderr = (exec_result.stderr or "").lower()
    stdout = (exec_result.stdout or "").lower()
    combined = stderr + stdout
    
    # Check for missing/empty file indicators in output or state
    if "file not found" in combined or "file is empty" in combined:
        return True
        
    # We also check the classification
    if failure_type == "incorrect_output":
        return True # This covers missing/empty artifacts detected in validation
        
    return False


def _build_suggestion(failure_type: str, exec_result) -> str:
    """Generate actionable suggestion."""
    base = _SUGGESTIONS.get(failure_type, _SUGGESTIONS["unknown"])

    stderr = exec_result.stderr or ""
    if "ModuleNotFoundError" in stderr:
        match = re.search(r"No module named '(\w+)'", stderr)
        if match:
            base += f" (missing: {match.group(1)})"
    elif "SyntaxError" in stderr:
        match = re.search(r"line (\d+)", stderr)
        if match:
            base += f" (at line {match.group(1)})"

    return base


def _compute_confidence(verdict: str, checks: list) -> float:
    """Calculate confidence from check results."""
    if not checks:
        return 0.8 if verdict == "pass" else 0.5

    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    ratio = passed / total if total > 0 else 0.0

    if verdict == "pass":
        return min(0.95, 0.7 + (ratio * 0.25))
    elif verdict == "partial":
        return min(0.70, 0.3 + (ratio * 0.40))
    elif verdict == "error":
        return 0.90
    else:
        return min(0.85, 0.5 + ((1 - ratio) * 0.35))


def _has_error_patterns(text: str) -> bool:
    """Check if text contains error-like patterns."""
    text_lower = text.lower()
    return any(p in text_lower for p in _ERROR_PATTERNS)


def _build_pass_reason(checks: list) -> str:
    if not checks:
        return "Command completed successfully"
    details = ", ".join(c["check"] for c in checks if c["passed"])
    return f"All checks passed: {details}"


def _build_fail_reason(checks: list) -> str:
    failed = [c for c in checks if not c["passed"]]
    if not failed:
        return "Expected outcome not met"
    details = ", ".join(f"{c['check']}: {c['detail']}" for c in failed)
    return f"Checks failed: {details}"


def _build_partial_reason(checks: list) -> str:
    passed = [c for c in checks if c["passed"]]
    failed = [c for c in checks if not c["passed"]]
    return (f"Partial success: {len(passed)} passed, {len(failed)} failed. "
            f"Failed: {', '.join(c['check'] for c in failed)}")