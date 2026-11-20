"""媒体代理：/api/download 单流代理、/api/download/mux YouTube 实时合流、/api/image 图片代理。

工程要点（来自参考仓库实测经验）：
- 按 CDN host 注入防盗链 Referer（视频需要；图片只带 UA，带 Referer/Cookie 反而 403，
  403 时自动补 Referer 重试一次）。
- 透传 Range/206（浏览器拖进度必需）。
- 抖音视频 CDN 会随机返回 ftyp box 长度首字节被混淆（00→01）的 MP4，流式转发时检测修复。
- 首字节/空闲双超时，绝不能用总超时一刀切掐断大文件传输。
- SSRF 防护：代理目标 host 必须命中平台 CDN 白名单。
"""
import asyncio
import shutil
import time
from typing import AsyncIterator
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .coding import b64d
from .http_client import DESKTOP_UA, client_for

router = APIRouter()

# ---- CDN host 白名单（后缀匹配） ----
ALLOWED_SUFFIXES = (
    # 抖音
    "douyin.com", "iesdouyin.com", "snssdk.com", "douyinvod.com",
    "douyinpic.com", "zjcdn.com", "bytecdn.cn", "byteimg.com", "bytedance.com",
    # 小红书
    "xhscdn.com", "xiaohongshu.com",
    # X
    "twimg.com",
    # YouTube
    "googlevideo.com", "ytimg.com",
)

# 视频代理需要 Referer 的 CDN（图片代理一律只带 UA）
REFERER_BY_SUFFIX = {
    "douyinvod.com": "https://www.douyin.com/",
    "snssdk.com": "https://www.douyin.com/",
    "iesdouyin.com": "https://www.douyin.com/",
    "douyin.com": "https://www.douyin.com/",
    "xhscdn.com": "https://www.xiaohongshu.com/",
    "googlevideo.com": "https://www.youtube.com/",
}

# 抖音视频 CDN（ftyp 修复适用域）
_DOUYIN_VIDEO_SUFFIXES = ("douyinvod.com", "zjcdn.com", "snssdk.com", "iesdouyin.com")

# 出口代理归组
_CN_SUFFIXES = ("douyin.com", "iesdouyin.com", "snssdk.com", "douyinvod.com",
                "douyinpic.com", "zjcdn.com", "bytecdn.cn", "byteimg.com", "xhscdn.com")
_OVERSEAS_SUFFIXES = ("googlevideo.com", "ytimg.com", "twimg.com")


def host_matches(host: str, suffixes) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def host_allowed(host: str) -> bool:
    return bool(host) and host_matches(host, ALLOWED_SUFFIXES)


def referer_for(host: str) -> str | None:
    for suffix, ref in REFERER_BY_SUFFIX.items():
        if host_matches(host, (suffix,)):
            return ref
    return None


def host_group(host: str) -> str:
    if host_matches(host, _CN_SUFFIXES):
        return "cn"
    if host_matches(host, _OVERSEAS_SUFFIXES):
        return "overseas"
    return "direct"


def fix_ftyp(buf: bytes) -> bytes:
    """修复抖音 CDN 的 ftyp 混淆：合法 MP4 的 ftyp box 长度首字节必为 0x00，
    被混淆成 0x01 会导致播放器/剪辑软件无法解析。"""
    if len(buf) >= 8 and buf[4:8] == b"ftyp" and buf[0] == 0x01:
        return b"\x00" + buf[1:]
    return buf


def _disposition(kind: str, filename: str) -> str:
    mode = "attachment" if kind == "download" else "inline"
    if not filename:
        return mode
    return f"{mode}; filename*=UTF-8''{quote(filename)}"


