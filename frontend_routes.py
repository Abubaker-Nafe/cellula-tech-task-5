"""Serve the browser interface alongside the existing FastAPI endpoints."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"


def install_frontend(app: FastAPI) -> None:
    """Call once, after creating the FastAPI app in main.py."""
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")

    @app.get("/", include_in_schema=False)
    def frontend_home():
        return FileResponse(FRONTEND_DIR / "index.html")
