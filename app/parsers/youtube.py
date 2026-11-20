"""YouTube 解析：yt-dlp Python API 提取格式，原画 = video-only 最高 (height, tbr) + 音轨最高。

关键约束：googlevideo 直链绑定解析者 IP —— 服务器提取的链接用户浏览器用不了，
所以音视频两条直链交给 /api/download/mux 由服务器 ffmpeg 实时合流（-c copy 不转码）。
音频优先 mp4a（可直接入 mp4 容器）；只有 opus 时 mux 端点自动加 -strict -2。
"""
import asyncio

from ..config import PROXY_OVERSEAS, YT_COOKIE
from ..errors import ParseError
from ..identify import Target
from ..normalize import MediaItem, ParseResult, QualityOption, sanitize_filename


def _extract_sync(url: str, opts: dict) -> dict:
    import yt_dlp
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return ydl.sanitize_info(info) or {}


def _pick_formats(formats: list[dict]) -> tuple[dict | None, dict | None, dict | None]:
    """返回 (最佳 video-only, 最佳音频(优先 mp4a), 最佳渐进式)。"""
    best_v: dict | None = None
    best_a: dict | None = None
    best_a_opus: dict | None = None
    best_prog: dict | None = None
    key_v = (0, 0)
    key_prog = (0, 0)
    abr_a = abr_opus = 0.0
    for f in formats:
        if not f.get("url"):
            continue
        vcodec = f.get("vcodec") or "none"
        acodec = f.get("acodec") or "none"
        height = f.get("height") or 0
        tbr = f.get("tbr") or 0.0
        if vcodec != "none" and acodec == "none":
            if (height, tbr) > key_v:
                best_v, key_v = f, (height, tbr)
        elif acodec != "none" and vcodec == "none":
            abr = f.get("abr") or tbr or 0.0
            if str(acodec).startswith("mp4a"):
                if abr > abr_a:
                    best_a, abr_a = f, abr
            elif abr > abr_opus:
                best_a_opus, abr_opus = f, abr
        elif vcodec != "none" and acodec != "none":
            if (height, tbr) > key_prog:
                best_prog, key_prog = f, (height, tbr)
    return best_v, (best_a or best_a_opus), best_prog


def _quality_tiers(formats: list[dict]) -> list[dict]:
    """按分辨率去重挑出可下载的高度档（从高到低，最多 4 档）。

    同一高度可能有多条流（VP9/AV1/H.264），这里只保留该高度里最高码率的一条，
    用于判断"这一档存在"，真正的下载由 mux 端点按高度重新解析。
    """
    best_by_height: dict[int, tuple[float, str]] = {}
    for f in formats:
        if not f.get("url"):
            continue
        if (f.get("vcodec") or "none") == "none" or (f.get("acodec") or "none") != "none":
            continue  # 只要 video-only
        h = f.get("height") or 0
        if h <= 0:
            continue
        tbr = f.get("tbr") or 0.0
        vcodec = (f.get("vcodec") or "").split(".")[0]
        prev = best_by_height.get(h)
        if prev is None or tbr > prev[0]:
            best_by_height[h] = (tbr, vcodec)
    heights = sorted(best_by_height.keys(), reverse=True)
    tiers = []
    for h in heights:
        if h in (2160, 1440, 1080, 720, 480, 360):
            tiers.append((h, best_by_height[h][1]))
    # 相邻档位落差过大时跳过中间档（如 2160 → 720 没有 1080 就别只给两档）
    picked = []
    for h, vcodec in tiers:
        if picked and h < picked[-1][0] * 0.55 and len(picked) >= 2:
            continue
        picked.append((h, vcodec))
        if len(picked) >= 4:
            break
    return [{"height": h, "vcodec": vcodec} for h, vcodec in picked]


async def parse(target: Target) -> ParseResult:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
    }
    if PROXY_OVERSEAS:
        opts["proxy"] = PROXY_OVERSEAS
    if YT_COOKIE:
        opts["http_headers"] = {"Cookie": YT_COOKIE}

    try:
        info = await asyncio.to_thread(_extract_sync, target.url, opts)
    except Exception as exc:
        msg = str(exc)
        if "Sign in" in msg or "age" in msg.lower() or "members" in msg.lower():
            raise ParseError("该视频需要登录/年龄验证/会员：请配置 YT_COOKIE 后重试")
        if "Private" in msg or "unavailable" in msg.lower() or "removed" in msg.lower():
            raise ParseError("视频不存在或为私密/已下架")
        raise ParseError(f"yt-dlp 提取失败：{msg[:200]}（多半是 yt-dlp 版本旧了，升级后重建镜像）")

    if info.get("_type") == "playlist" or info.get("entries"):
        raise ParseError("暂不支持播放列表，请粘贴单个视频链接")

    formats = info.get("formats") or []
    best_v, best_a, best_prog = _pick_formats(formats)

    title = info.get("title") or "youtube"
    author = info.get("uploader") or info.get("channel") or ""
    base = sanitize_filename(f"{author}_{title}" or "youtube")
    duration = int((info.get("duration") or 0) * 1000) or None

    result = ParseResult(
        platform="youtube",
        title=title,
        desc=(info.get("description") or "")[:500],
        author=author,
        author_id=info.get("channel_id") or info.get("uploader_id") or "",
        avatar="",
        cover=info.get("thumbnail") or "",
        duration=duration,
        source_url=target.url,
    )

    size = (best_v.get("filesize") or best_v.get("filesize_approx") or 0) + \
           (best_a.get("filesize") or best_a.get("filesize_approx") or 0) \
        if (best_v and best_a) else None

    if best_v and best_a:
        vcodec = (best_v.get("vcodec") or "").split(".")[0] or ""
        vcodec_label = {"avc1": "H.264", "vp09": "VP9", "vp9": "VP9", "av01": "AV1"}.get(
            vcodec, vcodec or "")
        height = best_v.get("height") or 0
        result.type = "video"

        # 画质分档：按高度给档位（YouTube 直链绑 IP，下载时按档位重新解析）
        tiers = _quality_tiers(info.get("formats") or [])
        qualities: list[QualityOption] = []
        for idx, t in enumerate(tiers):
            h = t["height"]
            label = "原画 " if idx == 0 else ""
            label += f"{h}p"
            qualities.append(QualityOption(
                label=label, height=h,
                note="上传者原始画质" if idx == 0 else None,
            ))
        if not qualities:
            qualities = [QualityOption(label=f"原画 {height}p", height=height)]

        result.items = [MediaItem(
            kind="video",
            ext="mp4",
            filename=f"{base}.mp4",
            label=f"{height}p {vcodec_label} 原画".strip(),
            note="下载时服务器按所选画质重新解析并实时合并音视频轨（-c copy 无转码损质）",
            size=size or None,
            yt_url=target.url,
            qualities=qualities,
        )]
        return result

    if best_prog:
        height = best_prog.get("height") or 0
        result.type = "video"
        result.items = [MediaItem(
            kind="video", url=best_prog["url"], ext="mp4",
            filename=f"{base}.mp4",
            label=f"{height}p 渐进式单流",
            note="未找到分轨原画格式，退回渐进式（清晰度上限较低）",
            size=best_prog.get("filesize") or best_prog.get("filesize_approx") or None,
        )]
        return result

    raise ParseError("未找到可下载格式（该视频可能不支持在线下载）")
