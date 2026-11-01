"""OpenMartian experiment orchestration layer (the Experiment App & Compute design).

This package is the *bespoke shell around the adopted pipeline* described in
``docs/Open_Martian_Experiment_App_and_Compute_Design.md``. PART B's load-bearing
principle: **the app invokes the pipeline, never reimplements it.** Everything
here is CRUD + job orchestration + visualization *around* the same modules the
webcam-live and video-offline contexts use unchanged — ``Episode``,
``build_prompt``, ``build_llm``, ``process_episode``, the ``Decision`` schema.

Data model (PART C / PART H)::

    Dataset      raw video file(s) + time-keyed labels
       |
    Derivation   (Dataset x SamplingSpec) -> a concrete episode set on disk
       |
    Experiment   (Derivation x arm x models x judge) -> the thing to run
       |
    Run          one execution in a pinned environment (ModelPin + RunEnvironment)
       |
    Results      per-shape accuracy + cost + latency, rendered as the matrix

Each arrow is a Job (PART G). The bespoke parts (orchestration + data model +
results matrix) are built here; the deceptively-huge-but-solved parts (Label
Studio, MLflow) are *adopted* behind seams (``labels.py``, ``tracking.py``) with
working built-in fallbacks so the whole graph is smoke-testable offline.
"""

from __future__ import annotations

from .models import (
    Dataset,
    Derivation,
    Experiment,
    Label,
    ModelPin,
    Run,
    RunEnvironment,
    SamplingSpec,
    TrialResult,
)
from .orchestrator import IMPLEMENTED_ARMS, KNOWN_ARMS, Orchestrator
from .runner import FakeLLMClient, Job, LocalRunner, Runner
from .store import ExperimentStore

__all__ = [
    "Dataset",
    "Derivation",
    "Experiment",
    "Label",
    "ModelPin",
    "Run",
    "RunEnvironment",
    "SamplingSpec",
    "TrialResult",
    "Orchestrator",
    "ExperimentStore",
    "Runner",
    "LocalRunner",
    "Job",
    "FakeLLMClient",
    "KNOWN_ARMS",
    "IMPLEMENTED_ARMS",
]
