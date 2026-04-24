"""Execute exactly one action at a time. Return structured results."""
import dataclasses
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional


_WORKSPACE_DIR = "output"
_FORBIDDEN_EXTENSIONS = [".sh", ".bash", ".exe", ".bat", ".cmd"]
_ALLOWED_EXTENSIONS = [".py", ".html", ".txt", ".json", ".md", ".css", ".js"]


def sanitize_path(path: str) -> Optional[str]:
    """Sanitize and validate file path for Windows safety."""
    if not path:
        return None
    path = path.replace("\\", "/")
    if path.startswith("/"):
        print(f"[EXECUTOR] Blocked linux path: {path}")
        return None
    forbidden_dirs = ["/tmp", "/usr", "/bin", "/lib", "/etc", "/var"]
    for forbid in forbidden_dirs:
        if forbid in path:
            print(f"[EXECUTOR] Blocked forbidden path: {path}")
            return None
    return path


def enforce_workspace(path: str, output_dir: str = "output") -> str:
    """Ensure file goes to task-scoped output directory."""
    clean = sanitize_path(path)
    if clean is None:
        return os.path.join(output_dir, "unnamed.txt")
    
    filename = clean.split("/")[-1]
    path = os.path.join(output_dir, filename)
    
    print(f"[OUTPUT CONTROL] Path enforced: {path}")
    return path


def validate_file_type(path: str) -> bool:
    """Validate file extension is allowed."""
    clean = sanitize_path(path)
    if clean is None:
        return False

    for forbid in _FORBIDDEN_EXTENSIONS:
        if clean.endswith(forbid):
            print(f"[EXECUTOR] Blocked forbidden extension: {forbid}")
            return False

    if _ALLOWED_EXTENSIONS:
        has_ext = any(clean.endswith(ext) for ext in _ALLOWED_EXTENSIONS)
        if not has_ext:
            print(f"[EXECUTOR] File type not in whitelist: {clean}")
            return False

    return True


def validate_shell_command(command: str) -> tuple[bool, str]:
    """Validate shell command is safe for Windows."""
    if not command:
        return False, "empty_command"

    cmd_lower = command.lower()

    unsafe = [
        "bash", "sudo", "gcc", "g++", "clang",
        "apt-get", "apt ", "yum ", "chmod", "curl ", "wget ",
        "ssh ", "scp ", "rm -rf", "del /s", "format ",
        "powershell.*-enc", "/bin/",
    ]
    for pattern in unsafe:
        if re.search(pattern, cmd_lower):
            return False, f"unsafe_command:{pattern}"

    return True, None


def ensure_workspace_dir() -> None:
    """Ensure workspace directory exists."""
    Path(_WORKSPACE_DIR).mkdir(parents=True, exist_ok=True)
    print(f"[OUTPUT CONTROL] {_WORKSPACE_DIR}/ directory ready")


def validate_output_isolation(files: list) -> tuple[bool, str]:
    """Validate all files are inside output directory."""
    for f in files:
        if not f.startswith(_WORKSPACE_DIR + "/"):
            return False, f"File outside output/: {f}"
    return True, None


@dataclasses.dataclass
class ExecutionResult:
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    files_changed: list
    timed_out: bool = False


def execute(action: dict, timeout: int = 60, working_dir: Optional[str] = None, output_dir: str = "output", state: Optional[dict] = None) -> ExecutionResult:
    """Dispatch to action handler with safety checks and crash protection."""
    ensure_workspace_dir()

    print(f"[ACTION RECEIVED] {action.get('type', 'unknown')}")

    if not isinstance(action, dict):
        print("[ACTION REJECTED] not_dict")
        action = {"type": "unknown", "command": str(action)}
    action_type = action.get("type", "")

    try:
        if action_type == "shell":
            return _execute_shell(action, timeout, working_dir)
        elif action_type == "file_write":
            return _execute_file_write(action, working_dir, output_dir, state)
        elif action_type == "file_read":
            return _execute_file_read(action, working_dir)
        elif action_type == "browser":
            return _execute_browser(action, timeout)
        elif action_type == "multi":
            return _execute_multi(action, timeout, working_dir)
        else:
            print(f"[ACTION REJECTED] unknown_type:{action_type}")
            return ExecutionResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=f"Unknown action type: {action_type}",
                duration_ms=0,
                files_changed=[]
            )
    except Exception as e:
        print(f"[EXECUTION ERROR] {e}")
        return ExecutionResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr=f"infrastructure_failure:{e}",
            duration_ms=0,
            files_changed=[]
        )


def _execute_shell(action: dict, timeout: int, working_dir: Optional[str]) -> ExecutionResult:
    """Run via subprocess.run(), capture stdout/stderr, enforce timeout."""
    command = action.get("command", "")

    valid, err = validate_shell_command(command)
    if not valid:
        print(f"[EXECUTOR] Command blocked: {err}")
        return ExecutionResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr=f"Command blocked: {err}",
            duration_ms=0,
            files_changed=[]
        )

    start_time = time.perf_counter()
    timed_out = False
    try:
        cwd = working_dir if working_dir else None
        if working_dir:
            cwd = str(Path(working_dir).resolve())
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd
        )
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return ExecutionResult(
            success=result.returncode == 0,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            files_changed=[],
            timed_out=False
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return ExecutionResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            duration_ms=duration_ms,
            files_changed=[],
            timed_out=True
        )


