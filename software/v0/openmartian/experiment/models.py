"""The experiment data model (PART C / PART F / PART H of the design doc).

Pure dataclasses with ``to_dict`` / ``from_dict`` round-trips so the store layer
(``store.py``) can persist them as JSON. **Metadata at every level**: every node
in the graph (Dataset, Derivation, Experiment, Run, TrialResult) carries a free
-form ``meta`` dict and a ``notes`` string, so a recipe, a result, or a one-off
observation can attach wherever it belongs — exactly as requested.

Nothing here re-implements pipeline logic; ``SamplingSpec`` maps straight onto
the existing :class:`openmartian.schema.EpisodeConfig`, and ``ModelPin`` carries
the sampling params the existing :func:`openmartian.llm.build_llm` already reads.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..schema import EpisodeConfig


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- #
# SamplingSpec — the sweep point (fps / fpe / res) over FIXED footage.         #
# This is the bespoke abstraction; it lowers to the pipeline's EpisodeConfig.  #
# --------------------------------------------------------------------------- #


@dataclass
class SamplingSpec:
    """One point in the fps / frames-per-episode / resolution sweep (PART C).

    Episodes are derived by *recording high and sampling down* a fixed video, so
    fps and fpe are sweep params over the same footage — label once, sweep many.
    """

    fps: float = 1.0
    frames_per_episode: int = 5
    frame_res: str = "320x240"
    jpeg_q: int = 5
    # stride_s: seconds to advance the window start between successive episodes.
    # 0 / None -> back-to-back, non-overlapping windows (window_s apart).
    stride_s: Optional[float] = None

    def to_episode_config(self) -> EpisodeConfig:
        """Lower this sweep point onto the pipeline's capture knobs (no fork)."""
        return EpisodeConfig(
            fps=self.fps,
            frames_per_episode=self.frames_per_episode,
            frame_res=self.frame_res,
            jpeg_q=self.jpeg_q,
        )

    @property
    def window_s(self) -> float:
        return self.frames_per_episode / self.fps if self.fps else 0.0

    @property
    def effective_stride_s(self) -> float:
        return self.stride_s if self.stride_s else self.window_s

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SamplingSpec":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


# --------------------------------------------------------------------------- #
# Label — time-keyed ground truth on the Dataset (PART D, firmware-doc 11.3).  #
# Labels attach to source-video time, so every Derivation inherits them by     #
# time-overlap. Shapes start at "presence"; count/anticipate are seams.        #
# --------------------------------------------------------------------------- #


@dataclass
class Label:
    """A time-keyed label on a Dataset video (an interval or an instant).

    ``shape`` is the question-shape this label is ground truth for. v1 implements
    ``presence`` (condition true across ``[t_start_ms, t_end_ms]``); ``count`` and
    ``anticipate`` are declared so the post-processor and matrix can grow into
    them (PART D open question 2) without a schema change.
    """

    id: str
    video: str  # which dataset video (basename) this label keys into
    t_start_ms: int
    t_end_ms: int
    value: Any = True  # presence: bool; count: int; free for other shapes
    shape: str = "presence"  # presence | count | anticipate | ...
    label: str = ""  # human description of the condition
    source: str = "builtin"  # builtin | label_studio
    meta: dict = field(default_factory=dict)

    @staticmethod
    def new(**kw) -> "Label":
        return Label(id=_new_id("lbl-"), **kw)

    def overlaps(self, t0_ms: int, t1_ms: int) -> bool:
        """True if this label's interval overlaps the window ``[t0, t1)``.

        Instants (t_start == t_end) count as overlapping any window that contains
        the instant — the anticipate "terminal instant" maps cleanly onto this.
        """
        if self.t_start_ms == self.t_end_ms:
            return t0_ms <= self.t_start_ms < t1_ms
        return self.t_start_ms < t1_ms and self.t_end_ms > t0_ms

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Label":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


# --------------------------------------------------------------------------- #
# Pinning metadata (PART F) — the apples-to-apples record. Auto-captured at run #
# start by pin.py; rides on every Run and logs to the tracker as params.       #
# --------------------------------------------------------------------------- #


@dataclass
class ModelPin:
    provider: str = "ollama"  # "claude" | "ollama"
    model_id: str = ""
    # --- ollama-specific (auto-pulled from the Ollama API) ---
    model_digest: Optional[str] = None
    quantization: Optional[str] = None
    parameter_size: Optional[str] = None
    modelfile_params: Optional[dict] = None
    # --- api-specific ---
    api_date: Optional[str] = None
    beta_headers: Optional[list] = None
    # --- sampling (BOTH paths; part of reproducibility) ---
    temperature: float = 0.0
    top_p: Optional[float] = None
    seed: Optional[int] = None
    max_tokens: int = 1024

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ModelPin":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class RunEnvironment:
    # --- software ---
    ollama_version: Optional[str] = None
    backend_version: Optional[str] = None
    docker_image_digest: Optional[str] = None
    pipeline_git_commit: str = ""
    # --- hardware (auto-detected per Run) ---
    gpu_model: Optional[str] = None
    gpu_vram_gb: Optional[float] = None
    cpu: Optional[str] = None
    ram_gb: Optional[float] = None
    os: Optional[str] = None
    cuda_driver: Optional[str] = None
    # --- routing ---
    endpoint: Optional[str] = None
    inference_geo: Optional[str] = None
    batch: bool = False
    captured_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RunEnvironment":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


