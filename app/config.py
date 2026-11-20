"""环境配置。优先读真实环境变量（Docker compose 注入），其次读项目根目录 .env。"""
import os
from pathlib import Path


def _load_dotenv() -> None:
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_dotenv()


def _get(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


# ---- 访问控制 ----
ACCESS_TOKEN: str = _get("ACCESS_TOKEN")

# ---- 各平台 Cookie（浏览器里复制 "Cookie" 请求头整串即可） ----
DOUYIN_COOKIE: str = _get("DOUYIN_COOKIE")
XHS_COOKIE: str = _get("XHS_COOKIE")
X_COOKIE: str = _get("X_COOKIE")
YT_COOKIE: str = _get("YT_COOKIE")

# ---- 分平台出口代理 ----
# 国内平台（抖音/小红书）走 PROXY_CN；海外平台（YouTube/X）走 PROXY_OVERSEAS。
# 服务器在国内时海外两平台必须配 PROXY_OVERSEAS，否则不可达。
PROXY_CN: str | None = _get("PROXY_CN") or None
PROXY_OVERSEAS: str | None = _get("PROXY_OVERSEAS") or None

# 解析结果内存缓存秒数
CACHE_TTL: int = int(_get("CACHE_TTL", "600") or 600)
