"""Fault Injector — controlled failure simulation via monkeypatching."""
import contextlib
import json
from pathlib import Path


@contextlib.contextmanager
def inject_shell_failure():
    """Make executor._execute_shell always return a failure."""
    from chotu_ai import executor

    original = executor._execute_shell

    def _failing_shell(*args, **kwargs):
        return executor.ExecutionResult(
            success=False, exit_code=1, stdout="",
            stderr="INJECTED FAULT: shell command failed",
            duration_ms=0, files_changed=[], timed_out=False
        )

    executor._execute_shell = _failing_shell
    try:
        yield
    finally:
        executor._execute_shell = original


@contextlib.contextmanager
def inject_browser_unavailable():
    """Make browser_agent.is_available() return False."""
    from chotu_ai import browser_agent

    original = browser_agent.is_available

    def _unavailable():
        return False

    browser_agent.is_available = _unavailable
    try:
        yield
    finally:
        browser_agent.is_available = original


@contextlib.contextmanager
def inject_llm_unavailable():
    """Make llm_gateway.is_available() return False."""
    from chotu_ai import llm_gateway

    original = llm_gateway.is_available

    def _unavailable():
        return False

    llm_gateway.is_available = _unavailable
    try:
        yield
    finally:
        llm_gateway.is_available = original


@contextlib.contextmanager
def inject_file_not_found(path: str):
    """Make executor._execute_file_read always fail with FileNotFoundError."""
    from chotu_ai import executor

    original = executor._execute_file_read

    def _failing_read(*args, **kwargs):
        return executor.ExecutionResult(
            success=False, exit_code=1, stdout="",
            stderr=f"INJECTED FAULT: FileNotFoundError: {path}",
            duration_ms=0, files_changed=[]
        )

    executor._execute_file_read = _failing_read
    try:
        yield
    finally:
        executor._execute_file_read = original


def inject_invalid_state(base_dir: Path) -> None:
    """Write a corrupted state.json to test recovery."""
    chotu_dir = base_dir / ".chotu"
    chotu_dir.mkdir(parents=True, exist_ok=True)
    state_file = chotu_dir / "state.json"
    state_file.write_text("{invalid json content", encoding="utf-8")


def inject_corrupt_queue(base_dir: Path) -> None:
    """Write a corrupted task_queue.json to test recovery."""
    chotu_dir = base_dir / ".chotu"
    chotu_dir.mkdir(parents=True, exist_ok=True)
    queue_file = chotu_dir / "task_queue.json"
    queue_file.write_text("NOT JSON AT ALL }{}{", encoding="utf-8")


def inject_stale_backup(base_dir: Path) -> None:
    """Create a stale state.json.bak to test crash recovery."""
    chotu_dir = base_dir / ".chotu"
    chotu_dir.mkdir(parents=True, exist_ok=True)

    from chotu_ai import state_manager
    state = state_manager.create_fresh_state("backup test task")
    backup_file = chotu_dir / "state.json.bak"
    with open(backup_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)