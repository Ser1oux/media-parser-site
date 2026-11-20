"""抖音解析（SSR 主路线）：iesdouyin 分享页 _ROUTER_DATA，不用签名接口。

来源：parse.shenzjd.com/parsers/douyin.js + douyin-cookie.js、video-downloader/download-douyin.sh、
multiplatform-downloader/douyin.py 的实测经验：
- App UA 是唯一会下发 ttwid 的 UA：先取 ttwid，再带 ttwid 二次请求（匿名首次只有数据空壳）。
- 页面可能同时含 argus 风控脚本和真实数据 —— 先解析数据再判风控。
- 无水印：playwm→play 替换；原画：bit_rate[] 最高档，或 play 端点 ratio=default。
- 图集：images[].url_list 无水印（download_url_list 带「抖音号」水印，弃用）。
- 实况图：live_photo_type==1 或 clip_type==5（不能看 video.url_list 非空——那是 BGM）。
- SSR 拿不到数据或实况内容时走 douyin_browser（CDP）兜底；302/403 风控绝不自动重试。
"""
import re

import httpx

from ..config import DOUYIN_COOKIE, PROXY_CN
from ..errors import ParseError
from ..http_client import DESKTOP_UA, DOUYIN_APP_UA, MOBILE_UA
from ..identify import Target
from ..normalize import MediaItem, ParseResult, sanitize_filename
from .douyin_extract import extract_aweme

ROUTER_DATA_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", re.S)
TTWID_RE = re.compile(r"ttwid=([^;\"'\s]+)")
FILTER_KEYS = ("SYSTEM_ITEM_NOT_EXIST", "aweme_not_exist", "CONTENT_DELETED")

_ttwid_store: dict[str, str | None] = {"value": None}


def _proxy() -> str | None:
    return PROXY_CN or None


def _merge_cookie(ttwid: str | None) -> str | None:
    parts = []
    if DOUYIN_COOKIE:
        parts.append(DOUYIN_COOKIE)
    if ttwid and (not DOUYIN_COOKIE or "ttwid=" not in DOUYIN_COOKIE):
        parts.append(f"ttwid={ttwid}")
    return "; ".join(parts) if parts else None


def _capture_ttwid(response: httpx.Response) -> str | None:
    for r in [response, *response.history]:
        for set_cookie in r.headers.get_list("set-cookie"):
            m = TTWID_RE.search(set_cookie)
            if m:
                return m.group(1)
    return None


async def _expand(url: str) -> tuple[str, str | None, str | None, str | None]:
    """展开短链并提取 aweme_id；顺路捕获 ttwid。请求全程不带 Cookie。"""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15,
                                     proxy=_proxy()) as client:
            resp = await client.get(url, headers={"User-Agent": MOBILE_UA})
    except httpx.HTTPError:
        return url, None, None, None
    final = str(resp.url)
    ttwid = _capture_ttwid(resp)
    if ttwid:
        _ttwid_store["value"] = ttwid
    m = re.search(r"/(?:share/)?(?:video|note|slides|story)/(\d{15,})", urlparse_path(final))
    if not m:
        m = re.search(r"/(\d{15,})", urlparse_path(final))
    kind = None
    for k in ("video", "note", "slides", "story"):
        if f"/{k}" in urlparse_path(final):
            kind = k
            break
    return final, (m.group(1) if m else None), kind, ttwid


def urlparse_path(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).path


async def _harvest_ttwid(item_id: str, force: bool = False) -> str | None:
    """App UA 访问分享页收集匿名游客 ttwid（跨解析复用，稳定优先）。

    force=True 时强制重新采集——ttwid 会过期，SSR 拿着旧 ttwid 会拿不到数据。
    """
    if _ttwid_store["value"] and not force:
        return _ttwid_store["value"]
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15,
                                     proxy=_proxy()) as client:
            resp = await client.get(
                f"https://www.iesdouyin.com/share/video/{item_id}/",
                headers={"User-Agent": DOUYIN_APP_UA},
            )
        ttwid = _capture_ttwid(resp)
        if ttwid:
            _ttwid_store["value"] = ttwid
        return ttwid
    except httpx.HTTPError:
        return None


