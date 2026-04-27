#!/usr/bin/env python3
"""Telegram 推送(可选,无 token 时静默 noop).

配置:
  环境变量 TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
  或在 GitHub Secrets 设同名

创建 bot:
  1. Telegram 找 @BotFather → /newbot → 给 token
  2. 找 @userinfobot 拿 chat_id
  3. 给你的 bot 发任何消息(激活会话)
"""
import json, os, sys, urllib.parse, urllib.request


def _creds():
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def send(text, silent=False):
    """发消息. 不抛异常,失败返回 False."""
    token, chat_id = _creds()
    if not token or not chat_id: return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_notification": "true" if silent else "false",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[notify] send failed: {e}", file=sys.stderr)
        return False


def open_msg(sym, side, entry, size_usd, funding, mode="PAPER"):
    arrow = "📈" if side == 1 else "📉"
    s_label = "LONG" if side == 1 else "SHORT"
    icon = "🟢" if mode == "PAPER" else "🔴"
    return (f"{icon} <b>{mode} OPEN</b>\n"
            f"{arrow} <code>{sym}</code> {s_label}\n"
            f"entry: ${entry:.4f}\n"
            f"size: ${size_usd:.0f}\n"
            f"funding: {funding*100:+.4f}%/8h")


def close_msg(sym, side, entry, exit_p, pnl_usd, held_h, reason, mode="PAPER"):
    win = "✅" if pnl_usd > 0 else "❌"
    s_label = "LONG" if side == 1 else "SHORT"
    pct = (exit_p/entry - 1) * side * 100
    return (f"{win} <b>{mode} CLOSE</b>\n"
            f"<code>{sym}</code> {s_label}\n"
            f"P&L: ${pnl_usd:+.2f} ({pct:+.2f}%)\n"
            f"held: {held_h:.1f}h\n"
            f"reason: {reason}")


def daily_summary(equity, total_change_pct, n_trades, n_open, top5, mode="PAPER"):
    arrow = "📈" if total_change_pct > 0 else "📉"
    return (f"📊 <b>{mode} 24h 汇总</b>\n"
            f"equity: ${equity:,.2f}  {arrow} {total_change_pct:+.2f}%\n"
            f"trades closed: {n_trades}\n"
            f"open positions: {n_open}\n"
            f"top-5: {', '.join(top5[:5])}")


def alert(text, level="WARN"):
    icon = {"WARN":"⚠️", "ERR":"🚨", "INFO":"ℹ️"}.get(level, "🔔")
    return f"{icon} <b>{level}</b>\n{text}"


def test():
    token, chat_id = _creds()
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN / CHAT_ID 未设. 跳过."); return False
    print(f"✓ token: {token[:10]}... chat: {chat_id}")
    ok = send("🧪 v10-alts-paper test\n如果看到这条 = 配置 OK")
    print("✓ sent" if ok else "✗ failed")
    return ok


if __name__ == "__main__":
    test()
