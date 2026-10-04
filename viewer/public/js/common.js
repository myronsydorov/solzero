// Shared helpers for the replay and results pages. No build step; plain ES modules.

export const CONDITIONS = ["lab", "random", "single", "oracle", "textbook", "random-scripted"];
export const CONDITION_LABEL = {
  lab: "Sol Zero lab", random: "Random experiments", single: "Single agent",
  oracle: "Oracle design", textbook: "Textbook", "random-scripted": "Scripted random",
};
// Colour follows the condition, never its rank (fixed categorical slots).
export const CONDITION_COLOR = {
  lab: "var(--series-1)", random: "var(--series-2)", single: "var(--series-3)",
  oracle: "var(--series-4)", textbook: "var(--series-5)", "random-scripted": "var(--series-6)",
};

export async function fetchJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

export async function fetchJSONL(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  const text = await r.text();
  return text.split("\n").filter((l) => l.trim()).map((l, i) => {
    try { return JSON.parse(l); } catch (e) { throw new Error(`${url} line ${i + 1}: ${e.message}`); }
  });
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function fmt(x, digits = 3) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  if (!Number.isFinite(x)) return x > 0 ? "∞" : "−∞";
  const a = Math.abs(x);
  if (a !== 0 && (a < 1e-3 || a >= 1e5)) return x.toExponential(2).replace("-", "−");
  return Number(x.toPrecision(digits)).toString().replace("-", "−");
}
export const pct = (x, d = 0) => (x === null || x === undefined || Number.isNaN(x) ? "–" : `${(100 * x).toFixed(d)}%`);

const UNITS = { landing_x_m: "m", flight_time_s: "s", fall_time_s: "s", force_n: "N" };
const OBS_LABEL = { landing_x_m: "landing x", flight_time_s: "flight time", fall_time_s: "fall time", force_n: "force" };
export const obsLabel = (k) => OBS_LABEL[k] || k;
export const obsUnit = (k) => UNITS[k] || "";

export function specText(s) {
  if (!s) return "–";
  if (s.type === "weigh") return `weigh ${s.sample_id} at ${fmt(s.height_m)} m`;
  if (s.type === "drop") return `drop ${s.sample_id} from ${fmt(s.height_m)} m`;
  if (s.type === "launch") return `launch ${s.sample_id} at ${fmt(s.speed_mps)} m/s, ${fmt(s.elevation_deg)}°`;
  return JSON.stringify(s);
}
export const specEqual = (a, b) => JSON.stringify(normSpec(a)) === JSON.stringify(normSpec(b));
function normSpec(s) {
  if (!s) return null;
  const o = {};
  for (const k of Object.keys(s).sort()) o[k] = typeof s[k] === "number" ? +s[k].toPrecision(10) : s[k];
  return o;
}

// --- icons (status colour never carries meaning alone) ---------------------------
export const ICON = {
  live: '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.5" fill="currentColor"/></svg>',
  rejected: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  retired: '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>',
  supported: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.5l2.5 2.5 4.5-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  insufficient: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1.5l5 9H1z" fill="currentColor"/></svg>',
  changed: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M1.5 4h7l-2-2M10.5 8h-7l2 2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  hit: '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="6" cy="6" r="1.8" fill="currentColor"/></svg>',
  miss: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
};

export function badge(kind, text) {
  const icon = ICON[kind] || "";
  return `<span class="badge ${kind}">${icon}${esc(text)}</span>`;
}
export function verdictBadge(v) {
  if (v === "supported") return `<span class="badge live">${ICON.supported}supported</span>`;
  if (v === "rejected") return badge("rejected", "rejected");
  return `<span class="badge insufficient">${ICON.insufficient}insufficient evidence</span>`;
}

// --- equations ---------------------------------------------------------------------
// Law expressions are sympy strings over m, z, vx, vz, speed and named parameters (SPEC 5.1).
const GREEK = new Set(["alpha", "beta", "gamma", "delta", "epsilon", "kappa", "lambda", "mu", "nu",
  "rho", "sigma", "tau", "phi", "theta", "omega", "eta", "xi", "zeta", "chi", "psi"]);
