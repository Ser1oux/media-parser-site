"""公共请求常量与客户端工厂。"""
import httpx

from .config import PROXY_CN, PROXY_OVERSEAS

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
# 抖音 App UA：实测是唯一会下发 ttwid 的 UA（必须先带它建立游客态）
DOUYIN_APP_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 aweme/32.7.0 Bag/1.0.0 ios/17.0"
)

DEFAULT_TIMEOUT = httpx.Timeout(15.0, read=30.0, write=30.0)

_clients: dict[str, httpx.AsyncClient] = {}


def httpx_client(proxy: str | None = None) -> httpx.AsyncClient:
    """按代理键复用 AsyncClient（连接池复用）。"""
    key = proxy or "__direct__"
    client = _clients.get(key)
    if client is None:
        client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            proxy=proxy or None,
        )
        _clients[key] = client
    return client


def client_for(host_group: str) -> httpx.AsyncClient:
    """host_group: "cn" | "overseas" | "direct" —— 由调用方按 CDN host 归组。"""
    if host_group == "cn" and PROXY_CN:
        return httpx_client(PROXY_CN)
    if host_group == "overseas" and PROXY_OVERSEAS:
        return httpx_client(PROXY_OVERSEAS)
    return httpx_client(None)


def curl_proxy(group: str) -> str | None:
    """curl_cffi 的 proxy 参数取值。"""
    if group == "cn" and PROXY_CN:
        return PROXY_CN
    if group == "overseas" and PROXY_OVERSEAS:
        return PROXY_OVERSEAS
    return None
