#!/usr/bin/env python3
"""v10 alts LIVE trader — Binance USDM 真实下单版.

🚨 默认 DRY_RUN=true,只打印不下单。要真交易必须:
   1. 在 GitHub Secrets 设 BINANCE_API_KEY + BINANCE_API_SECRET
   2. 在 workflow 里 export DRY_RUN=false

🛡️  风控:
   - KILL_SWITCH=true: 立即停止所有新开仓
   - daily_loss_halt: 当日 P&L < -5% 当日不再开仓
   - min_equity: equity < $5000 停止交易
   - 单仓 notional 限制
   - 双重确认: shadow_pnl 表明 top_n 才会进真单

🔄 状态同步:
   每轮先从 Binance 拉真实持仓 → 本地 state 重建
   shadow_pnl 仍维护(选币用)
   trade log 同时记 paper(模拟)+ live 标签
"""
import os, json, traceback
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import notify

# === Strategy config (与 paper_trader 完全一致) ===
UNIVERSE = [
    "DOGEUSDT", "XRPUSDT", "ADAUSDT", "BNBUSDT", "AVAXUSDT",
    "AAVEUSDT", "LINKUSDT", "DOTUSDT", "FILUSDT", "LTCUSDT",
    "BCHUSDT", "NEARUSDT", "XMRUSDT", "ZECUSDT", "MASKUSDT",
    "AXSUSDT", "APEUSDT", "LDOUSDT", "SUIUSDT", "INJUSDT",
    "ORDIUSDT", "TAOUSDT", "1000PEPEUSDT", "ENAUSDT", "1000LUNCUSDT",
    "FARTCOINUSDT", "TRUMPUSDT", "HYPEUSDT", "PENGUUSDT", "HIGHUSDT",
    "WIFUSDT", "1000BONKUSDT", "1000SHIBUSDT", "POPCATUSDT", "SPXUSDT",
    "TURBOUSDT", "MOODENGUSDT", "PNUTUSDT", "NEIROUSDT",
    "WLDUSDT", "JUPUSDT", "PYTHUSDT", "TIAUSDT", "JTOUSDT",
    "FETUSDT", "RUNEUSDT", "ATOMUSDT", "ARBUSDT", "OPUSDT",
    "CHZUSDT", "GALAUSDT", "CRVUSDT",
]

CFG = dict(
    leverage=3.0,              # was 6.0; sweep showed lev=3 has best Sharpe
    funding_thr=0.0003,
    hold_hours=8,              # was 12; signal decays fast, short hold beats long
    stop_pct=0.06,             # was 0.10; tighter stop preserves capital
    lookback_hours=336,
    top_pct=40,                # was 20; more diversification → Sh_w 0.14→0.21
    fee=0.0005, slippage=0.0003,
)

# === Risk gates ===
RISK = dict(
    daily_loss_halt_pct=-5.0,     # 当日总 P&L < -5% 停开仓
    min_equity_usd=5000,           # equity < 5k 停交易
    max_pos_notional_pct=10,       # 单仓 notional ≤ 10% × leverage × equity
    max_total_notional_pct=70,     # 全部 notional ≤ 70% × leverage × equity
)

# === Mode flags (env vars) ===
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
KILL_SWITCH = os.environ.get("KILL_SWITCH", "false").lower() == "true"

# === API keys ===
API_KEY = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")

EXCHANGE = ccxt.binanceusdm({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
    "options": {"defaultType": "future"},
})

REPO = Path(__file__).parent
STATE_FILE = REPO / "state" / "live_state.json"
TRADES_LOG = REPO / "state" / "live_trades.jsonl"
CONTROL_FILE = REPO / "state" / "control.json"


def is_paused():
    if not CONTROL_FILE.exists(): return False, False
    try:
        c = json.loads(CONTROL_FILE.read_text())
        return bool(c.get("paused")), bool(c.get("killed"))
    except Exception: return False, False


def to_ccxt(s): return f"{s[:-4]}/USDT:USDT" if s.endswith("USDT") else s


