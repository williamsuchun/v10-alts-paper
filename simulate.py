#!/usr/bin/env python3
"""Replay last N days of real Binance data through paper-trader logic.

Generates realistic "if we had been paper trading for N days" output:
  state/sim_state.json
  state/sim_trades.jsonl

After running, preview readiness:
  python readiness.py --sim

Usage:
  python simulate.py            # default 7 days
  python simulate.py --days 14  # 2 weeks
  python simulate.py --warmup 14 --days 7  # use 14d to warm shadow_pnl, then trade 7d
"""
import argparse, json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt

# Reuse strategy config
from paper_trader import UNIVERSE, CFG, is_funding_hour, to_ccxt
import comparator

REPO = Path(__file__).parent
SIM_STATE = REPO / "state" / "sim_state.json"
SIM_TRADES = REPO / "state" / "sim_trades.jsonl"
SIM_COMP = REPO / "state" / "sim_comparison.jsonl"

EXCHANGE = ccxt.binanceusdm({"enableRateLimit": True})


def fetch_klines(sym, hours):
    """Fetch last `hours` of 1h klines."""
    since_ms = int((datetime.now(timezone.utc) - timedelta(hours=hours+5)).timestamp() * 1000)
    out = []
    cursor = since_ms
    for _ in range(15):
        try:
            bars = EXCHANGE.fetch_ohlcv(to_ccxt(sym), "1h", since=cursor, limit=1000)
        except Exception as e:
            print(f"  [err {sym}] {e}"); break
        if not bars: break
        out.extend(bars)
        last_ts = bars[-1][0]
        if last_ts <= cursor: break
        cursor = last_ts + 1
        time.sleep(0.1)
    # dedup by ts
    seen = {}
    for b in out: seen[b[0]] = b
    return sorted(seen.values(), key=lambda x: x[0])


def fetch_funding_history(sym, hours):
    """Fetch funding events covering last `hours` of time."""
    since_ms = int((datetime.now(timezone.utc) - timedelta(hours=hours+10)).timestamp() * 1000)
    out = []
    cursor = since_ms
    for _ in range(10):
        try:
            ccxt_sym = to_ccxt(sym)
            batch = EXCHANGE.fetch_funding_rate_history(ccxt_sym, since=cursor, limit=200)
        except Exception as e:
            print(f"  [funding err {sym}] {e}"); break
        if not batch: break
        out.extend(batch)
        last_ts = batch[-1]["timestamp"]
        if last_ts <= cursor: break
        cursor = last_ts + 1
        time.sleep(0.1)
    seen = {}
    for r in out: seen[r["timestamp"]] = r
    return sorted(seen.values(), key=lambda x: x["timestamp"])


def build_panel(syms, hours):
    """Returns dict {sym: {hourly_close: [(ts, close), ...], hourly_funding: [(ts, rate), ...]}}.
    All aligned to hourly UTC."""
    print(f"[fetching {len(syms)} syms × klines + funding for {hours}h]...")
    panels = {}
    for i, s in enumerate(syms):
        kl = fetch_klines(s, hours)
        fh = fetch_funding_history(s, hours)
        panels[s] = {"klines": kl, "funding": fh}
        print(f"  {i+1}/{len(syms)} {s:14s} klines={len(kl)} funding={len(fh)}")
    return panels


