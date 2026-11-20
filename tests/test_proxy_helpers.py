from app.proxy import b64d  # noqa: F401  (确认模块可导入)
from app.proxy import fix_ftyp, host_allowed, host_group, referer_for


def test_host_allowed():
    assert host_allowed("v5-dy-o-no.douyinvod.com")
    assert host_allowed("sns-video-bd.xhscdn.com")
    assert host_allowed("video.twimg.com")
    assert host_allowed("rr1---sn-a5mekn6k.googlevideo.com")
    assert host_allowed("www.iesdouyin.com")
    assert not host_allowed("evil.com")
    assert not host_allowed("localhost")
    assert not host_allowed("169.254.169.254")
    assert not host_allowed("")
    # 防后缀伪装：douyinvod.com.evil.com 不允许
    assert not host_allowed("douyinvod.com.evil.com")
    # Instagram 已移除解析，其 CDN 不在白名单
    assert not host_allowed("scontent.cdninstagram.com")
    assert not host_allowed("cdninstagram.com")


def test_referer_for():
    assert referer_for("v3-web.douyinvod.com") == "https://www.douyin.com/"
    assert referer_for("sns-video-bd.xhscdn.com") == "https://www.xiaohongshu.com/"
    assert referer_for("rr1---sn-x.googlevideo.com") == "https://www.youtube.com/"
    assert referer_for("video.twimg.com") is None


def test_host_group():
    assert host_group("sns-video-bd.xhscdn.com") == "cn"
    assert host_group("v5.douyinvod.com") == "cn"
    assert host_group("rr1---sn-x.googlevideo.com") == "overseas"
    assert host_group("video.twimg.com") == "overseas"
    assert host_group("pbs.twimg.com") == "overseas"


def test_fix_ftyp_obfuscated():
    # 合法 MP4：00 00 00 18 66 74 79 70 …（ftyp）
    good = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8
    bad = b"\x01\x00\x00\x18ftypisom" + b"\x00" * 8
    assert fix_ftyp(good) == good          # 正常不动
    assert fix_ftyp(bad) == good           # 首字节混淆修复
    assert fix_ftyp(b"notmp4") == b"notmp4"
    assert fix_ftyp(b"") == b""
