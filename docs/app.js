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

// =================== STICKY HEADER ON SCROLL ===================
const stickyHeader = $("sticky-header");
const heroEl = document.querySelector(".hero");
function updateStickyHeader() {
  const heroBottom = heroEl.getBoundingClientRect().bottom;
  if (heroBottom < 20) {
    stickyHeader.classList.add("show");
    stickyHeader.setAttribute("aria-hidden", "false");
    // Sync values
    if (_lastState) {
      const init = _lastState.initial_capital || 10000;
      const cash = _lastState.equity || init;
      const positions = _lastState.positions || [];
      const lastPrices = _lastState.last_prices || {};
      let floating = 0;
      for (const p of positions) {
        const cur = lastPrices[p.sym] || p.entry_price;
        floating += (cur / p.entry_price - 1) * p.side * p.size_usd;
      }
      const total = cash + floating;
      const roi = (total / init - 1) * 100;
      $("sticky-eq").textContent = fmt.usd(total);
      $("sticky-roi").textContent = fmt.pct(roi);
      $("sticky-roi").className = cls(roi);
      const eq24 = (() => {
        const cutoff = Date.now() - 24 * 3600 * 1000;
        const past = allComps.find(c => new Date(c.ts).getTime() >= cutoff);
        return past ? past.paper_total : init;
      })();
      const r24 = (total / eq24 - 1) * 100;
      const e24 = $("sticky-24h");
      e24.textContent = " " + fmt.pct(r24);
      e24.className = cls(r24);
    }
  } else {
    stickyHeader.classList.remove("show");
    stickyHeader.setAttribute("aria-hidden", "true");
  }
}
window.addEventListener("scroll", updateStickyHeader, {passive: true});

// =================== TRADES SEARCH FILTER ===================
let _tradesFilter = "";
$("trades-search").addEventListener("input", (e) => {
  _tradesFilter = e.target.value.toLowerCase().trim();
  applyTradesFilter();
});
function applyTradesFilter() {
  const rows = document.querySelectorAll("#trades-table tbody tr");
  let visible = 0;
  rows.forEach(r => {
    const text = r.textContent.toLowerCase();
    const match = !_tradesFilter || text.includes(_tradesFilter);
    r.style.display = match ? "" : "none";
    if (match) visible++;
  });
  $("trades-sub").textContent = _tradesFilter
    ? `${visible} match · "${_tradesFilter}"`
    : "last 20 events";
}

// Re-apply filter after each render
const _origRenderTrades = renderTrades;
renderTrades = function(trades) {
  _origRenderTrades(trades);
  applyTradesFilter();
};

