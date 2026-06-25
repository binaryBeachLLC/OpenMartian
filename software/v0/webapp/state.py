"""Server state + on-disk persistence for the dev console.

Keeps things deliberately simple (it's a single-process dev tool):
- JSON files under ``v0/data/`` for app config, recipes, and run history;
- episodes live under ``v0/episodes/`` (the same dir the CLI uses);
- volatile webcam/recording state lives in memory, guarded by a lock.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from openmartian.capture import default_rtsp_url
from openmartian.schema import EpisodeConfig, decision_schema

# --------------------------------------------------------------------------- #
# Paths                                                                       #
# --------------------------------------------------------------------------- #

V0_DIR = Path(__file__).resolve().parent.parent  # .../software/v0
DATA_DIR = V0_DIR / "data"
EPISODES_DIR = V0_DIR / "episodes"

CONFIG_FILE = DATA_DIR / "app_config.json"
RECIPES_FILE = DATA_DIR / "recipes.json"
SCHEMAS_FILE = DATA_DIR / "schemas.json"
RUNS_FILE = DATA_DIR / "runs.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))


# --------------------------------------------------------------------------- #
# App config (capture defaults + decision schema + min_confidence)            #
# --------------------------------------------------------------------------- #


def default_config() -> dict:
    cfg = EpisodeConfig()
    return {
        "capture": {
            "fps": cfg.fps,
            "frames_per_episode": cfg.frames_per_episode,
            "frame_res": cfg.frame_res,
            "jpeg_q": cfg.jpeg_q,
        },
        "min_confidence": 0.6,
        "decision_schema": decision_schema(["notify"]),
        "webcam_url": default_rtsp_url(),
    }


def get_config() -> dict:
    cfg = default_config()
    stored = _read_json(CONFIG_FILE, {})
    if isinstance(stored, dict):
        # shallow-merge stored over defaults; capture is merged one level deeper
        cap = {**cfg["capture"], **(stored.get("capture") or {})}
        cfg.update({k: v for k, v in stored.items() if k != "capture"})
        cfg["capture"] = cap
    # Migrate an older stored schema that predates frame-level evidence.
    schema = cfg.get("decision_schema") or {}
    if "evidence" not in (schema.get("properties") or {}):
        cfg["decision_schema"] = decision_schema(["notify"])
    return cfg


def update_config(patch: dict) -> dict:
    cfg = get_config()
    if "capture" in patch and isinstance(patch["capture"], dict):
        cfg["capture"] = {**cfg["capture"], **patch["capture"]}
    for k in ("min_confidence", "decision_schema", "webcam_url"):
        if k in patch and patch[k] is not None:
            cfg[k] = patch[k]
    _write_json(CONFIG_FILE, cfg)
    return cfg


def episode_config_from(cfg_dict: dict) -> EpisodeConfig:
    cap = cfg_dict.get("capture", cfg_dict)
    return EpisodeConfig(
        fps=float(cap.get("fps", 1.0)),
        frames_per_episode=int(cap.get("frames_per_episode", 5)),
        frame_res=str(cap.get("frame_res", "320x240")),
        jpeg_q=int(cap.get("jpeg_q", 5)),
    )


# --------------------------------------------------------------------------- #
# Recipes                                                                     #
# --------------------------------------------------------------------------- #


def get_recipes() -> list[dict]:
    return _read_json(RECIPES_FILE, [])


def _save_recipes(recipes: list[dict]) -> None:
    _write_json(RECIPES_FILE, recipes)


def add_recipe(name: str, text: str, allowed_actions: Optional[list[str]] = None) -> dict:
    recipe = {
        "id": new_id("r-"),
        "name": name,
        "text": text,
        "allowed_actions": allowed_actions or ["notify"],
        "created_at": now_iso(),
    }
    recipes = get_recipes()
    recipes.append(recipe)
    _save_recipes(recipes)
    return recipe


def get_recipe(recipe_id: str) -> Optional[dict]:
    return next((r for r in get_recipes() if r["id"] == recipe_id), None)


def update_recipe(recipe_id: str, patch: dict) -> Optional[dict]:
    recipes = get_recipes()
    for r in recipes:
        if r["id"] == recipe_id:
            for k in ("name", "text", "allowed_actions"):
                if k in patch and patch[k] is not None:
                    r[k] = patch[k]
            _save_recipes(recipes)
            return r
    return None


def delete_recipe(recipe_id: str) -> bool:
    recipes = get_recipes()
    kept = [r for r in recipes if r["id"] != recipe_id]
    if len(kept) == len(recipes):
        return False
    _save_recipes(kept)
    return True


def seed_recipes_if_empty() -> None:
    if not get_recipes():
        add_recipe("Man standing", "Is there a man standing in the frame?")


# --------------------------------------------------------------------------- #
# Output-schema store (named, selectable structured-output schemas)           #
# --------------------------------------------------------------------------- #
# This is the user-facing side of the LLM abstraction: you author a neutral
# JSON Schema once, store it by name, pick it at process time, and each backend
# (Claude forced-tool-call / Ollama format) translates it to its own mechanism.


def get_schemas() -> list[dict]:
    return _read_json(SCHEMAS_FILE, [])


def _save_schemas(schemas: list[dict]) -> None:
    _write_json(SCHEMAS_FILE, schemas)


def add_schema(name: str, schema: dict) -> dict:
    entry = {
        "id": new_id("sch-"),
        "name": name,
        "schema": schema,
        "created_at": now_iso(),
    }
    schemas = get_schemas()
    schemas.append(entry)
    _save_schemas(schemas)
    return entry


def get_schema_entry(schema_id: str) -> Optional[dict]:
    return next((s for s in get_schemas() if s["id"] == schema_id), None)


def update_schema_entry(schema_id: str, patch: dict) -> Optional[dict]:
    schemas = get_schemas()
    for s in schemas:
        if s["id"] == schema_id:
            for k in ("name", "schema"):
                if k in patch and patch[k] is not None:
                    s[k] = patch[k]
            _save_schemas(schemas)
            return s
    return None


def delete_schema_entry(schema_id: str) -> bool:
    schemas = get_schemas()
    kept = [s for s in schemas if s["id"] != schema_id]
    if len(kept) == len(schemas):
        return False
    _save_schemas(kept)
    return True


def seed_schemas_if_empty() -> None:
    if not get_schemas():
        add_schema("Decision + evidence (default)", decision_schema(["notify"]))


# --------------------------------------------------------------------------- #
# Runs (processing history)                                                   #
# --------------------------------------------------------------------------- #


def get_runs(limit: Optional[int] = None, episode_id: Optional[str] = None) -> list[dict]:
    runs = _read_json(RUNS_FILE, [])
    runs = sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True)
    if episode_id:
        runs = [r for r in runs if r.get("episode_id") == episode_id]
    if limit:
        runs = runs[:limit]
    return runs


def append_runs(results: list[dict]) -> None:
    runs = _read_json(RUNS_FILE, [])
    runs.extend(results)
    _write_json(RUNS_FILE, runs)


def clear_runs() -> int:
    runs = _read_json(RUNS_FILE, [])
    _write_json(RUNS_FILE, [])
    return len(runs)


# --------------------------------------------------------------------------- #
# Volatile webcam / recording state (in-memory, single process)               #
# --------------------------------------------------------------------------- #


class WebcamState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.desired_connected: bool = False
        self.reachable: Optional[bool] = None
        self.recording: bool = False
        self.checked_at: Optional[str] = None
        self.detail: str = ""

    @property
    def url(self) -> str:
        return get_config().get("webcam_url", default_rtsp_url())

    def set_url(self, url: str) -> None:
        update_config({"webcam_url": url})

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "url": self.url,
                "desired_connected": self.desired_connected,
                "reachable": self.reachable,
                "recording": self.recording,
                "checked_at": self.checked_at,
                "detail": self.detail,
            }

    def set_probe(self, reachable: bool, detail: str) -> None:
        with self._lock:
            self.reachable = reachable
            self.detail = detail
            self.checked_at = now_iso()

    def set_recording(self, value: bool) -> None:
        with self._lock:
            self.recording = value


WEBCAM = WebcamState()


# --------------------------------------------------------------------------- #
# Processing jobs (in-memory registry for live progress + token tallies)      #
# --------------------------------------------------------------------------- #

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def create_job(job_id: str, run_id: str, total: int, meta: dict) -> dict:
    job = {
        "job_id": job_id,
        "run_id": run_id,
        "status": "running",  # running | done | error
        "total": total,
        "completed": 0,
        "current_episode": None,
        "results": [],
        "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "error": None,
        "started_at": now_iso(),
        "finished_at": None,
        **meta,  # backend, model, recipe_text
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    return dict(job)


def job_set_current(job_id: str, episode_id: Optional[str]) -> None:
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id]["current_episode"] = episode_id


def job_add_result(job_id: str, result: dict) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["results"].append(result)
        job["completed"] = len(job["results"])
        usage = (result.get("tokens") or {})
        for k in ("input_tokens", "output_tokens", "total_tokens"):
            job["tokens"][k] += int(usage.get(k, 0) or 0)


def job_finish(job_id: str, error: Optional[str] = None) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["status"] = "error" if error else "done"
        job["error"] = error
        job["current_episode"] = None
        job["finished_at"] = now_iso()


def get_job(job_id: str) -> Optional[dict]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_jobs(limit: Optional[int] = None) -> list[dict]:
    with _JOBS_LOCK:
        jobs = sorted(_JOBS.values(), key=lambda j: j["started_at"], reverse=True)
        jobs = [dict(j) for j in jobs]
    if limit:
        jobs = jobs[:limit]
    return jobs


def clear_finished_jobs() -> int:
    with _JOBS_LOCK:
        done = [jid for jid, j in _JOBS.items() if j["status"] != "running"]
        for jid in done:
            del _JOBS[jid]
        return len(done)
