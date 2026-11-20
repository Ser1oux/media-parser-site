/* 解析下载站前端：Apple 风格交互
   - 主题切换（跟随系统 + localStorage 记忆）
   - 画质分档（分段控制器，切换即换下载链接）
   - 图片尺寸/体积懒探测（/api/probe）
   - 骨架屏 / 可操作错误 / Lightbox 预览 */
"use strict";

const $ = (s) => document.querySelector(s);
const inputEl = $("#input"), parseBtn = $("#parseBtn"), clearBtn = $("#clearBtn");
const errorEl = $("#error"), resultEl = $("#result"), noticeEl = $("#notice");
const gate = $("#gate"), gateErr = $("#gateErr");

const PLATFORM_LABEL = {
  douyin: "抖音", xhs: "小红书", twitter: "X", youtube: "YouTube",
};
const KIND_LABEL = { video: "视频", image: "图片", live_video: "实况动图" };

/* ---------------- 工具 ---------------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtDuration(ms) {
  if (!ms || ms <= 0) return "";
  const s = Math.round(ms / 1000);
  const m = Math.floor(s / 60), r = s % 60;
  return m ? `${m}:${String(r).padStart(2, "0")}` : `0:${String(r).padStart(2, "0")}`;
}
function fmtSize(bytes) {
  if (!bytes || bytes < 0) return "";
  if (bytes >= 1 << 30) return (bytes / (1 << 30)).toFixed(2) + " GB";
  if (bytes >= 1 << 20) return (bytes / (1 << 20)).toFixed(1) + " MB";
  if (bytes >= 1024) return Math.round(bytes / 1024) + " KB";
  return bytes + " B";
}
async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  // 口令同时用 localStorage + 请求头携带：不依赖 cookie 持久化
  // （部分浏览器/隐私模式会丢弃 cookie），服务端两者都认。
  const token = loadToken();
  if (token) headers["x-auth-token"] = token;
  const resp = await fetch(path, { credentials: "same-origin", ...opts, headers });
  let data = {};
  try { data = await resp.json(); } catch (_) { /* 非 JSON 响应 */ }
  if (!resp.ok) throw new Error(data.detail || data.msg || `HTTP ${resp.status}`);
  return data;
}

/* ---------------- 主题 ---------------- */
const themeBtn = $("#themeBtn");
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("pd_theme", t); } catch (_) {}
}
function initTheme() {
  let t = null;
  try { t = localStorage.getItem("pd_theme"); } catch (_) {}
  if (!t) {
    t = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark" : "light";
  }
  applyTheme(t);
}
themeBtn.addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  applyTheme(cur === "dark" ? "light" : "dark");
  // 深浅主题的折射强度不同 → 强制重建玻璃滤镜
  if (window.LiquidGlass) window.LiquidGlass.refresh(true);
});

/* ---------------- 口令 ---------------- */
let authenticated = false;   // 本次会话是否已通过口令（防止 checkAuth 竞态重显浮层）

function loadToken() {
  try { return localStorage.getItem("pd_token") || ""; } catch (_) { return ""; }
}
function saveToken(t) {
  try { localStorage.setItem("pd_token", t); } catch (_) {}
}

