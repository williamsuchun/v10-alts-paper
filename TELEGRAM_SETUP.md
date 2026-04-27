# Telegram 通知配置(可选)

5 分钟设置完,就能在手机收到开/平仓 + 24h 汇总。无 token 时 notify 模块自动 noop,不影响交易。

## 1. 创建 bot

1. Telegram 找 **@BotFather** → 发 `/newbot`
2. 给 bot 起个名字(随便,如 `v10 alts trader`)
3. 给 bot 起个 username(必须以 `bot` 结尾,如 `williamsv10bot`)
4. BotFather 回你一串 token,形如:
   ```
   7891234567:AAEexampleSecretToken_abcdefg
   ```
   **复制保存**。

## 2. 获取你的 chat_id

1. Telegram 找 **@userinfobot** → 发 `/start`
2. 它回你一段消息,里面有个 `Id: 123456789`
3. **复制这串数字**

## 3. 激活会话

打开你刚才创建的 bot(在 BotFather 给你的链接 `t.me/williamsv10bot` 里),给它发任意消息(比如 "hi")。

(没这一步 bot 没法主动给你发消息)

## 4. 加进 GitHub Secrets

去 repo → Settings → Secrets and variables → Actions → **New repository secret**

加两个:
- Name: `TELEGRAM_BOT_TOKEN`  Value: 第 1 步的 token
- Name: `TELEGRAM_CHAT_ID`  Value: 第 2 步的数字 id

## 5. 测试

GitHub Actions → "v10 alts paper trader" → Run workflow

下次跑完,如果有开/平仓事件,你手机会收到推送。

## 本地测试(可选)

```bash
cd ~/v10-alts-paper
export TELEGRAM_BOT_TOKEN=你的token
export TELEGRAM_CHAT_ID=你的chat_id
python notify.py
```

应该看到 "✓ sent" + 手机收到测试消息。

## 推送内容预览

**开仓**:
```
🟢 PAPER OPEN
📈 DOTUSDT LONG
entry: $1.2280
size: $6000
funding: -0.0302%/8h
```

**平仓**:
```
✅ PAPER CLOSE
DOTUSDT LONG
P&L: +$45.20 (+0.75%)
held: 12.0h
reason: hold_expiry
```

**24h 汇总**(每天 UTC 00 推送):
```
📊 PAPER 24h 汇总
equity: $10,127.45  📈 +1.27%
trades closed: 12
open positions: 4
top-5: ORDIUSDT, APEUSDT, DOTUSDT, AXSUSDT, TRUMPUSDT
```

**风控告警**(只 live mode):
```
⚠️ WARN
Risk gate blocked: 📉 daily P&L -5.2% < halt -5.0%
```
