# Lessons Learned — v10 alts paper trader build

构建于 2026-04-27/28 一晚的迭代笔记。**不是 README,是给未来自己的踩坑警告**。

---

## 1. Backtest 1.94B% ROI 是 friction 假设错的幻觉

**症状**:funding_rev_v10 全周期 backtest 显示 ROI 1,937,907,626%,Sharpe 8.45。听起来梦幻。
**真相**:
- 18 个月 78 周 → +1.94B% 等于 +27%/周。任何策略稳定一周赚 27% 都不可能。
- 数学上是真的(每个 bar return 累乘出来),但前提全错:
  - slippage = 0.0003(实际 0.0005-0.0008)
  - lev = 10x(单仓 -10% stop = -100% 资本损失)
  - adaptive top-N 在 18m 池子里 hindsight 选(survivorship bias)
- 真实可达:**~30%/年**,Sharpe ~0.5,最差周 -20%

**教训**:任何 backtest > 100%/年 必须假设 friction 或 lookahead 错。**永远先估计真实 friction,然后 sweep**。

---

## 2. In-sample sweep 不能跨参数环境

**症状**:用 `optimize.py` sweep 在 slip=0.0003 下找到 top_pct=40 hold=8h 是最优(Sharpe_w 0.21 vs baseline 0.04)。激动地 deploy。
**真相**:
- 把 slip 改成更现实的 0.0008 后,top_pct=40 hold=8h 反而 mean -3.79%/wk(惨亏)。
- 原因:更多 turnover (70+ trades/wk × 5bp × 2 side = 7%/周) 被 friction 吃光。
- 同样的"最优"在不同 friction 假设下完全反向。

**教训**:**改任何一个假设都要 RE-SWEEP**。不能只调一个变量然后假定其它最优配置不变。

---

## 3. "MAX EFFORT" 加越多变越差

**症状**:一晚加了:
- per-coin funding_thr (rolling p85 quantile)
- regime-aware leverage (lev 1.5-4.0)
- top_pct 20→40 (更分散)
- hold 12→8h (短持)
- slippage 0.0003→0.0008 (更现实)
- net exposure cap

**真相**:这些 changes 看起来都"应该好",但 **乘起来**让策略 mean 从 +0.69%/wk 变成 **-3.79%/wk**(差 5x)。
- per-coin thr 让 meme 触发太频繁
- top_pct=40 让仓位数翻倍
- hold=8h 让周转翻倍
- slippage 翻 2.7x

每个 friction 单独看不大,**叠加**起来吃光所有 alpha。

**教训**:
1. **一次只改一个变量**,验证后再改下一个
2. 改完每个都要重跑 multi-window 验证
3. 不要在 sweep 之外 "凭感觉" 调参

---

## 4. Per-coin 自适应阈值不一定比全局好

**直觉**:meme funding 量级大(±0.1%),majors 小(±0.02%),应该每币用自己的 p85 当阈值。
**实测**:per-coin thr 在所有测试窗口都 underperform global 0.03%。
- meme 用更高阈值,触发率没变多
- majors 用更低阈值,触发更频繁但 noise 也多
- 总体增加交易次数 → 更多 friction

**教训**:adaptive 不一定优于 fixed。简单 + 全局 + tested 通常胜复杂 + per-asset + theoretical。

---

## 5. 高杠杆放大 friction 多于放大 alpha

**症状**:lev=6x 看起来该比 lev=3x 赚 2 倍。
**真相**:
- lev=6 + stop=10% = 单仓 max -60% 资本损失
- 高 lev 让 fee/slip 占 alpha 比例下降,但同时让 stop 触发概率提升
- 实测 lev=3 的 Sharpe_w(0.14) > lev=6(0.04)

**教训**:**最佳 leverage 不是越高越好**,是 alpha/(friction + tail risk) 的甜点。山寨/meme 上通常 2-4x。

---

## 6. Service Worker 缓存把开发体验搞死

**症状**:更新 dashboard 后用户看不到变化。
**原因**:sw.js 用 cache-first 策略 + 硬编码 VERSION="v1" → 永不失效。
**修复**:
- 改 network-first(每次先尝试网络,离线才用 cache)
- VERSION 加日期 string,每次部署 bump

**教训**:PWA 的 cache 策略要 dev-friendly。优先确保更新能即时生效,再优化离线体验。

---

## 7. 真实回报期望 vs 用户期望

**用户问**:"30%/年 是不是太低了?"
**事实**:
- BTC HODL 历史 ~50%/年(波动巨大)
- 顶尖加密 HF: 30-80%/年
- 高频做市: 30-60%/年(24/7 自动)
- 我们 30%/年 是真实可持续

但用户经历过 backtest 1.94B% 后,30% 看起来很差。**心理预期被夸张数字毁了**。

**教训**:
- 写 backtest 时 prominently 显示真实 friction 假设下的数字
- 不要让 "1.94B%" 这种数字进入用户记忆,会被锚定
- 用 "annualized expected" 而不是 "cumulative ROI"

---

## 8. 可视化提升 ≠ alpha 提升

**症状**:dashboard 9 轮 polish 后,strategy 真实 ROI 还是 30%/年。
**真相**:好看的 dashboard 不会让策略赚钱。但好看的 dashboard 让你**愿意持续监控**,catch 问题更早,这是 indirect alpha。

**教训**:UI/UX 投入和策略研究投入应该平衡。**不要用 polish 来逃避面对真实 alpha 不足**。

---

## 9. GitHub Actions cron 不可靠

**症状**:cron 设 `0 * * * *` 每小时,但实际经常延迟 30+ 分钟,有时跳过整小时。
**真相**:GitHub Actions 共享资源,scheduled workflow 在高峰期会被延后。
**应对**:
- 服务端 STOP_MARKET 单作为兜底(不依赖 cron 跑)
- 关键事件(开/平仓)立即 commit + 推送,不积攒
- 监控 cron 健康度(workflow 跑次数 vs 期望)

**教训**:免费 cron 服务不能假定 SLA。关键路径要有 fail-safe。

---

## 10. 写 doc 比想象的有价值

**症状**:这份文档花了 30 分钟写。
**预期回报**:
- 下次想做新策略时少走半天弯路
- 跟人解释 "为什么不用 lev=10x" 节省一小时
- 6 个月后回看,记得为什么 top_pct=20 而不是 40

**教训**:**做完一个项目立刻写 retrospective**。trade-off 在你脑子里清晰时记下,几周后就模糊了。

---

## 当前 deployable 配置(2026-04-28)

```python
# 经过所有踩坑后,真正稳的 config:
leverage = 3.0                  # base, regime-adjusted 2.0-3.5
funding_thr = 0.0003 (global)   # per-coin disabled (失败)
hold_hours = 12
stop_pct = 0.06
top_pct = 20                    # 10 positions
slippage = 0.0005               # 5bp realistic at $10k
fee = 0.0005

# 真实期望(16-window sim):
# Mean: +0.5%/wk  
# Median: 0.0%/wk  
# Worst week: -20%
# Annualized: ~30%/yr
# Sharpe_w: ~0.05
```

不要再 "优化" 这个配置了,除非有强证据某个变量改变后更好。
