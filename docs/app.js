// Fetch state from raw.githubusercontent (cache-busted) and render
const RAW = "https://raw.githubusercontent.com/williamsuchun/v10-alts-paper/main";

const fmt = {
  usd: (n) => "$" + (n || 0).toLocaleString("en-US", {maximumFractionDigits: 2, minimumFractionDigits: 2}),
  pct: (n) => (n >= 0 ? "+" : "") + (n || 0).toFixed(2) + "%",
  shortUsd: (n) => "$" + Math.round(n || 0).toLocaleString("en-US"),
  num: (n) => Math.round(n).toLocaleString("en-US"),
  time: (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString("en-US", {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"});
  },
  ago: (iso) => {
    if (!iso) return "—";
    const sec = (Date.now() - new Date(iso).getTime()) / 1000;
    if (sec < 90) return Math.round(sec) + "s ago";
    if (sec < 90*60) return Math.round(sec/60) + "m ago";
    if (sec < 36*3600) return Math.round(sec/3600) + "h ago";
    return Math.round(sec/86400) + "d ago";
  },
};

const $ = (id) => document.getElementById(id);
const setText = (id, t) => { $(id).textContent = t; };
const setHTML = (id, t) => { $(id).innerHTML = t; };
const cls = (val) => val >= 0 ? "gain" : "loss";

let charts = {};

async function fetchJson(path) {
  const r = await fetch(`${RAW}${path}?t=${Date.now()}`, {cache: "no-store"});
  if (!r.ok) throw new Error(`fetch ${path}: ${r.status}`);
  return r.json();
}

async function fetchJsonl(path) {
  const r = await fetch(`${RAW}${path}?t=${Date.now()}`, {cache: "no-store"});
  if (!r.ok) return [];
  const text = await r.text();
  return text.trim().split("\n").filter(Boolean).map(l => {
    try { return JSON.parse(l); } catch { return null; }
  }).filter(Boolean);
}

function rocCard(state, comps) {
  const init = state.initial_capital || 10000;
  const cash = state.equity || init;
  const positions = state.positions || [];
  const lastPrices = state.last_prices || {};
  let floating = 0;
  for (const p of positions) {
    const cur = lastPrices[p.sym] || p.entry_price;
    floating += (cur / p.entry_price - 1) * p.side * p.size_usd;
  }
  const total = cash + floating;
  const totalRoi = (total / init - 1) * 100;

  setText("total-eq", fmt.usd(total));
  const roiEl = $("total-roi");
  roiEl.textContent = `${fmt.pct(totalRoi)}  ·  cash ${fmt.shortUsd(cash)}  ·  floating ${fmt.pct(floating/init * 100)}`;
  roiEl.className = "hero-roi " + cls(totalRoi);

  // 24h / 7d
  const eqAgo = (hours) => {
    const cutoff = Date.now() - hours * 3600 * 1000;
    const past = comps.find(c => new Date(c.ts).getTime() >= cutoff);
    return past ? past.paper_total : init;
  };
  const eq24 = eqAgo(24);
  const eq7d = eqAgo(7 * 24);
  const r24 = (total / eq24 - 1) * 100;
  const r7d = (total / eq7d - 1) * 100;
  $("roi-24h").textContent = fmt.pct(r24);
  $("roi-24h").className = "stat-value " + cls(r24);
  $("roi-7d").textContent = fmt.pct(r7d);
  $("roi-7d").className = "stat-value " + cls(r7d);
  $("n-pos").textContent = positions.length;
}

function renderTopList(state) {
  const sp = state.shadow_pnl || {};
  const scored = Object.entries(sp)
    .filter(([_, i]) => i.rets && i.rets.length)
    .map(([s, i]) => ({sym: s, pnl: i.rets.reduce((a, b) => a + b, 0)}))
    .sort((a, b) => b.pnl - a.pnl)
    .slice(0, 10);
  if (!scored.length) {
    setHTML("top-list", '<div class="empty">no shadow data yet</div>');
    return;
  }
  setHTML("top-list", scored.map(x =>
    `<div class="top-row"><span class="top-sym">${x.sym}</span>` +
    `<span class="top-pnl ${cls(x.pnl)}">${fmt.pct(x.pnl * 100)}</span></div>`
  ).join(""));
}

function renderPositions(state) {
  const positions = state.positions || [];
  const lastPrices = state.last_prices || {};
  if (!positions.length) {
    setHTML("positions-table", '<div class="empty">no open positions</div>');
    return;
  }
  const rows = positions.map(p => {
    const cur = lastPrices[p.sym] || p.entry_price;
    const ret = (cur / p.entry_price - 1) * p.side * 100;
    const sideLabel = p.side === 1 ? "LONG" : "SHORT";
    const sideCls = p.side === 1 ? "gain-bg" : "loss-bg";
    return `<tr>
      <td>${p.sym}</td>
      <td><span class="badge ${sideCls}">${sideLabel}</span></td>
      <td class="right">$${p.entry_price.toFixed(4)}</td>
      <td class="right">$${cur.toFixed(4)}</td>
      <td class="right ${cls(ret)}">${fmt.pct(ret)}</td>
      <td class="right">${fmt.shortUsd(p.size_usd)}</td>
      <td class="right muted">${fmt.ago(p.entry_time)}</td>
    </tr>`;
  }).join("");
  setHTML("positions-table",
    `<table class="tbl"><thead><tr><th>Sym</th><th>Side</th><th class="right">Entry</th>` +
    `<th class="right">Now</th><th class="right">P&amp;L</th><th class="right">Size</th>` +
    `<th class="right">Held</th></tr></thead><tbody>${rows}</tbody></table>`);
}

function renderTrades(trades) {
  const recent = trades.filter(t => t.event === "open" || t.event === "close").slice(-20).reverse();
  if (!recent.length) {
    setHTML("trades-table", '<div class="empty">no trades yet</div>');
    return;
  }
  const rows = recent.map(t => {
    const ev = t.event;
    const sideLabel = t.side === 1 ? "LONG" : "SHORT";
    const sideCls = t.side === 1 ? "gain-bg" : "loss-bg";
    if (ev === "open") {
      return `<tr>
        <td class="muted">${fmt.ago(t.ts)}</td>
        <td><span class="badge">OPEN</span></td>
        <td>${t.sym}</td>
        <td><span class="badge ${sideCls}">${sideLabel}</span></td>
        <td class="right">$${(t.entry_price || 0).toFixed(4)}</td>
        <td class="right">${fmt.shortUsd(t.size_usd)}</td>
        <td class="right muted">${(t.funding * 100 || 0).toFixed(4)}%</td>
        <td class="right">—</td>
      </tr>`;
    }
    return `<tr>
      <td class="muted">${fmt.ago(t.ts)}</td>
      <td><span class="badge ${(t.pnl_usd || 0) >= 0 ? 'gain-bg' : 'loss-bg'}">CLOSE</span></td>
      <td>${t.sym}</td>
      <td><span class="badge ${sideCls}">${sideLabel}</span></td>
      <td class="right">$${(t.exit_price || 0).toFixed(4)}</td>
      <td class="right muted">${fmt.shortUsd(t.size_usd)}</td>
      <td class="right muted">${(t.held_h || 0).toFixed(1)}h</td>
      <td class="right ${cls(t.pnl_usd)}">${fmt.usd(t.pnl_usd || 0)}</td>
    </tr>`;
  }).join("");
  setHTML("trades-table",
    `<table class="tbl"><thead><tr><th>Time</th><th>Event</th><th>Sym</th>` +
    `<th>Side</th><th class="right">Price</th><th class="right">Size</th>` +
    `<th class="right">Fund/Held</th><th class="right">P&amp;L</th>` +
    `</tr></thead><tbody>${rows}</tbody></table>`);
}

function renderStats(trades, comps) {
  const cutoff = Date.now() - 7 * 86400 * 1000;
  const closes7d = trades.filter(t => t.event === "close" && new Date(t.ts).getTime() >= cutoff);
  const wins = closes7d.filter(c => (c.pnl_usd || 0) > 0);
  const losses = closes7d.filter(c => (c.pnl_usd || 0) <= 0);
  const wr = closes7d.length ? (wins.length / closes7d.length * 100) : 0;
  const gp = wins.reduce((s, c) => s + c.pnl_usd, 0);
  const gl = Math.abs(losses.reduce((s, c) => s + c.pnl_usd, 0)) || 1e-9;
  const pf = gp / gl;
  const totalPnl = closes7d.reduce((s, c) => s + (c.pnl_usd || 0), 0);
  const avgHold = closes7d.length ? closes7d.reduce((s, c) => s + (c.held_h || 0), 0) / closes7d.length : 0;
  const compsRecent = comps.filter(c => new Date(c.ts).getTime() >= cutoff);
  const avgFriction = compsRecent.length ?
    compsRecent.reduce((s, c) => s + (c.friction_pct || 0), 0) / compsRecent.length : 0;
  const stops = closes7d.filter(c => c.reason === "stop_loss").length;

  const rows = [
    ["Trades closed", closes7d.length],
    ["Win rate", wr.toFixed(1) + "%"],
    ["Profit factor", pf.toFixed(2)],
    ["Realized P&L", fmt.usd(totalPnl)],
    ["Avg hold", avgHold.toFixed(1) + "h"],
    ["Stops triggered", stops],
    ["Avg friction", fmt.pct(avgFriction)],
    ["Snapshots", compsRecent.length],
  ];
  setHTML("stats-grid", rows.map(([k, v]) =>
    `<div class="stat-row"><span>${k}</span><span>${v}</span></div>`).join(""));
}

function renderEquityChart(comps) {
  const ctx = document.getElementById("equity-chart").getContext("2d");
  if (charts.equity) charts.equity.destroy();
  if (!comps.length) {
    ctx.canvas.parentElement.innerHTML = '<div class="empty">no comparison data yet</div>';
    return;
  }
  const labels = comps.map(c => fmt.time(c.ts));
  const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const grid = isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.05)";
  const muted = isDark ? "#918d83" : "#8b867d";

  charts.equity = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {label: "Paper", data: comps.map(c => c.paper_total), borderColor: "#c96442",
         backgroundColor: "rgba(201,100,66,0.08)", fill: true, tension: 0.3, borderWidth: 2, pointRadius: 0},
        {label: "Shadow (no friction)", data: comps.map(c => c.shadow_total),
         borderColor: "#5b8c6e", borderWidth: 1.5, tension: 0.3, pointRadius: 0, borderDash: [4, 4]},
        {label: "Backtest expected", data: comps.map(c => c.bt_expected),
         borderColor: muted, borderWidth: 1, tension: 0.3, pointRadius: 0, borderDash: [2, 6]},
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: {mode: "index", intersect: false},
      plugins: {
        legend: {position: "bottom", labels: {boxWidth: 10, boxHeight: 10, padding: 12, color: muted, font: {size: 11}}},
        tooltip: {backgroundColor: isDark ? "#25241f" : "#fff", titleColor: isDark ? "#ededeb" : "#2a2823",
                   bodyColor: isDark ? "#ededeb" : "#2a2823", borderColor: isDark ? "#34322c" : "#ebe9e4",
                   borderWidth: 1, padding: 10, cornerRadius: 6, boxPadding: 4,
                   callbacks: {label: (ctx) => ctx.dataset.label + ": " + fmt.usd(ctx.parsed.y)}},
      },
      scales: {
        x: {ticks: {color: muted, font: {size: 10}, maxTicksLimit: 6}, grid: {display: false}},
        y: {ticks: {color: muted, font: {size: 10}, callback: v => "$" + v.toLocaleString()}, grid: {color: grid}},
      },
    },
  });
}

