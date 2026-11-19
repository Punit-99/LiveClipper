"""Run the LiveClipper FastAPI API server with Uvicorn.

Usage:
    python server.py
    python server.py --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import argparse
import sys
import uvicorn


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the LiveClipper Dashboard backend API server.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable hot reloading for development")
    args = parser.parse_args()

    print(f"\n🚀 Starting LiveClipper Dashboard API on http://{args.host}:{args.port}")
    print(f"📡 WebSocket live feed: ws://{args.host}:{args.port}/ws/live\n")

    uvicorn.run(
        "app.api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
