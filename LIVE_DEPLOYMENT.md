# 🔴 LIVE Trading Deployment

⚠️ **READ BEFORE FLIPPING THE SWITCH**

## 7 天 Paper Validation 通过后再做

paper trader 跑 7 天后,确认:
- [ ] 真实 ROI ≥ backtest 期望的 30%
- [ ] 触发 50+ 笔交易
- [ ] Win rate 不远离 backtest 模式
- [ ] 无明显 bug(平仓失败、计算错误)
- [ ] 你心理上接受 -10%/单仓亏损

否则别动真钱。

## Setup (一次性)

### 1. Binance API Key

https://www.binance.com/en/my/settings/api-management

- Create API
- 名字: `v10-alts-trader`
- ✅ Enable Futures
- ❌ Disable Spot trading
- ❌ Disable Withdrawals (绝对不要开!)
- IP whitelist: GitHub Actions IPs 是动态的,留空(或用 VPS 部署再 whitelist)
- 复制 API Key + Secret

### 2. 加到 GitHub Secrets

repo → Settings → Secrets and variables → Actions → New repository secret

- `BINANCE_API_KEY` = 你的 key
- `BINANCE_API_SECRET` = 你的 secret

### 3. 充值 Binance 期货账户

USDT 转入期货账户,**至少 $5,000**(MIN_EQUITY 风控)。

### 4. 第一次:DRY 模式跑 24h

Live trader 默认 DRY_RUN=true。它会:
- 读取你 Binance 账户余额(确认 API key 工作)
- 模拟下单(只 log,不真下)
- 跑 24h 看日志没异常

去 Actions → "v10 alts LIVE trader (gated)" → Run workflow,默认 dry_run=true。

### 5. 切真实交易

GitHub repo → Actions → "v10 alts LIVE trader" → Run workflow → **dry_run = false** → Run

这会 **触发一次真单**。从下次整点开始,hourly cron 也会用 false 跑(因为 GitHub Actions 的 schedule 用 env.DRY_RUN 默认值,你需要去 yml 改默认 'true' → 'false')。

或者更安全:每次都手动触发,不挂 schedule 自动跑。

## 紧急停止

3 种方式:

**A. KILL_SWITCH** (最快,1 分钟生效)
- repo → 编辑 `.github/workflows/live_trader.yml`
- 把 `KILL_SWITCH: 'false'` 改成 `'true'`
- commit
- 下次 cron 会停所有新开仓(已有仓位继续按 stop/expiry 平)

**B. 撤销 API Key** (强行停)
- Binance → API Management → 删 v10-alts-trader key
- 所有 cycle 立即失败

**C. 在 Binance 网页手动平仓**
- 直接在 binance 平掉所有仓位
- 下次 cron 看到无仓 + 无信号 = 不开新单

## 风控参数(在 live_trader.py CFG/RISK)

| 参数 | 默认 | 说明 |
|---|---|---|
| `daily_loss_halt_pct` | -5% | 当日 P&L 跌破此比例 → 当日不再开新仓 |
| `min_equity_usd` | 5000 | equity < 5k 停止交易 |
| `max_pos_notional_pct` | 10% | 单仓 notional ≤ 10% × leverage × equity |
| `max_total_notional_pct` | 70% | 全部 notional ≤ 70% × leverage × equity |

## 已实现的安全机制

✅ **Entry-time tracking** — `state["live_positions"]` 维护本地 metadata, 12h hold expiry 在 live 也工作
✅ **Server-side STOP_MARKET 单** — 开仓后立刻在 Binance 挂 -10% reduceOnly 止损单。
   万一 cron 漏跑(GitHub Actions 偶尔延迟 15+ 分钟),交易所自动平仓
✅ **Reconciliation** — 每 cycle 把 exchange truth 与本地对齐, 检测外部下单 / 手动平仓 / 强平
   有事件 → Telegram WARN 推送

## 已知 limitations / TODO

- ⚠️ **没有 partial fill 处理** — 假设市价单全部成交。Binance USDM 流动性大币基本都全成交,但 meme/小币可能部分成交
- ⚠️ **GitHub Actions 延迟** — schedule 通常延迟 0-15 分钟,影响 12h hold expiry 精度(可能 12.0-12.25h)
- ⚠️ **没有 funding cost 时实时 cost basis 调整** — 长持位 funding 累计但本地 entry_price 不变(对 -10% stop 判断有微小偏差)

要更鲁棒 → VPS 部署 + WebSocket 实时监控。

## 监控

- Status: `python status.py`(在仓库目录)
- Trade log: `state/live_trades.jsonl`
- Equity curve: `state/live_state.json` 里的 `equity_history`
- GitHub Actions log: repo → Actions → 最近一次跑

## 心理准备

backtest 1.94B%/18m 是数学极限,**实盘最多 30-50%**(slippage 流动性瓶颈)。不要期望几个月翻 100x。

但 Sharpe 4-6 + ruin <5% 在加密圈是顶级,**长期累积复利**比短期暴富靠谱。
