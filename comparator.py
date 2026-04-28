#!/usr/bin/env python3
"""Continuous backtest comparison logger.

Each cycle (called from paper_trader.py end), append a snapshot of:
  - paper_total_eq: actual paper equity + floating
  - shadow_total_eq: simulated equity if we perfectly captured ALL top-N signals
  - bt_expected_eq: naive linear extrapolation from backtest CAGR
  - friction_pct = 1 - paper/shadow (% of alpha lost to friction)
  - backtest_gap_pct = 1 - paper/bt_expected (% off backtest expectation)

The shadow portfolio is the THEORETICAL UPPER BOUND of what v10 can deliver
in this exact period — sum of all top-N picks at each bar with no slippage,
no funding-rev signal misses, no tx costs of switching positions.

Stored as JSONL in state/comparison_history.jsonl (append-only, human-readable).

Standalone use:
  python comparator.py            # show last 24h summary
  python comparator.py --plot     # ASCII plot of 3 curves
"""
import argparse, json
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).parent
STATE_FILE = REPO / "state" / "paper_state.json"
COMP_LOG = REPO / "state" / "comparison_history.jsonl"

# Realistic CAGR reference (lev=3 stop=6% top=40% hold=8h, optimal sweep)
# Sweep showed mean +1.6%/week → (1.016)^52 ≈ +128%/yr (Sharpe_w 0.21)
# Backtest 282,456% is at lev=6 with extreme winner concentration; lev=3 ≈ half
BT_CAGR_PCT = 130    # %/year (realistic for tuned config)
INIT_CAPITAL = 10000


def append_snapshot(state, n_top=10):
    """Compute current shadow + paper + bt expected, write a row to comp log.
    Shadow accumulates ONLY since first comparator call (parallel timeline to paper)."""
    now = datetime.now(timezone.utc)

    # --- Paper actual ---
    paper_eq = state.get("equity", INIT_CAPITAL)
    floating = sum(p.get("floating_pnl", 0) for p in state.get("positions", []))
    paper_total = paper_eq + floating

    # --- Shadow portfolio: cumulative apply this-hour's top-N avg bar return ---
    # State key tracks running equity. Each cycle: shadow_eq *= (1 + this_hour_top_n_avg_ret)
    # This is parallel to paper timeline (both start now=0).
    sp = state.get("shadow_pnl", {})
    shadow_eq = state.get("shadow_portfolio_eq", INIT_CAPITAL)
    if sp:
        scores = [(s, sum(i["rets"]), i.get("rets", [])) for s, i in sp.items() if i.get("rets")]
        scores.sort(key=lambda x: -x[1])
        top = scores[:n_top]
        if top:
            # latest bar return of each top-N (the hour just elapsed)
            last_rets = [r[2][-1] for r in top if r[2]]
            if last_rets:
                this_hour_avg = sum(last_rets) / len(last_rets)
                shadow_eq = shadow_eq * (1 + this_hour_avg)
                state["shadow_portfolio_eq"] = shadow_eq
    shadow_total = shadow_eq

    # --- Backtest expectation (linear extrapolation from "paper start") ---
    hist = state.get("history", {}).get("equity_curve", [])
    if hist:
        try:
            t0 = datetime.fromisoformat(hist[0]["ts"])
            days_elapsed = (now - t0).total_seconds() / 86400
        except Exception:
            days_elapsed = 0
    else:
        days_elapsed = 0
    # Continuous compounding approximation
    bt_expected = INIT_CAPITAL * ((1 + BT_CAGR_PCT/100) ** (days_elapsed / 365))

    # --- Compute deviations ---
    friction_pct = (1 - paper_total / shadow_total) * 100 if shadow_total > 0 else 0
    backtest_gap_pct = (1 - paper_total / bt_expected) * 100 if bt_expected > 0 else 0

    snapshot = {
        "ts": now.isoformat(),
        "days_elapsed": round(days_elapsed, 3),
        "paper_total": round(paper_total, 2),
        "shadow_total": round(shadow_total, 2),
        "bt_expected": round(bt_expected, 2),
        "friction_pct": round(friction_pct, 2),
        "backtest_gap_pct": round(backtest_gap_pct, 2),
        "n_positions": len(state.get("positions", [])),
        "shadow_top_n": n_top,
    }
    COMP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with COMP_LOG.open("a") as f:
        f.write(json.dumps(snapshot) + "\n")
    # Trim to last 90d (2160 entries) to keep file small
    _trim_jsonl(COMP_LOG, max_lines=2160)
    return snapshot


