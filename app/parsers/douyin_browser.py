"""抖音 CDP 兜底：无头 Chromium 打开视频页，捕获 detail 接口请求并提取数据。

detail 接口要求 a_bogus + timestamp + x-secsdk-web-signature + uifid 四件套，
由页面里的 secsdk JS 自动生成 —— 所以完全不实现签名，让真浏览器自己签。

提取数据两条路：
1. 读响应体（最快，但抖音页面 JS 会把 body 消费掉，Playwright 读到空 → JSONDecodeError）。
2. 捕获带签名的完整请求 URL 用 httpx 重放（a_bogus 签在 URL 里、不绑 IP，实测可靠）。

风控规则（实测）：
- 重放非 200 / 页面被拦 → 报错不重试（重试会重置风控窗口）。
- CDP 层抖动（响应体被提前回收、重放网络错误）→ 换干净浏览器重试一次。
"""
import asyncio
import time

import httpx
from playwright.sync_api import sync_playwright

from ..config import DOUYIN_COOKIE, PROXY_CN
from ..errors import ParseError
from ..http_client import DESKTOP_UA

DETAIL_PATH = "/aweme/v1/web/aweme/detail/"


class _CdpDynamics(Exception):
    """CDP 层抖动（响应体被提前回收等），可换干净浏览器重试一次。"""


def _cookie_pairs() -> list[dict]:
    pairs = []
    for part in DOUYIN_COOKIE.split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            pairs.append({"name": name.strip(), "value": value.strip(),
                          "domain": ".douyin.com", "path": "/"})
    return pairs


def _replay_detail(url: str, headers: dict, ttwid: str | None) -> dict:
    """用浏览器发出的带签名 URL 原样重放（沿用浏览器的 UA/Referer）。

    实测关键点：必须带 Cookie（至少 ttwid）——干净无 Cookie 的重放会返回 200 + 空响应体
    （抖音的静默风控）。"""
    replay_headers: dict[str, str] = {}
    for name in ("user-agent", "referer", "cookie", "accept"):
        value = headers.get(name)
        if value:
            key = {"user-agent": "User-Agent", "referer": "Referer",
                   "cookie": "Cookie", "accept": "Accept"}[name]
            replay_headers[key] = value
    replay_headers.setdefault("User-Agent", DESKTOP_UA)
    replay_headers.setdefault("Referer", "https://www.douyin.com/")
    if "Cookie" not in replay_headers and ttwid:
        replay_headers["Cookie"] = f"ttwid={ttwid}"

    try:
        with httpx.Client(timeout=20, proxy=PROXY_CN or None, follow_redirects=True) as client:
            resp = client.get(url, headers=replay_headers)
    except httpx.HTTPError as exc:
        raise _CdpDynamics(f"重放请求网络错误 {type(exc).__name__}")
    if resp.status_code != 200:
        raise ParseError(
            f"抖音风控拦截（重放 detail 返回 {resp.status_code}）："
            "请配置 DOUYIN_COOKIE，或稍等几分钟再试"
        )
    try:
        data = resp.json()
    except ValueError:
        raise _CdpDynamics("重放响应体不是 JSON")
    detail = (data or {}).get("aweme_detail")
    if detail:
        return detail
    raise ParseError("作品不存在、已删除，或为「动态」等不支持的内容")


