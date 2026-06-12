"""通用 Telegram 推送 CLI。对应原 notify_telegram.sh。

用法:
  lawn-notify "一行消息"
  lawn-notify -t 标题 "消息"
  echo "多行" | lawn-notify
"""
import sys

from . import config
from .telegram import Telegram, host_header


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    title = ""
    if argv and argv[0] == "-t":
        title = argv[1]
        argv = argv[2:]
    body = " ".join(argv) if argv else sys.stdin.read()

    try:
        st = config.Settings()
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    st.require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")

    ok = Telegram(st.bot_token).send(st.chat_id, body, header=host_header(title))
    if ok:
        print("sent")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