def _trim_jsonl(path, max_lines):
    """Keep only last max_lines lines of a jsonl file."""
    if not path.exists(): return
    lines = path.read_text().split("\n")
    if len(lines) <= max_lines + 100: return  # 100-line buffer to avoid frequent rewrites
    keep = "\n".join(lines[-max_lines:])
    path.write_text(keep)


def load_history(days=7):
    if not COMP_LOG.exists(): return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for ln in COMP_LOG.read_text().strip().split("\n"):
        if not ln: continue
        try:
            r = json.loads(ln)
            if datetime.fromisoformat(r["ts"]) >= cutoff:
                out.append(r)
        except Exception: pass
    return out


def ascii_plot(rows, key, label, width=60):
    """Tiny terminal plot."""
    if not rows: return f"{label}: (no data)"
    vals = [r[key] for r in rows]
    lo, hi = min(vals), max(vals)
    span = max(hi - lo, 1)
    line = ""
    for v in vals[-width:]:
        pos = int((v - lo) / span * 8)
        line += " ▁▂▃▄▅▆▇█"[pos]
    return f"{label:25s} [{lo:.0f} → {hi:.0f}]  {line}"


def summary(days=7):
    rows = load_history(days=days)
    print("="*80)
    print(f"  Comparison summary — last {days}d ({len(rows)} snapshots)")
    print("="*80)
    if not rows:
        print("  (no comparison data yet — paper trader needs to run)"); return
    first = rows[0]; last = rows[-1]
    print(f"\n  Window: {first['ts'][:16]} → {last['ts'][:16]}")
    print(f"\n  📊 Final values:")
    print(f"     Paper actual:       ${last['paper_total']:>14,.2f}  ({(last['paper_total']/INIT_CAPITAL-1)*100:+.2f}%)")
    print(f"     Shadow upper-bound: ${last['shadow_total']:>14,.2f}  ({(last['shadow_total']/INIT_CAPITAL-1)*100:+.2f}%)")
    print(f"     Backtest expected:  ${last['bt_expected']:>14,.2f}  ({(last['bt_expected']/INIT_CAPITAL-1)*100:+.2f}%)")
    print(f"\n  📉 Deviations (latest):")
    print(f"     Friction (paper vs shadow):       {last['friction_pct']:+.2f}%")
    print(f"     Backtest gap (paper vs expected): {last['backtest_gap_pct']:+.2f}%")
    if len(rows) > 1:
        avg_friction = sum(r['friction_pct'] for r in rows) / len(rows)
        avg_gap = sum(r['backtest_gap_pct'] for r in rows) / len(rows)
        print(f"\n  📊 Averages over window:")
        print(f"     Avg friction:     {avg_friction:+.2f}%")
        print(f"     Avg backtest gap: {avg_gap:+.2f}%")
    print(f"\n  📈 Trends:")
    print(f"  {ascii_plot(rows, 'paper_total', 'Paper ($)')}")
    print(f"  {ascii_plot(rows, 'shadow_total', 'Shadow ($)')}")
    print(f"  {ascii_plot(rows, 'bt_expected', 'BT expected ($)')}")
    print(f"  {ascii_plot(rows, 'friction_pct', 'Friction %')}")
    print("="*80)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    summary(days=args.days)


if __name__ == "__main__":
    main()
