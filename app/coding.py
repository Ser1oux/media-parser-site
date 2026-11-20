"""URL 安全 base64 编解码（无填充），用于把 CDN 直链塞进代理链接参数。"""
import base64


def b64e(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def b64d(s: str) -> str:
    return base64.urlsafe_b64decode((s + "=" * (-len(s) % 4)).encode("ascii")).decode("utf-8")
