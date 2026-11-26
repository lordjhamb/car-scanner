"""FastAPI application factory and entry point.

    python -m carscan            # or:  carscan  (after pip install -e .)
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db
from .adapter import AdapterManager, Busy, NotOwner
from .ai.assistant import default_provider_factory
from .ai.base import AIError
from .api import auth, garage, workflows
from .config import Settings, load_settings
from .obd import AdapterError
from .obd.dtc_db import load_custom
from .security import RateLimiter, SecretBox

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger("carscan")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def create_app(settings: Settings | None = None, adapter: AdapterManager | None = None,
               provider_factory=None) -> FastAPI:
    settings = settings or load_settings()
    db.migrate(settings.db_path)
    load_custom(str(settings.data_dir / "custom_dtcs.json"))

    app = FastAPI(title="Car Scanner", version=__version__,
                  docs_url=None if settings.is_production else "/api/docs", redoc_url=None, openapi_url=
                  None if settings.is_production else "/api/openapi.json")
    app.state.settings = settings
    app.state.adapter = adapter or AdapterManager()
    app.state.box = SecretBox(settings.secret_key)
    app.state.provider_factory = provider_factory or default_provider_factory
    app.state.login_limiter = RateLimiter(settings.login_attempts_per_10min, 600)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=()")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    def err(status: int, msg: str):
        return JSONResponse({"detail": msg}, status_code=status)

    app.add_exception_handler(AdapterError, lambda r, e: err(400, str(e)))
    app.add_exception_handler(Busy, lambda r, e: err(409, str(e)))
    app.add_exception_handler(NotOwner, lambda r, e: err(409, str(e)))
    app.add_exception_handler(AIError, lambda r, e: err(400, str(e)))
    app.add_exception_handler(PermissionError, lambda r, e: err(403, str(e)))

    @app.exception_handler(ValueError)
    async def value_error(request: Request, e: ValueError):
        return err(422, str(e))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, e: Exception):  # pragma: no cover - last resort
        log.exception("Unhandled error on %s", request.url.path)
        return err(500, "Something went wrong on the server.")

    app.include_router(auth.router)
    app.include_router(garage.router)
    app.include_router(workflows.router)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return err(404, "Not found.")
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def main() -> None:  # pragma: no cover - thin CLI wrapper
    import argparse
    import webbrowser

    import uvicorn

    p = argparse.ArgumentParser(description="Car Scanner server")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args()
    settings = load_settings()
    host, port = a.host or settings.host, a.port or settings.port
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not a.no_browser and host in ("127.0.0.1", "localhost"):
        import threading
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    uvicorn.run(create_app(settings), host=host, port=port, proxy_headers=True, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
