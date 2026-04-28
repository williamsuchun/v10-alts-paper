#!/usr/bin/env python3
"""Multi-window simulation: 7 rolling 7d trading windows over 49d of history.

Goal: see if -15% / 22% win rate from single-window simulate.py is anomaly or norm.
For each window: 14d warmup → 7d trade.

Output: window-by-window stats + summary distribution.

Usage:
  python simulate_multi.py            # 7 windows × 7d
  python simulate_multi.py --windows 4 --window-days 14  # 4 × 14d windows
"""
import argparse, json, statistics, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt

from paper_trader import UNIVERSE, CFG, is_funding_hour, to_ccxt, per_coin_thr, regime_leverage
from simulate import fetch_klines, fetch_funding_history

REPO = Path(__file__).parent
OUT_DIR = REPO / "state" / "multi_sim"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def build_panels_full(syms, total_h):
    """Fetch all data once. Cache."""
    cache = REPO / "state" / "_cache_panels.json"
    # we won't try to deserialize complex structures from cache — just refetch
    print(f"[fetching {len(syms)} syms × {total_h}h data ONCE]")
    panels = {}
    for i, s in enumerate(syms):
        kl = fetch_klines(s, total_h)
        fh = fetch_funding_history(s, total_h)
        panels[s] = {"klines": kl, "funding": fh}
        if (i+1) % 10 == 0 or i == len(syms)-1:
            print(f"  {i+1}/{len(syms)} fetched")
    return panels


