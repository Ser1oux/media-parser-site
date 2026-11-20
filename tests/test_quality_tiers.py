"""画质分档逻辑单测：YouTube 高度分档 + 抖音 bit_rate 分档。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parsers.douyin_extract import video_tiers  # noqa: E402
from app.parsers.youtube import _quality_tiers  # noqa: E402


def _yt_fmt(height, tbr, vcodec="vp09.00.50.08", acodec="none"):
    return {"height": height, "tbr": tbr, "vcodec": vcodec, "acodec": acodec, "url": "u"}


def test_youtube_tiers_sorted_desc():
    tiers = _quality_tiers([_yt_fmt(1080, 3000), _yt_fmt(2160, 8000), _yt_fmt(720, 1200)])
    assert [t["height"] for t in tiers] == [2160, 1080, 720]


def test_youtube_tiers_dedup_same_height_keeps_best_bitrate():
    tiers = _quality_tiers([
        _yt_fmt(1080, 2000, "vp09"),
        _yt_fmt(1080, 5000, "av01"),   # 同高度更高码率
        _yt_fmt(1080, 3000, "avc1"),
    ])
    assert len([t for t in tiers if t["height"] == 1080]) == 1
    assert tiers[0]["vcodec"] == "av01"


def test_youtube_tiers_ignore_audio_and_progressive():
    tiers = _quality_tiers([
        {"height": 0, "tbr": 128, "vcodec": "none", "acodec": "mp4a.40.2", "url": "a"},
        {"height": 1080, "tbr": 3000, "vcodec": "avc1", "acodec": "mp4a.40.2", "url": "p"},
        _yt_fmt(720, 1200),
    ])
    assert [t["height"] for t in tiers] == [720]


def test_youtube_tiers_max_four():
    tiers = _quality_tiers([_yt_fmt(h, h * 10) for h in (2160, 1440, 1080, 720, 480, 360)])
    assert 1 <= len(tiers) <= 4


def test_youtube_tiers_empty():
    assert _quality_tiers([]) == []


def _dy_tier(gear, height, bitrate):
    return {
        "gear_name": gear,
        "bit_rate": bitrate,
        "play_addr": {"url_list": [f"https://v.douyinvod.com/{gear}.mp4"]},
    }


def test_douyin_tiers_sorted_by_height():
    video = {"bit_rate": [
        _dy_tier("normal_720_0", 720, 2000),
        _dy_tier("normal_1080_0", 1080, 4000),
        _dy_tier("normal_540_0", 540, 1000),
    ]}
    tiers = video_tiers(video)
    assert [t[2] for t in tiers] == [1080, 720, 540]
    assert tiers[0][1] == "1080p"
    assert tiers[0][0].endswith("normal_1080_0.mp4")


def test_douyin_tiers_fallback_play_endpoint():
    video = {"play_addr": {"uri": "v0200fg10000abc"}}
    tiers = video_tiers(video)
    assert len(tiers) == 1
    assert "ratio=default" in tiers[0][0]
    assert tiers[0][1] == "原画"


def test_douyin_tiers_empty_returns_empty():
    assert video_tiers({}) == []
