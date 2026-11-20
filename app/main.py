"""FastAPI 入口：/api/parse 统一解析入口 + 结果缓存 + 静态前端。"""
import asyncio
import hashlib
import time
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import require_auth, router as auth_router
from .config import CACHE_TTL
from .errors import ParseError
from .identify import identify
from .normalize import ParseResponse, ParseResult, enrich
from .parsers import PARSERS
from .proxy import router as proxy_router

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR.parent / "static"


class NoCacheStatic(StaticFiles):
    """静态资源禁用缓存：部署后浏览器一定拿到新版本（否则旧 JS 会一直黏住）。"""

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp


app = FastAPI(title="parser-site", docs_url=None, redoc_url=None)
app.include_router(auth_router)
app.include_router(proxy_router)


@app.get("/", response_class=HTMLResponse)
async def index():
    """首页：给引用的静态资源打上内容哈希，强制浏览器拉新版本。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    ver = hashlib.sha1((STATIC_DIR / "app.js").read_bytes()).hexdigest()[:8]
    html = html.replace('src="app.js"', f'src="app.js?v={ver}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
async def health():
    return {"code": 200, "msg": "ok"}


class ParseBody(BaseModel):
    text: str = ""


_cache: dict[str, tuple[float, ParseResult]] = {}
_cache_lock = asyncio.Lock()
_CACHE_MAX = 300


async def _cache_get(key: str) -> ParseResult | None:
    async with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        if hit:
            _cache.pop(key, None)
    return None


async def _cache_put(key: str, result: ParseResult) -> None:
    async with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            now = time.time()
            for k in [k for k, v in _cache.items() if v[0] <= now]:
                _cache.pop(k, None)
            while len(_cache) >= _CACHE_MAX:
                _cache.pop(next(iter(_cache)))
        _cache[key] = (time.time() + CACHE_TTL, result)


@app.post("/api/parse", response_model=ParseResponse)
async def api_parse(body: ParseBody, _: None = Depends(require_auth)) -> ParseResponse:
    text = (body.text or "").strip()
    if not text:
        return ParseResponse(code=400, msg="请粘贴分享链接或文案")

    try:
        target = identify(text)
    except ParseError as exc:
        return ParseResponse(code=400, msg=str(exc))

    key = hashlib.sha1(f"{target.platform}|{target.item_id or target.url}".encode()).hexdigest()
    cached = await _cache_get(key)
    if cached:
        return ParseResponse(code=200, msg="ok（缓存）", data=enrich(cached.model_copy(deep=True)))

    parser = PARSERS.get(target.platform)
    if parser is None:
        return ParseResponse(code=400, msg=f"平台 {target.platform} 未实现")

    try:
        result = await parser.parse(target)
    except ParseError as exc:
        return ParseResponse(code=422, msg=str(exc))
    except Exception as exc:  # 解析器意外异常：不透传堆栈，只给类型与摘要
        return ParseResponse(code=500, msg=f"解析异常：{type(exc).__name__}: {str(exc)[:200]}")

    await _cache_put(key, result)
    return ParseResponse(code=200, msg="ok", data=enrich(result.model_copy(deep=True)))


# 静态前端挂在最后，API 路由优先匹配
app.mount("/", NoCacheStatic(directory=str(STATIC_DIR), html=True), name="static")
