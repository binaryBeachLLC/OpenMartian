"""Service helpers: model discovery and the processing run."""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

import requests

from openmartian.llm import build_llm
from openmartian.recipe import SYSTEM_PROMPT, build_prompt
from openmartian.schema import Decision, load_episode

from . import state

# Runtime params each backend actually supports — drives the Process panel's
# param controls so you only set knobs the selected model can honor.
PARAM_SPECS = {
    "ollama": [
        {"key": "temperature", "type": "float", "min": 0.0, "max": 2.0, "step": 0.1, "default": 0.0},
        {"key": "top_p", "type": "float", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.9},
        {"key": "top_k", "type": "int", "min": 0, "max": 100, "step": 1, "default": 40},
        {"key": "num_predict", "type": "int", "min": 1, "max": 8192, "step": 1, "default": 1024,
         "label": "num_predict (max tokens)"},
        {"key": "seed", "type": "int", "min": 0, "max": 2147483647, "step": 1, "default": 0, "optional": True},
    ],
    "claude": [
        {"key": "temperature", "type": "float", "min": 0.0, "max": 1.0, "step": 0.1, "default": 0.0},
        {"key": "max_tokens", "type": "int", "min": 1, "max": 8192, "step": 1, "default": 1024},
        {"key": "top_p", "type": "float", "min": 0.0, "max": 1.0, "step": 0.05, "default": 1.0, "optional": True},
        {"key": "top_k", "type": "int", "min": 0, "max": 500, "step": 1, "default": 0, "optional": True},
    ],
}


def param_specs() -> dict:
    """Per-backend runtime-param capabilities for the UI to render."""
    return PARAM_SPECS


# Substrings that indicate an Ollama model can see images. Anything else is
# flagged text-only (it will silently ignore frames).
_VISION_HINTS = ("llava", "vision", "gemma3", "minicpm-v", "moondream", "bakllava", "llama3.2-vision")

# Cloud models we know about (all vision-capable). Availability depends on the key.
_CLAUDE_MODELS = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-8",
]


def _ollama_models(host: str) -> list[dict]:
    try:
        resp = requests.get(f"{host}/api/tags", timeout=3)
        resp.raise_for_status()
        tags = resp.json().get("models", [])
    except Exception as e:
        return [{"backend": "ollama", "model": "(ollama unreachable)", "vision": False,
                 "available": False, "note": f"{e}"}]
    out = []
    for m in tags:
        name = m.get("name", "")
        low = name.lower()
        vision = any(h in low for h in _VISION_HINTS)
        size = m.get("size")
        size_str = f"{size / 1e9:.1f} GB" if isinstance(size, (int, float)) else None
        note = "vision-capable" if vision else "text-only — will ignore frames"
        out.append({"backend": "ollama", "model": name, "vision": vision,
                    "available": True, "size": size_str, "note": note})
    return out


def list_models() -> list[dict]:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    models = _ollama_models(host)
    key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
    for name in _CLAUDE_MODELS:
        models.append({
            "backend": "claude", "model": name, "vision": True,
            "available": key_set,
            "note": "ready" if key_set else "set ANTHROPIC_API_KEY to enable",
        })
    return models


