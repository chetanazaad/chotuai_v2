"""Entry point for the Chotu AI web server."""
import sys
import webbrowser
from pathlib import Path


def main():
    try:
        import uvicorn
    except ImportError:
        print("Installing dependencies...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "fastapi", "uvicorn[standard]"])
        import uvicorn

    frontend_path = Path(__file__).parent / "frontend"
    if not frontend_path.exists():
        frontend_path = Path(__file__).parent.parent / "frontend"

    port = 8000
    url = f"http://localhost:{port}"

    print(f"Starting Chotu AI on {url}...")
    print(f"Frontend: {frontend_path}")

    webbrowser.open(url)

    uvicorn.run(
        "chotu_ai.api_server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()