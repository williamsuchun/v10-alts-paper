#!/usr/bin/env python3
"""v10 alts paper trader — Binance funding-rev + adaptive top-N.

每小时:
  1) 拉 52 syms Binance 最新价 + funding
  2) shadow simulation: 对每个币算 funding-rev 假信号 + bar P&L → 维护 14d 滚动 P&L
  3) 真实持仓:
     - 已有: 检查 12h hold expiry / -10% stop / funding 8h 成本
     - 没有: 若在 top 20% (top 10) 且 funding 极端: 开仓
  4) 状态写 state/paper_state.json, 交易写 state/paper_trades.jsonl

不下单到交易所,只本地记账。
"""
import argparse, json, traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import notify  # local module, gracefully no-ops if no Telegram creds
import comparator  # continuous backtest comparison logger

# ============== Config ==============
EXCHANGE = ccxt.binanceusdm({"enableRateLimit": True})

UNIVERSE = [
    # 30 established alts
    "DOGEUSDT", "XRPUSDT", "ADAUSDT", "BNBUSDT", "AVAXUSDT",
    "AAVEUSDT", "LINKUSDT", "DOTUSDT", "FILUSDT", "LTCUSDT",
    "BCHUSDT", "NEARUSDT", "XMRUSDT", "ZECUSDT", "MASKUSDT",
    "AXSUSDT", "APEUSDT", "LDOUSDT",
    "SUIUSDT", "INJUSDT", "ORDIUSDT", "TAOUSDT", "1000PEPEUSDT",
    "ENAUSDT", "1000LUNCUSDT", "FARTCOINUSDT", "TRUMPUSDT",
    "HYPEUSDT", "PENGUUSDT", "HIGHUSDT",
    # 9 memes
    "WIFUSDT", "1000BONKUSDT", "1000SHIBUSDT", "POPCATUSDT", "SPXUSDT",
    "TURBOUSDT", "MOODENGUSDT", "PNUTUSDT", "NEIROUSDT",
    # 13 mid-tier alts
    "WLDUSDT", "JUPUSDT", "PYTHUSDT", "TIAUSDT", "JTOUSDT",
    "FETUSDT", "RUNEUSDT", "ATOMUSDT", "ARBUSDT", "OPUSDT",
    "CHZUSDT", "GALAUSDT", "CRVUSDT",
]

CFG = dict(
    universe=UNIVERSE,
    initial_capital=10_000.0,
    leverage=6.0,
    funding_thr=0.0003,        # 0.03% per 8h
    hold_hours=12,
    stop_pct=0.10,
    lookback_hours=336,        # 14d
    top_pct=20,                # top 10 of 52
    fee=0.0005,
    slippage=0.0003,
)

REPO = Path(__file__).parent
STATE_FILE = REPO / "state" / "paper_state.json"
TRADES_LOG = REPO / "state" / "paper_trades.jsonl"
CONTROL_FILE = REPO / "state" / "control.json"


def is_paused():
    if not CONTROL_FILE.exists(): return False
    try:
        c = json.loads(CONTROL_FILE.read_text())
        return c.get("paused") or c.get("killed")
    except Exception: return False


# ============== State ==============
def load_state():
    if STATE_FILE.exists():
        s = json.loads(STATE_FILE.read_text())
        for k, v in [("equity", CFG["initial_capital"]), ("positions", []),
                      ("shadow_pnl", {}), ("last_prices", {}),
                      ("last_funding", {}), ("last_check", None),
                      ("history", {"equity_curve": []})]:
            s.setdefault(k, v)
        return s
    return {
        "initial_capital": CFG["initial_capital"],
        "equity": CFG["initial_capital"],
        "positions": [],
        "shadow_pnl": {},
        "last_prices": {},
        "last_funding": {},
        "last_check": None,
        "history": {"equity_curve": []},
    }


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def log_event(event):
    TRADES_LOG.parent.mkdir(parents=True, exist_ok=True)
    event["ts"] = datetime.now(timezone.utc).isoformat()
    with TRADES_LOG.open("a") as f:
        f.write(json.dumps(event, default=str) + "\n")


# ============== Live data ==============
def to_ccxt(s):
    return f"{s[:-4]}/USDT:USDT" if s.endswith("USDT") else s


def fetch_latest_close(syms):
    out = {}
    try:
        tickers = EXCHANGE.fetch_tickers([to_ccxt(s) for s in syms])
        for s in syms:
            t = tickers.get(to_ccxt(s)) or {}
            if t.get("close"): out[s] = float(t["close"])
    except Exception as e:
        print(f"  [tickers err] {e}, fallback per-sym")
        for s in syms:
            try:
                t = EXCHANGE.fetch_ticker(to_ccxt(s))
                out[s] = float(t["close"])
            except Exception: pass
    return out


