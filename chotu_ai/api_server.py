"""FastAPI backend for chotu_ai web interface."""
import threading
import json
import collections
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel


app = FastAPI(title="Chotu AI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_active_tasks: Dict[str, Dict[str, Any]] = {}
_task_buffers: collections.defaultdict = lambda: collections.deque(maxlen=500)


class TaskRequest(BaseModel):
    task: str
    auto_run: bool = False
    force_new: bool = False


def _run_task_thread(task_id: str, task_desc: str, auto_run: bool):
    """Background thread for task execution."""
    from . import controller
    try:
        _active_tasks[task_id]["status"] = "running"
        success = controller._run_new(task_desc, auto_run=auto_run)
        if success and auto_run:
            controller._run_loop()
        _active_tasks[task_id]["status"] = "completed" if success else "failed"
    except Exception as e:
        _active_tasks[task_id]["status"] = "failed"
        _active_tasks[task_id]["error"] = str(e)


@app.get("/system/status")
async def system_status():
    """Return system readiness check."""
    from . import system_check
    return system_check.full_check()


@app.post("/task/new")
async def create_task(req: TaskRequest):
    """Create a new task after system check."""
    from . import system_check
    readiness = system_check.full_check()
    if not readiness["ready"]:
        return JSONResponse(
            status_code=400,
            content={
                "error": "System not ready",
                "diagnostics": readiness
            }
        )

    task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    _active_tasks[task_id] = {
        "description": req.task,
        "status": "pending",
        "started_at": datetime.now().isoformat(),
        "thread": None,
    }

    thread = threading.Thread(
        target=_run_task_thread,
        args=(task_id, req.task, req.auto_run)
    )
    _active_tasks[task_id]["thread"] = thread
    thread.start()

    return {
        "task_id": task_id,
        "description": req.task,
        "status": "started"
    }


@app.post("/task/run")
async def run_task():
    """Resume paused task."""
    from . import controller
    try:
        controller._run_loop()
        return {"status": "completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/task/list")
async def list_tasks():
    """List all tasks."""
    from . import task_index
    return task_index.list_tasks()


@app.get("/task/{task_id}")
async def get_task(task_id: str):
    """Get task state and logs."""
    state_file = Path(f".chotu/state.json")
    log_file = Path(f".chotu/logs/{task_id}.log")

    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    logs = []
    if log_file.exists():
        try:
            logs = log_file.read_text(encoding="utf-8").splitlines()
        except Exception:
            pass

    output_dir = state.get("core_task", {}).get("output_dir", "")
    output_files = []
    if output_dir:
        out_path = Path(output_dir)
        if out_path.exists():
            for f in out_path.iterdir():
                if f.is_file():
                    output_files.append({
                        "name": f.name,
                        "size": f.stat().st_size
                    })

    return {
        "state": state,
        "logs": logs[-100:],
        "output_files": output_files,
        "status": _active_tasks.get(task_id, {}).get("status", "unknown")
    }


@app.get("/task/{task_id}/logs")
async def get_task_logs(task_id: str, lines: int = 50):
    """Get last N lines of task log."""
    log_file = Path(f".chotu/logs/{task_id}.log")
    if not log_file.exists():
        return {"logs": []}

    content = log_file.read_text(encoding="utf-8")
    all_lines = content.splitlines()
    return {"logs": all_lines[-lines:]}


@app.get("/output/{task_id}")
async def list_output_files(task_id: str):
    """List files in task output directory."""
    state_file = Path(f".chotu/state.json")
    if not state_file.exists():
        return {"files": []}

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        output_dir = state.get("core_task", {}).get("output_dir", "")
        if not output_dir:
            return {"files": []}

        out_path = Path(output_dir)
        if not out_path.exists():
            return {"files": []}

        files = []
        for f in out_path.iterdir():
            if f.is_file():
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size
                })
        return {"files": files}
    except Exception:
        return {"files": []}


@app.get("/output/{task_id}/{filename}")
async def serve_output_file(task_id: str, filename: str):
    """Serve an output file."""
    state_file = Path(f".chotu/state.json")
    if not state_file.exists():
        raise HTTPException(status_code=404, detail="State not found")

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        output_dir = state.get("core_task", {}).get("output_dir", "")
        if not output_dir:
            raise HTTPException(status_code=404, detail="Output dir not found")

        file_path = Path(output_dir) / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")

        return FileResponse(file_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))