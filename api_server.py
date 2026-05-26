"""
api_server.py
-------------
FastAPI application that:
  • Exposes a JSON REST API consumed by the frontend
  • Manages the background poller thread
  • Serves the single-page HTML dashboard from ./frontend/
"""

import os
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database as db

# ---------------------------------------------------------------------------
# Global poller state
# ---------------------------------------------------------------------------

DB_PATH: str      = os.getenv("DB_PATH", "teslalogger.db")
POLL_INTERVAL     = int(os.getenv("POLL_INTERVAL", "30"))
FRONTEND_DIR      = Path(__file__).parent / "frontend"
ENV_FILE          = Path(__file__).parent / ".env"
IS_SERVERLESS     = bool(os.getenv("TESLALOGGER_SERVERLESS"))

_poller_thread: Optional[threading.Thread] = None
_configured_email: str = ""


def start_poller(email: str, db_path: str, interval: int) -> None:
    """Start the background poller thread (idempotent — won't start twice)."""
    global _poller_thread, _configured_email

    if _poller_thread and _poller_thread.is_alive():
        return   # already running

    _configured_email = email

    def _run():
        from poller import run_poller
        run_poller(email, db_path, interval)

    _poller_thread = threading.Thread(target=_run, daemon=True, name="tesla-poller")
    _poller_thread.start()


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db(DB_PATH)
    yield

app = FastAPI(title="TeslaLogger API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Setup / configuration endpoint
# ---------------------------------------------------------------------------

class ConfigureRequest(BaseModel):
    email: str


@app.get("/api/version")
def get_version():
    """Version check — confirms this build of api_server.py is running."""
    return {"version": "2.0", "configure_endpoint": "POST /api/configure"}


@app.get("/api/configured")
def get_configured() -> dict:
    """Returns whether an email has been configured (poller may still be starting up)."""
    return {
        "configured": bool(_configured_email),
        "email": _configured_email,
        "poller_alive": _poller_thread is not None and _poller_thread.is_alive(),
    }


@app.post("/api/configure")
async def configure(request: Request) -> JSONResponse:
    """
    Save the Tesla account email, persist it to .env, and start the poller.
    The poller will open a browser window for Tesla OAuth on first run.
    """
    # Accept JSON body robustly — parse manually so Pydantic never causes a 422
    try:
        data = await request.json()
        email = str(data.get("email", "")).strip()
    except Exception:
        return JSONResponse(status_code=422, content={"detail": "Invalid JSON body."})

    if not email or not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return JSONResponse(status_code=422, content={"detail": "Please enter a valid email address."})

    if IS_SERVERLESS:
        return JSONResponse(
            status_code=400,
            content={
                "detail": (
                    "This hosted dashboard is online, but Tesla login and background polling "
                    "must run from your local TeslaLogger server."
                )
            },
        )

    # Persist to .env so it survives restarts
    _write_env("TESLA_EMAIL", email)

    # Start the poller
    start_poller(email, DB_PATH, POLL_INTERVAL)

    return JSONResponse(content={"status": "ok", "email": email})


def _write_env(key: str, value: str) -> None:
    """Write or update a key=value line in the .env file."""
    lines: list[str] = []
    found = False

    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)

    if not found:
        lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Data API routes
# ---------------------------------------------------------------------------

@app.get("/api/status")
def get_status() -> dict[str, Any]:
    snap = db.get_latest_snapshot(DB_PATH)
    return snap or {}


@app.get("/api/drives")
def get_drives(limit: int = 100) -> list[dict[str, Any]]:
    return db.get_drives(DB_PATH, limit)


@app.get("/api/charges")
def get_charges(limit: int = 100) -> list[dict[str, Any]]:
    return db.get_charges(DB_PATH, limit)


@app.get("/api/snapshots")
def get_snapshots(limit: int = 720) -> list[dict[str, Any]]:
    return db.get_recent_snapshots(DB_PATH, limit)


@app.get("/api/stats")
def get_stats(vin: Optional[str] = None) -> dict:
    return db.get_stats(DB_PATH, vin)


@app.get("/api/drives/{drive_id}/waypoints")
def get_waypoints(drive_id: int) -> list[dict[str, Any]]:
    return db.get_waypoints(DB_PATH, drive_id)


@app.get("/api/drives/{drive_id}")
def get_drive(drive_id: int) -> dict[str, Any]:
    d = db.get_drive(DB_PATH, drive_id)
    if not d:
        raise HTTPException(status_code=404, detail="Drive not found")
    return d


@app.get("/api/states")
def get_states(vin: Optional[str] = None, limit: int = 200) -> list:
    return db.get_states(DB_PATH, limit, vin)


@app.get("/api/software-updates")
def get_software_updates(vin: Optional[str] = None) -> list:
    return db.get_software_updates(DB_PATH, vin)


@app.get("/api/vampire-drain")
def get_vampire_drain(limit: int = 100) -> list[dict[str, Any]]:
    return db.get_vampire_drain_events(DB_PATH, limit)


@app.get("/api/battery-health")
def get_battery_health(vin: Optional[str] = None) -> list:
    return db.get_battery_health(DB_PATH, vin)


@app.get("/api/lifetime-map")
def get_lifetime_map(limit: int = 20000) -> list[dict[str, Any]]:
    return db.get_all_waypoints(DB_PATH, limit)


# ---------------------------------------------------------------------------
# Serve the frontend SPA
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def serve_index() -> FileResponse:
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(index)
