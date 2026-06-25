"""Webcam control: status, connect/disconnect/reconnect, snapshot, live feed."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from .. import camera, state

router = APIRouter(prefix="/api/webcam", tags=["webcam"])


class WebcamConnect(BaseModel):
    url: Optional[str] = None


@router.get("")
def webcam_status() -> dict:
    return state.WEBCAM.snapshot()


@router.post("/connect")
def webcam_connect(body: WebcamConnect) -> dict:
    if body.url:
        state.WEBCAM.set_url(body.url.strip())
    state.WEBCAM.desired_connected = True
    ok, detail = camera.probe(state.WEBCAM.url)
    state.WEBCAM.set_probe(ok, detail)
    return state.WEBCAM.snapshot()


@router.post("/disconnect")
def webcam_disconnect() -> dict:
    state.WEBCAM.desired_connected = False
    state.WEBCAM.set_probe(False, "disconnected")
    return state.WEBCAM.snapshot()


@router.post("/reconnect")
def webcam_reconnect() -> dict:
    state.WEBCAM.desired_connected = True
    ok, detail = camera.probe(state.WEBCAM.url)
    state.WEBCAM.set_probe(ok, detail)
    return state.WEBCAM.snapshot()


@router.get("/snapshot")
def webcam_snapshot() -> Response:
    try:
        data = camera.snapshot(state.WEBCAM.url)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return Response(content=data, media_type="image/jpeg")


@router.get("/stream")
def webcam_stream() -> StreamingResponse:
    return StreamingResponse(
        camera.mjpeg_stream(state.WEBCAM.url),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )
