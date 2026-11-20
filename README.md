# parser-site · 自建媒体解析下载站

一个可以自己部署的媒体解析下载网站，支持 **小红书 / 抖音 / X (Twitter) / YouTube** 四个平台：

- **无水印**：图文原图、视频原文件
- **原画**：各平台取最高画质档（抖音 `ratio=default` / `bit_rate` 最高档；X 最高码率 mp4；YouTube 分轨原画合流）
- **图文 + 动图**：实况图（Live Photo）输出 `同编号 jpg + mp4` 对
- **画质分档**：YouTube 可切 2160p/1440p/1080p/720p，抖音与 X 提供多码率档位
- **服务端流式代理**：不落盘，服务器实时拉 CDN 转发（YouTube 音视频用 ffmpeg `-c copy` 实时合流）
- **Apple 风格界面**：Liquid Glass 真折射、深浅双主题、自托管 Inter / Noto Sans SC
- 简单口令访问，Docker Compose 一键部署，可挂 Caddy 自动签发 HTTPS 证书

> 仅限个人学习与归档使用，请尊重创作者版权，勿二次传播无水印内容。

## 特点：四个平台都不需要逆向签名算法

大部分同类项目靠逆向各平台的签名参数（`a_bogus` / `x-s-common` / `msToken` …）来抓取，平台一改版就得重写。这里走的是另一条路——**只用公开页面与公开接口**：

| 平台 | 路线 | 关键点 |
|---|---|---|
| **小红书** | 请求笔记分享页，解析页面里的 `__INITIAL_STATE__` | 无水印原图 = `fileId` 直拼原图 CDN；实况动图取 `imageList[].stream`；视频优先取 `originVideoKey` 原始文件 |
| **抖音** | ①分享页 SSR（`_ROUTER_DATA`）②无头浏览器兜底 | SSR 需要 **ttwid 二次握手**（先带 App UA 采集匿名设备 cookie，再带它请求），否则返回数据空壳；SSR 拿不到时用无头浏览器拦截 detail 接口，**读响应体是空的**（被页面 JS 消费了），要改为捕获带签名的请求 URL 重放 |
| **X** | `cdn.syndication.twimg.com/tweet-result` 公开接口 | token 由推文 ID 确定性推导（`(id/1e15)*π` 去零去点），零 cookie 零鉴权 |
| **YouTube** | yt-dlp | 提取分轨原画，ffmpeg `-c copy` 实时合流 |

代码里每个平台的坑都写了注释，值得一读。

## 快速开始

```bash
# 1. 准备配置
cp .env.example .env
vi .env        # 填 ACCESS_TOKEN 和各平台 Cookie（见下文）

# 2. 构建并启动
docker compose up -d --build

# 3. 打开 http://服务器IP:8000 ，输入口令，粘贴分享链接即可
```

## Cookie 配置（.env）

浏览器登录对应平台后，F12 → Network → 任选一个该域名请求 → 复制请求头 `Cookie` 整串粘贴到对应变量。

| 变量 | 必要性 | 说明 |
|---|---|---|
| `XHS_COOKIE` | 建议 | 含 `web_session`，显著降低风控；不配时走匿名（易 404） |
| `DOUYIN_COOKIE` | 建议 | 提升解析成功率；同时注入无头浏览器兜底 |
| `X_COOKIE` | 可选 | 公开推文不需要 |
| `YT_COOKIE` | 可选 | 仅会员/年龄限制视频需要 |

> 不配 Cookie 也能跑：公开内容匿名可解析，但被风控的概率明显更高。

## 出口代理（.env）

这是**部署前最需要考虑的一件事**：

| 服务器位置 | 必须配置 |
|---|---|
| 在中国大陆 | `PROXY_OVERSEAS` 必填（YouTube / X 直连不可达），格式 `http://host:port` |
| 在海外 | 建议 `PROXY_CN` 配住宅代理（机房 IP 解析抖音/小红书更容易被风控） |

代理出口 IP 需要**固定**：YouTube 的 `googlevideo` 等媒体直链是绑定出口 IP 的，IP 一变直链立刻 403。
若你的代理客户端用 `urltest`/负载均衡，会出现「时好时坏」的随机失败——两种解法：

```bash
# ① 代理客户端侧：把域名加进直连/固定节点
python scripts/fix_clash_route.py your-domain.example   # Clash / FlClash 自动加规则

# ② sing-box 侧：把选择器钉到单个固定节点
python scripts/pin_node.py "你的固定节点名"            # 出口 IP 恒定
```

另外：**下载必须走本站代理**。YouTube 直链绑定解析者 IP，把直链发给第三方必然 403。

## 各平台实现说明与已知限制

| 平台 | 路线 | 限制 |
|---|---|---|
| 抖音 | 分享页 SSR（ttwid 二次握手）为主，无头浏览器拦截 detail 接口兜底 | 机房 IP 可能被风控（报错提示稍后再试，**不会自动重试**——重试会重置风控窗口）；刚发布的内容可能被平台过滤 |
| 小红书 | 笔记页 `__INITIAL_STATE__`（curl_cffi TLS 伪装 Chrome） | 视频**部分笔记**平台不再暴露原始文件 key，降级网页流**可能带小红书号水印**——结果里会如实标注；无 `xsec_token` 的裸链接可能 404，请优先用 **App 分享链接** |
| X | syndication CDN 公开接口（零 cookie） | 非官方接口，字段变更时只需改 `app/parsers/twitter.py` |
| YouTube | yt-dlp 提取分轨原画 → ffmpeg `-c copy` 实时合流 | 不支持播放列表；yt-dlp 需保持新版；4K 视频轨常是 HLS manifest（ffmpeg 可直接当输入）；大文件在共享代理带宽下耗时较长 |

