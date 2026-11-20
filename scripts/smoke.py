"""真实链接冒烟脚本：python scripts/smoke.py <链接> [--raw]

解析指定链接并打印统一结果 JSON（enrich 后的 download 为站内代理相对链接）。
需要先在 .env 配置好 Cookie（IG 必需 / XHS 建议 / DOUYIN 建议），且服务器可访问目标平台。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.errors import ParseError            # noqa: E402
from app.identify import identify            # noqa: E402
from app.normalize import enrich             # noqa: E402
from app.parsers import PARSERS              # noqa: E402


async def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python scripts/smoke.py <分享链接> [--json]")
        return 2
    url = sys.argv[1]
    as_json = "--json" in sys.argv[2:]

    try:
        target = identify(url)
    except ParseError as exc:
        print(f"[identify] {exc}")
        return 1
    print(f"platform={target.platform}  id={target.item_id}  kind={target.kind}")

    parser = PARSERS[target.platform]
    try:
        result = await parser.parse(target)
    except ParseError as exc:
        print(f"[parse] {exc}")
        return 1
    except Exception as exc:
        print(f"[parse] 意外异常：{type(exc).__name__}: {exc}")
        return 1

    enriched = enrich(result.model_copy(deep=True))
    payload = json.loads(enriched.model_dump_json())
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"title  : {payload['title']}")
        print(f"author : {payload['author']}  type={payload['type']}  duration={payload['duration']}")
        for item in payload["items"]:
            note = f"  ⚠ {item['note']}" if item.get("note") else ""
            print(f"  - [{item['kind']}] {item['filename']}  {item.get('label') or ''}{note}")
            print(f"    {item['download'][:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
