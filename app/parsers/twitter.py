"""X (Twitter) 解析：syndication CDN 公开接口，零 cookie、零签名。

来源：parse.shenzjd.com/parsers/twitter.js。
token 由 tweet ID 确定性推导（(id/1e15)*π 去零去点），与页面 JS 同算法。
视频/animated_gif 取 mp4 variants 最高码率（X 媒体天然无水印）；
图片 media_url_https 加 ?format=…&name=orig 取原图。
"""
import math
import re

import httpx

from ..config import PROXY_OVERSEAS, X_COOKIE
from ..errors import ParseError
from ..http_client import DESKTOP_UA
from ..identify import Target
from ..normalize import MediaItem, ParseResult, QualityOption, sanitize_filename

API = "https://cdn.syndication.twimg.com/tweet-result"


def tweet_token(id_str: str) -> str:
    """与前端 syndication JS 一致的确定性 token。"""
    num = float(id_str)
    token = (num / 1e15) * math.pi
    token_str = str(token)
    if "e" in token_str or "E" in token_str:
        token_str = f"{token:.16f}".rstrip("0").rstrip(".")
    return token_str.replace("0", "").replace(".", "")


async def _expand_short(url: str) -> str:
    """t.co 短链展开后再取 tweet id。"""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15,
                                     proxy=PROXY_OVERSEAS or None) as client:
            resp = await client.get(url, headers={"User-Agent": DESKTOP_UA})
            return str(resp.url)
    except httpx.HTTPError:
        return url


def _pick_ext(media_url: str) -> str:
    m = re.search(r"\.(jpe?g|png|webp|gif)$", media_url, re.I)
    return (m.group(1).lower().replace("jpeg", "jpg") if m else "jpg")


async def parse(target: Target) -> ParseResult:
    tweet_id = target.item_id
    if not tweet_id and target.kind == "short":
        final = await _expand_short(target.url)
        m = re.search(r"/status(?:es)?/(\d{5,})", final)
        tweet_id = m.group(1) if m else None
    if not tweet_id:
        raise ParseError("无法从链接中提取推文 ID")

    url = f"{API}?id={tweet_id}&token={tweet_token(tweet_id)}&lang=zh"
    headers = {
        "User-Agent": DESKTOP_UA,
        "Referer": "https://platform.twitter.com/",
        "Accept": "*/*",
    }
    if X_COOKIE:
        headers["Cookie"] = X_COOKIE
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15,
                                     proxy=PROXY_OVERSEAS or None) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ParseError(f"X 接口请求失败（{type(exc).__name__}）：检查服务器能否访问 x.com")

    if resp.status_code != 200:
        raise ParseError(f"X 接口返回 {resp.status_code}：推文可能不存在，或 syndication 接口已变更")
    data = resp.json()
    if not isinstance(data, dict) or data.get("__typename") == "TweetTombstone":
        raise ParseError("推文不存在或已删除")

    user = data.get("user") or {}
    author = user.get("name") or user.get("screen_name") or ""
    author_id = user.get("screen_name") or ""
    avatar = (user.get("profile_image_url_https") or "").replace("_normal", "_400x400")
    text = (data.get("text") or "").strip()

    result = ParseResult(
        platform="twitter",
        title=text[:60] or f"推文 {tweet_id}",
        desc=text,
        author=author,
        author_id=author_id,
        avatar=avatar,
        source_url=f"https://x.com/i/status/{tweet_id}",
    )

    base = sanitize_filename(f"{author or 'x'}_{tweet_id}")
    items: list[MediaItem] = []
    medias = data.get("mediaDetails") or []
    for idx, media in enumerate(medias, 1):
        media_type = media.get("type")
        if media_type == "photo":
            media_url = media.get("media_url_https") or ""
            if not media_url:
                continue
            ext = _pick_ext(media_url)
            items.append(MediaItem(
                kind="image", url=f"{media_url}?format={ext}&name=orig", ext=ext,
                filename=f"{base}_{idx:02d}.{ext}",
                label="原图",
            ))
            if not result.cover:
                result.cover = media_url
        elif media_type in ("video", "animated_gif"):
            variants = (media.get("video_info") or {}).get("variants") or []
            mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
            if not mp4s:
                mp4s = [v for v in (data.get("video") or {}).get("variants") or []
                        if (v.get("content_type") == "video/mp4")]
            if not mp4s:
                continue
            # 画质分档：按码率降序，最多 3 档（每档都是现成直链）
            mp4s = sorted(mp4s, key=lambda v: v.get("bitrate") or 0, reverse=True)[:3]
            tiers: list[QualityOption] = []
            for idx, v in enumerate(mp4s):
                bitrate = v.get("bitrate") or 0
                tiers.append(QualityOption(
                    label=("原画 " if idx == 0 else "") + f"{bitrate // 1000}kbps",
                    url=v.get("url") or "",
                    note="最高码率" if idx == 0 else None,
                ))
            best = mp4s[0]
            items.append(MediaItem(
                kind="video", url=best.get("url") or "", ext="mp4",
                filename=f"{base}_{idx:02d}.mp4" if len(medias) > 1 else f"{base}.mp4",
                label="GIF 动图（原档）" if media_type == "animated_gif" else
                      f"{tiers[0].label.replace('原画 ', '')} 原档",
                note="X 媒体天然无水印",
                qualities=tiers,
            ))
            if not result.cover:
                result.cover = media.get("poster") or ""

    if not items:
        result.type = "text"
        return result

    result.type = "video" if any(i.kind == "video" for i in items) else "image"
    result.items = items
    return result
