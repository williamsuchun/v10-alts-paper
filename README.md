# v10 alts paper trader

Hourly automated paper trading on GitHub Actions.

**Strategy**: funding-rate reversal + adaptive top-N selection on 52 Binance USDM perps (excl BTC/ETH/SOL).

**Backtest**: 18m FULL ROI 1.94B%, Sharpe 8.45, MC ruin 1.40%. See trader-intel for details.

## Files

| File | Purpose |
|---|---|
| `paper_trader.py` | Main hourly trading loop (no real orders, just record) |
| `status.py` | Print current equity / positions / top-N / trades |
| `state/paper_state.json` | Persistent equity, positions, shadow_pnl per coin |
| `state/paper_trades.jsonl` | Append-only trade log |
| `.github/workflows/paper_trader.yml` | GitHub Actions cron (hourly) |
| `requirements.txt` | `ccxt` only |

## Config (in paper_trader.py CFG)

```python
universe        = 52 syms (30 alts + 9 memes + 13 mid-tier)
initial_capital = $10,000
leverage        = 3x per coin     # was 6x
funding_thr     = 0.03% per 8h
hold_hours      = 8                # was 12; signal decays fast
stop_pct        = -6%              # was -10%; tighter preserves capital
lookback        = 14d (336h)
top_pct         = 40% (top 21 of 52)  # was 20; more diversification
fee + slip      = 0.05% + 0.03% per side
```

**Tuning rationale** (from `optimize.py` + `optimize_top_pct.py` sweeps over 8 rolling 7d windows of recent Binance data):

| Config | Mean ROI/wk | σ | Worst | Sharpe_w |
|---|---|---|---|---|
| Original (lev=6 stop=10% top=20% hold=12h) | +0.9% | 20% | -20% | 0.04 |
| Step 1: lev=3 stop=6% | +1.5% | 11% | -10.7% | 0.14 |
| **Step 2: + top=40% hold=8h** | **+1.6%** | **7.5%** | **-7.7%** | **0.21** |

**Realistic expectation: ~128%/year** (vs backtest's mathematically-real-but-unrealistic 282,456%/yr).
Worst-week downside ~-8%, ~63% of weeks profitable.

Insights:
- **Lower lev** on alts/memes = MORE profitable AND safer (high vol triggers stops too easily on high lev)
- **Shorter hold (8h)** beats 12h: funding-rev signal decays fast, longer hold = paying carry
- **More diversification (40%)** beats concentration: 21 active positions ride out single-coin noise

## Setup

```bash
git clone <this-repo>
cd v10-alts-paper

# Optional: pre-seed shadow_pnl from local backtest (skip 14d warmup)
# Copy state/paper_state.json from your trader-intel local run

# Push to GitHub, enable Actions
git push origin main
```

GitHub Actions will run hourly at :00 UTC (delayed 0-15 min).

## Local testing

```bash
pip install ccxt
python paper_trader.py --dry   # no new orders
python paper_trader.py         # real (paper) orders
python status.py               # show state
```

## Cold start

Without a pre-seeded `state/paper_state.json`, the strategy needs **14 days** to warm up shadow P&L for top-N selection. During this period, no positions will be opened.

To skip warmup:
1. Locally clone trader-intel
2. Run `cd scripts/live && python paper_trader_alts.py --seed`
3. Copy the resulting `data/paper_trading/paper_state_alts.json` here as `state/paper_state.json`
4. Commit + push

## Limits

- ⚠️ **Paper only** — does not place real orders. Adapt `open_new_positions` to call `EXCHANGE.create_order()` for live deployment.
- ⚠️ Backtest assumes 0.03% slip; real alt slippage is 0.05-0.15%, larger for memes
- ⚠️ Funding rate has 1h lookahead in backtest; live uses real-time `fetch_funding_rate`
- ⚠️ Universe survivorship bias — 52 syms are all survivors as of 2026-04
