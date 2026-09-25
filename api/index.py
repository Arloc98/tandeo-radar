"""Vercel serverless entry point.

Vercel's Python runtime serves any ASGI application exported as `app`, so this
re-exports the FastAPI instance. The repo root goes on sys.path because the
function's working directory is not the project root.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402,F401
