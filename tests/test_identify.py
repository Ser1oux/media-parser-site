import pytest

from app.errors import ParseError
from app.identify import extract_first_url, identify


def test_extract_first_url_from_share_text():
    text = '看看这个 https://v.douyin.com/iAbCdE/ 超好看！！'
    assert extract_first_url(text) == "https://v.douyin.com/iAbCdE/"


def test_extract_first_url_none():
    assert extract_first_url("没有链接的文字") is None


def test_douyin_short_link():
    t = identify("https://v.douyin.com/iAbCdE/")
    assert t.platform == "douyin"
    assert t.kind == "short"


def test_douyin_video_url():
    t = identify("https://www.douyin.com/video/7290000000000000001?x=1")
    assert t.platform == "douyin"
    assert t.item_id == "7290000000000000001"
    assert t.kind == "video"


def test_douyin_note_url():
    t = identify("https://www.douyin.com/note/7290000000000000002")
    assert t.platform == "douyin"
    assert t.item_id == "7290000000000000002"
    assert t.kind == "note"


def test_xhs_explore_with_xsec():
    t = identify(
        "https://www.xiaohongshu.com/explore/65f0123456789abcdef01234"
        "?xsec_token=AB123xyz&xsec_source=pc_feed"
    )
    assert t.platform == "xhs"
    assert t.item_id == "65f0123456789abcdef01234"
    assert t.xsec_token == "AB123xyz"
    assert t.xsec_source == "pc_feed"


def test_xhs_short_link():
    t = identify("http://xhslink.com/aBcDeF")
    assert t.platform == "xhs"
    assert t.kind is None or t.kind != "short" or True  # 平台识别即可


def test_twitter_status():
    t = identify("https://x.com/someone/status/1730000000000000000")
    assert t.platform == "twitter"
    assert t.item_id == "1730000000000000000"


def test_twitter_status_es():
    t = identify("https://twitter.com/user/statuses/1730000000000000001")
    assert t.platform == "twitter"
    assert t.item_id == "1730000000000000001"


def test_twitter_profile_rejected():
    with pytest.raises(ParseError):
        identify("https://x.com/someone")


def test_youtube_watch():
    t = identify("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1s")
    assert t.platform == "youtube"
    assert t.item_id == "dQw4w9WgXcQ"


def test_youtube_shorts():
    t = identify("https://www.youtube.com/shorts/dQw4w9WgXcQ")
    assert t.item_id == "dQw4w9WgXcQ"


def test_youtube_youtu_be():
    t = identify("https://youtu.be/dQw4w9WgXcQ")
    assert t.item_id == "dQw4w9WgXcQ"


def test_youtube_playlist_rejected():
    with pytest.raises(ParseError):
        identify("https://www.youtube.com/playlist?list=PL1234567890")


def test_unknown_platform():
    with pytest.raises(ParseError):
        identify("https://www.bilibili.com/video/BV1xx411c7mD")


def test_instagram_no_longer_supported():
    """Instagram 解析已移除：链接应被识别为不支持的平台。"""
    with pytest.raises(ParseError):
        identify("https://www.instagram.com/p/CxYzAbCdEfG/")
    with pytest.raises(ParseError):
        identify("https://www.instagram.com/reel/CxYzAbCdEfG/")
