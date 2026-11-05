"""HTTP interface: status dashboard, JSON API, Prometheus metrics and liveness probe."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .monitor import Monitor
from .runner import ServiceResult

_templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _as_dict(result: ServiceResult) -> dict[str, Any]:
    return {
        "service": result.service,
        "base_url": result.base_url,
        "ok": result.ok,
        "total": result.total,
        "passed": result.passed,
        "failed": result.failed,
        "duration": round(result.duration, 3),
        "error": result.error,
        "failures": [{"name": f.name, "message": f.message} for f in result.failures],
        "checked_at": result.checked_at.isoformat(),
    }


def create_app(monitor: Monitor, run_in_background: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if run_in_background:
            monitor.start()
        yield
        if run_in_background:
            monitor.stop()

    app = FastAPI(title="Tavern Docker Wrapper", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        results = monitor.results
        pending = len(monitor.config.services) - len(results)
        return _templates.TemplateResponse(
            request,
            "index.html",
            {
                "results": results,
                "failing": sum(not r.ok for r in results),
                "pending": pending,
                "interval": monitor.config.interval,
            },
        )

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        results = monitor.results
        return {
            "ok": bool(results) and all(r.ok for r in results),
            "services": [_as_dict(r) for r in results],
        }

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(monitor.registry), media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        # Liveness of the wrapper itself, not of the monitored services
        return {"status": "ok"}

    return app
