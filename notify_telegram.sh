#!/usr/bin/env bash
# 通用 Telegram 推送。用法:
#   notify_telegram.sh "一行消息"
#   echo "多行" | notify_telegram.sh
# 从 ~/.config/eagle-notify.env 读 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID。
set -euo pipefail

ENV_FILE="${EAGLE_NOTIFY_ENV:-$HOME/.config/eagle-notify.env}"
[ -f "$ENV_FILE" ] || { echo "缺少 $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"
: "${TELEGRAM_BOT_TOKEN:?env 缺 TELEGRAM_BOT_TOKEN}"
: "${TELEGRAM_CHAT_ID:?env 缺 TELEGRAM_CHAT_ID}"

title=""
if [ "${1:-}" = "-t" ]; then title="$2"; shift 2; fi

if [ "$#" -gt 0 ]; then body="$*"; else body="$(cat)"; fi
host="$(hostname -s)"
header="[$host $(date '+%m-%d %H:%M')]"
[ -n "$title" ] && header="$header $title"
body="$header
$body"

API="https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN"
# Telegram 单条上限 4096,分块发
while [ -n "$body" ]; do
  chunk="${body:0:3500}"; body="${body:3500}"
  curl -fsS "$API/sendMessage" \
    --data-urlencode chat_id="$TELEGRAM_CHAT_ID" \
    --data-urlencode text="$chunk" >/dev/null || { echo "tg 推送失败" >&2; exit 1; }
done
echo "sent"