// =================== ATTRIBUTION INLINE SPARKLINES ===================
function buildSparkSVG(values, color, width = 60, height = 18) {
  if (!values || values.length < 2) return "";
  const max = Math.max(...values), min = Math.min(...values);
  const range = max - min || 1;
  const w = width, h = height;
  const step = w / (values.length - 1);
  const pts = values.map((v, i) => `${(i*step).toFixed(1)},${(h - ((v - min) / range) * h).toFixed(1)}`).join(" ");
  return `<svg class="attr-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

function buildSymPnlSeries(sym, trades) {
  // cumulative P&L over closed trades for this sym
  const closes = trades.filter(t => t.event === "close" && t.sym === sym);
  if (!closes.length) return [];
  let cum = 0;
  return closes.map(t => { cum += (t.pnl_usd || 0); return cum; });
}

const _origRenderAttribution = renderAttribution;
renderAttribution = function(state) {
  const trades = _lastTrades || [];
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
  const rows = entries.map((e, i) => {
    const series = buildSymPnlSeries(e.sym, trades);
    const sparkColor = e.total >= 0 ? "var(--gain)" : "var(--loss)";
    const spark = series.length >= 2 ? buildSparkSVG(series, e.total >= 0 ? "#5b8c6e" : "#b85450") : "";
    return `<tr data-sym="${e.sym}">
      <td><span class="top-rank ${i < 3 ? 'gold' : ''}">${i+1}</span></td>
      <td><strong>${e.sym}</strong></td>
      <td class="hide-mobile">${spark}</td>
      <td class="right ${cls(e.total)}"><strong>${fmt.usd(e.total)}</strong></td>
      <td class="right">${e.n}</td>
      <td class="right muted hide-mobile">${e.wins}W / ${e.losses}L</td>
      <td class="right">${e.wr.toFixed(0)}%</td>
    </tr>`;
  }).join("");
  setHTML("attribution-table",
    `<table class="tbl"><thead><tr><th></th><th>Sym</th><th class="hide-mobile">Trend</th>` +
    `<th class="right">Total P&amp;L</th><th class="right">Trades</th>` +
    `<th class="right hide-mobile">W/L</th><th class="right">WR</th>` +
    `</tr></thead><tbody>${rows}</tbody></table>`);
  setTimeout(attachSymClicks, 50);
};

// =================== COUNT-UP ANIMATION ===================
function countUp(el, end, duration = 800, formatter = (n) => n.toFixed(2)) {
  if (!el) return;
  const start = parseFloat(el.dataset.lastVal || "0");
  const t0 = performance.now();
  const ease = (t) => 1 - Math.pow(1 - t, 3);  // easeOutCubic
  function frame(now) {
    const t = Math.min(1, (now - t0) / duration);
    const v = start + (end - start) * ease(t);
    el.textContent = formatter(v);
    if (t < 1) requestAnimationFrame(frame);
    else el.dataset.lastVal = end;
  }
  requestAnimationFrame(frame);
}

// First-load count-up for hero equity
let _heroAnimated = false;
const _origRenderHero = renderHero;
renderHero = function(state, comps) {
  _origRenderHero(state, comps);
  if (!_heroAnimated) {
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
    const el = $("total-eq");
    if (el) {
      el.dataset.lastVal = "0";
      countUp(el, total, 1000, n => "$" + n.toLocaleString("en-US", {maximumFractionDigits: 2, minimumFractionDigits: 2}));
    }
    _heroAnimated = true;
  }
};

// =================== INSIGHTS ROW ===================
function renderInsights(state, trades, comps) {
  const insights = computeInsights(state, trades, comps);
  if (!insights.length) {
    $("insights-row").innerHTML = "";
    return;
  }
  $("insights-row").innerHTML = insights.map((ins, i) => `
    <div class="insight ${ins.type}" style="animation-delay: ${i * 0.05}s">
      <span class="insight-icon">${ins.icon}</span>
      <div class="insight-text">
        <div class="insight-title">${ins.title}</div>
        <div class="insight-desc">${ins.desc}</div>
      </div>
    </div>`).join("");
}

function computeInsights(state, trades, comps) {
  const out = [];
  const init = state.initial_capital || 10000;
  const positions = state.positions || [];
  const lastPrices = state.last_prices || {};
  let floating = 0;
  for (const p of positions) {
    const cur = lastPrices[p.sym] || p.entry_price;
    floating += (cur / p.entry_price - 1) * p.side * p.size_usd;
  }
  const total = (state.equity || init) + floating;

  // 1. Performance vs expected
  if (comps.length > 24) {
    const days = (Date.now() - new Date(comps[0].ts).getTime()) / 86400000;
    const expectedRoi = ((1.30 ** (days / 365) - 1) * 100);
    const actualRoi = (total / init - 1) * 100;
    const delta = actualRoi - expectedRoi;
    if (Math.abs(delta) > 3) {
      out.push({
        type: delta > 0 ? "gain" : "loss",
        icon: delta > 0 ? "📈" : "📉",
        title: `${Math.abs(delta).toFixed(1)}% ${delta > 0 ? "ahead of" : "behind"} expected`,
        desc: `paper ${actualRoi.toFixed(2)}% vs backtest projection ${expectedRoi.toFixed(2)}%`,
      });
    }
  }

  // 2. Recent winning/losing streak
  const closes24h = trades.filter(t => t.event === "close" &&
    (Date.now() - new Date(t.ts).getTime()) < 86400 * 1000).slice(-10);
  if (closes24h.length >= 3) {
    const wins = closes24h.filter(c => (c.pnl_usd || 0) > 0).length;
    const wr = wins / closes24h.length * 100;
    if (wr >= 70) {
      out.push({type: "gain", icon: "🔥", title: `Hot streak`, desc: `${wins}/${closes24h.length} winning trades in 24h (${wr.toFixed(0)}% WR)`});
    } else if (wr <= 25) {
      out.push({type: "warn", icon: "⚠️", title: `Cold streak`, desc: `only ${wins}/${closes24h.length} winning trades in 24h`});
    }
  }

  // 3. Single coin standout
  const attr = state.pnl_attribution || {};
  const ranked = Object.entries(attr).sort((a, b) => b[1].total_pnl - a[1].total_pnl);
  if (ranked.length) {
    const [topSym, topData] = ranked[0];
    if (topData.total_pnl > 100) {
      out.push({type: "gain", icon: "🏆", title: `${topSym} carrying`, desc: `+$${topData.total_pnl.toFixed(0)} across ${topData.n_trades} trades`});
    }
    const [worstSym, worstData] = ranked[ranked.length - 1];
    if (worstData.total_pnl < -100) {
      out.push({type: "loss", icon: "💀", title: `${worstSym} dragging`, desc: `-$${Math.abs(worstData.total_pnl).toFixed(0)} across ${worstData.n_trades} trades`});
    }
  }

  // 4. Workflow health
  if (state.last_check) {
    const sinceMin = (Date.now() - new Date(state.last_check).getTime()) / 60000;
    if (sinceMin > 90) {
      out.push({type: "warn", icon: "⏰", title: `Cron stale`, desc: `last check ${Math.floor(sinceMin)}m ago — investigate Actions`});
    }
  }

  // 5. Net exposure warning
  if (positions.length >= 5) {
    const net = positions.reduce((s, p) => s + p.side * p.size_usd, 0);
    const netPct = Math.abs(net / init * 100);
    if (netPct > 50) {
      const dir = net > 0 ? "LONG" : "SHORT";
      out.push({type: "warn", icon: "⚖️", title: `One-sided book`, desc: `net ${netPct.toFixed(0)}% ${dir} — concentration risk`});
    }
  }

  return out.slice(0, 4);  // max 4 insights
}

// =================== HEATMAP CALENDAR ===================
function renderHeatmap(comps) {
  const container = $("heatmap");
  if (!comps.length) {
    container.innerHTML = '<div class="empty">no daily data yet</div>';
    return;
  }
  // Bucket comps into daily P&L
  const dailyPnl = {};
  let prevTotal = comps[0].paper_total;
  let prevDay = new Date(comps[0].ts).toISOString().slice(0, 10);
  for (const c of comps) {
    const day = new Date(c.ts).toISOString().slice(0, 10);
    if (day !== prevDay) {
      dailyPnl[prevDay] = (dailyPnl[prevDay] || 0); // ensure key exists
      prevDay = day;
      prevTotal = c.paper_total;
    }
    const pnl = (c.paper_total / prevTotal - 1) * 100;
    dailyPnl[day] = pnl;
  }

  // 90 days back from today, organize by week (Mon-Sun)
  const today = new Date(); today.setHours(0,0,0,0);
  const daysBack = 90;
  const cells = [];
  for (let i = daysBack - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    cells.push({date: d, key, pnl: dailyPnl[key], hasData: key in dailyPnl});
  }
  // Find max abs pnl for color scaling
  const maxAbs = Math.max(0.5, ...cells.filter(c => c.hasData).map(c => Math.abs(c.pnl || 0)));
  const colorClass = (pnl) => {
    if (pnl == null || pnl === 0) return "";
    const p = Math.abs(pnl) / maxAbs;
    const lvl = p > 0.75 ? 4 : p > 0.5 ? 3 : p > 0.25 ? 2 : 1;
    return (pnl > 0 ? "gain-" : "loss-") + lvl;
  };

  // Build grid: columns = weeks, rows = days of week
  // Start each column on Monday
  const grid = []; // grid[col][row]
  let col = []; let curWeek = -1;
  for (const c of cells) {
    const dow = (c.date.getDay() + 6) % 7; // Mon=0
    const week = Math.floor((c.date - new Date(c.date.getFullYear(), 0, 1)) / (7*86400000));
    if (week !== curWeek) {
      if (col.length) grid.push(col);
      col = new Array(7).fill(null);
      curWeek = week;
    }
    col[dow] = c;
  }
  if (col.length) grid.push(col);

  const cellsHtml = grid.map(week => {
    const cellsCol = week.map((c, dow) => {
      if (!c) return `<div class="heatmap-cell empty"></div>`;
      const cls = c.hasData ? colorClass(c.pnl) : "";
      const label = c.hasData
        ? `${c.key}: ${(c.pnl >= 0 ? "+" : "")}${c.pnl.toFixed(2)}%`
        : `${c.key}: no data`;
      return `<div class="heatmap-cell ${cls}" title="${label}"></div>`;
    }).join("");
    return cellsCol;
  }).join("");

  container.innerHTML = `<div class="heatmap-grid">${cellsHtml}</div>`;
}

// =================== ACTIVITY FEED ===================
function renderActivityFeed(trades) {
  const events = trades.filter(t => t.event === "open" || t.event === "close").slice(-30).reverse();
  if (!events.length) {
    setHTML("activity-feed", '<div class="empty">no events yet</div>');
    return;
  }
  $("activity-feed").innerHTML = events.map(t => {
    const sideLabel = t.side === 1 ? "LONG" : "SHORT";
    let iconCls, title, meta;
    if (t.event === "open") {
      iconCls = "open";
      title = `Opened <strong data-sym="${t.sym}">${t.sym}</strong> ${sideLabel}`;
      meta = `@$${(t.entry_price || 0).toFixed(4)} · ${fmt.shortUsd(t.size_usd)} · funding ${(t.funding * 100 || 0).toFixed(4)}%`;
    } else {
      const win = (t.pnl_usd || 0) >= 0;
      iconCls = win ? "close-gain" : "close-loss";
      title = `Closed <strong data-sym="${t.sym}">${t.sym}</strong> ${sideLabel} <span class="${win ? 'gain' : 'loss'}">${fmt.usd(t.pnl_usd || 0)}</span>`;
      meta = `@$${(t.exit_price || 0).toFixed(4)} · ${(t.held_h || 0).toFixed(1)}h · ${t.reason || ""}`;
    }
    return `<div class="activity-item">
      <span class="activity-icon ${iconCls}"></span>
      <div class="activity-content">
        <div class="activity-title">${title}</div>
        <div class="activity-meta">${fmt.ago(t.ts)} · ${meta}</div>
      </div>
    </div>`;
  }).join("");
}

// =================== HOVER PREVIEW ===================
let _previewTimer;
function attachHoverPreviews() {
  document.querySelectorAll("[data-sym]").forEach(el => {
    if (el._previewAttached) return;
    el._previewAttached = true;
    el.addEventListener("mouseenter", e => {
      clearTimeout(_previewTimer);
      _previewTimer = setTimeout(() => showPreview(el.dataset.sym, e), 250);
    });
    el.addEventListener("mouseleave", () => {
      clearTimeout(_previewTimer);
      hidePreview();
    });
    el.addEventListener("mousemove", e => {
      const pop = $("sym-preview");
      if (pop && pop.classList.contains("show")) positionPreview(e);
    });
  });
}

function positionPreview(e) {
  const pop = $("sym-preview");
  if (!pop) return;
  const w = 240, h = pop.offsetHeight || 120;
  let left = e.clientX + 16;
  let top = e.clientY + 16;
  if (left + w > window.innerWidth - 16) left = e.clientX - w - 16;
  if (top + h > window.innerHeight - 16) top = e.clientY - h - 16;
  pop.style.left = left + "px";
  pop.style.top = top + "px";
}

function showPreview(sym, e) {
  if (!_lastState) return;
  const attr = (_lastState.pnl_attribution || {})[sym];
  const sp = (_lastState.shadow_pnl || {})[sym];
  $("preview-sym").textContent = sym;
  const trail = sp && sp.rets ? sp.rets.reduce((a,b)=>a+b, 0) * 100 : 0;
  $("preview-pnl").textContent = sp && sp.rets ? fmt.pct(trail) : "—";
  $("preview-pnl").className = "mono " + (trail >= 0 ? "gain" : "loss");

  // mini chart of shadow rets cumulative
  const ctx = $("preview-chart").getContext("2d");
  if (charts.preview) charts.preview.destroy();
  if (sp && sp.rets && sp.rets.length > 5) {
    let cum = 1;
    const series = sp.rets.map(r => { cum *= (1 + r); return cum; });
    charts.preview = new Chart(ctx, {
      type: "line",
      data: {labels: series.map((_, i) => i), datasets: [{
        data: series,
        borderColor: trail >= 0 ? "#5b8c6e" : "#b85450",
        backgroundColor: trail >= 0 ? "rgba(91,140,110,0.15)" : "rgba(184,84,80,0.15)",
        fill: true, tension: 0.4, pointRadius: 0, borderWidth: 1.5,
      }]},
      options: {
        responsive: false, maintainAspectRatio: false,
        plugins: {legend: {display: false}, tooltip: {enabled: false}},
        scales: {x: {display: false}, y: {display: false}},
        animation: false,
      },
    });
  }
  let stats = "";
  if (attr) stats += `<span>${attr.n_trades} trades</span><span>${attr.wins}W / ${attr.losses}L</span><span>${attr.n_trades ? (attr.wins/attr.n_trades*100).toFixed(0)+"%" : "—"} WR</span>`;
  $("preview-stats").innerHTML = stats;
  positionPreview(e);
  $("sym-preview").classList.add("show");
  $("sym-preview").setAttribute("aria-hidden", "false");
}
function hidePreview() {
  $("sym-preview").classList.remove("show");
  $("sym-preview").setAttribute("aria-hidden", "true");
}

// =================== POSITION LIFECYCLE BAR ===================
const _origRenderPositions2 = renderPositions;
renderPositions = function(state) {
  _origRenderPositions2(state);
  // Replace "Held" cell with held + lifecycle bar
  const positions = state.positions || [];
  const rows = document.querySelectorAll("#positions-table tbody tr");
  rows.forEach((row, i) => {
    const p = positions[i];
    if (!p) return;
    const heldCell = row.cells[row.cells.length - 1];
    if (!heldCell) return;
    const holdMax = 12;  // CFG.hold_hours
    const heldH = (Date.now() - new Date(p.entry_time).getTime()) / 3600000;
    const pct = Math.min(100, (heldH / holdMax) * 100);
    let barCls = "";
    if (pct > 80) barCls = "warn";
    if (pct >= 100) barCls = "expired";
    heldCell.innerHTML = `${heldH.toFixed(1)}h<span class="lifecycle-bar"><span class="lifecycle-fill ${barCls}" style="width:${pct.toFixed(0)}%"></span></span>`;
  });
};

// =================== TREEMAP (capital allocation) ===================
function renderTreemap(state) {
  const positions = state.positions || [];
  const lastPrices = state.last_prices || {};
  const container = $("treemap");
  if (!positions.length) {
    container.innerHTML = '<div class="treemap-empty">no active positions — awaiting funding signals</div>';
    return;
  }
  // Compute total notional and per-pos size
  const totalNotional = positions.reduce((s, p) => s + p.size_usd, 0);
  const cells = positions.map(p => {
    const cur = lastPrices[p.sym] || p.entry_price;
    const ret = (cur / p.entry_price - 1) * p.side * 100;
    return {
      sym: p.sym,
      side: p.side,
      sizeUsd: p.size_usd,
      ret,
      frac: p.size_usd / totalNotional,
    };
  }).sort((a, b) => b.sizeUsd - a.sizeUsd);

  // 20-col grid, 18 rows = 360 cells. Each cell = ~0.27% allocation.
  const TOTAL_CELLS = 360;
  let html = "";
  for (const c of cells) {
    const span = Math.max(8, Math.round(c.frac * TOTAL_CELLS));
    const cols = Math.min(20, Math.max(2, Math.ceil(Math.sqrt(span * 1.5))));
    const rows = Math.max(1, Math.ceil(span / cols));
    const sideCls = c.side === 1 ? "long" : "short";
    const ret = c.ret;
    const arrow = ret >= 0 ? "↑" : "↓";
    html += `<div class="treemap-cell ${sideCls}" data-sym="${c.sym}"
              style="grid-column: span ${cols}; grid-row: span ${rows};"
              title="${c.sym} ${c.side === 1 ? 'LONG' : 'SHORT'} · ${fmt.shortUsd(c.sizeUsd)} · ${fmt.pct(ret)}">
      <strong>${c.sym.replace("USDT", "")}</strong>
      <span class="tm-pnl">${arrow} ${fmt.pct(ret)}</span>
    </div>`;
  }
  container.innerHTML = html;
  // Click to open detail
  setTimeout(() => {
    container.querySelectorAll(".treemap-cell").forEach(el => {
      el.classList.add("clickable");
    });
    attachSymClicks();
    attachHoverPreviews();
  }, 50);
}

// =================== WEEKLY ROI HISTOGRAM ===================
function renderWeeklyHist(comps) {
  const ctx = $("weekly-hist").getContext("2d");
  if (charts.weeklyHist) charts.weeklyHist.destroy();
  if (!comps.length) return;

  // Bucket comps into weekly snapshots, compute weekly ROI
  const weekly = [];
  let weekStart = comps[0].paper_total;
  let weekStartTs = new Date(comps[0].ts).getTime();
  for (const c of comps) {
    const ts = new Date(c.ts).getTime();
    if (ts - weekStartTs >= 7 * 86400 * 1000) {
      const roi = (c.paper_total / weekStart - 1) * 100;
      weekly.push(roi);
      weekStart = c.paper_total;
      weekStartTs = ts;
    }
  }
  if (weekly.length < 1) {
    ctx.canvas.parentElement.innerHTML = '<div class="empty">need at least 1 week of data</div>';
    return;
  }
  // Bucket into bins
  const min = Math.min(-15, ...weekly);
  const max = Math.max(15, ...weekly);
  const binSize = 2.5;
  const bins = {};
  weekly.forEach(r => {
    const bin = Math.round(r / binSize) * binSize;
    bins[bin] = (bins[bin] || 0) + 1;
  });
  const sortedBins = Object.keys(bins).map(Number).sort((a, b) => a - b);
  const t = chartTheme();
  charts.weeklyHist = new Chart(ctx, {
    type: "bar",
    data: {
      labels: sortedBins.map(b => (b >= 0 ? "+" : "") + b.toFixed(0) + "%"),
      datasets: [{
        data: sortedBins.map(b => bins[b]),
        backgroundColor: sortedBins.map(b => b >= 0 ? "rgba(91,140,110,0.7)" : "rgba(184,84,80,0.7)"),
        borderWidth: 0,
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: {display: false},
        tooltip: {
          backgroundColor: t.bg, titleColor: t.text, bodyColor: t.text,
          borderColor: t.grid, borderWidth: 1, padding: 8, cornerRadius: 6,
          callbacks: {label: c => `${c.raw} week${c.raw === 1 ? "" : "s"}`},
        },
      },
      scales: {
        x: {ticks: {color: t.muted, font: {size: 9}}, grid: {display: false}, border: {display: false}},
        y: {ticks: {color: t.muted, font: {size: 9}, stepSize: 1}, grid: {color: t.grid}, border: {display: false}},
      },
    },
  });
}

// =================== CARD FOCUS / MAXIMIZE ===================
function attachCardFocus() {
  document.querySelectorAll(".card").forEach(card => {
    if (card._focusAttached) return;
    card._focusAttached = true;
    // Add focus button to header if it has one
    const header = card.querySelector(".card-header");
    if (header && !header.querySelector(".card-focus-btn")) {
      const btn = document.createElement("button");
      btn.className = "card-focus-btn";
      btn.innerHTML = "⤢";
      btn.title = "Focus mode";
      btn.onclick = (e) => { e.stopPropagation(); toggleCardFocus(card); };
      // Add to header (after card-header-text)
      header.appendChild(btn);
    }
  });
}
function toggleCardFocus(card) {
  const isFocused = card.classList.contains("card-focused");
  document.querySelectorAll(".card-focused").forEach(c => c.classList.remove("card-focused"));
  if (!isFocused) {
    card.classList.add("card-focused");
    $("focus-backdrop").classList.add("show");
    document.body.style.overflow = "hidden";
    // Re-render charts in card to fit new size
    setTimeout(() => {
      Object.values(charts).forEach(ch => { try { ch.resize(); } catch {} });
    }, 300);
  } else {
    closeFocus();
  }
}
function closeFocus() {
  document.querySelectorAll(".card-focused").forEach(c => c.classList.remove("card-focused"));
  $("focus-backdrop").classList.remove("show");
  document.body.style.overflow = "";
  setTimeout(() => {
    Object.values(charts).forEach(ch => { try { ch.resize(); } catch {} });
  }, 200);
}
$("focus-backdrop").addEventListener("click", closeFocus);
// Esc closes focus too
const _origEscHandler = document.onkeydown;
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && document.querySelector(".card-focused")) closeFocus();
});

// =================== PULL-TO-REFRESH (MOBILE) ===================
let _ptrStartY = null;
let _ptrThreshold = 80;
let _ptrTriggered = false;
const ptrIndicator = $("ptr-indicator");
const ptrText = $("ptr-text");

document.addEventListener("touchstart", e => {
  if (window.scrollY <= 5) {
    _ptrStartY = e.touches[0].clientY;
    _ptrTriggered = false;
  }
}, {passive: true});

document.addEventListener("touchmove", e => {
  if (_ptrStartY === null) return;
  const delta = e.touches[0].clientY - _ptrStartY;
  if (delta > 10 && window.scrollY <= 5) {
    ptrIndicator.classList.add("show");
    if (delta >= _ptrThreshold) {
      ptrIndicator.classList.add("ready");
      ptrText.textContent = "Release to refresh";
    } else {
      ptrIndicator.classList.remove("ready");
      ptrText.textContent = "Pull to refresh";
    }
  }
}, {passive: true});

document.addEventListener("touchend", e => {
  if (_ptrStartY === null) return;
  const delta = (e.changedTouches[0]?.clientY || 0) - _ptrStartY;
  if (delta >= _ptrThreshold && !_ptrTriggered) {
    _ptrTriggered = true;
    ptrText.textContent = "Refreshing…";
    loadAll(false).then(() => {
      ptrIndicator.classList.remove("show", "ready");
    });
  } else {
    ptrIndicator.classList.remove("show", "ready");
  }
  _ptrStartY = null;
});

// =================== RIGHT-CLICK CONTEXT MENU ===================
const ctxMenu = $("context-menu");
let _ctxSym = null;

function showContextMenu(x, y, sym) {
  _ctxSym = sym;
  const items = [
    {icon: "📊", label: "View details", action: () => showSymModal(sym, _lastState, _lastTrades)},
    {icon: "📋", label: "Copy symbol", action: () => navigator.clipboard.writeText(sym).then(() => toast(`Copied ${sym}`))},
    {divider: true},
    {icon: "↗", label: "Open on Binance", action: () => window.open(`https://www.binance.com/en/futures/${sym}`, "_blank")},
    {icon: "🔍", label: "Search trades", action: () => { $("trades-search").value = sym; $("trades-search").dispatchEvent(new Event("input")); $("trades-search").scrollIntoView({behavior: "smooth", block: "center"}); }},
  ];
  ctxMenu.innerHTML = items.map(it => {
    if (it.divider) return '<div class="ctx-item divider"></div>';
    return `<div class="ctx-item" data-act="${it.label}"><span class="ctx-icon">${it.icon}</span>${it.label}</div>`;
  }).join("");
  ctxMenu.querySelectorAll(".ctx-item:not(.divider)").forEach((el, idx) => {
    el.addEventListener("click", () => {
      hideContextMenu();
      const it = items.filter(i => !i.divider)[idx];
      if (it) it.action();
    });
  });
  // Position (avoid edge)
  const w = 200, h = ctxMenu.offsetHeight || 200;
  let left = x;
  let top = y;
  if (left + w > window.innerWidth - 10) left = window.innerWidth - w - 10;
  if (top + h > window.innerHeight - 10) top = window.innerHeight - h - 10;
  ctxMenu.style.left = left + "px";
  ctxMenu.style.top = top + "px";
  ctxMenu.classList.add("show");
}
function hideContextMenu() {
  ctxMenu.classList.remove("show");
  _ctxSym = null;
}

