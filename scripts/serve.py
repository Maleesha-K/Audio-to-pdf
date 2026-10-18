"""Run the local app on the first available port in a small predictable range."""
import socket
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def available_port() -> int:
    for port in range(8766, 8786):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free localhost port found between 8766 and 8785")


if __name__ == "__main__":
    try:
        port = available_port()
    except RuntimeError as error:
        print(error, file=sys.stderr, flush=True)
        raise SystemExit(1)
    project_root = Path(__file__).resolve().parents[1]
    uvicorn.run(
        "backend.app:app",
        host="127.0.0.1",
        port=port,
        reload=True,
        reload_dirs=[str(project_root / "backend"), str(project_root / "frontend")],
        reload_excludes=["*.png", "*.m4a", "*.mp3", "*.pdf", "*.tmp", "data/*"],
    )

