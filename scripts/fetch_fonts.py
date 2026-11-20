"""从 Google Fonts 抓取 woff2 分片并自托管（避开 GFW，浏览器按 unicode-range 懒加载）。

在服务器上执行（需要 sing-box 代理出口）。
产出：/opt/parser-site/static/fonts/<name>.css + 同目录的 woff2 分片
"""
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

OUT = Path("/opt/parser-site/static/fonts")
OUT.mkdir(parents=True, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")

FAMILIES = [
    ("Inter", "Inter:wght@400..700"),
    ("NotoSansSC", "Noto+Sans+SC:wght@400..700"),
]

PROXY = "http://127.0.0.1:7890"   # 服务器上的 sing-box


def curl(url: str, dest: Path | None = None) -> bytes:
    cmd = ["curl", "-sL", "--max-time", "60", "-A", UA]
    if dest:
        cmd += ["-o", str(dest)]
        subprocess.run(cmd + [url], check=False)
        return dest.read_bytes() if dest.exists() else b""
    cmd += [url]
    return subprocess.run(cmd, capture_output=True).stdout


def main():
    css_parts = []
    total = 0
    for label, fam in FAMILIES:
        css = curl(f"https://fonts.googleapis.com/css2?family={fam}&display=swap").decode("utf-8")
        if not css.strip():
            print(f"!! {label}: CSS 获取失败")
            continue
        blocks = re.findall(r"/\*\s*([^*]+?)\s*\*/\s*(@font-face\s*\{.*?\})", css, re.S)
        print(f"== {label}: {len(blocks)} 个分片")
        for subset, block in blocks:
            m = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", block)
            if not m:
                continue
            url = m.group(1)
            fname = hashlib.sha1(url.encode()).hexdigest()[:12] + ".woff2"
            dest = OUT / fname
            if not dest.exists():
                data = curl(url, dest)
                if not data:
                    print(f"   下载失败 {subset}")
                    dest.unlink(missing_ok=True)
                    continue
            total += dest.stat().st_size
            block = block.replace(url, f"./{fname}")
            block = re.sub(r"url\(\./([0-9a-f]+\.woff2)\)",
                           r"url(./\1)", block)
            css_parts.append(f"/* {label} {subset} */\n{block}")
        print(f"   累计 {total/1024:.0f} KB")

    (OUT / "fonts.css").write_text("\n".join(css_parts), encoding="utf-8")
    n = len(list(OUT.glob("*.woff2")))
    print(f"完成：{n} 个 woff2，{total/1024/1024:.1f} MB，CSS {OUT/'fonts.css'}")


main()
