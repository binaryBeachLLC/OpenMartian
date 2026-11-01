"""The Job / Runner seam (PART G): start synchronous, async drops in later.

PART G's decision: start synchronous ("run and wait") but design the Job / Runner
seam from day one so a queue or a remote GPU is a later drop-in with no rework —
the same pattern as ``EpisodeSource`` / ``LLMClient``. The orchestrator calls a
``Runner`` and never knows whether the job ran in-process, on a queue, or on a
remote pinned container.

Also here: ``FakeLLMClient`` — the no-cost stand-in that lets a Run exercise the
full graph (derive -> pin -> trial -> matrix) and the *paid* Claude path's request
construction without ever spending. It satisfies the same ``LLMClient`` interface
the pipeline consumes, so substituting it is a config flag, not a fork.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..llm.base import Content, LLMClient, LLMConfig
from .models import ModelPin, RunEnvironment


# --------------------------------------------------------------------------- #
# Job                                                                          #
# --------------------------------------------------------------------------- #


@dataclass
class Job:
    job_id: str
    kind: str  # "derive" | "label_sync" | "run_experiment"
    params: dict = field(default_factory=dict)
    status: str = "pending"  # pending | running | done | failed
    pin: Optional[ModelPin] = None
    env: Optional[RunEnvironment] = None


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #


class Runner(ABC):
    """Executes a Job. Location / async-ness is the only thing that varies."""

    @abstractmethod
    def submit(self, job: Job, work: Callable[[], dict]) -> str:
        ...

    @abstractmethod
    def status(self, job_id: str) -> str:
        ...

    @abstractmethod
    def result(self, job_id: str) -> dict:
        ...


class LocalRunner(Runner):
    """v1: synchronous in-process. 'Run and wait.'

    ``work`` is the thunk the orchestrator hands over (the actual experiment loop).
    Keeping the closure here means the queue/remote runners later only change
    *where* ``work`` runs, not what it is.
    """

    def __init__(self) -> None:
        self._results: dict = {}
        self._status: dict = {}

    def submit(self, job: Job, work: Callable[[], dict]) -> str:
        self._status[job.job_id] = "running"
        try:
            self._results[job.job_id] = work()
            self._status[job.job_id] = "done"
        except Exception as e:  # noqa: BLE001 - surface as a failed job, never crash the server
            self._results[job.job_id] = {"error": str(e)}
            self._status[job.job_id] = "failed"
        return job.job_id

    def status(self, job_id: str) -> str:
        return self._status.get(job_id, "unknown")

    def result(self, job_id: str) -> dict:
        return self._results.get(job_id, {})


class QueueRunner(Runner):  # pragma: no cover - FUTURE (RQ / Dramatiq / Celery)
    """Same interface, async. Drops in when overnight sweeps outgrow run-and-wait."""

    def submit(self, job: Job, work: Callable[[], dict]) -> str:
        raise NotImplementedError("QueueRunner is a PART G seam, not yet wired")

    def status(self, job_id: str) -> str:
        raise NotImplementedError

    def result(self, job_id: str) -> dict:
        raise NotImplementedError


class ModalRunner(Runner):  # pragma: no cover - FUTURE (remote pinned-container GPU)
    """Same interface, remote. The pinned Ollama container as a config flag (E.4)."""

    def submit(self, job: Job, work: Callable[[], dict]) -> str:
        raise NotImplementedError("ModalRunner is a PART G seam, not yet wired")

    def status(self, job_id: str) -> str:
        raise NotImplementedError

    def result(self, job_id: str) -> dict:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# FakeLLMClient — no-cost stand-in for smoke tests (paid path, never billed).  #
# --------------------------------------------------------------------------- #


class FakeLLMClient(LLMClient):
    """A deterministic, free ``LLMClient`` for end-to-end smoke runs.

    Returns a schema-shaped decision dict without any network call, and records a
    nominal token count so cost/latency aggregation is exercised. Because it
    satisfies the exact ``generate`` seam the pipeline consumes, a smoke Run drives
    derivation, pin capture, trial logging, and the matrix identically to a real
    Run — only the inference is replaced. This is how the Claude arm is validated
    end to end without sending a costing call to the paid API.
    """

    def __init__(self, config: Optional[LLMConfig] = None, *, answer_match: bool = True) -> None:
        super().__init__(config or LLMConfig(provider="fake", model="fake-vlm"))
        self.answer_match = answer_match
        self.calls = 0

    def generate(self, content: list, *, schema: dict, schema_name: str = "decision") -> dict:
        self.calls += 1
        # Count images in the prompt so token usage scales with the episode, the
        # way a real VLM bill would — keeps the cost axis non-trivial in tests.
        from ..llm.base import ImagePart

        n_images = sum(1 for c in content if isinstance(c, ImagePart))
        self.last_usage = {
            "input_tokens": 200 + n_images * 100,
            "output_tokens": 40,
            "total_tokens": 240 + n_images * 100,
        }
        props = (schema or {}).get("properties", {})
        out: dict = {}
        if "match" in props:
            out["match"] = bool(self.answer_match)
        if "confidence" in props:
            out["confidence"] = 0.92
        if "action" in props:
            out["action"] = "notify" if self.answer_match else "none"
        if "reason" in props:
            out["reason"] = "fake deterministic verdict (no model was called)"
        if "evidence" in props:
            out["evidence"] = [{"frame": 0, "supports": bool(self.answer_match),
                                "observation": "fake evidence"}]
        return out