def _fetch_sync(aweme_id: str, ttwid: str | None = None) -> dict | None:
    with sync_playwright() as p:
        launch_kwargs: dict = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--mute-audio"],
        }
        if PROXY_CN:
            launch_kwargs["proxy"] = {"server": PROXY_CN}
        try:
            browser = p.chromium.launch(**launch_kwargs)
        except Exception as exc:
            raise ParseError(
                f"无头浏览器启动失败（{str(exc)[:120]}）：容器内需完成 playwright install chromium"
            )
        try:
            context = browser.new_context(
                user_agent=DESKTOP_UA,
                locale="zh-CN",
                viewport={"width": 1440, "height": 900},
            )
            # ttwid 是 detail 数据能否返回的命门（无 Cookie 的干净配置会被静默风控成空响应）
            cookie_pairs = _cookie_pairs() if DOUYIN_COOKIE else []
            if ttwid and not any(c["name"] == "ttwid" for c in cookie_pairs):
                cookie_pairs.append({"name": "ttwid", "value": ttwid,
                                     "domain": ".douyin.com", "path": "/"})
            if cookie_pairs:
                try:
                    context.add_cookies(cookie_pairs)
                except Exception:
                    pass
            page = context.new_page()
            holder: dict = {}

            def on_request(req):
                # 捕获带签名的 detail 请求 URL（body 被 JS 消费时用它重放）
                if DETAIL_PATH not in req.url:
                    return
                try:
                    headers = dict(req.all_headers())   # headers() 不含 Cookie，all_headers() 才有
                except Exception:
                    headers = dict(req.headers)
                if "a_bogus=" in req.url or "msToken=" in req.url:
                    holder["signed"] = (req.url, headers)   # 优先用签过名的
                elif "url" not in holder:
                    holder["url"] = req.url
                    holder["headers"] = headers

            def on_response(resp):
                if DETAIL_PATH in resp.url:
                    try:
                        holder["data"] = resp.json()
                    except Exception as exc:
                        holder["err"] = f"{type(exc).__name__}: {str(exc)[:120]}"

            page.on("request", on_request)
            page.on("response", on_response)
            try:
                page.goto(f"https://www.douyin.com/video/{aweme_id}",
                          wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass  # goto 超时不致命：detail 请求可能已经发出

            # 页面会先发一个未签名请求，等签过名的那个（最多 20s）
            deadline = time.time() + 20
            while time.time() < deadline and "signed" not in holder \
                    and "data" not in holder:
                page.wait_for_timeout(300)

            if "data" in holder:
                detail = (holder["data"] or {}).get("aweme_detail")
                if detail:
                    return detail
                raise ParseError("作品不存在、已删除，或为「动态」等不支持的内容")

            if "signed" in holder:
                url, headers = holder["signed"]
                return _replay_detail(url, headers, ttwid)
            if "url" in holder:
                url, headers = holder["url"], holder["headers"]
                return _replay_detail(url, headers, ttwid)

            # 没有任何 detail 请求：大概率被风控/登录墙拦住
            head = ""
            try:
                head = page.content()[:2000]
            except Exception:
                pass
            if "登录" in head or "verify" in head.lower():
                raise ParseError("抖音要求登录验证：请配置 DOUYIN_COOKIE 后重试")
            raise ParseError(
                "抖音风控拦截（无头浏览器未发出 detail 请求）：请配置 DOUYIN_COOKIE，"
                "给 PROXY_CN 配住宅代理，或稍等几分钟再试"
            )
        finally:
            browser.close()


async def fetch_detail(aweme_id: str, ttwid: str | None = None) -> dict | None:
    """风控错误（ParseError）直接上抛不重试；CDP 抖动换新浏览器重试一次。"""
    from .douyin import _harvest_ttwid, _ttwid_store
    if not ttwid:
        # 无 Cookie 的重放会被抖音静默风控（200 + 空响应体），必须先拿到 ttwid
        await _harvest_ttwid(aweme_id)
        ttwid = _ttwid_store.get("value")
    try:
        return await asyncio.to_thread(_fetch_sync, aweme_id, ttwid)
    except _CdpDynamics:
        # 可能是 ttwid 过期 → 换一个干净浏览器 + 新 ttwid 再试一次
        await _harvest_ttwid(aweme_id, force=True)
        fresh = _ttwid_store.get("value") or ttwid
        try:
            return await asyncio.to_thread(_fetch_sync, aweme_id, fresh)
        except _CdpDynamics:
            raise ParseError(
                "浏览器响应体提取连续失败，请稍后再试。"
                "若持续失败请配置 DOUYIN_COOKIE，或给 PROXY_CN 配住宅代理"
            )