def log_event(event):
    TRADES_LOG.parent.mkdir(parents=True, exist_ok=True)
    event["ts"] = datetime.now(timezone.utc).isoformat()
    event["dry_run"] = DRY_RUN
    with TRADES_LOG.open("a") as f:
        f.write(json.dumps(event, default=str) + "\n")


def load_state():
    """Load state. NOTE: positions are re-derived from exchange each cycle."""
    if STATE_FILE.exists():
        s = json.loads(STATE_FILE.read_text())
    else:
        s = {}
    # required fields
    for k, v in [("equity_history", []), ("shadow_pnl", {}),
                  ("last_prices", {}), ("last_funding", {}),
                  ("last_check", None), ("daily_start_equity", None),
                  ("day_marker", None), ("kill_history", [])]:
        s.setdefault(k, v)
    return s


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


# ============== Exchange truth source ==============
def fetch_account_state():
    """Fetch live equity + positions from Binance. The truth source."""
    try:
        bal = EXCHANGE.fetch_balance()
        usdt = bal.get("USDT", {})
        # Binance USDM: available + used = total
        equity = float(usdt.get("total", 0))
        positions_raw = EXCHANGE.fetch_positions(symbols=[to_ccxt(s) for s in UNIVERSE])
    except Exception as e:
        print(f"  [exchange err] {e}")
        return None, []
    positions = []
    for p in positions_raw:
        contracts = float(p.get("contracts") or 0)
        if abs(contracts) < 1e-9: continue
        sym_full = p["symbol"]  # e.g. "DOGE/USDT:USDT"
        sym = sym_full.replace("/USDT:USDT", "USDT")
        side = 1 if (p.get("side") == "long") else -1
        entry = float(p.get("entryPrice") or 0)
        notional = float(p.get("notional") or 0)
        positions.append({
            "sym": sym, "side": side, "entry_price": entry,
            "size_usd": abs(notional),
            "contracts": contracts,
            "entry_time": p.get("info", {}).get("updateTime"),  # rough
            "unrealized_pnl": float(p.get("unrealizedPnl") or 0),
        })
    return equity, positions


def fetch_market_data():
    """Prices + funding for universe."""
    prices, fundings = {}, {}
    try:
        ts = EXCHANGE.fetch_tickers([to_ccxt(s) for s in UNIVERSE])
        for s in UNIVERSE:
            t = ts.get(to_ccxt(s)) or {}
            if t.get("close"): prices[s] = float(t["close"])
    except Exception as e:
        print(f"  [tickers err] {e}")
    for s in UNIVERSE:
        try:
            r = EXCHANGE.fetch_funding_rate(to_ccxt(s))
            fundings[s] = float(r["fundingRate"])
        except Exception: pass
    return prices, fundings


# ============== Shadow sim (same as paper) ==============
def is_funding_hour(t): return t.hour % 8 == 0


def update_shadow(state, prices, fundings):
    lookback = CFG["lookback_hours"]; lev = CFG["leverage"]
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    f_event = is_funding_hour(now)
    for sym in UNIVERSE:
        cur_close = prices.get(sym); prev_close = state["last_prices"].get(sym)
        cur_f = fundings.get(sym, state["last_funding"].get(sym))
        sh = state["shadow_pnl"].setdefault(sym, {"pos": 0, "bars_held": 0, "entry": None, "rets": []})
        if sh["pos"] != 0 and cur_close and sh["entry"]:
            ret_e = (cur_close / sh["entry"] - 1) * sh["pos"]
            if ret_e <= -CFG["stop_pct"] or sh["bars_held"] >= CFG["hold_hours"]:
                sh["pos"] = 0; sh["bars_held"] = 0; sh["entry"] = None
        prev_pos = sh["pos"]
        if sh["pos"] == 0 and cur_f is not None:
            sig = -1 if cur_f > CFG["funding_thr"] else (1 if cur_f < -CFG["funding_thr"] else 0)
            if sig != 0 and cur_close:
                sh["pos"] = sig; sh["bars_held"] = 0; sh["entry"] = cur_close
        if sh["pos"] != 0: sh["bars_held"] += 1
        bar_ret = 0.0
        if prev_close and cur_close and prev_close > 0:
            pct = cur_close / prev_close - 1
            bar_ret = prev_pos * pct * lev
            if f_event and cur_f is not None: bar_ret -= prev_pos * cur_f * lev
            if prev_pos != sh["pos"]:
                bar_ret -= abs(sh["pos"] - prev_pos) * (CFG["fee"] + CFG["slippage"]) * lev
        sh["rets"].append(float(bar_ret))
        if len(sh["rets"]) > lookback: sh["rets"] = sh["rets"][-lookback:]


