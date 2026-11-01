"""Labels: time-keyed ground truth + the Label Studio post-processor seam (PART D).

Labels attach to the **Dataset**, keyed to source-video time, so every Derivation
under it inherits them by time-overlap (label once, sweep many). v1 ships a
built-in labeler (the ``Label`` objects on a Dataset) and a ``LabelSource``
abstraction whose ``LabelStudioSource`` adapter is the documented seam for the
adopted tool — the app pulls completed annotations via the Label Studio API and
post-processes its regions into our ``Label`` schema. The built-in path keeps the
whole graph smoke-testable offline, exactly as the design's build-order demands.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from .models import Dataset, Label


# --------------------------------------------------------------------------- #
# Ground-truth resolution: which labels does an episode inherit, and what is   #
# the per-shape answer? v1 implements the "presence" shape end to end.         #
# --------------------------------------------------------------------------- #


def episode_ground_truth(
    dataset: Dataset,
    src_video: str,
    window_ms: tuple,
    shape: str = "presence",
) -> Optional[Any]:
    """Resolve ground truth for an episode window from the dataset's labels.

    presence -> ``True`` if any positive label for ``src_video`` overlaps the
    window, ``False`` if labels exist for the video but none overlap, ``None`` if
    the video is unlabelled (so accuracy isn't fabricated from absence of data).

    count -> number of distinct labels overlapping the window (best-effort).
    Other shapes return ``None`` until their post-processor lands (open Q2).
    """
    t0, t1 = window_ms
    vid_labels = [
        l for l in _labels(dataset)
        if l.video == src_video and l.shape == shape
    ]
    if not vid_labels:
        return None
    overlapping = [l for l in vid_labels if l.overlaps(t0, t1)]
    if shape == "presence":
        return any(bool(l.value) for l in overlapping)
    if shape == "count":
        return len(overlapping)
    return overlapping[0].value if overlapping else None


def _labels(dataset: Dataset) -> list:
    out = []
    for l in dataset.labels:
        out.append(l if isinstance(l, Label) else Label.from_dict(l))
    return out


def score_presence(decision: Optional[dict], ground_truth: Optional[Any]) -> Optional[bool]:
    """Compare a presence decision against ground truth. None when not scorable."""
    if ground_truth is None or decision is None:
        return None
    return bool(decision.get("match")) == bool(ground_truth)


# --------------------------------------------------------------------------- #
# LabelSource seam — built-in vs adopted Label Studio (PART D).                #
# --------------------------------------------------------------------------- #


class LabelSource(ABC):
    """Anything that yields ``Label`` objects for a dataset's videos."""

    @abstractmethod
    def fetch(self) -> list:
        ...


class BuiltinLabelSource(LabelSource):
    """The labels a user authors in our own UI/CLI — already in our schema."""

    def __init__(self, labels: list) -> None:
        self._labels = labels

    def fetch(self) -> list:
        return list(self._labels)


class LabelStudioSource(LabelSource):
    """Adapter for the adopted Label Studio (PART D) — the documented seam.

    Pulls completed annotations from a Label Studio project via its API and
    post-processes the raw regions into our time-keyed ``Label`` schema. Where our
    shapes don't map cleanly (count-with-crossing-timestamps; anticipate =
    precursor interval + terminal instant), the mapping is built from raw regions
    here. Kept import-light and lazy so the offline path never needs the service.
    """

    def __init__(self, base_url: str, api_key: str, project_id: str, video: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.project_id = project_id
        self.video = video

    def fetch(self) -> list:  # pragma: no cover - requires a live Label Studio
        import requests

        resp = requests.get(
            f"{self.base_url}/api/projects/{self.project_id}/export?exportType=JSON",
            headers={"Authorization": f"Token {self.api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        return self.post_process(resp.json())

    def post_process(self, raw_tasks: list) -> list:
        """Map Label Studio task/region JSON -> our ``Label`` list (presence v1).

        Looks for temporal/interval regions (``value.start`` / ``value.end`` in
        seconds, or ``ranges``) and emits one presence ``Label`` per region. This
        is intentionally small and explicit so the mapping for count/anticipate
        can be grown alongside the first real labelled video.
        """
        out = []
        for task in raw_tasks or []:
            for ann in task.get("annotations", []) or []:
                for region in ann.get("result", []) or []:
                    val = region.get("value", {}) or {}
                    start = val.get("start")
                    end = val.get("end", start)
                    if start is None:
                        continue
                    labels = val.get("labels") or val.get("timelinelabels") or [""]
                    out.append(
                        Label.new(
                            video=self.video,
                            t_start_ms=int(float(start) * 1000),
                            t_end_ms=int(float(end) * 1000),
                            value=True,
                            shape="presence",
                            label=str(labels[0]) if labels else "",
                            source="label_studio",
                            meta={"region_id": region.get("id")},
                        )
                    )
        return out
