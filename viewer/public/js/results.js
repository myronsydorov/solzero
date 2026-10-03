// Aggregate results from eval output (SPEC 5.6 aggregate.json).
import { CONDITIONS, CONDITION_COLOR, CONDITION_LABEL, esc, fetchJSON, fmt, initTheme, pct } from "./common.js";
import { lineChart } from "./chart.js";

const $ = (s) => document.querySelector(s);
const HEADLINE = 0.8;
const PRIMARY = ["lab", "random", "single"];
initTheme($("#theme"));
main().catch((e) => { console.error(e); $("#app").innerHTML = `<div class="notice">Could not load eval/aggregate.json: ${esc(e.message)}</div>`; });

async function main() {
  const agg = await fetchJSON("eval/aggregate.json");
  const budget = agg.budget || 12;
  const rows = agg.rows || [];
  $("#agg-label").textContent = agg.label || "";
  const present = CONDITIONS.filter((c) => rows.some((r) => r.condition === c));
  const shown = [...PRIMARY, ...present.filter((c) => !PRIMARY.includes(c))];
  const by = Object.fromEntries(shown.map((c) => [c, rows.filter((r) => r.condition === c)]));
  const stats = Object.fromEntries(shown.map((c) => [c, summarise(by[c], budget)]));

  // notes per condition
  const notes = Object.entries(agg.notes || {}).map(([c, n]) => `<li><span class="swatch" style="background:${CONDITION_COLOR[c] || "var(--axis)"}"></span><b>${esc(CONDITION_LABEL[c] || c)}</b>: ${esc(n)}</li>`);
  const missing = PRIMARY.filter((c) => !by[c].length);
  $("#notes").innerHTML = `<ul style="margin:0;padding-left:18px">${notes.join("")}</ul>${missing.length ? `<p style="margin:8px 0 0">No rows yet for: ${missing.map((c) => `<b>${esc(CONDITION_LABEL[c])}</b>`).join(", ")}. Their table rows read "not run".</p>` : ""}`;

  // tiles
  $("#tiles").innerHTML = present.map((c) => {
    const s = stats[c];
    return `<div class="tile"><div class="k"><span class="swatch" style="background:${CONDITION_COLOR[c]}"></span>${esc(CONDITION_LABEL[c])}</div>
      <div class="v num">${s.medianExp === null ? "–" : s.medianExp > budget ? `>${budget}` : fmt(s.medianExp, 3)}</div>
      <div class="s">median experiments to 80% of beyond-range probes · ${s.n} worlds</div>
      <div class="s">law recovery ${s.recovered}/${s.n} · control false discovery ${s.fdK}/${s.fdN}</div></div>`;
  }).join("");

  // learning curves
  const xs = Array.from({ length: budget + 1 }, (_, i) => i);
  lineChart($("#curve-within"), {
    xs, yDomain: [0, 1], yTicks: [0, 0.25, 0.5, 0.75, 1], yFormat: (v) => pct(v), xLabel: "experiments",
    series: present.map((c) => ({ label: CONDITION_LABEL[c], color: CONDITION_COLOR[c], values: stats[c].meanWithin })),
    refLines: [{ y: HEADLINE, label: "80% headline" }], tipTitle: (x) => `after ${x} experiment${x === 1 ? "" : "s"}`,
    ariaLabel: "Mean share of beyond-range probes within the hit radius",
  });
  const errs = present.flatMap((c) => stats[c].medianErr).filter((v) => v !== null && v > 0);
  const lo = errs.length ? 10 ** Math.floor(Math.log10(Math.min(...errs))) : 1e-3;
  const hi = errs.length ? 10 ** Math.ceil(Math.log10(Math.max(...errs))) : 1;
  const ticks = []; for (let v = lo; v <= hi * 1.0001; v *= 10) ticks.push(v);
  lineChart($("#curve-error"), {
    xs, yDomain: [lo, hi], yTicks: ticks, logY: true, yFormat: (v) => (v >= 1 ? `${fmt(v)} m` : v >= 0.01 ? `${fmt(v * 100)} cm` : `${fmt(v * 1000)} mm`), xLabel: "experiments",
    series: present.map((c) => ({ label: CONDITION_LABEL[c], color: CONDITION_COLOR[c], values: stats[c].medianErr })),
    tipTitle: (x) => `after ${x} experiment${x === 1 ? "" : "s"}`, ariaLabel: "Median landing error over all 40 probes",
  });

  // primary metric table
  const cell = (s, f) => (s.n ? f(s) : "–");
  $("#metrics").innerHTML = shown.map((c) => {
    const s = stats[c];
    const cls = s.n ? "" : "nodata";
    return `<tr class="${cls}"><td><span class="swatch" style="background:${CONDITION_COLOR[c]}"></span>${esc(CONDITION_LABEL[c])}${s.n ? "" : ' <span class="muted">· not run</span>'}</td>
      <td class="num">${s.n || "–"}</td>
      <td class="num">${cell(s, (x) => (x.medianExp > budget ? `>${budget}` : fmt(x.medianExp, 3)))}</td>
      <td class="num">${cell(s, (x) => `${x.reached}/${x.n} (${pct(x.reached / x.n)})`)}</td>
      <td class="num">${cell(s, (x) => pct(x.finalWithin, 1))}</td>
      <td class="num">${cell(s, (x) => fmtErr(x.finalErr))}</td>
      <td class="num">${cell(s, (x) => `${x.recovered}/${x.n}`)}</td>
      <td class="num">${cell(s, (x) => (x.fdN ? `${x.fdK}/${x.fdN} (${pct(x.fdK / x.fdN)})` : "no control worlds"))}</td>
      <td class="num">${cell(s, (x) => `${x.abstained}/${x.n}`)}</td>
      <td class="num">${cell(s, (x) => (x.hits === null ? "–" : `${fmt(x.hits, 3)} / 5`))}</td>
      <td class="num">${cell(s, (x) => (x.hitsBeyond === null ? "–" : `${fmt(x.hitsBeyond, 3)} / 3`))}</td></tr>`;
  }).join("");

  // control-world false discovery, per condition
  $("#fdr").innerHTML = shown.map((c) => {
    const s = stats[c];
    if (!s.n) return `<tr class="nodata"><td>${esc(CONDITION_LABEL[c])}</td><td class="num">–</td><td class="num">–</td><td class="muted">not run</td></tr>`;
    const worlds = by[c].filter((r) => r.family === "F0");
    return `<tr><td><span class="swatch" style="background:${CONDITION_COLOR[c]}"></span>${esc(CONDITION_LABEL[c])}</td>
      <td class="num">${s.fdN ? pct(s.fdK / s.fdN) : "–"}</td><td class="num">${s.fdK}/${s.fdN}</td>
      <td class="secondary" style="font-size:12px">${worlds.map((r) => `${esc(r.world)}${r.claims_non_ordinary ? " (claimed non-ordinary)" : ""}`).join(", ") || "–"}</td></tr>`;
  }).join("");

  // paired differences against random
  const base = by.random;
  const pairedRows = present.filter((c) => c !== "random").map((c) => {
    const pairs = by[c].map((r) => [r, base.find((b) => b.world === r.world)]).filter(([, b]) => b);
    if (!pairs.length) return `<tr class="nodata"><td>${esc(CONDITION_LABEL[c])}</td><td colspan="3" class="muted">no matched random worlds</td></tr>`;
    const dExp = pairs.map(([r, b]) => expTo(b.within_beyond, budget) - expTo(r.within_beyond, budget));
    const hitPairs = pairs.filter(([r, b]) => r.mission_hits !== undefined && b.mission_hits !== undefined);
    const dHit = hitPairs.map(([r, b]) => r.mission_hits - b.mission_hits);
    const ci = bootstrap(dExp), ch = dHit.length ? bootstrap(dHit) : null;
    return `<tr><td><span class="swatch" style="background:${CONDITION_COLOR[c]}"></span>${esc(CONDITION_LABEL[c])}</td><td class="num">${pairs.length}</td>
      <td class="num">${fmt(mean(dExp), 3)} <span class="muted">[${fmt(ci[0], 3)}, ${fmt(ci[1], 3)}]</span></td>
      <td class="num">${ch ? `${fmt(mean(dHit), 3)} <span class="muted">[${fmt(ch[0], 3)}, ${fmt(ch[1], 3)}]</span>` : "–"}</td></tr>`;
  });
  $("#paired").innerHTML = base.length ? pairedRows.join("") || '<tr class="nodata"><td colspan="4">No other condition to compare.</td></tr>' : '<tr class="nodata"><td colspan="4">No random-condition rows.</td></tr>';

  // law recovery by family
  const fams = [...new Set(rows.map((r) => r.family))].sort();
  $("#family-head").innerHTML = `<tr><th>Condition</th>${fams.map((f) => `<th class="num">${esc(f)}</th>`).join("")}</tr>`;
  $("#family").innerHTML = present.map((c) => `<tr><td><span class="swatch" style="background:${CONDITION_COLOR[c]}"></span>${esc(CONDITION_LABEL[c])}</td>${fams.map((f) => {
    const rs = by[c].filter((r) => r.family === f);
    return `<td class="num">${rs.length ? `${rs.filter((r) => r.law_recovered).length}/${rs.length}` : "–"}</td>`;
  }).join("")}</tr>`).join("");

  // curve table (accessible view of the chart)
  $("#curve-head").innerHTML = `<tr><th>Condition</th>${xs.map((x) => `<th class="num">${x}</th>`).join("")}</tr>`;
  $("#curve-table").innerHTML = present.map((c) => `<tr><td>${esc(CONDITION_LABEL[c])}</td>${stats[c].meanWithin.map((v) => `<td class="num">${v === null ? "–" : pct(v)}</td>`).join("")}</tr>`).join("");
}