async def _proxy_stream(request: Request, url: str, kind: str, disp: str, fn: str,
                        cache_seconds: int = 0) -> StreamingResponse:
    host = urlparse(url).hostname or ""
    if not host_allowed(host):
        raise HTTPException(status_code=400, detail="目标域名不在允许的 CDN 白名单内")

    base_headers = {
        "User-Agent": DESKTOP_UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    ref = referer_for(host)
    # 图片只带 UA；视频/未知类型按 CDN 注入 Referer
    if kind != "image" and ref:
        base_headers["Referer"] = ref
    if request.headers.get("range"):
        base_headers["Range"] = request.headers["range"]

    client = client_for(host_group(host))

    resp: httpx.Response | None = None
    last_err: Exception | None = None
    for attempt in range(2):  # 连接级失败退避重试一次
        try:
            req = client.build_request("GET", url, headers=base_headers)
            resp = await client.send(req, stream=True)
            break
        except httpx.HTTPError as e:  # 连接重置/DNS/超时
            last_err = e
            if attempt == 0:
                await asyncio.sleep(0.6)
    if resp is None:
        raise HTTPException(status_code=502, detail=f"上游连接失败：{type(last_err).__name__}")

    # 图片被 403 → 补 Referer 重试一次（防盗链兜底）
    if resp.status_code == 403 and kind == "image" and ref:
        await resp.aclose()
        retry_headers = dict(base_headers)
        retry_headers["Referer"] = ref
        try:
            resp = await client.send(
                client.build_request("GET", url, headers=retry_headers), stream=True)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"上游连接失败：{type(e).__name__}")

    if resp.status_code in (404, 410):
        await resp.aclose()
        raise HTTPException(status_code=404, detail="直链已过期或不存在，请重新解析")
    if resp.status_code == 403:
        await resp.aclose()
        raise HTTPException(status_code=403, detail="上游拒绝访问（403），直链可能已失效，请重新解析")
    if resp.status_code >= 400:
        await resp.aclose()
        raise HTTPException(status_code=502, detail=f"上游返回 {resp.status_code}")

    needs_ftyp = kind == "video" and host_matches(host, _DOUYIN_VIDEO_SUFFIXES)

    async def gen() -> AsyncIterator[bytes]:
        first = True
        try:
            async for chunk in resp.aiter_bytes(65536):
                if first:
                    first = False
                    if needs_ftyp:
                        chunk = fix_ftyp(chunk)
                    if not chunk:
                        continue
                yield chunk
        except httpx.HTTPError:
            # 传输中断（客户端断开或上游抖动），不再重试大流
            return
        finally:
            await resp.aclose()

    headers = {
        "Content-Disposition": _disposition(disp, fn),
        "Accept-Ranges": resp.headers.get("accept-ranges", "bytes"),
    }
    if resp.headers.get("content-length"):
        headers["Content-Length"] = resp.headers["content-length"]
    if resp.headers.get("content-range"):
        headers["Content-Range"] = resp.headers["content-range"]
    if cache_seconds:
        headers["Cache-Control"] = f"public, max-age={cache_seconds}"

    media_type = resp.headers.get("content-type") or (
        "image/jpeg" if kind == "image" else "video/mp4"
    )
    media_type = media_type.split(";")[0].strip() or media_type
    return StreamingResponse(
        gen(), status_code=resp.status_code, media_type=media_type, headers=headers
    )


# ---- 图片尺寸/体积探测（前端懒加载，不拖慢解析） ----

def _parse_image_size(data: bytes) -> tuple[int | None, int | None]:
    """从图片头部字节解析宽高：支持 JPEG / PNG / WebP。"""
    if len(data) < 24:
        return None, None
    # PNG: 8 字节签名 + IHDR(width,height 大端)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    # WebP: RIFF....WEBP + VP8X / VP8 / VP8L
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            w = int.from_bytes(data[24:27], "little") + 1
            h = int.from_bytes(data[27:30], "little") + 1
            return w, h
        if chunk == b"VP8 " and len(data) >= 30:
            w = int.from_bytes(data[26:28], "little") & 0x3FFF
            h = int.from_bytes(data[28:30], "little") & 0x3FFF
            return w, h
        if chunk == b"VP8L" and len(data) >= 25:
            b = int.from_bytes(data[21:25], "little")
            return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
        return None, None
    # JPEG: 扫描 SOFn 标记
    if data[:2] == b"\xff\xd8":
        i = 2
        n = len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = int.from_bytes(data[i + 2:i + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h = int.from_bytes(data[i + 5:i + 7], "big")
                w = int.from_bytes(data[i + 7:i + 9], "big")
                return w, h
            i += 2 + max(seg_len, 2)
        return None, None
    return None, None


@router.get("/api/probe")
async def probe(u: str):
    """探测图片宽高与体积：读首 64KB 解析尺寸 + HEAD 拿 Content-Length。"""
    try:
        url = b64d(u)
    except Exception:
        raise HTTPException(status_code=400, detail="参数解码失败")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="非法链接")
    host = urlparse(url).hostname or ""
    if not host_allowed(host):
        raise HTTPException(status_code=400, detail="目标域名不在允许的 CDN 白名单内")

    headers = {"User-Agent": DESKTOP_UA, "Accept": "*/*"}
    ref = referer_for(host)
    if ref:
        headers["Referer"] = ref
    client = client_for(host_group(host))

    width = height = None
    size = None
    try:
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail="上游返回错误")
            cl = resp.headers.get("content-length")
            if cl and cl.isdigit():
                size = int(cl)
            buf = b""
            async for chunk in resp.aiter_bytes(65536):
                buf += chunk
                if len(buf) >= 65536:
                    break
            width, height = _parse_image_size(buf)
            if size is None:
                size = len(buf) if buf else None
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"探测失败：{type(exc).__name__}")

    return {"code": 200, "width": width, "height": height, "size": size}


