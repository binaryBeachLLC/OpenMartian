"""Episode capture (RTSP webcam via ffmpeg) and replay (saved episodes on disk).

Part of the v0 host pipeline: this is the *front* of the seam described in
``schema.py``. A capture source produces :class:`~openmartian.schema.Episode`
objects; everything downstream consumes Episodes and never touches the camera
directly. Two sources are provided:

* :class:`WebcamEpisodeSource` — grabs fresh episodes from the live RTSP stream.
* :class:`FolderEpisodeSource` — replays episodes previously written to disk
  (manual review today, regression fixtures tomorrow).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator, Optional

from .schema import Episode, EpisodeConfig, Frame, list_episodes, load_episode

# --------------------------------------------------------------------------- #
# Stream location. The webcam lives behind a NetBird VPN; the default matches  #
# the user's ``webcam`` alias. Override per-run via the OM_RTSP_URL env var.   #
# --------------------------------------------------------------------------- #

DEFAULT_RTSP_URL = "rtsp://100.68.17.41:8554/webcam"


def default_rtsp_url() -> str:
    """Return the RTSP URL, honouring the ``OM_RTSP_URL`` env override."""
    return os.environ.get("OM_RTSP_URL", DEFAULT_RTSP_URL)


# --------------------------------------------------------------------------- #
# Capture                                                                      #
# --------------------------------------------------------------------------- #


def capture_episode(
    url: str,
    config: EpisodeConfig,
    eye: str = "left",
    ffmpeg_timeout: float = 30.0,
) -> Episode:
    """Capture one :class:`~openmartian.schema.Episode` from an RTSP stream.

    Shells out to ffmpeg to grab exactly ``config.frames_per_episode`` JPEG
    frames at ``config.fps``, scaled to ``config.frame_res``. We use
    ``-rtsp_transport tcp`` because UDP is unreliable across the NetBird VPN —
    TCP trades a little latency for not dropping packets mid-stream.

    Frame bytes are read into memory; the caller decides whether to persist the
    Episode via :func:`openmartian.schema.save_episode`.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        out_pattern = str(Path(tmpdir) / "frame_%03d.jpg")
        vf = f"fps={config.fps},scale={config.width}:{config.height}"
        cmd = [
            "/usr/bin/ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            url,
            "-vf",
            vf,
            "-frames:v",
            str(config.frames_per_episode),
            "-q:v",
            str(config.jpeg_q),
            "-f",
            "image2",
            out_pattern,
            "-y",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=ffmpeg_timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"ffmpeg timed out after {ffmpeg_timeout}s capturing from {url} "
                "— stream unreachable?"
            )

        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", errors="replace")
            if len(stderr) > 800:
                stderr = stderr[:800] + "… (truncated)"
            raise RuntimeError(
                f"ffmpeg exited {result.returncode} capturing from {url}:\n{stderr}"
            )

        jpgs = sorted(Path(tmpdir).glob("frame_*.jpg"))
        if not jpgs:
            raise RuntimeError(
                f"ffmpeg produced no frames from {url} — check the stream is "
                "live / reachable over the VPN."
            )

        episode = Episode.create(config=config, source_url=url)
        for i, jpg in enumerate(jpgs[: config.frames_per_episode]):
            episode.frames.append(
                Frame(
                    eye=eye,
                    res=config.frame_res,
                    t_offset_ms=round(i / config.fps * 1000),
                    jpeg_q=config.jpeg_q,
                    data=jpg.read_bytes(),
                )
            )
        return episode


# --------------------------------------------------------------------------- #
# Episode sources                                                              #
# --------------------------------------------------------------------------- #


class EpisodeSource(ABC):
    """Anything that yields Episodes: live capture, disk replay, fixtures, …"""

    @abstractmethod
    def episodes(self) -> Iterator[Episode]:
        """Yield Episodes. May be finite (disk) or infinite (live capture)."""
        ...


class WebcamEpisodeSource(EpisodeSource):
    """Live source: each iteration captures a fresh episode from the webcam."""

    def __init__(
        self,
        config: EpisodeConfig,
        url: Optional[str] = None,
        eye: str = "left",
        ffmpeg_timeout: float = 30.0,
    ) -> None:
        self.config = config
        self.url = url if url is not None else default_rtsp_url()
        self.eye = eye
        self.ffmpeg_timeout = ffmpeg_timeout

    def capture_one(self) -> Episode:
        """Capture and return a single episode from the configured stream."""
        return capture_episode(
            self.url,
            self.config,
            eye=self.eye,
            ffmpeg_timeout=self.ffmpeg_timeout,
        )

    def episodes(self) -> Iterator[Episode]:
        """Infinite generator: capture episodes back-to-back, forever."""
        while True:
            yield self.capture_one()


class FolderEpisodeSource(EpisodeSource):
    """Replay source: yields saved episodes from a folder, oldest first.

    The "manually pick a saved episode and replay it" path — and the future
    regression-fixture mechanism (record once, replay against the model).
    """

    def __init__(self, folder: str) -> None:
        self.folder = folder

    def episodes(self) -> Iterator[Episode]:
        """Yield each saved episode under ``self.folder`` in id (time) order."""
        for path in list_episodes(self.folder):
            yield load_episode(path)
