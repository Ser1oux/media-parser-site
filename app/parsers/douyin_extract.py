"""抖音 aweme 数据 → 统一 ParseResult（SSR 与 CDP 兜底共用这一层提取）。"""
import re

from ..errors import ParseError
from ..normalize import MediaItem, ParseResult, QualityOption, sanitize_filename


def _first_url(node) -> str:
    if not isinstance(node, dict):
        return ""
    urls = node.get("url_list") or []
    return urls[0] if urls else ""


def _unwrap_wm(url: str) -> str:
    """去水印端点替换：/playwm/→/play/、play_wm→play、watermark=1→0。"""
    return (url.replace("/playwm/", "/play/")
               .replace("play_wm", "play")
               .replace("watermark=1", "watermark=0"))


def _gear_height(gear_name: str) -> int:
    m = re.search(r"_(\d{3,4})_", gear_name or "")
    return int(m.group(1)) if m else 0


def best_video_url(video: dict) -> tuple[str, str]:
    """原画地址：bit_rate[] 按 (高度, 码率) 取最高档；否则 play 端点 ratio=default。"""
    tiers = video_tiers(video)
    if tiers:
        return tiers[0][0], tiers[0][1]
    fallback = _first_url(video.get("play_addr"))
    if fallback:
        return _unwrap_wm(fallback), ""
    raise ParseError("未找到视频地址")


def video_tiers(video: dict) -> list[tuple[str, str, int]]:
    """抖音画质档位：返回 [(url, label, height)]，从高到低。

    bit_rate[] 里每档都是独立直链（不同 gear_name/清晰度），
    最高档即上传者原画；没有 bit_rate 时用 play 端点 ratio=default。
    """
    candidates: list[tuple[int, int, str, str]] = []   # (-height, -bitrate, url, gear)
    for entry in video.get("bit_rate") or []:
        urls = (entry.get("play_addr") or {}).get("url_list") or []
        if not urls:
            continue
        gear = entry.get("gear_name") or ""
        height = _gear_height(gear)
        candidates.append((
            -height, -(entry.get("bit_rate") or 0),
            _unwrap_wm(urls[0]), gear,
        ))
    if not candidates:
        uri = (video.get("play_addr") or {}).get("uri") or ""
        if uri and not uri.startswith("http"):
            return [(
                f"https://www.iesdouyin.com/aweme/v1/play/?video_id={uri}&ratio=default&line=0",
                "原画", 0,
            )]
        fallback = _first_url(video.get("play_addr"))
        return [(_unwrap_wm(fallback), "原画", 0)] if fallback else []

    candidates.sort()
    tiers: list[tuple[str, str, int]] = []
    seen_heights: set[int] = set()
    for neg_h, _bitrate, url, gear in candidates:
        h = -neg_h
        if h in seen_heights:
            continue
        seen_heights.add(h)
        label = f"{h}p" if h else "原画"
        tiers.append((url, label, h))
        if len(tiers) >= 4:
            break
    return tiers


def _best_image_url(im: dict) -> str:
    """图集无水印原图：url_list（优先 .jpeg 条目）；download_url_list 带水印，不碰。"""
    urls = [u for u in (im.get("url_list") or []) if isinstance(u, str)]
    for u in urls:
        if ".jpeg" in u or ".jpg" in u:
            return u
    return urls[0] if urls else ""


def _is_live(im: dict) -> bool:
    """实况图判定：live_photo_type==1 或 clip_type==5。
    不能用 video.url_list 非空判断——普通图集的 video.play_addr 是背景音乐。"""
    return im.get("live_photo_type") == 1 or im.get("clip_type") == 5


def _live_mp4_url(im: dict) -> str:
    video = im.get("video") or {}
    for key in ("download_addr", "play_addr"):
        url = _first_url(video.get(key))
        if url:
            return _unwrap_wm(url)
    return ""


def extract_aweme(aweme: dict, source_url: str = "") -> ParseResult:
    author = aweme.get("author") or {}
    author_name = author.get("nickname") or author.get("unique_id") or ""
    author_id = str(author.get("uid") or author.get("sec_uid") or aweme.get("author_user_id") or "")
    avatar = _first_url(author.get("avatar_thumb"))
    desc = (aweme.get("desc") or "").strip()
    video = aweme.get("video") or {}
    images = aweme.get("images") or []

    aweme_id = str(aweme.get("aweme_id") or "")
    base = sanitize_filename(f"{author_name}_{desc}" or f"douyin_{aweme_id}")

    result = ParseResult(
        platform="douyin",
        title=desc[:60] or f"抖音 {aweme_id}",
        desc=desc,
        author=author_name,
        author_id=author_id,
        avatar=avatar,
        duration=int(video.get("duration") or 0) or None,
        source_url=source_url,
    )

    if images:
        result.type = "image"
        items: list[MediaItem] = []
        for idx, im in enumerate(images, 1):
            if not isinstance(im, dict):
                continue
            img = _best_image_url(im)
            if img:
                result.cover = result.cover or img
                items.append(MediaItem(
                    kind="image", url=img,
                    ext="jpg" if ".jpeg" in img or ".jpg" in img else "webp" if ".webp" in img else "jpg",
                    filename=f"{base}_{idx:02d}.jpg",
                ))
            if _is_live(im):
                live_url = _live_mp4_url(im)
                if live_url:
                    items.append(MediaItem(
                        kind="live_video", url=live_url, ext="mp4",
                        filename=f"{base}_{idx:02d}.mp4",
                        label="实况动图", note="与同编号图片配对（Live Photo）",
                    ))
        if not items:
            raise ParseError("图集数据为空")
        result.items = items
        return result

    url, label = best_video_url(video)
    result.type = "video"
    result.cover = _first_url(video.get("cover")) or _first_url(video.get("origin_cover"))

    # 画质分档：bit_rate 多档各自直链
    tiers = video_tiers(video)
    qualities = []
    for idx, t in enumerate(tiers):
        name = t[1]
        if idx == 0 and name != "原画":
            name = f"原画 {name}"
        qualities.append(QualityOption(label=name, url=t[0], height=t[2]))
    result.items = [MediaItem(
        kind="video", url=url, ext="mp4",
        filename=f"{base}.mp4",
        label=label or None,
        qualities=qualities,
    )]
    return result
