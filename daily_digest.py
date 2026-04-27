#!/usr/bin/env python3
"""Rich daily Telegram digest. Triggered at UTC 00 by paper_trader.

Includes:
  - 24h ROI vs 7d running ROI
  - Per-day P&L delta
  - Win rate, profit factor 24h
  - Best/worst trade today
  - Top-10 changes vs yesterday
  - Friction (paper vs shadow) trend
  - Workflow health (cron success rate)
  - Sim 16-window expectation comparison

Standalone: `python daily_digest.py` for manual run.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import notify

REPO = Path(__file__).parent
STATE = REPO / "state" / "paper_state.json"
TRADES = REPO / "state" / "paper_trades.jsonl"
COMP = REPO / "state" / "comparison_history.jsonl"

# Sim 16-window expectations (from simulate_multi.py 16x7d)
EXPECTED_WEEKLY_MEAN = 0.69       # %
EXPECTED_WEEKLY_MEDIAN = -0.59    # %
EXPECTED_WORST_WEEK = -10.06      # %
EXPECTED_BEST_WEEK = 12.61        # %


def _read_jsonl(path):
    if not path.exists(): return []
    out = []
    for ln in path.read_text().strip().split("\n"):
        if not ln: continue
        try: out.append(json.loads(ln))
        except: pass
    return out


def _filter_window(rows, hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(r["ts"])
            if ts >= cutoff: out.append(r)
        except: pass
    return out


def build_digest():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    trades = _read_jsonl(TRADES)
    comps = _read_jsonl(COMP)

    init = state.get("initial_capital", 10000)
    cash = state.get("equity", init)
    floating = sum(p.get("floating_pnl", 0) for p in state.get("positions", []))
    total_now = cash + floating

    # 24h window
    closes_24h = [t for t in trades if t.get("event") == "close"
                   and (datetime.now(timezone.utc) - datetime.fromisoformat(t["ts"])).total_seconds() < 86400]
    opens_24h = [t for t in trades if t.get("event") == "open"
                  and (datetime.now(timezone.utc) - datetime.fromisoformat(t["ts"])).total_seconds() < 86400]
    pnl_24h = sum(c.get("pnl_usd", 0) for c in closes_24h)
    wins_24h = [c for c in closes_24h if c.get("pnl_usd", 0) > 0]
    win_rate_24h = len(wins_24h) / len(closes_24h) * 100 if closes_24h else 0
    best_24h = max(closes_24h, key=lambda c: c.get("pnl_usd", -1e9)) if closes_24h else None
    worst_24h = min(closes_24h, key=lambda c: c.get("pnl_usd", 1e9)) if closes_24h else None

    # 7d window
    closes_7d = [t for t in trades if t.get("event") == "close"
                  and (datetime.now(timezone.utc) - datetime.fromisoformat(t["ts"])).total_seconds() < 7*86400]
    pnl_7d = sum(c.get("pnl_usd", 0) for c in closes_7d)
    wins_7d = [c for c in closes_7d if c.get("pnl_usd", 0) > 0]
    win_rate_7d = len(wins_7d) / len(closes_7d) * 100 if closes_7d else 0

    # ROI total
    total_roi_pct = (total_now / init - 1) * 100
    # 24h equity delta
    comp_24h = _filter_window(comps, 24)
    eq_24h_ago = comp_24h[0]["paper_total"] if comp_24h else init
    roi_24h_pct = (total_now / eq_24h_ago - 1) * 100 if eq_24h_ago else 0
    # 7d equity delta
    comp_7d = _filter_window(comps, 7*24)
    eq_7d_ago = comp_7d[0]["paper_total"] if comp_7d else init
    roi_7d_pct = (total_now / eq_7d_ago - 1) * 100 if eq_7d_ago else 0

    # Workflow health
    n_cycles_24h = len(comp_24h)
    expected_cycles = 24
    cycle_ratio = n_cycles_24h / expected_cycles if expected_cycles else 0

    # Friction
    if comp_24h:
        avg_friction = sum(c["friction_pct"] for c in comp_24h) / len(comp_24h)
    else:
        avg_friction = 0

    # Top-10 from shadow_pnl
    sp = state.get("shadow_pnl", {})
    scores = [(s, sum(i["rets"])) for s, i in sp.items() if i.get("rets")]
    scores.sort(key=lambda x: -x[1])
    top5 = [s for s, _ in scores[:5]]
    top10 = [s for s, _ in scores[:10]]

    # Active positions
    n_pos = len(state.get("positions", []))

    # === Build message ===
    icon_24h = "📈" if roi_24h_pct >= 0 else "📉"
    icon_7d = "📈" if roi_7d_pct >= 0 else "📉"
    icon_total = "📈" if total_roi_pct >= 0 else "📉"

    # Compare to expectations
    weekly_pace = "🟢 ahead of expected" if roi_7d_pct > EXPECTED_WEEKLY_MEAN else \
                   "🟡 within range" if roi_7d_pct > EXPECTED_WORST_WEEK else "🔴 below worst-case"

    health_icon = "✅" if cycle_ratio > 0.85 else ("⚠️" if cycle_ratio > 0.5 else "❌")

    lines = [
        "📊 <b>v10 alts paper digest</b>",
        f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>",
        "",
        f"💰 <b>Equity</b>: ${total_now:,.2f}  {icon_total} {total_roi_pct:+.2f}%",
        f"   cash ${cash:,.0f} · floating ${floating:+,.0f} · {n_pos} positions",
        "",
        f"<b>24h</b> {icon_24h} {roi_24h_pct:+.2f}%  ({len(closes_24h)} closes / {len(opens_24h)} opens)",
    ]
    if closes_24h:
        lines.append(f"   WR {win_rate_24h:.0f}% · realized P&L ${pnl_24h:+,.2f}")
        if best_24h:
            lines.append(f"   🏆 best: {best_24h['sym']} ${best_24h['pnl_usd']:+,.2f} ({best_24h.get('reason','?')})")
        if worst_24h and worst_24h != best_24h:
            lines.append(f"   💀 worst: {worst_24h['sym']} ${worst_24h['pnl_usd']:+,.2f} ({worst_24h.get('reason','?')})")
    lines.extend([
        "",
        f"<b>7d</b> {icon_7d} {roi_7d_pct:+.2f}%  ({len(closes_7d)} closes, WR {win_rate_7d:.0f}%)",
        f"   {weekly_pace}",
        f"   expected: mean +{EXPECTED_WEEKLY_MEAN:.1f}% · worst {EXPECTED_WORST_WEEK:+.1f}% · best {EXPECTED_BEST_WEEK:+.1f}%",
        "",
        f"🔝 <b>Top-5</b>: {', '.join(top5)}",
        "",
        f"📐 <b>Friction</b> (paper vs shadow): {avg_friction:+.1f}%",
        f"{health_icon} Workflow: {n_cycles_24h}/{expected_cycles} cycles last 24h",
    ])
    return "\n".join(lines)


def send_digest():
    msg = build_digest()
    print(msg)
    print()
    sent = notify.send(msg)
    print("✓ sent" if sent else "(notify not configured or send failed)")
    return sent


if __name__ == "__main__":
    send_digest()