function expTo(curve, budget) {
  const i = (curve || []).findIndex((v) => v !== null && v >= HEADLINE);
  return i < 0 ? budget + 1 : i; // censored at budget + 1, as in calibration/report.py
}

function summarise(rs, budget) {
  const n = rs.length;
  if (!n) return { n: 0 };
  const exps = rs.map((r) => expTo(r.within_beyond, budget));
  const meanWithin = Array.from({ length: budget + 1 }, (_, i) => meanOrNull(rs.map((r) => r.within_beyond?.[i])));
  const medianErr = Array.from({ length: budget + 1 }, (_, i) => median(rs.map((r) => r.median_error_m?.[i])));
  const ctrl = rs.filter((r) => r.family === "F0");
  const hits = rs.filter((r) => typeof r.mission_hits === "number");
  const hitsB = rs.filter((r) => typeof r.mission_hits_beyond === "number");
  return {
    n, medianExp: median(exps), reached: exps.filter((e) => e <= budget).length,
    meanWithin, medianErr, finalWithin: meanWithin[budget], finalErr: medianErr[budget],
    recovered: rs.filter((r) => r.law_recovered).length,
    fdK: ctrl.filter((r) => r.claims_non_ordinary).length, fdN: ctrl.length,
    abstained: rs.filter((r) => r.claim === "insufficient_evidence").length,
    hits: hits.length ? mean(hits.map((r) => r.mission_hits)) : null,
    hitsBeyond: hitsB.length ? mean(hitsB.map((r) => r.mission_hits_beyond)) : null,
  };
}

