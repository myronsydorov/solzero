// Replay of one recorded session: ledger.jsonl (SPEC 5.5) + metrics.json (SPEC 5.7) + optional video.
import {
  CONDITION_LABEL, FAMILY_LABEL, ICON, badge, esc, fetchJSON, fetchJSONL, fmt, initTheme, lawTex,
  obsLabel, obsUnit, pct, renderTex, specEqual, specText, truthTex, verdictBadge,
} from "./common.js";
import { lineChart } from "./chart.js";

const $ = (s) => document.querySelector(s);
const state = { runs: [], run: null, ledger: [], metrics: null, steps: [], pos: 0, timer: null, revealed: false };

initTheme($("#theme"));
boot().catch(fail);

async function boot() {
  const index = await fetchJSON("runs/index.json");
  state.runs = index.runs || [];
  if (!state.runs.length) {
    $("#app").innerHTML = `<div class="notice">No recorded runs found. Drop a run directory into <code>viewer/public/runs/</code> and rebuild the index (see the README).</div>`;
    return;
  }
  const sel = $("#run");
  sel.innerHTML = state.runs.map((r) => `<option value="${esc(r.run_id)}">${esc(r.label || r.run_id)}</option>`).join("");
  const q = new URLSearchParams(location.search);
  const want = q.get("run") && state.runs.find((r) => r.run_id === q.get("run")) ? q.get("run") : state.runs[0].run_id;
  sel.value = want;
  sel.addEventListener("change", () => load(sel.value, null));
  const cyc = q.get("step");
  await load(want, cyc === null ? null : +cyc);
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("select, input, textarea")) return;
    if (e.key === "ArrowRight") go(state.pos + 1);
    if (e.key === "ArrowLeft") go(state.pos - 1);
    if (e.key === " ") { e.preventDefault(); togglePlay(); }
  });
  $("#prev").addEventListener("click", () => go(state.pos - 1));
  $("#next").addEventListener("click", () => go(state.pos + 1));
  $("#first").addEventListener("click", () => go(0));
  $("#last").addEventListener("click", () => go(state.steps.length - 1));
  $("#play").addEventListener("click", togglePlay);
}

function fail(err) {
  console.error(err);
  $("#app").innerHTML = `<div class="notice">Could not load the replay: ${esc(err.message)}</div>`;
}

async function load(runId, step) {
  stop();
  const run = state.runs.find((r) => r.run_id === runId);
  const base = `runs/${encodeURIComponent(runId)}/`;
  const [ledger, metrics] = await Promise.all([
    fetchJSONL(base + "ledger.jsonl"),
    fetchJSON(base + "metrics.json").catch(() => null),
  ]);
  Object.assign(state, { run, ledger, metrics, revealed: false, base });
  state.steps = buildSteps(ledger);
  $("#runmeta").textContent = [
    CONDITION_LABEL[metrics?.condition] || metrics?.condition || run.condition,
    metrics?.session_info?.session_id && `session ${metrics.session_info.session_id}`,
    `${ledger.length} ledger entries`,
  ].filter(Boolean).join(" · ");
  $("#policy").textContent = metrics?.policy ? `Policy: ${metrics.policy}` : "";
  renderVideo(run);
  go(step === null || Number.isNaN(step) ? 0 : step, true);
}

// Steps: one per ledger cycle, plus a final "mission" step when the PI committed.
function buildSteps(ledger) {
  const maxCycle = Math.max(0, ...ledger.map((e) => e.cycle));
  const steps = [];
  for (let c = 0; c <= maxCycle; c++) {
    const entries = ledger.filter((e) => e.cycle === c && e.kind !== "commit");
    const result = entries.find((e) => e.kind === "result");
    const diff = entries.find((e) => e.kind === "decision_diff");
    const verdicts = entries.filter((e) => e.kind === "verdicts").flatMap((e) => e.payload.verdicts || []);
    steps.push({
      kind: "cycle", cycle: c, entries, result: result?.payload,
      changed: !!diff?.payload?.changed, rejected: verdicts.filter((v) => v.verdict === "rejected").map((v) => v.law_id),
    });
  }
  const commit = ledger.find((e) => e.kind === "commit");
  if (commit) steps.push({ kind: "mission", cycle: commit.cycle, entries: [commit], commit: commit.payload });
  return steps;
}

