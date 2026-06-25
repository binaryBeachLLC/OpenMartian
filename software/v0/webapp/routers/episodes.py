"""Episodes: list/detail, frame images, capture, edit (label/config), delete."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from openmartian.capture import capture_episode
from openmartian.schema import save_episode

from .. import state

router = APIRouter(prefix="/api/episodes", tags=["episodes"])


# -- meta helpers ----------------------------------------------------------- #

def _read_meta(ep_id: str) -> dict:
    meta_path = state.EPISODES_DIR / ep_id / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"episode {ep_id} not found")
    return json.loads(meta_path.read_text())


def _write_meta(ep_id: str, meta: dict) -> None:
    (state.EPISODES_DIR / ep_id / "meta.json").write_text(json.dumps(meta, indent=2))


def _summary(meta: dict) -> dict:
    return {
        "episode_id": meta["episode_id"],
        "started": meta.get("started"),
        "source_url": meta.get("source_url"),
        "label": meta.get("label", ""),
        "frame_count": len(meta.get("frames", [])),
        "config": meta.get("config"),
    }


def _detail(ep_id: str, meta: dict) -> dict:
    frames = [
        {
            "index": i, "file": fm["file"], "eye": fm["eye"], "res": fm["res"],
            "t_offset_ms": fm["t_offset_ms"], "jpeg_q": fm.get("jpeg_q", 5),
            "url": f"/api/episodes/{ep_id}/frames/{i}",
        }
        for i, fm in enumerate(meta.get("frames", []))
    ]
    d = _summary(meta)
    d["frames"] = frames
    return d


# -- routes ----------------------------------------------------------------- #

class CaptureRequest(BaseModel):
    url: Optional[str] = None
    config: Optional[dict] = None
    count: int = 1


class EpisodePatch(BaseModel):
    label: Optional[str] = None
    config: Optional[dict] = None


@router.get("")
def list_episodes() -> list[dict]:
    out = []
    if state.EPISODES_DIR.exists():
        for p in state.EPISODES_DIR.iterdir():
            meta_path = p / "meta.json"
            if p.is_dir() and meta_path.exists():
                out.append(_summary(json.loads(meta_path.read_text())))
    out.sort(key=lambda s: s["episode_id"], reverse=True)  # newest first
    return out


@router.post("/capture")
def capture(req: CaptureRequest) -> dict:
    cfg = state.get_config()
    if req.config:
        cfg = state.update_config({"capture": req.config})  # persist as new defaults
    url = (req.url or state.WEBCAM.url).strip()
    ep_config = state.episode_config_from(cfg)

    created = []
    state.WEBCAM.set_recording(True)
    try:
        for _ in range(max(1, req.count)):
            episode = capture_episode(url, ep_config)
            save_episode(episode, state.EPISODES_DIR)
            created.append(_summary(_read_meta(episode.episode_id)))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        state.WEBCAM.set_recording(False)
    return {"created": created}


@router.get("/{ep_id}")
def get_episode(ep_id: str) -> dict:
    return _detail(ep_id, _read_meta(ep_id))


@router.get("/{ep_id}/frames/{index}")
def get_frame(ep_id: str, index: int) -> FileResponse:
    meta = _read_meta(ep_id)
    frames = meta.get("frames", [])
    if index < 0 or index >= len(frames):
        raise HTTPException(status_code=404, detail="frame index out of range")
    path = state.EPISODES_DIR / ep_id / frames[index]["file"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="frame file missing")
    return FileResponse(path, media_type="image/jpeg")


@router.patch("/{ep_id}")
def patch_episode(ep_id: str, patch: EpisodePatch) -> dict:
    meta = _read_meta(ep_id)
    if patch.label is not None:
        meta["label"] = patch.label
    if patch.config is not None:
        meta["config"] = {**(meta.get("config") or {}), **patch.config}
    _write_meta(ep_id, meta)
    return _detail(ep_id, meta)


@router.delete("/{ep_id}")
def delete_episode(ep_id: str) -> dict:
    ep_dir = state.EPISODES_DIR / ep_id
    if not ep_dir.exists():
        raise HTTPException(status_code=404, detail=f"episode {ep_id} not found")
    for f in ep_dir.iterdir():
        f.unlink()
    ep_dir.rmdir()
    return {"deleted": ep_id}