def get_top_n(state):
    pct = CFG["top_pct"]; n = max(3, int(len(UNIVERSE) * pct / 100))
    scores = {}; min_bars = CFG["lookback_hours"] // 4
    for s in UNIVERSE:
        sh = state["shadow_pnl"].get(s, {})
        if len(sh.get("rets", [])) < min_bars: continue
        scores[s] = sum(sh["rets"])
    return sorted(scores, key=lambda s: -scores[s])[:n] if scores else []


# ============== Risk gates ==============
def check_risk_gates(state, equity, positions):
    """Return (allow_new_orders, reason)."""
    if KILL_SWITCH:
        return False, "🛑 KILL_SWITCH active"
    if equity < RISK["min_equity_usd"]:
        return False, f"💀 equity ${equity:.0f} < min ${RISK['min_equity_usd']}"
    # Daily loss check
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("day_marker") != today:
        state["day_marker"] = today
        state["daily_start_equity"] = equity
    daily_pnl_pct = (equity / state["daily_start_equity"] - 1) * 100 if state.get("daily_start_equity") else 0
    if daily_pnl_pct < RISK["daily_loss_halt_pct"]:
        return False, f"📉 daily P&L {daily_pnl_pct:.2f}% < halt {RISK['daily_loss_halt_pct']}%"
    # Total notional
    total_notional = sum(p["size_usd"] for p in positions)
    cap = equity * CFG["leverage"] * RISK["max_total_notional_pct"] / 100
    if total_notional > cap:
        return False, f"⚠️  total notional ${total_notional:.0f} > cap ${cap:.0f}"
    return True, "ok"


# ============== Order placement ==============
def place_stop_loss(sym, side, contracts, entry_price):
    """Place a server-side STOP_MARKET reduceOnly order at -stop_pct from entry.
    Safety net: if cron skips an hour, exchange auto-closes."""
    if DRY_RUN or not API_KEY: return None
    try:
        # long stop = entry * (1 - stop_pct); short stop = entry * (1 + stop_pct)
        if side == 1:
            stop_price = entry_price * (1 - CFG["stop_pct"])
            ccxt_side = "sell"
        else:
            stop_price = entry_price * (1 + CFG["stop_pct"])
            ccxt_side = "buy"
        # Binance USDM: stopPrice triggers MARKET order
        order = EXCHANGE.create_order(
            to_ccxt(sym), "STOP_MARKET", ccxt_side, abs(contracts),
            None, params={"stopPrice": stop_price, "reduceOnly": True, "workingType": "MARK_PRICE"},
        )
        return order.get("id")
    except Exception as e:
        log_event({"event": "stop_place_FAIL", "sym": sym, "err": str(e)})
        notify.send(notify.alert(f"stop-loss order FAIL {sym}: {e}", "ERR"))
        return None


def cancel_stop(sym, stop_order_id):
    if DRY_RUN or not API_KEY or not stop_order_id: return
    try:
        EXCHANGE.cancel_order(stop_order_id, to_ccxt(sym))
    except Exception as e:
        # often the stop already triggered; not an error worth alerting
        print(f"  [stop cancel skip] {sym}: {e}")


