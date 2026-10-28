from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import __version__
from app.database import get_db
from app.models import Monitor
from app.routers.params import MonitorId

APP_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=APP_DIR / "templates")
router = APIRouter(include_in_schema=False)


def asset(path: str) -> str:
    """Static file URL with a modification-time cache buster."""
    file = APP_DIR / "static" / path
    version = int(file.stat().st_mtime) if file.exists() else __version__
    return f"/static/{path}?v={version}"


templates.env.globals["asset"] = asset


def _context(request: Request, **extra: object) -> dict:
    settings = request.app.state.settings
    return {
        "version": __version__,
        "allow_localhost": settings.allow_localhost,
        "incident_threshold": settings.incident_failure_threshold,
        **extra,
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html", _context(request))


@router.get("/monitors/{monitor_id}", response_class=HTMLResponse)
def monitor_detail(monitor_id: MonitorId, request: Request, db: Session = Depends(get_db)):
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        return templates.TemplateResponse(
            request, "not_found.html", _context(request, monitor_id=monitor_id), status_code=404
        )
    return templates.TemplateResponse(
        request, "monitor_detail.html", _context(request, monitor=monitor)
    )