def _execute_file_write(action: dict, working_dir: Optional[str], output_dir: str, state: Optional[dict] = None) -> ExecutionResult:
    """Write file, create parent dirs, report file as changed with safety checks."""
    start_time = time.perf_counter()
    # PRIORITY: planner target_file
    target_file = action.get("target_file")

    # FALLBACK to state step if missing
    if not target_file and state:
        step = state.get("current_step", {})
        target_file = step.get("target_file")

    # FINAL DECISION
    if target_file:
        file_path = target_file
    else:
        file_path = action.get("file", action.get("path", "output.html"))

    # ADD HARD ASSERT
    if target_file is None:
        print("[WARNING] No target_file found → fallback to output.html")

    # ADD DEBUG
    print(f"[EXECUTOR] target_file resolved → {target_file}")
    print(f"[ENFORCED FILE] → {file_path}")
    
    content = action.get("content", "")

    if not file_path:
        return ExecutionResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr="No path specified for file_write",
            duration_ms=0,
            files_changed=[]
        )

    file_path = enforce_workspace(file_path, output_dir)

    if not validate_file_type(file_path):
        return ExecutionResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr=f"Invalid file type: {file_path}",
            duration_ms=0,
            files_changed=[]
        )

    if working_dir:
        file_path = str(Path(working_dir) / file_path)
    path = Path(file_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[FILE TARGET] Writing → {path.name}")
        path.write_text(content, encoding="utf-8")
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return ExecutionResult(
            success=True,
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=duration_ms,
            files_changed=[str(path)]
        )
    except Exception as e:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return ExecutionResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr=str(e),
            duration_ms=duration_ms,
            files_changed=[]
        )


def _execute_file_read(action: dict, working_dir: Optional[str]) -> ExecutionResult:
    """Read file, return content in stdout."""
    start_time = time.perf_counter()
    file_path = action.get("path", "")
    if not file_path:
        return ExecutionResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr="No path specified for file_read",
            duration_ms=0,
            files_changed=[]
        )
    if working_dir:
        file_path = str(Path(working_dir) / file_path)
    path = Path(file_path)
    try:
        if not path.exists():
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ExecutionResult(
                success=False,
                exit_code=2,
                stdout="",
                stderr=f"File not found: {file_path}",
                duration_ms=duration_ms,
                files_changed=[]
            )
        content = path.read_text(encoding="utf-8")
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return ExecutionResult(
            success=True,
            exit_code=0,
            stdout=content,
            stderr="",
            duration_ms=duration_ms,
            files_changed=[]
        )
    except Exception as e:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return ExecutionResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr=str(e),
            duration_ms=duration_ms,
            files_changed=[]
        )


def _execute_browser(action: dict, timeout: int) -> ExecutionResult:
    """Execute a browser action via browser_agent."""
    start_time = time.perf_counter()

    try:
        from . import browser_agent

        if not browser_agent.is_available():
            return ExecutionResult(
                success=False, exit_code=-1, stdout="", duration_ms=0,
                stderr="Playwright not installed. Run: pip install playwright && playwright install chromium",
                files_changed=[]
            )

        browser_action = action.get("browser_action", "")
        timeout_ms = timeout * 1000

        if browser_action == "search":
            query = action.get("query", "")
            result = browser_agent.search_google(query, timeout_ms)
        elif browser_action == "open_url":
            url = action.get("url", "")
            result = browser_agent.open_url(url, timeout_ms)
        elif browser_action == "click":
            selector = action.get("selector", action.get("text", ""))
            result = browser_agent.click(selector, timeout_ms)
        elif browser_action == "type":
            selector = action.get("selector", "")
            text = action.get("text", "")
            result = browser_agent.type_text(selector, text, timeout_ms)
        elif browser_action == "extract_text":
            selector = action.get("selector", "body")
            result = browser_agent.extract_text(selector, timeout_ms)
        elif browser_action == "extract_links":
            result = browser_agent.extract_links(timeout_ms)
        elif browser_action == "wait_for":
            selector = action.get("selector", "")
            result = browser_agent.wait_for(selector, timeout_ms)
        elif browser_action == "screenshot":
            path = action.get("path", "")
            result = browser_agent.screenshot(path)
        else:
            return ExecutionResult(
                success=False, exit_code=-1, stdout="",
                stderr=f"Unknown browser_action: {browser_action}",
                duration_ms=0, files_changed=[]
            )

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        files_changed = [result.screenshot_path] if result.screenshot_path else []

        return ExecutionResult(
            success=result.success,
            exit_code=0 if result.success else 1,
            stdout=result.extracted_text,
            stderr=result.error,
            duration_ms=duration_ms,
            files_changed=files_changed,
        )

    except Exception as e:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return ExecutionResult(
            success=False, exit_code=-1, stdout="",
            stderr=str(e), duration_ms=duration_ms, files_changed=[]
        )


def _execute_multi(action: dict, timeout: int, working_dir: Optional[str]) -> ExecutionResult:
    """Execute each sub-action sequentially. Stop on first failure."""
    steps = action.get("steps", [])
    all_stdout = []
    all_stderr = []
    all_files = []
    total_duration = 0
    for step in steps:
        result = execute(step, timeout, working_dir)
        total_duration += result.duration_ms
        all_stdout.append(result.stdout)
        all_stderr.append(result.stderr)
        all_files.extend(result.files_changed)
        if result.exit_code != 0:
            return ExecutionResult(
                success=False,
                exit_code=result.exit_code,
                stdout="\n".join(all_stdout),
                stderr="\n".join(all_stderr),
                duration_ms=total_duration,
                files_changed=all_files
            )
    return ExecutionResult(
        success=True,
        exit_code=0,
        stdout="\n".join(all_stdout),
        stderr="\n".join(all_stderr),
        duration_ms=total_duration,
        files_changed=all_files
    )