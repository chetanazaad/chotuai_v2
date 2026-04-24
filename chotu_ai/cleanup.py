import os
import shutil
import fnmatch
from pathlib import Path

SAFE_ROOT_FILES = [
    "setup.py",
    "README.md",
    "requirements.txt",
    "status.md",
    "update_status.py", # Added to be safe since we use it
    ".gitignore",
    "LICENSE"
]

SAFE_DIRS = [
    "chotu_ai",
    ".chotu",
    "venv",
    "output",
    ".git",
    ".gemini" # Used by the environment
]

JUNK_PATTERNS = [
    "hello.py",
    "test_*.py",
    "*_test.py",
    "calculator_*.py",
    "calculator_*.sh",
    "*.sh",
    "dummy*.html",
    "*.tmp",
    "*.bak"
]

def run_cleanup(dry_run=True):
    """Main cleanup logic."""
    files_to_delete = []
    dirs_to_delete = []

    # 1. Scan Root Directory for Junk Files and Clutter
    for item in os.listdir("."):
        if os.path.isfile(item):
            # Check junk patterns
            is_junk = any(fnmatch.fnmatch(item.lower(), p) for p in JUNK_PATTERNS)
            # Check root clutter (if not safe and not junk already)
            is_clutter = item not in SAFE_ROOT_FILES and item.lower() not in [f.lower() for f in SAFE_ROOT_FILES]
            
            # Special case for output.txt outside output/
            if item == "output.txt":
                is_junk = True
            
            # Special case for .log outside .chotu/
            if item.endswith(".log"):
                is_junk = True

            if is_junk or is_clutter:
                # Double check it's not a core system file we somehow missed
                if item not in SAFE_ROOT_FILES:
                    files_to_delete.append(os.path.abspath(item))

        elif os.path.isdir(item):
            if item.lower() in ["workspace", "tmp", "__pycache__"]:
                dirs_to_delete.append(os.path.abspath(item))
            elif item not in SAFE_DIRS:
                # Any other directory not in safe list
                dirs_to_delete.append(os.path.abspath(item))

    # 2. Recursive Pycache Search
    for root, dirs, files in os.walk(".", topdown=False):
        for name in dirs:
            if name == "__pycache__":
                dirs_to_delete.append(os.path.abspath(os.path.join(root, name)))

    # 3. Clean Output Directory
    if os.path.exists("output"):
        allowed_output = ["index.html", "article.html", "contact.html", "tasks", "shared"]
        for item in os.listdir("output"):
            if item not in allowed_output:
                path = os.path.join("output", item)
                if os.path.isfile(path):
                    files_to_delete.append(os.path.abspath(path))
                elif os.path.isdir(path):
                    dirs_to_delete.append(os.path.abspath(path))

    # Remove duplicates from lists
    files_to_delete = list(set(files_to_delete))
    dirs_to_delete = list(set(dirs_to_delete))

    # filter out any safe dirs/files that might have been picked up
    files_to_delete = [f for f in files_to_delete if os.path.basename(f) not in SAFE_ROOT_FILES]
    dirs_to_delete = [d for d in dirs_to_delete if os.path.basename(d) not in SAFE_DIRS]

    if not files_to_delete and not dirs_to_delete:
        print("[CLEANUP] System already clean.")
        return

    print("\n[CLEANUP] Items to delete:")
    for d in dirs_to_delete:
        print(f"  [DIR]  {os.path.relpath(d)}")
    for f in files_to_delete:
        print(f"  [FILE] {os.path.relpath(f)}")

    if dry_run:
        confirm = input("\nProceed? (y/n): ").lower()
        if confirm != 'y':
            print("[CLEANUP] Aborted.")
            return

    # 4. EXECUTE CLEANUP
    files_removed = 0
    dirs_removed = 0

    for f in files_to_delete:
        try:
            if os.path.exists(f):
                os.remove(f)
                files_removed += 1
        except Exception as e:
            print(f"[CLEANUP ERROR] Failed to remove file {f}: {e}")

    for d in dirs_to_delete:
        try:
            if os.path.exists(d):
                shutil.rmtree(d)
                dirs_removed += 1
        except Exception as e:
            print(f"[CLEANUP ERROR] Failed to remove directory {d}: {e}")

    print(f"\n[CLEANUP] Removed {files_removed} files")
    print(f"[CLEANUP] Removed {dirs_removed} directories")
    print("[CLEANUP] System clean")
