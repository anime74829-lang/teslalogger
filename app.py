"""
Vercel entrypoint.

Vercel's Python runtime looks for a top-level ASGI app named `app`.
The local entrypoint is still `main.py`.
"""

from __future__ import annotations

import os

if os.getenv("VERCEL"):
    os.environ.setdefault("DB_PATH", "/tmp/teslalogger.db")
    os.environ.setdefault("TESLALOGGER_SERVERLESS", "1")

from api_server import app