function go(i, force = false) {
  i = Math.max(0, Math.min(state.steps.length - 1, i));
  if (i === state.pos && !force) return;
  state.pos = i;
  const u = new URL(location.href);
  u.searchParams.set("run", state.run.run_id);
  u.searchParams.set("step", i);
  history.replaceState(null, "", u);
  render();
}
function togglePlay() { state.timer ? stop() : play(); }
function play() {
  if (state.pos >= state.steps.length - 1) go(0);
  state.timer = setInterval(() => { if (state.pos >= state.steps.length - 1) stop(); else go(state.pos + 1); }, 2200);
  $("#play").textContent = "Pause";
}
function stop() { clearInterval(state.timer); state.timer = null; const b = $("#play"); if (b) b.textContent = "Play"; }

// --- state as of the current step ----------------------------------------------------
function snapshot() {
  const step = state.steps[state.pos];
  const upto = step.kind === "mission" ? Infinity : step.cycle;
  const seen = state.ledger.filter((e) => e.cycle <= upto && (e.kind !== "commit" || step.kind === "mission"));
  const laws = new Map(); // law_id -> {law, first, last}
  let live = [];
  const verdicts = new Map();
  const fits = new Map();
  let used = 0;
  for (const e of seen) {
    if (e.kind === "law_set") {
      live = (e.payload.laws || []).map((l) => l.law_id);
      for (const l of e.payload.laws || []) {
        const prev = laws.get(l.law_id);
        laws.set(l.law_id, { law: l, first: prev ? prev.first : e.cycle, last: e.cycle });
      }
    } else if (e.kind === "verdicts") {
      for (const v of e.payload.verdicts || []) {
        if (!verdicts.has(v.law_id)) verdicts.set(v.law_id, []);
        verdicts.get(v.law_id).push({ ...v, cycle: e.cycle });
      }
    } else if (e.kind === "nomination") {
      fits.set(e.payload.law.law_id, { fit: e.payload.fit, cycle: e.cycle });
    } else if (e.kind === "result") used += 1;
  }
  return { step, laws, live, verdicts, fits, used };
}

// --- render ---------------------------------------------------------------------------
function render() {
  const snap = snapshot();
  renderTimeline();
  renderBudget(snap);
  renderMission(snap);
  renderCycle(snap);
  renderLaws(snap);
  renderReveal(snap);
}

function renderTimeline() {
  const t = $("#timeline");
  t.innerHTML = state.steps.map((s, i) => {
    const cls = i < state.pos ? "past" : i === state.pos ? "current" : "future";
    const title = s.kind === "mission" ? "Mission" : s.cycle === 0 ? "Cycle 0" : `Cycle ${s.cycle}`;
    const sub = s.kind === "mission" ? "commit & fire" : s.result ? s.result.spec.type : s.cycle === 0 ? "setup" : "–";
    const marks = [];
    if (s.changed) marks.push(`<span class="mark" title="Plan changed by evidence" style="color:var(--serious)">${ICON.changed}</span>`);
    if (s.rejected?.length) marks.push(`<span class="mark" title="Rejected: ${esc(s.rejected.join(", "))}" style="color:var(--critical)">${ICON.rejected}${s.rejected.length}</span>`);
    if (s.result?.status === "failed") marks.push(`<span class="mark" title="Experiment failed" style="color:var(--warning)">${ICON.insufficient}</span>`);
    return `<button class="tl-cycle ${cls}" data-i="${i}" aria-current="${i === state.pos ? "step" : "false"}" aria-label="${esc(title)}: ${esc(sub)}">
      <span class="n">${esc(title)}</span><span class="ty">${esc(sub)}</span><span class="marks">${marks.join("")}</span></button>`;
  }).join("");
  t.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { stop(); go(+b.dataset.i); }));
  t.querySelector(".current")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  $("#pos").textContent = `Step ${state.pos + 1} of ${state.steps.length}`;
}

