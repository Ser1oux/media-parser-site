#!/usr/bin/env python3
"""给 Clash / FlClash 添加自建域名的直连规则。

为什么需要：自建站点部署在境内服务器时，通用顶级域（.cloud / .top / .xyz 等）
会被 Clash 的 `geolocation-!cn` 规则送上海外节点，浏览器表现为
「似乎 xx 关闭了连接」（ERR_CONNECTION_CLOSED），但服务器本身完全正常。

改两处：
1) shared_preferences.json 的 networkProps.bypassDomain（FlClash 应用级设置，
   更新订阅不会被覆盖）
2) config.yaml 运行配置：rules 顶部加 DIRECT 规则 + dns.fake-ip-filter 加一条

用法：
    python fix_clash_route.py <域名>              # 写入
    python fix_clash_route.py <域名> --dry-run    # 只预览不写
    python fix_clash_route.py <域名> --dir <路径> # 指定配置目录

⚠️ 重要：**不要在 FlClash 运行时用 taskkill 强杀它**——FlClash 是系统代理，
   强杀后所有流量指向无人监听的端口，整个网络会断。
   要让它重新读取配置，请从 FlClash 界面里正常退出/重启，
   或先关掉「系统代理」开关再操作。
"""
import argparse
import json
import os
import re
from pathlib import Path


def find_config_dir(explicit: str | None) -> Path:
    """定位 FlClash / Clash 的配置目录（跨平台，自动探测）。"""
    if explicit:
        return Path(explicit)
    home = Path.home()
    candidates = [
        home / "AppData/Roaming/com.follow/clash",   # FlClash (Windows)
        home / "AppData/Roaming/io.github.clash-verge-rev",
        home / "AppData/Roaming/clash-verge",
        home / ".config/clash",
        home / ".config/mihomo",
        home / ".config/flclash",
    ]
    for c in candidates:
        if c.is_dir() and (c / "config.yaml").exists():
            return c
    appdata = os.environ.get("APPDATA")
    if appdata:
        for sub in ("com.follow/clash", "io.github.clash-verge-rev", "clash-verge"):
            c = Path(appdata) / sub
            if c.is_dir():
                return c
    raise SystemExit("找不到 Clash/FlClash 配置目录，请用 --dir 指定")


def patch_prefs(base: Path, domains: list[str], dry: bool) -> None:
    """FlClash：shared_preferences.json → flutter.config → networkProps.bypassDomain"""
    prefs = base / "shared_preferences.json"
    if not prefs.exists():
        print(f"[1] 跳过：{prefs} 不存在（非 FlClash 或无应用级设置）")
        return
    outer = json.loads(prefs.read_text(encoding="utf-8"))
    if "flutter.config" not in outer:
        print("[1] 跳过：shared_preferences.json 结构不认识")
        return
    inner = json.loads(outer["flutter.config"])
    net = inner.get("networkProps")
    if not isinstance(net, dict):
        print("[1] 跳过：没有 networkProps")
        return
    bypass = net.setdefault("bypassDomain", [])
    added = [d for d in domains if d not in bypass]
    if dry:
        print(f"[1] [dry-run] 将向 bypassDomain 添加 {added}")
        return
    bypass.extend(added)
    outer["flutter.config"] = json.dumps(inner, ensure_ascii=False)
    prefs.write_text(json.dumps(outer, ensure_ascii=False), encoding="utf-8")
    print(f"[1] bypassDomain 已更新（新增 {added or '无'}），现共 {len(bypass)} 条")


def patch_config(base: Path, domain: str, dry: bool) -> None:
    """运行配置：rules 顶部加 DIRECT + dns.fake-ip-filter"""
    cfg = base / "config.yaml"
    if not cfg.exists():
        print(f"[2] 跳过：{cfg} 不存在")
        return
    text = cfg.read_text(encoding="utf-8")

    rule = f"  - 'DOMAIN-SUFFIX,{domain},DIRECT'\n"
    if f"DOMAIN-SUFFIX,{domain}," not in text:
        text = re.sub(r"(rules:\n)", r"\1" + rule, text, count=1)
        print("[2] rules 顶部已插入 DIRECT 规则")
    else:
        print("[2] rules 已存在该规则")

    flt = f"    - '+.{domain}'\n"
    if f"+.{domain}" not in text:
        m = re.search(r"(fake-ip-filter:\n)", text)
        if m:
            text = text[: m.end()] + flt + text[m.end():]
            print("[2] dns.fake-ip-filter 已插入")
        else:
            print("[2] !! 未找到 fake-ip-filter 段，需手动处理")
    else:
        print("[2] fake-ip-filter 已存在")

    if dry:
        print("[2] [dry-run] 未写入")
        return
    cfg.write_text(text, encoding="utf-8")
    print("[2] config.yaml 已保存")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain", help="要直连的域名，如 example.com")
    ap.add_argument("--dir", dest="dir", help="Clash 配置目录（默认自动探测）")
    ap.add_argument("--dry-run", action="store_true", help="只预览不写入")
    args = ap.parse_args()

    domain = args.domain.strip().lstrip("*.")
    if not domain or "/" in domain or " " in domain:
        raise SystemExit("域名格式不对")

    base = find_config_dir(args.dir)
    print(f"配置目录: {base}")
    patch_prefs(base, [domain, f"*.{domain}"], args.dry_run)
    patch_config(base, domain, args.dry_run)
    print("\n完成。重启 Clash/FlClash（用界面正常重启，别强杀进程）后生效。")


if __name__ == "__main__":
    main()
