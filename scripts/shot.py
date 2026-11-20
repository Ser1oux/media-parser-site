"""用无头 Chromium 给站点截图（独立于内置浏览器的渲染实现）。

用法：
    export PARSER_TOKEN=<访问口令>
    python3 scripts/shot.py "<分享链接>" [light|dark] [宽度]
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

URL = os.environ.get("PARSER_URL", "http://127.0.0.1:8000/")
TOKEN = os.environ.get("PARSER_TOKEN", "")
TEXT = sys.argv[1] if len(sys.argv) > 1 else (
    "https://www.xiaohongshu.com/discovery/item/6afac4ae000000002b030fee"
    "?source=webshare&xhsshare=pc_web"
    "&xsec_token=ABq98M1nUNpBRvbuaz33sPrUM0XnQGOubW6pB78kLxBEg=&xsec_source=pc_share"
)
THEME = sys.argv[2] if len(sys.argv) > 2 else "light"
WIDTH = int(sys.argv[3]) if len(sys.argv) > 3 else 900


async def main():
    if not TOKEN:
        print("提示: 未设置 PARSER_TOKEN 环境变量（无口令的站点可忽略）")
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await browser.new_context(
            viewport={"width": WIDTH, "height": 1100}, device_scale_factor=2,
            locale="zh-CN",
        )
        if TOKEN:
            host = URL.split("//", 1)[1].split("/", 1)[0].split(":", 1)[0]
            await ctx.add_cookies([{
                "name": "pd_auth", "value": TOKEN, "domain": host,
                "path": "/", "httpOnly": True,
            }])
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded")
        await page.evaluate(f"localStorage.setItem('pd_token', '{TOKEN}')")
        await page.evaluate(f"localStorage.setItem('pd_theme', '{THEME}')")
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(1200)

        gate_hidden = await page.evaluate(
            "document.getElementById('gate').hasAttribute('hidden')")
        print("gate hidden:", gate_hidden)

        await page.fill("#input", TEXT)
        await page.click("#parseBtn")
        await page.wait_for_timeout(9000)

        info = await page.evaluate("""() => ({
            rows: document.querySelectorAll('.row').length,
            dims: [...document.querySelectorAll('[data-dim]')].map(e => e.textContent),
            badges: [...document.querySelectorAll('.cbadges .badge')].map(e => e.textContent.trim()),
            err: document.getElementById('errorMsg').textContent,
            seg: [...document.querySelectorAll('#qualSeg button')].map(e => e.textContent),
        })""")
        print("state:", info)

        name = f"/tmp/shot_{THEME}_{WIDTH}.png"
        await page.screenshot(path=name, full_page=True)
        print("saved:", name)
        await browser.close()


asyncio.run(main())