## API

- `POST /api/auth` `{"token": "..."}` → 口令换 cookie
- `POST /api/parse` `{"text": "分享文案"}` → 统一解析结果
  ```json
  {
    "code": 200,
    "data": {
      "platform": "douyin", "title": "...", "author": "...",
      "cover": "/api/image?u=...", "duration": 27679,
      "items": [
        {"kind": "video", "filename": "xxx.mp4", "label": "原画",
         "width": null, "height": null,
         "qualities": [
           {"label": "原画 2160p", "height": 2160,
            "download": "/api/download/mux?yt=...&q=2160&fn=..."}
         ],
         "download": "/api/download/mux?yt=...&q=2160&fn=...", "preview": null, "note": null}
      ]
    }
  }
  ```
- `GET /api/download?u=<b64url>&kind=video|image&fn=<b64文件名>` — 单媒体流式代理（Range 透传）
- `GET /api/download/mux?yt=<b64>&q=<高度上限>&fn=<b64>` — YouTube 按画质档位重新解析并实时合流（fragmented mp4）
- `GET /api/image?u=<b64url>` — 图片代理（封面/头像/图集预览）
- `GET /api/probe?u=<b64url>` — 图片宽高与体积探测（JPEG/PNG/WebP）
- `GET /api/health` — 健康检查

### 画质分档

`items[].qualities[]` 列出各档，每档自带 `download` 链接：

- **YouTube**：`q=高度上限`，下载时由服务器用 yt-dlp 重新解析该档（googlevideo 直链绑定出口 IP 且会过期，不能复用解析时的旧链）
- **抖音 / X**：每档是独立直链，直接切换即可

### 图片尺寸与体积

解析时已知宽高（小红书）直接返回；其余平台由前端懒调用 `/api/probe` 获取（JPEG/PNG/WebP 头部解析 + Content-Length）。

## 界面

- **Apple HIG 设计体系**：设计令牌取自 Apple 官方规范（颜色角色 label/fill/separator、材料层 material-*、控件度量 control-height-*、圆角阶梯），深浅双主题各自一套
- **Liquid Glass 真折射**：SVG 位移贴图 + RGB 通道色散 + 边缘遮罩。位移贴图**按元素实际尺寸实时生成**（canvas），因此按钮、卡片等任意尺寸都成立
- **自托管字体**：Inter（拉丁）+ Noto Sans SC（中文），Apple 设备自动使用原生 SF Pro / 苹方。按 unicode-range 分片，浏览器懒加载
- **八条淡出竖条**的活动指示器（Apple Activity Indicator）
- 尊重系统偏好：`prefers-color-scheme`、`prefers-reduced-motion`、`prefers-reduced-transparency`

## 配域名与 HTTPS（可选但推荐）

用 Caddy 反代，证书自动申请/续期（`tls-alpn-01` 挑战走 443，不依赖 80 端口）：

```bash
# 1. 装 Caddy
curl -sL --max-time 120 -o /tmp/caddy.tar.gz \
  https://github.com/caddyserver/caddy/releases/download/v2.10.2/caddy_2.10.2_linux_amd64.tar.gz
tar xzf /tmp/caddy.tar.gz -C /tmp && install -m 0755 /tmp/caddy /usr/local/bin/caddy

# 2. 容器端口改为只监听本机，公网 80/443 交给 Caddy
#    docker-compose.yml:  - "127.0.0.1:8080:8000"

# 3. 改好域名后写配置并重载
cp scripts/Caddyfile.template /etc/caddy/Caddyfile   # 把 example.com 换成你的域名
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

域名需先解析到服务器 IP。

> ⚠️ **每个解析到本机的子域都要在 Caddyfile 里显式配置**。Caddy 对未配置的主机名会直接 TLS 握手失败（浏览器显示「无法访问此网站」而非 404），很容易被误判成服务挂了。

## 本地开发（不用 Docker）

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m pytest tests/ -q

# 单链接冒烟
python scripts/smoke.py "https://www.douyin.com/video/..."

# 起服务
python -m uvicorn app.main:app --port 8000
```

Windows / macOS 上抖音 CDP 兜底会自动找本机 Chrome/Edge；Linux 用 Playwright 自带的 Chromium。

## 常见问题

| 报错 | 处理 |
|---|---|
| 抖音「风控拦截」 | 配 `DOUYIN_COOKIE`；给 `PROXY_CN` 换住宅 IP；等几分钟再试 |
| 抖音解析时好时坏 | ttwid 过期导致，程序会自动刷新重试；持续失败说明 IP 被风控 |
| 小红书「笔记被风控（404）」 | 配 `XHS_COOKIE`；用 App 内分享出来的链接（带 `xsec_token`）而不是裸网页链接 |
| YouTube 提取失败 | 多半 yt-dlp 版本旧了：`docker compose build --no-cache` |
| 下载失败 / 直链过期 | 重新解析即可（直链有时效） |
| 解析成功但下载 403 | 出口 IP 变了（代理切节点），见「出口代理」一节 |
| 浏览器打不开域名 | 代理客户端把域名送上海外节点了，用 `scripts/fix_clash_route.py` 加直连规则 |

## 安全说明

- 所有代理目标强制 CDN 域名白名单（防 SSRF）
- 口令用 localStorage + `x-auth-token` 头 + HttpOnly cookie 三重传递（部分浏览器会丢弃 cookie）
- 图片代理只带 UA（带 Referer/Cookie 反而被 CDN 403），视频按 CDN host 注入 Referer
- 静态资源带内容哈希版本号 + `no-cache`，避免部署后旧 JS 黏住

## License

MIT，见 [LICENSE](LICENSE)。内置字体遵循 SIL OFL 1.1，见 [NOTICE](NOTICE)。
