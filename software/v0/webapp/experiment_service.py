"""Service glue for the experiment console: orchestrator + background jobs.

Wraps :class:`openmartian.experiment.Orchestrator` for the FastAPI router. Derive
and run can both be slow (ffmpeg sampling, real VLM inference), so each runs in a
background thread tracked in an in-memory job registry — the same live-progress
pattern the existing process panel uses, applied to the experiment graph (PART G:
``LocalRunner`` async-ness is a registry detail, not a pipeline change).
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from openmartian.experiment import Dataset, Experiment, ExperimentStore, Orchestrator, SamplingSpec
from openmartian.experiment.models import Label

from . import state

# One store + orchestrator for the process, rooted at the same data/ dir.
STORE = ExperimentStore(data_root=state.DATA_DIR)
ORCH = Orchestrator(store=STORE)

_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_job(kind: str, meta: dict) -> str:
    job_id = f"xjob-{uuid.uuid4().hex[:8]}"
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "job_id": job_id, "kind": kind, "status": "running",
            "completed": 0, "total": 0, "current": None,
            "result": None, "error": None, "started_at": _now(), "finished_at": None,
            **meta,
        }
    return job_id


def _update_job(job_id: str, **kw) -> None:
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kw)


def get_job(job_id: str) -> Optional[dict]:
    with _JOBS_LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else None


def list_jobs(limit: Optional[int] = None) -> list:
    with _JOBS_LOCK:
        jobs = sorted(_JOBS.values(), key=lambda j: j["started_at"], reverse=True)
        jobs = [dict(j) for j in jobs]
    return jobs[:limit] if limit else jobs


# --------------------------------------------------------------------------- #
# Datasets + labels                                                           #
# --------------------------------------------------------------------------- #


def create_dataset(name: str, video_paths: Optional[list] = None, notes: str = "", meta: Optional[dict] = None) -> dict:
    ds = Dataset.new(name=name, video_paths=video_paths or [], notes=notes, meta=meta or {})
    return STORE.save_dataset(ds).to_dict()


def add_video_to_dataset(ds_id: str, filename: str) -> dict:
    ds = STORE.get_dataset(ds_id)
    if ds is None:
        raise KeyError(ds_id)
    if filename not in ds.video_paths:
        ds.video_paths.append(filename)
    return STORE.save_dataset(ds).to_dict()


def add_label(ds_id: str, **kw) -> dict:
    ds = STORE.get_dataset(ds_id)
    if ds is None:
        raise KeyError(ds_id)
    ds.labels.append(Label.new(**kw))
    return STORE.save_dataset(ds).to_dict()


def delete_label(ds_id: str, label_id: str) -> dict:
    ds = STORE.get_dataset(ds_id)
    if ds is None:
        raise KeyError(ds_id)
    ds.labels = [l for l in ds.labels if (l.id if hasattr(l, "id") else l.get("id")) != label_id]
    return STORE.save_dataset(ds).to_dict()


# --------------------------------------------------------------------------- #
# Derivation (background)                                                      #
# --------------------------------------------------------------------------- #


def start_derive(ds_id: str, spec: dict, max_episodes: Optional[int] = None) -> dict:
    ds = STORE.get_dataset(ds_id)
    if ds is None:
        raise KeyError(ds_id)
    sampling = SamplingSpec.from_dict(spec)
    job_id = _new_job("derive", {"dataset_id": ds_id})

    def _work():
        try:
            der = ORCH.derive(ds, sampling, max_episodes=max_episodes)
            _update_job(job_id, status="done", result={"derivation_id": der.id,
                        "episode_count": len(der.episode_ids)}, finished_at=_now())
        except Exception as e:  # noqa: BLE001
            _update_job(job_id, status="failed", error=str(e), finished_at=_now())

    threading.Thread(target=_work, daemon=True).start()
    return get_job(job_id)


# --------------------------------------------------------------------------- #
# Experiment + run (background)                                                #
# --------------------------------------------------------------------------- #


def create_experiment(payload: dict) -> dict:
    from openmartian.experiment.models import ModelPin

    obj = payload.get("objective_model") or {"provider": "ollama", "model_id": "gemma3:4b"}
    judge = payload.get("judge_model")
    exp = Experiment.new(
        derivation_id=payload["derivation_id"],
        arm=payload.get("arm", "direct"),
        objective_model=ModelPin.from_dict(obj),
        judge_model=ModelPin.from_dict(judge) if judge else None,
        judge_cadence_s=float(payload.get("judge_cadence_s", 0.0) or 0.0),
        recipes=payload.get("recipes") or [],
        name=payload.get("name", ""),
        notes=payload.get("notes", ""),
        meta=payload.get("meta") or {},
    )
    return STORE.save_experiment(exp).to_dict()


def start_run(exp_id: str, no_cost: bool = False) -> dict:
    exp = STORE.get_experiment(exp_id)
    if exp is None:
        raise KeyError(exp_id)
    job_id = _new_job("run_experiment", {"experiment_id": exp_id, "no_cost": no_cost})

    def _progress(done, total, ep):
        _update_job(job_id, completed=done, total=total, current=ep)

    def _work():
        try:
            run = ORCH.run_experiment(exp, no_cost=no_cost, progress=_progress)
            _update_job(job_id, status="done", result={"run_id": run.id,
                        "n_results": len(run.results), "summary": run.summary}, finished_at=_now())
        except Exception as e:  # noqa: BLE001
            _update_job(job_id, status="failed", error=str(e), finished_at=_now())

    threading.Thread(target=_work, daemon=True).start()
    return get_job(job_id)
