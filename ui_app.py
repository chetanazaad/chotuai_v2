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
from pathlib import Path


class TaskRunner(threading.Thread):
    """Runs chotu_ai CLI in a subprocess and streams output to a queue."""
    
    def __init__(self, task_text: str, output_queue: queue.Queue):
        super().__init__(daemon=True)
        self.task_text = task_text
        self.output_queue = output_queue
        self.process = None
        self.stopped = False
    
    def run(self):
        cmd = [
            sys.executable, "-m", "chotu_ai.cli",
            "new", self.task_text, "--auto-run"
        ]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        
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
        
        self._setup_window()
        self._setup_styles()
        self._build_input_panel()
        self._build_status_bar()
        self._build_output_panel()
        self._build_action_buttons()
        self._load_past_tasks()
    
    def _setup_window(self):
        self.root.title("Chotu AI — Desktop Controller")
        self.root.minsize(900, 650)
        self.root.geometry("1000x700")
        self.root.configure(bg=self.BG_DARK)
        
        self.root.grid_rowconfigure(0, minsize=80)
        self.root.grid_rowconfigure(1, minsize=50)
        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_rowconfigure(3, minsize=50)
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
        
        self.past_tasks_var = tk.StringVar()
        self.past_tasks_combo = ttk.Combobox(
            frame,
            textvariable=self.past_tasks_var,
            state="readonly",
            font=self.FONT_LABEL
        )
        self.past_tasks_combo.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.past_tasks_combo.bind("<<ComboboxSelected>>", self._on_task_selected)
        
        self.task_input = tk.Entry(
            frame,
            bg=self.BG_INPUT,
            fg=self.FG_PRIMARY,
            font=self.FONT_LABEL,
            insertbackground=self.FG_PRIMARY
        )
        self.task_input.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.task_input.bind("<Return>", lambda e: self._run_task())
        
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
    
    def _build_status_bar(self):
        frame = tk.Frame(self.root, bg=self.BG_PANEL)
        frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 5))
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
        frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)
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
        frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(5, 10))
        
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
        self._clear_output()
        
        self.task_runner = TaskRunner(task_text, self.output_queue)
        self.task_runner.start()
        
        self._set_status("Running", self.ACCENT_YELLOW)
        self._poll_output()
    
    def _stop_task(self):
        if self.task_runner and self.task_runner.is_alive():
            self.task_runner.stop()
            self._set_status("Stopped", self.ACCENT_RED)
    
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
            self.root.after(100, self._poll_output)
        
        while not self.output_queue.empty():
            line = self.output_queue.get_nowait()
            
            if line.startswith("__EXIT__"):
                exit_code = int(line.replace("__EXIT__", ""))
                self._on_task_finished(exit_code)
                return
            
            self._append_output(line)
            self._parse_status(line)
    
    def _append_output(self, line: str):
        self.output_text.configure(state="normal")
        self.output_text.insert(tk.END, line + "\n", self._get_line_tag(line))
        self.output_text.configure(state="disabled")
        
        if self._should_autoscroll():
            self.output_text.see(tk.END)
    
    def _get_line_tag(self, line: str) -> str:
        if "[STEP START]" in line:
            return "step_start"
        elif "[STEP DONE]" in line:
            return "step_done"
        elif "[LLM]" in line:
            return "llm"
        elif "[ERROR]" in line or "[FATAL]" in line or "[WARNING]" in line:
            return "error"
        elif "[CONTROLLER]" in line or "[ENFORCED]" in line:
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
    
    def _set_status(self, status: str, color: str):
        self.status_indicator.configure(text=f"● {status}", fg=color)
    
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
        if self.current_output_dir and os.path.exists(self.current_output_dir):
            os.startfile(self.current_output_dir)
    
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