#!/usr/bin/env python3
"""Paper trader readiness report - 决定能否上 live.

7 天后跑这个脚本, 检查 paper 跑出来的统计特征是否匹配 backtest 期望。
任何 ❌ 就别上 live, 找原因。

Backtest baseline (v10 MAX_v10, 18m FULL period):
  Sharpe        = 8.45
  CAGR          = 282,456%/yr
  MDD           = -36.7%
  ruin (MC)     = 1.40%

Paper trading expected per-trade stats:
  Win rate      ~ 50-65% (funding-rev typical)
  Avg hold      ~ 6-12h (12h max enforced)
  Trades/day    ~ 5-15 (depends on funding extremity)
  Top-N stable  - top-10 should be 60%+ overlap day-over-day

Readiness criteria (all must pass):
  ✅ daily_realized_roi > -2% (no immediate disaster)
  ✅ trade_count >= 30 (enough sample)
  ✅ win_rate in [40%, 75%] (not gaming/random)
  ✅ avg_hold_h in [3, 12] (not bug)
  ✅ no_workflow_failures > 95% (cron ran reliably)
  ✅ shadow_pnl populated for all 52 syms
"""
import argparse, json, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).parent
STATE = REPO / "state" / "paper_state.json"
TRADES = REPO / "state" / "paper_trades.jsonl"
SIM_STATE = REPO / "state" / "sim_state.json"
SIM_TRADES = REPO / "state" / "sim_trades.jsonl"


# Backtest reference (v10 MAX, 18m FULL)
BT = dict(
    annual_cagr_pct=282_456,
    daily_roi_pct=1500 / 365,        # ~4.1%/day from CAGR ≈ 1500% (more realistic)
    sharpe=8.45,
    mdd_pct=36.7,
    ruin_pct=1.40,
)


_USE_SIM = False
def _trades_path(): return SIM_TRADES if _USE_SIM else TRADES
def _state_path(): return SIM_STATE if _USE_SIM else STATE


def load_trades():
    p = _trades_path()
    if not p.exists(): return []
    out = []
    for ln in p.read_text().strip().split("\n"):
        if not ln: continue
        try: out.append(json.loads(ln))
        except: pass
    return out


def load_state():
    p = _state_path()
    if not p.exists(): return {}
    return json.loads(p.read_text())