function renderBudget(snap) {
  const budget = state.metrics?.session_info?.budget || 12;
  const thisCycle = snap.step.kind === "cycle" && snap.step.result ? snap.used : -1;
  $("#budget").innerHTML = `<div class="budget-label"><span>Experiment budget</span><span class="num">${snap.used} used · ${budget - snap.used} left</span></div>
    <div class="budget" role="img" aria-label="${snap.used} of ${budget} experiments used">${Array.from({ length: budget }, (_, i) =>
      `<span class="${i + 1 === thisCycle ? "now" : i < snap.used ? "used" : ""}"></span>`).join("")}</div>`;
}

function renderVideo(run) {
  const v = $("#video");
  if (run?.video) {
    v.innerHTML = `<header><h2>Robot video</h2><span class="hint">recorded run</span></header><video src="${esc(state.base + run.video)}" controls preload="metadata" style="width:100%;border-radius:8px"></video>`;
    v.hidden = false;
  } else {
    v.hidden = true;
  }
}

// Side view of the workspace: launcher, targets with hit zones, shot zero, committed shots.
function renderMission(snap) {
  const info = state.metrics?.session_info;
  const el = $("#mission-body");
  if (!info) { el.innerHTML = `<div class="notice">metrics.json missing: no targets to show.</div>`; return; }
  const kinds = Object.fromEntries((state.metrics.truth?.targets || state.metrics.score?.targets || []).map((t) => [t.target_id, t.kind]));
  const fired = snap.step.kind === "mission" ? state.metrics.score?.commit : null;
  const shots = Object.fromEntries((fired?.shots || []).map((s) => [s.target_id, s]));
  const planned = Object.fromEntries((snap.step.commit?.shots || []).map((s) => [s.target_id, s]));
  const sz = info.shot_zero;
  const xmax = Math.max(1, ...info.targets.map((t) => t.x_m + t.hit_radius_m), sz?.landing_x_m || 0) * 1.08;
  const W = 760, H = 210, L = 30, R = 16, T = 18, B = 34;
  const sx = (x) => L + (x / xmax) * (W - L - R);
  const sz_ = (z) => H - B - (z / 0.6) * (H - T - B);
  let svg = `<svg class="scene" viewBox="0 0 ${W} ${H}" role="img" aria-label="Side view of the workspace with targets">`;
  for (let z = 0.2; z <= 0.6; z += 0.2) svg += `<line x1="${L}" x2="${W - R}" y1="${sz_(z)}" y2="${sz_(z)}" stroke="var(--grid)"/><text x="${L - 4}" y="${sz_(z) + 4}" text-anchor="end" fill="var(--text-muted)" font-size="10">${z.toFixed(1)}</text>`;
  svg += `<line x1="${L}" x2="${W - R}" y1="${sz_(0)}" y2="${sz_(0)}" stroke="var(--axis)" stroke-width="2"/>`;
  for (let x = 0; x <= xmax; x += xmax > 4 ? 1 : 0.5) svg += `<text x="${sx(x)}" y="${H - B + 16}" text-anchor="middle" fill="var(--text-muted)" font-size="10">${x} m</text>`;
  // launcher
  svg += `<rect x="${sx(0) - 6}" y="${sz_(0.2)}" width="12" height="${sz_(0) - sz_(0.2)}" rx="2" fill="var(--axis)"/><circle cx="${sx(0)}" cy="${sz_(0.2)}" r="5" fill="var(--text-secondary)"/>`;
  // shot zero
  if (sz) {
    svg += `<g><path d="M${sx(sz.landing_x_m) - 5},${sz_(0) - 5} l10,10 m0,-10 l-10,10" stroke="var(--text-muted)" stroke-width="2"/>
      <text x="${sx(sz.landing_x_m)}" y="${sz_(0) + 28}" text-anchor="middle" fill="var(--text-muted)" font-size="10">shot zero</text></g>`;
  }
  for (const t of info.targets) {
    const beyond = kinds[t.target_id] === "beyond";
    const x0 = sx(t.x_m - t.hit_radius_m), x1 = sx(t.x_m + t.hit_radius_m), y = sz_(t.z_m);
    if (t.z_m > 0.005) svg += `<rect x="${x0 - 4}" y="${y}" width="${x1 - x0 + 8}" height="${sz_(0) - y}" fill="var(--surface-2)" stroke="var(--border)"/>`;
    svg += `<rect x="${x0}" y="${y - 4}" width="${Math.max(3, x1 - x0)}" height="6" rx="2" fill="${beyond ? "var(--series-4)" : "var(--series-1)"}"/>`;
    svg += `<text x="${sx(t.x_m)}" y="${y - 10}" text-anchor="middle" fill="var(--text-secondary)" font-size="11" font-weight="600">${esc(t.target_id)}</text>`;
    const s = shots[t.target_id];
    if (s && s.x_m !== null && s.x_m !== undefined) {
      const c = s.hit ? "var(--good)" : "var(--critical)";
      svg += s.hit
        ? `<circle cx="${sx(s.x_m)}" cy="${y - 1}" r="5" fill="none" stroke="${c}" stroke-width="2"/><circle cx="${sx(s.x_m)}" cy="${y - 1}" r="2" fill="${c}"/>`
        : `<path d="M${sx(s.x_m) - 5},${y - 6} l10,10 m0,-10 l-10,10" stroke="${c}" stroke-width="2"/>`;
    }
  }
  svg += `</svg>`;
  const hits = fired ? fired.shots.filter((s) => s.hit).length : null;
  const rows = info.targets.map((t) => {
    const s = shots[t.target_id], p = planned[t.target_id] || s;
    const outcome = s ? (s.hit ? badge("hit", "hit") : badge("miss", "miss")) : '<span class="muted">not fired</span>';
    return `<tr><td><b>${esc(t.target_id)}</b></td><td>${kinds[t.target_id] === "beyond" ? "beyond tested range" : kinds[t.target_id] === "in_range" ? "in range" : "–"}</td>
      <td class="num">${fmt(t.x_m)}</td><td class="num">${fmt(t.z_m)}</td><td class="num">${fmt(100 * t.hit_radius_m, 2)} cm</td>
      <td class="num">${p ? `${fmt(p.speed_mps)} m/s, ${fmt(p.elevation_deg)}°` : "–"}</td>
      <td class="num">${s ? `${fmt(100 * s.miss_m, 2)} cm` : "–"}</td><td>${outcome}</td></tr>`;
  }).join("");
  el.innerHTML = `
    <div class="secondary" style="font-size:13px;margin-bottom:6px">Five untouched targets, one shot each with the 300 g mission sample. ${sz ? `Shot zero, fired with textbook physics, missed by <b>${fmt(100 * sz.miss_m, 3)} cm</b>.` : ""}</div>
    ${svg}
    <div class="legend"><span><i style="background:var(--series-1)"></i>target in tested range</span><span><i style="background:var(--series-4)"></i>beyond tested range</span><span class="muted">bar width = hit zone · vertical scale exaggerated</span></div>
    ${fired ? `<p style="margin:10px 0 4px"><b>${hits} of ${fired.shots.length} hit</b> <span class="secondary">· law ${esc(fired.law_id)} · claim ${esc(fired.claim.replace(/_/g, " "))}${fired.claims_non_ordinary ? " · claims non-ordinary physics" : ""}</span></p>` : snap.step.commit ? "" : `<p class="muted" style="margin:10px 0 4px;font-size:12px">Shots are fired at the final step, after the PI commits.</p>`}
    <div class="table-wrap"><table><thead><tr><th>Target</th><th>Kind</th><th class="num">x (m)</th><th class="num">z (m)</th><th class="num">Radius</th><th class="num">Shot</th><th class="num">Miss</th><th>Result</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderCycle(snap) {
  const step = snap.step;
  const el = $("#cycle-body");
  $("#cycle-title").textContent = step.kind === "mission" ? "Mission commit" : `Cycle ${step.cycle}`;
  if (step.kind === "mission") {
    const c = step.commit;
    el.innerHTML = `<div class="event"><h3><span class="who">PI</span> Firing table committed</h3>
      <div class="diff"><span class="k">Law</span><span class="mono">${esc(c.law_id)}</span>
      <span class="k">Claim</span><span>${esc(c.claim.replace(/_/g, " "))}</span>
      <span class="k">Non-ordinary</span><span>${c.claims_non_ordinary ? "yes, claims non-ordinary physics" : "no"}</span></div>
      <p class="reason">The commit returns no hit or miss information to the agents. Outcomes shown in the mission panel come from the server's admin record.</p></div>`;
    return;
  }
  const by = (k) => step.entries.filter((e) => e.kind === k);
  const parts = [];
  const cand = by("candidates")[0]?.payload;
  const diff = by("decision_diff")[0]?.payload;
  const table = by("prediction_table")[0]?.payload;
  const result = by("result")[0]?.payload;
  const verdicts = by("verdicts").flatMap((e) => e.payload.verdicts || []);
  const confound = by("verdicts").map((e) => e.payload.confound_note).filter(Boolean);

  if (step.cycle === 0) parts.push(`<div class="event"><h3><span class="who">Theorist</span> Starting law set</h3><p class="secondary" style="margin:0">${snap.live.length ? `${snap.live.length} live law(s).` : "No live laws yet. The first experiment is run without predictions."}</p></div>`);
  if (cand || table) parts.push(disagreementBlock(cand, table));
  if (diff) {
    parts.push(`<div class="event"><h3><span class="who">Experimentalist</span> Decision diff ${diff.changed ? badge("changed", "plan changed by evidence") : '<span class="badge">plan unchanged</span>'}</h3>
      <div class="diff"><span class="k">Tentative</span><span class="spec">${esc(specText(diff.tentative))}</span><span class="k">Actual</span><span class="spec">${esc(specText(diff.actual))}</span></div>
      ${diff.reason ? `<p class="reason">${esc(diff.reason)}</p>` : ""}</div>`);
  }
  if (result) parts.push(predictionBlock(table, result, verdicts));
  if (verdicts.length) {
    parts.push(`<div class="event"><h3><span class="who">Analyst</span> Verdicts</h3>
      <div class="table-wrap"><table><thead><tr><th>Law</th><th class="num">z</th><th>Verdict</th><th>Note</th></tr></thead><tbody>
      ${verdicts.map((v) => `<tr><td class="mono">${esc(v.law_id)}</td><td class="num zcell">${fmt(v.z, 3)}</td><td>${verdictBadge(v.verdict)}</td><td class="secondary" style="font-size:12px">${esc(v.note || "")}</td></tr>`).join("")}
      </tbody></table></div>${confound.map((c) => `<p class="reason"><b>Confounds.</b> ${esc(c)}</p>`).join("")}</div>`);
  }
  const nom = by("nomination").at(-1)?.payload;
  if (nom) parts.push(`<div class="event"><h3><span class="who">Analyst</span> Nominated best law <span class="mono">${esc(nom.law.law_id)}</span></h3><p class="reason" style="margin:0">Scored by the server on hidden probes; the score is never returned to the agents.</p></div>`);
  const sets = by("law_set");
  if (sets.length && step.cycle > 0) {
    const ids = sets.at(-1).payload.laws.map((l) => l.law_id);
    parts.push(`<div class="event"><h3><span class="who">Theorist</span> Live law set for the next cycle</h3><p style="margin:0">${ids.length ? ids.map((i) => `<span class="mono">${esc(i)}</span>`).join(", ") : '<span class="muted">empty</span>'}</p></div>`);
  }
  el.innerHTML = `<div class="events">${parts.join("") || '<p class="muted">No entries in this cycle.</p>'}</div>`;
}

