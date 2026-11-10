"""Vercel serverless entry point.

Vercel looks for an ASGI callable named ``app`` in this module. The project code
lives one level up, so the root is put on the path first.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402  (import after sys.path is set)

__all__ = ["app"]
