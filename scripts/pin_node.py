"""把 sing-box 的代理选择器钉到单个固定出口节点。

为什么需要：urltest/fallback 组会按延迟或目标切换节点，导致出口 IP 漂移；
而 YouTube 的 googlevideo 等媒体直链是**绑定出口 IP** 的，IP 一变直链就 403。

用法：
    python pin_node.py <节点名> [--config /etc/sing-box/config.json]
"""
import json
import sys

PATH = "/etc/sing-box/config.json"
if "--config" in sys.argv:
    PATH = sys.argv[sys.argv.index("--config") + 1]
if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
    sys.exit("用法: python pin_node.py <节点名> [--config <路径>]")
TARGET = sys.argv[1]
cfg = json.load(open(PATH))

changed = False
for o in cfg["outbounds"]:
    if o.get("tag") == "Proxy" and o.get("type") == "selector":
        if o.get("default") != TARGET:
            o["default"] = TARGET
            changed = True
        # 让选择器只暴露固定节点，避免误切
        fixed_nodes = [t for t in (o.get("outbounds") or []) if t.startswith(TARGET)]
        if fixed_nodes:
            o["outbounds"] = fixed_nodes

json.dump(cfg, open(PATH, "w"), ensure_ascii=False)
print("pinned Proxy ->", TARGET, "| changed:", changed)

check = subprocess.run(["/usr/local/bin/sing-box", "check", "-c", PATH],
                       capture_output=True, text=True)
print("check rc:", check.returncode, (check.stderr or "").strip()[:200])
if check.returncode == 0:
    subprocess.run(["systemctl", "restart", "sing-box"], check=False)
    print("restarted")
