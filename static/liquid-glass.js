/* Liquid Glass：按元素尺寸动态生成 SVG 位移贴图 + 玻璃材质
   ------------------------------------------------------------------
   参考 rdev/liquid-glass-react (MIT) 的滤镜管线：
     feImage(位移贴图) → feColorMatrix(边缘强度) → feComponentTransfer(边缘遮罩)
     → RGB 三通道 feDisplacementMap(色散) → feBlend(screen) → feGaussianBlur
     → feComposite(边缘遮罩) → 与干净中心 over 合成
   关键差异：官方贴图是固定尺寸的，用在小/大元素上会失真（缩成色块或拉伸撕裂），
   所以这里用 canvas 按元素实际尺寸实时生成同构位移贴图。
   ------------------------------------------------------------------ */
(function () {
  "use strict";

  var MAP_CACHE = {};
  var filters = {};
  var seq = 0;

  /* 生成位移贴图：R 通道 = X 位移，B 通道 = Y 位移，中心为 0、边缘最大 */
  function makeMap(w, h) {
    var W = Math.max(32, Math.min(512, Math.round(w)));
    var H = Math.max(32, Math.min(512, Math.round(h)));
    var key = W + "x" + H;
    if (MAP_CACHE[key]) return MAP_CACHE[key];

    var cv = document.createElement("canvas");
    cv.width = W; cv.height = H;
    var ctx = cv.getContext("2d");
    var img = ctx.createImageData(W, H);
    var data = img.data;
    var maxD = 10;                       // 最大位移（贴图内部以 128 为中点编码）
    for (var y = 0; y < H; y++) {
      for (var x = 0; x < W; x++) {
        var nx = (x / (W - 1)) * 2 - 1;   // -1..1
        var ny = (y / (H - 1)) * 2 - 1;
        // 超椭圆距离：近似圆角矩形的边缘分布
        var r = Math.pow(
          Math.pow(Math.abs(nx), 4) + Math.pow(Math.abs(ny), 4), 0.25);
        var t = Math.min(1, Math.max(0, (r - 0.55) / 0.45));  // 只在边缘 45% 区域起作用
        var k = t * t * (3 - 2 * t);      // smoothstep
        var i = (y * W + x) * 4;
        data[i] = 128 + (-nx * maxD * k) * 127 / maxD;        // R → X
        data[i + 1] = 128;                                   // G 不用
        data[i + 2] = 128 + (-ny * maxD * k) * 127 / maxD;    // B → Y
        data[i + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    var url = cv.toDataURL("image/png");
    MAP_CACHE[key] = url;
    return url;
  }

  function filterMarkup(id, mapUrl, displacement, aberration) {
    return (
      '<filter id="' + id + '" x="-25%" y="-25%" width="150%" height="150%" ' +
      'color-interpolation-filters="sRGB">' +
      '<feImage x="0" y="0" width="100%" height="100%" result="DM" href="' + mapUrl + '" ' +
      'preserveAspectRatio="none"/>' +
      '<feColorMatrix in="DM" type="matrix" result="EI" ' +
      'values="0.3 0.3 0.3 0 0  0.3 0.3 0.3 0 0  0.3 0.3 0.3 0 0  0 0 0 1 0"/>' +
      '<feComponentTransfer in="EI" result="EM">' +
      '<feFuncA type="discrete" tableValues="0 ' + (aberration * 0.05) + ' 1"/>' +
      '</feComponentTransfer>' +
      '<feOffset in="SourceGraphic" dx="0" dy="0" result="CO"/>' +
      '<feDisplacementMap in="SourceGraphic" in2="DM" scale="' + (-displacement) + '" ' +
      'xChannelSelector="R" yChannelSelector="B" result="RD"/>' +
      '<feColorMatrix in="RD" type="matrix" result="RC" ' +
      'values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0"/>' +
      '<feDisplacementMap in="SourceGraphic" in2="DM" scale="' + (-displacement - aberration * 0.05) + '" ' +
      'xChannelSelector="R" yChannelSelector="B" result="GD"/>' +
      '<feColorMatrix in="GD" type="matrix" result="GC" ' +
      'values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0"/>' +
      '<feDisplacementMap in="SourceGraphic" in2="DM" scale="' + (-displacement - aberration * 0.1) + '" ' +
      'xChannelSelector="R" yChannelSelector="B" result="BD"/>' +
      '<feColorMatrix in="BD" type="matrix" result="BC" ' +
      'values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0"/>' +
      '<feBlend in="GC" in2="BC" mode="screen" result="GB"/>' +
      '<feBlend in="RC" in2="GB" mode="screen" result="RGB"/>' +
      '<feGaussianBlur in="RGB" stdDeviation="0.3" result="AB"/>' +
      '<feComposite in="AB" in2="EM" operator="in" result="EA"/>' +
      '<feComponentTransfer in="EM" result="IM">' +
      '<feFuncA type="table" tableValues="1 0"/></feComponentTransfer>' +
      '<feComposite in="CO" in2="IM" operator="in" result="CC"/>' +
      '<feComposite in="EA" in2="CC" operator="over"/>' +
      '</filter>'
    );
  }

  var defsHost = null;

  function ensureDefs() {
    if (defsHost && defsHost.isConnected) return defsHost;
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "lg-defs");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.setAttribute("width", "0");
    svg.setAttribute("height", "0");
    var defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    svg.appendChild(defs);
    document.body.insertBefore(svg, document.body.firstChild);
    defsHost = defs;
    return defs;
  }

  /* 给一个玻璃元素绑定专属滤镜（按其尺寸生成贴图） */
  function applyTo(el) {
    if (!el || !el.isConnected) return;
    var rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    var key = Math.round(rect.width) + "x" + Math.round(rect.height);
    if (el.dataset.lgKey === key) return;
    el.dataset.lgKey = key;

    // 折射强度：贴图是按元素尺寸生成的，强度也要匹配；
    // 深色模式下再收敛一档 —— 纯色深背景上折射差异会被放大成可见的方块噪点。
    var dark = document.documentElement.getAttribute("data-theme") === "dark";
    var displacement = dark ? 5 : 14;   // 折射强度（px）
    var aberration = dark ? 1 : 1.5;    // 色散强度

    // 主题切换后强度变了，需要重建（旧的 filter 一并移除）
    if (el.dataset.lgId && filters[el.dataset.lgId]) {
      var old = document.getElementById(el.dataset.lgId);
      if (old && old.parentNode) old.parentNode.removeChild(old);
      delete filters[el.dataset.lgId];
    }
    var id = "lg-" + (++seq);
    var mapUrl = makeMap(rect.width, rect.height);
    var defs = ensureDefs();
    var holder = document.createElementNS("http://www.w3.org/2000/svg", "g");
    holder.innerHTML = filterMarkup(id, mapUrl, displacement, aberration);
    while (holder.firstChild) defs.appendChild(holder.firstChild);
    filters[id] = true;
    el.dataset.lgId = id;

    var warp = el.querySelector(".glass__warp");
    if (warp) warp.style.filter = "url(#" + id + ")";
  }

  function refresh(force) {
    if (!supportsGlass()) return;
    document.querySelectorAll(".glass__warp").forEach(function (warp) {
      var host = warp.parentElement;
      if (force && host) delete host.dataset.lgKey;   // 强制重建（主题切换后强度变了）
      applyTo(host);
    });
  }

  function supportsGlass() {
    return window.CSS && CSS.supports &&
      (CSS.supports("backdrop-filter", "blur(10px)") ||
       CSS.supports("-webkit-backdrop-filter", "blur(10px)"));
  }

  function init() {
    if (!supportsGlass()) {
      document.documentElement.classList.add("no-glass");
      return;
    }
    document.documentElement.classList.add("has-glass");
    refresh();
    window.addEventListener("resize", refresh);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.LiquidGlass = { refresh: refresh, supports: supportsGlass, makeMap: makeMap };
})();
