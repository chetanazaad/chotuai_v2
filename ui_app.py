"""Chotu AI Desktop Controller — Tkinter GUI Application (Chat Edition)."""
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import subprocess
import queue
import json
import os
import re
import sys
import time
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from chotu_ai import system_check

class TaskRunner(threading.Thread):
    """Runs chotu_ai CLI in a subprocess and streams output to a queue."""
    
    def __init__(self, task_text: str, model: str, output_queue: queue.Queue, is_continuation: bool = False):
        super().__init__(daemon=True)
        self.task_text = task_text
        self.model = model
        self.output_queue = output_queue
        self.is_continuation = is_continuation
        self.process = None
        self.stopped = False
    
    def run(self):
        # Determine command: 'new' or potentially 'append' (to be implemented)
        # For now, we'll use 'new' but if is_continuation is true, we might want to handle it differently
        # However, the user said "new task, new chat window, and new output folder" 
        # while continuation stays in the same chat.
        
        cmd_type = "append" if self.is_continuation else "new"
        cmd = [
            sys.executable, "-m", "chotu_ai.cli",
            cmd_type, self.task_text, "--auto-run"
        ]
        
        # We can pass the model preference via environment or a flag if we add it to CLI
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["CHOTU_MODEL"] = self.model 
        
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
    """Chat-style GUI Application for Chotu AI."""
    
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
        
        self._setup_window()
        self._setup_styles()
        self._build_header()
        self._build_chat_area()
        self._build_input_area()
        self._add_welcome_message()

    def _setup_window(self):
        self.root.title("Chotu AI — Chat Assistant")
        self.root.minsize(900, 700)
        self.root.geometry("1100x800")
        self.root.configure(bg=self.BG_DARK)
        
        self.root.grid_rowconfigure(0, minsize=60) # Header
        self.root.grid_rowconfigure(1, weight=1)    # Chat History
        self.root.grid_rowconfigure(2, minsize=100) # Input Area
        self.root.grid_columnconfigure(0, weight=1)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=self.BG_DARK)
        style.configure("TLabel", background=self.BG_DARK, foreground=self.FG_PRIMARY, font=self.FONT_LABEL)
        style.configure("TButton", font=self.FONT_BUTTON)
        style.configure("Horizontal.TProgressbar", thickness=15)
        style.configure("TCombobox", fieldbackground=self.BG_INPUT, background=self.BG_PANEL, foreground=self.FG_PRIMARY)

    def _build_header(self):
        header = tk.Frame(self.root, bg=self.BG_PANEL, height=60)
        header.grid(row=0, column=0, sticky="nsew")
        header.grid_columnconfigure(1, weight=1)
        
        # Model Selection
        tk.Label(header, text="Model:", bg=self.BG_PANEL, fg=self.FG_DIM).grid(row=0, column=0, padx=(20, 5), pady=15)
        self.model_var = tk.StringVar(value="qwen:7b")
        self.model_combo = ttk.Combobox(header, textvariable=self.model_var, values=["qwen:7b", "phi3"], state="readonly", width=10)
        self.model_combo.grid(row=0, column=1, sticky="w", pady=15)
        
        # Status & Progress
        self.status_label = tk.Label(header, text="● Idle", bg=self.BG_PANEL, fg=self.FG_DIM, font=self.FONT_LABEL)
        self.status_label.grid(row=0, column=2, padx=20)
        
        self.step_label = tk.Label(header, text="Step: -/-", bg=self.BG_PANEL, fg=self.FG_PRIMARY)
        self.step_label.grid(row=0, column=3, padx=10)
        
        self.progress = ttk.Progressbar(header, mode="determinate", length=150)
        self.progress.grid(row=0, column=4, padx=(0, 20))
        
        # New Chat Button
        tk.Button(header, text="+ New Chat", bg=self.ACCENT_BLUE, fg=self.BG_DARK, font=self.FONT_BUTTON, 
                  command=self._new_chat, relief="flat", padx=15).grid(row=0, column=5, padx=10)
        
        # Stop Button
        self.stop_button = tk.Button(header, text="Stop", bg=self.ACCENT_RED, fg=self.FG_PRIMARY, font=self.FONT_BUTTON, 
                                     command=self._stop_task, relief="flat", padx=15, state="disabled")
        self.stop_button.grid(row=0, column=6, padx=10)

    def _build_chat_area(self):
        chat_frame = tk.Frame(self.root, bg=self.BG_DARK)
        chat_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)
        chat_frame.grid_rowconfigure(0, weight=1)
        chat_frame.grid_columnconfigure(0, weight=1)
        
        self.chat_text = tk.Text(chat_frame, bg=self.BG_DARK, fg=self.FG_PRIMARY, font=self.FONT_LABEL,
                                state="disabled", wrap="word", relief="flat", padx=10, pady=10)
        self.chat_text.grid(row=0, column=0, sticky="nsew")
        
        # Tags for chat styling
        self.chat_text.tag_config("user", foreground=self.ACCENT_BLUE, font=self.FONT_TITLE)
        self.chat_text.tag_config("chotu", foreground=self.ACCENT_GREEN, font=self.FONT_TITLE)
        self.chat_text.tag_config("system", foreground=self.FG_DIM, font=self.FONT_MONO)
        self.chat_text.tag_config("error", foreground=self.ACCENT_RED, font=self.FONT_MONO)
        self.chat_text.tag_config("link", foreground=self.ACCENT_BLUE, underline=True)
        
        scrollbar = tk.Scrollbar(chat_frame, command=self.chat_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.chat_text.configure(yscrollcommand=scrollbar.set)

    def _build_input_area(self):
        input_frame = tk.Frame(self.root, bg=self.BG_PANEL, height=100)
        input_frame.grid(row=2, column=0, sticky="nsew")
        input_frame.grid_columnconfigure(0, weight=1)
        
        self.task_input = tk.Text(input_frame, bg=self.BG_INPUT, fg=self.FG_PRIMARY, font=self.FONT_LABEL,
                                 insertbackground=self.FG_PRIMARY, height=3, relief="flat", padx=10, pady=10)
        self.task_input.grid(row=0, column=0, sticky="nsew", padx=20, pady=15)
        self.task_input.bind("<Return>", self._handle_return)
        
        self.send_button = tk.Button(input_frame, text="Send", bg=self.ACCENT_BLUE, fg=self.BG_DARK, 
                                    font=self.FONT_BUTTON, command=self._run_task, relief="flat", width=10)
        self.send_button.grid(row=0, column=1, sticky="nse", padx=(0, 20), pady=15)

    def _add_welcome_message(self):
        self._append_chat("Chotu", "Hello! I am Chotu, your autonomous digital worker. What can I build for you today?")

    def _handle_return(self, event):
        if not event.state & 0x1: # If shift is NOT held
            self._run_task()
            return "break"
        return None

    def _new_chat(self):
        if self.task_runner and self.task_runner.is_alive():
            if not messagebox.askyesno("Confirm", "A task is currently running. Start a new chat anyway?"):
                return
            self._stop_task()
        
        self.chat_text.configure(state="normal")
        self.chat_text.delete(1.0, tk.END)
        self.chat_text.configure(state="disabled")
        self.current_task_id = None
        self.current_output_dir = None
        self.progress["value"] = 0
        self.step_label.configure(text="Step: -/-")
        self._set_status("Idle", self.FG_DIM)
        self._add_welcome_message()

    def _stop_task(self):
        if self.task_runner and self.task_runner.is_alive():
            self.task_runner.stop()
            self._append_chat("System", "Task stopped by user.", "error")
            self._set_status("Stopped", self.ACCENT_RED)
            self.send_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.model_combo.configure(state="readonly")

    def _append_chat(self, sender: str, message: str, tag: str = None):
        self.chat_text.configure(state="normal")
        if sender == "User":
            self.chat_text.insert(tk.END, f"\nUser\n", "user")
            self.chat_text.insert(tk.END, f"{message}\n")
        elif sender == "Chotu":
            self.chat_text.insert(tk.END, f"\nChotu\n", "chotu")
            self.chat_text.insert(tk.END, f"{message}\n")
        else:
            self.chat_text.insert(tk.END, f"\n[{sender}] {message}\n", tag or "system")
        
        self.chat_text.configure(state="disabled")
        self.chat_text.see(tk.END)

    def _run_task(self):
        task_text = self.task_input.get("1.0", tk.END).strip()
        if not task_text:
            return
        
        self.task_input.delete("1.0", tk.END)
        self._append_chat("User", task_text)
        
        # 1. Pre-flight Checks
        self._set_status("Checking System...", self.ACCENT_YELLOW)
        self._append_chat("System", "Running pre-flight checks...")
        model = self.model_var.get()
        ok, messages = system_check.run_preflight(model)
        
        for msg in messages:
            tag = "error" if "❌" in msg else "system"
            self._append_chat("System", f"• {msg}", tag)
        
        if not ok:
            self._append_chat("System", "CRITICAL: System requirements not fulfilled. Cannot proceed.", "error")
            self._set_status("Ready", self.FG_DIM)
            return
        
        self._append_chat("System", "All requirements fulfilled. Starting task...", "chotu")

        # 2. Start Task
        self.send_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.model_combo.configure(state="disabled")
        
        is_cont = self.current_task_id is not None
        self.task_runner = TaskRunner(task_text, model, self.output_queue, is_continuation=is_cont)
        self.task_runner.start()
        
        self._set_status("Running", self.ACCENT_YELLOW)
        self._poll_output()

    def _set_status(self, status: str, color: str):
        self.status_label.configure(text=f"● {status}", fg=color)

    def _poll_output(self):
        while not self.output_queue.empty():
            line = self.output_queue.get_nowait()
            
            if line.startswith("__EXIT__"):
                exit_code = int(line.replace("__EXIT__", ""))
                self._on_task_finished(exit_code)
                return
            
            self._parse_line(line)
            
        if self.task_runner and self.task_runner.is_alive():
            self.root.after(100, self._poll_output)

    def _parse_line(self, line: str):
        # We don't want to spam the chat with every single log line
        # Only important milestones
        if "[STEP START]" in line:
            match = self.STEP_PATTERN.search(line)
            if match:
                current, total = int(match.group(1)), int(match.group(2))
                self.step_label.configure(text=f"Step: {current}/{total}")
                self.progress["value"] = (current / total) * 100
                desc = line.split(":", 1)[1].strip() if ":" in line else "Executing step"
                self._append_chat("System", f"Step {current}/{total}: {desc}")
        
        elif "[ERROR]" in line or "[FATAL]" in line:
            self._append_chat("System", line.strip(), "error")
            
        elif "[TASK OUTPUT] Created folder:" in line:
            self.current_output_dir = line.split(":", 1)[1].strip()
            
        elif "[TASK INDEX] Added task:" in line:
            self.current_task_id = line.split(":", 1)[1].strip()

    def _on_task_finished(self, exit_code: int):
        self.send_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.model_combo.configure(state="readonly")
        
        if exit_code == 0:
            self._set_status("Completed", self.ACCENT_GREEN)
            self.progress["value"] = 100
            self._append_chat("Chotu", "I have completed the task successfully!")
            if self.current_output_dir:
                self._add_output_link()
        else:
            self._set_status("Failed", self.ACCENT_RED)
            self._append_chat("Chotu", "Execution encountered an error. Please check the logs.")

    def _add_output_link(self):
        self.chat_text.configure(state="normal")
        self.chat_text.insert(tk.END, "\nClick to open output folder: ")
        self.chat_text.insert(tk.END, f"{self.current_output_dir}\n", ("link", self.current_output_dir))
        self.chat_text.tag_bind(self.current_output_dir, "<Button-1>", lambda e, p=self.current_output_dir: os.startfile(p))
        self.chat_text.configure(state="disabled")
        self.chat_text.see(tk.END)

def main():
    root = tk.Tk()
    app = ChotuApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()