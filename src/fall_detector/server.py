"""fall-mcp service: one Starlette app with /mcp (Streamable HTTP), /stream (MJPEG), /healthz,
and a tiny /control API the demo uses to switch clips.

    uv run python -m fall_detector.server                  # HTTP on 127.0.0.1:8001, autostart source
    uv run python -m fall_detector.server --source 0       # camera
    uv run python -m fall_detector.server --stdio          # MCP over stdio (Claude Desktop), no MJPEG
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route

from fall_detector.config import FallConfig, load_config
from fall_detector.events import EventStore
from fall_detector.mcp_tools import build_mcp
from fall_detector.monitor import MonitorController
from fall_detector.reporter import Reporter
from fall_detector.sources import SourceError
from fall_detector.stream import MEDIA_TYPE, mjpeg_generator

log = logging.getLogger("fall_detector")


def build_controller(cfg: FallConfig, report: bool = True) -> MonitorController:
    store = EventStore(cfg.mcp.events_path, cfg.mcp.snapshots_dir)
    return MonitorController(cfg, store, Reporter(cfg.report_url) if report else None)


def create_app(cfg: FallConfig, controller: MonitorController, autostart: str | None = None) -> Starlette:
    mcp = build_mcp(controller, controller.store)
    mcp_app = mcp.streamable_http_app(host=cfg.mcp.host)

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "monitor": controller.status().to_dict()})

    async def stream(request: Request) -> StreamingResponse:
        return StreamingResponse(mjpeg_generator(controller), media_type=MEDIA_TYPE,
                                 headers={"Cache-Control": "no-store"})

    async def control_status(request: Request) -> JSONResponse:
        return JSONResponse(controller.status().to_dict())

    async def control_start(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            s = controller.start(str(body.get("source", "")), loop=bool(body.get("loop", True)))
        except SourceError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(s.to_dict())

    async def control_stop(request: Request) -> JSONResponse:
        return JSONResponse(controller.stop().to_dict())

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            if autostart:
                try:
                    controller.start(autostart, loop=True)
                except SourceError as e:
                    log.error("autostart failed: %s (waiting for start_monitoring)", e)
            try:
                yield
            finally:
                controller.close()

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/stream", stream),
            Route("/control/status", control_status),
            Route("/control/start", control_start, methods=["POST"]),
            Route("/control/stop", control_stop, methods=["POST"]),
            Mount("/", app=mcp_app),  # serves /mcp; last so the routes above win
        ],
        lifespan=lifespan,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fall_detector.server", description="fall-mcp service")
    ap.add_argument("--stdio", action="store_true", help="MCP over stdio only (Claude Desktop)")
    ap.add_argument("--source", help="override autostart source (video path or camera index)")
    ap.add_argument("--no-autostart", action="store_true", help="wait for start_monitoring")
    ap.add_argument("--host", help="override fall.mcp.host")
    ap.add_argument("--port", type=int, help="override fall.mcp.port")
    args = ap.parse_args(argv)

    # stdout belongs to the MCP protocol in stdio mode: log to stderr only
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    controller = build_controller(cfg)

    if args.stdio:
        # read-mostly: the HTTP service owns live monitoring; tools still work on the shared store
        mcp = build_mcp(controller, controller.store)
        try:
            mcp.run("stdio")
        finally:
            controller.close()
        return 0

    import uvicorn

    autostart = None if args.no_autostart else (args.source or cfg.mcp.autostart_source)
    app = create_app(cfg, controller, autostart=autostart)
    uvicorn.run(app, host=args.host or cfg.mcp.host, port=args.port or cfg.mcp.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
