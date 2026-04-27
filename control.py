#!/usr/bin/env python3
"""Emergency control for paper + live trader.

State flags written to state/control.json, read by traders each cycle.

Commands:
  python control.py status              # show flags
  python control.py pause               # stop opening NEW positions (existing keep running)
  python control.py resume              # un-pause
  python control.py kill                # set kill switch (same as pause + alert)
  python control.py unkill              # un-kill
  python control.py panic_close         # 🚨 close ALL live positions NOW (Binance API call)
  python control.py panic_close --dry   # show what would close, don't do it

After running, commit + push:
  cd ~/v10-alts-paper && git add -A && git commit -m "control: ..." && git push

  Live trader will read updated control.json on next cycle (within 1 hour).
  For instant effect, manually trigger workflow on GitHub Actions.
"""
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).parent
CONTROL_FILE = REPO / "state" / "control.json"
LIVE_STATE = REPO / "state" / "live_state.json"
TRADES_LOG = REPO / "state" / "live_trades.jsonl"


def load_control():
    if CONTROL_FILE.exists():
        return json.loads(CONTROL_FILE.read_text())
    return {"paused": False, "killed": False, "last_change": None, "history": []}


def save_control(c):
    CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONTROL_FILE.write_text(json.dumps(c, indent=2, default=str))


def log_event(action, note=""):
    c = load_control()
    c["history"].append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action, "note": note,
    })
    c["history"] = c["history"][-100:]
    c["last_change"] = c["history"][-1]["ts"]
    save_control(c)
    # Also append to trades log for audit trail
    if TRADES_LOG.exists() or True:
        TRADES_LOG.parent.mkdir(parents=True, exist_ok=True)
        with TRADES_LOG.open("a") as f:
            f.write(json.dumps({
                "event": "control", "action": action, "note": note,
                "ts": datetime.now(timezone.utc).isoformat(),
            }) + "\n")


def cmd_status():
    c = load_control()
    print("="*60)
    print(f"  Control state ({CONTROL_FILE.name})")
    print("="*60)
    print(f"  paused: {'🛑 YES' if c['paused'] else '✅ no'}")
    print(f"  killed: {'💀 YES' if c['killed'] else '✅ no'}")
    print(f"  last change: {c.get('last_change', 'never')}")
    print(f"\n  Recent actions:")
    for h in c.get("history", [])[-5:]:
        print(f"    [{h['ts'][:19]}] {h['action']}: {h.get('note','')}")
    # Show current live state
    if LIVE_STATE.exists():
        ls = json.loads(LIVE_STATE.read_text())
        positions = ls.get("live_positions", {}) or ls.get("positions_sim", [])
        if isinstance(positions, dict):
            n = len(positions)
            print(f"\n  Active live positions: {n}")
            for sym, p in positions.items():
                print(f"    {sym:14s} {('LONG' if p['side']==1 else 'SHORT'):5s} "
                      f"entry=${p['entry_price']:.4f} size=${p['size_usd']:.0f}")
        else:
            print(f"\n  Active SIM positions: {len(positions)}")


def cmd_pause():
    c = load_control()
    c["paused"] = True
    save_control(c)
    log_event("pause", "stop opening NEW positions")
    print("🛑 paused. Existing positions keep running. Commit + push to apply.")


def cmd_resume():
    c = load_control()
    c["paused"] = False
    save_control(c)
    log_event("resume", "")
    print("✅ resumed.")


def cmd_kill():
    c = load_control()
    c["killed"] = True
    c["paused"] = True
    save_control(c)
    log_event("kill", "kill switch + pause")
    print("💀 KILL_SWITCH set + paused.")


def cmd_unkill():
    c = load_control()
    c["killed"] = False
    save_control(c)
    log_event("unkill", "")
    print("✅ kill cleared. (still paused unless you also resume)")


def cmd_panic_close(dry=False):
    """🚨 Close ALL live positions on Binance NOW."""
    import ccxt   # lazy: only needed here
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        print("❌ BINANCE_API_KEY / SECRET not set in env.")
        print("   Run: BINANCE_API_KEY=xxx BINANCE_API_SECRET=xxx python control.py panic_close")
        return
    ex = ccxt.binanceusdm({"apiKey": api_key, "secret": api_secret,
                           "enableRateLimit": True, "options": {"defaultType": "future"}})
    try:
        positions = ex.fetch_positions()
    except Exception as e:
        print(f"❌ failed to fetch positions: {e}"); return
    actives = [p for p in positions if abs(float(p.get("contracts") or 0)) > 1e-9]
    if not actives:
        print("✅ no active positions on Binance"); return
    print(f"🚨 found {len(actives)} active positions:")
    for p in actives:
        sym = p["symbol"]
        contracts = float(p["contracts"])
        side = p.get("side")
        upnl = float(p.get("unrealizedPnl") or 0)
        print(f"   {sym:18s} {side:5s} contracts={contracts:.4f} unrealized=${upnl:+.2f}")
    if dry:
        print("\n(dry mode — no orders placed)")
        return
    confirm = input("\nClose all? Type 'YES' to confirm: ")
    if confirm.strip() != "YES":
        print("Aborted."); return
    closed = 0
    for p in actives:
        sym = p["symbol"]
        contracts = abs(float(p["contracts"]))
        side = p.get("side")
        ccxt_side = "sell" if side == "long" else "buy"
        try:
            order = ex.create_market_order(sym, ccxt_side, contracts, params={"reduceOnly": True})
            print(f"  ✅ closed {sym}: order id {order.get('id')}")
            closed += 1
        except Exception as e:
            print(f"  ❌ failed {sym}: {e}")
    log_event("panic_close", f"closed {closed}/{len(actives)} positions")
    # auto-set kill switch after panic
    cmd_kill()
    print(f"\n💀 panic_close complete. {closed}/{len(actives)} closed. KILL_SWITCH auto-set.")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("pause")
    sub.add_parser("resume")
    sub.add_parser("kill")
    sub.add_parser("unkill")
    pc = sub.add_parser("panic_close")
    pc.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    cmds = {"status": cmd_status, "pause": cmd_pause, "resume": cmd_resume,
            "kill": cmd_kill, "unkill": cmd_unkill,
            "panic_close": lambda: cmd_panic_close(dry=args.dry)}
    if args.cmd not in cmds:
        ap.print_help(); sys.exit(1)
    cmds[args.cmd]()


if __name__ == "__main__":
    main()