document.addEventListener("contextmenu", e => {
  const symEl = e.target.closest("[data-sym]");
  if (symEl) {
    e.preventDefault();
    showContextMenu(e.clientX, e.clientY, symEl.dataset.sym);
  }
});
document.addEventListener("click", e => {
  if (!ctxMenu.contains(e.target)) hideContextMenu();
});
document.addEventListener("scroll", hideContextMenu, {passive: true});

// =================== COMPARE OVERLAY ON EQUITY CHART ===================
let _compareMode = false;
$("compare-toggle").addEventListener("click", () => {
  _compareMode = !_compareMode;
  $("compare-toggle").classList.toggle("active", _compareMode);
  if (allComps.length) renderEquityChart(allComps);
  toast(_compareMode ? "Showing previous period overlay" : "Comparison off");
});

const _origRenderEquity = renderEquityChart;
renderEquityChart = function(comps) {
  if (!_compareMode) { _origRenderEquity(comps); return; }
  // Build current-period and prior-period data
  const ctx = $("equity-chart").getContext("2d");
  if (charts.equity) charts.equity.destroy();
  const filtered = filterPeriod(comps, currentPeriod);
  if (!filtered.length) { _origRenderEquity(comps); return; }
  // Prior-period: same length window, ending where current starts
  const startIdx = comps.indexOf(filtered[0]);
  const priorLen = filtered.length;
  const prior = comps.slice(Math.max(0, startIdx - priorLen), startIdx);
  if (!prior.length) { _origRenderEquity(comps); return; }
  // Normalize both to start at 100 for comparison
  const normCurrent = filtered.map((c, i) => (c.paper_total / filtered[0].paper_total - 1) * 100);
  const normPrior = prior.map((c, i) => (c.paper_total / prior[0].paper_total - 1) * 100);
  const labels = filtered.map((_, i) => `t+${i}`);
  const t = chartTheme();
  charts.equity = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {label: "Current", data: normCurrent, borderColor: "#c96442",
         backgroundColor: "rgba(201,100,66,0.10)", fill: true, tension: 0.35, borderWidth: 2.5, pointRadius: 0},
        {label: "Previous", data: normPrior, borderColor: t.muted, borderWidth: 1.5, tension: 0.35, pointRadius: 0, borderDash: [4, 4]},
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: {mode: "index", intersect: false},
      plugins: {
        legend: {position: "bottom", labels: {boxWidth: 8, boxHeight: 8, padding: 14, color: t.muted, font: {size: 11, weight: "500"}, usePointStyle: true, pointStyle: "circle"}},
        tooltip: {backgroundColor: t.bg, titleColor: t.text, bodyColor: t.text, borderColor: t.grid, borderWidth: 1, padding: 12, cornerRadius: 8, displayColors: true, boxWidth: 8, boxHeight: 8, usePointStyle: true,
          callbacks: {label: (ctx) => `  ${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}%`}},
      },
      scales: {
        x: {ticks: {color: t.muted, font: {size: 10}, maxTicksLimit: 6, maxRotation: 0}, grid: {display: false}, border: {display: false}},
        y: {ticks: {color: t.muted, font: {size: 10}, callback: v => (v >= 0 ? "+" : "") + v.toFixed(0) + "%"}, grid: {color: t.grid}, border: {display: false}},
      },
    },
  });
};

