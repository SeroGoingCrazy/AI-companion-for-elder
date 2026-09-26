"""Control a running fall-mcp service from the terminal (demo helper).

    uv run python -m fall_detector.ctl play demo/videos/fall_01.mp4   # play once, then hold
    uv run python -m fall_detector.ctl play demo/videos/walk.mp4 --loop
    uv run python -m fall_detector.ctl play 0                         # camera
    uv run python -m fall_detector.ctl stop
    uv run python -m fall_detector.ctl status
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from fall_detector.config import load_config


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fall_detector.ctl", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    play = sub.add_parser("play", help="start monitoring a video or camera")
    play.add_argument("source")
    play.add_argument("--loop", action="store_true")
    sub.add_parser("stop")
    sub.add_parser("status")
    args = ap.parse_args(argv)

    cfg = load_config()
    host = "127.0.0.1" if cfg.mcp.host in ("0.0.0.0", "") else cfg.mcp.host
    base = f"http://{host}:{cfg.mcp.port}/control"
    try:
        if args.cmd == "play":
            r = httpx.post(f"{base}/start", json={"source": args.source, "loop": args.loop}, timeout=30)
        elif args.cmd == "stop":
            r = httpx.post(f"{base}/stop", timeout=10)
        else:
            r = httpx.get(f"{base}/status", timeout=10)
    except httpx.HTTPError as e:
        print(f"fall-mcp is not reachable at {base}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(r.json(), indent=2))
    return 0 if r.status_code < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
