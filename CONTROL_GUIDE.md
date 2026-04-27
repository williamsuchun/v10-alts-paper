# 紧急控制指令

3 种粒度的"刹车":pause / kill / panic_close

## 命令速查

| 命令 | 效果 | 多久生效 | 用途 |
|---|---|---|---|
| `python control.py status` | 看当前状态 | 立即 | 查 |
| `python control.py pause` | 不开新仓,已有仓继续 | 下次 cron(<1h) | 怀疑出问题想观察 |
| `python control.py resume` | 取消 pause | 下次 cron | 解除暂停 |
| `python control.py kill` | pause + kill 标记 | 下次 cron | 决定停一段时间 |
| `python control.py unkill` | 取消 kill(还需 resume) | 下次 cron | 恢复前置 |
| `python control.py panic_close` | **立即平所有 live 仓** + 自动 kill | 立即(几秒) | 🚨 紧急止血 |

## 用 control.py 后必须 commit + push

flag 写在 `state/control.json`,GitHub Actions 跑的 trader 读 repo 里的版本:

```bash
python control.py pause
cd ~/v10-alts-paper && git add state/control.json && git commit -m "control: pause" && git push
```

如果你想**立刻生效**(不等下个整点),去 GitHub Actions 手动触发一次 workflow。

## panic_close 详细流程

```bash
# 必须本地有 API key
export BINANCE_API_KEY="你的key"
export BINANCE_API_SECRET="你的secret"

# 先 dry 看看会平掉什么
python control.py panic_close --dry

# 确认无误后真平
python control.py panic_close
# 输入 YES 确认
```

panic_close 会:
1. 拉 Binance 所有 active 持仓
2. 显示列表(coin / side / contracts / unrealized PnL)
3. 让你输 YES 确认
4. 逐个 reduceOnly market close
5. **自动设 KILL_SWITCH**(防止下次 cron 又开新仓)

## 三种刹车的差别

**pause** — 软刹车
- 不开新仓
- 已有仓位按 12h hold + -10% stop 自然关闭
- 用于: 想观察一段, 不确定要不要继续

**kill** — 硬标记
- 同 pause
- 在 telegram 推送 KILL alert
- 在 trade log 永久记一笔
- 用于: 决定明确停一段较长时间

**panic_close** — 紧急止血
- 立即在 Binance 平掉所有持仓
- 自动 set KILL
- 用于: 发现策略疯了 / 黑天鹅 / 想 100% 落地

## GitHub Actions 也有 KILL_SWITCH

`.github/workflows/live_trader.yml` 里有 env `KILL_SWITCH: 'false'`。改成 `'true'` commit push 也能停, 但要等 cron 跑下一次。

不如用 control.py + 手动 trigger workflow 快。
