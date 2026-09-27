"""Render the Sunny app icons (PWA manifest + iOS home screen).

Run once when the mark or palette changes; the PNGs are committed.
Needs Pillow, which is not a project dependency:

    uv run --with pillow python scripts/make_icons.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

SKY = (232, 240, 247, 255)  # --sky
INK = (19, 35, 63, 255)  # --ink
SUN = (244, 165, 28, 255)  # --sun

ICONS_DIR = Path(__file__).resolve().parent.parent / "src/elder_companion/web/static/icons"
SS = 8  # supersampling factor, for smooth edges


def draw_sun(
    size: int, *, bleed: bool, background: tuple[int, int, int, int] | None
) -> Image.Image:
    """The Sunny mark: an ink-outlined amber disc with eight rays.

    `bleed` fills the whole square (maskable icons are cropped to a circle by the
    launcher, so the mark has to sit inside the safe zone and the padding must be
    opaque). Otherwise the mark is drawn with transparent corners.
    """
    n = size * SS
    img = Image.new("RGBA", (n, n), background or (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    c = n / 2
    # Size the mark by its full extent so the rays never touch the edge. Maskable icons are
    # cropped to a circle by the launcher, so their mark stays inside the inner 80% safe zone.
    extent = n * (0.38 if bleed else 0.46)
    disc_r = extent / 2.15  # extent = ray_out (2.0r) + half a round cap (0.15r)
    ray_in = disc_r * 1.42
    ray_out = disc_r * 2.0
    ray_w = disc_r * 0.30
    outline = max(1, int(disc_r * 0.14))

    for i in range(8):
        a = math.radians(i * 45)
        dx, dy = math.cos(a), math.sin(a)
        d.line(
            [(c + dx * ray_in, c + dy * ray_in), (c + dx * ray_out, c + dy * ray_out)],
            fill=SUN,
            width=int(ray_w),
        )
        # round the ray caps
        for t in (ray_in, ray_out):
            d.ellipse(
                [
                    c + dx * t - ray_w / 2,
                    c + dy * t - ray_w / 2,
                    c + dx * t + ray_w / 2,
                    c + dy * t + ray_w / 2,
                ],
                fill=SUN,
            )

    d.ellipse(
        [c - disc_r, c - disc_r, c + disc_r, c + disc_r], fill=SUN, outline=INK, width=outline
    )
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    written = []

    for size in (192, 512):
        draw_sun(size, bleed=False, background=None).save(ICONS_DIR / f"icon-{size}.png")
        written.append(f"icon-{size}.png")
        draw_sun(size, bleed=True, background=SKY).save(ICONS_DIR / f"maskable-{size}.png")
        written.append(f"maskable-{size}.png")

    # iOS ignores the manifest icons and squares off whatever it gets, so this one is opaque.
    draw_sun(180, bleed=False, background=SKY).save(ICONS_DIR / "apple-touch-icon.png")
    written.append("apple-touch-icon.png")

    print(f"wrote {len(written)} icons to {ICONS_DIR}:")
    for name in written:
        print(f"  {name}")


if __name__ == "__main__":
    main()
