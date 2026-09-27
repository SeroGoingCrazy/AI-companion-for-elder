"""Serve fall-mcp's MJPEG view from the main app's own origin.

The dashboard used to point the <img> straight at fall-mcp on :8001. That works on the
machine running both, and nowhere else:

  - a phone on venue wifi cannot reach the laptop's :8001 at all;
  - behind the HTTPS tunnel the demo needs, an http://host:8001 image is mixed content
    and the browser blocks it before a request is even made.

Proxying it through :8000 means one origin, one tunnel, one thing to start. The stream is
an endless multipart/x-mixed-replace body, so it is relayed chunk by chunk rather than
buffered, and fall-mcp being down is an ordinary 503 the page already knows how to retry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(include_in_schema=False)

CONNECT_TIMEOUT_S = 3.0
CHUNK = 8192


@router.get("/fall/stream")
async def fall_stream(request: Request) -> StreamingResponse:
    mcp = request.app.state.settings.fall.mcp
    upstream = f"http://{mcp.host}:{mcp.port}/stream"

    # No read timeout: an idle-but-alive MJPEG stream is normal between frames.
    client = httpx.AsyncClient(timeout=httpx.Timeout(CONNECT_TIMEOUT_S, read=None))
    try:
        req = client.build_request("GET", upstream)
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        await client.aclose()
        raise HTTPException(status_code=503, detail=f"fall-mcp is not reachable: {e}") from e

    if resp.status_code != 200:
        await resp.aclose()
        await client.aclose()
        raise HTTPException(status_code=503, detail=f"fall-mcp returned {resp.status_code}")

    async def relay() -> AsyncIterator[bytes]:
        try:
            async for chunk in resp.aiter_bytes(CHUNK):
                yield chunk
        finally:
            # The viewer closing the tab must not leak the upstream connection.
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(),
        status_code=200,
        media_type=resp.headers.get("content-type", "multipart/x-mixed-replace"),
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
