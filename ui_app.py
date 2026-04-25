"""Chotu AI Desktop Controller — Tkinter GUI Application."""
import tkinter as tk
from tkinter import ttk
import threading
import subprocess
import queue
import json
import os
import re
import sys
import urllib.request
from pathlib import Path


class TaskRunner(threading.Thread):
    """Runs chotu_ai CLI in a subprocess and streams output to a queue."""
    
    def __init__(self, task_text: str, output_queue: queue.Queue, forced_model: str = None):
        super().__init__(daemon=True)
        self.task_text = task_text
        self.output_queue = output_queue
        self.forced_model = forced_model
        self.process = None
        self.stopped = False
    
    def run(self):
        cmd = [
            sys.executable, "-m", "chotu_ai.cli",
            "new", self.task_text, "--auto-run"
        ]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        # FIX 5: Pass forced model via environment variable
        if self.forced_model and self.forced_model != "auto":
            env["CHOTU_FORCED_MODEL"] = self.forced_model
            print(f"[UI] Setting forced model: {self.forced_model}")
        
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(Path(__file__).parent),
            env=env
        )
        for line in iter(self.process.stdout.readline, ""):
            if self.stopped:
                break
            self.output_queue.put(line.rstrip("\n"))
        
        self.process.wait()
        exit_code = self.process.returncode
        self.output_queue.put(f"__EXIT__{exit_code}")
    
    def stop(self):
        self.stopped = True
        if self.process:
            self.process.terminate()



