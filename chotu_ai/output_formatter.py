"""Output Formatter — rich, task-type-aware output formatting."""
import dataclasses
import os
from pathlib import Path
from typing import Optional

from . import artifact_manager


@dataclasses.dataclass
class DisplayBlock:
    kind: str
    content: object


@dataclasses.dataclass
class FormattedOutput:
    summary: str
    status: str
    results: list
    artifacts: list
    display_blocks: list
    actions: list
    task_type: str
    stats: dict


def format_output(state: dict) -> FormattedOutput:
    """Main entry point. Reads state, returns structured output."""
    if not state:
        return _build_empty_output()

    task_profile = state.get("core_task", {}).get("task_profile", {})
    task_type = task_profile.get("task_type", "unknown")

    status = _determine_status(state)
    artifacts = _collect_artifacts(state)
    results = _collect_results(state)
    summary = _build_summary(state, task_profile, status)
    display_blocks = _build_display_blocks(state, task_type, status, artifacts, results)
    actions = _build_actions(task_type, status, artifacts)
    stats = state.get("stats", {})

    return FormattedOutput(
        summary=summary,
        status=status,
        results=results,
        artifacts=artifacts,
        display_blocks=display_blocks,
        actions=actions,
        task_type=task_type,
        stats=stats,
    )


def _determine_status(state: dict) -> str:
    task_status = state.get("core_task", {}).get("status", "")
    if task_status == "completed":
        return "success"
    elif task_status == "failed":
        stats = state.get("stats", {})
        if stats.get("completed", 0) > 0:
            return "partial"
        return "failure"
    return "failure"


def _build_summary(state: dict, task_profile: dict, status: str) -> str:
    task_desc = state.get("core_task", {}).get("description", "Task")
    stats = state.get("stats", {})
    completed = stats.get("completed", 0)
    total = stats.get("total_steps", 0)

    if status == "success":
        return f"Completed: {task_desc} ({completed}/{total} steps)"
    elif status == "partial":
        failed = stats.get("failed", 0)
        return f"Partially completed: {task_desc} ({completed}/{total} steps, {failed} failed)"
    else:
        return f"Failed: {task_desc} ({completed}/{total} steps completed)"


def _collect_artifacts(state: dict) -> list:
    artifacts = []
    working_dir = state.get("config", {}).get("working_directory", ".")

    try:
        registry_artifacts = artifact_manager.list_artifacts()
        for a in registry_artifacts:
            artifacts.append({
                "type": a.get("artifact_type", "file"),
                "path": a.get("file_path", ""),
                "label": a.get("label", ""),
                "step_id": a.get("step_id", ""),
            })
    except Exception:
        pass

    if artifacts:
        return artifacts

    for step in state.get("todo_list", []):
        action = step.get("action", {})
        action_type = action.get("type", "")

        if action_type == "file_write":
            path = action.get("path", "")
            if path:
                artifacts.append({
                    "type": "file",
                    "path": path,
                    "label": os.path.basename(path),
                    "step_id": step.get("id", ""),
                })
        elif action_type == "shell":
            cmd = action.get("command", "")
            result = step.get("result", {})
            if result.get("verdict") == "pass":
                stdout = result.get("stdout", "")
                if stdout and len(stdout) < 500:
                    artifacts.append({
                        "type": "result",
                        "path": "",
                        "label": f"Output: {stdout[:100]}",
                        "step_id": step.get("id", ""),
                    })

    workspace = Path(working_dir) / "workspace"
    if workspace.exists():
        for root, dirs, files in os.walk(workspace):
            for f in files:
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, working_dir)
                if not any(a["path"] == rel_path for a in artifacts):
                    artifacts.append({"type": "file", "path": rel_path, "label": f, "step_id": ""})

    common_outputs = ["hello.py", "app.py", "main.py", "index.py", "output.txt", "result.txt", "report.txt"]
    for fname in common_outputs:
        fpath = Path(working_dir) / fname
        if fpath.exists():
            if not any(a.get("label") == fname for a in artifacts):
                artifacts.append({"type": "file", "path": fname, "label": fname, "step_id": ""})

    return artifacts


