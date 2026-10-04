// Minimal SVG line chart: one y axis, thin 2px lines, >=8px markers, crosshair tooltip.
import { esc } from "./common.js";

const NS = "http://www.w3.org/2000/svg";

export function lineChart(container, opts) {
  const {
    series, xs, yDomain = [0, 1], yTicks, yFormat = (v) => v, xFormat = (v) => v,
    xLabel = "", yLabel = "", refLines = [], logY = false, height = 260, width = 640,
  } = opts;
  container.innerHTML = "";
  container.classList.add("chart");
  const avail = Math.round(container.clientWidth || width);
  if (!container._ro) {
    container._ro = new ResizeObserver(() => {
      if (Math.abs((container.clientWidth || 0) - container._w) > 24) lineChart(container, container._opts);
    });
    container._ro.observe(container);
  }
  container._opts = opts;
  container._w = avail;
  const m = { l: 48, r: directLabelWidth(series), t: 12, b: 38 };
  const W = Math.max(300, avail), H = height;
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const x0 = xs[0], x1 = xs[xs.length - 1];
  const sx = (x) => m.l + ((x - x0) / Math.max(1e-9, x1 - x0)) * iw;
  const [ylo, yhi] = yDomain;
  const tf = logY ? Math.log10 : (v) => v;
  const sy = (y) => m.t + ih - ((tf(y) - tf(ylo)) / (tf(yhi) - tf(ylo))) * ih;
  const clampY = (y) => Math.min(yhi, Math.max(ylo, y));

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.ariaLabel || yLabel });
  // grid + y ticks
  const ticks = yTicks || niceTicks(ylo, yhi, 5);
  for (const t of ticks) {
    svg.append(el("line", { class: "gridline", x1: m.l, x2: m.l + iw, y1: sy(t), y2: sy(t) }));
    svg.append(text(m.l - 6, sy(t) + 4, yFormat(t), { "text-anchor": "end", class: "num" }));
  }
  svg.append(el("line", { class: "axis", x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih }));
  const step = xs.length > 14 ? 2 : 1;
  xs.forEach((x, i) => {
    if (i % step) return;
    svg.append(text(sx(x), m.t + ih + 16, xFormat(x), { "text-anchor": "middle", class: "num" }));
  });
  if (xLabel) svg.append(text(m.l + iw / 2, H - 4, xLabel, { "text-anchor": "middle", class: "lbl" }));
  if (yLabel) svg.append(text(m.l - 40, m.t - 2, "", {}));
  for (const r of refLines) {
    svg.append(el("line", { x1: m.l, x2: m.l + iw, y1: sy(r.y), y2: sy(r.y), stroke: "var(--text-muted)", "stroke-dasharray": "4 4", "stroke-width": 1 }));
    svg.append(text(m.l + 4, sy(r.y) - 5, r.label, { class: "lbl" }));
  }
  // series
  for (const s of series) {
    let d = "", pen = false;
    s.values.forEach((v, i) => {
      if (v === null || v === undefined || !Number.isFinite(v)) { pen = false; return; }
      d += `${pen ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(clampY(v)).toFixed(1)}`;
      pen = true;
    });
    svg.append(el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    s.values.forEach((v, i) => {
      if (v === null || v === undefined || !Number.isFinite(v)) return;
      if (s.values.length > 16) return;
      svg.append(el("circle", { cx: sx(xs[i]), cy: sy(clampY(v)), r: 3, fill: s.color, stroke: "var(--surface-1)", "stroke-width": 2 }));
    });
  }
  // direct labels at the last finite point (<= 4 series), nudged apart so they never overlap
  if (series.length <= 4) {
    const labels = series.map((s) => ({ s, i: lastFinite(s.values) })).filter((l) => l.i >= 0)
      .map((l) => ({ ...l, y: sy(clampY(l.s.values[l.i])) + 4 })).sort((a, b) => a.y - b.y);
    for (let k = 1; k < labels.length; k++) labels[k].y = Math.max(labels[k].y, labels[k - 1].y + 14);
    for (const l of labels) svg.append(text(sx(xs[l.i]) + 8, l.y, l.s.label, { class: "lbl" }));
  }
  // hover layer
  const cross = el("line", { y1: m.t, y2: m.t + ih, stroke: "var(--axis)", "stroke-width": 1, visibility: "hidden" });
  svg.append(cross);
  const hit = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" });
  svg.append(hit);
  if (series.length >= 2) {
    const lg = document.createElement("div");
    lg.className = "legend";
    lg.style.margin = "0 0 6px";
    lg.innerHTML = series.map((s) => `<span><i style="background:${s.color};border-radius:2px;height:3px;width:14px;vertical-align:3px"></i>${esc(s.label)}</span>`).join("");
    container.append(lg);
  }
  container.append(svg);
  const tip = document.createElement("div");
  tip.className = "tooltip";
  container.append(tip);
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    let i = Math.round(((px - m.l) / iw) * (xs.length - 1));
    i = Math.max(0, Math.min(xs.length - 1, i));
    cross.setAttribute("x1", sx(xs[i])); cross.setAttribute("x2", sx(xs[i]));
    cross.setAttribute("visibility", "visible");
    tip.innerHTML = `<div class="muted">${esc(opts.tipTitle ? opts.tipTitle(xs[i]) : xFormat(xs[i]))}</div>` +
      series.map((s) => `<div class="row"><span class="sw" style="background:${s.color}"></span>${esc(s.label)}<b style="margin-left:auto;padding-left:12px" class="num">${esc(fmtVal(s.values[i], yFormat))}</b></div>`).join("");
    tip.style.display = "block";
    const cx = (sx(xs[i]) / W) * r.width;
    const tw = tip.offsetWidth;
    tip.style.left = `${cx + 12 + tw > r.width ? cx - tw - 12 : cx + 12}px`;
    tip.style.top = `${8}px`;
  };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerleave", () => { tip.style.display = "none"; cross.setAttribute("visibility", "hidden"); });
}

function fmtVal(v, f) { return v === null || v === undefined || !Number.isFinite(v) ? "no data" : f(v); }
function lastFinite(a) { for (let i = a.length - 1; i >= 0; i--) if (a[i] !== null && Number.isFinite(a[i])) return i; return -1; }
function directLabelWidth(series) {
  if (series.length > 4) return 16;
  return 16 + Math.max(0, ...series.map((s) => s.label.length)) * 6.6;
}
function el(name, attrs) {
  const e = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
function text(x, y, s, attrs) {
  const t = el("text", { x, y, ...attrs });
  t.textContent = s;
  return t;
}
export function niceTicks(lo, hi, n) {
  const span = hi - lo, raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => span / s <= n) || mag * 10;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toPrecision(10));
  return out;
}
