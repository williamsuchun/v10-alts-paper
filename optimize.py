#!/usr/bin/env python3
"""Parameter sweep over multi-window simulation.
   Sweep (leverage, stop_pct) across N windows, find best Sharpe + worst-week.

Usage:
  python optimize.py            # default 8 windows × 4 lev × 3 stop = 96 sims
"""
import argparse, json, statistics, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt

from paper_trader import UNIVERSE, CFG, is_funding_hour, to_ccxt
from simulate import fetch_klines, fetch_funding_history
from simulate_multi import build_panels_full, panels_to_aligned

REPO = Path(__file__).parent
OUT_DIR = REPO / "state" / "multi_sim"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def replay_one(panels, end_dt, warmup_h, sim_h, lev, stop_pct):
    """Run replay with custom lev/stop. Returns dict of stats."""
    total_h = warmup_h + sim_h
    syms_data, timeline = panels_to_aligned(panels, end_dt, total_h)
    if len(syms_data) < 30: return None

    state = {"equity": CFG["initial_capital"], "positions": [],
             "shadow_pnl": {}, "last_prices": {}, "last_funding": {}}
    n_opens = 0; closes = []

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
                if ret_e <= -stop_pct: exit_reason = "stop_loss"
                elif held_h >= CFG["hold_hours"]: exit_reason = "hold_expiry"
                if exit_reason:
                    cost = size_usd * (CFG["fee"] + CFG["slippage"])
                    realized = pnl_usd - cost
                    state["equity"] += realized
                    closes.append({"pnl_usd": realized, "held_h": held_h, "reason": exit_reason})
                else:
                    new_pos.append(p)
            state["positions"] = new_pos

        # update shadow (uses lev + stop_pct)
        lookback = CFG["lookback_hours"]
        for sym in syms_data:
            cur_close = prices.get(sym); prev_close = state["last_prices"].get(sym)
            cur_f = fundings.get(sym, state["last_funding"].get(sym))
            sh = state["shadow_pnl"].setdefault(sym, {"pos": 0, "bars_held": 0, "entry": None, "rets": []})
            if sh["pos"] != 0 and cur_close and sh["entry"]:
                ret_e = (cur_close / sh["entry"] - 1) * sh["pos"]
                if ret_e <= -stop_pct or sh["bars_held"] >= CFG["hold_hours"]:
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

        # pick top-N
        scores = {}; min_bars = lookback // 4
        for s in syms_data:
            sh = state["shadow_pnl"].get(s, {})
            if len(sh.get("rets", [])) < min_bars: continue
            scores[s] = sum(sh["rets"])
        n_top = max(3, int(len(UNIVERSE) * CFG["top_pct"] / 100))
        top = sorted(scores, key=lambda s: -scores[s])[:n_top]

        # open new
        if not warming and top:
            held_syms = {p["sym"] for p in state["positions"]}
            slots = n_top - len(state["positions"])
            alloc = state["equity"] / n_top * lev
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
                    "entry_time": t_dt.isoformat(), "size_usd": alloc,
                })
                n_opens += 1
                slots -= 1

        state["last_prices"] = prices
        state["last_funding"] = {**state.get("last_funding", {}), **fundings}

    floating = sum((state["last_prices"].get(p["sym"], p["entry_price"])/p["entry_price"]-1)
                    *p["side"]*p["size_usd"] for p in state["positions"])
    final_total = state["equity"] + floating
    roi = (final_total / CFG["initial_capital"] - 1) * 100

    if closes:
        wins = [c for c in closes if c["pnl_usd"] > 0]
        losses = [c for c in closes if c["pnl_usd"] <= 0]
        wr = len(wins) / len(closes) * 100
        gp = sum(c["pnl_usd"] for c in wins) if wins else 0
        gl = abs(sum(c["pnl_usd"] for c in losses)) if losses else 1e-9
        pf = gp / gl
    else:
        wr = pf = 0
    return {"roi_pct": roi, "wr_pct": wr, "pf": pf, "n_closes": len(closes), "n_opens": n_opens}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=8)
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--warmup-days", type=int, default=14)
    args = ap.parse_args()

    sim_h = args.window_days * 24
    warmup_h = args.warmup_days * 24
    n_w = args.windows
    total_h = warmup_h + sim_h + (n_w - 1) * sim_h

    print(f"[fetching {total_h}h history for sweep]")
    panels = build_panels_full(UNIVERSE, total_h)

    # Sweep grid
    LEV_GRID = [3, 4, 6, 8]
    STOP_GRID = [0.06, 0.08, 0.10]
    n_total = len(LEV_GRID) * len(STOP_GRID) * n_w
    print(f"\n[sweeping {len(LEV_GRID)} lev × {len(STOP_GRID)} stop × {n_w} windows = {n_total} sims]")

    now_dt = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    grid = []
    cnt = 0; t0 = time.time()
    for lev in LEV_GRID:
        for stop_pct in STOP_GRID:
            window_results = []
            for w in range(n_w):
                end_dt = now_dt - timedelta(hours=w * sim_h)
                r = replay_one(panels, end_dt, warmup_h, sim_h, lev, stop_pct)
                if r: window_results.append(r["roi_pct"])
                cnt += 1
            if not window_results: continue
            mean_roi = statistics.mean(window_results)
            median_roi = statistics.median(window_results)
            min_roi = min(window_results)
            max_roi = max(window_results)
            std = statistics.stdev(window_results) if len(window_results) >= 2 else 0
            sharpe_weekly = mean_roi / std if std > 0 else 0
            n_pos = sum(1 for r in window_results if r > 0)
            entry = dict(lev=lev, stop=stop_pct, mean=mean_roi, median=median_roi,
                          min=min_roi, max=max_roi, std=std, sharpe_w=sharpe_weekly,
                          n_pos=n_pos, n_total=len(window_results),
                          rois=window_results)
            grid.append(entry)
            el = time.time() - t0
            print(f"  lev={lev} stop={stop_pct*100:.0f}%: mean {mean_roi:+5.1f}%  "
                  f"median {median_roi:+5.1f}%  min {min_roi:+6.1f}%  max {max_roi:+5.1f}%  "
                  f"σ {std:.1f}%  Sh_w {sharpe_weekly:.2f}  pos {n_pos}/{len(window_results)}  ({el:.0f}s)",
                   flush=True)

    # Save
    out = OUT_DIR / f"optimize_grid_{n_w}w.jsonl"
    with out.open("w") as f:
        for g in grid: f.write(json.dumps(g) + "\n")
    print(f"\nsaved → {out}")

    # === Ranking ===
    print("\n" + "="*100)
    print("  RANKING")
    print("="*100)
    by_sharpe = sorted(grid, key=lambda g: -g["sharpe_w"])
    print(f"\n  Top 5 by weekly Sharpe (mean / std):")
    print(f"  {'lev':>4s} {'stop':>5s} {'mean':>7s} {'median':>7s} {'min':>7s} {'max':>7s} {'σ':>6s} {'Sh_w':>5s} {'pos%':>5s}")
    for g in by_sharpe[:5]:
        print(f"  {g['lev']:>4d} {g['stop']*100:>4.0f}% {g['mean']:+6.1f}% {g['median']:+6.1f}% "
              f"{g['min']:+6.1f}% {g['max']:+6.1f}% {g['std']:>5.1f}% {g['sharpe_w']:>4.2f} {g['n_pos']/g['n_total']*100:>4.0f}%")

    by_min = sorted(grid, key=lambda g: -g["min"])
    print(f"\n  Top 5 by best worst-week (downside protection):")
    print(f"  {'lev':>4s} {'stop':>5s} {'mean':>7s} {'min':>7s} {'Sh_w':>5s} {'pos%':>5s}")
    for g in by_min[:5]:
        print(f"  {g['lev']:>4d} {g['stop']*100:>4.0f}% {g['mean']:+6.1f}% {g['min']:+6.1f}% "
              f"{g['sharpe_w']:>4.2f} {g['n_pos']/g['n_total']*100:>4.0f}%")

    # Also: check which baseline (lev=6, stop=10%) currently is
    print(f"\n  Current default (lev=6, stop=10%):")
    for g in grid:
        if g["lev"] == 6 and abs(g["stop"] - 0.10) < 0.001:
            print(f"    mean {g['mean']:+.1f}% median {g['median']:+.1f}% min {g['min']:+.1f}% "
                  f"Sh_w {g['sharpe_w']:.2f}  pos {g['n_pos']}/{g['n_total']}")

    # Recommendation
    print("\n  RECOMMENDATION:")
    best = by_sharpe[0]
    safest = by_min[0]
    print(f"    Best Sharpe: lev={best['lev']} stop={best['stop']*100:.0f}% "
          f"(mean +{best['mean']:.1f}%/wk, σ {best['std']:.1f}%, Sh_w {best['sharpe_w']:.2f})")
    print(f"    Safest:      lev={safest['lev']} stop={safest['stop']*100:.0f}% "
          f"(worst week {safest['min']:+.1f}%, mean +{safest['mean']:.1f}%/wk)")
    print("="*100)


if __name__ == "__main__":
    main()