// =================== ACCENT COLOR PICKER (in cmdK) ===================
const ACCENT_PRESETS = [
  {name: "Sienna", color: "#c96442"},   // default
  {name: "Indigo", color: "#6366f1"},
  {name: "Emerald", color: "#10b981"},
  {name: "Rose", color: "#f43f5e"},
  {name: "Amber", color: "#f59e0b"},
  {name: "Violet", color: "#8b5cf6"},
];
const _origCommands = COMMANDS.slice();
ACCENT_PRESETS.forEach(p => {
  COMMANDS.push({
    section: "Personalize",
    name: `Accent: ${p.name}`,
    icon: `<span style="display:inline-block;width:14px;height:14px;border-radius:3px;background:${p.color};"></span>`,
    fn: () => setAccentColor(p.color, p.name),
  });
});

function setAccentColor(color, name) {
  document.documentElement.style.setProperty("--accent", color);
  // Soft & strong variants — derive
  document.documentElement.style.setProperty("--accent-soft", hexToRgba(color, 0.13));
  document.documentElement.style.setProperty("--accent-strong", shadeColor(color, -15));
  localStorage.setItem("accent", color);
  if (allComps.length) {
    renderEquityChart(allComps);
    renderHeroSparkline(allComps);
  }
  toast(`Accent: ${name}`);
}
function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
  return `rgba(${r},${g},${b},${alpha})`;
}
function shadeColor(hex, percent) {
  const r = Math.max(0, Math.min(255, parseInt(hex.slice(1,3),16) + percent*2));
  const g = Math.max(0, Math.min(255, parseInt(hex.slice(3,5),16) + percent*2));
  const b = Math.max(0, Math.min(255, parseInt(hex.slice(5,7),16) + percent*2));
  return "#" + [r,g,b].map(x => Math.round(x).toString(16).padStart(2,"0")).join("");
}

