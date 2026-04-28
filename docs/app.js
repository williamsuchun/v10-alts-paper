// v10 alts paper dashboard — Claude-style refined
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
  agoShort: (iso) => {
    if (!iso) return "—";
    const sec = (Date.now() - new Date(iso).getTime()) / 1000;
    if (sec < 90) return Math.round(sec) + "s";
    if (sec < 90*60) return Math.round(sec/60) + "m";
    if (sec < 36*3600) return Math.round(sec/3600) + "h";
    return Math.round(sec/86400) + "d";
  },
};

const $ = (id) => document.getElementById(id);
const setText = (id, t) => { const el = $(id); if (el) el.textContent = t; };
const setHTML = (id, t) => { const el = $(id); if (el) el.innerHTML = t; };
const cls = (val) => val >= 0 ? "gain" : "loss";

let charts = {};
let allComps = [];   // full comparison history cache
let currentPeriod = 168;  // hours; matches default active tab

// === Toast ===
function toast(msg, ms = 2000) {
  const t = $("toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => t.classList.remove("show"), ms);
}

// === Number flash on change ===
function setValue(id, newText, prevAttr = "data-prev") {
  const el = $(id); if (!el) return;
  const prev = el.getAttribute(prevAttr);
  if (prev !== newText && prev !== null) {
    el.classList.remove("value-flash");
    void el.offsetWidth;  // restart animation
    el.classList.add("value-flash");
  }
  el.textContent = newText;
  el.setAttribute(prevAttr, newText);
}

// === Fetch ===
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

// === Filter comparisons by period ===
function filterPeriod(rows, hours) {
  if (!hours) return rows;
  const cutoff = Date.now() - hours * 3600 * 1000;
  return rows.filter(r => new Date(r.ts).getTime() >= cutoff);
}

// === Hero + regime ===
function renderHero(state, comps) {
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

  setValue("total-eq", fmt.usd(total));
  const roiEl = $("total-roi");
  const roiPct = `${fmt.pct(totalRoi)}`;
  const cashLabel = `cash ${fmt.shortUsd(cash)}`;
  const floatLabel = `floating ${fmt.pct(floating/init * 100)}`;
  roiEl.innerHTML = `<span class="roi-pct ${cls(totalRoi)}">${roiPct}</span><span class="sep">·</span>${cashLabel}<span class="sep">·</span>${floatLabel}`;

  // 24h / 7d
  const eqAgo = (hours) => {
    const cutoff = Date.now() - hours * 3600 * 1000;
    const past = comps.find(c => new Date(c.ts).getTime() >= cutoff);
    return past ? past.paper_total : init;
  };
  const r24 = (total / eqAgo(24) - 1) * 100;
  const r7d = (total / eqAgo(7 * 24) - 1) * 100;
  setValue("roi-24h", fmt.pct(r24));
  $("roi-24h").className = "stat-value " + cls(r24);
  setValue("roi-7d", fmt.pct(r7d));
  $("roi-7d").className = "stat-value " + cls(r7d);
  setValue("n-pos", positions.length);

  // Max DD
  let peak = init, maxDD = 0;
  for (const c of comps) {
    if (c.paper_total > peak) peak = c.paper_total;
    const dd = (c.paper_total / peak - 1) * 100;
    if (dd < maxDD) maxDD = dd;
  }
  if (total > peak) peak = total;
  const curDD = (total / peak - 1) * 100;
  if (curDD < maxDD) maxDD = curDD;
  setValue("max-dd", fmt.pct(maxDD));

  // Regime bar
  const lev = state.current_lev;
  const btcVol = (state.btc_vol_cache || {}).vol_ann;
  let regime = "—";
  if (btcVol !== undefined) {
    const v = btcVol * 100;
    if (v < 40) regime = "🟢 calm";
    else if (v < 80) regime = "🟡 normal";
    else regime = "🔴 chaos";
    setValue("btc-vol", v.toFixed(1) + "%");
  }
  setValue("regime-badge", regime);
  setValue("cur-lev", lev ? lev.toFixed(2) + "x" : "—");

  const netExp = positions.reduce((s, p) => s + p.side * p.size_usd, 0);
  const netPct = init ? (netExp / init * 100) : 0;
  let netLabel;
  if (positions.length === 0) netLabel = "—";
  else if (Math.abs(netPct) < 5) netLabel = "≈ neutral";
  else netLabel = netExp >= 0 ? `+${netPct.toFixed(0)}% LONG` : `${netPct.toFixed(0)}% SHORT`;
  setValue("net-exp", netLabel);
  $("net-exp").className = "value-mono " + (positions.length === 0 ? "" : (Math.abs(netPct) < 5 ? "" : (netExp >= 0 ? "gain" : "loss")));

  // Workflow status
  const lastTs = state.last_check;
  if (lastTs) {
    const sinceMin = (Date.now() - new Date(lastTs).getTime()) / 60000;
    const dot = $("status-dot");
    if (sinceMin < 90) dot.className = "status-dot healthy";
    else if (sinceMin < 240) dot.className = "status-dot warn";
    else dot.className = "status-dot error";
  }
}

// === Top list ===
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
  setHTML("top-list", scored.map((x, i) => {
    const rankCls = i < 3 ? "gold" : "";
    return `<div class="top-row">
      <div style="display:flex;align-items:center;flex:1;min-width:0;">
        <span class="top-rank ${rankCls}">${i+1}</span>
        <span class="top-sym">${x.sym}</span>
      </div>
      <span class="top-pnl ${cls(x.pnl)}">${fmt.pct(x.pnl * 100)}</span>
    </div>`;
  }).join(""));
}

