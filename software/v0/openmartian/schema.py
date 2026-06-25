"""Core schema for the v0 host pipeline.

Mirrors PART 5 / PART 9 of docs/Open_Martian_Firmware_Software_Design.md.

The ``Episode`` is v0's stand-in for the normalized ``Event`` object — it is the
non-negotiable seam: capture produces Episodes, the LLM consumes Episodes, and
everything downstream speaks the same object. Episodes are persisted to local
disk so capture and interpretation are fully decoupled (you record now, manually
pick an episode to feed the model later).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

# --------------------------------------------------------------------------- #
# Raw media (the carrier — never pre-discarded; PART 4 "red shirt" invariant)  #
# --------------------------------------------------------------------------- #


@dataclass
class Frame:
    """A single still frame. Bytes live in-memory (``data``) and/or on disk (``uri``)."""

    eye: str  # "left" in single-camera v0
    res: str  # e.g. "320x240"
    t_offset_ms: int  # offset of this frame within the episode window
    jpeg_q: int = 5  # ffmpeg -q:v scale (2=best .. 31=worst)
    uri: Optional[str] = None  # path to the .jpg on disk
    data: Optional[bytes] = None  # raw JPEG bytes when held in memory

    def jpeg_bytes(self) -> bytes:
        """Return the JPEG bytes, lazily loading from ``uri`` if not in memory."""
        if self.data is not None:
            return self.data
        if self.uri is not None:
            return Path(self.uri).read_bytes()
        raise ValueError(f"Frame {self!r} has neither in-memory data nor a uri")


@dataclass
class EpisodeConfig:
    """Capture knobs. ``frame_res`` is the dominant cost lever (tokens ~ W x H)."""

    fps: float = 1.0  # frames captured per second
    frames_per_episode: int = 5  # window span = frames_per_episode / fps seconds
    frame_res: str = "320x240"  # WxH; keep small — quadratic on VLM token cost
    jpeg_q: int = 5  # ffmpeg -q:v (lower = higher quality / bigger)

    @property
    def width(self) -> int:
        return int(self.frame_res.lower().split("x")[0])

    @property
    def height(self) -> int:
        return int(self.frame_res.lower().split("x")[1])

    @property
    def window_s(self) -> float:
        return self.frames_per_episode / self.fps if self.fps else 0.0


@dataclass
class Episode:
    """A bounded window of frames from one camera — v0's unit of perception."""

    episode_id: str
    started: datetime
    frames: list[Frame] = field(default_factory=list)
    config: Optional[EpisodeConfig] = None
    source_url: Optional[str] = None

    @staticmethod
    def new_id(started: Optional[datetime] = None) -> str:
        started = started or datetime.now(timezone.utc)
        stamp = started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{stamp}_{uuid.uuid4().hex[:6]}"

    @classmethod
    def create(
        cls,
        config: Optional[EpisodeConfig] = None,
        source_url: Optional[str] = None,
        started: Optional[datetime] = None,
    ) -> "Episode":
        started = started or datetime.now(timezone.utc)
        return cls(
            episode_id=cls.new_id(started),
            started=started,
            frames=[],
            config=config,
            source_url=source_url,
        )


# --------------------------------------------------------------------------- #
# Episode persistence — the on-disk format is authoritative HERE so capture    #
# and replay never diverge. Layout:                                            #
#   <root>/<episode_id>/                                                        #
#       meta.json                                                              #
#       frame_000.jpg, frame_001.jpg, ...                                      #
# --------------------------------------------------------------------------- #


