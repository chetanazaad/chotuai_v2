# chotu_ai

A deterministic, state-driven autonomous execution engine that can accept a natural-language task, decompose it into steps, execute step-by-step, evaluate results, retry failures, log everything, and resume after crashes — all via a CLI.

## Core Principle

**STATE → PLAN → EXECUTE → VALIDATE → DECIDE → LEARN → OPTIMIZE → OUTPUT**

## Quick Start

```bash
# Create a new task (hello world - uses fallback decomposition, no LLM required)
python -m chotu_ai.cli new "create a hello world python script"

# Check status
python -m chotu_ai.cli status

# Show the plan
python -m chotu_ai.cli plan

# Execute the task
python -m chotu_ai.cli run

# Show logs
python -m chotu_ai.cli log

# Show issues
python -m chotu_ai.cli issues
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `new "<task>"` | Start a new task |
| `run` | Resume/rexecute the task |
| `status` | Show task status |
| `plan` | Show the task plan |
| `log [step_id]` | Show logs (optional step ID) |
| `issues` | Show issues |
| `skip` | Skip current step |
| `reset` | Reset current step |
| `abort` | Abort the task |

## State Invariants

1. **Single Writer**: Only controller.py calls state_manager.save()
2. **Atomic Writes**: Write temp → rename. Never partial writes.
3. **Always Valid**: Validate before every save. Invalid state = ValueError.
4. **Monotonic Timestamps**: updated_at never decreases.
5. **Stats = f(todo_list)**: Stats are always recomputed, never manually edited.
6. **No backward transitions**: completed → never back to pending. failed → only to skipped (manual).
7. **current_step truth**: Always reflects actual execution phase or is null.

## Step Status State Machine

```
pending → generating → executing → evaluating → completed
                         ↓
                      improving → failed → skipped
```

## Failure Handling

| Error Type | Detection | Default Suggestion |
|------------|-----------|---------------------|
| syntax_error | SyntaxError, IndentationError in stderr | Fix the syntax error in the generated code |
| missing_dependency | ModuleNotFoundError, ImportError in stderr | Install the missing dependency |
| infrastructure | FileNotFoundError, PermissionError, OSError | Check file paths and permissions |
| timeout | exec_result.timed_out == True | Increase timeout or simplify the step |
| runtime_error | Non-zero exit code (catch-all) | Review the error output and adjust the action |

## Runtime Directories

- `.chotu/state.json` - State file
- `.chotu/events.jsonl` - Event log
- `.chotu/issues.jsonl` - Issues log
- `.chotu/decisions.jsonl` - Decisions log
- `.chotu/resolutions.jsonl` - Resolutions log
- `.chotu/logs/step_*.log` - Step logs

## Features

- **Deterministic**: Works without LLM using fallback keyword-based decomposition
- **Restart-safe**: Can resume after crashes by rolling back to last safe state
- **Structured logging**: JSONL events + human-readable step logs
- **Retry logic**: Automatic retry with configurable max attempts
- **Error classification**: Auto-classifies errors and provides suggestions

## Requirements

- Python 3.8+
- No external dependencies (uses standard library only)

## Phase 1 Scope

- CLI-only interface
- Local shell commands and file operations
- Fallback decomposition (no LLM required)
- No browser automation
- No cloud APIs
- No GUI