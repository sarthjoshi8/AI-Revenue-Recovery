"""
server.py — Simple local web server to host the Starbucks-Inspired Web UI.

Usage:
    python server.py
"""

import http.server
import socketserver
import webbrowser
from pathlib import Path

PORT = 8000
DIRECTORY = Path("output")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORY), **kwargs)


def main():
    if not (DIRECTORY / "report.html").exists():
        print("report.html not found! Running batch runner first...")
        from runner.batch_runner import main as run_batch
        run_batch()

    url = f"http://localhost:{PORT}/report.html"
    print(f"\n☕ Starbucks Revenue Recovery UI Web Server")
    print(f"👉 Serving live dashboard at: {url}")
    print("Press Ctrl+C to stop.\n")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