def replay(panels, warmup_h, sim_h):
    """Run paper trader logic over the panels. warmup_h bars warm up shadow_pnl, then sim_h bars actually trade."""
    print(f"\n[replaying warmup={warmup_h}h sim={sim_h}h]")

    # Build hourly time axis
    now_dt = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    total_h = warmup_h + sim_h
    timeline = [now_dt - timedelta(hours=total_h - i - 1) for i in range(total_h)]

    # Per-sym hourly close, hourly funding (ffilled)
    syms_data = {}
    for s in panels:
        kl = panels[s]["klines"]
        fh = panels[s]["funding"]
        if not kl: continue
        # hourly close indexed by hour-of-the-timeline
        kl_by_h = {}
        for b in kl:
            ts = datetime.fromtimestamp(b[0]/1000, tz=timezone.utc)
            ts = ts.replace(minute=0, second=0, microsecond=0)
            kl_by_h[ts] = b[4]  # close
        # funding: last seen rate at each timeline hour (ffill)
        fh_by_h = {}
        if fh:
            sorted_fh = sorted(fh, key=lambda x: x["timestamp"])
            cur_rate = None
            fh_iter = iter(sorted_fh)
            next_event = next(fh_iter, None)
            for t in timeline:
                t_ms = int(t.timestamp() * 1000)
                while next_event is not None and next_event["timestamp"] <= t_ms:
                    cur_rate = next_event["fundingRate"]
                    next_event = next(fh_iter, None)
                fh_by_h[t] = cur_rate
        # Only include if we have full coverage
        n_kl = sum(1 for t in timeline if t in kl_by_h)
        if n_kl < total_h * 0.8: continue
        syms_data[s] = {"close": kl_by_h, "funding": fh_by_h}
    print(f"  {len(syms_data)}/{len(panels)} syms have sufficient data")

    # === State ===
    state = {
        "initial_capital": CFG["initial_capital"], "equity": CFG["initial_capital"],
        "positions": [], "shadow_pnl": {}, "last_prices": {}, "last_funding": {},
        "history": {"equity_curve": []}, "shadow_portfolio_eq": CFG["initial_capital"],
    }
    SIM_STATE.parent.mkdir(parents=True, exist_ok=True)
    SIM_TRADES.unlink(missing_ok=True)
    SIM_COMP.unlink(missing_ok=True)

    def log_event(e):
        with SIM_TRADES.open("a") as f: f.write(json.dumps(e, default=str) + "\n")

    # === Hourly cycle ===
    n_opens = 0; n_closes = 0
    for ti, t_dt in enumerate(timeline):
        t_iso = t_dt.isoformat()
        warming = ti < warmup_h
        funding_event = is_funding_hour(t_dt)

        # Build prices/fundings dict for this hour
        prices = {s: syms_data[s]["close"][t_dt] for s in syms_data if t_dt in syms_data[s]["close"]}
        fundings = {s: syms_data[s]["funding"].get(t_dt) for s in syms_data
                     if syms_data[s]["funding"].get(t_dt) is not None}

        # === manage real positions ===
        if not warming:
            new_pos = []
            for p in state["positions"]:
                sym = p["sym"]; entry = p["entry_price"]; size_usd = p["size_usd"]; side = p["side"]
                cur_close = prices.get(sym, entry)
                ret_e = (cur_close / entry - 1) * side
                pnl_usd = ret_e * size_usd
                if funding_event:
                    f = fundings.get(sym, state["last_funding"].get(sym, 0))
                    pnl_usd -= side * f * size_usd
                entry_t = datetime.fromisoformat(p["entry_time"])
                held_h = (t_dt - entry_t).total_seconds() / 3600
                exit_reason = None
                if ret_e <= -CFG["stop_pct"]: exit_reason = "stop_loss"
                elif held_h >= CFG["hold_hours"]: exit_reason = "hold_expiry"
                if exit_reason:
                    cost = size_usd * (CFG["fee"] + CFG["slippage"])
                    realized = pnl_usd - cost
                    state["equity"] += realized
                    log_event({"event": "close", "sym": sym, "side": side,
                               "entry_price": entry, "exit_price": cur_close,
                               "size_usd": size_usd, "held_h": round(held_h, 2),
                               "pnl_usd": round(realized, 2), "reason": exit_reason,
                               "ts": t_iso})
                    n_closes += 1
                else:
                    new_pos.append(p)
            state["positions"] = new_pos

        # === update shadow ===
        lookback = CFG["lookback_hours"]; lev = CFG["leverage"]
        for sym in syms_data:
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
                if funding_event and cur_f is not None: bar_ret -= prev_pos * cur_f * lev
                if prev_pos != sh["pos"]:
                    bar_ret -= abs(sh["pos"] - prev_pos) * (CFG["fee"] + CFG["slippage"]) * lev
            sh["rets"].append(float(bar_ret))
            if len(sh["rets"]) > lookback: sh["rets"] = sh["rets"][-lookback:]

        # === pick top-N ===
        scores = {}; min_bars = lookback // 4
        for s in syms_data:
            sh = state["shadow_pnl"].get(s, {})
            if len(sh.get("rets", [])) < min_bars: continue
            scores[s] = sum(sh["rets"])
        n_top = max(3, int(len(UNIVERSE) * CFG["top_pct"] / 100))
        top = sorted(scores, key=lambda s: -scores[s])[:n_top]

        # === open new positions (only outside warmup) ===
        if not warming and top:
            held_syms = {p["sym"] for p in state["positions"]}
            slots = n_top - len(state["positions"])
            alloc = state["equity"] / n_top * CFG["leverage"]
            for sym in top:
                if slots <= 0: break
                if sym in held_syms: continue
                f = fundings.get(sym, state["last_funding"].get(sym))
                if f is None: continue
                side = -1 if f > CFG["funding_thr"] else (1 if f < -CFG["funding_thr"] else 0)
                if side == 0: continue
                cur = prices.get(sym)
                if cur is None: continue
                state["equity"] -= alloc * (CFG["fee"] + CFG["slippage"])
                state["positions"].append({
                    "sym": sym, "side": side, "entry_price": cur,
                    "entry_time": t_iso, "size_usd": alloc, "funding_at_entry": f,
                })
                log_event({"event": "open", "sym": sym, "side": side, "entry_price": cur,
                           "size_usd": alloc, "funding": f, "ts": t_iso})
                n_opens += 1
                slots -= 1

        # === comparator snapshot (after warmup only) ===
        if not warming:
            floating = 0
            for p in state["positions"]:
                cur = prices.get(p["sym"], p["entry_price"])
                floating += (cur / p["entry_price"] - 1) * p["side"] * p["size_usd"]
            paper_total = state["equity"] + floating
            shadow_eq = state.get("shadow_portfolio_eq", CFG["initial_capital"])
            if top:
                last_rets = [state["shadow_pnl"][s]["rets"][-1] for s in top
                              if state["shadow_pnl"].get(s, {}).get("rets")]
                if last_rets:
                    shadow_eq *= (1 + sum(last_rets) / len(last_rets))
                    state["shadow_portfolio_eq"] = shadow_eq
            elapsed_d = (ti - warmup_h) / 24
            bt_expected = CFG["initial_capital"] * ((1 + comparator.BT_CAGR_PCT/100) ** (elapsed_d / 365))
            with SIM_COMP.open("a") as f:
                f.write(json.dumps({
                    "ts": t_iso, "days_elapsed": round(elapsed_d, 3),
                    "paper_total": round(paper_total, 2),
                    "shadow_total": round(shadow_eq, 2),
                    "bt_expected": round(bt_expected, 2),
                    "friction_pct": round((1 - paper_total/shadow_eq)*100, 2) if shadow_eq > 0 else 0,
                    "backtest_gap_pct": round((1 - paper_total/bt_expected)*100, 2) if bt_expected > 0 else 0,
                    "n_positions": len(state["positions"]),
                }) + "\n")
            state.setdefault("history", {}).setdefault("equity_curve", []).append({
                "ts": t_iso, "equity": state["equity"], "floating": floating,
                "total": paper_total, "n_positions": len(state["positions"]),
            })

        state["last_prices"] = prices
        state["last_funding"] = {**state.get("last_funding", {}), **fundings}
        state["last_check"] = t_iso

        if (ti+1) % 24 == 0:
            phase = "warming" if warming else "trading"
            floating = sum((prices.get(p["sym"], p["entry_price"])/p["entry_price"]-1)*p["side"]*p["size_usd"]
                            for p in state["positions"])
            print(f"  hour {ti+1}/{total_h} ({phase})  eq=${state['equity']:.0f}  "
                   f"floating=${floating:+.0f}  positions={len(state['positions'])}  "
                   f"opens={n_opens} closes={n_closes}")

    # save state
    SIM_STATE.write_text(json.dumps(state, indent=2, default=str))
    print(f"\n  saved → {SIM_STATE}")
    print(f"  trades → {SIM_TRADES}  ({n_opens} opens, {n_closes} closes)")
    print(f"  comparator → {SIM_COMP}")
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="Days to simulate trading")
    ap.add_argument("--warmup", type=int, default=14, help="Warmup days for shadow P&L")
    args = ap.parse_args()

    warmup_h = args.warmup * 24
    sim_h = args.days * 24
    total_h = warmup_h + sim_h
    print(f"=== simulate {args.days}d trading + {args.warmup}d warmup = {total_h}h total ===")
    panels = build_panel(UNIVERSE, total_h)
    replay(panels, warmup_h, sim_h)


if __name__ == "__main__":
    main()