function disagreementBlock(cand, table) {
  const chosen = cand?.chosen || table?.spec;
  let dis = cand?.disagreements;
  let derived = false;
  if (!dis?.length && table?.predictions?.length >= 2) {
    dis = [gapsFromPredictions(table)];
    derived = true;
  }
  const specs = dis?.length ? dis.map((d) => d.spec) : cand?.candidates || (chosen ? [chosen] : []);
  if (!specs.length) return "";
  const pairs = dis?.[0]?.pairs?.map((p) => `${p.a} vs ${p.b}`) || [];
  const head = `<tr><th>Candidate</th>${pairs.map((p) => `<th class="num mono" style="font-size:11px">${esc(p)}</th>`).join("")}${pairs.length ? '<th class="num">max σ</th>' : ""}</tr>`;
  const maxAll = Math.max(1, ...(dis || []).map((d) => Math.min(50, d.max_gap_sigma)));
  const body = specs.map((s, i) => {
    const d = dis?.[i];
    const cells = d ? d.pairs.map((p) => `<td class="num">${fmtGap(p.gap_sigma)}</td>`).join("") +
      `<td class="num"><b>${fmtGap(d.max_gap_sigma)}</b><span class="zbar" style="width:${Math.round((Math.min(50, d.max_gap_sigma) / maxAll) * 48)}px"></span></td>` : "";
    const isChosen = chosen && specEqual(s, chosen);
    return `<tr class="${isChosen ? "chosen" : ""}"><td class="spec">${isChosen ? "▶ " : ""}${esc(specText(s))}</td>${cells}</tr>`;
  }).join("");
  const n = cand?.candidates?.length;
  return `<div class="event"><h3><span class="who">Experimentalist</span> Disagreement table</h3>
    <p class="reason" style="margin:0 0 6px">${derived ? "Not recorded in the ledger; gaps for the chosen spec are derived from the prediction table (|Δmean| / √(½(sd²ₐ+sd²ᵦ)), worst observable)." : n ? `Gap between each pair of live laws' predictions, in units of predictive sd. ${n} candidate(s) shown; the chosen one is highlighted.` : ""}</p>
    <div class="table-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div></div>`;
}
const fmtGap = (g) => (g >= 1000 ? "lands / not" : `${fmt(g, 3)}σ`);

