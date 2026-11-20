import pytest

from app.coding import b64d, b64e
from app.normalize import ParseResult, enrich, sanitize_filename


def test_sanitize_basic():
    assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_strip_dots_and_spaces():
    assert sanitize_filename("  ..hello world..  ") == "hello world"


def test_sanitize_truncate():
    out = sanitize_filename("x" * 200)
    assert len(out) == 80


def test_sanitize_fallback():
    assert sanitize_filename("", fallback="media") == "media"
    assert sanitize_filename("///") == "media"


def test_b64_roundtrip():
    for s in ("https://a.b/c?d=e&f=g", "中文文件名_01.jpg", "x" * 500):
        assert b64d(b64e(s)) == s


def test_enrich_single_stream():
    r = ParseResult(platform="xhs", items=[])
    from app.normalize import MediaItem
    r.items = [MediaItem(kind="image", url="https://sns-img-bd.xhscdn.com/abc",
                         ext="jpg", filename="a_01.jpg")]
    enrich(r)
    item = r.items[0]
    assert item.download.startswith("/api/download?u=")
    assert "kind=image" in item.download
    assert item.preview.startswith("/api/image?u=")


def test_enrich_mux():
    from app.normalize import MediaItem
    r = ParseResult(platform="youtube", items=[])
    r.items = [MediaItem(kind="video", ext="mp4", filename="y.mp4",
                         mux_v="https://a.googlevideo.com/v", mux_a="https://b.googlevideo.com/a")]
    enrich(r)
    item = r.items[0]
    assert item.download.startswith("/api/download/mux?v=")
    assert "a=" in item.download
    # 原始直链不回传（IP 绑定服务器）
    assert item.mux_v == "" and item.mux_a == "" and item.url == ""
    assert item.preview is None


def test_enrich_youtube_prefers_yt_url():
    """YouTube 走 yt_url（下载时重新解析），不再固化可能过期的 googlevideo 直链。"""
    from app.normalize import MediaItem
    r = ParseResult(platform="youtube", items=[])
    r.items = [MediaItem(kind="video", ext="mp4", filename="y.mp4",
                         yt_url="https://youtu.be/abc123")]
    enrich(r)
    item = r.items[0]
    assert item.download.startswith("/api/download/mux?yt=")
    assert "v=" not in item.download
    assert item.yt_url == "" and item.url == ""
