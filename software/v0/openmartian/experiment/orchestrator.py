"""The orchestrator (PART A build): CRUD + job orchestration *around* the pipeline.

This is the one place the experiment graph is executed. It never reimplements a
pipeline step — it calls the same modules webcam-live and video-offline use:
:func:`openmartian.recipe.build_prompt`, :func:`openmartian.llm.build_llm`, the
:class:`openmartian.schema.Decision` schema. Its job is the bespoke part:

* **derive** a Dataset x SamplingSpec into an episode set on disk (video.py),
  attaching source-time provenance so labels are inherited by overlap;
* **run** an Experiment — for each episode x recipe, build the recipe-conditioned
  prompt, ask the (real or Fake) model, score against ground truth, and log a
  TrialResult carrying the auto-captured ModelPin + RunEnvironment + cost;
* **aggregate** runs into the shape x arm x spec results matrix.

The arm registry makes "how the pipeline answers" pluggable: ``direct`` is the
recipe-conditioned VLM call (fully implemented); pyramid / RAG arms are declared
seams that raise until built, so the matrix enumerates them honestly.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from ..llm import build_llm
from ..llm.base import LLMClient
from ..recipe import SYSTEM_PROMPT, Recipe, build_prompt
from ..schema import Decision, decision_schema, load_episode
from . import labels as labels_mod
from .models import (
    Dataset,
    Derivation,
    Experiment,
    ModelPin,
    Run,
    SamplingSpec,
    TrialResult,
)
from .pin import capture_model_pin, capture_run_environment
from .store import ExperimentStore
from .tracking import JsonTracker, Tracker, aggregate_matrix, trial_cost_usd
from .video import derive_to_disk

# Arms the orchestrator knows about. "direct" is implemented; the rest are
# declared so the results matrix can enumerate them and so adding one is a
# registry entry, not a rewrite (PART A: the arm is the bespoke abstraction).
KNOWN_ARMS = ["direct", "pyramid_agnostic", "pyramid_specific", "caption_rag", "vision_rag"]
IMPLEMENTED_ARMS = {"direct"}


class Orchestrator:
    """Owns the experiment graph: derive, run, aggregate. Pipeline stays untouched."""

    def __init__(self, store: Optional[ExperimentStore] = None, tracker: Optional[Tracker] = None) -> None:
        self.store = store or ExperimentStore()
        self.tracker = tracker or JsonTracker()

    # -- derivation -------------------------------------------------------- #

    def derive(self, dataset: Dataset, spec: SamplingSpec, max_episodes: Optional[int] = None) -> Derivation:
        """Sample every video in the dataset into one episode set (PART C).

        Reuses ``video.derive_episode_set`` (which reuses ``save_episode``), so the
        derived episodes are indistinguishable from live-captured ones downstream.
        """
        der = Derivation.new(dataset_id=dataset.id, spec=spec)
        der.episode_dir = str(self.store.episodes_dir / der.id)
        episode_ids = []
        for vpath in dataset.video_paths:
            full = self._resolve_video(vpath)
            # derive_to_disk stamps source_video / source_t_start_ms / source_window_ms
            # into each meta.json so labels attach by time-overlap (PART C/D).
            ids = derive_to_disk(full, spec, der.episode_dir, max_episodes=max_episodes)
            episode_ids.extend(ids)
        der.episode_ids = episode_ids
        der.meta["episode_count"] = len(episode_ids)
        self.store.save_derivation(der)
        return der

    def _resolve_video(self, vpath: str) -> str:
        """Allow a bare filename (under the videos store) or an absolute path."""
        from pathlib import Path

        p = Path(vpath)
        if p.is_absolute() and p.exists():
            return str(p)
        candidate = self.store.videos_dir / p.name
        return str(candidate if candidate.exists() else p)

    # -- running ----------------------------------------------------------- #

    def run_experiment(
        self,
        experiment: Experiment,
        *,
        llm: Optional[LLMClient] = None,
        no_cost: bool = False,
        progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> Run:
        """Execute one Experiment -> a Run with per-trial results + the matrix slice.

        ``llm`` lets a caller inject a client (e.g. ``FakeLLMClient`` for a no-cost
        smoke of the paid arm). ``no_cost=True`` is the convenience switch that
        builds a Fake client matching the experiment's pin. Either way the rest of
        the graph runs identically — that is the whole point of the LLMClient seam.
        """
        der = self.store.get_derivation(experiment.derivation_id)
        if der is None:
            raise ValueError(f"derivation {experiment.derivation_id} not found")
        dataset = self.store.get_dataset(der.dataset_id)

        if experiment.arm not in IMPLEMENTED_ARMS:
            raise NotImplementedError(
                f"arm '{experiment.arm}' is a declared seam (KNOWN_ARMS) but not yet "
                f"implemented; implemented: {sorted(IMPLEMENTED_ARMS)}"
            )

        pin_spec = experiment.objective_model
        # Auto-capture the apples-to-apples record at run start (PART F).
        pin = capture_model_pin(
            pin_spec.provider or "ollama",
            pin_spec.model_id,
            temperature=pin_spec.temperature,
            top_p=pin_spec.top_p,
            seed=pin_spec.seed,
            max_tokens=pin_spec.max_tokens,
            api_date=pin_spec.api_date,
            beta_headers=pin_spec.beta_headers,
        )
        env = capture_run_environment(pin.provider, batch=bool((experiment.meta or {}).get("batch")))

        run = Run.new(experiment_id=experiment.id, pin=pin, env=env, status="running")

        if llm is None:
            if no_cost:
                from .runner import FakeLLMClient

                llm = FakeLLMClient()
            else:
                llm = build_llm(
                    pin.provider,
                    model=pin.model_id,
                    system=SYSTEM_PROMPT,
                    max_tokens=pin.max_tokens,
                    temperature=pin.temperature,
                    params={"top_p": pin.top_p, "seed": pin.seed},
                )

        recipes = experiment.recipes or [Recipe().text]
        episode_ids = der.episode_ids
        total = len(episode_ids) * len(recipes)
        done = 0
        for ep_id in episode_ids:
            for recipe_text in recipes:
                done += 1
                if progress:
                    progress(done, total, ep_id)
                tr = self._run_trial(run, experiment, der, dataset, ep_id, recipe_text, llm)
                run.results.append(tr)

        run.status = "done"
        run.summary = self._run_summary(run, experiment, der)
        run.mlflow_run_id = self.tracker.log_run(run, experiment)
        from .models import _now_iso

        run.finished_at = _now_iso()
        self.store.save_run(run)
        return run

    def _run_trial(self, run, experiment, der, dataset, ep_id, recipe_text, llm) -> TrialResult:
        import json
        from pathlib import Path

        shape = (experiment.meta or {}).get("shape", "presence")
        tr = TrialResult.new(
            run_id=run.id,
            experiment_id=experiment.id,
            episode_id=ep_id,
            recipe_text=recipe_text,
            shape=shape,
        )
        try:
            ep_dir = Path(der.episode_dir) / ep_id
            episode = load_episode(ep_dir)
            meta = json.loads((ep_dir / "meta.json").read_text())
            content = build_prompt(episode, Recipe(text=recipe_text))
            schema = decision_schema(["notify"])
            t0 = time.monotonic()
            raw = llm.generate(content, schema=schema, schema_name="decision")
            tr.latency_ms = round((time.monotonic() - t0) * 1000, 1)
            dec = Decision.from_dict(raw)
            tr.decision = {
                "match": dec.match, "confidence": dec.confidence, "action": dec.action,
                "reason": dec.reason, "message": dec.message, "evidence": dec.evidence,
            }
            tr.tokens = dict(getattr(llm, "last_usage", {}) or tr.tokens)
            tr.cost_usd = trial_cost_usd(
                run.pin.provider, run.pin.model_id,
                int(tr.tokens.get("input_tokens", 0) or 0),
                int(tr.tokens.get("output_tokens", 0) or 0),
                tr.latency_ms,
                batch=run.env.batch,
            )
            # Ground truth by time-overlap (PART D), when the dataset has labels.
            if dataset is not None:
                src_video = meta.get("source_video", "")
                start = int(meta.get("source_t_start_ms", 0) or 0)
                wlen = int(meta.get("source_window_ms", 0) or 0)
                window = (start, start + wlen)
                gt = labels_mod.episode_ground_truth(dataset, src_video, window, shape=shape)
                tr.ground_truth = gt
                if shape == "presence":
                    tr.correct = labels_mod.score_presence(tr.decision, gt)
        except Exception as e:  # noqa: BLE001 - one bad trial must not sink the run
            tr.error = str(e)
        return tr

    def _run_summary(self, run, experiment, der) -> dict:
        exps = {experiment.id: experiment}
        ders = {der.id: der}
        return aggregate_matrix([run], exps, ders)

    # -- aggregation ------------------------------------------------------- #

    def matrix(self, experiment_ids: Optional[list] = None) -> dict:
        """Build the full shape x arm x spec matrix over all (or selected) runs."""
        experiments = self.store.list_experiments()
        if experiment_ids is not None:
            experiments = [e for e in experiments if e.id in experiment_ids]
        exps_by_id = {e.id: e for e in experiments}
        ders_by_id = {d.id: d for d in self.store.list_derivations()}
        runs = [r for r in self.store.list_runs() if r.experiment_id in exps_by_id]
        return aggregate_matrix(runs, exps_by_id, ders_by_id)
