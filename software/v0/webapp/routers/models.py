"""Available models (Ollama local + Claude), with vision/availability flags."""

from __future__ import annotations

from fastapi import APIRouter

from ..services import list_models, param_specs

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models")
def get_models() -> list[dict]:
    return list_models()


@router.get("/param-specs")
def get_param_specs() -> dict:
    """Per-backend runtime params the UI should expose (capability-gated)."""
    return param_specs()
