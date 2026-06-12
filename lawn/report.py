"""汇总状态并推送(或仅打印)。对应原 report_status.sh。

环境变量:
  NOTIFY_STDOUT=1   只打印,不推送(供轮询器用 Bot 回复)
  EAGLE_REPO        覆盖仓库目录
"""
import os
import sys

from . import config, status
from .telegram import Telegram, host_header


def main():
    report = status.build_report(config.EAGLE_REPO)

    if os.environ.get("NOTIFY_STDOUT", "0") == "1":
        print(report)
        return 0

    try:
        st = config.Settings()
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    st.require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    Telegram(st.bot_token).send(st.chat_id, report, header=host_header("EAGLE 状态报告"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
