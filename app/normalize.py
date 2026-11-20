"""统一响应模型 + 文件名清洗 + 代理链接注入。"""
import re
from typing import Literal, Optional

from pydantic import BaseModel

from .coding import b64e


def sanitize_filename(name: str, fallback: str = "media") -> str:
    """清洗为安全文件名：替换非法字符、去首尾点、压缩空白、限 80 字符。
    清洗后只剩空白/下划线/点（如 "///"）时回退到 fallback。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name or "")
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    name = name[:80].strip()
    if not name or set(name) <= {"_"}:
        return fallback
    return name


class QualityOption(BaseModel):
    """一个画质档位。

    - url 非空：该档已有现成直链（抖音 / X）→ enrich 直接包装成单流代理
    - height 非空：按高度上限重新解析（YouTube，googlevideo 直链绑 IP 不可复用）
    - download 由 enrich() 填充
    """

    label: str                       # "原画 2160p" / "1080p" / "2400kbps"
    height: Optional[int] = None      # YouTube 高度上限
    url: Optional[str] = None         # 现成直链
    note: Optional[str] = None        # 补充说明（如"最高码率"）
    download: Optional[str] = None    # 站内代理相对链接（enrich 填充）


class MediaItem(BaseModel):
    """单个可下载媒体。url 为 CDN 直链（服务端解析所得）。

    - 普通媒体：download 指向 /api/download 单流代理。
    - 带档位的媒体（视频）：qualities 列出各画质，download 指向默认档（最高）。
    - YouTube 原画：yt_url 存 YouTube 页面链接，下载时由 mux 端点**重新解析**再合流——
      googlevideo 直链绑定出口 IP 且会过期，解析时固化的旧链在下载时可能 403。
    """

    kind: Literal["video", "image", "live_video"]
    url: str = ""
    ext: str = "mp4"
    filename: str
    label: Optional[str] = None      # 例：1080p H.264 / 原画 / GIF 动图
    note: Optional[str] = None       # 例：网页流可能带号水印
    size: Optional[int] = None       # 已知字节数（可选）
    thumb: Optional[str] = None      # 低清预览直链（可选，仅图集）
    width: Optional[int] = None      # 图片宽（解析时已知时填充）
    height: Optional[int] = None     # 图片高
    mux_v: Optional[str] = None      # 视频轨直链（YouTube，备用路径）
    mux_a: Optional[str] = None      # 音轨直链（YouTube，备用路径）
    yt_url: Optional[str] = None     # YouTube 页面链接（首选路径：下载时重新解析）
    qualities: list[QualityOption] = []
    # 以下由 enrich() 填充，为站内代理相对链接
    download: Optional[str] = None
    preview: Optional[str] = None


class ParseResult(BaseModel):
    platform: str
    title: str = ""
    desc: str = ""
    author: str = ""
    author_id: str = ""
    avatar: str = ""
    cover: str = ""
    type: Literal["video", "image", "mixed", "text"] = "video"
    duration: Optional[int] = None   # 毫秒
    source_url: str = ""
    items: list[MediaItem] = []


class ParseResponse(BaseModel):
    code: int
    msg: str = ""
    data: Optional[ParseResult] = None


def _mux_link(yt_url: str, height: Optional[int], filename: str) -> str:
    q = f"&q={height}" if height else ""
    return f"/api/download/mux?yt={b64e(yt_url)}{q}&fn={b64e(filename)}"


def _single_link(url: str, kind: str, filename: str) -> str:
    q = f"u={b64e(url)}&kind={kind}"
    if filename:
        q += f"&fn={b64e(filename)}"
    return f"/api/download?{q}"


def enrich(result: ParseResult) -> ParseResult:
    """把原始 CDN 直链包装成站内代理链接（返回同一实例，已就地修改）。"""
    for item in result.items:
        # 1) 有画质档位：逐档生成下载链，默认档 = 第一档（最高画质）
        if item.qualities:
            for opt in item.qualities:
                if opt.url:
                    opt.download = _single_link(opt.url, item.kind, item.filename)
                elif item.yt_url:
                    opt.download = _mux_link(item.yt_url, opt.height, item.filename)
            if item.qualities[0].download:
                item.download = item.qualities[0].download
            for opt in item.qualities:
                opt.url = ""      # 原始直链不回传前端
            item.url = ""
            item.yt_url = ""
        # 2) YouTube 无档位（退化路径）：yt_url 优先
        elif item.yt_url:
            item.download = _mux_link(item.yt_url, None, item.filename)
            item.yt_url = ""
            item.url = ""
        # 3) 旧 mux 直链路径（v/a）
        elif item.mux_v and item.mux_a:
            item.download = (
                f"/api/download/mux?v={b64e(item.mux_v)}&a={b64e(item.mux_a)}"
                f"&fn={b64e(item.filename)}"
            )
            item.mux_v = ""   # 原始直链不回传前端（googlevideo 绑定服务器 IP）
            item.mux_a = ""
            item.url = ""
        else:
            item.download = _single_link(item.url, item.kind, item.filename)

        if item.kind == "image" and item.url:
            item.preview = f"/api/image?u={b64e(item.thumb or item.url)}"
    if result.cover:
        result.cover = f"/api/image?u={b64e(result.cover)}"
    if result.avatar:
        result.avatar = f"/api/image?u={b64e(result.avatar)}"
    return result
