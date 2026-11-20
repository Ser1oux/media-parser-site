"""简单口令鉴权：POST /api/auth 换 HttpOnly cookie，依赖函数校验所有业务接口。"""
import hmac

from fastapi import APIRouter, HTTPException, Request, Response

from .config import ACCESS_TOKEN

COOKIE_NAME = "pd_auth"


def _match(token: str) -> bool:
    return bool(ACCESS_TOKEN) and hmac.compare_digest(token or "", ACCESS_TOKEN)


def is_authorized(request: Request) -> bool:
    if not ACCESS_TOKEN:
        return True  # 未配置口令 = 开放模式（本地调试用）
    token = request.cookies.get(COOKIE_NAME) or request.headers.get("x-auth-token", "")
    return _match(token)


async def require_auth(request: Request) -> None:
    if not is_authorized(request):
        raise HTTPException(status_code=401, detail="未授权：请先输入访问口令")


router = APIRouter()


@router.post("/api/auth")
async def auth(body: dict, response: Response):
    token = (body or {}).get("token", "")
    if not ACCESS_TOKEN:
        return {"code": 200, "msg": "未启用口令"}
    if _match(token):
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=30 * 24 * 3600,
            httponly=True,
            samesite="lax",
        )
        return {"code": 200, "msg": "ok"}
    raise HTTPException(status_code=401, detail="口令错误")


@router.get("/api/auth/check")
async def auth_check(request: Request):
    return {"code": 200, "ok": is_authorized(request), "auth_required": bool(ACCESS_TOKEN)}
