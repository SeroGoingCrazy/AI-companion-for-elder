"""Fall-detection source control for the family dashboard.

The dashboard switches fall-mcp between the computer's camera and the demo video. The browser
talks to this same-origin API; it forwards to fall-mcp's /control over HTTP (spec 5.3: the two
services never import each other).
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/fall", tags=["fall"])

DEFAULT_DEMO_SOURCE = "demo/videos/walk.mp4"
START_TIMEOUT_S = 30  # opening a camera the first time can wait on the OS permission prompt


class SourceIn(BaseModel):
    mode: Literal["camera", "demo", "stop"]


def _control_url(request: Request) -> str:
    mcp = request.app.state.settings.fall.mcp
    host = "127.0.0.1" if mcp.host in ("0.0.0.0", "") else mcp.host
    return f"http://{host}:{mcp.port}/control"


async def _forward(method: str, url: str, **kwargs: Any) -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=kwargs.pop("timeout", 10)) as client:
            r = await client.request(method, url, **kwargs)
    except httpx.HTTPError:
        return JSONResponse({"error": "Fall detection is offline."}, status_code=503)
    return JSONResponse(r.json(), status_code=r.status_code)


@router.get("/status")
async def fall_status(request: Request) -> JSONResponse:
    return await _forward("GET", f"{_control_url(request)}/status")


@router.post("/source")
async def set_source(body: SourceIn, request: Request) -> JSONResponse:
    """camera = the computer's camera (fall.camera index); demo = the looped demo clip."""
    base = _control_url(request)
    if body.mode == "stop":
        return await _forward("POST", f"{base}/stop")
    fall = request.app.state.settings.fall
    source = str(fall.camera) if body.mode == "camera" else (
        fall.mcp.autostart_source or DEFAULT_DEMO_SOURCE)
    return await _forward("POST", f"{base}/start", json={"source": source, "loop": True},
                          timeout=START_TIMEOUT_S)
