"""Telegram 轮询入口。对应原 poll_commands_tg.sh 主体。由 cron 每分钟调用。"""
import os
import sys

from . import commands, config
from .telegram import Telegram


def _read_offset():
    try:
        with open(config.OFFSET_FILE, encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _write_offset(v):
    with open(config.OFFSET_FILE, "w", encoding="utf-8") as fh:
        fh.write(str(v))


def main():
    try:
        st = config.Settings()
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    st.require("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID")
    os.makedirs(config.STATE_DIR, exist_ok=True)

    tg = Telegram(st.bot_token)
    data = tg.get_updates(_read_offset())
    if not data or not data.get("ok"):
        return 0
    results = data.get("result") or []
    if not results:
        return 0

    # 新 offset = 最大 update_id + 1(先确认,避免重复处理)
    _write_offset(max(u["update_id"] for u in results) + 1)

    allowed = set(st.allowed_user_ids)
    for u in results:
        msg = u.get("message") or {}
        uid = str((msg.get("from") or {}).get("id", ""))
        chat = str((msg.get("chat") or {}).get("id", ""))
        text = msg.get("text") or ""
        if uid not in allowed:
            continue
        if not text.startswith("!"):
            continue
        commands.handle(chat, text, tg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