def _collect_results(state: dict) -> list:
    results = []
    for step in state.get("todo_list", []):
        result = step.get("result", {})
        if not result:
            continue
        entry = {
            "step_id": step.get("id", ""),
            "description": step.get("description", ""),
            "status": step.get("status", ""),
            "verdict": result.get("verdict", ""),
            "exit_code": result.get("exit_code", None),
            "reason": result.get("reason", ""),
            "duration_ms": result.get("duration_ms", 0),
        }
        results.append(entry)
    return results


def _build_display_blocks(state: dict, task_type: str, status: str, artifacts: list, results: list) -> list:
    _TYPE_FORMATTERS = {
        "build": _format_build, "coding": _format_coding,
        "search": _format_search, "analysis": _format_analysis,
        "summary": _format_summary_task, "cleanup": _format_cleanup,
    }
    formatter = _TYPE_FORMATTERS.get(task_type, _format_generic)
    blocks = formatter(state, artifacts, results)

    stats = state.get("stats", {})
    stats_line = f"{stats.get('completed', 0)} steps completed | {stats.get('failed', 0)} failures | {stats.get('total_retries', 0)} retries"
    blocks.append(DisplayBlock(kind="info", content=f"📊 Stats: {stats_line}"))
    return blocks


def _build_actions(task_type: str, status: str, artifacts: list) -> list:
    _TYPE_ACTIONS = {
        "build": ["Run the application", "Open project folder", "View logs: chotu log"],
        "coding": ["Run the script", "View logs: chotu log"],
        "search": ["Review results", "View logs: chotu log"],
        "analysis": ["Open report", "View logs: chotu log"],
        "summary": ["View summary", "View logs: chotu log"],
        "cleanup": ["Verify changes", "View logs: chotu log"],
        "automation": ["Check automation output", "View logs: chotu log"],
    }
    actions = list(_TYPE_ACTIONS.get(task_type, ["View logs: chotu log"]))

    file_artifacts = [a for a in artifacts if a["type"] == "file"]
    for art in file_artifacts[:3]:
        label = art.get("label", "")
        path = art.get("path", label)
        if label.endswith(".py"):
            actions.insert(0, f"Run: python {path}")
            break
        elif label.endswith(".js"):
            actions.insert(0, f"Run: node {path}")
            break

    if status == "failure":
        actions = ["Check issues: chotu issues", "Retry: chotu run", "View logs: chotu log"]
    elif status == "partial":
        actions.insert(0, "Retry remaining: chotu run")

    return actions[:5]


def _format_build(state: dict, artifacts: list, results: list) -> list:
    blocks = []
    file_artifacts = [a for a in artifacts if a["type"] == "file"]
    if file_artifacts:
        blocks.append(DisplayBlock(kind="artifact_links", content=[f"📄 {a['label']}" + (f"  ({a['path']})" if a["path"] != a["label"] else "") for a in file_artifacts]))
    completed = [r for r in results if r.get("verdict") == "pass"]
    if completed:
        last = completed[-1]
        if last.get("exit_code") == 0:
            blocks.append(DisplayBlock(kind="info", content=f"▶ Build completed successfully ({last.get('duration_ms', 0)}ms)"))
    return blocks


def _format_coding(state: dict, artifacts: list, results: list) -> list:
    blocks = []
    file_artifacts = [a for a in artifacts if a["type"] == "file"]
    if file_artifacts:
        blocks.append(DisplayBlock(kind="artifact_links", content=[f"📄 {a['label']}" for a in file_artifacts]))
    completed = [r for r in results if r.get("verdict") == "pass"]
    if completed:
        last = completed[-1]
        blocks.append(DisplayBlock(kind="code", content=f"exit_code: {last.get('exit_code', '?')} | {last.get('duration_ms', 0)}ms"))
    result_artifacts = [a for a in artifacts if a["type"] == "result"]
    for ra in result_artifacts[:3]:
        blocks.append(DisplayBlock(kind="info", content=ra["label"]))
    return blocks


