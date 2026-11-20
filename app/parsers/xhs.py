"""小红书解析：笔记页 HTML 抓 __INITIAL_STATE__，无需 x-s/x-s-common 签名。

来源：multiplatform-downloader/xhs.py + parse.shenzjd.com/parsers/xhs.js 的实测经验：
- curl_cffi impersonate=chrome 是硬要求（TLS 指纹风控）。
- xsec_token 是 App 分享链接自带的时效凭证；短链展开时不带 Cookie（Cookie 头不按域隔离，
  跳转跨 host 会泄露登录态）。
- 无水印原图：imageList[].fileId 直拼 sns-img CDN（原分辨率，服务端水印变体弃用）。
- 动图（实况）：imageList[].stream 按 EF4→h264→…取 masterUrl。
- 原画视频：note.video.consumer.originVideoKey 直拼；缺失时降级网页流（可能带号水印）。
"""
import json
import re
from urllib.parse import parse_qs, urlparse

from curl_cffi.requests import AsyncSession

from ..config import XHS_COOKIE
from ..errors import ParseError
from ..http_client import DESKTOP_UA, MOBILE_UA, client_for, curl_proxy
from ..identify import Target, XHS_ID_RE
from ..normalize import MediaItem, ParseResult, sanitize_filename

HOME = "https://www.xiaohongshu.com"
IMG_CDN = "https://sns-img-bd.xhscdn.com/"
VIDEO_CDN = "https://sns-video-bd.xhscdn.com/"

LIVE_ORDER = ("EF4", "h264", "EF6", "EF7", "h265")     # 实况动图：优先原画编码
VIDEO_ORDER = ("h265", "h264", "EF4", "EF5", "EF6", "EF7")  # 网页流降级时选高质量编码


