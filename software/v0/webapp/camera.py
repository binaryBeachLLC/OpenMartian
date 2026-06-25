"""Webcam helpers: reachability probe, single snapshot, and live MJPEG stream.

All three shell out to ffmpeg against the RTSP URL. The RTSP server (mediamtx on
the Windows host) allows multiple concurrent readers, so the live stream and an
episode capture can run at the same time.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Iterator, Tuple

FFMPEG = "/usr/bin/ffmpeg"

# JPEG markers, for splitting a raw MJPEG byte stream into discrete frames.
_SOI = b"\xff\xd8"  # start of image
_EOI = b"\xff\xd9"  # end of image


def probe(url: str, timeout: float = 8.0) -> Tuple[bool, str]:
    """Grab a single frame to confirm the stream is live. Returns (ok, detail)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "probe.jpg"
        cmd = [
            FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-i", url,
            "-frames:v", "1", "-q:v", "5", "-f", "image2", str(out), "-y",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"timed out after {timeout:.0f}s — stream unreachable?"
        if res.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            err = res.stderr.decode("utf-8", "replace").strip()[:300]
            return False, err or "ffmpeg produced no frame"
        return True, "stream reachable"


def snapshot(url: str, res: str = "640x480", timeout: float = 8.0) -> bytes:
    """Return one JPEG frame from the stream (raises RuntimeError on failure)."""
    w, h = res.lower().split("x")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "snap.jpg"
        cmd = [
            FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-i", url,
            "-frames:v", "1", "-vf", f"scale={w}:{h}", "-q:v", "4",
            "-f", "image2", str(out), "-y",
        ]
        try:
            res_proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"snapshot timed out after {timeout:.0f}s")
        if res_proc.returncode != 0 or not out.exists():
            err = res_proc.stderr.decode("utf-8", "replace").strip()[:300]
            raise RuntimeError(err or "snapshot failed")
        return out.read_bytes()


def mjpeg_stream(url: str, fps: int = 10, res: str = "640x480") -> Iterator[bytes]:
    """Yield multipart/x-mixed-replace chunks for an <img> live view.

    Runs ffmpeg outputting concatenated MJPEG to stdout, splits on JPEG SOI/EOI,
    and wraps each frame in a multipart boundary. ffmpeg is terminated when the
    consumer (the HTTP client) goes away and the generator is closed.
    """
    w, h = res.lower().split("x")
    # Output a continuous MJPEG byte stream on stdout; we split it into discrete
    # JPEGs on the SOI/EOI markers and wrap each in a multipart boundary.
    # NOTE: do NOT use -fflags nobuffer / -flags low_delay here — on a jittery
    # RTSP-over-VPN link they starve the output and emit zero frames. Let ffmpeg
    # buffer normally so it rides out network jitter.
    cmd = [
        FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-rtsp_transport", "tcp", "-i", url,
        "-an", "-r", str(fps), "-vf", f"scale={w}:{h}", "-q:v", "7",
        "-f", "mjpeg", "-",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buf = b""
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            # Emit every complete JPEG currently in the buffer.
            while True:
                start = buf.find(_SOI)
                if start == -1:
                    break
                end = buf.find(_EOI, start + 2)
                if end == -1:
                    break
                frame = buf[start : end + 2]
                buf = buf[end + 2 :]
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                    + frame + b"\r\n"
                )
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
