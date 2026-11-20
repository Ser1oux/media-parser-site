本目录包含自托管的 web 字体文件（用于在没有 SF Pro / 苹方的设备上呈现接近 Apple 的排版）：

- **Inter**（拉丁字母、数字、符号）—— Inter 是社区公认的 SF Pro 替代品
  - 版本：Inter Variable（weight 400–700），按 unicode-range 分片
  - 来源：Google Fonts / @fontsource-variable/inter
  - 许可：SIL Open Font License 1.1（见下方 LICENSE-Inter）

- **Noto Sans SC**（简体中文）—— 思源黑体，Windows/Linux 上替代苹方
  - 版本：Noto Sans SC Variable（weight 400–700），按 unicode-range 分片
  - 来源：Google Fonts / notofonts/noto-cjk
  - 许可：SIL Open Font License 1.1（见下方 LICENSE-NotoSansSC）

两套字体均为 OFL 1.1，允许自托管与再分发。分片文件由 `scripts/fetch_fonts.py` 生成
（抓取 Google Fonts CSS2 + woff2，按 unicode-range 拆分后改为本地路径，浏览器按需懒加载）。

Apple 设备上会优先使用系统原生的 SF Pro / 苹方（见 index.html 的字体栈），
仅在缺失时才回落到这里的自托管字体。
