"""分享文案 → URL 提取 → 平台识别 → ID 提取。"""
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .errors import ParseError

# 分享文案里的第一个链接（中文标点也作边界）
URL_RE = re.compile(r'https?://[^\s<>"\'（）【】，。；！？]+', re.I)

# 各平台域名后缀（hostname 等于后缀或以 .后缀 结尾即命中）
_HOST_SUFFIX = {
    "douyin": ("douyin.com", "iesdouyin.com", "zjcdn.com", "snssdk.com"),
    "xhs": ("xiaohongshu.com", "xhslink.com"),
    "twitter": ("twitter.com", "x.com", "t.co"),
    "youtube": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
}

DOUYIN_ID_RE = re.compile(r"/(?:share/)?(?:video|note|slides|story)/(\d{15,})")
DOUYIN_FALLBACK_RE = re.compile(r"/(\d{15,})")
XHS_ID_RE = re.compile(r"/(?:explore|discovery/item|user/profile/[0-9a-zA-Z]+)/([0-9a-f]{16,})")
TW_ID_RE = re.compile(r"/(?:[^/]+/)?status(?:es)?/(\d{5,})")
YT_ID_RE = re.compile(r"(?:[?&]v=|/shorts/|/embed/|/live/|youtu\.be/)([\w-]{11})")


@dataclass
class Target:
    """识别结果：平台 + 规整链接 + 条目 ID + 附加参数。"""
    platform: str
    url: str
    item_id: str | None = None
    kind: str | None = None            # douyin: video/note/slides
    xsec_token: str | None = None      # 小红书风控凭证（分享链接自带）
    xsec_source: str | None = None


def extract_first_url(text: str) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(".,;") if m else None


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def detect_platform(url: str) -> str:
    host = _host_of(url)
    if not host:
        raise ParseError("无法识别链接主机名")
    for platform, suffixes in _HOST_SUFFIX.items():
        for suffix in suffixes:
            if host == suffix or host.endswith("." + suffix):
                return platform
    raise ParseError(
        "不支持该链接。当前支持：小红书 / 抖音 / X(Twitter) / YouTube"
    )


def identify(text: str) -> Target:
    """从分享文案提取第一个链接并识别平台。"""
    url = extract_first_url(text)
    if not url:
        raise ParseError("分享文案中未找到链接")
    platform = detect_platform(url)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    target = Target(platform=platform, url=url)

    if platform == "douyin":
        m = DOUYIN_ID_RE.search(parsed.path)
        if not m:
            m = DOUYIN_FALLBACK_RE.search(parsed.path)
        if m:
            target.item_id = m.group(1)
        for k in ("video", "note", "slides", "story"):
            if f"/{k}" in parsed.path:
                target.kind = k
                break
        if "v.douyin.com" in parsed.netloc:
            target.kind = target.kind or "short"

    elif platform == "xhs":
        m = XHS_ID_RE.search(parsed.path)
        if m:
            target.item_id = m.group(1)
        target.xsec_token = (qs.get("xsec_token") or [None])[0]
        target.xsec_source = (qs.get("xsec_source") or [None])[0]

    elif platform == "twitter":
        if "t.co" in parsed.netloc:
            target.kind = "short"      # 由解析器展开后再取 ID
        else:
            m = TW_ID_RE.search(parsed.path)
            if m:
                target.item_id = m.group(1)
            else:
                raise ParseError("仅支持单条推文链接（…/status/…），不支持主页/搜索页")

    elif platform == "youtube":
        m = YT_ID_RE.search(url)
        if m:
            target.item_id = m.group(1)
        if "list" in qs and not target.item_id:
            raise ParseError("暂不支持播放列表，请粘贴单个视频链接")

    return target
