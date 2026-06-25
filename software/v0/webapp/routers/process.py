"""Processing: start a job, poll live job progress/tokens, browse run history."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

from .. import state
from ..services import start_process_job

router = APIRouter(prefix="/api", tags=["process"])


@router.post("/process")
def process(req: dict = Body(...)) -> dict:
    """Start a processing job (runs in the background).

    Returns the job snapshot immediately so the UI can poll
    ``GET /api/jobs/{job_id}`` for a live progress bar + token tallies. Pass
    ``"wait": true`` to block until done and get the final snapshot in one call.
    """
    episode_ids = req.get("episode_ids") or []
    if not episode_ids:
        raise HTTPException(status_code=400, detail="episode_ids is required")

    recipe_text = req.get("recipe_text")
    if not recipe_text and req.get("recipe_id"):
        r = state.get_recipe(req["recipe_id"])
        if not r:
            raise HTTPException(status_code=404, detail="recipe_id not found")
        recipe_text = r["text"]
    if not recipe_text:
        raise HTTPException(status_code=400, detail="recipe_id or recipe_text is required")

    # Output schema: inline > stored schema_id > active config default.
    schema = req.get("schema")
    if not schema and req.get("schema_id"):
        s = state.get_schema_entry(req["schema_id"])
        if not s:
            raise HTTPException(status_code=404, detail="schema_id not found")
        schema = s["schema"]

    # Runtime params (temperature, top_p, top_k, num_predict/max_tokens, seed) —
    # only the keys the chosen backend supports are honored.
    params = req.get("params")
    if params is None and "temperature" in req:  # back-compat
        params = {"temperature": req.get("temperature")}

    return start_process_job(
        episode_ids,
        backend=req.get("backend", "ollama"),
        recipe_text=recipe_text,
        model=req.get("model"),
        schema=schema,
        params=params,
        wait=bool(req.get("wait", False)),
    )


@router.get("/jobs")
def get_jobs(limit: Optional[int] = Query(None)) -> list[dict]:
    return state.list_jobs(limit=limit)


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = state.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.delete("/jobs")
def clear_jobs() -> dict:
    return {"cleared": state.clear_finished_jobs()}


@router.get("/runs")
def get_runs(limit: Optional[int] = Query(None), episode_id: Optional[str] = Query(None)) -> list[dict]:
    return state.get_runs(limit=limit, episode_id=episode_id)


@router.get("/runs/{run_or_result_id}")
def get_run(run_or_result_id: str) -> dict:
    for r in state.get_runs():
        if r.get("id") == run_or_result_id or r.get("run_id") == run_or_result_id:
            return r
    raise HTTPException(status_code=404, detail="run not found")


@router.delete("/runs")
def delete_runs() -> dict:
    return {"cleared": state.clear_runs()}