class ChotuApp:
    """Main GUI Application for Chotu AI."""
    
    BG_DARK = "#1e1e2e"
    BG_PANEL = "#282840"
    BG_INPUT = "#313154"
    FG_PRIMARY = "#cdd6f4"
    FG_DIM = "#6c7086"
    ACCENT_BLUE = "#89b4fa"
    ACCENT_GREEN = "#a6e3a1"
    ACCENT_RED = "#f38ba8"
    ACCENT_YELLOW = "#f9e2af"
    ACCENT_PURPLE = "#cba6f7"
    
    FONT_MONO = ("Consolas", 11)
    FONT_LABEL = ("Segoe UI", 11)
    FONT_TITLE = ("Segoe UI", 13, "bold")
    FONT_BUTTON = ("Segoe UI", 10, "bold")
    
    STEP_PATTERN = re.compile(r'\[STEP START\] Step (\d+)/(\d+)')
    DONE_PATTERN = re.compile(r'Build completed successfully|Stats:.*completed|\[TASK SUMMARY\]')
    FAIL_PATTERN = re.compile(r'\[FATAL\]|\[ERROR\]|failed')
    FOLDER_PATTERN = re.compile(r'\[TASK OUTPUT\] Created folder: (.+)')
    TASK_ID_PATTERN = re.compile(r'\[TASK INDEX\] Added task: (.+)')
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.output_queue = queue.Queue()
        self.task_runner = None
        self.current_task_id = None
        self.current_output_dir = None
        self._autoscroll = True
        self._pending_autoscroll = False
        self._llm_mode = "IDLE"  # IDLE, LLM, FALLBACK
        self._events_file_pos = 0
        try:
            events_file = Path(".chotu/events.jsonl")
            if events_file.exists():
                self._events_file_pos = events_file.stat().st_size
        except Exception:
            pass
        self._last_state_step = ""
        
        self._setup_window()
        self._setup_styles()
        self._build_input_panel()
        self._build_system_panel()
        self._build_status_bar()
        self._build_output_panel()
        self._build_action_buttons()
        self._load_past_tasks()
        
        self._check_system_status() # Start Ollama background check
        self._poll_events_and_state() # Start 500ms polling loop
    
    def _setup_window(self):
        self.root.title("Chotu AI — Desktop Controller")
        self.root.minsize(900, 650)
        self.root.geometry("1000x750")
        self.root.configure(bg=self.BG_DARK)
        
        self.root.grid_rowconfigure(0, minsize=80)   # input panel
        self.root.grid_rowconfigure(1, minsize=40)   # system status panel
        self.root.grid_rowconfigure(2, minsize=50)   # step progress bar
        self.root.grid_rowconfigure(3, weight=1)     # output panel
        self.root.grid_rowconfigure(4, minsize=50)   # action buttons
        self.root.grid_columnconfigure(0, weight=1)
    
    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=self.BG_DARK)
        style.configure("TLabel", background=self.BG_DARK, foreground=self.FG_PRIMARY, font=self.FONT_LABEL)
        style.configure("TButton", font=self.FONT_BUTTON)
        style.configure("Horizontal.TProgressbar", thickness=20)
    
    def _build_input_panel(self):
        frame = tk.Frame(self.root, bg=self.BG_PANEL)
        frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        frame.grid_columnconfigure(1, weight=1)
        
        # Model label and dropdown in column 0
        label_frame = tk.Frame(frame, bg=self.BG_PANEL)
        label_frame.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        
        tk.Label(label_frame, text="Model:", bg=self.BG_PANEL, fg=self.FG_DIM, font=self.FONT_LABEL).pack(side="left")
        
        self.model_var = tk.StringVar(value="auto")
        self.model_combo = ttk.Combobox(
            label_frame,
            textvariable=self.model_var,
            values=["auto", "phi3", "qwen:7b"],
            state="readonly",
            font=self.FONT_LABEL,
            width=8
        )
        self.model_combo.pack(side="left", padx=(5, 0))
        
        # Task input in column 1
        self.task_input = tk.Entry(
            frame,
            bg=self.BG_INPUT,
            fg=self.FG_PRIMARY,
            font=self.FONT_LABEL,
            insertbackground=self.FG_PRIMARY
        )
        self.task_input.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.task_input.bind("<Return>", lambda e: self._run_task())
        
        # Run button in column 2
        self.run_button = tk.Button(
            frame,
            text="Run Task",
            bg=self.ACCENT_BLUE,
            fg=self.BG_DARK,
            font=self.FONT_BUTTON,
            command=self._run_task,
            relief="flat",
            padx=20
        )
        self.run_button.grid(row=0, column=2, sticky="e")
    
    def _build_system_panel(self):
        """Build the system status panel showing Ollama, Model, LLM, and Mode status."""
        frame = tk.Frame(self.root, bg=self.BG_PANEL)
        frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 3))
        for c in range(4):
            frame.grid_columnconfigure(c, weight=1)
        
        lbl_font = ("Segoe UI", 9)
        
        self.sys_ollama = tk.Label(frame, text="Ollama: ...", fg=self.FG_DIM, bg=self.BG_PANEL, font=lbl_font)
        self.sys_ollama.grid(row=0, column=0, sticky="w", padx=10, pady=4)
        
        self.sys_model = tk.Label(frame, text="Model: ...", fg=self.FG_DIM, bg=self.BG_PANEL, font=lbl_font)
        self.sys_model.grid(row=0, column=1, sticky="w", padx=10, pady=4)
        
        self.sys_llm = tk.Label(frame, text="LLM: IDLE", fg=self.FG_DIM, bg=self.BG_PANEL, font=lbl_font)
        self.sys_llm.grid(row=0, column=2, sticky="w", padx=10, pady=4)
        
        self.sys_mode = tk.Label(frame, text="Mode: IDLE", fg=self.FG_DIM, bg=self.BG_PANEL, font=lbl_font)
        self.sys_mode.grid(row=0, column=3, sticky="w", padx=10, pady=4)

    def _build_status_bar(self):
        frame = tk.Frame(self.root, bg=self.BG_PANEL)
        frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 5))
        frame.grid_columnconfigure(1, weight=1)
        
        self.status_indicator = tk.Label(
            frame,
            text="● Idle",
            fg=self.FG_DIM,
            bg=self.BG_PANEL,
            font=self.FONT_LABEL
        )
        self.status_indicator.grid(row=0, column=0, sticky="w", padx=10)
        
        self.step_label = tk.Label(
            frame,
            text="Step: -/-",
            fg=self.FG_PRIMARY,
            bg=self.BG_PANEL,
            font=self.FONT_LABEL
        )
        self.step_label.grid(row=0, column=1, sticky="w", padx=20)
        
        self.progress = ttk.Progressbar(
            frame,
            mode="determinate",
            length=200
        )
        self.progress.grid(row=0, column=2, sticky="ew", padx=10)
    
    def _build_output_panel(self):
        frame = tk.Frame(self.root, bg=self.BG_PANEL)
        frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=5)
        # (grid already set above)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        
        self.output_text = tk.Text(
            frame,
            bg=self.BG_DARK,
            fg=self.FG_PRIMARY,
            font=self.FONT_MONO,
            state="disabled",
            wrap="word",
            relief="flat"
        )
        self.output_text.grid(row=0, column=0, sticky="nsew")
        
        self.output_text.tag_config("step_start", foreground=self.ACCENT_BLUE)
        self.output_text.tag_config("step_done", foreground=self.ACCENT_GREEN)
        self.output_text.tag_config("llm", foreground=self.ACCENT_PURPLE)
        self.output_text.tag_config("error", foreground=self.ACCENT_RED)
        self.output_text.tag_config("controller", foreground=self.ACCENT_YELLOW)
        self.output_text.tag_config("file", foreground=self.FG_DIM)
        
        self.scrollbar = tk.Scrollbar(frame, command=self.output_text.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.output_text.configure(yscrollcommand=self._on_text_scroll)
    
    def _build_action_buttons(self):
        frame = tk.Frame(self.root, bg=self.BG_PANEL)
        frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=(5, 10))
        
        tk.Button(
            frame,
            text="Open Output",
            bg=self.BG_INPUT,
            fg=self.FG_PRIMARY,
            font=self.FONT_BUTTON,
            command=self._open_output_folder,
            relief="flat",
            padx=15
        ).grid(row=0, column=0, padx=5)
        
        tk.Button(
            frame,
            text="View Logs",
            bg=self.BG_INPUT,
            fg=self.FG_PRIMARY,
            font=self.FONT_BUTTON,
            command=self._view_logs,
            relief="flat",
            padx=15
        ).grid(row=0, column=1, padx=5)
        
        tk.Button(
            frame,
            text="Stop Task",
            bg=self.ACCENT_RED,
            fg=self.BG_DARK,
            font=self.FONT_BUTTON,
            command=self._stop_task,
            relief="flat",
            padx=15
        ).grid(row=0, column=2, padx=5)
        
        tk.Button(
            frame,
            text="Clear",
            bg=self.BG_INPUT,
            fg=self.FG_PRIMARY,
            font=self.FONT_BUTTON,
            command=self._clear_output,
            relief="flat",
            padx=15
        ).grid(row=0, column=3, padx=5)
    
    def _load_past_tasks(self):
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from chotu_ai import task_index
            tasks = task_index.list_tasks()
            tasks.reverse()
            
            display_tasks = []
            for i, t in enumerate(tasks[:20], 1):
                name = t.get("task_name", "")[:60]
                status = t.get("status", "")
                display_tasks.append(f"[{i}] {name} ({status})")
            
            if hasattr(self, 'past_tasks_combo'):
                self.past_tasks_combo["values"] = display_tasks
            self.past_tasks = tasks
        except Exception as e:
            self.past_tasks = []
            print(f"Failed to load past tasks: {e}")
    
    def _on_task_selected(self, event):
        idx = self.past_tasks_combo.current()
        if idx >= 0 and idx < len(self.past_tasks):
            task = self.past_tasks[idx]
            self.task_input.delete(0, tk.END)
            self.task_input.insert(0, task.get("task_name", ""))
            self.current_output_dir = task.get("output_dir", "")
            self.current_task_id = task.get("task_id", "")
    
    def _run_task(self):
        task_text = self.task_input.get().strip()
        if not task_text:
            return
        
        self.run_button.configure(state="disabled")
        
        # FIX 4: Chat-like interface - only clear if it's a completely new prompt
        if not hasattr(self, '_last_task_text') or self._last_task_text != task_text:
            self._clear_output()
            self._last_task_text = task_text
        
        self._append_output(f"\n[USER PROMPT] {task_text}\n" + "-"*40)
        
        # FIX 5: Get forced model from dropdown
        forced_model = self.model_var.get()
        if forced_model == "auto":
            forced_model = None
        
        self.task_runner = TaskRunner(task_text, self.output_queue, forced_model)
        self.task_runner.start()
        
        self._set_status("Running", self.ACCENT_YELLOW)
        self._poll_output()
    
    def _stop_task(self):
        if self.task_runner and self.task_runner.is_alive():
            print("[TASK STOPPED BY USER]")
            self._append_output("\n[TASK STOPPED BY USER]")
            self.task_runner.stop()
            self._set_status("Stopped", self.ACCENT_RED)
            self._update_sys_mode("IDLE")
            
            # Force update state file if we can
            try:
                state_file = Path(".chotu/state.json")
                if state_file.exists():
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                    state["core_task"]["status"] = "stopped"
                    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
            except Exception:
                pass
    
    def _on_task_finished(self, exit_code: int):
        self.run_button.configure(state="normal")
        
        if exit_code == 0:
            self._set_status("Completed", self.ACCENT_GREEN)
            self.progress["value"] = 100
        else:
            self._set_status("Failed", self.ACCENT_RED)
        
        self._load_past_tasks()
    
    def _poll_output(self):
        if self.task_runner and self.task_runner.is_alive():
            self.root.after(300, self._poll_output)
        
        while not self.output_queue.empty():
            line = self.output_queue.get_nowait()
            
            if line.startswith("__EXIT__"):
                exit_code = int(line.replace("__EXIT__", ""))
                self._on_task_finished(exit_code)
                return
            
            # Use events.jsonl for UI logging instead of stdout
            # self._append_output(line)
            self._parse_status(line)
    
    def _append_output(self, line: str):
        self.output_text.configure(state="normal")
        self.output_text.insert(tk.END, line + "\n", self._get_line_tag(line))
        self.output_text.configure(state="disabled")
        
        if self._should_autoscroll():
            self.output_text.see(tk.END)
    
    def _get_line_tag(self, line: str) -> str:
        if "[STEP]" in line or "[STEP START]" in line:
            return "step_start"
        elif "[RESULT]" in line or "[STEP DONE]" in line:
            return "step_done"
        elif "[LLM]" in line or "[USER PROMPT]" in line:
            return "llm"
        elif "[ERROR]" in line or "[FATAL]" in line or "[WARNING]" in line:
            return "error"
        elif "[SYSTEM PLAN]" in line or "[CONTROLLER]" in line or "[ENFORCED]" in line:
            return "controller"
        elif "[FILE TARGET]" in line or "[OUTPUT]" in line or "[TASK OUTPUT]" in line:
            return "file"
        return "default"
    
    def _parse_status(self, line: str):
        match = self.STEP_PATTERN.search(line)
        if match:
            current, total = int(match.group(1)), int(match.group(2))
            self.step_label.configure(text=f"Step: {current}/{total}")
            self.progress["value"] = (current / total) * 100
            return
        
        if self.DONE_PATTERN.search(line):
            self._set_status("Completed", self.ACCENT_GREEN)
            self.progress["value"] = 100
            self._update_sys_mode("COMPLETE")
            return
        
        if self.FAIL_PATTERN.search(line):
            self._set_status("Failed", self.ACCENT_RED)
            return
        
        folder_match = self.FOLDER_PATTERN.search(line)
        if folder_match:
            self.current_output_dir = folder_match.group(1).strip()
        
        task_id_match = self.TASK_ID_PATTERN.search(line)
        if task_id_match:
            self.current_task_id = task_id_match.group(1).strip()
        
        # ── System panel live updates from log output ──
        if "[LLM]" in line and "using" in line:
            # e.g. [LLM] using local: phi3
            model_match = re.search(r'using\s+\w+:\s*(\S+)', line)
            if model_match:
                model_name = model_match.group(1)
                self.sys_model.configure(text=f"Model: {model_name}", fg=self.ACCENT_BLUE)
            self.sys_llm.configure(text="LLM: ACTIVE", fg=self.ACCENT_GREEN)
            self._update_sys_mode("LLM")
        
        if "[LLM RETRY 1]" in line:
            self.sys_llm.configure(text="LLM: RETRY", fg=self.ACCENT_YELLOW)
        
        if "[MODEL SWITCH" in line:
            model_match = re.search(r'MODEL SWITCH.*?→\s*(\S+)', line)
            if model_match:
                self.sys_model.configure(text=f"Model: {model_match.group(1)}", fg=self.ACCENT_YELLOW)
            self.sys_llm.configure(text="LLM: SWITCHING", fg=self.ACCENT_YELLOW)
        
        if "[ESCALATION" in line:
            self.sys_llm.configure(text="LLM: CLOUD", fg=self.ACCENT_PURPLE)
            self._update_sys_mode("CLOUD")
        
        if "[LLM STATUS] FAILED" in line or "All retry steps exhausted" in line:
            self.sys_llm.configure(text="LLM: FAILED", fg=self.ACCENT_RED)
            self._update_sys_mode("FALLBACK")
        
        if "[LLM CACHE HIT]" in line:
            self.sys_llm.configure(text="LLM: CACHED", fg=self.ACCENT_GREEN)
    
    def _set_status(self, status: str, color: str):
        self.status_indicator.configure(text=f"● {status}", fg=color)

    def _update_sys_mode(self, mode: str):
        """Update the Mode indicator on the system panel."""
        colors = {
            "IDLE": self.FG_DIM,
            "LLM": self.ACCENT_GREEN,
            "FALLBACK": self.ACCENT_RED,
            "CLOUD": self.ACCENT_PURPLE,
            "COMPLETE": self.ACCENT_GREEN,
        }
        self._llm_mode = mode
        self.sys_mode.configure(text=f"Mode: {mode}", fg=colors.get(mode, self.FG_DIM))

    def _check_system_status(self):
        """Background check of Ollama and model availability. Runs once on startup."""
        def _check():
            # Check Ollama
            ollama_ok = False
            models = []
            try:
                req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        ollama_ok = True
                        import json as _json
                        data = _json.loads(resp.read().decode())
                        models = [m.get("name", "") for m in data.get("models", [])]
            except Exception:
                pass
            
            # Update UI from main thread
            def _apply():
                if ollama_ok:
                    self.sys_ollama.configure(text="✔ Ollama: Running", fg=self.ACCENT_GREEN)
                    model_str = ", ".join(m.split(":")[0] for m in models[:3]) if models else "none"
                    self.sys_model.configure(text=f"Model: {model_str}", fg=self.ACCENT_BLUE)
                else:
                    self.sys_ollama.configure(text="✗ Ollama: Offline", fg=self.ACCENT_RED)
                    self.sys_model.configure(text="Model: N/A", fg=self.ACCENT_RED)
            self.root.after(0, _apply)
        
        t = threading.Thread(target=_check, daemon=True)
        t.start()

    def _poll_events_and_state(self):
        """FIX 3: Polling state.json and events.jsonl every 300ms (Central State Authority)."""
        # 1. Read state.json for real-time task state (FIX 3)
        try:
            state_file = Path(".chotu/state.json")
            if state_file.exists():
                with open(state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                
                # Update status
                core_task = state.get("core_task", {})
                status = core_task.get("status", "idle")
                if status == "running":
                    self._set_status("Running", self.ACCENT_YELLOW)
                elif status == "completed":
                    self._set_status("Completed", self.ACCENT_GREEN)
                elif status == "failed":
                    self._set_status("Failed", self.ACCENT_RED)
                
                # Update step count
                todo_list = state.get("todo_list", [])
                completed_list = state.get("completed_steps", [])
                total = len(todo_list)
                step_num = len(completed_list)
                if status == "running" and step_num < total:
                    step_num += 1
                
                self.step_label.configure(text=f"Step: {step_num}/{total}")
                if total > 0:
                    self.progress["value"] = (len(completed_list) / total) * 100
                
                # FIX 4: Error Visibility
                last_issue = state.get("issues", [])[-1] if state.get("issues") else None
                if status == "failed" and last_issue:
                    err_msg = f"[ERROR] {last_issue.get('step_id', 'Unknown Step')} failed\nReason: {last_issue.get('description', 'Unknown Error')}"
                    if not hasattr(self, '_last_rendered_error') or self._last_rendered_error != err_msg:
                        self._append_output(err_msg)
                        self._last_rendered_error = err_msg
                    
        except Exception:
            pass

        # 2. Read events.jsonl for log streaming (FIX 1)
        try:
            events_file = Path(".chotu/events.jsonl")
            if events_file.exists():
                with open(events_file, "r", encoding="utf-8") as f:
                    f.seek(self._events_file_pos)
                    new_lines = f.readlines()
                    self._events_file_pos = f.tell()
                    
                    for line in new_lines:
                        if not line.strip(): continue
                        try:
                            event = json.loads(line)
                            ev_type = event.get("event_type", "")
                            msg = event.get("message", "")
                            payload = event.get("payload", {})
                            
                            formatted_log = ""
                            
                            if ev_type == "gateway_start":
                                formatted_log = f"[LLM] Sending request to {payload.get('provider', 'unknown')}"
                            elif ev_type == "step_start":
                                formatted_log = f"[STEP] Starting {event.get('step_id')} - {msg}"
                            elif ev_type == "task_complete":
                                formatted_log = f"[RESULT] {msg}"
                            elif ev_type == "error":
                                formatted_log = f"[ERROR] {msg}"
                            elif "retry" in ev_type:
                                formatted_log = f"[LLM RETRY] {msg}"
                            elif ev_type == "gateway_failure":
                                formatted_log = f"[ERROR] LLM Failed: {msg}"
                            elif ev_type == "task_created":
                                formatted_log = f"[SYSTEM PLAN] {msg}"
                            
                            if formatted_log:
                                self._append_output(formatted_log)
                        except Exception:
                            pass
        except Exception:
            pass
            
        # Poll every 300ms
        self.root.after(300, self._poll_events_and_state)
    
    def _should_autoscroll(self) -> bool:
        try:
            return self.output_text.yview()[1] >= 0.98
        except Exception:
            return True
    
    def _on_text_scroll(self, first, last):
        """Update scrollbar and detect manual scroll."""
        self.scrollbar.set(first, last)
        
        # If user scrolls up, disable autoscroll
        if float(last) < 1.0:
            self._autoscroll = False
        else:
            self._autoscroll = True
    
    def _open_output_folder(self):
        output_dir = self.current_output_dir
        try:
            state_file = Path(".chotu/state.json")
            if state_file.exists():
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
                out_dir = state_data.get("core_task", {}).get("output_dir", "")
                if out_dir:
                    output_dir = out_dir
        except Exception:
            pass
            
        print(f"[UI] Opening output folder: {output_dir}")
        if output_dir and os.path.exists(output_dir):
            if sys.platform == "win32":
                os.startfile(output_dir)
            else:
                import subprocess
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.call([opener, output_dir])
        else:
            import tkinter.messagebox as messagebox
            messagebox.showerror("Error", f"Output folder not found:\n{output_dir}")
    
    def _view_logs(self):
        if self.current_task_id:
            log_path = Path(".chotu/logs") / f"{self.current_task_id}.log"
            if log_path.exists():
                os.startfile(log_path)
            else:
                tk.messagebox.showinfo("Logs", f"No log file found: {log_path}")
    
    def _clear_output(self):
        self.output_text.configure(state="normal")
        self.output_text.delete(1.0, tk.END)
        self.output_text.configure(state="disabled")
        self.step_label.configure(text="Step: -/-")
        self.progress["value"] = 0



def main():
    root = tk.Tk()
    app = ChotuApp(root)
    root.mainloop()



if __name__ == "__main__":
    main()