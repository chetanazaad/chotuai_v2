"""Stress Tester — sustained usage tests."""
import shutil
import tempfile
import time
import traceback
from pathlib import Path

from .regression_suite import TestResult


def run_all(base_dir: Path = None) -> list:
    """Run all stress tests."""
    tests = [
        stress_repeated_tasks,
        stress_queue_load,
        stress_planning_cycles,
        stress_autonomous_short,
    ]
    results = []
    for test_fn in tests:
        results.append(_run_stress(test_fn))
    return results


def _run_stress(test_fn) -> TestResult:
    """Run a stress test in an isolated directory."""
    name = test_fn.__name__
    test_dir = Path(tempfile.mkdtemp(prefix=f"chotu_stress_{name[:20]}_"))
    (test_dir / ".chotu").mkdir(parents=True, exist_ok=True)

    from chotu_ai import logger
    logger.init(test_dir)

    start = time.perf_counter()
    try:
        result = test_fn(test_dir)
        result.duration_ms = int((time.perf_counter() - start) * 1000)
        return result
    except Exception as e:
        duration = int((time.perf_counter() - start) * 1000)
        return TestResult(name, "stress", False, duration, f"Exception: {e}", traceback.format_exc())
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def stress_repeated_tasks(base_dir: Path) -> TestResult:
    """Run the same simple task 5 times. Check for state leaks."""
    from chotu_ai import state_manager, executor

    (base_dir / ".chotu").mkdir(parents=True, exist_ok=True)

    for i in range(5):
        state_manager.ensure_runtime_dirs(base_dir)
        state = state_manager.create_fresh_state(f"stress task {i}")
        state_manager.save(state, base_dir)

        result = executor.execute(
            {"type": "shell", "command": f"echo stress_{i}"},
            timeout=10, working_dir=str(base_dir)
        )
        if not result.success:
            return TestResult("stress_repeated_tasks", "stress", False, 0,
                              f"Task {i} failed: {result.stderr}", "")

        loaded = state_manager.load(base_dir)
        if loaded is None:
            return TestResult("stress_repeated_tasks", "stress", False, 0,
                              f"State lost after task {i}", "")

    return TestResult("stress_repeated_tasks", "stress", True, 0,
                      "5 repeated tasks executed cleanly", "")


def stress_queue_load(base_dir: Path) -> TestResult:
    """Add 10 tasks to queue. Verify all are tracked."""
    from chotu_ai import task_queue

    (base_dir / ".chotu").mkdir(parents=True, exist_ok=True)

    task_ids = []
    for i in range(10):
        tid = task_queue.add_task(f"queue task {i}", base_dir=base_dir)
        task_ids.append(tid)

    tasks = task_queue.list_tasks(base_dir=base_dir)
    if len(tasks) != 10:
        return TestResult("stress_queue_load", "stress", False, 0,
                          f"Expected 10 tasks, got {len(tasks)}", "")

    listed_ids = {t["task_id"] for t in tasks}
    missing = [tid for tid in task_ids if tid not in listed_ids]
    if missing:
        return TestResult("stress_queue_load", "stress", False, 0,
                          f"Missing tasks: {missing}", "")

    descriptions = [t["description"] for t in tasks]
    expected = [f"queue task {i}" for i in range(10)]
    if descriptions != expected:
        return TestResult("stress_queue_load", "stress", False, 0,
                          f"Order mismatch: {descriptions[:3]}...", "")

    return TestResult("stress_queue_load", "stress", True, 0,
                      "10 tasks queued correctly with FIFO order", "")


def stress_planning_cycles(base_dir: Path) -> TestResult:
    """Call planner.plan() 10 times without crashing."""
    from chotu_ai import planner, state_manager

    state = state_manager.create_fresh_state("plan stress")

    for i in range(10):
        step = state_manager.create_step(f"s{i}", f"Test step {i}")
        try:
            result = planner.plan(step, state)
            if not result or not result.action:
                return TestResult("stress_planning_cycles", "stress", False, 0,
                                  f"Plan returned no action on cycle {i}", "")
        except Exception as e:
            return TestResult("stress_planning_cycles", "stress", False, 0,
                              f"Planner crashed on cycle {i}: {e}", traceback.format_exc())

    return TestResult("stress_planning_cycles", "stress", True, 0,
                      "10 planning cycles completed without crash", "")


def stress_autonomous_short(base_dir: Path) -> TestResult:
    """Run a 2-iteration autonomous loop. Must terminate safely."""
    from chotu_ai import goal_manager

    (base_dir / ".chotu").mkdir(parents=True, exist_ok=True)

    goal_manager.set_goal(
        "test autonomous stress",
        max_iterations=2,
        max_runtime=30,
        base_dir=base_dir,
    )

    try:
        from chotu_ai import autonomous_runner
        result = autonomous_runner.start(base_dir)
    except Exception as e:
        return TestResult("stress_autonomous_short", "stress", False, 0,
                          f"Autonomous loop crashed: {e}", traceback.format_exc())

    goal = goal_manager.get_goal(base_dir=base_dir)
    if not goal:
        return TestResult("stress_autonomous_short", "stress", False, 0,
                          "Goal disappeared after autonomous run", "")

    terminated = goal["status"] in ("completed", "failed")
    return TestResult("stress_autonomous_short", "stress", terminated, 0,
                      f"Autonomous loop terminated: status={goal['status']}, iterations={goal['iterations']}",
                      "" if terminated else "Loop did not terminate!")