const fmtErr = (v) => (v === null ? "–" : v >= 0.01 ? `${fmt(v * 100, 3)} cm` : `${fmt(v * 1000, 3)} mm`);
const mean = (a) => a.reduce((s, x) => s + x, 0) / a.length;
function meanOrNull(a) { const f = a.filter((x) => x !== null && x !== undefined && Number.isFinite(x)); return f.length ? mean(f) : null; }
function median(a) {
  // null (never lands) counts as infinitely bad, matching calibration's inf
  const v = a.filter((x) => x !== undefined).map((x) => (x === null ? Infinity : x)).sort((p, q) => p - q);
  if (!v.length) return null;
  const m = v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2;
  return Number.isFinite(m) ? m : null;
}
// Percentile bootstrap of the mean, 4000 resamples, fixed seed (mulberry32) so the page is reproducible.
function bootstrap(d, seed = 0) {
  let t = seed >>> 0;
  const rnd = () => { t += 0x6d2b79f5; let r = Math.imul(t ^ (t >>> 15), 1 | t); r ^= r + Math.imul(r ^ (r >>> 7), 61 | r); return ((r ^ (r >>> 14)) >>> 0) / 4294967296; };
  const boots = [];
  for (let b = 0; b < 4000; b++) { let s = 0; for (let i = 0; i < d.length; i++) s += d[Math.floor(rnd() * d.length)]; boots.push(s / d.length); }
  boots.sort((a, b) => a - b);
  return [boots[Math.floor(0.025 * boots.length)], boots[Math.floor(0.975 * boots.length)]];
}
