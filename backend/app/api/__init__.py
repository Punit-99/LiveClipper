"""LiveClipper FastAPI Dashboard API package."""

__all__ = ["app", "create_app"]


def __getattr__(name: str):
    if name in ("app", "create_app"):
        from app.api.server import app, create_app
        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