function gapsFromPredictions(table) {
  const ps = table.predictions;
  const pairs = [];
  for (let a = 0; a < ps.length; a++) for (let b = a + 1; b < ps.length; b++) {
    let gap = 0;
    for (const k of Object.keys(ps[a].observables)) {
      const A = ps[a].observables[k], B = ps[b].observables[k];
      if (!B || A.mean === null || B.mean === null) continue;
      gap = Math.max(gap, Math.abs(A.mean - B.mean) / Math.sqrt(0.5 * (A.sd ** 2 + B.sd ** 2)));
    }
    pairs.push({ a: ps[a].law_id, b: ps[b].law_id, gap_sigma: gap });
  }
  return { spec: table.spec, pairs, max_gap_sigma: Math.max(0, ...pairs.map((p) => p.gap_sigma)) };
}

function predictionBlock(table, result, verdicts) {
  const obs = Object.keys(result.observables || {});
  const vby = Object.fromEntries(verdicts.map((v) => [v.law_id, v]));
  const failed = result.status !== "ok";
  let rows = "";
  for (const p of table?.predictions || []) {
    obs.forEach((k, j) => {
      const est = p.observables[k];
      const y = result.observables[k];
      const z = est && est.mean !== null && y !== null && est.sd ? (y - est.mean) / est.sd : null;
      const az = z === null ? 0 : Math.min(6, Math.abs(z));
      const color = z === null ? "var(--axis)" : Math.abs(z) > 3 ? "var(--critical)" : Math.abs(z) > 2 ? "var(--warning)" : "var(--good)";
      rows += `<tr>${j === 0 ? `<td rowspan="${obs.length}" class="mono">${esc(p.law_id)}<div>${vby[p.law_id] ? verdictBadge(vby[p.law_id].verdict) : ""}</div></td>` : ""}
        <td>${esc(obsLabel(k))}</td><td class="num">${est && est.mean !== null ? `${fmt(est.mean, 4)} ± ${fmt(est.sd, 2)}` : "never lands"}</td>
        <td class="num">${y === null || y === undefined ? "–" : fmt(y, 4)} ${esc(obsUnit(k))}</td>
        <td class="num zcell">${z === null ? "–" : (z >= 0 ? "+" : "") + fmt(z, 3)}<span class="zbar" style="width:${Math.round(az * 8)}px;background:${color}"></span></td></tr>`;
    });
  }
  const values = obs.map((k) => `${obsLabel(k)} ${result.observables[k] === null ? "–" : fmt(result.observables[k], 4)} ${obsUnit(k)}`).join(", ");
  return `<div class="event"><h3><span class="who">Operator</span> Result ${esc(result.experiment_id)} ${failed ? `<span class="badge insufficient">${ICON.insufficient}failed run, budget still spent</span>` : ""}</h3>
    <p style="margin:0 0 6px"><span class="spec">${esc(specText(result.spec))}</span> → <b>${esc(values)}</b></p>
    ${table?.predictions?.length ? `<p class="reason" style="margin:0 0 6px">Pre-registered predictions against the result. z = (observed − predicted) / predictive sd; bar length is |z| (capped at 6).</p>
    <div class="table-wrap"><table><thead><tr><th>Law</th><th>Observable</th><th class="num">Predicted</th><th class="num">Observed</th><th class="num">z</th></tr></thead><tbody>${rows}</tbody></table></div>`
    : '<p class="reason" style="margin:0">No live laws at pre-registration, so no predictions.</p>'}</div>`;
}