def fetch_latest_funding(syms):
    out = {}
    for s in syms:
        try:
            r = EXCHANGE.fetch_funding_rate(to_ccxt(s))
            out[s] = float(r["fundingRate"])
        except Exception: pass
    return out


def is_funding_hour(t):
    return t.hour % 8 == 0


# ============== Strategy ==============
def update_shadow(state, prices, fundings):
    """Update shadow simulation for ALL universe (whether or not real position)."""
    lookback = CFG["lookback_hours"]
    lev = CFG["leverage"]
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    funding_event = is_funding_hour(now)

    for sym in CFG["universe"]:
        cur_close = prices.get(sym)
        prev_close = state["last_prices"].get(sym)
        cur_funding = fundings.get(sym, state["last_funding"].get(sym))
        sh = state["shadow_pnl"].setdefault(sym, {"pos": 0, "bars_held": 0, "entry": None, "rets": []})

        # close shadow position if stop/expiry
        if sh["pos"] != 0 and cur_close is not None and sh["entry"]:
            ret_e = (cur_close / sh["entry"] - 1) * sh["pos"]
            if ret_e <= -CFG["stop_pct"] or sh["bars_held"] >= CFG["hold_hours"]:
                sh["pos"] = 0; sh["bars_held"] = 0; sh["entry"] = None

        prev_pos = sh["pos"]

        # generate new shadow signal if flat
        if sh["pos"] == 0 and cur_funding is not None:
            sig = 0
            if cur_funding > CFG["funding_thr"]: sig = -1
            elif cur_funding < -CFG["funding_thr"]: sig = 1
            if sig != 0 and cur_close:
                sh["pos"] = sig; sh["bars_held"] = 0; sh["entry"] = cur_close

        if sh["pos"] != 0:
            sh["bars_held"] += 1

        # bar return
        bar_ret = 0.0
        if prev_close and cur_close and prev_close > 0:
            pct = cur_close / prev_close - 1
            bar_ret = prev_pos * pct * lev
            if funding_event and cur_funding is not None:
                bar_ret -= prev_pos * cur_funding * lev
            if prev_pos != sh["pos"]:
                bar_ret -= abs(sh["pos"] - prev_pos) * (CFG["fee"] + CFG["slippage"]) * lev
        sh["rets"].append(float(bar_ret))
        if len(sh["rets"]) > lookback:
            sh["rets"] = sh["rets"][-lookback:]


def get_top_n(state):
    pct = CFG["top_pct"]
    n = max(3, int(len(CFG["universe"]) * pct / 100))
    scores = {}
    min_bars = CFG["lookback_hours"] // 4
    for s in CFG["universe"]:
        sh = state["shadow_pnl"].get(s, {})
        if len(sh.get("rets", [])) < min_bars: continue
        scores[s] = sum(sh["rets"])
    if not scores: return []
    return sorted(scores, key=lambda s: -scores[s])[:n]


def manage_real_positions(state, prices, fundings):
    closed = []
    cap = state["equity"]
    new_positions = []
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    funding_event = is_funding_hour(now)

    for p in state["positions"]:
        sym = p["sym"]; entry = p["entry_price"]; size_usd = p["size_usd"]; side = p["side"]
        cur_close = prices.get(sym, entry)
        ret_e = (cur_close / entry - 1) * side
        pnl_usd = ret_e * size_usd
        if funding_event:
            f = fundings.get(sym, state["last_funding"].get(sym, 0))
            funding_cost = side * f * size_usd
            pnl_usd -= funding_cost
            p["funding_paid"] = p.get("funding_paid", 0) + funding_cost

        entry_t = datetime.fromisoformat(p["entry_time"])
        held_h = (datetime.now(timezone.utc) - entry_t).total_seconds() / 3600

        exit_reason = None
        if ret_e <= -CFG["stop_pct"]: exit_reason = "stop_loss"
        elif held_h >= CFG["hold_hours"]: exit_reason = "hold_expiry"

        p["floating_pnl"] = pnl_usd
        if exit_reason:
            cost = size_usd * (CFG["fee"] + CFG["slippage"])
            realized = pnl_usd - cost
            cap += realized
            log_event({
                "event": "close", "sym": sym, "side": side,
                "entry_price": entry, "exit_price": cur_close,
                "size_usd": size_usd, "held_h": round(held_h, 2),
                "pnl_usd": round(realized, 2), "reason": exit_reason,
            })
            notify.send(notify.close_msg(sym, side, entry, cur_close, realized, held_h, exit_reason, "PAPER"))
            closed.append(sym)
        else:
            new_positions.append(p)

    state["positions"] = new_positions
    state["equity"] = cap
    return closed


