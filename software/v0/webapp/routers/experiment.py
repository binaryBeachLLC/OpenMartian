"""Experiment-graph API: datasets, video upload, labels, derivations, runs, matrix.

The REST surface over ``openmartian.experiment`` — CRUD for the
Dataset -> Derivation -> Experiment -> Run graph plus the bespoke results matrix.
Mounted at ``/api/experiment``. Like the rest of the dev console it's wide-open on
a trusted network; an agent can drive the whole platform over these endpoints.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from openmartian.experiment import IMPLEMENTED_ARMS, KNOWN_ARMS
from openmartian.experiment.video import probe_duration_ms

from .. import experiment_service as svc

router = APIRouter(prefix="/api/experiment", tags=["experiment"])


# -- arms / meta ------------------------------------------------------------ #


@router.get("/arms")
def get_arms() -> dict:
    return {"known": KNOWN_ARMS, "implemented": sorted(IMPLEMENTED_ARMS)}


# -- datasets --------------------------------------------------------------- #


@router.get("/datasets")
def list_datasets() -> list:
    return [d.to_dict() for d in svc.STORE.list_datasets()]


@router.post("/datasets")
def create_dataset(req: dict = Body(...)) -> dict:
    name = (req.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    return svc.create_dataset(name, req.get("video_paths"), req.get("notes", ""), req.get("meta"))


@router.get("/datasets/{ds_id}")
def get_dataset(ds_id: str) -> dict:
    ds = svc.STORE.get_dataset(ds_id)
    if ds is None:
        raise HTTPException(404, "dataset not found")
    return ds.to_dict()


@router.delete("/datasets/{ds_id}")
def delete_dataset(ds_id: str) -> dict:
    return {"deleted": svc.STORE.delete_dataset(ds_id)}


@router.post("/datasets/{ds_id}/videos")
async def upload_video(ds_id: str, file: UploadFile = File(...)) -> dict:
    """Upload a raw video file and register it on the dataset (the new input)."""
    ds = svc.STORE.get_dataset(ds_id)
    if ds is None:
        raise HTTPException(404, "dataset not found")
    safe = Path(file.filename or "upload.mp4").name
    dest = svc.STORE.videos_dir / safe
    data = await file.read()
    dest.write_bytes(data)
    out = svc.add_video_to_dataset(ds_id, safe)
    out["uploaded"] = {"filename": safe, "bytes": len(data),
                       "duration_s": round(probe_duration_ms(str(dest)) / 1000.0, 2)}
    return out


# -- labels ----------------------------------------------------------------- #


@router.post("/datasets/{ds_id}/labels")
def add_label(ds_id: str, req: dict = Body(...)) -> dict:
    try:
        return svc.add_label(
            ds_id,
            video=req["video"],
            t_start_ms=int(req["t_start_ms"]),
            t_end_ms=int(req.get("t_end_ms", req["t_start_ms"])),
            value=req.get("value", True),
            shape=req.get("shape", "presence"),
            label=req.get("label", ""),
            meta=req.get("meta") or {},
        )
    except KeyError as e:
        raise HTTPException(400, f"missing field: {e}")


@router.delete("/datasets/{ds_id}/labels/{label_id}")
def delete_label(ds_id: str, label_id: str) -> dict:
    try:
        return svc.delete_label(ds_id, label_id)
    except KeyError:
        raise HTTPException(404, "dataset not found")


# -- derivations ------------------------------------------------------------ #


@router.post("/datasets/{ds_id}/derive")
def derive(ds_id: str, req: dict = Body(default={})) -> dict:
    try:
        return svc.start_derive(ds_id, req.get("spec") or {}, req.get("max_episodes"))
    except KeyError:
        raise HTTPException(404, "dataset not found")


@router.get("/derivations")
def list_derivations(dataset_id: Optional[str] = Query(None)) -> list:
    return [d.to_dict() for d in svc.STORE.list_derivations(dataset_id=dataset_id)]


@router.get("/derivations/{der_id}")
def get_derivation(der_id: str) -> dict:
    der = svc.STORE.get_derivation(der_id)
    if der is None:
        raise HTTPException(404, "derivation not found")
    return der.to_dict()


@router.delete("/derivations/{der_id}")
def delete_derivation(der_id: str) -> dict:
    return {"deleted": svc.STORE.delete_derivation(der_id)}


@router.get("/derivations/{der_id}/episodes")
def list_episodes(der_id: str) -> list:
    der = svc.STORE.get_derivation(der_id)
    if der is None:
        raise HTTPException(404, "derivation not found")
    out = []
    for eid in der.episode_ids:
        meta_path = Path(der.episode_dir) / eid / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        frames = meta.get("frames", [])
        # Translate the on-disk source-time keys into the stable API window field.
        start = int(meta.get("source_t_start_ms", 0) or 0)
        wlen = int(meta.get("source_window_ms", 0) or 0)
        out.append({
            "episode_id": eid,
            "src_video": meta.get("source_video"),
            "src_window_ms": [start, start + wlen],
            "frame_count": len(frames),
            "frames": [{"index": i, "url": f"/api/experiment/derivations/{der_id}/episodes/{eid}/frames/{i}"}
                       for i in range(len(frames))],
        })
    return out


@router.get("/derivations/{der_id}/episodes/{ep_id}/frames/{index}")
def get_frame(der_id: str, ep_id: str, index: int) -> FileResponse:
    der = svc.STORE.get_derivation(der_id)
    if der is None:
        raise HTTPException(404, "derivation not found")
    meta_path = Path(der.episode_dir) / ep_id / "meta.json"
    if not meta_path.exists():
        raise HTTPException(404, "episode not found")
    frames = json.loads(meta_path.read_text()).get("frames", [])
    if index < 0 or index >= len(frames):
        raise HTTPException(404, "frame index out of range")
    path = Path(der.episode_dir) / ep_id / frames[index]["file"]
    if not path.exists():
        raise HTTPException(404, "frame file missing")
    return FileResponse(path, media_type="image/jpeg")


# -- experiments ------------------------------------------------------------ #


@router.get("/experiments")
def list_experiments(derivation_id: Optional[str] = Query(None)) -> list:
    return [e.to_dict() for e in svc.STORE.list_experiments(derivation_id=derivation_id)]


@router.post("/experiments")
def create_experiment(req: dict = Body(...)) -> dict:
    if not req.get("derivation_id"):
        raise HTTPException(400, "derivation_id is required")
    return svc.create_experiment(req)


@router.get("/experiments/{exp_id}")
def get_experiment(exp_id: str) -> dict:
    exp = svc.STORE.get_experiment(exp_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    return exp.to_dict()


@router.delete("/experiments/{exp_id}")
def delete_experiment(exp_id: str) -> dict:
    return {"deleted": svc.STORE.delete_experiment(exp_id)}


@router.post("/experiments/{exp_id}/run")
def run_experiment(exp_id: str, req: dict = Body(default={})) -> dict:
    try:
        return svc.start_run(exp_id, no_cost=bool(req.get("no_cost", False)))
    except KeyError:
        raise HTTPException(404, "experiment not found")


# -- runs + matrix ---------------------------------------------------------- #


@router.get("/runs")
def list_runs(experiment_id: Optional[str] = Query(None)) -> list:
    return [r.to_dict() for r in svc.STORE.list_runs(experiment_id=experiment_id)]


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = svc.STORE.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run.to_dict()


@router.delete("/runs/{run_id}")
def delete_run(run_id: str) -> dict:
    return {"deleted": svc.STORE.delete_run(run_id)}


@router.get("/matrix")
def matrix(experiment_ids: Optional[str] = Query(None)) -> dict:
    ids = experiment_ids.split(",") if experiment_ids else None
    return svc.ORCH.matrix(experiment_ids=ids)


# -- jobs ------------------------------------------------------------------- #


@router.get("/jobs")
def list_jobs(limit: Optional[int] = Query(None)) -> list:
    return svc.list_jobs(limit=limit)


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    j = svc.get_job(job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return j