function renderLaws(snap) {
  const el = $("#laws-body");
  if (!snap.laws.size) { el.innerHTML = '<p class="muted">No laws proposed yet.</p>'; return; }
  const status = (id) => {
    if (snap.live.includes(id)) return "live";
    return (snap.verdicts.get(id) || []).some((v) => v.verdict === "rejected") ? "rejected" : "retired";
  };
  const order = { live: 0, rejected: 1, retired: 2 };
  const items = [...snap.laws.values()].sort((a, b) => order[status(a.law.law_id)] - order[status(b.law.law_id)] || b.last - a.last);
  el.innerHTML = items.map(({ law, first }) => {
    const st = status(law.law_id);
    const last = (snap.verdicts.get(law.law_id) || []).at(-1);
    const fit = snap.fits.get(law.law_id);
    const params = Object.keys(law.params).map((k) => {
      const f = fit?.fit?.params?.[k];
      return f ? `${k} = ${fmt(f.value, 4)} ± ${fmt(f.sd, 2)}` : `${k} ≈ ${fmt(law.params[k].init, 4)}`;
    }).join(" · ");
    const fitLine = fit ? `fit at cycle ${fit.cycle}: χ²/dof ${fmt(fit.fit.chi2_dof, 3)}${fit.fit.loo_error !== null && fit.fit.loo_error !== undefined ? `, LOO ${fmt(fit.fit.loo_error, 3)}σ` : ""}` : "initial values from the law set; no fit nominated for this law";
    return `<article class="law is-${st}">
      <div class="head"><span class="id">${esc(law.law_id)}</span>${badge(st, st)}${last && !(st === "rejected" && last.verdict === "rejected") ? verdictBadge(last.verdict) : ""}<span class="muted" style="font-size:11px;margin-left:auto">since cycle ${first}</span></div>
      ${law.description ? `<div class="desc">${esc(law.description)}</div>` : ""}
      <div class="eq" data-tex="${esc(lawTex(law))}"></div>
      <div class="params">${esc(params)}</div>
      <div class="params muted">${esc(fitLine)}</div>
      ${last ? `<div class="verdict-note">Last verdict, ${esc(last.experiment_id)}: z = ${fmt(last.z, 3)}${last.note ? ` · ${esc(last.note)}` : ""}</div>` : ""}
    </article>`;
  }).join("");
  el.querySelectorAll("[data-tex]").forEach((n) => renderTex(n, n.dataset.tex, true));
}

