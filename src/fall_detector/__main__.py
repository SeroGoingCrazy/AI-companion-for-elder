"""Debug CLI: run detection on a video or camera and print state changes and fall events.

    uv run python -m fall_detector --source demo/videos/fall_01.mp4 --show
    uv run python -m fall_detector --source demo/videos/lie_down.mp4 --dry-run
    uv run python -m fall_detector --source 0 --show          # camera

Nothing is stored or reported here; the service is `python -m fall_detector.server`.
"""

from __future__ import annotations

import argparse
import sys
import time

from fall_detector.config import load_config
from fall_detector.pose import PoseEstimator, banner, draw
from fall_detector.rules import FallDetector
from fall_detector.sources import SourceError, open_source


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fall_detector", description=__doc__.splitlines()[0])
    ap.add_argument("--source", required=True, help="video path or camera index (0)")
    ap.add_argument("--show", action="store_true", help="show a window with skeletons (q quits)")
    ap.add_argument("--dry-run", action="store_true", help="no window; just print (the default)")
    ap.add_argument("--loop", action="store_true", help="loop a video file")
    ap.add_argument("--verbose", "-v", action="store_true", help="print features every 10 frames")
    args = ap.parse_args(argv)

    cfg = load_config()
    try:
        src = open_source(args.source, loop=args.loop, realtime=args.show)
    except SourceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    est = PoseEstimator(cfg.model_path, device=cfg.device)
    det = FallDetector(cfg.rules)
    show = args.show and not args.dry_run
    if show:
        import cv2

    events, prev, n, t0, loops = [], {}, 0, time.perf_counter(), 0
    try:
        for frame, ts in src.frames():
            if src.loops != loops:
                loops = src.loops
                est.reset()
                det.reset()
            people = est.track(frame)
            for ev in det.update(people, ts):
                events.append(ev)
                print(f"[{ts:6.2f}s] FALL EVENT track #{ev.track_id} (fell at {ev.fall_ts:.2f}s, "
                      f"conf {ev.confidence:.2f})")
            states = det.states
            for tid, st in states.items():
                if prev.get(tid) != st:
                    print(f"[{ts:6.2f}s] #{tid} {prev.get(tid, '-')} -> {st}")
            prev = states
            if args.verbose and n % 3 == 0:
                from fall_detector.rules import compute_features

                for p in people:
                    f = compute_features(p, ts, cfg.rules.min_keypoint_conf)
                    ang = f"{f.torso_angle:5.1f}" if f.torso_angle is not None else "  -  "
                    print(f"  {ts:6.2f}s #{p.track_id} ar={f.aspect_ratio:.2f} torso={ang} "
                          f"hip_y={f.hip_y:6.1f} h={f.height:6.1f} score={f.confidence:.2f}")
            n += 1
            if show:
                draw(frame, people, states, cfg.rules.min_keypoint_conf)
                fps = n / (time.perf_counter() - t0)
                banner(frame, f"{args.source}  {fps:4.1f} FPS  {len(people)} person(s)",
                       alarm="DOWN" in states.values())
                cv2.imshow("fall_detector", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        src.close()
        if show:
            cv2.destroyAllWindows()
    elapsed = time.perf_counter() - t0
    print(f"{n} frames in {elapsed:.1f}s ({n / max(elapsed, 1e-6):.1f} FPS), {len(events)} fall event(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