// === Positions ===
function renderPositions(state) {
  const positions = state.positions || [];
  const lastPrices = state.last_prices || {};
  setText("positions-sub", positions.length ? `${positions.length} open` : "no positions");
  if (!positions.length) {
    setHTML("positions-table", '<div class="empty">awaiting funding signals</div>');
    return;
  }
  const rows = positions.map(p => {
    const cur = lastPrices[p.sym] || p.entry_price;
    const ret = (cur / p.entry_price - 1) * p.side * 100;
    const sideLabel = p.side === 1 ? "LONG" : "SHORT";
    const sideCls = p.side === 1 ? "gain-bg" : "loss-bg";
    return `<tr>
      <td><strong>${p.sym}</strong></td>
      <td><span class="badge ${sideCls}">${sideLabel}</span></td>
      <td class="right">$${p.entry_price.toFixed(4)}</td>
      <td class="right">$${cur.toFixed(4)}</td>
      <td class="right ${cls(ret)}"><strong>${fmt.pct(ret)}</strong></td>
      <td class="right hide-mobile">${fmt.shortUsd(p.size_usd)}</td>
      <td class="right muted">${fmt.agoShort(p.entry_time)}</td>
    </tr>`;
  }).join("");
  setHTML("positions-table",
    `<table class="tbl"><thead><tr><th>Sym</th><th>Side</th><th class="right">Entry</th>` +
    `<th class="right">Now</th><th class="right">P&amp;L</th><th class="right hide-mobile">Size</th>` +
    `<th class="right">Held</th></tr></thead><tbody>${rows}</tbody></table>`);
}

