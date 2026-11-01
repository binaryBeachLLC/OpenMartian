"""FastAPI app factory for the OpenMartian v0 dev console.

Run (from software/v0/):
    uvicorn webapp.app:app --reload --port 8000     # or: make dev
UI:   http://localhost:8000/        API docs: http://localhost:8000/docs

The app is split into per-domain routers under ``webapp/routers/``; this module
just assembles them, serves the static frontend, and loads the local ``.env``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# Load v0/.env (Claude key, RTSP URL, OLLAMA_HOST) before anything reads env.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover
    pass

from . import state
from .routers import config, episodes, experiment, models, process, recipes, schemas, webcam

app = FastAPI(
    title="OpenMartian v0 dev console",
    version="0.0.0",
    description="Dev-only control surface for the webcam→episode→VLM pipeline.",
)

# Trusted dev tool on a private network — wide open by design.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

for _router in (webcam.router, config.router, episodes.router,
                recipes.router, schemas.router, models.router, process.router,
                experiment.router):
    app.include_router(_router)

# Serve static assets (vendored React/Babel, etc.) so the UI needs no public CDN.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def _startup() -> None:
    state.DATA_DIR.mkdir(parents=True, exist_ok=True)
    state.EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    state.seed_recipes_if_empty()
    state.seed_schemas_if_empty()


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        return HTMLResponse("<h1>OpenMartian v0 dev console</h1><p>Frontend not built yet.</p>")
    return HTMLResponse(idx.read_text())


@app.get("/experiment", include_in_schema=False)
def experiment_console() -> HTMLResponse:
    """The experiment platform UI: upload video -> label -> derive -> run -> matrix."""
    page = STATIC_DIR / "experiment.html"
    if not page.exists():
        return HTMLResponse("<h1>Experiment console</h1><p>Frontend not built yet.</p>")
    return HTMLResponse(page.read_text())
