"""Regression Suite — core behavior contract tests."""
import dataclasses
import json
import time
import traceback
import shutil
import tempfile
from pathlib import Path


@dataclasses.dataclass
class TestResult:
    name: str
    category: str
    passed: bool
    duration_ms: int
    detail: str
    error: str


def run_all(base_dir: Path = None) -> list:
    """Run all regression tests. Returns list[TestResult]."""
    tests = [
        test_state_create_validate,
        test_state_save_load_roundtrip,
        test_state_recompute_stats,
        test_task_classifier_types,
        test_planner_fallback,
        test_executor_shell,
        test_executor_file_write,
        test_executor_unknown_type,
        test_queue_add_list_remove,
        test_queue_priority_ordering,
        test_scheduler_select,
        test_goal_lifecycle,
        test_output_formatter,
        test_loop_controller_limits,
    ]
    results = []
    for test_fn in tests:
        results.append(_run_test(test_fn, base_dir))
    return results


def _run_test(test_fn, base_dir) -> TestResult:
    """Run a single test safely."""
    name = test_fn.__name__
    test_dir = Path(tempfile.mkdtemp(prefix=f"chotu_reg_{name[:20]}_"))
    (test_dir / ".chotu").mkdir(parents=True, exist_ok=True)

    from chotu_ai import logger
    logger.init(test_dir)

    (test_dir / ".chotu").mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    try:
        result = test_fn(test_dir)
        result.duration_ms = int((time.perf_counter() - start) * 1000)
        return result
    except Exception as e:
        duration = int((time.perf_counter() - start) * 1000)
        return TestResult(
            name=name, category="regression", passed=False,
            duration_ms=duration, detail=f"Exception: {e}",
            error=traceback.format_exc()
        )
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_state_create_validate(base_dir: Path) -> TestResult:
    from chotu_ai import state_manager
    state = state_manager.create_fresh_state("test task", str(base_dir))
    valid, errors = state_manager.validate(state)
    if valid:
        return TestResult("test_state_create_validate", "regression", True, 0,
                          "Fresh state passes validation", "")
    return TestResult("test_state_create_validate", "regression", False, 0,
                      f"Validation failed: {errors}", "")


def test_state_save_load_roundtrip(base_dir: Path) -> TestResult:
    from chotu_ai import state_manager
    state_manager.ensure_runtime_dirs(base_dir)
    state = state_manager.create_fresh_state("roundtrip test")
    state_manager.save(state, base_dir)
    loaded = state_manager.load(base_dir)
    if loaded is None:
        return TestResult("test_state_save_load_roundtrip", "regression", False, 0,
                          "load() returned None", "")
    if loaded["core_task"]["description"] != "roundtrip test":
        return TestResult("test_state_save_load_roundtrip", "regression", False, 0,
                          f"Description mismatch: {loaded['core_task']['description']}", "")
    return TestResult("test_state_save_load_roundtrip", "regression", True, 0,
                      "Save/load roundtrip preserves data", "")


def test_state_recompute_stats(base_dir: Path) -> TestResult:
    from chotu_ai import state_manager
    state = state_manager.create_fresh_state("stats test")
    state["todo_list"] = [
        state_manager.create_step("s1", "step1"),
        state_manager.create_step("s2", "step2"),
        state_manager.create_step("s3", "step3"),
    ]
    state["todo_list"][0]["status"] = "completed"
    state["todo_list"][1]["status"] = "failed"
    stats = state_manager.recompute_stats(state)
    ok = stats["total_steps"] == 3 and stats["completed"] == 1 and stats["failed"] == 1 and stats["pending"] == 1
    return TestResult("test_state_recompute_stats", "regression", ok, 0,
                      f"Stats: {stats}" if ok else f"Wrong stats: {stats}", "")


def test_task_classifier_types(base_dir: Path) -> TestResult:
    from chotu_ai import task_classifier
    cases = [
        ("find recent news about AI", "search"),
        ("build a calculator app", "build"),
        ("fix the bug in main.py", "coding"),
        ("summarize this document", "summary"),
    ]
    failures = []
    for input_text, expected_type in cases:
        profile = task_classifier.classify(input_text)
        if profile.task_type != expected_type:
            failures.append(f"'{input_text}' → {profile.task_type} (expected {expected_type})")
    if failures:
        return TestResult("test_task_classifier_types", "regression", False, 0,
                          f"Misclassified: {'; '.join(failures)}", "")
    return TestResult("test_task_classifier_types", "regression", True, 0,
                      f"All {len(cases)} classifications correct", "")


def test_planner_fallback(base_dir: Path) -> TestResult:
    """Verify planner produces a valid action even without LLM."""
    from chotu_ai import state_manager
    state = state_manager.create_fresh_state("planner test", str(base_dir))
    step = state_manager.create_step("s1", "Create a hello world script")
    step["retries"] = 0

    from chotu_ai import planner
    result = planner.plan(step, state)
    action = result.action

    ok = isinstance(action, dict) and "type" in action
    detail = f"Action: type={action.get('type')}, source={result.source}" if ok else f"Invalid action: {action}"
    return TestResult("test_planner_fallback", "regression", ok, 0, detail, "")