def save_episode(episode: Episode, root: Union[str, Path]) -> Path:
    """Write an episode (frames + meta.json) to ``<root>/<episode_id>/``."""
    root = Path(root)
    ep_dir = root / episode.episode_id
    ep_dir.mkdir(parents=True, exist_ok=True)

    frame_meta = []
    for i, fr in enumerate(episode.frames):
        fname = f"frame_{i:03d}.jpg"
        (ep_dir / fname).write_bytes(fr.jpeg_bytes())
        frame_meta.append(
            {
                "file": fname,
                "eye": fr.eye,
                "res": fr.res,
                "t_offset_ms": fr.t_offset_ms,
                "jpeg_q": fr.jpeg_q,
            }
        )

    meta = {
        "episode_id": episode.episode_id,
        "started": episode.started.astimezone(timezone.utc).isoformat(),
        "source_url": episode.source_url,
        "config": episode.config.__dict__ if episode.config else None,
        "frames": frame_meta,
    }
    (ep_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return ep_dir


def load_episode(path: Union[str, Path]) -> Episode:
    """Load an episode from a directory written by :func:`save_episode`.

    Frames are lazy: ``Frame.uri`` points at the .jpg; bytes load on demand.
    """
    ep_dir = Path(path)
    meta = json.loads((ep_dir / "meta.json").read_text())

    cfg = EpisodeConfig(**meta["config"]) if meta.get("config") else None
    frames = [
        Frame(
            eye=fm["eye"],
            res=fm["res"],
            t_offset_ms=fm["t_offset_ms"],
            jpeg_q=fm.get("jpeg_q", 5),
            uri=str(ep_dir / fm["file"]),
        )
        for fm in meta.get("frames", [])
    ]
    return Episode(
        episode_id=meta["episode_id"],
        started=datetime.fromisoformat(meta["started"]),
        frames=frames,
        config=cfg,
        source_url=meta.get("source_url"),
    )


def list_episodes(root: Union[str, Path]) -> list[Path]:
    """Return episode directories under ``root``, oldest first (id sorts by time)."""
    root = Path(root)
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "meta.json").exists())


# --------------------------------------------------------------------------- #
# Decision schema + parsed verdict (PART 9.4). The LLM returns a dict matching #
# this schema; a deterministic Dispatcher executes the side effect.           #
# --------------------------------------------------------------------------- #


def decision_schema(allowed_actions: list[str]) -> dict:
    """JSON Schema the LLM is forced to satisfy. ``allowed_actions`` clamps the enum.

    The ``evidence`` array makes the model cite its work: which frames (by the
    ``Frame N`` index they are labeled with in the prompt) justify the verdict,
    each with a one-phrase observation — pointing at the pixels it relied on
    instead of just asserting a conclusion.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "match": {
                "type": "boolean",
                "description": "Did the recipe condition occur across the frames?",
            },
            "confidence": {
                "type": "number",
                "description": "Calibrated confidence in [0,1].",
            },
            "reason": {
                "type": "string",
                "description": "Brief justification grounded in what is visible.",
            },
            "evidence": {
                "type": "array",
                "description": (
                    "Frames that justify the verdict. Cite each frame by its "
                    "integer index (the 'Frame N' label in the prompt) with a "
                    "short observation. May be empty if nothing is relevant."
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "frame": {
                            "type": "integer",
                            "description": "Frame index (the 'Frame N' label) this evidence refers to.",
                        },
                        "supports": {
                            "type": "boolean",
                            "description": "True if this frame supports match=true; false if it shows the condition absent.",
                        },
                        "observation": {
                            "type": "string",
                            "description": "One short phrase: what in this frame is relevant.",
                        },
                    },
                    "required": ["frame", "observation"],
                },
            },
            "action": {
                "type": "string",
                "enum": ["none", *allowed_actions],
                "description": "Requested side effect; 'none' when match is false.",
            },
            "message": {
                "type": "string",
                "description": "Text to send if action != none.",
            },
        },
        "required": ["match", "confidence", "action", "evidence"],
    }


@dataclass
class Decision:
    """Parsed from the schema-guaranteed dict the LLM returns."""

    match: bool
    confidence: float
    action: str = "none"
    reason: str = ""
    message: str = ""
    evidence: list = field(default_factory=list)  # [{frame, supports, observation}]

    @classmethod
    def from_dict(cls, raw: dict) -> "Decision":
        ev = raw.get("evidence") or []
        return cls(
            match=bool(raw.get("match", False)),
            confidence=float(raw.get("confidence", 0.0) or 0.0),
            action=str(raw.get("action", "none") or "none"),
            reason=str(raw.get("reason", "") or ""),
            message=str(raw.get("message", "") or ""),
            evidence=ev if isinstance(ev, list) else [],
        )