function renderReveal(snap) {
  const el = $("#reveal-body");
  const truth = state.metrics?.truth;
  if (!truth) { el.innerHTML = '<p class="muted">No hidden-law record in metrics.json.</p>'; return; }
  const atEnd = snap.step.kind === "mission" || state.pos === state.steps.length - 1;
  if (!atEnd && !state.revealed) {
    el.innerHTML = `<div class="locked"><p class="secondary" style="margin:0 0 8px">The hidden law is revealed after the mission, as in the demo.</p><button id="reveal-now">Reveal now</button></div>`;
    $("#reveal-now").addEventListener("click", () => { state.revealed = true; renderReveal(snapshot()); });
    return;
  }
  const commit = state.ledger.find((e) => e.kind === "commit")?.payload;
  const discoveredId = commit?.law_id || [...snap.fits.keys()].at(-1);
  const law = discoveredId && [...state.ledger].reverse().find((e) => e.kind === "law_set" && e.payload.laws.some((l) => l.law_id === discoveredId))?.payload.laws.find((l) => l.law_id === discoveredId);
  const nom = [...state.ledger].reverse().find((e) => e.kind === "nomination" && e.payload.law.law_id === discoveredId)?.payload;
  const vals = nom ? Object.fromEntries(Object.entries(nom.fit.params).map(([k, v]) => [k, v.value])) : null;
  const truthParams = [["g0", "g₀", "m/s²"], ["c", "c", "kg/m"], ["p", "drag exponent p", ""], ["alpha", "α", ""], ["kappa", "κ", "1/m"], ["rho", "ρ (drag/weight, 100 g at 3 m/s)", ""]]
    .filter(([k]) => truth[k] !== undefined && !(k === "alpha" && truth.family !== "F2") && !(k === "kappa" && truth.family !== "F3"))
    .map(([k, l, u]) => `${l} = ${fmt(truth[k], 4)} ${u}`).join(" · ");
  el.innerHTML = `<div class="reveal-grid">
    <div class="col"><h3>Hidden law</h3><div class="secondary" style="font-size:12px;margin:2px 0 6px">${esc(FAMILY_LABEL[truth.family] || truth.family)}</div>
      <div class="eq" id="tex-truth"></div><div class="params" style="margin-top:6px">${esc(truthParams)}</div></div>
    <div class="col"><h3>Discovered law <span class="mono" style="font-weight:500">${esc(discoveredId || "–")}</span></h3>
      <div class="secondary" style="font-size:12px;margin:2px 0 6px">${commit ? `committed, claim: ${esc(commit.claim.replace(/_/g, " "))}${commit.claims_non_ordinary ? ", non-ordinary" : ", ordinary"}` : "last nomination (no commit yet)"}</div>
      <div class="eq" id="tex-found"></div><div class="params" style="margin-top:6px">${nom ? esc(Object.entries(nom.fit.params).map(([k, v]) => `${k} = ${fmt(v.value, 4)} ± ${fmt(v.sd, 2)}`).join(" · ")) : ""}</div></div>
  </div>
  <h3 style="margin:16px 0 4px">Hidden scoring of the nominated law</h3>
  <p class="reason" style="margin:0 0 6px">Share of the 20 beyond-range probe launches (4 to 7 m/s) predicted within the hit radius, after each experiment. The agents never see this.</p>
  <div id="probe-chart"></div>`;
  renderTex($("#tex-truth"), truthTex(truth), true);
  if (law) renderTex($("#tex-found"), lawTex(law, vals), true);
  const noms = state.metrics?.score?.nominations || [];
  const budget = state.metrics?.session_info?.budget || 12;
  const xs = Array.from({ length: budget + 1 }, (_, i) => i);
  const beyond = xs.map(() => null), inr = xs.map(() => null);
  for (const n of noms) { beyond[n.n_experiments] = n.probe_within_beyond; inr[n.n_experiments] = n.probe_within_in_range; }
  if (noms.length) {
    lineChart($("#probe-chart"), {
      xs, yDomain: [0, 1], yTicks: [0, 0.25, 0.5, 0.75, 1], yFormat: (v) => pct(v), xLabel: "experiments",
      series: [{ label: "beyond range", color: "var(--series-1)", values: beyond }, { label: "in range", color: "var(--series-3)", values: inr }],
      refLines: [{ y: 0.8, label: "80% threshold" }], height: 220, tipTitle: (x) => `after ${x} experiment${x === 1 ? "" : "s"}`,
    });
  } else $("#probe-chart").innerHTML = '<p class="muted">No nominations recorded.</p>';
}
