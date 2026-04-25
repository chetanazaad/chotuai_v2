"""Evaluate execution results against expected outcomes. Return verdicts."""
import dataclasses
import os
import re
from typing import Optional


@dataclasses.dataclass
class EvaluationResult:
    verdict: str
    reason: str
    suggestion: str
    error_type: str


_ERROR_SUGGESTIONS = {
    "syntax_error": "Fix the syntax error in the generated code",
    "missing_dependency": "Install the missing dependency",
    "infrastructure": "Check file paths and permissions",
    "timeout": "Increase timeout or simplify the step",
    "runtime_error": "Review the error output and adjust the action",
    "unknown": "Review the full error output"
}


def validate_output_artifacts(exec_result, action: dict, state: dict) -> Optional[EvaluationResult]:
    """FIX 3: Validate generated file existence, size, and structure."""
    if action.get("type") != "file_write":
        return None
        
    path = action.get("path")
    if not path:
        return None
        
    if not os.path.exists(path):
        return EvaluationResult(
            verdict="fail",
            reason=f"File not found: {path}",
            suggestion="Verify file path and permissions",
            error_type="infrastructure"
        )
        
    if os.path.getsize(path) == 0:
        return EvaluationResult(
            verdict="fail",
            reason=f"File is empty: {path}",
            suggestion="Regenerate content with more detail",
            error_type="runtime_error"
        )
        
    # Check structure for common types
    try:
        content = Path(path).read_text(encoding="utf-8")
        if path.endswith(".html"):
            if "<html>" not in content.lower() or "<body>" not in content.lower():
                return EvaluationResult(
                    verdict="fail",
                    reason=f"Invalid HTML structure in {path}",
                    suggestion="Ensure <html> and <body> tags are present",
                    error_type="syntax_error"
                )
        if path.endswith(".js"):
            if len(content.strip()) < 10:
                return EvaluationResult(
                    verdict="fail",
                    reason=f"JS file too short/empty: {path}",
                    suggestion="Check LLM output for code blocks",
                    error_type="runtime_error"
                )
    except Exception:
        pass
        
    return None

def evaluate(exec_result, action: dict, expected_outcome: Optional[str], state: dict) -> EvaluationResult:
    """Main evaluation entry point.
    
    FIX 3: Integrated output validation.
    """
    if exec_result.timed_out:
        return EvaluationResult(
            verdict="error",
            reason="Command timed out",
            suggestion=_ERROR_SUGGESTIONS["timeout"],
            error_type="timeout"
        )
    if exec_result.exit_code != 0:
        error_type = classify_error(exec_result)
        return EvaluationResult(
            verdict="error",
            reason=f"Exit code {exec_result.exit_code}: {exec_result.stderr[:200]}",
            suggestion=_ERROR_SUGGESTIONS.get(error_type, _ERROR_SUGGESTIONS["unknown"]),
            error_type=error_type
        )
        
    # FIX 3: Validation Layer
    val_res = validate_output_artifacts(exec_result, action, state)
    if val_res:
        return val_res
    if expected_outcome is None:
        return EvaluationResult(
            verdict="pass",
            reason="No expected outcome specified, trusting exit code",
            suggestion="",
            error_type=""
        )
    if isinstance(expected_outcome, dict):
        exp_type = expected_outcome.get("type", "")
        if exp_type == "file_exists":
            file_path = expected_outcome.get("path", "")
            if os.path.exists(file_path):
                return EvaluationResult(
                    verdict="pass",
                    reason=f"File exists: {file_path}",
                    suggestion="",
                    error_type=""
                )
            else:
                return EvaluationResult(
                    verdict="fail",
                    reason=f"Expected file not found: {file_path}",
                    suggestion=_ERROR_SUGGESTIONS["infrastructure"],
                    error_type="infrastructure"
                )
        elif exp_type == "output_contains":
            pattern = expected_outcome.get("pattern", "")
            if pattern in exec_result.stdout:
                return EvaluationResult(
                    verdict="pass",
                    reason=f"Output contains: {pattern}",
                    suggestion="",
                    error_type=""
                )
            else:
                return EvaluationResult(
                    verdict="fail",
                    reason=f"Output does not contain: {pattern}",
                    suggestion=_ERROR_SUGGESTIONS["runtime_error"],
                    error_type="runtime_error"
                )
        elif exp_type == "exit_code":
            expected_code = expected_outcome.get("code", 0)
            if exec_result.exit_code == expected_code:
                return EvaluationResult(
                    verdict="pass",
                    reason=f"Exit code matches: {expected_code}",
                    suggestion="",
                    error_type=""
                )
            else:
                return EvaluationResult(
                    verdict="fail",
                    reason=f"Exit code {exec_result.exit_code} != {expected_code}",
                    suggestion=_ERROR_SUGGESTIONS["runtime_error"],
                    error_type="runtime_error"
                )
    if isinstance(expected_outcome, str):
        if expected_outcome:
            if expected_outcome in exec_result.stdout or expected_outcome in exec_result.stderr:
                return EvaluationResult(
                    verdict="pass",
                    reason=f"Output contains expected string",
                    suggestion="",
                    error_type=""
                )
            else:
                return EvaluationResult(
                    verdict="fail",
                    reason="Expected output not found",
                    suggestion=_ERROR_SUGGESTIONS["runtime_error"],
                    error_type="runtime_error"
                )
    if exec_result.exit_code == 0:
        return EvaluationResult(
            verdict="pass",
            reason="Command completed successfully",
            suggestion="",
            error_type=""
        )
    return EvaluationResult(
        verdict="fail",
        reason="Unexpected state",
        suggestion=_ERROR_SUGGESTIONS["unknown"],
        error_type="unknown"
    )


def classify_error(exec_result) -> str:
    """Classify error type from execution result."""
    stderr = exec_result.stderr
    stdout = exec_result.stdout
    combined = stderr + stdout
    if "SyntaxError" in combined or "IndentationError" in combined:
        return "syntax_error"
    if "ModuleNotFoundError" in combined or "ImportError" in combined:
        return "missing_dependency"
    if "FileNotFoundError" in combined or "PermissionError" in combined or "OSError" in combined:
        return "infrastructure"
    if exec_result.timed_out:
        return "timeout"
    if exec_result.exit_code != 0:
        return "runtime_error"
    return "unknown"


def evaluate_with_validator(exec_result, expected_outcome, step: dict, state: dict):
    """Backward-compatible wrapper that delegates to validator."""
    from . import validator
    v_result = validator.validate(exec_result, expected_outcome, step, state)

    verdict = v_result.verdict
    if verdict == "partial":
        verdict = "fail"

    return EvaluationResult(
        verdict=verdict,
        reason=v_result.reason,
        suggestion=v_result.suggestion,
        error_type=v_result.failure_type
    )