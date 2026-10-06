"""Compatibility entry point: uvicorn main:app --host 127.0.0.1."""

from meeting_notes.api import app

__all__ = ["app"]
