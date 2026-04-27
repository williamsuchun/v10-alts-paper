#!/usr/bin/env python3
"""v10 alts paper trading status."""
import json
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).parent
STATE_FILE = REPO / "state" / "paper_state.json"
TRADES_LOG = REPO / "state" / "paper_trades.jsonl"


def fmt_td(iso):
    if not iso: return "never"
    t = datetime.fromisoformat(iso)
    d = datetime.now(timezone.utc) - t
    if d.days > 0: return f"{d.days}d{d.seconds//3600}h ago"
    if d.seconds > 3600: return f"{d.seconds//3600}h{(d.seconds%3600)//60}m ago"
    return f"{d.seconds//60}m ago"


def main():
    print("=" * 70)
    print("  v10 alts paper trading status")
    print("=" * 70)
    if not STATE_FILE.exists():
        print("(no state)"); return

    s = json.loads(STATE_FILE.read_text())
    floating = sum(p.get("floating_pnl", 0) for p in s["positions"])
    eq = s["equity"]; total = eq + floating; init = s["initial_capital"]
    roi = (total / init - 1) * 100
    print(f"\n💰 cash: ${eq:,.2f}  floating: ${floating:+,.2f}  total: ${total:,.2f}  ROI: {roi:+.3f}%")
    print(f"   last check: {fmt_td(s.get('last_check'))}")

    pos = s["positions"]
    print(f"\n📊 open positions ({len(pos)}):")
    for p in pos:
        side = "LONG" if p["side"] == 1 else "SHORT"
        cur = s.get("last_prices", {}).get(p["sym"], p["entry_price"])
        unr = (cur / p["entry_price"] - 1) * p["side"] * 100
        print(f"   {p['sym']:14s} {side:5s} entry=${p['entry_price']:.4f} now=${cur:.4f} ({unr:+.2f}%) "
              f"size=${p['size_usd']:.0f} held={fmt_td(p['entry_time'])}")

    sp = s.get("shadow_pnl", {})
    if sp:
        scores = {sym: sum(info["rets"]) for sym, info in sp.items() if info.get("rets")}
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        print(f"\n🏆 top-10 (rolling 14d shadow P&L):")
        for sym, sc in ranked[:10]:
            ico = "📈" if sp[sym].get("pos", 0) == 1 else ("📉" if sp[sym].get("pos", 0) == -1 else "—")
            print(f"   {sym:14s} P&L={sc*100:+.1f}%  {ico}")

    print(f"\n📝 last 8 trade events:")
    if TRADES_LOG.exists():
        for ln in TRADES_LOG.read_text().strip().split("\n")[-8:]:
            try:
                e = json.loads(ln); ts = fmt_td(e.get("ts")); ev = e.get("event")
                if ev == "open":
                    side = "LONG" if e["side"] == 1 else "SHORT"
                    print(f"   [{ts:>10s}] OPEN  {e['sym']:14s} {side:5s} @${e['entry_price']:.4f} fund={e['funding']*100:+.4f}%")
                elif ev == "close":
                    side = "LONG" if e["side"] == 1 else "SHORT"
                    print(f"   [{ts:>10s}] CLOSE {e['sym']:14s} {side:5s} pnl=${e['pnl_usd']:+.2f} held={e.get('held_h',0):.1f}h reason={e.get('reason')}")
            except Exception: pass
    print("=" * 70)


if __name__ == "__main__":
    main()