// Restore saved accent
const savedAccent = localStorage.getItem("accent");
if (savedAccent) {
  document.documentElement.style.setProperty("--accent", savedAccent);
  document.documentElement.style.setProperty("--accent-soft", hexToRgba(savedAccent, 0.13));
  document.documentElement.style.setProperty("--accent-strong", shadeColor(savedAccent, -15));
}

// =================== FUNDING TICKER ===================
function renderFundingTicker(state) {
  const fund = state.last_funding || {};
  const items = Object.entries(fund)
    .filter(([s, v]) => v != null)
    .map(([s, v]) => ({sym: s, rate: v}))
    .sort((a, b) => Math.abs(b.rate) - Math.abs(a.rate))
    .slice(0, 25);
  if (!items.length) {
    $("funding-ticker").style.display = "none";
    return;
  }
  $("funding-ticker").style.display = "flex";
  $("funding-ticker").setAttribute("aria-hidden", "false");
  // Duplicate for seamless loop
  const html = items.map(it => {
    const pct = it.rate * 100;
    const arrow = pct > 0 ? "↑" : "↓";
    const cls = pct > 0 ? "loss" : "gain";  // positive funding = longs pay → bearish for the long crowd
    return `<span class="ticker-item" data-sym="${it.sym}">
      <span class="ticker-arrow ${cls}">${arrow}</span>
      <span class="ticker-sym">${it.sym.replace("USDT", "")}</span>
      <span class="ticker-rate ${cls}">${pct >= 0 ? "+" : ""}${pct.toFixed(4)}%</span>
    </span>`;
  }).join("");
  $("ticker-content").innerHTML = html + html;
}