# --------------------------------------------------------------------------- #
# The graph: Dataset -> Derivation -> Experiment -> Run -> TrialResult.        #
# --------------------------------------------------------------------------- #


@dataclass
class Dataset:
    """Raw video file(s) + time-keyed labels. The input is video now, not webcam."""

    id: str
    name: str
    video_paths: list = field(default_factory=list)  # paths under the videos store
    label_studio_project_id: Optional[str] = None
    labels: list = field(default_factory=list)  # list[Label] (synced or built-in)
    notes: str = ""
    meta: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    @staticmethod
    def new(name: str, video_paths: Optional[list] = None, **kw) -> "Dataset":
        return Dataset(id=_new_id("ds-"), name=name, video_paths=video_paths or [], **kw)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["labels"] = [l.to_dict() if isinstance(l, Label) else l for l in self.labels]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Dataset":
        kw = {k: d.get(k) for k in cls.__dataclass_fields__ if k in d}
        kw["labels"] = [Label.from_dict(l) for l in d.get("labels", [])]
        return cls(**kw)


@dataclass
class Derivation:
    """(Dataset x SamplingSpec) -> a concrete episode set on disk (ffmpeg output)."""

    id: str
    dataset_id: str
    spec: SamplingSpec
    episode_dir: str = ""  # directory holding the derived episodes
    episode_ids: list = field(default_factory=list)
    notes: str = ""
    meta: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    @staticmethod
    def new(dataset_id: str, spec: SamplingSpec, **kw) -> "Derivation":
        return Derivation(id=_new_id("der-"), dataset_id=dataset_id, spec=spec, **kw)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["spec"] = self.spec.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Derivation":
        kw = {k: d.get(k) for k in cls.__dataclass_fields__ if k in d}
        kw["spec"] = SamplingSpec.from_dict(d.get("spec", {}))
        return cls(**kw)


@dataclass
class Experiment:
    """(Derivation x arm x models x judge cadence) -> the thing to run (PART H).

    ``recipes`` are the recipe-conditioned questions this experiment asks (the
    metadata that turns an arm into a concrete query). The arm selects *how* the
    pipeline answers; v1 ships the ``direct`` arm and registers the rest.
    """

    id: str
    derivation_id: str
    arm: str = "direct"  # direct | pyramid_agnostic | caption_rag | vision_rag | ...
    objective_model: ModelPin = field(default_factory=ModelPin)
    judge_model: Optional[ModelPin] = None
    judge_cadence_s: float = 0.0
    recipes: list = field(default_factory=list)  # list[str] recipe questions
    name: str = ""
    notes: str = ""
    meta: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    @staticmethod
    def new(derivation_id: str, **kw) -> "Experiment":
        return Experiment(id=_new_id("exp-"), derivation_id=derivation_id, **kw)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["objective_model"] = self.objective_model.to_dict()
        d["judge_model"] = self.judge_model.to_dict() if self.judge_model else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Experiment":
        kw = {k: d.get(k) for k in cls.__dataclass_fields__ if k in d}
        kw["objective_model"] = ModelPin.from_dict(d.get("objective_model") or {})
        jm = d.get("judge_model")
        kw["judge_model"] = ModelPin.from_dict(jm) if jm else None
        return cls(**kw)


@dataclass
class TrialResult:
    """One (episode x recipe) cell — the atom the results matrix aggregates."""

    id: str
    run_id: str
    experiment_id: str
    episode_id: str
    recipe_text: str
    shape: str = "presence"
    decision: Optional[dict] = None  # the parsed Decision dict
    ground_truth: Optional[Any] = None  # from labels, when available
    correct: Optional[bool] = None  # decision vs ground_truth (when computable)
    tokens: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    @staticmethod
    def new(**kw) -> "TrialResult":
        return TrialResult(id=_new_id("res-"), **kw)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TrialResult":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Run:
    """One execution of an Experiment in a pinned environment (PART H).

    Carries the ModelPin actually exercised + the RunEnvironment (the
    apples-to-apples record), the cross-ref to the tracker (``mlflow_run_id``),
    and the per-trial results.
    """

    id: str
    experiment_id: str
    pin: ModelPin = field(default_factory=ModelPin)
    env: RunEnvironment = field(default_factory=RunEnvironment)
    mlflow_run_id: Optional[str] = None
    status: str = "pending"  # pending | running | done | failed
    results: list = field(default_factory=list)  # list[TrialResult]
    summary: dict = field(default_factory=dict)  # the rolled-up matrix for this run
    error: Optional[str] = None
    notes: str = ""
    meta: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    finished_at: Optional[str] = None

    @staticmethod
    def new(experiment_id: str, **kw) -> "Run":
        return Run(id=_new_id("run-"), experiment_id=experiment_id, **kw)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pin"] = self.pin.to_dict()
        d["env"] = self.env.to_dict()
        d["results"] = [r.to_dict() if isinstance(r, TrialResult) else r for r in self.results]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Run":
        kw = {k: d.get(k) for k in cls.__dataclass_fields__ if k in d}
        kw["pin"] = ModelPin.from_dict(d.get("pin") or {})
        kw["env"] = RunEnvironment.from_dict(d.get("env") or {})
        kw["results"] = [TrialResult.from_dict(r) for r in d.get("results", [])]
        return cls(**kw)