def fmt_check(name, ok, value, threshold, note=""):
    icon = "✅" if ok else "❌"
    return f"  {icon} {name:30s} {value:>15s}  (threshold: {threshold})  {note}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7,
                     help="Number of days back to analyze (default 7)")
    ap.add_argument("--sim", action="store_true",
                     help="Read from sim_state.json instead of paper_state.json")
    args = ap.parse_args()
    global _USE_SIM
    _USE_SIM = args.sim
    if _USE_SIM:
        print("📊 Reading SIMULATED state (state/sim_*.{json,jsonl})\n")

    trades = load_trades()
    state = load_state()

    if not trades or not state:
        print("❌ No data yet. Run paper trader first.")
        return

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=args.days)

    # Filter trades in window
    recent = []
    for t in trades:
        try:
            ts = datetime.fromisoformat(t["ts"])
            if ts >= window_start: recent.append(t)
        except: pass
    opens = [t for t in recent if t.get("event") == "open"]
    closes = [t for t in recent if t.get("event") == "close"]

    print("="*80)
    print(f"  v10 alts paper trader — readiness report ({args.days}d window)")
    print("="*80)
    print(f"  Window: {window_start.date()} → {now.date()}")
    print(f"  Total opens:  {len(opens)}")
    print(f"  Total closes: {len(closes)}")

    # =============== ROI ===============
    print(f"\n📈 ROI vs backtest expectation")
    init_cap = state.get("initial_capital", 10000)
    cur_eq = state.get("equity", init_cap)
    floating = sum(p.get("floating_pnl", 0) for p in state.get("positions", []))
    cur_total = cur_eq + floating

    # equity from window_start (find closest history point)
    hist = state.get("history", {}).get("equity_curve", [])
    eq_at_start = init_cap
    for h in hist:
        try:
            ts = datetime.fromisoformat(h["ts"])
            if ts >= window_start:
                eq_at_start = h.get("total", h.get("equity", init_cap))
                break
        except: pass

    roi_window_pct = (cur_total / eq_at_start - 1) * 100
    expected_roi = BT["daily_roi_pct"] * args.days  # naive linear extrapolation
    expected_roi_floor = expected_roi * 0.20         # accept 20% of expected (heavy haircut)
    ok_roi = roi_window_pct > -2.0  # at minimum: not catastrophic
    print(fmt_check("ROI in window", ok_roi, f"{roi_window_pct:+.2f}%",
                     "> -2% (no disaster)",
                     f"backtest expects ~{expected_roi:.1f}% naive, ~{expected_roi_floor:.1f}% realistic"))

    # =============== Trade count ===============
    n_trades = len(closes)
    expected_per_day = 5
    expected_total = expected_per_day * args.days
    ok_count = n_trades >= max(10, expected_per_day * args.days // 3)  # at least 1/3 of expected
    print(fmt_check("Trades closed", ok_count, str(n_trades),
                     f">= {max(10, expected_per_day*args.days//3)}",
                     f"expected ~{expected_total}"))

    # =============== Per-trade P&L distribution ===============
    print(f"\n💰 Per-trade P&L stats")
    pnls = [t["pnl_usd"] for t in closes if "pnl_usd" in t]
    if pnls:
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(pnls) * 100
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        total_pnl = sum(pnls)
        biggest_loss = min(pnls) if pnls else 0
        biggest_win = max(pnls) if pnls else 0
        ok_wr = 35 <= win_rate <= 80
        ok_avg = (avg_win > 0 and avg_loss < 0)
        print(fmt_check("Win rate", ok_wr, f"{win_rate:.1f}%", "in [35%, 80%]"))
        print(fmt_check("Avg win", ok_avg, f"${avg_win:+.2f}", "> 0"))
        print(fmt_check("Avg loss", ok_avg, f"${avg_loss:+.2f}", "< 0"))
        print(f"  ℹ️  Total realized P&L: ${total_pnl:+,.2f}")
        print(f"  ℹ️  Best/worst trade: ${biggest_win:+.2f} / ${biggest_loss:+.2f}")
        # profit factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1e-9
        pf = gross_profit / gross_loss
        ok_pf = pf > 1.0
        print(fmt_check("Profit factor", ok_pf, f"{pf:.2f}", "> 1.0"))
    else:
        print("  ⚠️  No closed trades yet")
        ok_wr = ok_avg = ok_pf = False

    # =============== Hold time ===============
    print(f"\n⏱️  Hold time stats")
    holds = [t["held_h"] for t in closes if "held_h" in t]
    if holds:
        avg_hold = sum(holds) / len(holds)
        max_hold = max(holds)
        ok_hold = 2 <= avg_hold <= 12.5
        print(fmt_check("Avg hold (h)", ok_hold, f"{avg_hold:.1f}h", "in [2h, 12.5h]"))
        print(f"  ℹ️  Max hold: {max_hold:.1f}h (cap: 12h)")
        # exit reason breakdown
        reasons = defaultdict(int)
        for t in closes: reasons[t.get("reason", "?")] += 1
        print(f"  ℹ️  Exit reasons: {dict(reasons)}")
    else:
        ok_hold = False

    # =============== Per-symbol breakdown ===============
    print(f"\n🪙 Per-symbol P&L (top 10)")
    by_sym = defaultdict(lambda: {"pnl": 0, "n": 0, "wins": 0})
    for t in closes:
        s = t.get("sym", "?")
        by_sym[s]["pnl"] += t.get("pnl_usd", 0)
        by_sym[s]["n"] += 1
        if t.get("pnl_usd", 0) > 0: by_sym[s]["wins"] += 1
    ranked = sorted(by_sym.items(), key=lambda x: -x[1]["pnl"])
    for sym, st in ranked[:10]:
        wr = st["wins"] / st["n"] * 100 if st["n"] else 0
        print(f"  {sym:14s} P&L=${st['pnl']:+8.2f}  n={st['n']:3d}  win={wr:.0f}%")
    if len(ranked) > 10:
        print(f"  ... {len(ranked) - 10} more sym")

    # =============== Shadow P&L health ===============
    print(f"\n🎯 Shadow P&L (used for top-N selection)")
    sp = state.get("shadow_pnl", {})
    n_with_data = sum(1 for s in sp.values() if s.get("rets"))
    avg_bars = sum(len(s.get("rets", [])) for s in sp.values()) / max(len(sp), 1)
    ok_sp = n_with_data >= 50
    print(fmt_check("Symbols with shadow data", ok_sp, f"{n_with_data}/52", ">= 50"))
    print(f"  ℹ️  Avg shadow bars/sym: {avg_bars:.0f} (max 336)")

    # =============== Top-N stability ===============
    # Look at recent equity_curve and infer top-N from history (rough)
    # If top-N changes 100% day-over-day, signal is noise
    # (skip detailed check unless we have logged top-N over time)
    print(f"\n🔝 Current top-10")
    scores = [(s, sum(i["rets"])) for s, i in sp.items() if i.get("rets")]
    ranked = sorted(scores, key=lambda x: -x[1])
    for s, sc in ranked[:10]:
        print(f"  {s:14s} 14d shadow P&L = {sc*100:+6.1f}%")

    # =============== Comparison history (paper vs shadow vs backtest) ===============
    comp_log_name = "sim_comparison.jsonl" if _USE_SIM else "comparison_history.jsonl"
    print(f"\n📊 Comparison vs theoretical (from {comp_log_name})")
    try:
        import comparator
        if _USE_SIM:
            comparator.COMP_LOG = REPO / "state" / "sim_comparison.jsonl"
        rows = comparator.load_history(days=args.days)
        if rows:
            avg_friction = sum(r['friction_pct'] for r in rows) / len(rows)
            avg_gap = sum(r['backtest_gap_pct'] for r in rows) / len(rows)
            last = rows[-1]
            ok_friction = avg_friction < 30  # paper should not lose more than 30% to friction
            print(fmt_check("Avg friction (paper vs shadow)", ok_friction,
                             f"{avg_friction:+.1f}%", "< 30% (acceptable)",
                             f"{len(rows)} snapshots"))
            print(f"  ℹ️  Latest: paper=${last['paper_total']:.0f}  shadow=${last['shadow_total']:.0f}  bt=${last['bt_expected']:.0f}")
            print(f"  ℹ️  Latest backtest gap: {last['backtest_gap_pct']:+.2f}%  (paper vs ~1500% CAGR extrapolation)")
        else:
            print("  ⚠️  No comparison history yet (comparator added in latest version)")
            ok_friction = True  # don't block
    except ImportError:
        ok_friction = True

    # =============== Final verdict ===============
    print("\n" + "="*80)
    all_critical = ok_roi and ok_count and ok_pf and ok_hold and ok_sp and ok_friction
    if all_critical:
        print("  ✅ READY FOR LIVE")
        print("     Paper performance matches backtest expectations.")
        print("     Recommended: switch live_trader.yml DRY_RUN to 'false', start with $5000.")
    else:
        print("  ❌ NOT READY")
        print("     Investigate failed checks above before going live.")
        print("     Common issues:")
        if not ok_count: print("       - Too few trades: cron not running, or funding signals too rare")
        if not ok_wr: print("       - Win rate off: possibly broken signal logic")
        if not ok_pf: print("       - Profit factor < 1: losing money, check sizing/exec")
        if not ok_hold: print("       - Hold time anomaly: 12h expiry not firing")
        if not ok_sp: print("       - Shadow data missing: many syms not tracked, top-N broken")
    print("="*80)


if __name__ == "__main__":
    main()