function symbolTex(name) {
  if (name === "vx") return "v_x";
  if (name === "vz") return "v_z";
  if (name === "speed") return "|v|";
  const m = /^([A-Za-z]+)_?(\d+|[A-Za-z]+)?$/.exec(name);
  if (!m) return `\\mathrm{${name.replace(/_/g, "\\_")}}`;
  const [, base, sub] = m;
  const b = GREEK.has(base) ? `\\${base}` : base.length > 1 ? `\\mathrm{${base}}` : base;
  if (!sub) return b;
  const s = GREEK.has(sub) ? `\\${sub}` : sub.length > 1 && !/^\d+$/.test(sub) ? `\\mathrm{${sub}}` : sub;
  return `${b}_{${s}}`;
}

export function exprTex(expr, values) {
  const math = window.math;
  const src = String(expr).replace(/\*\*/g, "^");
  if (!math) return `\\texttt{${src}}`;
  try {
    const node = math.parse(src);
    return node.toTex({
      parenthesis: "auto",
      implicit: "hide",
      handler: (n) => {
        if (n.type === "SymbolNode") {
          if (values && n.name in values && Number.isFinite(values[n.name])) return numTex(values[n.name]);
          return symbolTex(n.name);
        }
        if (n.type === "ConstantNode" && typeof n.value === "number") return numTex(n.value);
        return undefined;
      },
    }).replace(/\\cdot/g, "\\,").replace(/\+\s*-/g, "-").replace(/-\s*-(?=\d)/g, "+");
  } catch {
    return `\\texttt{${src.replace(/[\\{}_^]/g, " ")}}`;
  }
}
function numTex(v) {
  const s = fmt(v, 4).replace("−", "-");
  const m = /^(-?[\d.]+)e([+-]?\d+)$/.exec(s);
  return m ? `${m[1]}\\times10^{${+m[2]}}` : s;
}

export function renderTex(el, tex, display = false) {
  if (window.katex) {
    try { window.katex.render(tex, el, { throwOnError: false, displayMode: display }); return; } catch { /* fall through */ }
  }
  el.textContent = tex;
}

export function lawTex(law, values) {
  return `\\begin{aligned} a_x &= ${exprTex(law.ax, values)} \\\\ a_z &= ${exprTex(law.az, values)} \\end{aligned}`;
}

// The hidden law from GET /admin/truth (SPEC section 2), written as accelerations.
export function truthTex(t, withNumbers = true) {
  const n = (k, sym) => (withNumbers ? numTex(t[k]) : sym);
  const p = Math.round(t.p);
  const gravity = {
    F0: n("g0", "g_0"), F1: n("g0", "g_0"),
    F2: `${n("g0", "g_0")}\\left(\\frac{m}{0.1}\\right)^{${n("alpha", "\\alpha")}}`,
    F3: `${n("g0", "g_0")}\\,(1 ${t.kappa < 0 && withNumbers ? "-" : "+"} ${withNumbers ? numTex(Math.abs(t.kappa)) : "\\kappa"}\\,z)`,
  }[t.family] || n("g0", "g_0");
  const c = n("c", "c");
  const speed = p === 1 ? "" : p === 2 ? "|v|\\," : `|v|^{${p - 1}}\\,`;
  return `\\begin{aligned} a_x &= -\\frac{${c}}{m}\\,${speed}v_x \\\\ a_z &= -${gravity} - \\frac{${c}}{m}\\,${speed}v_z \\end{aligned}`;
}
export const FAMILY_LABEL = {
  F0: "F0 ordinary physics (control)", F1: "F1 drag-law shift",
  F2: "F2 mass-dependent gravity", F3: "F3 height-dependent gravity",
};

// --- theme toggle -------------------------------------------------------------------
export function initTheme(button) {
  const root = document.documentElement;
  let saved = null;
  try { saved = localStorage.getItem("solzero-theme"); } catch { /* storage unavailable */ }
  root.dataset.theme = saved || root.dataset.theme || "dark";  // dark is the default
  const label = () => {
    const dark = root.dataset.theme === "dark";
    button.textContent = dark ? "Light" : "Dark";
    button.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  };
  label();
  button.addEventListener("click", () => {
    const dark = root.dataset.theme === "dark";
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("solzero-theme", root.dataset.theme); } catch { /* ignore */ }
    label();
  });
}