async def _get_html(url: str, cookie: str | None) -> tuple[str, int, str]:
    """桌面 Chrome（TLS 伪装）优先；失败或被踢到登录页时回退移动端 Safari UA。

    参考仓库实测：桌面 UA 从服务端访问会被 302 到 /login（服务端出口无登录态），
    同一出口换 iPhone Safari UA 即可拿到 200 + 完整 __INITIAL_STATE__（无需 Cookie）。
    """
    headers = {
        "Referer": f"{HOME}/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if cookie:
        headers["Cookie"] = cookie

    # ① 桌面 Chrome + TLS 指纹伪装
    try:
        async with AsyncSession(impersonate="chrome", timeout=25,
                                proxy=curl_proxy("cn")) as session:
            resp = await session.get(url, headers={**headers, "User-Agent": DESKTOP_UA},
                                     allow_redirects=True)
        final, html = str(resp.url), resp.text
        if resp.status_code == 200 and "__INITIAL_STATE__" in html and "/login" not in final:
            return final, resp.status_code, html
    except Exception:
        pass

    # ② 移动端 Safari UA（匿名解析的关键路径）
    last_status = 0
    try:
        async with AsyncSession(impersonate="safari18_0", timeout=25,
                                proxy=curl_proxy("cn")) as session:
            resp = await session.get(url, headers={**headers, "User-Agent": MOBILE_UA},
                                     allow_redirects=True)
        return str(resp.url), resp.status_code, resp.text
    except Exception:
        return url, last_status, ""


def _extract_state(html: str) -> dict | None:
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        return None
    raw = m.group(1)
    # __INITIAL_STATE__ 是未序列化的 JS 对象，清洗裸 token 后才能 json.loads
    raw = re.sub(r"\bundefined\b", "null", raw)
    raw = re.sub(r"\bNaN\b", "null", raw)
    raw = re.sub(r"\b-?Infinity\b", "null", raw)
    raw = raw.replace("new Map([])", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _resolve_note(state: dict, note_id: str | None) -> dict | None:
    if not isinstance(state, dict):
        return None
    # 新版结构：note.noteDetailMap[noteId].note（登录态页面）
    note_root = state.get("note") or {}
    if isinstance(note_root, dict):
        detail_map = note_root.get("noteDetailMap") or {}
        if note_id and note_id in detail_map:
            note = (detail_map[note_id] or {}).get("note")
            if note:
                return note
        for entry in detail_map.values():
            note = (entry or {}).get("note")
            if note:
                return note
    # 匿名/旧版变体（顶层没有 note 键，如 noteData.data.noteData）
    def _is_note(node) -> bool:
        return isinstance(node, dict) and bool(
            node.get("imageList") or node.get("video") or node.get("noteId"))

    for path in (("noteData", "data", "noteData"), ("noteData", "data", "note"),
                 ("noteData", "noteData"), ("note", "data"), ("noteData", "data"),
                 ("noteData", "data", "noteDetailMap")):
        cur: object = state
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
        if _is_note(cur):
            return cur
    return None


def _image_url(im: dict) -> str:
    """无水印原图：fileId 直拼是原始分辨率；否则回退页面自带地址（升级 https）。"""
    file_id = im.get("fileId")
    if file_id:
        return IMG_CDN + str(file_id)
    for key in ("urlDefault", "url"):
        url = im.get(key)
        if url:
            return url.replace("http://", "https://")
    infos = im.get("infoList") or []
    if infos:
        url = (infos[-1] or {}).get("url")
        if url:
            return url.replace("http://", "https://")
    return ""


def _stream_url(streams: dict, order) -> str:
    for enc in order:
        arr = streams.get(enc) or []
        for entry in arr:
            url = entry.get("masterUrl")
            if url:
                return url
    return ""


def _ext_of(url: str, default: str = "jpg") -> str:
    m = re.search(r"\.(jpe?g|png|webp|gif)(?:[?#]|$)", url, re.I)
    return m.group(1).lower().replace("jpeg", "jpg") if m else default


async def _borrow_xsec_from_homepage(note_id: str) -> str | None:
    """无 xsec_token 时的兜底：首页推荐流里找同 ID 笔记，借它的 token。
    匿名首页的推荐流由前端异步加载（SSR 常为空），主要对配置了 Cookie 的会话有效。"""
    try:
        _, _, html = await _get_html(f"{HOME}/explore", XHS_COOKIE or None)
        state = _extract_state(html) or {}
        candidates: list[dict] = []
        note_root = state.get("note") or {}
        if isinstance(note_root.get("feed"), dict):
            candidates += note_root["feed"].get("feeds") or []
        home = state.get("home") or {}
        if isinstance(home.get("notes"), list):
            candidates += home["notes"]
        feed_root = state.get("feed")
        if isinstance(feed_root, dict):
            candidates += feed_root.get("feeds") or []
        for feed in candidates:
            if isinstance(feed, dict) and feed.get("id") == note_id:
                return feed.get("xsecToken") or None
    except Exception:
        pass
    return None


async def parse(target: Target) -> ParseResult:
    url = target.url
    note_id = target.item_id
    xsec_token = target.xsec_token
    xsec_source = target.xsec_source or "pc_feed"

    # 1. 短链展开：全程不带 Cookie（跨 host 跳转会泄露登录态）
    if "xhslink.com" in urlparse(url).netloc or target.kind == "short" or not note_id:
        final, _, _ = await _get_html(url, None)
        url = final
        m = XHS_ID_RE.search(urlparse(final).path)
        note_id = m.group(1) if m else note_id
        qs = parse_qs(urlparse(final).query)
        xsec_token = xsec_token or (qs.get("xsec_token") or [None])[0]
        xsec_source = (qs.get("xsec_source") or [xsec_source])[0]

    if not note_id:
        raise ParseError("无法从小红书链接中提取笔记 ID")

    # 无 token 时先尝试从首页推荐流借（App 分享链接自带时效 token，网页裸链接没有）
    if not xsec_token:
        borrowed = await _borrow_xsec_from_homepage(note_id)
        if borrowed:
            xsec_token = borrowed
            xsec_source = "pc_feed"

    # 2. 笔记页
    page = f"{HOME}/explore/{note_id}"
    if xsec_token:
        page += f"?xsec_token={xsec_token}&xsec_source={xsec_source}"
    final, _, html = await _get_html(page, XHS_COOKIE or None)

    if "/404" in final or "页面不见了" in html[:2000]:
        raise ParseError(
            "笔记被风控（404）：请改用 App 内「分享 → 复制链接」（链接自带时效 token），"
            "或在 /opt/parser-site/.env 配置 XHS_COOKIE 后重启容器再试"
        )

    state = _extract_state(html)
    note = _resolve_note(state, note_id) if state else None
    if not note:
        raise ParseError("解析失败：页面里没有笔记数据（可能需要 Cookie 或已被风控）")

    # 3. 元数据
    user = note.get("user") or {}
    author = user.get("nickName") or user.get("nickname") or user.get("name") or ""
    author_id = str(user.get("userId") or user.get("redId") or "")
    avatar = (user.get("avatar") or "").replace("http://", "https://")
    title = (note.get("title") or "").strip()
    desc = (note.get("desc") or "").strip()
    base = sanitize_filename(f"{author}_{title or desc}" or "xhs")

    result = ParseResult(
        platform="xhs",
        title=title or desc[:60],
        desc=desc,
        author=author,
        author_id=author_id,
        avatar=avatar,
        source_url=url,
    )

    # 4. 媒体提取
    image_list = note.get("imageList") or []
    video_root = note.get("video") or {}

    if note.get("type") == "video" and video_root:
        duration = None
        stream_map = (video_root.get("media") or {}).get("stream") or {}
        for streams in stream_map.values():
            for entry in streams or []:
                if isinstance(entry.get("duration"), int):
                    duration = entry["duration"]
                    break
            if duration:
                break
        result.duration = duration

        origin_key = ((video_root.get("consumer") or {}).get("originVideoKey")) or ""
        if origin_key:
            video_url, label, note_msg = VIDEO_CDN + origin_key, "原画", None
        else:
            video_url = _stream_url((video_root.get("media") or {}).get("stream") or {}, VIDEO_ORDER)
            label, note_msg = "网页流", "小红书未提供原始文件，此地址可能带小红书号水印（平台行为）"
        if not video_url:
            raise ParseError("未找到视频地址")
        result.type = "video"
        result.cover = _image_url(image_list[0]) if image_list else ""
        result.items = [MediaItem(
            kind="video", url=video_url, ext="mp4",
            filename=f"{base}.mp4", label=label, note=note_msg,
        )]
        return result

    # 图文笔记（含实况图）
    if not image_list:
        raise ParseError("未找到图片或视频数据")
    result.type = "image"
    result.cover = _image_url(image_list[0]) if image_list else ""
    items: list[MediaItem] = []
    for idx, im in enumerate(image_list, 1):
        img = _image_url(im)
        if img:
            thumb = None
            file_id = im.get("fileId")
            if file_id:
                # 预览用缩放变体（原图直链太重）；带 imageView2 参数的地址仅作预览
                thumb = f"{IMG_CDN}{file_id}?imageView2/2/w/640/format/jpg"
            items.append(MediaItem(
                kind="image", url=img, ext=_ext_of(img),
                filename=f"{base}_{idx:02d}.{_ext_of(img)}",
                thumb=thumb,
                width=im.get("width"), height=im.get("height"),
            ))
        # 实况动图：封面 jpg + 同编号 mp4（按文件名前缀配对）
        live_url = _stream_url(im.get("stream") or {}, LIVE_ORDER)
        if live_url:
            items.append(MediaItem(
                kind="live_video", url=live_url, ext="mp4",
                filename=f"{base}_{idx:02d}.mp4",
                label="实况动图", note="与同编号图片配对（Live Photo）",
            ))
    result.items = items
    return result