def open_new_positions(state, prices, fundings, top_syms):
    cap = state["equity"]
    held_syms = {p["sym"] for p in state["positions"]}
    n_target = max(3, int(len(CFG["universe"]) * CFG["top_pct"] / 100))
    available_slots = n_target - len(state["positions"])
    if available_slots <= 0: return []

    alloc_per_pos_usd = cap / n_target
    opened = []
    for sym in top_syms:
        if available_slots <= 0: break
        if sym in held_syms: continue
        f = fundings.get(sym, state["last_funding"].get(sym))
        if f is None: continue
        side = -1 if f > CFG["funding_thr"] else (1 if f < -CFG["funding_thr"] else 0)
        if side == 0: continue
        cur_close = prices.get(sym)
        if cur_close is None: continue
        size_usd = alloc_per_pos_usd * CFG["leverage"]
        cap -= size_usd * (CFG["fee"] + CFG["slippage"])
        state["positions"].append({
            "sym": sym, "side": side, "entry_price": cur_close,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "size_usd": size_usd, "funding_at_entry": f,
        })
        log_event({"event": "open", "sym": sym, "side": side, "entry_price": cur_close,
                   "size_usd": size_usd, "funding": f})
        notify.send(notify.open_msg(sym, side, cur_close, size_usd, f, "PAPER"))
        opened.append(sym)
        available_slots -= 1
    state["equity"] = cap
    return opened


# ============== Main cycle ==============
def run_once(state, dry=False):
    print(f"\n=== {datetime.now(timezone.utc).isoformat()} ===")
    syms = CFG["universe"]
    prices = fetch_latest_close(syms)
    print(f"  prices: {len(prices)}/{len(syms)}")
    fundings = fetch_latest_funding(syms)
    print(f"  funding: {len(fundings)}/{len(syms)}")

    closed = manage_real_positions(state, prices, fundings)
    if closed: print(f"  closed: {closed}")

    update_shadow(state, prices, fundings)
    top = get_top_n(state)
    print(f"  top-{len(top)}: {top[:5]}{'...' if len(top)>5 else ''}")

    if not dry and not is_paused():
        opened = open_new_positions(state, prices, fundings, top)
        if opened: print(f"  opened: {opened}")
    elif is_paused():
        print(f"  ⏸  paused (control.json) — no new positions")

    state["last_prices"] = prices
    state["last_funding"] = {**state.get("last_funding", {}), **fundings}
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    floating = sum(p.get("floating_pnl", 0) for p in state["positions"])
    total = state["equity"] + floating
    state.setdefault("history", {}).setdefault("equity_curve", []).append({
        "ts": state["last_check"], "equity": state["equity"],
        "floating": floating, "total": total, "n_positions": len(state["positions"]),
    })
    state["history"]["equity_curve"] = state["history"]["equity_curve"][-2000:]
    print(f"  equity=${state['equity']:.2f} floating=${floating:+.2f} total=${total:.2f} positions={len(state['positions'])}")

    # Comparison snapshot: paper vs shadow vs backtest
    try:
        snap = comparator.append_snapshot(state)
        print(f"  comparator: paper=${snap['paper_total']:.0f} shadow=${snap['shadow_total']:.0f} bt=${snap['bt_expected']:.0f}  friction={snap['friction_pct']:+.1f}%")
    except Exception as e:
        print(f"  [comparator err] {e}")

    # Daily summary at UTC 00:xx (first cycle of day)
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    if now.hour == 0 and state.get("last_daily_summary") != today:
        state["last_daily_summary"] = today
        # Find equity 24h ago
        hist = state.get("history", {}).get("equity_curve", [])
        prev_total = next((h["total"] for h in reversed(hist[:-1])
                           if (now - datetime.fromisoformat(h["ts"])).total_seconds() > 22*3600),
                          state["initial_capital"])
        change_pct = (total / prev_total - 1) * 100 if prev_total else 0
        # Count closes in last 24h
        n_closed_24h = 0
        if TRADES_LOG.exists():
            for ln in TRADES_LOG.read_text().strip().split("\n"):
                try:
                    e = json.loads(ln)
                    if e.get("event") == "close":
                        ts = datetime.fromisoformat(e["ts"])
                        if (now - ts).total_seconds() < 24*3600:
                            n_closed_24h += 1
                except Exception: pass
        scored = [(s, sum(i["rets"])) for s, i in state["shadow_pnl"].items() if i.get("rets")]
        top5 = [s for s, _ in sorted(scored, key=lambda x: -x[1])[:5]]
        notify.send(notify.daily_summary(total, change_pct, n_closed_24h, len(state["positions"]), top5, "PAPER"))
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="Don't open new positions")
    args = ap.parse_args()

    state = load_state()
    if not state["shadow_pnl"]:
        print("⚠️  shadow_pnl empty — first run will warm up over 14 days.")
        print("    To skip warmup: copy a pre-seeded state/paper_state.json from local backtest.")

    try:
        run_once(state, dry=args.dry)
    except Exception as e:
        print(f"ERR: {e}")
        traceback.print_exc()
    finally:
        save_state(state)


if __name__ == "__main__":
    main()