async function checkAuth() {
  const stored = loadToken();
  if (stored) {
    try {
      const d = await api("/api/auth/check");
      if (d.ok) { authenticated = true; gate.hidden = true; inputEl.focus(); return; }
    } catch (_) { /* 继续走口令流程 */ }
    try { localStorage.removeItem("pd_token"); } catch (_) {}
  }
  try {
    const d = await api("/api/auth/check");
    if (!d.auth_required || d.ok) {
      authenticated = true;
      gate.hidden = true;
      inputEl.focus();
      return;
    }
  } catch (_) { /* 服务异常时也要求口令 */ }
  if (!authenticated) {
    gate.hidden = false;
    $("#tokenInput").focus();
  }
}
async function submitToken() {
  gateErr.style.display = "none";
  const token = $("#tokenInput").value.trim();
  if (!token) return;
  try {
    await api("/api/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    saveToken(token);
    authenticated = true;
    gate.hidden = true;
    inputEl.focus();
  } catch (err) {
    gateErr.textContent = err.message;
    gateErr.style.display = "block";
  }
}
$("#tokenBtn").addEventListener("click", submitToken);
$("#tokenInput").addEventListener("keydown", (e) => { if (e.key === "Enter") submitToken(); });

/* ---------------- 解析 ---------------- */
function showError(title, msg, raw) {
  $("#errorTitle").textContent = title;
  $("#errorMsg").textContent = msg || "";
  $("#errorRaw").textContent = raw || "";
  $("#errorRaw").style.display = raw ? "block" : "none";
  errorEl.hidden = false;
}
function hideError() { errorEl.hidden = true; }

function showSkeleton() {
  resultEl.innerHTML = `
    <div class="sk-card">
      <div style="display:flex;gap:16px">
        <div class="sk sk-thumb"></div>
        <div style="flex:1">
          <div class="sk sk-line short"></div>
          <div class="sk sk-line"></div>
          <div class="sk sk-line med"></div>
        </div>
      </div>
    </div>
    <div class="sk-card">
      <div class="sk sk-line med"></div>
      <div class="sk sk-line"></div>
      <div class="sk sk-line short"></div>
    </div>`;
}

async function doParse() {
  hideError();
  const text = inputEl.value.trim();
  if (!text) { showError("请输入链接", "把分享链接或整段分享文案粘贴到输入框"); return; }
  parseBtn.disabled = true;
  parseBtn.innerHTML = '<span class="spin"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></span>解析中';
  showSkeleton();
  try {
    const data = await api("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (data.code !== 200 || !data.data) {
      resultEl.innerHTML = "";
      showError("解析失败", data.msg || "未知错误");
    } else {
      render(data.data);
      noticeEl.hidden = false;
    }
  } catch (err) {
    resultEl.innerHTML = "";
    showError("请求失败", err.message);
  } finally {
    parseBtn.disabled = false;
    parseBtn.textContent = "解析";
  }
}

/* ---------------- 渲染 ---------------- */
function render(d) {
  const badges = [`<span class="badge blue">${PLATFORM_LABEL[d.platform] || d.platform}</span>`];
  if (d.duration) badges.push(`<span class="badge">${fmtDuration(d.duration)}</span>`);
  badges.push(`<span class="badge">${d.items.length} 个文件</span>`);
  if (d.type === "image") badges.push(`<span class="badge">图文</span>`);
  badges.push(`<span class="badge green">已就绪</span>`);

  const hasVideo = d.items.some((i) => i.kind === "video" || i.kind === "live_video");
  let html = `
    <div class="card">
      <div class="chead glass glass--clear">
        <span class="glass__warp"></span>
        ${d.cover ? `<img class="cover" id="coverImg" src="${esc(d.cover)}" alt="" loading="lazy">` : ""}
        <div class="cbody">
          <div class="cbadges">${badges.join("")}</div>
          <div class="ctitle">${esc(d.title || "未命名")}</div>
          <div class="cby">${esc(d.author || "")}${d.author_id ? " · @" + esc(d.author_id) : ""}</div>
        </div>
      </div>`;

  // 画质分档：所有带 qualities 的项共用一个分段控制器
  const tiered = d.items.filter((i) => (i.qualities || []).length > 1);
  if (tiered.length) {
    const opts = tiered[0].qualities;
    html += `<div class="qbar">
      <span class="lbl">画质</span>
      <div class="seg" id="qualSeg">${opts.map((q, i) =>
        `<button data-qi="${i}" class="${i === 0 ? "on" : ""}">${esc(q.label)}</button>`).join("")}</div>
      <div class="spacer"></div>
      <button class="btn glassbtn" id="dlAll"><span class="glass__warp"></span><span class="btn-label">全部下载</span></button>
    </div>`;
  } else if (d.items.length > 1) {
    html += `<div class="qbar"><div class="spacer"></div>
      <button class="btn glassbtn" id="dlAll"><span class="glass__warp"></span><span class="btn-label">全部下载</span></button></div>`;
  }

  // 单视频内联预览
  const singleVideo = d.items.length === 1 && hasVideo ? d.items[0] : null;
  if (singleVideo && singleVideo.download) {
    html += `<video class="preview" controls preload="metadata"
               src="${esc(singleVideo.download)}&disp=inline"></video>`;
  }

  html += `<div class="rows">`;
  for (const it of d.items) {
    const tags = [];
    tags.push(`<span class="badge">${KIND_LABEL[it.kind] || it.kind}</span>`);
    if (it.kind === "live_video") tags.push(`<span class="badge orange">实况动图</span>`);
    if (it.qualities && it.qualities.length) {
      tags.push(`<span class="badge blue" data-qual-label="${d.items.indexOf(it)}">${esc(it.qualities[0].label)}</span>`);
    }
    tags.push(`<span data-dim="${d.items.indexOf(it)}"></span>`);
    if (it.size) tags.push(`<span>${fmtSize(it.size)}</span>`);
    if (it.note) tags.push(`<span class="warn">⚠ ${esc(it.note)}</span>`);

    const thumb = it.preview
      ? `<img class="thumb" src="${esc(it.preview)}" alt="" loading="lazy" data-zoom="${esc(it.preview)}">`
      : `<div class="thumb ph"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="3"/><path d="m10 9 5 3-5 3z"/></svg></div>`;

    html += `
      <div class="row">
        ${thumb}
        <div class="rmain">
          <div class="rname">${esc(it.filename)}</div>
          <div class="rmeta">${tags.join("")}</div>
        </div>
        <a class="dl" href="${esc(it.download)}" download data-item="${d.items.indexOf(it)}">下载</a>
      </div>`;
  }
  html += `</div></div>`;
  resultEl.innerHTML = html;

  bindResult(d);
  probeDimensions(d);
  if (window.LiquidGlass) window.LiquidGlass.refresh();
}

/* ---------------- 画质档位切换 ---------------- */
function bindResult(d) {
  const seg = $("#qualSeg");
  if (seg) {
    seg.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-qi]");
      if (!btn) return;
      const qi = Number(btn.dataset.qi);
      seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === btn));
      const tiered = d.items.filter((i) => (i.qualities || []).length > 1);
      tiered.forEach((item) => {
        const opt = item.qualities[qi];
        if (!opt || !opt.download) return;
        const link = resultEl.querySelector(`a.dl[data-item="${d.items.indexOf(item)}"]`);
        if (link) link.setAttribute("href", opt.download);
        const label = resultEl.querySelector(`[data-qual-label="${d.items.indexOf(item)}"]`);
        if (label) label.textContent = opt.label;
      });
    });
  }

  const dlAll = $("#dlAll");
  if (dlAll) {
    dlAll.addEventListener("click", async () => {
      const links = [...resultEl.querySelectorAll("a.dl")];
      dlAll.disabled = true;
      for (const a of links) {
        a.click();
        await new Promise((r) => setTimeout(r, 900));
      }
      dlAll.disabled = false;
    });
  }

  // 缩略图点击放大（Lightbox）
  resultEl.querySelectorAll("[data-zoom]").forEach((img) => {
    img.addEventListener("click", () => {
      const lb = document.createElement("div");
      lb.className = "lb";
      lb.innerHTML = `<img src="${esc(img.dataset.zoom)}" alt="">`;
      lb.addEventListener("click", () => lb.remove());
      document.body.appendChild(lb);
    });
  });
  const cover = $("#coverImg");
  if (cover) {
    cover.style.cursor = "zoom-in";
    cover.addEventListener("click", () => {
      const lb = document.createElement("div");
      lb.className = "lb";
      lb.innerHTML = `<img src="${esc(cover.src)}" alt="">`;
      lb.addEventListener("click", () => lb.remove());
      document.body.appendChild(lb);
    });
  }

  // 下载完成态：按钮变绿并变为「已保存」
  resultEl.querySelectorAll("a.dl").forEach((a) => {
    a.addEventListener("click", () => {
      a.classList.add("done");
      a.textContent = "已保存";
      setTimeout(() => { a.classList.remove("done"); a.textContent = "下载"; }, 2400);
    });
  });
}

