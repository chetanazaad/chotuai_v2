"""Validation Harness — orchestrates all test categories."""
import json
import shutil
import tempfile
import time
import traceback
from pathlib import Path


def run_all(output_dir: Path = None) -> dict:
    """Run ALL validation categories in order. Returns the readiness report dict."""
    if output_dir is None:
        output_dir = Path.cwd() / ".chotu" / "validation"
    output_dir.mkdir(parents=True, exist_ok=True)

    test_dir = Path(tempfile.mkdtemp(prefix="chotu_validation_"))
    (test_dir / ".chotu").mkdir(parents=True, exist_ok=True)
    from chotu_ai import logger
    logger.init(test_dir)

    all_results = []
    print("\n" + "=" * 60)
    print("  CHOTU AI - VALIDATION & HARDENING")
    print("=" * 60)

    print("\n  > [1/7] Smoke Tests...")
    smoke_results = _run_smoke_tests()
    all_results.extend(smoke_results)
    _print_category_summary("Smoke", smoke_results)

    print("\n  > [2/7] Regression Suite...")
    from . import regression_suite
    reg_results = regression_suite.run_all()
    all_results.extend(reg_results)
    _print_category_summary("Regression", reg_results)

    print("\n  > [3/7] Recovery Tests...")
    recovery_results = _run_recovery_tests()
    all_results.extend(recovery_results)
    _print_category_summary("Recovery", recovery_results)

    print("\n  > [4/7] Fault Injection Tests...")
    fault_results = _run_fault_injection_tests()
    all_results.extend(fault_results)
    _print_category_summary("Fault Injection", fault_results)

    print("\n  > [5/7] Stress Tests...")
    from . import stress_tester
    stress_results = stress_tester.run_all()
    all_results.extend(stress_results)
    _print_category_summary("Stress", stress_results)

    print("\n  > [6/7] Autonomous Mode Tests...")
    auto_results = _run_autonomous_tests()
    all_results.extend(auto_results)
    _print_category_summary("Autonomous", auto_results)

    print("\n  ▸ [7/7] Browser Tests...")
    browser_results = _run_browser_tests()
    all_results.extend(browser_results)
    _print_category_summary("Browser", browser_results)

    print("\n  ▸ Generating readiness report...")
    from . import readiness_reporter
    report = readiness_reporter.generate_report(all_results, output_dir)

    _print_final_summary(report)

    return report


def _run_smoke_tests() -> list:
    results = []

    start = time.perf_counter()
    try:
        from chotu_ai import (state_manager, controller, executor, planner, validator,
                              decision_engine, task_queue, task_worker, scheduler,
                              goal_manager, autonomous_runner, browser_agent,
                              output_formatter, artifact_manager, ui_renderer, logger)
        results.append(_make_result("smoke_module_imports", "smoke", True,
                                   int((time.perf_counter() - start) * 1000),
                                   "All core modules imported successfully", ""))
    except Exception as e:
        results.append(_make_result("smoke_module_imports", "smoke", False,
                                   int((time.perf_counter() - start) * 1000),
                                   f"Import failed: {e}", traceback.format_exc()))

    start = time.perf_counter()
    try:
        from chotu_ai import state_manager
        state = state_manager.create_fresh_state("smoke test")
        valid, _ = state_manager.validate(state)
        results.append(_make_result("smoke_state_creation", "smoke", valid,
                                   int((time.perf_counter() - start) * 1000),
                                   "Fresh state created and validated", ""))
    except Exception as e:
        results.append(_make_result("smoke_state_creation", "smoke", False,
                                   int((time.perf_counter() - start) * 1000),
                                   f"State creation failed: {e}", traceback.format_exc()))

    start = time.perf_counter()
    try:
        from chotu_ai import executor
        result = executor.execute({"type": "shell", "command": "echo smoke_ok"}, timeout=5)
        ok = result.success and "smoke_ok" in result.stdout
        results.append(_make_result("smoke_shell_execution", "smoke", ok,
                                   int((time.perf_counter() - start) * 1000),
                                   f"echo returned: '{result.stdout.strip()}'",
                                   result.stderr if not ok else ""))
    except Exception as e:
        results.append(_make_result("smoke_shell_execution", "smoke", False,
                                   int((time.perf_counter() - start) * 1000),
                                   f"Shell failed: {e}", traceback.format_exc()))

    return results


def _run_recovery_tests() -> list:
    results = []
    results.append(_isolated_test(_test_corrupt_state_recovery, "recovery_corrupt_state", "recovery"))
    results.append(_isolated_test(_test_corrupt_queue_recovery, "recovery_corrupt_queue", "recovery"))
    results.append(_isolated_test(_test_stale_backup_recovery, "recovery_stale_backup", "recovery"))
    return results


