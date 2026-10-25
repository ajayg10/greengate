"use strict";

const fmt = (v, digits = 3, unit = "") => (v === null || v === undefined || Number.isNaN(v))
  ? "–" : `${Number(v).toFixed(digits)}${unit}`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function tile(label, value, note = "", good = false) {
  return `<div class="tile${good ? " good" : ""}"><div class="label">${esc(label)}</div>` +
    `<div class="value">${esc(value)}</div>${note ? `<div class="note">${esc(note)}</div>` : ""}</div>`;
}

function renderTiles(k) {
  const red = k.reduction_pct;
  document.getElementById("tiles").innerHTML = [
    tile("Pipelines measured", k.pipelines_measured ?? 0, "real GitLab runs"),
    tile("gCO2e measured", fmt(k.gco2e_measured_total, 3, " g"), "all measured runs"),
    tile("Carbon cut per run", red === null ? "–" : `${fmt(red, 1)}%`, "optimized vs baseline median", red > 0),
    tile("Saved per run", fmt(k.saved_g_per_pipeline, 3, " g"), "gCO2e, measured"),
    tile("Runner minutes saved", fmt(k.minutes_saved_per_pipeline, 2), "per pipeline run"),
    tile("Deploys", `${k.deploys_promoted ?? 0} / ${k.deploys_deferred ?? 0} / ${k.rollbacks ?? 0}`,
      "promoted / deferred / rolled back"),
    tile("Rollback time", k.median_rollback_seconds == null ? "–" : `${fmt(k.median_rollback_seconds, 1)} s`,
      "SLO breach → traffic restored"),
    tile("MTTR", k.mttr_minutes == null ? "–" : `${fmt(k.mttr_minutes, 0)} min`, "incident → healthy deploy"),
  ].join("");
}

function renderChart(series) {
  const canvas = document.getElementById("chart");
  if (!series.length || typeof Chart === "undefined") {
    document.getElementById("chart-empty").hidden = series.length > 0;
    if (!series.length) canvas.parentElement.hidden = true;
    return;
  }
  const kinds = { baseline: css("--base-line"), optimized: css("--opt-line"), normal: css("--muted") };
  const labels = series.map((p, i) => `#${p.pipeline ?? i + 1}`);
  new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "gCO2e (app scope)",
        data: series.map((p) => p.app_g),
        borderColor: css("--muted"),
        pointBackgroundColor: series.map((p) => kinds[p.kind] || kinds.normal),
        pointRadius: 5,
        tension: 0.2,
      }],
    },
    options: {
      maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, title: { display: true, text: "gCO2e" }, ticks: { color: css("--muted") },
          grid: { color: css("--border") } },
        x: { ticks: { color: css("--muted") }, grid: { display: false } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const p = series[ctx.dataIndex];
              return `${p.kind}: ${fmt(p.app_g)} g (total ${fmt(p.g)} g, ${fmt(p.minutes, 1)} runner min)`;
            },
          },
        },
      },
      onClick: (_e, els) => { if (els.length && series[els[0].index].url) window.open(series[els[0].index].url); },
    },
  });
}

function renderDecisions(rows) {
  if (!rows.length) return;
  document.getElementById("decisions").innerHTML = rows.slice().reverse().map((r) => {
    const kind = r.kind === "rollback" ? "rollback" : (r.action === "DEFER" ? "defer" : "");
    const label = r.kind === "deploy" ? `promoted v${esc(r.version)}` : r.kind === "rollback" ? "rolled back"
      : r.action === "DEFER" ? "deferred" : "deploy now";
    const when = esc((r.t || "").replace("T", " ").slice(0, 16));
    const link = r.url ? `<a href="${esc(r.url)}">${when}</a>` : when;
    return `<tr><td class="num">${link}</td><td><span class="pill ${kind}">${label}</span></td>` +
      `<td>${esc(r.reason)}</td><td class="num">${r.g_per_kwh == null ? "–" : fmt(r.g_per_kwh, 0) + " g/kWh"}</td></tr>`;
  }).join("");
}

function renderProjection(k) {
  const input = document.getElementById("perday");
  try { const saved = localStorage.getItem("gg-perday"); if (saved) input.value = saved; } catch (_) { /* ignore */ }
  const update = () => {
    const n = Math.max(1, Number(input.value) || 1);
    try { localStorage.setItem("gg-perday", String(n)); } catch (_) { /* ignore */ }
    const g = k.saved_g_per_pipeline;
    if (g == null) {
      document.getElementById("projection").textContent = "–";
      document.getElementById("projection-basis").textContent = "Needs baseline and optimized runs in the ledger.";
      return;
    }
    const kgYear = (g * n * 365) / 1000;
    const minYear = (k.minutes_saved_per_pipeline || 0) * n * 365;
    document.getElementById("projection").textContent = `${fmt(kgYear, 2)} kg CO2e / year`;
    document.getElementById("projection-basis").textContent =
      `${fmt(g, 3)} g saved per run (measured) × ${n} runs/day × 365. Also ≈ ${Math.round(minYear).toLocaleString()} runner minutes a year.`;
  };
  input.addEventListener("input", update);
  update();
}

async function main() {
  let data;
  try {
    const res = await fetch("data.json", { cache: "no-store" });
    data = await res.json();
  } catch (err) {
    document.getElementById("generated").textContent = "Could not load ledger data.";
    return;
  }
  document.getElementById("generated").textContent =
    `Ledger snapshot ${String(data.generated_at || "").replace("T", " ").slice(0, 16)} UTC`;
  renderTiles(data.kpis || {});
  renderChart(data.series || []);
  renderDecisions(data.decisions || []);
  renderProjection(data.kpis || {});
}

document.addEventListener("DOMContentLoaded", main);