def _format_search(state: dict, artifacts: list, results: list) -> list:
    blocks = []
    completed = [r for r in results if r["status"] == "completed"]
    if completed:
        items = [f"• {r['description']}: {r['verdict']}" for r in completed[:10]]
        blocks.append(DisplayBlock(kind="list", content=items))
    else:
        blocks.append(DisplayBlock(kind="warning", content="No search results produced."))
    return blocks


def _format_analysis(state: dict, artifacts: list, results: list) -> list:
    blocks = []
    completed = [r for r in results if r["status"] == "completed"]
    for r in completed:
        blocks.append(DisplayBlock(kind="info", content=f"📋 {r['description']}: {r['verdict']}"))
    file_artifacts = [a for a in artifacts if a["type"] == "file"]
    if file_artifacts:
        blocks.append(DisplayBlock(kind="artifact_links", content=[f"📄 {a['label']}" for a in file_artifacts]))
    return blocks


def _format_summary_task(state: dict, artifacts: list, results: list) -> list:
    blocks = []
    result_artifacts = [a for a in artifacts if a["type"] == "result"]
    for ra in result_artifacts[:3]:
        blocks.append(DisplayBlock(kind="summary", content=ra["label"]))
    completed = [r for r in results if r["status"] == "completed"]
    if completed:
        blocks.append(DisplayBlock(kind="info", content=f"Summarized {len(completed)} items"))
    return blocks


def _format_cleanup(state: dict, artifacts: list, results: list) -> list:
    blocks = []
    completed = [r for r in results if r["status"] == "completed"]
    if completed:
        items = [f"✓ {r['description']}" for r in completed]
        blocks.append(DisplayBlock(kind="list", content=items))
    return blocks


def _format_generic(state: dict, artifacts: list, results: list) -> list:
    blocks = []
    completed = [r for r in results if r["status"] == "completed"]
    if completed:
        items = [f"✓ {r['description']}" for r in completed]
        blocks.append(DisplayBlock(kind="list", content=items))
    file_artifacts = [a for a in artifacts if a["type"] == "file"]
    if file_artifacts:
        blocks.append(DisplayBlock(kind="artifact_links", content=[f"📄 {a['label']}" for a in file_artifacts]))
    return blocks


def render_cli(output: FormattedOutput) -> None:
    status_icons = {"success": "✅", "partial": "⚠️", "failure": "❌"}
    status_labels = {"success": "TASK COMPLETED", "partial": "TASK PARTIALLY COMPLETED", "failure": "TASK FAILED"}
    icon = status_icons.get(output.status, "❓")
    label = status_labels.get(output.status, "TASK FINISHED")
    type_label = f" — {output.task_type.title()} Task" if output.task_type != "unknown" else ""

    print()
    print(f"╔{'═'*54}╗")
    print(f"║  {icon} {label}{type_label:<{50 - len(label) - len(type_label)}}║")
    print(f"╚{'═'*54}╝")

    if output.summary:
        print(f"\n  {output.summary}")

    for block in output.display_blocks:
        print()
        _render_block(block)

    if output.actions:
        print(f"\n  💡 Next Actions:")
        for action in output.actions:
            print(f"     • {action}")

    print()


def _render_block(block: DisplayBlock) -> None:
    if block.kind == "heading":
        print(f"  {'─'*40}")
        print(f"  {block.content}")
    elif block.kind == "list":
        if isinstance(block.content, list):
            for item in block.content:
                print(f"     {item}")
    elif block.kind == "code":
        print(f"  ▶ {block.content}")
    elif block.kind == "summary":
        print(f"  📝 {block.content}")
    elif block.kind == "artifact_links":
        print(f"  📁 Created Files:")
        if isinstance(block.content, list):
            for item in block.content:
                print(f"     └─ {item}")
    elif block.kind == "info":
        print(f"  {block.content}")
    elif block.kind == "warning":
        print(f"  ⚠️  {block.content}")


def _build_empty_output() -> FormattedOutput:
    return FormattedOutput(
        summary="No task data available.",
        status="failure",
        results=[],
        artifacts=[],
        display_blocks=[DisplayBlock(kind="warning", content="No state data found.")],
        actions=["Create a task: chotu new <description>"],
        task_type="unknown",
        stats={},
    )