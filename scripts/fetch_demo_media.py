"""Download the pose model and build the demo videos (spec F2).

    uv run python scripts/fetch_demo_media.py          # skips files that already exist
    uv run python scripts/fetch_demo_media.py --force

Clips come from the UR Fall Detection Dataset (University of Rzeszow, CC BY-NC-SA 4.0,
https://fenix.ur.edu.pl/~mkepski/ds/uf.html). Each source video is depth | RGB side by side;
we keep the RGB half, upscale it 2x and hold the last frame for a few seconds so the
"on the floor for 3s" rule has time to fire. See demo/videos/README.md for expected results.
Requires the vision extra (opencv): uv sync --extra vision
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-pose.pt"
MODEL_PATH = ROOT / "models" / "yolo11n-pose.pt"
URFD = "https://fenix.ur.edu.pl/~mkepski/ds/data/{}-cam0.mp4"
VIDEOS = ROOT / "demo" / "videos"
HOLD_S = 4.0

# demo name -> URFD sequence
CLIPS = {
    "fall_01": "fall-03",   # walks toward the camera, falls sideways (main demo clip)
    "fall_02": "fall-01",   # sits on a chair, stands, falls near the camera
    "fall_03": "fall-05",   # walks in, falls at the bottom edge (hard: body partly out of frame)
    "lie_down": "adl-10",   # sits on the bed, then slowly lies down
    "walk": "adl-06",       # walks around, bends over, stands up
    "sit": "adl-07",        # sits down in an armchair
    "crouch": "adl-01",     # crouches to the floor
}


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "elder-companion-demo"})
    with urllib.request.urlopen(req, timeout=60) as r:
        dest.write_bytes(r.read())


def build_clip(src: Path, dest: Path) -> None:
    import cv2

    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    half = w // 2
    out_size = (half * 2, h * 2)
    writer = cv2.VideoWriter(str(dest), cv2.VideoWriter_fourcc(*"mp4v"), fps, out_size)
    last = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        last = cv2.resize(frame[:, half:], out_size, interpolation=cv2.INTER_CUBIC)
        writer.write(last)
    for _ in range(int(HOLD_S * fps)):
        if last is not None:
            writer.write(last)
    cap.release()
    writer.release()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="re-download and rebuild everything")
    args = ap.parse_args()

    if args.force or not MODEL_PATH.exists():
        print("Pose model")
        download(MODEL_URL, MODEL_PATH)

    VIDEOS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, seq in CLIPS.items():
            dest = VIDEOS / f"{name}.mp4"
            if dest.exists() and not args.force:
                continue
            print(f"{name}.mp4 <- {seq}")
            src = Path(tmp) / f"{seq}.mp4"
            download(URFD.format(seq), src)
            build_clip(src, dest)
    print("Done: models/ and demo/videos/ are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
