"""PlaylistSource: the demo plays every scenario clip in turn."""

from pathlib import Path

import pytest

from fall_detector.sources import PlaylistSource, SourceError, open_source, parse_playlist

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

pytestmark = pytest.mark.unit


def _clip(path: Path, frames: int, shade: int) -> None:
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
    for _ in range(frames):
        w.write(np.full((48, 64, 3), shade, dtype=np.uint8))
    w.release()


@pytest.fixture
def playlist(tmp_path: Path) -> Path:
    _clip(tmp_path / "walk.mp4", 3, 40)
    _clip(tmp_path / "fall.mp4", 2, 200)
    m3u = tmp_path / "demo.m3u"
    m3u.write_text("#EXTM3U\n# comment\n#EXTINF:-1,Walking\nwalk.mp4\n\nfall.mp4\n")
    return m3u


def test_parse_labels_and_relative_paths(playlist: Path) -> None:
    assert parse_playlist(playlist) == [
        (playlist.parent / "walk.mp4", "Walking"),
        (playlist.parent / "fall.mp4", None),
    ]


def test_plays_clips_in_order_and_bumps_loops_per_clip(playlist: Path) -> None:
    src = open_source(str(playlist))
    assert isinstance(src, PlaylistSource)
    seen = [(src.clip, src.loops, round(ts, 1)) for _, ts in src.frames()]
    assert seen == [
        ("1/2 Walking", 0, 0.0), ("1/2 Walking", 0, 0.1), ("1/2 Walking", 0, 0.2),
        ("2/2 fall", 1, 0.0), ("2/2 fall", 1, 0.1),
    ]


def test_loop_starts_over(playlist: Path) -> None:
    src = PlaylistSource(str(playlist), loop=True)
    frames = src.frames()
    clips = [(next(frames), src.clip)[1] for _ in range(6)]
    src.close()
    assert clips[5] == "1/2 Walking" and src.loops == 2


def test_missing_clip_is_a_source_error(playlist: Path) -> None:
    (playlist.parent / "fall.mp4").unlink()
    with pytest.raises(SourceError, match="fall.mp4.*fetch_demo_media"):
        PlaylistSource(str(playlist))


def test_empty_playlist_is_a_source_error(tmp_path: Path) -> None:
    (tmp_path / "empty.m3u").write_text("#EXTM3U\n")
    with pytest.raises(SourceError, match="empty"):
        open_source(str(tmp_path / "empty.m3u"))
