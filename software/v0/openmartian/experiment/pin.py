"""Auto-capture of the pinning metadata (PART F): the apples-to-apples record.

PART F's rule is **auto-capture, don't hand-enter**. At Run start the orchestrator
calls :func:`capture_model_pin` and :func:`capture_run_environment` to scrape:

* Ollama HTTP API — version (``/api/version``) and per-model digest / quantization
  / parameter size / modelfile params (``/api/show``);
* Docker — the running image digest, when in a container;
* host — GPU (nvidia-smi), CPU, RAM, OS, CUDA driver;
* the pipeline git commit.

Both ride on every Run and serialize as the matrix's filter/group keys, so a cell
can be sliced by exact model+quant+hardware. Everything is best-effort: a missing
probe yields ``None``, never an exception, so a Run is never blocked on metadata.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Optional

from .models import ModelPin, RunEnvironment


def _run(cmd: list, timeout: float = 5.0) -> Optional[str]:
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if out.returncode == 0:
            return (out.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------- #
# ModelPin                                                                     #
# --------------------------------------------------------------------------- #


def capture_model_pin(
    provider: str,
    model_id: str,
    *,
    ollama_host: Optional[str] = None,
    temperature: float = 0.0,
    top_p: Optional[float] = None,
    seed: Optional[int] = None,
    max_tokens: int = 1024,
    api_date: Optional[str] = None,
    beta_headers: Optional[list] = None,
) -> ModelPin:
    """Build a ModelPin, auto-scraping Ollama model metadata for the local path."""
    pin = ModelPin(
        provider=provider,
        model_id=model_id,
        temperature=temperature,
        top_p=top_p,
        seed=seed,
        max_tokens=max_tokens,
        api_date=api_date,
        beta_headers=beta_headers,
    )
    if provider == "ollama":
        _scrape_ollama_model(pin, ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    return pin


def _scrape_ollama_model(pin: ModelPin, host: str) -> None:
    """Fill digest / quantization / parameter_size / modelfile params from /api/show.

    Field names per current Ollama docs (confirmed at build time, PART F open Q4):
    ``/api/show`` returns ``details.quantization_level``, ``details.parameter_size``,
    a top-level ``modelfile`` plus a ``parameters`` string, and the blob digest is
    exposed on ``/api/tags`` per model (``digest``).
    """
    import requests

    try:
        show = requests.post(f"{host}/api/show", json={"name": pin.model_id}, timeout=5)
        if show.status_code == 200:
            data = show.json()
            details = data.get("details", {}) or {}
            pin.quantization = details.get("quantization_level")
            pin.parameter_size = details.get("parameter_size")
            pin.modelfile_params = {
                "template": (data.get("template") or "")[:2000],
                "parameters": data.get("parameters"),
            }
    except Exception:
        pass
    try:
        tags = requests.get(f"{host}/api/tags", timeout=5)
        if tags.status_code == 200:
            for m in tags.json().get("models", []):
                if m.get("name") == pin.model_id or m.get("model") == pin.model_id:
                    pin.model_digest = m.get("digest")
                    break
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# RunEnvironment                                                               #
# --------------------------------------------------------------------------- #


def _git_commit() -> str:
    out = _run(["git", "rev-parse", "HEAD"])
    return out or ""


def _ollama_version(host: str) -> Optional[str]:
    import requests

    try:
        r = requests.get(f"{host}/api/version", timeout=3)
        if r.status_code == 200:
            return r.json().get("version")
    except Exception:
        return None
    return None


def _gpu() -> tuple:
    """Return (gpu_model, vram_gb, cuda_driver) via nvidia-smi, when present."""
    if not shutil.which("nvidia-smi"):
        return None, None, None
    out = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return None, None, None
    first = out.splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    name = parts[0] if parts else None
    vram = None
    driver = None
    if len(parts) >= 2:
        try:
            vram = round(float(parts[1]) / 1024.0, 1)  # MiB -> GiB
        except ValueError:
            pass
    if len(parts) >= 3:
        driver = parts[2]
    return name, vram, driver


def _ram_gb() -> Optional[float]:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round(pages * page_size / (1024**3), 1)
    except (ValueError, OSError, AttributeError):
        return None


def _docker_image_digest() -> Optional[str]:
    """Best-effort: the image digest of the running container (PART E.3)."""
    digest = os.environ.get("OM_DOCKER_IMAGE_DIGEST")
    if digest:
        return digest
    # /proc/self/cgroup or /.dockerenv presence hints at a container; the digest
    # isn't introspectable from inside without the env var, so we surface that.
    if os.path.exists("/.dockerenv"):
        return "containerized (set OM_DOCKER_IMAGE_DIGEST to pin)"
    return None


def capture_run_environment(
    provider: str,
    *,
    endpoint: Optional[str] = None,
    batch: bool = False,
    inference_geo: Optional[str] = None,
) -> RunEnvironment:
    """Scrape the full RunEnvironment at Run start (best-effort, never raises)."""
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    gpu_model, vram, cuda = _gpu()
    env = RunEnvironment(
        ollama_version=_ollama_version(host) if provider == "ollama" else None,
        backend_version=None,  # llama.cpp build not separately exposed by Ollama HTTP
        docker_image_digest=_docker_image_digest(),
        pipeline_git_commit=_git_commit(),
        gpu_model=gpu_model,
        gpu_vram_gb=vram,
        cpu=platform.processor() or platform.machine(),
        ram_gb=_ram_gb(),
        os=f"{platform.system()} {platform.release()}",
        cuda_driver=cuda,
        endpoint=endpoint or (host if provider == "ollama" else "https://api.anthropic.com"),
        inference_geo=inference_geo,
        batch=batch,
        captured_at=datetime.now(timezone.utc).isoformat(),
    )
    return env
