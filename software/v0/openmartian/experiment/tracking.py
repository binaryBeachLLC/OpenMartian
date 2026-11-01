"""Experiment tracking + cost model (PART D adopt-MLflow seam; PART F cost axis).

The bespoke deliverable (PART A) is the **results view** — the question-shape x arm
x spec matrix. This module owns two things:

1. **Cost** — a comparable "cost" axis for the matrix (PART open-question 5). API
   arms have a real $-per-call from token usage x model price; local arms are
   priced in wall-clock seconds (a time/energy proxy) so a cell can still compare
   fairly across the API/local divide.
2. **The tracker seam** — a ``Tracker`` ABC. ``JsonTracker`` (default) logs runs to
   our own store so the matrix is fully offline; ``MLflowTracker`` is the
   documented adapter for the adopted tool (writes runs/metrics/params via the
   MLflow API, reads them back). The orchestrator depends only on the ABC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

# Anthropic list prices, USD per 1M tokens (input, output). Batch API = 50% off
# (PART E.2). Editable here so the matrix's cost axis tracks real pricing.
CLAUDE_PRICING = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
}

# Local-model "cost": a wall-clock proxy in USD/hour for the inference machine
# (a tier knob — set to your GPU's amortized $/h). Lets local arms land on the
# same cost axis as API arms instead of showing $0.
LOCAL_COMPUTE_USD_PER_HOUR = 0.50


def claude_cost_usd(model_id: str, in_tok: int, out_tok: int, batch: bool = False) -> float:
    """$ for one Claude call from token usage. Batch API halves it (PART E.2)."""
    price_in, price_out = CLAUDE_PRICING.get(model_id, (0.0, 0.0))
    cost = (in_tok / 1e6) * price_in + (out_tok / 1e6) * price_out
    return cost * (0.5 if batch else 1.0)


def local_cost_usd(latency_ms: float) -> float:
    """Wall-clock proxy cost for a local inference (PART open-question 5)."""
    return (latency_ms / 1000.0 / 3600.0) * LOCAL_COMPUTE_USD_PER_HOUR


def trial_cost_usd(
    provider: str,
    model_id: str,
    in_tok: int,
    out_tok: int,
    latency_ms: float,
    batch: bool = False,
) -> float:
    """Unified cost for one trial across the API/local divide."""
    if provider == "claude":
        return claude_cost_usd(model_id, in_tok, out_tok, batch)
    return local_cost_usd(latency_ms)


# --------------------------------------------------------------------------- #
# The shape x arm x spec matrix — the real Part-11 deliverable.                #
# --------------------------------------------------------------------------- #


def aggregate_matrix(runs: list, experiments_by_id: dict, derivations_by_id: dict) -> dict:
    """Roll runs up into the bespoke results matrix.

    Returns ``{"cells": [...], "shapes": [...], "arms": [...], "specs": [...]}``
    where each cell is keyed by (shape, arm, spec-signature) and carries accuracy
    (over scorable trials), mean cost, mean latency, token totals, and n. This is
    what the UI renders and what later feeds the firmware-doc cost/perf estimator.
    """
    cells: dict = {}
    shapes, arms, specs = set(), set(), set()
    for run in runs:
        exp = experiments_by_id.get(run.experiment_id)
        if not exp:
            continue
        der = derivations_by_id.get(exp.derivation_id)
        spec_sig = _spec_signature(der.spec) if der else "?"
        arm = exp.arm
        for tr in run.results:
            tr = _as_trial(tr)
            shape = tr.shape
            key = (shape, arm, spec_sig)
            shapes.add(shape); arms.add(arm); specs.add(spec_sig)
            c = cells.setdefault(key, _empty_cell(shape, arm, spec_sig))
            c["n"] += 1
            if tr.error:
                c["errors"] += 1
                continue
            if tr.correct is not None:
                c["scored"] += 1
                c["correct"] += 1 if tr.correct else 0
            c["cost_usd"] += tr.cost_usd
            c["latency_ms"] += tr.latency_ms
            tk = tr.tokens or {}
            c["input_tokens"] += int(tk.get("input_tokens", 0) or 0)
            c["output_tokens"] += int(tk.get("output_tokens", 0) or 0)
    out_cells = []
    for c in cells.values():
        n_ok = max(1, c["n"] - c["errors"])
        c["accuracy"] = (c["correct"] / c["scored"]) if c["scored"] else None
        c["mean_cost_usd"] = c["cost_usd"] / n_ok
        c["mean_latency_ms"] = c["latency_ms"] / n_ok
        out_cells.append(c)
    return {
        "cells": out_cells,
        "shapes": sorted(shapes),
        "arms": sorted(arms),
        "specs": sorted(specs),
    }


def _empty_cell(shape: str, arm: str, spec: str) -> dict:
    return {
        "shape": shape, "arm": arm, "spec": spec,
        "n": 0, "scored": 0, "correct": 0, "errors": 0,
        "cost_usd": 0.0, "latency_ms": 0.0,
        "input_tokens": 0, "output_tokens": 0,
    }


def _spec_signature(spec) -> str:
    return f"{spec.fps}fps/{spec.frames_per_episode}fpe/{spec.frame_res}"


def _as_trial(tr):
    from .models import TrialResult

    return tr if isinstance(tr, TrialResult) else TrialResult.from_dict(tr)


# --------------------------------------------------------------------------- #
# Tracker seam (PART D).                                                       #
# --------------------------------------------------------------------------- #


class Tracker(ABC):
    """Logs a Run's params + metrics. The orchestrator depends only on this."""

    @abstractmethod
    def log_run(self, run, experiment) -> Optional[str]:
        """Persist the run; return a tracker-side run id (or None)."""


class JsonTracker(Tracker):
    """Default: the Run is already stored in our JSON store; nothing extra to do.

    Returns None for ``mlflow_run_id`` — the matrix reads straight from our store,
    so the offline path needs no external tracking server.
    """

    def log_run(self, run, experiment) -> Optional[str]:
        return None


class MLflowTracker(Tracker):  # pragma: no cover - requires a live MLflow server
    """Adapter for the adopted MLflow (PART D) — the documented seam.

    Writes params (arm, fps, fpe, model pin, hardware) and metrics (accuracy by
    shape, cost, latency) via the MLflow API and returns the MLflow run id, which
    the Run stores as a cross-reference. One MLflow run per Run here; per-(arm x
    spec) nesting is PART open-question 3, switchable at this seam.
    """

    def __init__(self, tracking_uri: str, experiment_name: str = "openmartian") -> None:
        import mlflow

        self._mlflow = mlflow
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name)

    def log_run(self, run, experiment) -> Optional[str]:
        mlflow = self._mlflow
        with mlflow.start_run() as ml_run:
            mlflow.log_params({
                "arm": experiment.arm,
                "provider": run.pin.provider,
                "model_id": run.pin.model_id,
                "quantization": run.pin.quantization,
                "gpu_model": run.env.gpu_model,
                "pipeline_git_commit": run.env.pipeline_git_commit,
            })
            scored = [t for t in run.results if _as_trial(t).correct is not None]
            if scored:
                acc = sum(1 for t in scored if _as_trial(t).correct) / len(scored)
                mlflow.log_metric("accuracy", acc)
            mlflow.log_metric("n_trials", len(run.results))
            return ml_run.info.run_id