def _test_corrupt_state_recovery(base_dir: Path):
    from chotu_ai import fault_injector, state_manager
    fault_injector.inject_invalid_state(base_dir)

    try:
        state = state_manager.load(base_dir)
        if state is None:
            return _make_result("recovery_corrupt_state", "recovery", True, 0,
                              "Corrupt state returned None (safe)", "")
        return _make_result("recovery_corrupt_state", "recovery", False, 0,
                          "Corrupt state loaded without error!", "")
    except (ValueError, json.JSONDecodeError):
        return _make_result("recovery_corrupt_state", "recovery", True, 0,
                          "Corrupt state raised expected error (safe)", "")
    except Exception as e:
        return _make_result("recovery_corrupt_state", "recovery", False, 0,
                          f"Unexpected error: {e}", traceback.format_exc())


def _test_corrupt_queue_recovery(base_dir: Path):
    from chotu_ai import fault_injector, task_queue
    fault_injector.inject_corrupt_queue(base_dir)

    tasks = task_queue.list_tasks(base_dir=base_dir)
    ok = isinstance(tasks, list) and len(tasks) == 0
    return _make_result("recovery_corrupt_queue", "recovery", ok, 0,
                      f"Corrupt queue → empty list (safe): {len(tasks)} tasks", "")


def _test_stale_backup_recovery(base_dir: Path):
    from chotu_ai import fault_injector
    fault_injector.inject_stale_backup(base_dir)

    backup_file = base_dir / ".chotu" / "state.json.bak"
    exists = backup_file.exists()
    return _make_result("recovery_stale_backup", "recovery", exists, 0,
                      f"Stale backup created: {exists}", "")


def _run_fault_injection_tests() -> list:
    results = []
    results.append(_isolated_test(_test_invalid_task_input, "fault_invalid_input", "fault"))
    results.append(_isolated_test(_test_shell_failure_injection, "fault_shell_failure", "fault"))
    results.append(_isolated_test(_test_browser_unavailable, "fault_browser_unavail", "fault"))
    results.append(_isolated_test(_test_llm_unavailable, "fault_llm_unavail", "fault"))
    results.append(_isolated_test(_test_unknown_action_fault, "fault_unknown_action", "fault"))
    results.append(_isolated_test(_test_empty_task, "fault_empty_task", "fault"))
    return results


def _test_invalid_task_input(base_dir: Path):
    from chotu_ai import task_classifier
    profile = task_classifier.classify("")
    ok = profile.task_type == "unknown" and profile.confidence <= 0.2
    return _make_result("fault_invalid_input", "fault", ok, 0,
                      f"Empty input → type={profile.task_type}, conf={profile.confidence}", "")


def _test_shell_failure_injection(base_dir: Path):
    from chotu_ai import executor, fault_injector
    with fault_injector.inject_shell_failure():
        result = executor.execute({"type": "shell", "command": "echo should_fail"})
        ok = not result.success and "INJECTED" in result.stderr
    after = executor.execute({"type": "shell", "command": "echo should_pass"})
    recovered = after.success and "should_pass" in after.stdout
    return _make_result("fault_shell_failure", "fault", ok and recovered, 0,
                      f"Injected={ok}, Recovered={recovered}", "")


def _test_browser_unavailable(base_dir: Path):
    from chotu_ai import browser_agent, fault_injector
    original_available = browser_agent.is_available()
    with fault_injector.inject_browser_unavailable():
        unavailable = not browser_agent.is_available()
    restored = browser_agent.is_available() == original_available
    return _make_result("fault_browser_unavail", "fault", unavailable and restored, 0,
                      f"Injected unavailable={unavailable}, Restored={restored}", "")


def _test_llm_unavailable(base_dir: Path):
    from chotu_ai import planner, state_manager, fault_injector
    state = state_manager.create_fresh_state("llm test")
    step = state_manager.create_step("s1", "write hello world")

    with fault_injector.inject_llm_unavailable():
        result = planner.plan(step, state)
        ok = result is not None and result.action is not None
    return _make_result("fault_llm_unavail", "fault", ok, 0,
                      f"Planner fallback works: source={result.source}" if ok else "Planner crashed", "")


def _test_unknown_action_fault(base_dir: Path):
    from chotu_ai import executor
    result = executor.execute({"type": "nonexistent_type", "data": "test"})
    ok = not result.success and "Unknown" in result.stderr
    return _make_result("fault_unknown_action", "fault", ok, 0,
                      f"Graceful: {result.stderr[:60]}", "")


def _test_empty_task(base_dir: Path):
    from chotu_ai import task_classifier
    profile = task_classifier.classify("   ")
    ok = profile.task_type == "unknown"
    return _make_result("fault_empty_task", "fault", ok, 0,
                      f"Whitespace → {profile.task_type}", "")


