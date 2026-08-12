"""uvicorn 진입점 — `uvicorn hyeseongkit.hub.main:app` (컨테이너 CMD, §12-1)."""

from .app import create_app

app = create_app()