/* ---------------- 图片尺寸/体积懒探测 ---------------- */
function extractRawParam(downloadLink) {
  // 下载链接里带的是原始 CDN 直链的 base64（/api/download?u=...），
  // 直接取出来交给 /api/probe，探测时服务端会自己补防盗链 Referer。
  if (!downloadLink) return "";
  const m = downloadLink.match(/[?&]u=([^&]+)/);
  return m ? m[1] : "";
}

async function probeDimensions(d) {
  const jobs = [];
  d.items.forEach((it, i) => {
    if (it.kind !== "image" && it.kind !== "live_video") return;
    const cell = resultEl.querySelector(`[data-dim="${i}"]`);
    if (!cell) return;
    if (it.width && it.height) {
      cell.textContent = `${it.width} × ${it.height}`;
      return;
    }
    const raw = extractRawParam(it.download);
    if (!raw) return;
    jobs.push({ i, raw, cell });
  });

  for (const job of jobs) {
    try {
      const r = await api(`/api/probe?u=${job.raw}`);
      const bits = [];
      if (r.width && r.height) bits.push(`${r.width} × ${r.height}`);
      if (r.size) bits.push(fmtSize(r.size));
      if (bits.length) job.cell.textContent = bits.join(" · ");
    } catch (_) { /* 探测失败不影响下载 */ }
  }
}

/* ---------------- 事件 ---------------- */
parseBtn.addEventListener("click", doParse);
clearBtn.addEventListener("click", () => {
  inputEl.value = "";
  resultEl.innerHTML = "";
  hideError();
  noticeEl.hidden = true;
  inputEl.focus();
});
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); doParse(); }
});

initTheme();
checkAuth();
