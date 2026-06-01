"""Entry point for the PyInstaller binary.

Run as:
    axioma-sidecar [--host HOST] [--port PORT] [--reload]

Defaults match development usage: host=127.0.0.1, port=8000.
"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Axioma Sidecar API server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev only)")
    args = parser.parse_args()

    import uvicorn  # noqa: PLC0415 — intentional late import for frozen-binary compat

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    sys.exit(main())
