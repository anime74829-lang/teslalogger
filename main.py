"""
main.py
-------
Entry point for TeslaLogger.

  • Serves the FastAPI app (REST API + HTML dashboard) via uvicorn
  • If TESLA_EMAIL is already set in .env, starts the poller automatically
  • Otherwise the web UI shows a setup screen to enter the email

Usage
─────
    python3 main.py

Then open  http://localhost:8000  in your browser.
"""

from __future__ import annotations

import logging
import os
import threading

import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)-8s]  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("teslalogger")

DB_PATH       = os.getenv("DB_PATH", "teslalogger.db")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))
HOST          = os.getenv("HOST", "0.0.0.0")
PORT          = int(os.getenv("PORT", "8000"))

import database as db
db.init_db(DB_PATH)

# If email already configured, kick off the poller before the web server starts
TESLA_EMAIL = os.getenv("TESLA_EMAIL", "").strip()
if TESLA_EMAIL:
    from api_server import start_poller
    start_poller(TESLA_EMAIL, DB_PATH, POLL_INTERVAL)

logger.info("Dashboard → http://localhost:%d", PORT)
uvicorn.run(
    "api_server:app",
    host=HOST,
    port=PORT,
    log_level="warning",
    reload=False,
)
