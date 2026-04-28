#!/usr/bin/env python3
"""Re-sweep at REALISTIC slip=0.0008. Find true optimum.
Includes both global-thr and per-coin-thr modes for honest A/B test."""
import argparse, json, statistics, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paper_trader import UNIVERSE, CFG as PCFG, is_funding_hour, to_ccxt
from simulate import fetch_klines, fetch_funding_history
from simulate_multi import build_panels_full, panels_to_aligned, _btc_vol_at

REPO = Path(__file__).parent
OUT_DIR = REPO / "state" / "multi_sim"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def replay(panels, end_dt, warmup_h, sim_h, lev, stop_pct, top_pct, hold_h,
            funding_thr, slippage, fee, mode_thr="global", btc_panel=None):
    total_h = warmup_h + sim_h
    syms_data, timeline = panels_to_aligned(panels, end_dt, total_h)
    if len(syms_data) < 30: return None

    state = {"equity": 10000.0, "positions": [], "shadow_pnl": {},
              "last_prices": {}, "last_funding": {}, "funding_history": {}}
    n_opens = 0; closes = []
    n_top = max(3, int(len(UNIVERSE) * top_pct / 100))
    lookback = 336

    def get_thr(state, sym):
        if mode_thr == "global": return funding_thr
        # per-coin p85
        hist = state["funding_history"].get(sym, [])
        if len(hist) < 30: return funding_thr
        abs_vals = sorted(abs(f) for f in hist[-90:])
        q_idx = int(len(abs_vals) * 0.85)
        thr = abs_vals[min(q_idx, len(abs_vals)-1)]
        return max(0.0001, min(0.002, thr))

    for ti, t_dt in enumerate(timeline):
        warming = ti < warmup_h
        funding_event = is_funding_hour(t_dt)
        prices = {s: syms_data[s]["close"][t_dt] for s in syms_data if t_dt in syms_data[s]["close"]}
        fundings = {s: syms_data[s]["funding"].get(t_dt) for s in syms_data
                     if syms_data[s]["funding"].get(t_dt) is not None}

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
                hh = (t_dt - entry_t).total_seconds() / 3600
                exit_reason = None
                if ret_e <= -stop_pct: exit_reason = "stop_loss"
                elif hh >= hold_h: exit_reason = "hold_expiry"
                if exit_reason:
                    cost = size_usd * (fee + slippage)
                    realized = pnl_usd - cost
                    state["equity"] += realized
                    closes.append({"pnl_usd": realized})
                else:
                    new_pos.append(p)
            state["positions"] = new_pos

        for sym in syms_data:
            cur_close = prices.get(sym); prev_close = state["last_prices"].get(sym)
            cur_f = fundings.get(sym, state["last_funding"].get(sym))
            sh = state["shadow_pnl"].setdefault(sym, {"pos": 0, "bars_held": 0, "entry": None, "rets": []})
            if funding_event and cur_f is not None:
                fh = state["funding_history"].setdefault(sym, [])
                fh.append(float(cur_f))
                if len(fh) > 200: state["funding_history"][sym] = fh[-200:]
            thr = get_thr(state, sym)
            if sh["pos"] != 0 and cur_close and sh["entry"]:
                ret_e = (cur_close / sh["entry"] - 1) * sh["pos"]
                if ret_e <= -stop_pct or sh["bars_held"] >= hold_h:
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
                    bar_ret -= abs(sh["pos"] - prev_pos) * (fee + slippage) * lev
            sh["rets"].append(float(bar_ret))
            if len(sh["rets"]) > lookback: sh["rets"] = sh["rets"][-lookback:]

        scores = {}; min_bars = lookback // 4
        for s in syms_data:
            sh = state["shadow_pnl"].get(s, {})
            if len(sh.get("rets", [])) < min_bars: continue
            scores[s] = sum(sh["rets"])
        top = sorted(scores, key=lambda s: -scores[s])[:n_top]

        if not warming and top:
            held_syms = {p["sym"] for p in state["positions"]}
            slots = n_top - len(state["positions"])
            alloc = state["equity"] / n_top * lev
            for sym in top:
                if slots <= 0: break
                if sym in held_syms: continue
                f = fundings.get(sym, state["last_funding"].get(sym))
                if f is None: continue
                thr = get_thr(state, sym)
                side = -1 if f > thr else (1 if f < -thr else 0)
                if side == 0: continue
                cur = prices.get(sym)
                if cur is None: continue
                state["equity"] -= alloc * (fee + slippage)
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
    roi = (final_total / 10000 - 1) * 100
    return {"roi_pct": roi, "n_closes": len(closes), "n_opens": n_opens}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=8)
    args = ap.parse_args()
    sim_h = 7 * 24; warmup_h = 14 * 24
    n_w = args.windows
    total_h = warmup_h + sim_h + (n_w - 1) * sim_h

    print(f"[fetching {total_h}h]")
    panels = build_panels_full(UNIVERSE, total_h)

    # Pin lev=3 (validated). Slip=0.0008 (realistic).
    LEV = 3; SLIP = 0.0008; FEE = 0.0005
    GRID_TOP = [15, 20, 25, 30, 40]
    GRID_HOLD = [8, 12, 16, 24]
    GRID_THR = [0.0003, 0.0005]
    GRID_STOP = [0.06, 0.08]
    GRID_MODE = ["global", "per_coin"]

    n_total = len(GRID_TOP) * len(GRID_HOLD) * len(GRID_THR) * len(GRID_STOP) * len(GRID_MODE) * n_w
    print(f"\n[sweep grid: {n_total} sims (lev={LEV} slip={SLIP*100:.3f}%)]")

    now_dt = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    grid = []
    cnt = 0; t0 = time.time()
    for top_pct in GRID_TOP:
        for hold in GRID_HOLD:
            for thr in GRID_THR:
                for stop in GRID_STOP:
                    for mode in GRID_MODE:
                        ws = []
                        for w in range(n_w):
                            end_dt = now_dt - timedelta(hours=w * sim_h)
                            r = replay(panels, end_dt, warmup_h, sim_h,
                                        LEV, stop, top_pct, hold, thr, SLIP, FEE, mode)
                            if r: ws.append(r["roi_pct"])
                            cnt += 1
                        if not ws: continue
                        m = statistics.mean(ws); md = statistics.median(ws)
                        std = statistics.stdev(ws) if len(ws) >= 2 else 0
                        sh = m / std if std > 0 else 0
                        n_pos = sum(1 for r in ws if r > 0)
                        grid.append(dict(top=top_pct, hold=hold, thr=thr*100, stop=stop*100, mode=mode,
                                          mean=m, median=md, min=min(ws), max=max(ws), std=std,
                                          sh=sh, n_pos=n_pos, n=len(ws)))
                        el = time.time() - t0
                        print(f"  top={top_pct} h={hold} thr={thr*100:.3f} sl={stop*100:.0f} {mode}: "
                              f"mean {m:+5.2f}% med {md:+5.2f}% min {min(ws):+5.1f}% sh={sh:.2f} "
                              f"pos {n_pos}/{len(ws)} ({el:.0f}s)", flush=True)

    out = OUT_DIR / f"opt_v2_{n_w}w.jsonl"
    with out.open("w") as f:
        for g in grid: f.write(json.dumps(g) + "\n")
    print(f"\nsaved → {out}")

    print("\n" + "="*100)
    print("  RANKING by Sharpe_w (top 10)")
    print("="*100)
    by_sh = sorted(grid, key=lambda g: -g["sh"])
    print(f"  {'top':>4s} {'hold':>5s} {'thr%':>6s} {'stop%':>6s} {'mode':>10s} {'mean':>7s} {'median':>7s} {'min':>7s} {'std':>6s} {'sh':>5s} {'pos%':>5s}")
    for g in by_sh[:10]:
        print(f"  {g['top']:>3d}% {g['hold']:>4d}h {g['thr']:>5.3f} {g['stop']:>5.0f} {g['mode']:>10s} "
              f"{g['mean']:+6.2f}% {g['median']:+6.2f}% {g['min']:+6.1f}% {g['std']:>5.1f}% {g['sh']:>4.2f} {g['n_pos']/g['n']*100:>4.0f}%")

    print(f"\n  Current default: top=40 hold=8 thr=0.030 (per-coin) stop=6 slip=0.080:")
    for g in grid:
        if g["top"]==40 and g["hold"]==8 and abs(g["thr"]-0.03)<0.001 and abs(g["stop"]-6)<0.1 and g["mode"]=="per_coin":
            print(f"    mean {g['mean']:+.2f}% sh {g['sh']:.2f}")

    best = by_sh[0]
    print(f"\n  RECOMMENDATION: top={best['top']} hold={best['hold']}h thr={best['thr']:.3f}% stop={best['stop']:.0f}% mode={best['mode']}")
    print(f"    mean {best['mean']:+.2f}%/wk  sh {best['sh']:.2f}  worst {best['min']:+.1f}%")


if __name__ == "__main__":
    main()