def place_market_order(sym, side, size_usd, price_hint, funding=0):
    """Returns (success, info_dict). info_dict includes stop_order_id if placed."""
    mode = "PAPER" if DRY_RUN else "LIVE"
    if DRY_RUN:
        log_event({"event": "open_DRY", "sym": sym, "side": side,
                   "entry_price": price_hint, "size_usd": size_usd})
        notify.send(notify.open_msg(sym, side, price_hint, size_usd, funding, mode))
        return True, {"dry_run": True}
    if not API_KEY:
        return False, {"err": "no api key"}
    try:
        contracts = size_usd / price_hint
        ccxt_side = "buy" if side == 1 else "sell"
        order = EXCHANGE.create_market_order(
            to_ccxt(sym), ccxt_side, contracts,
            params={"reduceOnly": False},
        )
        avg_p = float(order.get("average") or price_hint)
        actual_contracts = float(order.get("filled") or contracts)
        # Place server-side stop loss as safety net
        stop_id = place_stop_loss(sym, side, actual_contracts, avg_p)
        log_event({"event": "open", "sym": sym, "side": side,
                   "entry_price": avg_p,
                   "size_usd": size_usd, "order_id": order.get("id"),
                   "filled": actual_contracts, "stop_order_id": stop_id})
        notify.send(notify.open_msg(sym, side, avg_p, size_usd, funding, mode))
        return True, {**order, "stop_order_id": stop_id}
    except Exception as e:
        log_event({"event": "open_FAIL", "sym": sym, "side": side, "err": str(e)})
        notify.send(notify.alert(f"open FAIL {sym}: {e}", "ERR"))
        return False, {"err": str(e)}


def close_position(sym, side, contracts, price_hint, reason, entry=0, size_usd=0, held_h=0,
                    stop_order_id=None):
    mode = "PAPER" if DRY_RUN else "LIVE"
    if DRY_RUN:
        log_event({"event": "close_DRY", "sym": sym, "side": side,
                   "exit_price": price_hint, "reason": reason})
        pnl = (price_hint/entry - 1) * side * size_usd if entry else 0
        notify.send(notify.close_msg(sym, side, entry or price_hint, price_hint, pnl, held_h, reason, mode))
        return True
    try:
        # Cancel server-side stop first (avoid race: our market order + stop both fire)
        cancel_stop(sym, stop_order_id)
        ccxt_side = "sell" if side == 1 else "buy"
        order = EXCHANGE.create_market_order(
            to_ccxt(sym), ccxt_side, abs(contracts),
            params={"reduceOnly": True},
        )
        exit_p = float(order.get("average") or price_hint)
        pnl = (exit_p/entry - 1) * side * size_usd if entry else 0
        log_event({"event": "close", "sym": sym, "side": side,
                   "exit_price": exit_p, "reason": reason, "order_id": order.get("id")})
        notify.send(notify.close_msg(sym, side, entry or price_hint, exit_p, pnl, held_h, reason, mode))
        return True
    except Exception as e:
        log_event({"event": "close_FAIL", "sym": sym, "err": str(e)})
        notify.send(notify.alert(f"close FAIL {sym}: {e}", "ERR"))
        return False