def _process_one(llm, run_id: str, ep_id: str, backend: str, recipe_text: str, schema: dict) -> dict:
    """Process a single episode → a normalized result record (with token usage)."""
    from openmartian.recipe import Recipe

    result = {
        "id": state.new_id("res-"),
        "run_id": run_id,
        "episode_id": ep_id,
        "backend": backend,
        "model": llm.model,
        "recipe_text": recipe_text,
        "raw_output": None,
        "decision": None,
        "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "latency_ms": 0.0,
        "error": None,
        "created_at": state.now_iso(),
    }
    try:
        ep_dir = state.EPISODES_DIR / ep_id
        if not (ep_dir / "meta.json").exists():
            raise RuntimeError(f"episode {ep_id} not found")
        episode = load_episode(ep_dir)
        content = build_prompt(episode, Recipe(text=recipe_text))
        t0 = time.monotonic()
        raw = llm.generate(content, schema=schema, schema_name="decision")
        result["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
        result["raw_output"] = raw
        result["tokens"] = dict(llm.last_usage) or result["tokens"]
        dec = Decision.from_dict(raw)
        result["decision"] = {
            "match": dec.match, "confidence": dec.confidence,
            "action": dec.action, "reason": dec.reason, "message": dec.message,
            "evidence": dec.evidence,
        }
    except Exception as e:  # noqa: BLE001 - surface any backend error to the UI
        result["error"] = str(e)
    return result


def _error_result(run_id: str, ep_id: str, backend: str, model: Optional[str],
                  recipe_text: str, error: str) -> dict:
    """A durable result record for a JOB-level failure (e.g. build_llm), so the
    outcome still lands in Runs instead of vanishing with the in-memory job."""
    return {
        "id": state.new_id("res-"), "run_id": run_id, "episode_id": ep_id,
        "backend": backend, "model": model or backend, "recipe_text": recipe_text,
        "raw_output": None, "decision": None,
        "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "latency_ms": 0.0, "error": error,
        "notify": {"sent": False, "skipped_reason": "job error"},
        "created_at": state.now_iso(),
    }


def _job_worker(job_id: str, run_id: str, episode_ids: list[str], backend: str,
                recipe_text: str, model: Optional[str], schema: dict, params: dict) -> None:
    """Run a job to completion in a background thread, updating live state.

    EVERY episode produces a persisted Runs record — match, no-match, low
    confidence, or error — and carries a ``notify`` outcome explaining whether a
    Discord ping fired and, if not, why. A job-level error (bad model/params) is
    also persisted per episode so nothing silently disappears.
    """
    try:
        llm = build_llm(backend, model=model, system=SYSTEM_PROMPT, params=params)
    except Exception as e:  # noqa: BLE001 - bad backend/model, fail the whole job
        err = f"backend/model init failed: {e}"
        for ep_id in episode_ids:
            r = _error_result(run_id, ep_id, backend, model, recipe_text, err)
            state.append_runs([r])
            state.job_add_result(job_id, r)
        state.job_finish(job_id, error=str(e))
        return

    for ep_id in episode_ids:
        state.job_set_current(job_id, ep_id)
        result = _process_one(llm, run_id, ep_id, backend, recipe_text, schema)
        result["notify"] = _notify(result)   # post to Discord + record the outcome
        state.append_runs([result])          # persist as each finishes
        state.job_add_result(job_id, result)
    state.job_finish(job_id)


def _notify(result: dict) -> dict:
    """On a 'notify' verdict (above the confidence gate), post the output to
    Discord. Returns a status — {"sent": bool, "skipped_reason": str|None} — that
    is attached to the result so the UI always shows what happened to it.
    """
    decision = result.get("decision") or {}
    if result.get("error"):
        return {"sent": False, "skipped_reason": "result error"}
    if decision.get("action") != "notify":
        return {"sent": False, "skipped_reason": "no match (action != notify)"}
    min_conf = float(state.get_config().get("min_confidence", 0.6) or 0.0)
    conf = float(decision.get("confidence", 0.0) or 0.0)
    if conf < min_conf:
        return {"sent": False, "skipped_reason": f"confidence {conf:.2f} < min_confidence {min_conf:.2f}"}
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return {"sent": False, "skipped_reason": "no DISCORD_WEBHOOK_URL configured"}

    import json as _json

    from openmartian.dispatch import DiscordSink

    ev = decision.get("evidence") or []
    ev_section = ""
    if ev:
        lines = "".join(
            f"\n  • frame {e.get('frame')}: {e.get('observation', '')}" for e in ev
        )
        ev_section = "\nevidence:" + lines
    blob = _json.dumps(decision, indent=2)[:1400]
    msg = (
        f"🔴 **OpenMartian match** — episode `{result.get('episode_id')}`\n"
        f"model `{result.get('model')}` · confidence {decision.get('confidence')}\n"
        f"recipe: {result.get('recipe_text')}\n"
        f"reason: {decision.get('reason', '')}{ev_section}\n"
        f"```json\n{blob}\n```"
    )
    try:
        DiscordSink(url).send(msg[:1990])  # Discord hard-caps content at 2000
        return {"sent": True, "skipped_reason": None}
    except Exception as e:  # noqa: BLE001 - notification is best-effort
        return {"sent": False, "skipped_reason": f"discord post failed: {e}"}


def start_process_job(
    episode_ids: list[str],
    backend: str,
    recipe_text: str,
    *,
    model: Optional[str] = None,
    schema: Optional[dict] = None,
    params: Optional[dict] = None,
    wait: bool = False,
) -> dict:
    """Create a processing job and run it in a background thread.

    ``params`` is the runtime-param dict (temperature, top_p, top_k,
    num_predict/max_tokens, seed) — only the keys the chosen backend supports are
    honored (see PARAM_SPECS). Returns the job snapshot immediately
    (status="running") so the UI can poll ``GET /api/jobs/{job_id}``; if ``wait``
    is true, blocks until done and returns the final snapshot.
    """
    cfg = state.get_config()
    schema = schema or cfg["decision_schema"]
    params = params or {}
    run_id = state.new_id("run-")
    job_id = state.new_id("job-")
    resolved_model = model or {"claude": "claude-haiku-4-5-20251001", "ollama": "gemma3:4b"}.get(backend, model)

    state.create_job(job_id, run_id, total=len(episode_ids),
                     meta={"backend": backend, "model": resolved_model, "recipe_text": recipe_text})

    thread = threading.Thread(
        target=_job_worker,
        args=(job_id, run_id, episode_ids, backend, recipe_text, model, schema, params),
        daemon=True,
    )
    thread.start()
    if wait:
        thread.join()
    return state.get_job(job_id)