@router.get("/api/download")
async def download(request: Request, u: str, kind: str = "video",
                   fn: str = "", disp: str = "attachment"):
    try:
        url = b64d(u)
        filename = b64d(fn) if fn else ""
    except Exception:
        raise HTTPException(status_code=400, detail="参数解码失败")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="非法链接")
    return await _proxy_stream(request, url, kind, disp, filename)


@router.get("/api/image")
async def image_proxy(request: Request, u: str):
    try:
        url = b64d(u)
    except Exception:
        raise HTTPException(status_code=400, detail="参数解码失败")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="非法链接")
    return await _proxy_stream(request, url, "image", "inline", "", cache_seconds=3600)


# ---- YouTube 音视频实时合流（googlevideo 直链绑定服务器 IP，必须本机 ffmpeg 封装） ----

def _ffmpeg_cmd(v_url: str, a_url: str, strict_opus: bool,
                v_headers: str | None = None, a_headers: str | None = None) -> list[str]:
    from .config import PROXY_OVERSEAS

    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    default_headers = f"User-Agent: {DESKTOP_UA}\r\nReferer: https://www.youtube.com/\r\n"
    v_headers = v_headers or default_headers
    a_headers = a_headers or default_headers
    # Debian 13 的 ffmpeg 7.1 用 -http_proxy（不是 -proxy）
    proxy_args = ["-http_proxy", PROXY_OVERSEAS] if PROXY_OVERSEAS else []
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-headers", v_headers, *proxy_args, "-i", v_url,
        "-headers", a_headers, *proxy_args, "-i", a_url,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c", "copy",
        "-movflags", "frag_keyframe+empty_moov",
        "-f", "mp4", "pipe:1",
    ]
    if strict_opus:
        cmd.insert(1, "-strict")
        cmd.insert(2, "-2")
    return cmd


def _yt_headers(format_info: dict | None) -> str | None:
    """取 yt-dlp 为该格式生成的 http_headers（googlevideo 签名与 UA 绑定，必须一致）。"""
    if not format_info:
        return None
    headers = format_info.get("http_headers") or {}
    if not headers:
        return None
    lines = []
    for key, value in headers.items():
        if key.lower() in ("user-agent", "referer", "origin", "accept", "accept-language"):
            lines.append(f"{key}: {value}")
    return "\r\n".join(lines) + "\r\n" if lines else None


# 下载时重新解析 YouTube（60s 缓存，避免预览+下载重复解析）
_yt_resolve_cache: dict[str, tuple[float, str, str, str | None, str | None]] = {}