# ============== Main cycle ==============
def cycle():
    print(f"\n{'='*70}\n=== {datetime.now(timezone.utc).isoformat()}  DRY_RUN={DRY_RUN}  KILL={KILL_SWITCH}")
    state = load_state()

    # 1. Live data
    prices, fundings = fetch_market_data()
    print(f"  prices={len(prices)} funding={len(fundings)}")
    if len(prices) < 30:
        print("  ⚠️ not enough market data, skip"); save_state(state); return

    # 2. Truth-source: equity + positions from exchange (or simulated for DRY)
    now_dt = datetime.now(timezone.utc)
    if DRY_RUN and not API_KEY:
        # Pure simulation when no key set: equity static
        equity = state.get("equity_sim", 10000.0)
        positions = state.get("positions_sim", [])
        new_pos = []
        for p in positions:
            cur = prices.get(p["sym"], p["entry_price"])
            ret_e = (cur / p["entry_price"] - 1) * p["side"]
            entry_t = datetime.fromisoformat(p["entry_time"])
            held_h = (now_dt - entry_t).total_seconds() / 3600
            exit_reason = None
            if ret_e <= -CFG["stop_pct"]: exit_reason = "stop_loss"
            elif held_h >= CFG["hold_hours"]: exit_reason = "hold_expiry"
            if exit_reason:
                pnl = ret_e * p["size_usd"]
                cost = p["size_usd"] * (CFG["fee"] + CFG["slippage"])
                realized = pnl - cost
                equity += realized
                log_event({"event": "close_SIM", "sym": p["sym"], "side": p["side"],
                           "exit_price": cur, "pnl_usd": round(realized, 2),
                           "reason": exit_reason, "held_h": round(held_h, 2)})
                notify.send(notify.close_msg(p["sym"], p["side"], p["entry_price"], cur,
                                              realized, held_h, exit_reason, "PAPER"))
            else:
                new_pos.append(p)
        positions = new_pos
        state["positions_sim"] = positions
        state["equity_sim"] = equity
    else:
        # Real exchange — RECONCILE local metadata with exchange truth
        equity, exch_positions = fetch_account_state()
        if equity is None:
            print("  exchange unreachable, skip"); save_state(state); return

        # local cache: state["live_positions"] = {sym: {entry_time, entry_price, size_usd, side, ...}}
        local = state.setdefault("live_positions", {})
        exch_syms = {p["sym"] for p in exch_positions}
        local_syms = set(local.keys())

        # Detect externally-closed positions (was in local, gone from exchange)
        for sym in local_syms - exch_syms:
            removed = local.pop(sym)
            log_event({"event": "external_close", "sym": sym, "note": "was in local but not on exchange",
                       "had_metadata": removed})
            notify.send(notify.alert(f"{sym} closed externally (manual / liquidation?)", "WARN"))

        # Merge: enrich exchange positions with local metadata, or create fresh
        positions = []
        for ep in exch_positions:
            sym = ep["sym"]
            if sym in local and local[sym].get("side") == ep["side"]:
                # Use local metadata for entry_time, but exchange for current state
                ep["entry_time"] = local[sym]["entry_time"]
                # If sizes mismatch, log (could be partial fill or DCA)
                if abs(local[sym].get("size_usd", 0) - ep["size_usd"]) / max(ep["size_usd"], 1) > 0.05:
                    print(f"  ⚠️ size drift {sym}: local=${local[sym].get('size_usd', 0):.0f} exch=${ep['size_usd']:.0f}")
                    local[sym]["size_usd"] = ep["size_usd"]
            else:
                # New position not opened by us, or side flipped — record now as entry
                ep["entry_time"] = now_dt.isoformat()
                local[sym] = {
                    "entry_time": now_dt.isoformat(),
                    "entry_price": ep["entry_price"], "side": ep["side"],
                    "size_usd": ep["size_usd"],
                }
                if sym not in local_syms:
                    log_event({"event": "external_open", "sym": sym,
                               "note": "appeared on exchange without our open call",
                               "details": ep})
                    notify.send(notify.alert(f"{sym} {('LONG' if ep['side']==1 else 'SHORT')} appeared "
                                              f"(manual order?)", "WARN"))
            positions.append(ep)

    print(f"  equity=${equity:.2f}  active_positions={len(positions)}")
    for p in positions:
        sym = p["sym"]; cur = prices.get(sym, p["entry_price"])
        unr = (cur / p["entry_price"] - 1) * p["side"] * 100
        held_h = (now_dt - datetime.fromisoformat(p["entry_time"])).total_seconds() / 3600 if p.get("entry_time") else 0
        print(f"    {sym:14s} {('LONG' if p['side']==1 else 'SHORT'):5s} entry=${p['entry_price']:.4f} "
              f"now=${cur:.4f} ({unr:+.2f}%) size=${p['size_usd']:.0f} held={held_h:.1f}h")

    # 3. Update shadow simulation
    update_shadow(state, prices, fundings)

    # 4. Pick top-N
    top = get_top_n(state)
    print(f"  top-{len(top)}: {top[:5]}{'...' if len(top)>5 else ''}")

    # 5. Risk gates + control flags
    allow_new, reason = check_risk_gates(state, equity, positions)
    paused, killed = is_paused()
    if killed:
        allow_new, reason = False, "💀 KILL_SWITCH (control.json)"
        global KILL_SWITCH; KILL_SWITCH = True  # also block close logic
    elif paused:
        allow_new, reason = False, "⏸ paused (control.json)"
    print(f"  risk gates: {reason}")
    if not allow_new and state.get("last_risk_alert") != reason:
        notify.send(notify.alert(f"Risk gate blocked: {reason}", "WARN"))
        state["last_risk_alert"] = reason

    # 6. Manage existing positions (live mode): both stop_loss AND hold_expiry
    if not DRY_RUN or API_KEY:
        local = state.get("live_positions", {})
        for p in positions:
            sym = p["sym"]
            cur = prices.get(sym, p["entry_price"])
            ret_e = (cur / p["entry_price"] - 1) * p["side"]
            entry_t_iso = p.get("entry_time")
            held_h = ((now_dt - datetime.fromisoformat(entry_t_iso)).total_seconds() / 3600
                       if entry_t_iso else 0)

            exit_reason = None
            if ret_e <= -CFG["stop_pct"]:
                exit_reason = "stop_loss"
            elif held_h >= CFG["hold_hours"]:
                exit_reason = "hold_expiry"

            if exit_reason:
                stop_id = local.get(sym, {}).get("stop_order_id")
                close_position(sym, p["side"], p.get("contracts", 0), cur, exit_reason,
                               entry=p["entry_price"], size_usd=p["size_usd"], held_h=held_h,
                               stop_order_id=stop_id)
                local.pop(sym, None)

    # 7. Open new positions if risk gates pass
    if allow_new:
        held_syms = {p["sym"] for p in positions}
        n_target = max(3, int(len(UNIVERSE) * CFG["top_pct"] / 100))
        slots = n_target - len(positions)
        per_pos_alloc = (equity / n_target) * CFG["leverage"]
        # Cap per-pos by risk
        max_pos_cap = equity * CFG["leverage"] * RISK["max_pos_notional_pct"] / 100
        per_pos_alloc = min(per_pos_alloc, max_pos_cap)

        for sym in top:
            if slots <= 0: break
            if sym in held_syms: continue
            f = fundings.get(sym, state["last_funding"].get(sym))
            if f is None: continue
            side = -1 if f > CFG["funding_thr"] else (1 if f < -CFG["funding_thr"] else 0)
            if side == 0: continue
            cur = prices.get(sym)
            if cur is None: continue

            ok, info = place_market_order(sym, side, per_pos_alloc, cur, funding=f)
            if ok:
                if DRY_RUN and not API_KEY:
                    state.setdefault("positions_sim", []).append({
                        "sym": sym, "side": side, "entry_price": cur,
                        "entry_time": now_dt.isoformat(),
                        "size_usd": per_pos_alloc,
                        "funding_at_entry": f,
                    })
                else:
                    # Live: record metadata for entry_time + stop tracking
                    actual_entry = float((info or {}).get("average") or cur) if not DRY_RUN else cur
                    state.setdefault("live_positions", {})[sym] = {
                        "entry_time": now_dt.isoformat(),
                        "entry_price": actual_entry,
                        "side": side,
                        "size_usd": per_pos_alloc,
                        "funding_at_entry": f,
                        "order_id": (info or {}).get("id"),
                        "stop_order_id": (info or {}).get("stop_order_id"),
                    }
                slots -= 1

    # 8. Snapshot
    state["last_prices"] = prices
    state["last_funding"] = {**state.get("last_funding", {}), **fundings}
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    state.setdefault("equity_history", []).append({
        "ts": state["last_check"], "equity": equity, "n_positions": len(positions),
        "dry_run": DRY_RUN,
    })
    state["equity_history"] = state["equity_history"][-2000:]
    save_state(state)
    print(f"\n  saved state, mode={'PAPER' if DRY_RUN else '🔴 LIVE'}")


def main():
    try:
        cycle()
    except Exception as e:
        print(f"FATAL: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