def panels_to_aligned(panels, end_dt, n_hours):
    """Build dict {sym: {close: {ts: float}, funding: {ts: float}}} for [end_dt - n_hours .. end_dt]."""
    timeline = [end_dt - timedelta(hours=n_hours - i - 1) for i in range(n_hours)]
    syms_data = {}
    for s, p in panels.items():
        kl = p["klines"]; fh = p["funding"]
        if not kl: continue
        kl_by_h = {}
        for b in kl:
            ts = datetime.fromtimestamp(b[0]/1000, tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
            kl_by_h[ts] = b[4]
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
        n_kl = sum(1 for t in timeline if t in kl_by_h)
        if n_kl < n_hours * 0.7: continue
        syms_data[s] = {"close": kl_by_h, "funding": fh_by_h, "timeline": timeline}
    return syms_data, timeline


def _btc_vol_at(btc_panel, end_dt, lookback_h=336):
    """Compute BTC realized vol annualized from a 336h window ending at end_dt."""
    if not btc_panel: return 0.5
    closes = []
    for i in range(lookback_h):
        t = end_dt - timedelta(hours=lookback_h - 1 - i)
        c = btc_panel["close"].get(t)
        if c: closes.append(c)
    if len(closes) < 50: return 0.5
    rets = [(closes[i]/closes[i-1] - 1) for i in range(1, len(closes))]
    if not rets: return 0.5
    mean = sum(rets) / len(rets)
    var = sum((r - mean)**2 for r in rets) / len(rets)
    return (var ** 0.5) * (8760 ** 0.5)


def run_window(panels, end_dt, warmup_h, sim_h, btc_panel=None):
    """Run replay for one window ending at end_dt. Returns stats dict.
    Uses per-coin thr + regime-aware lev from current CFG."""
    total_h = warmup_h + sim_h
    syms_data, timeline = panels_to_aligned(panels, end_dt, total_h)
    if len(syms_data) < 30:
        return None  # not enough data

    state = {
        "initial_capital": CFG["initial_capital"], "equity": CFG["initial_capital"],
        "positions": [], "shadow_pnl": {}, "last_prices": {}, "last_funding": {},
        "funding_history": {},
    }
    n_opens = 0; n_closes = 0
    closes = []

    # Compute regime lev from BTC vol at trade-start (end of warmup)
    trade_start_dt = timeline[warmup_h] if warmup_h < len(timeline) else end_dt
    btc_vol = _btc_vol_at(btc_panel, trade_start_dt)
    lev = regime_leverage(btc_vol)

    for ti, t_dt in enumerate(timeline):
        warming = ti < warmup_h
        funding_event = is_funding_hour(t_dt)
        prices = {s: syms_data[s]["close"][t_dt] for s in syms_data if t_dt in syms_data[s]["close"]}
        fundings = {s: syms_data[s]["funding"].get(t_dt) for s in syms_data
                     if syms_data[s]["funding"].get(t_dt) is not None}

        # manage positions
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
                    closes.append({"sym": sym, "side": side, "pnl_usd": realized,
                                    "held_h": held_h, "reason": exit_reason})
                    n_closes += 1
                else:
                    new_pos.append(p)
            state["positions"] = new_pos

        # update shadow + funding history
        lookback = CFG["lookback_hours"]
        for sym in syms_data:
            cur_close = prices.get(sym); prev_close = state["last_prices"].get(sym)
            cur_f = fundings.get(sym, state["last_funding"].get(sym))
            sh = state["shadow_pnl"].setdefault(sym, {"pos": 0, "bars_held": 0, "entry": None, "rets": []})
            # Track funding history for per-coin thr
            if funding_event and cur_f is not None:
                fh = state["funding_history"].setdefault(sym, [])
                fh.append(float(cur_f))
                max_n = (CFG["funding_thr_history_h"] // 8) * 2
                if len(fh) > max_n: state["funding_history"][sym] = fh[-max_n:]
            thr = per_coin_thr(state, sym)
            if sh["pos"] != 0 and cur_close and sh["entry"]:
                ret_e = (cur_close / sh["entry"] - 1) * sh["pos"]
                if ret_e <= -CFG["stop_pct"] or sh["bars_held"] >= CFG["hold_hours"]:
                    sh["pos"] = 0; sh["bars_held"] = 0; sh["entry"] = None
            prev_pos = sh["pos"]
            if sh["pos"] == 0 and cur_f is not None:
                sig = -1 if cur_f > thr else (1 if cur_f < -thr else 0)
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

        # pick top-N
        scores = {}; min_bars = lookback // 4
        for s in syms_data:
            sh = state["shadow_pnl"].get(s, {})
            if len(sh.get("rets", [])) < min_bars: continue
            scores[s] = sum(sh["rets"])
        n_top = max(3, int(len(UNIVERSE) * CFG["top_pct"] / 100))
        top = sorted(scores, key=lambda s: -scores[s])[:n_top]

        # open new (per-coin thr)
        if not warming and top:
            held_syms = {p["sym"] for p in state["positions"]}
            slots = n_top - len(state["positions"])
            alloc = state["equity"] / n_top * lev
            for sym in top:
                if slots <= 0: break
                if sym in held_syms: continue
                f = fundings.get(sym, state["last_funding"].get(sym))
                if f is None: continue
                thr = per_coin_thr(state, sym)
                side = -1 if f > thr else (1 if f < -thr else 0)
                if side == 0: continue
                cur = prices.get(sym)
                if cur is None: continue
                state["equity"] -= alloc * (CFG["fee"] + CFG["slippage"])
                state["positions"].append({
                    "sym": sym, "side": side, "entry_price": cur,
                    "entry_time": t_dt.isoformat(), "size_usd": alloc, "funding_at_entry": f,
                })
                n_opens += 1
                slots -= 1

        state["last_prices"] = prices
        state["last_funding"] = {**state.get("last_funding", {}), **fundings}

    # final equity = cash + open floating
    floating = 0
    for p in state["positions"]:
        cur = state["last_prices"].get(p["sym"], p["entry_price"])
        floating += (cur / p["entry_price"] - 1) * p["side"] * p["size_usd"]
    final_total = state["equity"] + floating
    roi = (final_total / CFG["initial_capital"] - 1) * 100

    if closes:
        wins = [c for c in closes if c["pnl_usd"] > 0]
        losses = [c for c in closes if c["pnl_usd"] <= 0]
        win_rate = len(wins) / len(closes) * 100
        avg_win = sum(c["pnl_usd"] for c in wins) / len(wins) if wins else 0
        avg_loss = sum(c["pnl_usd"] for c in losses) / len(losses) if losses else 0
        gp = sum(c["pnl_usd"] for c in wins) if wins else 0
        gl = abs(sum(c["pnl_usd"] for c in losses)) if losses else 1e-9
        pf = gp / gl
        avg_hold = sum(c["held_h"] for c in closes) / len(closes)
        n_stops = sum(1 for c in closes if c["reason"] == "stop_loss")
    else:
        win_rate = avg_win = avg_loss = pf = avg_hold = n_stops = 0

    return {
        "end": end_dt.isoformat(), "n_opens": n_opens, "n_closes": n_closes,
        "n_open_at_end": len(state["positions"]),
        "final_total": round(final_total, 2), "roi_pct": round(roi, 2),
        "win_rate_pct": round(win_rate, 1), "profit_factor": round(pf, 2),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "avg_hold_h": round(avg_hold, 1), "n_stops": n_stops,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=7)
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--warmup-days", type=int, default=14)
    args = ap.parse_args()

    n_w = args.windows
    sim_h = args.window_days * 24
    warmup_h = args.warmup_days * 24
    total_h = warmup_h + sim_h + (n_w - 1) * sim_h  # cover N rolling windows
    print(f"[multi-sim {n_w} × {args.window_days}d, {args.warmup_days}d warmup, fetching {total_h}h]")

    panels = build_panels_full(UNIVERSE, total_h)
    # Also fetch BTC for regime lev
    print("[fetching BTC for regime detection]")
    btc_panels = build_panels_full(["BTCUSDT"], total_h)
    btc_panel_full = None
    if btc_panels and "BTCUSDT" in btc_panels:
        kl = btc_panels["BTCUSDT"]["klines"]
        if kl:
            btc_panel_full = {"close": {datetime.fromtimestamp(b[0]/1000, tz=timezone.utc).replace(minute=0, second=0, microsecond=0): b[4] for b in kl}}

    print(f"\n[running {n_w} rolling windows]")

    now_dt = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    results = []
    for w in range(n_w):
        end_dt = now_dt - timedelta(hours=w * sim_h)
        # BTC vol at trade-start (end of warmup)
        trade_start = end_dt - timedelta(hours=sim_h)
        btc_v = _btc_vol_at(btc_panel_full, trade_start) if btc_panel_full else 0.5
        regime_lev = regime_leverage(btc_v)
        print(f"\n  window {n_w-w}/{n_w}: end={end_dt.isoformat()}  BTC vol={btc_v*100:.0f}% → lev={regime_lev:.2f}x")
        r = run_window(panels, end_dt, warmup_h, sim_h, btc_panel=btc_panel_full)
        if r:
            print(f"    ROI {r['roi_pct']:+6.2f}%  WR {r['win_rate_pct']:5.1f}%  PF {r['profit_factor']:.2f}  "
                  f"trades {r['n_closes']:3d}  stops {r['n_stops']:2d}  hold {r['avg_hold_h']:.1f}h")
            results.append(r)
        else:
            print("    insufficient data")

    # Save
    out_file = OUT_DIR / f"multi_{n_w}x{args.window_days}d.jsonl"
    with out_file.open("w") as f:
        for r in results: f.write(json.dumps(r, default=str) + "\n")
    print(f"\n  saved → {out_file}")

    # === Distribution summary ===
    if results:
        rois = [r["roi_pct"] for r in results]
        wrs = [r["win_rate_pct"] for r in results]
        pfs = [r["profit_factor"] for r in results]
        n_trades = [r["n_closes"] for r in results]
        print("\n" + "="*80)
        print(f"  Distribution over {len(results)} windows × {args.window_days}d")
        print("="*80)
        n_pos = sum(1 for r in rois if r > 0)
        print(f"\n  ROI:  mean {statistics.mean(rois):+6.2f}%   median {statistics.median(rois):+6.2f}%   "
              f"min {min(rois):+6.2f}%   max {max(rois):+6.2f}%")
        print(f"  ROI:  positive windows = {n_pos}/{len(rois)} ({n_pos/len(rois)*100:.0f}%)")
        print(f"  WR:   mean {statistics.mean(wrs):5.1f}%   median {statistics.median(wrs):5.1f}%   range [{min(wrs):.1f}, {max(wrs):.1f}]")
        print(f"  PF:   mean {statistics.mean(pfs):.2f}  median {statistics.median(pfs):.2f}   range [{min(pfs):.2f}, {max(pfs):.2f}]")
        print(f"  Trd:  mean {statistics.mean(n_trades):.0f}  range [{min(n_trades)}, {max(n_trades)}]")

        # Stress test: worst window vs typical
        sorted_rois = sorted(rois)
        if len(sorted_rois) >= 4:
            p25 = sorted_rois[len(sorted_rois)//4]
            print(f"\n  P25 (bad-quartile)  ROI: {p25:+.2f}%")
        print("\n  Window-by-window:")
        print(f"  {'end':24s} {'ROI':>8s} {'WR':>6s} {'PF':>5s} {'trd':>4s}")
        for r in results:
            print(f"    {r['end'][:16]:24s} {r['roi_pct']:+7.2f}% {r['win_rate_pct']:5.1f}% {r['profit_factor']:5.2f} {r['n_closes']:4d}")

        # Verdict
        print("\n" + "="*80)
        median_roi = statistics.median(rois)
        if n_pos/len(rois) > 0.6 and median_roi > 0:
            print("  ✅ Strategy mostly profitable in these windows. Worst case acceptable?")
        elif median_roi > 0:
            print("  ⚠️  Mixed: median positive but many losing windows. Higher variance than backtest.")
        else:
            print("  ❌ Median negative. Strategy not working in current regime — DO NOT deploy live yet.")
        print("="*80)


if __name__ == "__main__":
    main()