async def _ssr_item(item_id: str, kind: str) -> tuple[dict | None, str | None]:
    """SSR 取数据：返回 (item, filter_reason)。"""
    for ua in (MOBILE_UA, DESKTOP_UA):
        html = await _fetch_share_html(item_id, kind, _ttwid_store["value"], ua)
        if not html:
            continue
        item, reason = _extract_item(html)
        if item:
            return item, None
        if reason:
            return None, reason
    return None, None


async def _fetch_share_html(item_id: str, kind: str, ttwid: str | None, ua: str) -> str | None:
    cookie = _merge_cookie(ttwid)
    headers = {"User-Agent": ua, "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        headers["Cookie"] = cookie
    urls = [f"https://www.iesdouyin.com/share/{kind}/{item_id}/"]
    if kind == "video":
        urls.append(f"https://www.iesdouyin.com/share/note/{item_id}/")
    elif kind == "note":
        urls.append(f"https://www.iesdouyin.com/share/video/{item_id}/")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15,
                                     proxy=_proxy()) as client:
            for u in urls:
                try:
                    resp = await client.get(u, headers=headers)
                except httpx.HTTPError:
                    continue
                if resp.status_code == 200 and "_ROUTER_DATA" in resp.text:
                    return resp.text
    except httpx.HTTPError:
        return None
    return None


def _extract_item(html: str) -> tuple[dict | None, str | None]:
    """解析 _ROUTER_DATA → aweme item；同时提取 filter 原因（先解析数据再判风控）。"""
    m = ROUTER_DATA_RE.search(html)
    if not m:
        return None, None
    import json
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None, None
    loader = data.get("loaderData") or {}
    for key, value in loader.items():
        if not key.endswith("/page"):
            continue
        info = (value or {}).get("videoInfoRes") or {}
        items = info.get("item_list") or []
        if items:
            return items[0], None
        for f in info.get("filter_list") or []:
            reason = (f or {}).get("filter_reason") or ""
            if reason:
                return None, reason
    return None, None


def _is_challenge(html: str) -> bool:
    marks = ("argus-csp-token", "_$jsvmprt", "precollect")
    return sum(1 for m in marks if m in html) >= 2


async def _browser_detail(item_id: str, ttwid: str | None = None) -> dict | None:
    try:
        from .douyin_browser import fetch_detail
        return await fetch_detail(item_id, ttwid)
    except ImportError:
        return None  # 未安装 playwright：跳过 CDP，走 SSR 结果或报错


async def parse(target: Target) -> ParseResult:
    url = target.url
    item_id, kind = target.item_id, target.kind

    if not item_id or kind == "short" or "v.douyin.com" in url:
        _, item_id, kind, _ = await _expand(url)
    if not item_id or not re.fullmatch(r"\d{15,}", item_id):
        raise ParseError("无法提取抖音作品 ID（不支持主页/直播/合集链接）")
    kind = kind or "video"

    # ttwid 二次握手是数据能否返回的命门（无/过期 ttwid = 数据空壳）
    if not _ttwid_store["value"]:
        await _harvest_ttwid(item_id)

    # ---- 路线 A：SSR 分享页 ----
    item, filter_reason = await _ssr_item(item_id, kind)
    if not item and not filter_reason and _ttwid_store["value"]:
        # ttwid 可能已过期 → 强制刷新再试一次（实测：旧 ttwid 必然拿不到数据）
        await _harvest_ttwid(item_id, force=True)
        item, filter_reason = await _ssr_item(item_id, kind)

    if item is None and filter_reason:
        raise ParseError(f"内容不可解析（{filter_reason}）：可能已删除/私密，或刚发布被抖音过滤")

    aweme_type = (item or {}).get("aweme_type")
    # SSR 数据可能不含实况动图字段 → aweme_type==2（实况）或完全无数据时走 CDP
    browser_error: ParseError | None = None
    if item is None or aweme_type == 2:
        try:
            browser_item = await _browser_detail(item_id, _ttwid_store["value"])
            if browser_item:
                item = browser_item
        except ParseError as exc:
            browser_error = exc

    if not item and browser_error:
        raise browser_error
    if not item:
        raise ParseError(
            "抖音解析失败：分享页无有效数据（可能被风控）。"
            "建议配置 DOUYIN_COOKIE，或稍等几分钟再试"
        )

    try:
        result = extract_aweme(item, source_url=url)
    except ParseError:
        # CDP 失败且 SSR 数据不完整（如实况只剩空壳）时，把 CDP 的错误如实报出
        if browser_error is not None:
            raise browser_error
        raise
    return result