function renderFrictionChart(comps) {
  const ctx = document.getElementById("friction-chart").getContext("2d");
  if (charts.friction) charts.friction.destroy();
  if (!comps.length) return;
  const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const grid = isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.05)";
  const muted = isDark ? "#918d83" : "#8b867d";

  charts.friction = new Chart(ctx, {
    type: "line",
    data: {
      labels: comps.map(c => fmt.time(c.ts)),
      datasets: [{
        label: "Friction %", data: comps.map(c => c.friction_pct),
        borderColor: "#c96442", backgroundColor: "rgba(201,100,66,0.1)",
        fill: true, tension: 0.3, pointRadius: 0, borderWidth: 1.5,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => c.parsed.y.toFixed(2) + "%"}}},
      scales: {
        x: {ticks: {color: muted, font: {size: 9}, maxTicksLimit: 4}, grid: {display: false}},
        y: {ticks: {color: muted, font: {size: 9}, callback: v => v.toFixed(0) + "%"}, grid: {color: grid}},
      },
    },
  });
}

async function loadAll() {
  setText("last-updated", "loading…");
  try {
    const [state, trades, comps] = await Promise.all([
      fetchJson("/state/paper_state.json"),
      fetchJsonl("/state/paper_trades.jsonl"),
      fetchJsonl("/state/comparison_history.jsonl"),
    ]);
    rocCard(state, comps);
    renderTopList(state);
    renderPositions(state);
    renderTrades(trades);
    renderStats(trades, comps);
    renderEquityChart(comps);
    renderFrictionChart(comps);
    const lastTs = state.last_check || (comps.length ? comps[comps.length-1].ts : null);
    setText("last-updated", "Updated " + fmt.ago(lastTs));
  } catch (e) {
    console.error(e);
    setText("last-updated", "Error loading data");
  }
}

document.getElementById("refresh").addEventListener("click", loadAll);
loadAll();
setInterval(loadAll, 5 * 60 * 1000);  // auto-refresh every 5 min