def test_executor_shell(base_dir: Path) -> TestResult:
    from chotu_ai import executor
    result = executor.execute({"type": "shell", "command": "echo hello"}, timeout=10, working_dir=str(base_dir))
    ok = result.success and "hello" in result.stdout
    detail = f"exit={result.exit_code}, stdout='{result.stdout.strip()}'"
    return TestResult("test_executor_shell", "regression", ok, 0, detail,
                      result.stderr if not ok else "")


def test_executor_file_write(base_dir: Path) -> TestResult:
    from chotu_ai import executor
    test_file = base_dir / "test_write.txt"
    result = executor.execute({
        "type": "file_write",
        "path": str(test_file),
        "content": "hello world"
    }, working_dir=str(base_dir))
    ok = result.success and test_file.exists()
    if ok:
        content = test_file.read_text()
        ok = "hello world" in content
    return TestResult("test_executor_file_write", "regression", ok, 0,
                      f"File exists={test_file.exists()}" if ok else f"Failed: {result.stderr}", "")


def test_executor_unknown_type(base_dir: Path) -> TestResult:
    from chotu_ai import executor
    result = executor.execute({"type": "unknown_action"}, working_dir=str(base_dir))
    ok = not result.success and result.exit_code == -1
    return TestResult("test_executor_unknown_type", "regression", ok, 0,
                      f"Graceful failure: {result.stderr[:60]}", "")


def test_queue_add_list_remove(base_dir: Path) -> TestResult:
    from chotu_ai import task_queue
    state_dir = base_dir / ".chotu"
    state_dir.mkdir(parents=True, exist_ok=True)

    tid = task_queue.add_task("test task", base_dir=base_dir)
    tasks = task_queue.list_tasks(base_dir=base_dir)
    found = any(t["task_id"] == tid for t in tasks)

    task_queue.remove_task(tid, base_dir=base_dir)
    after = task_queue.list_tasks(base_dir=base_dir)
    removed = not any(t["task_id"] == tid for t in after)

    ok = found and removed
    return TestResult("test_queue_add_list_remove", "regression", ok, 0,
                      f"Added={found}, Removed={removed}", "")


def test_queue_priority_ordering(base_dir: Path) -> TestResult:
    from chotu_ai import task_queue
    (base_dir / ".chotu").mkdir(parents=True, exist_ok=True)

    task_queue.add_task("low task", priority="low", base_dir=base_dir)
    task_queue.add_task("high task", priority="high", base_dir=base_dir)
    task_queue.add_task("normal task", priority="normal", base_dir=base_dir)

    next_task = task_queue.get_next_task(base_dir=base_dir)
    ok = next_task and next_task["priority"] == "high"
    return TestResult("test_queue_priority_ordering", "regression", ok, 0,
                      f"Next priority: {next_task['priority'] if next_task else 'NONE'}", "")


def test_scheduler_select(base_dir: Path) -> TestResult:
    from chotu_ai import task_queue, scheduler
    (base_dir / ".chotu").mkdir(parents=True, exist_ok=True)

    task_queue.add_task("low prio", priority="low", base_dir=base_dir)
    task_queue.add_task("high prio", priority="high", base_dir=base_dir)

    decision = scheduler.select_next(base_dir)
    ok = decision.has_work and decision.priority == "high"
    return TestResult("test_scheduler_select", "regression", ok, 0,
                      f"Selected: {decision.priority} — {decision.reason}", "")


def test_goal_lifecycle(base_dir: Path) -> TestResult:
    from chotu_ai import goal_manager
    (base_dir / ".chotu").mkdir(parents=True, exist_ok=True)

    gid = goal_manager.set_goal("test goal", base_dir=base_dir)
    goal = goal_manager.get_goal(base_dir=base_dir)
    if not goal or goal["status"] != "active":
        return TestResult("test_goal_lifecycle", "regression", False, 0, "Goal not created", "")

    goal_manager.update_progress(0.5, base_dir=base_dir)
    goal = goal_manager.get_goal(base_dir=base_dir)
    if abs(goal["progress"] - 0.5) > 0.01:
        return TestResult("test_goal_lifecycle", "regression", False, 0,
                          f"Progress not updated: {goal['progress']}", "")

    goal_manager.mark_complete("done", base_dir=base_dir)
    goal = goal_manager.get_goal(base_dir=base_dir)
    ok = goal["status"] == "completed"
    return TestResult("test_goal_lifecycle", "regression", ok, 0,
                      f"Lifecycle: active → 0.5 → completed = {ok}", "")


def test_output_formatter(base_dir: Path) -> TestResult:
    from chotu_ai import state_manager, output_formatter
    state = state_manager.create_fresh_state("format test")
    state["core_task"]["status"] = "completed"

    formatted = output_formatter.format_output(state)
    ok = formatted is not None and formatted.summary != ""
    return TestResult("test_output_formatter", "regression", ok, 0,
                      f"Summary: '{formatted.summary[:60]}'" if ok else "Empty output", "")


def test_loop_controller_limits(base_dir: Path) -> TestResult:
    import time as _time
    from chotu_ai import loop_controller, state_manager

    state = state_manager.create_fresh_state("loop test")
    fake_start = _time.time() - 700
    verdict = loop_controller.check(state, fake_start)
    ok = verdict.action == "timeout"
    return TestResult("test_loop_controller_limits", "regression", ok, 0,
                      f"Timeout detection: action={verdict.action}, reason={verdict.reason[:60]}", "")