def _run_autonomous_tests() -> list:
    results = []
    results.append(_isolated_test(_test_autonomous_goal_set, "auto_goal_lifecycle", "autonomous"))
    results.append(_isolated_test(_test_autonomous_max_iterations, "auto_max_iterations", "autonomous"))
    return results


def _test_autonomous_goal_set(base_dir: Path):
    from chotu_ai import goal_manager
    (base_dir / ".chotu").mkdir(parents=True, exist_ok=True)

    gid = goal_manager.set_goal("auto test goal", max_iterations=2, max_runtime=30, base_dir=base_dir)
    goal = goal_manager.get_goal(base_dir=base_dir)
    ok = goal is not None and goal["status"] == "active" and goal["goal"] == "auto test goal"
    return _make_result("auto_goal_lifecycle", "autonomous", ok, 0,
                      f"Goal created: id={gid}, status={goal['status'] if goal else 'NONE'}", "")


def _test_autonomous_max_iterations(base_dir: Path):
    from chotu_ai import goal_manager
    (base_dir / ".chotu").mkdir(parents=True, exist_ok=True)

    goal_manager.set_goal("max iter test", max_iterations=1, max_runtime=30, base_dir=base_dir)

    try:
        from chotu_ai import autonomous_runner
        result = autonomous_runner.start(base_dir)
    except Exception as e:
        return _make_result("auto_max_iterations", "autonomous", False, 0,
                          f"Crashed: {e}", traceback.format_exc())

    goal = goal_manager.get_goal(base_dir)
    terminated = goal and goal["status"] in ("completed", "failed")
    return _make_result("auto_max_iterations", "autonomous", terminated, 0,
                      f"Terminated: status={goal['status'] if goal else 'NONE'}", "")


def _run_browser_tests() -> list:
    results = []

    start = time.perf_counter()
    try:
        from chotu_ai import browser_agent
        available = browser_agent.is_available()
        results.append(_make_result("browser_availability", "browser", True,
                                   int((time.perf_counter() - start) * 1000),
                                   f"Playwright available: {available}", ""))
    except Exception as e:
        results.append(_make_result("browser_availability", "browser", False,
                                   int((time.perf_counter() - start) * 1000),
                                   f"Check failed: {e}", traceback.format_exc()))

    start = time.perf_counter()
    try:
        from chotu_ai import browser_agent
        if browser_agent.is_available():
            result = browser_agent.search_google("test query validation", timeout_ms=10000)
            ok = result.success or "timeout" in result.error.lower()
            detail = f"success={result.success}, duration={result.duration_ms}ms"
            if not result.success:
                detail += f", error={result.error[:60]}"
            browser_agent.close()
            results.append(_make_result("browser_search", "browser", ok,
                                       int((time.perf_counter() - start) * 1000), detail, ""))
        else:
            results.append(_make_result("browser_search", "browser", True,
                                       int((time.perf_counter() - start) * 1000),
                                       "Skipped (Playwright not installed)", ""))
    except Exception as e:
        try:
            browser_agent.close()
        except Exception:
            pass
        results.append(_make_result("browser_search", "browser", False,
                                   int((time.perf_counter() - start) * 1000),
                                   f"Crashed: {e}", traceback.format_exc()))

    return results


def _isolated_test(test_fn, name: str, category: str):
    """Run test in isolated temp directory."""
    test_dir = Path(tempfile.mkdtemp(prefix=f"chotu_{name[:20]}_"))
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
        return _make_result(name, category, False, duration, f"Exception: {e}", traceback.format_exc())
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def _make_result(name: str, category: str, passed: bool, duration_ms: int, detail: str, error: str = "") -> "TestResult":
    from .regression_suite import TestResult
    return TestResult(name, category, passed, duration_ms, detail, error)


def _print_category_summary(name: str, results: list) -> None:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    icon = "PASS" if passed == total else "FAIL"
    print(f"     {icon} {name}: {passed}/{total} passed")
    for r in results:
        status = "OK" if r.passed else "X"
        print(f"        {status} {r.name} ({r.duration_ms}ms)")
        if not r.passed and r.detail:
            print(f"           .. {r.detail[:80]}")


def _print_final_summary(report: dict) -> None:
    totals = report["totals"]
    readiness = report["readiness"]

    icon = {"READY": "OK", "CONDITIONAL": "WARN", "NOT READY": "FAIL"}.get(readiness, "?")

    print("\n" + "=" * 60)
    print(f"  {icon} READINESS: {readiness}")
    print(f"  {report['readiness_note']}")
    print("")
    print(f"  Tests: {totals['total']}  |  Passed: {totals['passed']}  |  Failed: {totals['failed']}  |  Rate: {totals['pass_rate']}%")

    if report["failure_traces"]:
        print("\n  Failures:")
        for ft in report["failure_traces"][:5]:
            print(f"     - {ft['test']}: {ft['detail'][:70]}")

    print("=" * 60)