// === Trades ===
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
        <td class="muted">${fmt.agoShort(t.ts)}</td>
        <td><span class="badge accent">OPEN</span></td>
        <td><strong>${t.sym}</strong></td>
        <td><span class="badge ${sideCls}">${sideLabel}</span></td>
        <td class="right">$${(t.entry_price || 0).toFixed(4)}</td>
        <td class="right hide-mobile">${fmt.shortUsd(t.size_usd)}</td>
        <td class="right muted hide-mobile">${(t.funding * 100 || 0).toFixed(4)}%</td>
        <td class="right">—</td>
      </tr>`;
    }
    const winCls = (t.pnl_usd || 0) >= 0 ? 'gain-bg' : 'loss-bg';
    return `<tr>
      <td class="muted">${fmt.agoShort(t.ts)}</td>
      <td><span class="badge ${winCls}">CLOSE</span></td>
      <td><strong>${t.sym}</strong></td>
      <td><span class="badge ${sideCls}">${sideLabel}</span></td>
      <td class="right">$${(t.exit_price || 0).toFixed(4)}</td>
      <td class="right muted hide-mobile">${fmt.shortUsd(t.size_usd)}</td>
      <td class="right muted hide-mobile">${(t.held_h || 0).toFixed(1)}h</td>
      <td class="right ${cls(t.pnl_usd)}"><strong>${fmt.usd(t.pnl_usd || 0)}</strong></td>
    </tr>`;
  }).join("");
  setHTML("trades-table",
    `<table class="tbl"><thead><tr><th>Time</th><th>Event</th><th>Sym</th>` +
    `<th>Side</th><th class="right">Price</th><th class="right hide-mobile">Size</th>` +
    `<th class="right hide-mobile">Fund/Held</th><th class="right">P&amp;L</th>` +
    `</tr></thead><tbody>${rows}</tbody></table>`);
}

// === Stats grid (7d) ===
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
    ["Trades", closes7d.length, ""],
    ["Win rate", wr.toFixed(0) + "%", wr >= 45 ? "gain" : (wr <= 30 ? "loss" : "")],
    ["Profit factor", pf.toFixed(2), pf >= 1 ? "gain" : "loss"],
    ["P&L", fmt.usd(totalPnl), totalPnl >= 0 ? "gain" : "loss"],
    ["Avg hold", avgHold.toFixed(1) + "h", ""],
    ["Stops hit", stops, ""],
    ["Friction", fmt.pct(avgFriction), Math.abs(avgFriction) < 5 ? "" : "loss"],
    ["Snapshots", compsRecent.length, ""],
  ];
  setHTML("stats-grid", rows.map(([k, v, c]) =>
    `<div class="stat-row"><span>${k}</span><span class="${c}">${v}</span></div>`).join(""));
}

// === Attribution ===
function renderAttribution(state) {
  const attr = state.pnl_attribution || {};
  const entries = Object.entries(attr).map(([sym, a]) => ({
    sym, total: a.total_pnl, n: a.n_trades, wins: a.wins, losses: a.losses,
    wr: a.n_trades ? (a.wins / a.n_trades * 100) : 0,
  }));
  entries.sort((a, b) => b.total - a.total);
  if (!entries.length) {
    setHTML("attribution-table", '<div class="empty">no closed trades yet</div>');
    return;
  }
  const rows = entries.map((e, i) => `<tr>
    <td><span class="top-rank ${i < 3 ? 'gold' : ''}">${i+1}</span></td>
    <td><strong>${e.sym}</strong></td>
    <td class="right ${cls(e.total)}"><strong>${fmt.usd(e.total)}</strong></td>
    <td class="right">${e.n}</td>
    <td class="right muted hide-mobile">${e.wins}W / ${e.losses}L</td>
    <td class="right">${e.wr.toFixed(0)}%</td>
  </tr>`).join("");
  setHTML("attribution-table",
    `<table class="tbl"><thead><tr><th></th><th>Sym</th><th class="right">Total P&amp;L</th>` +
    `<th class="right">Trades</th><th class="right hide-mobile">W/L</th><th class="right">WR</th>` +
    `</tr></thead><tbody>${rows}</tbody></table>`);
}

// === Charts ===
function chartTheme() {
  const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  return {
    isDark,
    grid: isDark ? "rgba(255,255,255,0.06)" : "rgba(42,40,35,0.06)",
    muted: isDark ? "#918d83" : "#8b867d",
    text: isDark ? "#f0eee9" : "#2a2823",
    bg: isDark ? "#232220" : "#ffffff",
  };
}

function renderEquityChart(comps) {
  const ctx = document.getElementById("equity-chart").getContext("2d");
  if (charts.equity) charts.equity.destroy();
  const filtered = filterPeriod(comps, currentPeriod);
  if (!filtered.length) {
    ctx.canvas.parentElement.innerHTML = '<div class="empty">no comparison data yet</div>';
    return;
  }
  const t = chartTheme();
  charts.equity = new Chart(ctx, {
    type: "line",
    data: {
      labels: filtered.map(c => fmt.time(c.ts)),
      datasets: [
        {label: "Paper", data: filtered.map(c => c.paper_total), borderColor: "#c96442",
         backgroundColor: "rgba(201,100,66,0.10)", fill: true, tension: 0.35, borderWidth: 2.5, pointRadius: 0,
         pointHoverRadius: 4, pointHoverBackgroundColor: "#c96442"},
        {label: "Shadow (no friction)", data: filtered.map(c => c.shadow_total),
         borderColor: "#5b8c6e", borderWidth: 1.5, tension: 0.35, pointRadius: 0, borderDash: [5, 5]},
        {label: "Backtest expected", data: filtered.map(c => c.bt_expected),
         borderColor: t.muted, borderWidth: 1, tension: 0.35, pointRadius: 0, borderDash: [2, 6]},
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: {mode: "index", intersect: false},
      plugins: {
        legend: {position: "bottom", labels: {boxWidth: 8, boxHeight: 8, padding: 14, color: t.muted, font: {size: 11, weight: "500"}, usePointStyle: true, pointStyle: "circle"}},
        tooltip: {backgroundColor: t.bg, titleColor: t.text, bodyColor: t.text, borderColor: t.grid, borderWidth: 1, padding: 12, cornerRadius: 8, boxPadding: 6, titleFont: {weight: "600", size: 12}, bodyFont: {size: 12}, displayColors: true, boxWidth: 8, boxHeight: 8, usePointStyle: true,
          callbacks: {label: (ctx) => `  ${ctx.dataset.label}: ${fmt.usd(ctx.parsed.y)}`}},
      },
      scales: {
        x: {ticks: {color: t.muted, font: {size: 10}, maxTicksLimit: 6, maxRotation: 0}, grid: {display: false}, border: {display: false}},
        y: {ticks: {color: t.muted, font: {size: 10}, callback: v => "$" + v.toLocaleString()}, grid: {color: t.grid, drawTicks: false}, border: {display: false}},
      },
    },
  });
}

function renderUnderwaterChart(comps) {
  const ctx = document.getElementById("underwater-chart").getContext("2d");
  if (charts.underwater) charts.underwater.destroy();
  if (!comps.length) return;
  let peak = comps[0].paper_total;
  const dds = comps.map(c => {
    if (c.paper_total > peak) peak = c.paper_total;
    return (c.paper_total / peak - 1) * 100;
  });
  const t = chartTheme();
  charts.underwater = new Chart(ctx, {
    type: "line",
    data: {
      labels: comps.map(c => fmt.time(c.ts)),
      datasets: [{
        label: "Drawdown %", data: dds,
        borderColor: "#b85450", backgroundColor: "rgba(184,84,80,0.16)",
        fill: true, tension: 0.35, pointRadius: 0, borderWidth: 1.5,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {legend: {display: false}, tooltip: {backgroundColor: t.bg, titleColor: t.text, bodyColor: t.text, borderColor: t.grid, borderWidth: 1, padding: 10, cornerRadius: 8, callbacks: {label: c => c.parsed.y.toFixed(2) + "%"}}},
      scales: {
        x: {ticks: {color: t.muted, font: {size: 9}, maxTicksLimit: 4, maxRotation: 0}, grid: {display: false}, border: {display: false}},
        y: {max: 0, ticks: {color: t.muted, font: {size: 9}, callback: v => v.toFixed(0) + "%"}, grid: {color: t.grid}, border: {display: false}},
      },
    },
  });
}

function renderFrictionChart(comps) {
  const ctx = document.getElementById("friction-chart").getContext("2d");
  if (charts.friction) charts.friction.destroy();
  if (!comps.length) return;
  const t = chartTheme();
  charts.friction = new Chart(ctx, {
    type: "line",
    data: {
      labels: comps.map(c => fmt.time(c.ts)),
      datasets: [{
        label: "Friction %", data: comps.map(c => c.friction_pct),
        borderColor: "#c96442", backgroundColor: "rgba(201,100,66,0.12)",
        fill: true, tension: 0.35, pointRadius: 0, borderWidth: 1.5,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {legend: {display: false}, tooltip: {backgroundColor: t.bg, titleColor: t.text, bodyColor: t.text, borderColor: t.grid, borderWidth: 1, padding: 10, cornerRadius: 8, callbacks: {label: c => c.parsed.y.toFixed(2) + "%"}}},
      scales: {
        x: {ticks: {color: t.muted, font: {size: 9}, maxTicksLimit: 4, maxRotation: 0}, grid: {display: false}, border: {display: false}},
        y: {ticks: {color: t.muted, font: {size: 9}, callback: v => v.toFixed(0) + "%"}, grid: {color: t.grid}, border: {display: false}},
      },
    },
  });
}

// === Period tab handler ===
document.querySelectorAll("#equity-period-tabs .period-tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#equity-period-tabs .period-tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentPeriod = parseInt(btn.dataset.period);
    renderEquityChart(allComps);
  });
});

// === Main load ===
async function loadAll(silent = false) {
  if (!silent) {
    const btn = $("refresh");
    btn.classList.add("spinning");
  }
  try {
    const [state, trades, comps] = await Promise.all([
      fetchJson("/state/paper_state.json"),
      fetchJsonl("/state/paper_trades.jsonl"),
      fetchJsonl("/state/comparison_history.jsonl"),
    ]);
    allComps = comps;
    renderHero(state, comps);
    renderTopList(state);
    renderPositions(state);
    renderTrades(trades);
    renderStats(trades, comps);
    renderAttribution(state);
    renderEquityChart(comps);
    renderUnderwaterChart(comps);
    renderFrictionChart(comps);
    const lastTs = state.last_check || (comps.length ? comps[comps.length-1].ts : null);
    setText("last-updated", "");
    $("last-updated").innerHTML = `<span id="status-dot" class="status-dot healthy"></span>Updated ${fmt.ago(lastTs)}`;
    if (!silent) toast("Refreshed");
  } catch (e) {
    console.error(e);
    setText("last-updated", "");
    $("last-updated").innerHTML = `<span class="status-dot error"></span>Error loading`;
    if (!silent) toast("Error loading data");
  } finally {
    $("refresh").classList.remove("spinning");
  }
}

document.getElementById("refresh").addEventListener("click", () => loadAll(false));
loadAll(true);
setInterval(() => loadAll(true), 5 * 60 * 1000);

// Update "ago" text every minute even without re-fetching
setInterval(() => {
  if (allComps.length) {
    const lastTs = allComps[allComps.length-1].ts;
    const dot = $("status-dot")?.outerHTML || '<span class="status-dot healthy"></span>';
    $("last-updated").innerHTML = `${dot}Updated ${fmt.ago(lastTs)}`;
  }
}, 60 * 1000);

// =================== HERO SPARKLINE ===================
function renderHeroSparkline(comps) {
  const canvas = $("hero-sparkline");
  if (!canvas) return;
  const recent = comps.slice(-Math.min(comps.length, 168));  // last 7d
  if (!recent.length) return;
  const ctx = canvas.getContext("2d");
  if (charts.heroSpark) charts.heroSpark.destroy();
  // determine trend color
  const first = recent[0].paper_total;
  const last = recent[recent.length-1].paper_total;
  const positive = last >= first;
  const color = positive ? "#5b8c6e" : "#b85450";
  charts.heroSpark = new Chart(ctx, {
    type: "line",
    data: {
      labels: recent.map((_, i) => i),
      datasets: [{
        data: recent.map(c => c.paper_total),
        borderColor: color,
        backgroundColor: positive ? "rgba(91,140,110,0.10)" : "rgba(184,84,80,0.10)",
        fill: true, tension: 0.4, pointRadius: 0, borderWidth: 1.5,
      }],
    },
    options: {
      responsive: false, maintainAspectRatio: false,
      plugins: {legend: {display: false}, tooltip: {enabled: false}},
      scales: {x: {display: false}, y: {display: false}},
      animation: {duration: 600, easing: "easeOutQuart"},
    },
  });
}

// Hook into existing render flow — augment loadAll
const _origRenderHero = renderHero;
function renderHeroWithSpark(state, comps) {
  _origRenderHero(state, comps);
  renderHeroSparkline(comps);
}
renderHero = renderHeroWithSpark;

// =================== MODAL (sym detail) ===================
function showSymModal(sym, state, trades) {
  const attr = (state.pnl_attribution || {})[sym];
  const sp = (state.shadow_pnl || {})[sym];
  const positions = (state.positions || []).filter(p => p.sym === sym);
  const symTrades = trades.filter(t => t.sym === sym).slice(-10).reverse();

  $("modal-title").textContent = sym;
  $("modal-sub").textContent = positions.length ? `Currently ${positions[0].side === 1 ? "LONG" : "SHORT"}` : "No active position";

  let html = '<div class="modal-stat-grid">';
  if (attr) {
    html += `<div><span>Total P&L</span><span class="${cls(attr.total_pnl)}">${fmt.usd(attr.total_pnl)}</span></div>`;
    html += `<div><span>Trades</span><span>${attr.n_trades}</span></div>`;
    html += `<div><span>Wins / Losses</span><span>${attr.wins}W / ${attr.losses}L</span></div>`;
    html += `<div><span>Win rate</span><span>${attr.n_trades ? (attr.wins/attr.n_trades*100).toFixed(0)+"%" : "—"}</span></div>`;
  }
  if (sp && sp.rets && sp.rets.length) {
    const trail = sp.rets.reduce((a,b)=>a+b, 0);
    html += `<div><span>14d shadow P&L</span><span class="${cls(trail)}">${fmt.pct(trail*100)}</span></div>`;
    html += `<div><span>Shadow position</span><span>${sp.pos === 1 ? "📈 LONG" : sp.pos === -1 ? "📉 SHORT" : "—"}</span></div>`;
  }
  html += '</div>';

  if (positions.length) {
    const p = positions[0];
    const cur = (state.last_prices || {})[sym] || p.entry_price;
    const ret = (cur / p.entry_price - 1) * p.side * 100;
    html += `<h4 style="margin:16px 0 8px;font-size:13px;font-weight:600;">Active Position</h4>
      <div class="modal-stat-grid">
        <div><span>Entry</span><span>$${p.entry_price.toFixed(4)}</span></div>
        <div><span>Now</span><span>$${cur.toFixed(4)}</span></div>
        <div><span>Unrealized</span><span class="${cls(ret)}">${fmt.pct(ret)}</span></div>
        <div><span>Size</span><span>${fmt.shortUsd(p.size_usd)}</span></div>
        <div><span>Held</span><span>${fmt.agoShort(p.entry_time)}</span></div>
        <div><span>Funding @ entry</span><span>${(p.funding_at_entry*100||0).toFixed(4)}%</span></div>
      </div>`;
  }

  if (symTrades.length) {
    html += `<h4 style="margin:16px 0 8px;font-size:13px;font-weight:600;">Recent ${symTrades.length} Trades</h4>
      <table class="tbl"><thead><tr><th>Time</th><th>Event</th><th>Side</th><th class="right">Price</th><th class="right">P&L</th></tr></thead><tbody>`;
    for (const t of symTrades) {
      const sideLabel = t.side === 1 ? "LONG" : "SHORT";
      const sideCls = t.side === 1 ? "gain-bg" : "loss-bg";
      const evCls = t.event === "open" ? "accent" : ((t.pnl_usd||0) >= 0 ? "gain-bg" : "loss-bg");
      const price = t.event === "open" ? (t.entry_price||0) : (t.exit_price||0);
      const pnl = t.event === "close" ? `<span class="${cls(t.pnl_usd)}">${fmt.usd(t.pnl_usd||0)}</span>` : "—";
      html += `<tr>
        <td class="muted">${fmt.agoShort(t.ts)}</td>
        <td><span class="badge ${evCls}">${t.event.toUpperCase()}</span></td>
        <td><span class="badge ${sideCls}">${sideLabel}</span></td>
        <td class="right">$${price.toFixed(4)}</td>
        <td class="right">${pnl}</td>
      </tr>`;
    }
    html += "</tbody></table>";
  }

  $("modal-content").innerHTML = html;
  $("modal-backdrop").classList.add("show");
  $("modal-backdrop").setAttribute("aria-hidden", "false");
}

function closeModal() {
  $("modal-backdrop").classList.remove("show");
  $("modal-backdrop").setAttribute("aria-hidden", "true");
}

$("modal-close").addEventListener("click", closeModal);
$("modal-backdrop").addEventListener("click", e => { if (e.target.id === "modal-backdrop") closeModal(); });

// Make sym pills/rows clickable to open modal — set up after each render
let _lastState = null, _lastTrades = [];
const _origLoad = loadAll;
loadAll = async function(silent) {
  const result = await _origLoad(silent);
  // re-attach click handlers
  setTimeout(attachSymClicks, 100);
  return result;
};

function attachSymClicks() {
  document.querySelectorAll("[data-sym]").forEach(el => {
    if (el._symAttached) return;
    el._symAttached = true;
    el.classList.add("clickable");
    el.addEventListener("click", () => showSymModal(el.dataset.sym, _lastState, _lastTrades));
  });
}

// Wrap renderTopList / Positions / Attribution to add data-sym
const _origRenderTopList = renderTopList;
renderTopList = function(state) {
  _origRenderTopList(state);
  document.querySelectorAll(".top-row").forEach(row => {
    const sym = row.querySelector(".top-sym")?.textContent;
    if (sym) row.setAttribute("data-sym", sym);
  });
};

const _origRenderPositions = renderPositions;
renderPositions = function(state) {
  _origRenderPositions(state);
  document.querySelectorAll("#positions-table tbody tr").forEach(tr => {
    const sym = tr.querySelector("td strong")?.textContent;
    if (sym) tr.setAttribute("data-sym", sym);
  });
};

const _origRenderAttr = renderAttribution;
renderAttribution = function(state) {
  _origRenderAttr(state);
  document.querySelectorAll("#attribution-table tbody tr").forEach(tr => {
    const cells = tr.querySelectorAll("td");
    const sym = cells[1]?.querySelector("strong")?.textContent;
    if (sym) tr.setAttribute("data-sym", sym);
  });
};

// Cache state/trades for modal to use
const _origLoad2 = loadAll;
loadAll = async function(silent) {
  if (!silent) $("refresh").classList.add("spinning");
  try {
    const [state, trades, comps] = await Promise.all([
      fetchJson("/state/paper_state.json"),
      fetchJsonl("/state/paper_trades.jsonl"),
      fetchJsonl("/state/comparison_history.jsonl"),
    ]);
    _lastState = state; _lastTrades = trades;
    allComps = comps;
    renderHero(state, comps);
    renderTopList(state);
    renderPositions(state);
    renderTrades(trades);
    renderStats(trades, comps);
    renderAttribution(state);
    renderEquityChart(comps);
    renderUnderwaterChart(comps);
    renderFrictionChart(comps);
    const lastTs = state.last_check || (comps.length ? comps[comps.length-1].ts : null);
    $("last-updated").innerHTML = `<span id="status-dot" class="status-dot healthy"></span>Updated ${fmt.ago(lastTs)}`;
    setTimeout(attachSymClicks, 50);
    if (!silent) toast("Refreshed");
  } catch (e) {
    console.error(e);
    $("last-updated").innerHTML = `<span class="status-dot error"></span>Error loading`;
    if (!silent) toast("Error loading data");
  } finally {
    $("refresh").classList.remove("spinning");
  }
};

// =================== COMMAND PALETTE ===================
const COMMANDS = [
  {section: "View", name: "Switch to 24h period", icon: "⏱", shortcut: "1", fn: () => switchPeriod(24)},
  {section: "View", name: "Switch to 7d period", icon: "📅", shortcut: "2", fn: () => switchPeriod(168)},
  {section: "View", name: "Switch to 30d period", icon: "📆", shortcut: "3", fn: () => switchPeriod(720)},
  {section: "View", name: "Switch to all-time period", icon: "♾", shortcut: "4", fn: () => switchPeriod(0)},
  {section: "Action", name: "Refresh data", icon: "↻", shortcut: "R", fn: () => loadAll(false)},
  {section: "Action", name: "Toggle theme (light/dark)", icon: "◐", shortcut: "T", fn: toggleTheme},
  {section: "Action", name: "Open repository on GitHub", icon: "↗", fn: () => window.open("https://github.com/williamsuchun/v10-alts-paper", "_blank")},
  {section: "Action", name: "View latest workflow run", icon: "⚙", fn: () => window.open("https://github.com/williamsuchun/v10-alts-paper/actions", "_blank")},
];

let cmdkActive = -1;

function openCmdK() {
  $("cmdk-backdrop").classList.add("show");
  $("cmdk-backdrop").setAttribute("aria-hidden", "false");
  $("cmdk-input").value = "";
  cmdkActive = 0;
  renderCmdK("");
  setTimeout(() => $("cmdk-input").focus(), 50);
}
function closeCmdK() {
  $("cmdk-backdrop").classList.remove("show");
  $("cmdk-backdrop").setAttribute("aria-hidden", "true");
}

function renderCmdK(query) {
  const q = query.toLowerCase().trim();
  // Add sym commands dynamically
  const sp = (_lastState?.shadow_pnl) || {};
  const symCommands = Object.keys(sp).map(sym => ({
    section: "Symbols", name: `Open ${sym} details`, icon: "🪙", fn: () => { closeCmdK(); showSymModal(sym, _lastState, _lastTrades); }
  }));
  const all = [...COMMANDS, ...symCommands];
  const matches = q ? all.filter(c => c.name.toLowerCase().includes(q) || (c.section || "").toLowerCase().includes(q)) : all;
  if (!matches.length) {
    $("cmdk-results").innerHTML = '<div class="cmdk-empty">No commands match.</div>';
    return;
  }
  // Group by section
  let html = "";
  let lastSection = null;
  matches.forEach((c, i) => {
    if (c.section !== lastSection) {
      html += `<div class="cmdk-section-label">${c.section}</div>`;
      lastSection = c.section;
    }
    const activeCls = i === cmdkActive ? "active" : "";
    html += `<div class="cmdk-item ${activeCls}" data-idx="${i}">
      <span class="cmd-icon">${c.icon || "→"}</span>
      <div class="cmd-text">
        <div class="cmd-name">${c.name}</div>
      </div>
      ${c.shortcut ? `<span class="cmd-shortcut"><span class="kbd">${c.shortcut}</span></span>` : ""}
    </div>`;
  });
  $("cmdk-results").innerHTML = html;
  // Click handlers
  document.querySelectorAll(".cmdk-item").forEach(el => {
    el.addEventListener("click", () => {
      const idx = parseInt(el.dataset.idx);
      const cmd = matches[idx];
      if (cmd) { closeCmdK(); cmd.fn(); }
    });
  });
}

$("cmdk-input").addEventListener("input", e => { cmdkActive = 0; renderCmdK(e.target.value); });

document.addEventListener("keydown", e => {
  // Cmd+K / Ctrl+K
  if ((e.metaKey || e.ctrlKey) && e.key === "k") {
    e.preventDefault();
    if ($("cmdk-backdrop").classList.contains("show")) closeCmdK(); else openCmdK();
    return;
  }
  // Esc closes overlays
  if (e.key === "Escape") {
    closeCmdK();
    closeModal();
    return;
  }
  // CmdK navigation
  if ($("cmdk-backdrop").classList.contains("show")) {
    const items = document.querySelectorAll(".cmdk-item");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      cmdkActive = Math.min(items.length - 1, cmdkActive + 1);
      renderCmdK($("cmdk-input").value);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      cmdkActive = Math.max(0, cmdkActive - 1);
      renderCmdK($("cmdk-input").value);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const active = document.querySelector(".cmdk-item.active");
      if (active) active.click();
    }
    return;
  }
  // Skip shortcut handling if user typing in input
  if (document.activeElement?.tagName === "INPUT" || document.activeElement?.tagName === "TEXTAREA") return;
  // Quick shortcuts
  if (e.key === "r" || e.key === "R") { e.preventDefault(); loadAll(false); }
  else if (e.key === "t" || e.key === "T") { e.preventDefault(); toggleTheme(); }
  else if (e.key === "1") { e.preventDefault(); switchPeriod(24); }
  else if (e.key === "2") { e.preventDefault(); switchPeriod(168); }
  else if (e.key === "3") { e.preventDefault(); switchPeriod(720); }
  else if (e.key === "4") { e.preventDefault(); switchPeriod(0); }
});

$("cmdk-backdrop").addEventListener("click", e => { if (e.target.id === "cmdk-backdrop") closeCmdK(); });
$("cmdk-trigger").addEventListener("click", openCmdK);

// =================== PERIOD SWITCH ===================
function switchPeriod(hours) {
  currentPeriod = hours;
  document.querySelectorAll("#equity-period-tabs .period-tab").forEach(b => {
    b.classList.toggle("active", parseInt(b.dataset.period) === hours);
  });
  if (allComps.length) renderEquityChart(allComps);
  toast(`Period: ${hours === 24 ? "24h" : hours === 168 ? "7d" : hours === 720 ? "30d" : "All"}`);
}

// =================== THEME TOGGLE ===================
function toggleTheme() {
  const cur = document.documentElement.dataset.theme;
  const sysIsDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const effective = cur || (sysIsDark ? "dark" : "light");
  const next = effective === "dark" ? "light" : "dark";
  document.documentElement.classList.add("theme-transitioning");
  document.documentElement.dataset.theme = next;
  $("theme-icon").textContent = next === "dark" ? "◑" : "◐";
  localStorage.setItem("theme", next);
  setTimeout(() => document.documentElement.classList.remove("theme-transitioning"), 350);
  // Re-render charts with new theme
  if (allComps.length) {
    setTimeout(() => {
      renderEquityChart(allComps);
      renderUnderwaterChart(allComps);
      renderFrictionChart(allComps);
      renderHeroSparkline(allComps);
    }, 100);
  }
  toast(`Theme: ${next}`);
}
$("theme-toggle").addEventListener("click", toggleTheme);

// Restore saved theme
const savedTheme = localStorage.getItem("theme");
if (savedTheme) {
  document.documentElement.dataset.theme = savedTheme;
  setTimeout(() => { $("theme-icon").textContent = savedTheme === "dark" ? "◑" : "◐"; }, 0);
}

// =================== SHORTCUT HINT (first visit) ===================
if (!localStorage.getItem("kbd-hint-seen")) {
  setTimeout(() => {
    $("kbd-hint-banner").classList.add("show");
    setTimeout(() => $("kbd-hint-banner").classList.remove("show"), 6000);
    localStorage.setItem("kbd-hint-seen", "1");
  }, 1500);
}