// =================== STRATEGY HEALTH RADAR ===================
function renderHealthRadar(state, trades, comps) {
  const ctx = $("health-radar").getContext("2d");
  if (charts.radar) charts.radar.destroy();
  if (!comps.length) return;

  const cutoff7d = Date.now() - 7 * 86400 * 1000;
  const closes7d = trades.filter(t => t.event === "close" && new Date(t.ts).getTime() >= cutoff7d);

  // 5 axes (each 0-100):
  // 1. Win Rate — clamped 0-100
  // 2. Profit Factor — clamped 0-100 (PF=1 → 50, PF=2 → 100)
  // 3. % Profitable Weeks — over comparison history
  // 4. Friction Health — 100 - (friction% × 5), clamped
  // 5. ROI Pace — (current 7d ROI / expected 0.7%) × 100, clamped 0-100

  const wins = closes7d.filter(c => (c.pnl_usd || 0) > 0).length;
  const wr = closes7d.length ? (wins / closes7d.length * 100) : 50;
  const gp = closes7d.filter(c => (c.pnl_usd||0)>0).reduce((s,c)=>s+c.pnl_usd, 0);
  const gl = Math.abs(closes7d.filter(c => (c.pnl_usd||0)<=0).reduce((s,c)=>s+c.pnl_usd, 0)) || 1e-9;
  const pf = gp / gl;
  const pfScore = Math.min(100, Math.max(0, pf * 50));

  // Weekly profitable %
  const weekly = [];
  let weekStart = comps[0].paper_total;
  let weekStartTs = new Date(comps[0].ts).getTime();
  for (const c of comps) {
    const ts = new Date(c.ts).getTime();
    if (ts - weekStartTs >= 7 * 86400 * 1000) {
      weekly.push((c.paper_total / weekStart - 1) * 100);
      weekStart = c.paper_total;
      weekStartTs = ts;
    }
  }
  const profWeeks = weekly.length ? (weekly.filter(r => r > 0).length / weekly.length * 100) : 50;

  const compsRecent = comps.filter(c => new Date(c.ts).getTime() >= cutoff7d);
  const avgFriction = compsRecent.length ?
    compsRecent.reduce((s, c) => s + (c.friction_pct || 0), 0) / compsRecent.length : 0;
  const frictionScore = Math.max(0, Math.min(100, 100 - Math.abs(avgFriction) * 5));

  // ROI pace
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
  const eq7dAgo = (() => {
    const past = comps.find(c => new Date(c.ts).getTime() >= cutoff7d);
    return past ? past.paper_total : init;
  })();
  const roi7d = (total / eq7dAgo - 1) * 100;
  const roiScore = Math.max(0, Math.min(100, 50 + (roi7d / 0.7) * 25));  // 0% → 50, +0.7% → 75, +2.8% → 100

  const t = chartTheme();
  charts.radar = new Chart(ctx, {
    type: "radar",
    data: {
      labels: ["Win Rate", "Profit Factor", "Profitable Weeks", "Friction Health", "ROI Pace"],
      datasets: [{
        label: "Health",
        data: [wr, pfScore, profWeeks, frictionScore, roiScore],
        backgroundColor: "rgba(201,100,66,0.18)",
        borderColor: "#c96442",
        borderWidth: 2,
        pointBackgroundColor: "#c96442",
        pointRadius: 3,
        pointHoverRadius: 5,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: {display: false},
        tooltip: {backgroundColor: t.bg, titleColor: t.text, bodyColor: t.text, borderColor: t.grid, borderWidth: 1, padding: 8, cornerRadius: 6,
          callbacks: {label: (c) => `${c.label}: ${c.raw.toFixed(0)}/100`}},
      },
      scales: {
        r: {
          min: 0, max: 100,
          beginAtZero: true,
          ticks: {display: false, stepSize: 25},
          grid: {color: t.grid},
          angleLines: {color: t.grid},
          pointLabels: {color: t.muted, font: {size: 10, weight: "500"}},
        },
      },
    },
  });
}

// =================== SCROLL SPY ===================
function buildScrollSpy() {
  const cards = document.querySelectorAll(".card, .hero, .insights-row");
  const spy = $("scroll-spy");
  spy.innerHTML = "";
  cards.forEach((card, i) => {
    let label = "Section";
    if (card.classList.contains("hero")) label = "Hero";
    else if (card.classList.contains("insights-row")) label = "Insights";
    else {
      const h = card.querySelector("h2");
      if (h) label = h.textContent;
    }
    const dot = document.createElement("div");
    dot.className = "spy-dot";
    dot.dataset.label = label;
    dot.dataset.idx = i;
    dot.addEventListener("click", () => {
      card.scrollIntoView({behavior: "smooth", block: "center"});
    });
    spy.appendChild(dot);
  });
  // Update active on scroll
  const update = () => {
    const dots = spy.querySelectorAll(".spy-dot");
    cards.forEach((card, i) => {
      const r = card.getBoundingClientRect();
      const inView = r.top < window.innerHeight / 2 && r.bottom > window.innerHeight / 2;
      dots[i].classList.toggle("active", inView);
    });
  };
  window.addEventListener("scroll", update, {passive: true});
  setTimeout(update, 100);
}

// =================== CARD COLLAPSE ===================
function attachCardCollapse() {
  document.querySelectorAll(".card").forEach(card => {
    if (card._collapseAttached) return;
    card._collapseAttached = true;
    const header = card.querySelector(".card-header");
    if (header && !header.querySelector(".card-collapse-btn")) {
      const btn = document.createElement("button");
      btn.className = "card-collapse-btn";
      btn.innerHTML = "▾";
      btn.title = "Collapse / expand";
      btn.onclick = (e) => {
        e.stopPropagation();
        card.classList.toggle("collapsed");
        const id = card.querySelector("h2")?.textContent || "";
        const collapsed = JSON.parse(localStorage.getItem("collapsed-cards") || "[]");
        if (card.classList.contains("collapsed")) {
          if (!collapsed.includes(id)) collapsed.push(id);
        } else {
          const idx = collapsed.indexOf(id);
          if (idx >= 0) collapsed.splice(idx, 1);
        }
        localStorage.setItem("collapsed-cards", JSON.stringify(collapsed));
      };
      // Insert as last child of header (after focus button)
      header.appendChild(btn);
    }
  });
  // Restore collapsed state
  const collapsed = JSON.parse(localStorage.getItem("collapsed-cards") || "[]");
  document.querySelectorAll(".card").forEach(card => {
    const id = card.querySelector("h2")?.textContent || "";
    if (collapsed.includes(id)) card.classList.add("collapsed");
  });
}

// =================== NUMBER TICKER (Flipboard-style for hero) ===================
function tickerizeNumber(el, formatted) {
  // formatted is like "$10,234.56"
  // Build per-character spans, animating digits
  const chars = formatted.split("");
  let html = "";
  for (const c of chars) {
    if (/\d/.test(c)) {
      html += `<span class="ticker-digit"><span class="ticker-digit-inner" data-target="${c}"><span>0</span><span>1</span><span>2</span><span>3</span><span>4</span><span>5</span><span>6</span><span>7</span><span>8</span><span>9</span></span></span>`;
    } else {
      html += `<span>${c}</span>`;
    }
  }
  el.innerHTML = html;
  // Animate to target
  requestAnimationFrame(() => {
    el.querySelectorAll(".ticker-digit-inner").forEach(inner => {
      const d = parseInt(inner.dataset.target);
      inner.style.transform = `translateY(-${d}em)`;
    });
  });
}

// Apply ticker on data refresh AFTER first load
let _heroTickerEnabled = false;
const _origRenderHero2 = renderHero;
renderHero = function(state, comps) {
  _origRenderHero2(state, comps);
  if (_heroTickerEnabled) {
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
    const formatted = "$" + total.toLocaleString("en-US", {maximumFractionDigits: 2, minimumFractionDigits: 2});
    setTimeout(() => tickerizeNumber($("total-eq"), formatted), 50);
  }
  // Enable for next refresh
  setTimeout(() => { _heroTickerEnabled = true; }, 1500);
};

// =================== AUGMENT loadAll for new sections ===================
const _origLoad4 = loadAll;
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
    renderInsights(state, trades, comps);
    renderTopList(state);
    renderPositions(state);
    renderTrades(trades);
    renderStats(trades, comps);
    renderAttribution(state);
    renderActivityFeed(trades);
    renderHeatmap(comps);
    renderEquityChart(comps);
    renderUnderwaterChart(comps);
    renderFrictionChart(comps);
    renderTreemap(state);
    renderWeeklyHist(comps);
    renderHealthRadar(state, trades, comps);
    renderFundingTicker(state);
    const lastTs = state.last_check || (comps.length ? comps[comps.length-1].ts : null);
    $("last-updated").innerHTML = `<span id="status-dot" class="status-dot healthy"></span>Updated ${fmt.ago(lastTs)}`;
    setTimeout(() => {
      attachSymClicks();
      attachHoverPreviews();
      attachCardFocus();
      attachCardCollapse();
      buildScrollSpy();
    }, 50);
    if (!silent) toast("Refreshed");
  } catch (e) {
    console.error(e);
    $("last-updated").innerHTML = `<span class="status-dot error"></span>Error loading`;
    if (!silent) toast("Error loading data");
  } finally {
    $("refresh").classList.remove("spinning");
  }
};
