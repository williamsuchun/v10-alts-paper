#!/usr/bin/env python3
"""真诚 sweep: 在 slip=0.0005 (现实) 下找 lev × top × hold 最优."""
import argparse, json, statistics, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paper_trader import UNIVERSE, is_funding_hour, to_ccxt
from simulate import fetch_klines, fetch_funding_history
from simulate_multi import build_panels_full, panels_to_aligned

REPO = Path(__file__).parent
OUT_DIR = REPO / "state" / "multi_sim"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def replay(panels, end_dt, warmup_h, sim_h, lev, stop_pct, top_pct, hold_h,
            funding_thr=0.0003, slippage=0.0005, fee=0.0005):
    total_h = warmup_h + sim_h
    syms_data, timeline = panels_to_aligned(panels, end_dt, total_h)
    if len(syms_data) < 30: return None
    state = {"equity": 10000.0, "positions": [], "shadow_pnl": {},
              "last_prices": {}, "last_funding": {}}
    n_opens = 0; closes = []
    n_top = max(3, int(len(UNIVERSE) * top_pct / 100))
    lookback = 336

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
            if sh["pos"] != 0 and cur_close and sh["entry"]:
                ret_e = (cur_close / sh["entry"] - 1) * sh["pos"]
                if ret_e <= -stop_pct or sh["bars_held"] >= hold_h:
                    sh["pos"] = 0; sh["bars_held"] = 0; sh["entry"] = None
            prev_pos = sh["pos"]
            if sh["pos"] == 0 and cur_f is not None:
                sig = -1 if cur_f > funding_thr else (1 if cur_f < -funding_thr else 0)
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
                side = -1 if f > funding_thr else (1 if f < -funding_thr else 0)
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
    final = state["equity"] + floating
    return {"roi": (final/10000-1)*100, "n_opens": n_opens, "n_closes": len(closes)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=12)
    args = ap.parse_args()
    sim_h = 7 * 24; warmup_h = 14 * 24; n_w = args.windows
    total_h = warmup_h + sim_h + (n_w - 1) * sim_h

    print(f"[fetching {total_h}h] (one shot)")
    panels = build_panels_full(UNIVERSE, total_h)

    # GENUINE SWEEP under realistic slip=0.0005
    SLIP = 0.0005
    GRID = []
    for lev in [3, 4, 5, 6, 8, 10]:
        for top in [10, 15, 20, 25]:
            for hold in [8, 12, 16, 24]:
                for stop in [0.05, 0.06, 0.08, 0.10]:
                    GRID.append((lev, top, hold, stop))

    print(f"\n[sweep grid: {len(GRID)} configs × {n_w} windows = {len(GRID)*n_w} sims at slip={SLIP*100:.2f}%]")
    now_dt = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    results = []
    cnt = 0; t0 = time.time()
    for lev, top, hold, stop in GRID:
        ws = []
        for w in range(n_w):
            end_dt = now_dt - timedelta(hours=w * sim_h)
            r = replay(panels, end_dt, warmup_h, sim_h, lev, stop, top, hold, slippage=SLIP)
            if r: ws.append(r["roi"])
            cnt += 1
        if not ws: continue
        m = statistics.mean(ws); md = statistics.median(ws)
        std = statistics.stdev(ws) if len(ws) >= 2 else 0
        sh = m / std if std > 0 else 0
        n_pos = sum(1 for r in ws if r > 0)
        ann = ((1 + m/100) ** 52 - 1) * 100
        results.append(dict(lev=lev, top=top, hold=hold, stop=stop,
                            mean=m, median=md, min=min(ws), max=max(ws),
                            std=std, sh=sh, n_pos=n_pos, n=len(ws), ann=ann))
        el = time.time() - t0
        if cnt % 20 == 0 or cnt == len(GRID)*n_w:
            print(f"  ...{cnt}/{len(GRID)*n_w} ({el:.0f}s)  best so far Sh: {max(g['sh'] for g in results):.2f}", flush=True)

    out = OUT_DIR / f"opt_v3_{n_w}w.jsonl"
    with out.open("w") as f:
        for g in results: f.write(json.dumps(g) + "\n")
    print(f"\nsaved → {out}")

    print("\n" + "="*100)
    print("  TOP 10 by Sharpe_w (slip=0.05%)")
    print("="*100)
    by_sh = sorted(results, key=lambda g: -g["sh"])
    print(f"  {'lev':>4s} {'top':>4s} {'hold':>5s} {'stop':>5s} {'mean':>7s} {'med':>7s} {'min':>7s} {'std':>5s} {'sh':>5s} {'pos':>4s} {'ann':>8s}")
    for g in by_sh[:10]:
        print(f"  {g['lev']:>3d}x {g['top']:>3d}% {g['hold']:>4d}h {g['stop']*100:>4.0f}% {g['mean']:+6.2f}% {g['median']:+6.2f}% {g['min']:+6.1f}% {g['std']:>4.1f}% {g['sh']:>4.2f} {g['n_pos']/g['n']*100:>3.0f}% {g['ann']:+7.0f}%")

    print("\n  TOP 10 by Annualized return (under ruin/sh constraint sh > 0.05)")
    safe = [g for g in results if g["sh"] > 0.05]
    by_ann = sorted(safe, key=lambda g: -g["ann"])
    print(f"  {'lev':>4s} {'top':>4s} {'hold':>5s} {'stop':>5s} {'mean':>7s} {'min':>7s} {'sh':>5s} {'ann':>8s}")
    for g in by_ann[:10]:
        print(f"  {g['lev']:>3d}x {g['top']:>3d}% {g['hold']:>4d}h {g['stop']*100:>4.0f}% {g['mean']:+6.2f}% {g['min']:+6.1f}% {g['sh']:>4.2f} {g['ann']:+7.0f}%")


if __name__ == "__main__":
    main()
