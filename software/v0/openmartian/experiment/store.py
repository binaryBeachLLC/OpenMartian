"""JSON-file persistence for the experiment graph (PART D: our DB owns the graph).

Deliberately simple, matching the webapp's existing ``data/`` convention: one JSON
file per node type under ``data/experiment/``, plus a ``videos/`` directory for
uploaded raw footage and an ``episodes/`` tree for derived episode sets. SQLite is
the obvious upgrade (PART open-question 1, "lean SQLite for v1") and every access
goes through this module, so swapping the backend later touches only this file.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional, Union

from .models import Dataset, Derivation, Experiment, Run

# Default root is software/v0/data — overridable so tests get an isolated store.
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data"


class ExperimentStore:
    """All experiment-graph persistence, behind one lock (single-process dev tool)."""

    def __init__(self, data_root: Union[str, Path, None] = None) -> None:
        self.root = Path(data_root) if data_root else _DEFAULT_ROOT
        self.exp_dir = self.root / "experiment"
        self.videos_dir = self.exp_dir / "videos"
        self.episodes_dir = self.exp_dir / "episodes"
        for d in (self.exp_dir, self.videos_dir, self.episodes_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # -- low-level json list helpers --------------------------------------- #

    def _path(self, name: str) -> Path:
        return self.exp_dir / f"{name}.json"

    def _read(self, name: str) -> list:
        p = self._path(name)
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text())
        except Exception:
            return []

    def _write(self, name: str, items: list) -> None:
        self._path(name).write_text(json.dumps(items, indent=2))

    # -- datasets ---------------------------------------------------------- #

    def list_datasets(self) -> list:
        return [Dataset.from_dict(d) for d in self._read("datasets")]

    def get_dataset(self, ds_id: str) -> Optional[Dataset]:
        return next((d for d in self.list_datasets() if d.id == ds_id), None)

    def save_dataset(self, ds: Dataset) -> Dataset:
        with self._lock:
            items = self._read("datasets")
            items = [i for i in items if i.get("id") != ds.id]
            items.append(ds.to_dict())
            self._write("datasets", items)
        return ds

    def delete_dataset(self, ds_id: str) -> bool:
        with self._lock:
            items = self._read("datasets")
            kept = [i for i in items if i.get("id") != ds_id]
            self._write("datasets", kept)
            return len(kept) != len(items)

    # -- derivations ------------------------------------------------------- #

    def list_derivations(self, dataset_id: Optional[str] = None) -> list:
        ds = [Derivation.from_dict(d) for d in self._read("derivations")]
        return [d for d in ds if dataset_id is None or d.dataset_id == dataset_id]

    def get_derivation(self, der_id: str) -> Optional[Derivation]:
        return next((d for d in self.list_derivations() if d.id == der_id), None)

    def save_derivation(self, der: Derivation) -> Derivation:
        with self._lock:
            items = self._read("derivations")
            items = [i for i in items if i.get("id") != der.id]
            items.append(der.to_dict())
            self._write("derivations", items)
        return der

    def delete_derivation(self, der_id: str) -> bool:
        with self._lock:
            items = self._read("derivations")
            kept = [i for i in items if i.get("id") != der_id]
            self._write("derivations", kept)
            return len(kept) != len(items)

    # -- experiments ------------------------------------------------------- #

    def list_experiments(self, derivation_id: Optional[str] = None) -> list:
        xs = [Experiment.from_dict(d) for d in self._read("experiments")]
        return [x for x in xs if derivation_id is None or x.derivation_id == derivation_id]

    def get_experiment(self, exp_id: str) -> Optional[Experiment]:
        return next((x for x in self.list_experiments() if x.id == exp_id), None)

    def save_experiment(self, exp: Experiment) -> Experiment:
        with self._lock:
            items = self._read("experiments")
            items = [i for i in items if i.get("id") != exp.id]
            items.append(exp.to_dict())
            self._write("experiments", items)
        return exp

    def delete_experiment(self, exp_id: str) -> bool:
        with self._lock:
            items = self._read("experiments")
            kept = [i for i in items if i.get("id") != exp_id]
            self._write("experiments", kept)
            return len(kept) != len(items)

    # -- runs -------------------------------------------------------------- #

    def list_runs(self, experiment_id: Optional[str] = None) -> list:
        rs = [Run.from_dict(d) for d in self._read("runs")]
        rs = [r for r in rs if experiment_id is None or r.experiment_id == experiment_id]
        return sorted(rs, key=lambda r: r.created_at, reverse=True)

    def get_run(self, run_id: str) -> Optional[Run]:
        return next((r for r in self.list_runs() if r.id == run_id), None)

    def save_run(self, run: Run) -> Run:
        with self._lock:
            items = self._read("runs")
            items = [i for i in items if i.get("id") != run.id]
            items.append(run.to_dict())
            self._write("runs", items)
        return run

    def delete_run(self, run_id: str) -> bool:
        with self._lock:
            items = self._read("runs")
            kept = [i for i in items if i.get("id") != run_id]
            self._write("runs", kept)
            return len(kept) != len(items)
