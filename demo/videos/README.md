# Demo videos

Video files are git-ignored. Build them (and download the pose model) with:

```
uv sync --extra vision
uv run python scripts/fetch_demo_media.py
```

Clips come from the [UR Fall Detection Dataset](https://fenix.ur.edu.pl/~mkepski/ds/uf.html)
(Bogdan Kwolek, Michal Kepski, University of Rzeszow; CC BY-NC-SA 4.0, non-commercial use).
Only the RGB half of each `*-cam0.mp4` is kept; it is upscaled to 640x480 and the last frame is
held for 4 s so the "down for 3 s" rule can fire.

| File | Source | Content | Expected |
|---|---|---|---|
| `fall_01.mp4` | fall-03 | walks toward the camera, falls sideways (**main demo clip**) | 1 fall alert |
| `fall_02.mp4` | fall-01 | sits on a chair, falls forward near the camera | 1 fall alert |
| `fall_03.mp4` | fall-05 | walks in, falls at the bottom edge of the frame | 1 fall (known miss: the body on the floor is mostly out of frame, pose confidence ~0.1) |
| `lie_down.mp4` | adl-10 | sits on the bed, then slowly lies down | no alert |
| `walk.mp4` | adl-06 | walks, bends over, stands up (autostart idle scene) | no alert |
| `sit.mp4` | adl-07 | sits down in an armchair | no alert |
| `crouch.mp4` | adl-01 | crouches down to the floor | no alert |

Check any clip: `uv run python -m fall_detector --source demo/videos/fall_01.mp4 --show`

On all 17 URFD clips we downloaded (fall-01..05, adl-01..12), yolo11n-pose + the rules in
`src/fall_detector/rules.py` detected 4/5 falls with 0/12 false positives (CPU, ~50 FPS).