async def _resolve_youtube(yt_url: str, height_cap: int | None = None
                           ) -> tuple[str, str, str | None, str | None]:
    """返回 (视频流 URL, 音频流 URL, 视频 headers, 音频 headers)。

    height_cap 给定时按高度上限选流（画质分档），否则取最高画质。
    """
    now = time.time()
    ck = f"{yt_url}|{height_cap or 'max'}"
    hit = _yt_resolve_cache.get(ck)
    if hit and hit[0] > now:
        return hit[1], hit[2], hit[3], hit[4]

    from .parsers.youtube import _extract_sync, _pick_formats

    opts: dict = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "noplaylist": True, "socket_timeout": 20, "retries": 3,
    }
    if height_cap:
        # 画质分档：视频轨限高，音频轨不限；退化到 mp4 单流
        opts["format"] = (f"bv*[height<={height_cap}]+ba/"
                          f"b[height<={height_cap}]/bv*[height<={height_cap}]+ba")
    from .config import PROXY_OVERSEAS, YT_COOKIE
    if PROXY_OVERSEAS:
        opts["proxy"] = PROXY_OVERSEAS
    if YT_COOKIE:
        opts["http_headers"] = {"Cookie": YT_COOKIE}

    try:
        info = await asyncio.to_thread(_extract_sync, yt_url, opts)
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"YouTube 重新解析失败：{str(exc)[:150]}")
    video_fmt, audio_fmt, prog_fmt = _pick_formats(info.get("formats") or [])
    if video_fmt and audio_fmt:
        v_url = video_fmt.get("url") or ""
        a_url = audio_fmt.get("url") or ""
        v_hdr = _yt_headers(video_fmt)
        a_hdr = _yt_headers(audio_fmt)
    elif prog_fmt:
        # 没有分轨（罕见）：单流直接透传，不做合流
        return prog_fmt.get("url") or "", "", None, None
    else:
        raise HTTPException(status_code=502, detail="YouTube 未找到可下载格式")
    if not v_url or not a_url:
        raise HTTPException(status_code=502, detail="YouTube 格式解析不完整")
    _yt_resolve_cache[ck] = (now + 60, v_url, a_url, v_hdr, a_hdr)
    return v_url, a_url, v_hdr, a_hdr


@router.get("/api/download/mux")
async def download_mux(request: Request, v: str = "", a: str = "", fn: str = "",
                       yt: str = "", q: str = ""):
    filename = ""
    try:
        if yt:
            # 首选路径：下载时重新解析（解析时固化的 googlevideo 直链会过期/403）
            yt_url = b64d(yt)
            filename = b64d(fn) if fn else ""
            height_cap = int(q) if q.isdigit() and int(q) > 0 else None
            v_url, a_url, v_hdr, a_hdr = await _resolve_youtube(yt_url, height_cap)
        else:
            v_url, a_url = b64d(v), b64d(a)
            filename = b64d(fn) if fn else ""
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="参数解码失败")

    for url in (v_url, a_url):
        if not url:
            continue
        host = urlparse(url).hostname or ""
        if not (host_matches(host, ("googlevideo.com", "manifest.googlevideo.com"))
                or host.endswith(".youtube.com")):
            raise HTTPException(status_code=400, detail="合流端点仅支持 YouTube 直链")

    # 单流（无分轨）：直接透传字节流
    if not a_url:
        return await _proxy_stream(request, v_url, "video", "download", filename)

    proc = await asyncio.create_subprocess_exec(
        *_ffmpeg_cmd(v_url, a_url, strict_opus=False, v_headers=v_hdr, a_headers=a_hdr),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=1 << 20,
    )

    # 先等第一块输出：ffmpeg 早期失败（403/格式问题）能如实报错，而不是吐 200 空文件
    try:
        first = await asyncio.wait_for(proc.stdout.read(256 * 1024), timeout=45)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(status_code=504, detail="ffmpeg 合流超时")
    if not first:
        stderr = (await proc.stderr.read() or b"").decode("utf-8", "ignore")[-400:]
        await proc.wait()
        if "Opus" in stderr or "opus" in stderr:
            # opus 入 mp4 需要严格实验标志，换参数重来一次
            proc2 = await asyncio.create_subprocess_exec(
                *_ffmpeg_cmd(v_url, a_url, strict_opus=True, v_headers=v_hdr, a_headers=a_hdr),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=1 << 20,
            )
            try:
                first = await asyncio.wait_for(proc2.stdout.read(256 * 1024), timeout=45)
            except asyncio.TimeoutError:
                proc2.kill()
                raise HTTPException(status_code=504, detail="ffmpeg 合流超时")
            if not first:
                proc2.kill()
                raise HTTPException(status_code=502, detail="ffmpeg 合流失败（音频编码无法入 mp4）")
            proc = proc2
        else:
            raise HTTPException(status_code=502, detail=f"ffmpeg 合流失败：{stderr or '无输出'}")

    async def gen() -> AsyncIterator[bytes]:
        try:
            yield first
            while True:
                chunk = await proc.stdout.read(256 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            if proc.returncode is None:
                proc.kill()

    return StreamingResponse(
        gen(),
        media_type="video/mp4",
        headers={"Content-Disposition": _disposition("download", filename)},
    )
