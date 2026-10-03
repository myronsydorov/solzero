// Scan viewer/public/runs/*/ and write viewer/public/runs/index.json (SPEC 5.7).
// A run directory needs ledger.jsonl; metrics.json and video.mp4/.webm are optional.
//   node viewer/scripts/build-index.mjs
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const runsDir = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "runs");
const runs = [];
for (const name of existsSync(runsDir) ? readdirSync(runsDir).sort() : []) {
  const dir = join(runsDir, name);
  if (!statSync(dir).isDirectory() || !existsSync(join(dir, "ledger.jsonl"))) continue;
  let metrics = {};
  try { metrics = JSON.parse(readFileSync(join(dir, "metrics.json"), "utf8")); } catch { /* optional */ }
  const video = ["video.mp4", "video.webm"].find((v) => existsSync(join(dir, v))) || null;
  const lines = readFileSync(join(dir, "ledger.jsonl"), "utf8").split("\n").filter((l) => l.trim()).length;
  runs.push({
    run_id: name,
    label: metrics.label || name,
    condition: metrics.condition || null,
    has_metrics: Object.keys(metrics).length > 0,
    video,
    ledger_entries: lines,
  });
}
// Real (non-fixture) runs first, then fixtures.
runs.sort((a, b) => (a.condition === "oracle") - (b.condition === "oracle") || a.label.localeCompare(b.label));
writeFileSync(join(runsDir, "index.json"), JSON.stringify({ runs }, null, 1) + "\n");
console.log(`wrote ${runs.length} runs to ${join(runsDir, "index.json")}`);
