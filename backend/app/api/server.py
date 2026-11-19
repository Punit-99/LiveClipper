"""Main FastAPI application factory and ASGI server."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.events import ws_manager
from app.api.routes import channels, clips, monitor, settings, shorts, status as status_route, upload, websocket
from app.api.state import supervisor

FRONTEND_DIST_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle manager."""
    # Capture the running event loop for thread-safe websocket broadcasts
    loop = asyncio.get_running_loop()
    ws_manager.set_loop(loop)

    yield

    # Shutdown: ensure active monitoring is stopped cleanly
    if supervisor.is_running:
        try:
            await supervisor.stop()
        except Exception:
            pass


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="LiveClipper Dashboard API",
        version="1.0.0",
        description="REST and WebSocket API for the LiveClipper automated stream monitoring and Shorts pipeline.",
        lifespan=lifespan,
    )

    # Configure permissive CORS for local React/Vite development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API routers
    app.include_router(status_route.router, prefix="/api")
    app.include_router(monitor.router, prefix="/api")
    app.include_router(channels.router, prefix="/api")
    app.include_router(clips.router, prefix="/api")
    app.include_router(shorts.router, prefix="/api")
    app.include_router(upload.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")
    app.include_router(websocket.router)

    @app.get("/api/health")
    def health_check() -> dict[str, str]:
        """Simple liveness probe."""
        return {"status": "ok"}

    # Serve built React frontend if frontend/dist exists
    if FRONTEND_DIST_DIR.is_dir():
        assets_dir = FRONTEND_DIST_DIR / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            """SPA catch-all route: serves index.html for client-side routing."""
            if full_path.startswith("api/") or full_path == "ws/live":
                return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Not found"})

            candidate = FRONTEND_DIST_DIR / full_path
            if candidate.is_file():
                return FileResponse(candidate)

            index_html = FRONTEND_DIST_DIR / "index.html"
            if index_html.is_file():
                return FileResponse(index_html)

            return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Frontend index.html not found"})

    return app


app = create_app()
