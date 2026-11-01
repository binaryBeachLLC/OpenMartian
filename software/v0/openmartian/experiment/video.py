"""Raw-video episode derivation (PART C "Derivation"; replaces the webcam input).

v0 captured episodes live from an RTSP webcam. The experiment app's input is now
**uploaded raw video files**: we *record high and sample down* a fixed file with
ffmpeg into the exact same :class:`~openmartian.schema.Episode` objects the rest
of the pipeline consumes. This is the load-bearing seam (PART B) — swap the
*source* (webcam -> video file), not the pipeline.

``VideoEpisodeSource`` subclasses the pipeline's :class:`EpisodeSource`, so the
benchmark harness and the app iterate it exactly like ``WebcamEpisodeSource``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Iterator, Optional

from ..capture import EpisodeSource
from ..schema import Episode, Frame, save_episode
from .models import SamplingSpec

FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"


def probe_duration_ms(video_path: str, timeout: float = 30.0) -> int:
    """Return the video duration in milliseconds via ffprobe (0 if unknown)."""
    cmd = [
        FFPROBE, "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(video_path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0
    if out.returncode != 0:
        return 0
    try:
        dur = json.loads(out.stdout.decode("utf-8")).get("format", {}).get("duration")
        return int(float(dur) * 1000) if dur else 0
    except Exception:
        return 0


def _extract_window(
    video_path: str,
    start_ms: int,
    spec: SamplingSpec,
    eye: str = "left",
    ffmpeg_timeout: float = 60.0,
) -> Optional[Episode]:
    """Extract one episode-window of frames starting at ``start_ms`` from a video.

    Mirrors :func:`openmartian.capture.capture_episode` but seeks into a file
    instead of pulling from RTSP. Returns ``None`` if the window yields no frames
    (e.g. the seek landed past the end of the video).
    """
    cfg = spec.to_episode_config()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out_pattern = str(Path(tmp) / "frame_%03d.jpg")
        vf = f"fps={cfg.fps},scale={cfg.width}:{cfg.height}"
        cmd = [
            FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_ms / 1000:.3f}",  # input-side seek = fast + accurate enough
            "-i", str(video_path),
            "-vf", vf,
            "-frames:v", str(cfg.frames_per_episode),
            "-q:v", str(cfg.jpeg_q),
            "-f", "image2", out_pattern, "-y",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=ffmpeg_timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"ffmpeg timed out after {ffmpeg_timeout}s sampling {video_path}"
            )
        if res.returncode != 0:
            stderr = (res.stderr or b"").decode("utf-8", "replace")[:800]
            raise RuntimeError(f"ffmpeg exited {res.returncode} on {video_path}:\n{stderr}")

        jpgs = sorted(Path(tmp).glob("frame_*.jpg"))
        if not jpgs:
            return None

        from datetime import datetime, timezone

        episode = Episode.create(
            config=cfg,
            source_url=f"file://{Path(video_path).resolve()}#t={start_ms}ms",
            started=datetime.now(timezone.utc),
        )
        for i, jpg in enumerate(jpgs[: cfg.frames_per_episode]):
            episode.frames.append(
                Frame(
                    eye=eye,
                    res=cfg.frame_res,
                    t_offset_ms=round(i / cfg.fps * 1000),
                    jpeg_q=cfg.jpeg_q,
                    data=jpg.read_bytes(),
                )
            )
        # Stamp the source-video time on the episode so labels attach by overlap.
        if episode.config is not None:
            pass
        episode.frames and setattr(episode, "source_t_start_ms", start_ms)
        return episode


class VideoEpisodeSource(EpisodeSource):
    """Replay source over a raw video file: yields sampled-down Episodes.

    Walks the video from ``start_ms`` to ``end_ms`` in ``spec.effective_stride_s``
    steps, extracting one episode per window. Each episode records its
    ``source_t_start_ms`` so the derivation can attach Dataset labels by time
    -overlap (label once on the source video, inherit across every sweep).
    """

    def __init__(
        self,
        video_path: str,
        spec: SamplingSpec,
        start_ms: int = 0,
        end_ms: Optional[int] = None,
        eye: str = "left",
        max_episodes: Optional[int] = None,
        ffmpeg_timeout: float = 60.0,
    ) -> None:
        self.video_path = video_path
        self.spec = spec
        self.start_ms = start_ms
        self.end_ms = end_ms if end_ms is not None else probe_duration_ms(video_path)
        self.eye = eye
        self.max_episodes = max_episodes
        self.ffmpeg_timeout = ffmpeg_timeout

    def window_starts(self) -> list:
        """The list of source-video start offsets (ms) this source will sample."""
        stride_ms = max(1, int(self.spec.effective_stride_s * 1000))
        window_ms = int(self.spec.window_s * 1000)
        end = self.end_ms or (self.start_ms + window_ms)
        starts = []
        t = self.start_ms
        # Stop once a full window would run past the end (avoid short tail episodes),
        # but always allow at least one window for very short clips.
        while t + window_ms <= end or not starts:
            starts.append(t)
            t += stride_ms
            if self.max_episodes and len(starts) >= self.max_episodes:
                break
            if t >= end:
                break
        return starts

    def episodes(self) -> Iterator[Episode]:
        for start_ms in self.window_starts():
            ep = _extract_window(
                self.video_path, start_ms, self.spec, eye=self.eye,
                ffmpeg_timeout=self.ffmpeg_timeout,
            )
            if ep is not None:
                yield ep


def derive_to_disk(
    video_path: str,
    spec: SamplingSpec,
    out_dir: str,
    start_ms: int = 0,
    end_ms: Optional[int] = None,
    max_episodes: Optional[int] = None,
) -> list:
    """Sample a video into episodes on disk; return the list of saved episode ids.

    Reuses :func:`openmartian.schema.save_episode` so the on-disk layout is
    identical to webcam-captured episodes — the dev console's episode browser and
    the CLI ``process`` path read them unchanged.
    """
    src = VideoEpisodeSource(
        video_path, spec, start_ms=start_ms, end_ms=end_ms, max_episodes=max_episodes
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    for start_ms_i, episode in zip(src.window_starts(), src.episodes()):
        ep_dir = save_episode(episode, out)
        # Persist the source-video time into meta.json so label-overlap survives reloads.
        meta_path = ep_dir / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["source_video"] = Path(video_path).name
        meta["source_t_start_ms"] = start_ms_i
        meta["source_window_ms"] = int(spec.window_s * 1000)
        meta_path.write_text(json.dumps(meta, indent=2))
        saved.append(episode.episode_id)
    return saved
