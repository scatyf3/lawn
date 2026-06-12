#!/usr/bin/env bash
# Telegram 轮询式命令器:读取 bot 收到的新消息,匹配白名单指令并回复。
# 由 cron 每分钟调用。无常驻进程,适合登录节点。
#
# 需要 ~/.config/eagle-notify.env 里有:
#   TELEGRAM_BOT_TOKEN="..."
#   TELEGRAM_CHAT_ID="..."            # 回复发到这个 chat
#   TELEGRAM_ALLOWED_USER_ID="..."    # 只响应这些用户 ID(逗号分隔可多个)
#
# 白名单指令(绝不执行任意文本):
#   !status / !jobs   -> Slurm 作业 + GPU + 最新结果摘要
#   !tail <jobid>     -> 该作业日志最后 40 行
#   !projects         -> 列出可操作项目,标出当前项目
#   !use <项目>       -> 切换当前项目(worktree 模式会按需创建隔离工作区)
#   !where            -> 显示当前项目及其工作目录
#   !ai <自然语言>    -> 在当前项目里跑 Claude Code 改代码(后台执行)
#   !help             -> 指令列表
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${EAGLE_REPO:-/scratch/yf3005/EAGLE_new}"
ENV_FILE="${EAGLE_NOTIFY_ENV:-$HOME/.config/eagle-notify.env}"
STATE_DIR="$HOME/.cache/eagle-notify"
OFFSET_FILE="$STATE_DIR/tg_offset"

[ -f "$ENV_FILE" ] || { echo "缺少 $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"
: "${TELEGRAM_BOT_TOKEN:?env 缺 TELEGRAM_BOT_TOKEN}"
: "${TELEGRAM_ALLOWED_USER_ID:?env 缺 TELEGRAM_ALLOWED_USER_ID}"
mkdir -p "$STATE_DIR"
API="https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN"

# 项目清单库 + 当前项目状态
# shellcheck disable=SC1090
source "$HERE/projects_lib.sh"
ACTIVE_FILE="$STATE_DIR/active_project"
active_project() { cat "$ACTIVE_FILE" 2>/dev/null || echo "eagle"; }

# 回复到指定 chat(分块,Telegram 上限 4096)
tg_send() {
  local chat="$1" text="$2" chunk
  while [ -n "$text" ]; do
    chunk="${text:0:3500}"; text="${text:3500}"
    curl -fsS "$API/sendMessage" \
      --data-urlencode chat_id="$chat" \
      --data-urlencode text="$chunk" >/dev/null || echo "回复失败" >&2
  done
}

is_allowed() { [[ ",$TELEGRAM_ALLOWED_USER_ID," == *",$1,"* ]]; }

handle() {
  local chat="$1" text="$2"
  local cmd="${text%% *}"                 # 第一个词
  local rest="${text#"$cmd"}"; rest="${rest# }"   # 其余(保留空格)
  case "$cmd" in
    "!status"|"!jobs")
      tg_send "$chat" "$(NOTIFY_STDOUT=1 bash "$HERE/report_status.sh")"
      ;;
    "!tail")
      if [[ ! "$rest" =~ ^[0-9_]+$ ]]; then tg_send "$chat" "用法: !tail <jobid>"; return; fi
      local log
      log="$(ls -t "$REPO"/logs/*"$rest"* "$REPO"/logs/slurm-"$rest"* 2>/dev/null | head -1)"
      if [ -z "$log" ]; then tg_send "$chat" "找不到含 '$rest' 的日志"; return; fi
      tg_send "$chat" "$(basename "$log") 最后 40 行:
$(tail -n 40 "$log")"
      ;;
    "!projects")
      local cur; cur="$(active_project)"
      tg_send "$chat" "可操作项目(★=当前):
$(proj_names | sed "s/^/  /; s|^  $cur\$|★ $cur|")
切换: !use <项目>"
      ;;
    "!use")
      if ! proj_exists "$rest"; then
        tg_send "$chat" "未知项目: '$rest'。可选: $(proj_names | paste -sd' ')"; return
      fi
      local wd; wd="$(proj_workdir "$rest" 2>/tmp/proj_err)"
      if [ -z "$wd" ]; then tg_send "$chat" "切换失败: $(cat /tmp/proj_err)"; return; fi
      echo "$rest" > "$ACTIVE_FILE"
      tg_send "$chat" "✅ 当前项目: $rest
工作目录: $wd"
      ;;
    "!where")
      local cur wd; cur="$(active_project)"; wd="$(proj_workdir "$cur" 2>/dev/null)"
      tg_send "$chat" "当前项目: $cur
工作目录: ${wd:-?}"
      ;;
    "!ai")
      if [ -z "$rest" ]; then tg_send "$chat" "用法: !ai <要做的事>"; return; fi
      local cur wd; cur="$(active_project)"
      wd="$(proj_workdir "$cur" 2>/tmp/proj_err)"
      if [ -z "$wd" ]; then tg_send "$chat" "无法解析项目 '$cur': $(cat /tmp/proj_err)"; return; fi
      # 后台跑,脱离 cron 进程,完成后自行回复(不阻塞每分钟轮询)
      setsid bash "$HERE/ai_agent.sh" "$chat" "$cur" "$wd" "$rest" \
        >> "$REPO/logs/ai_agent_launch.log" 2>&1 &
      ;;
    "!help")
      tg_send "$chat" "指令:
!status | !jobs   状态
!tail <jobid>     作业日志
!projects         项目清单
!use <项目>       切换项目
!where            当前项目
!ai <自然语言>    让 agent 改代码
!help"
      ;;
    *) : ;;
  esac
}

# ---- 拉取新消息(offset 机制:已确认的不再返回)----
offset="$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)"
updates="$(curl -fsS "$API/getUpdates?offset=$offset&timeout=0&allowed_updates=%5B%22message%22%5D")" || exit 0
[ "$(echo "$updates" | jq -r '.ok')" = "true" ] || { echo "getUpdates 失败" >&2; exit 0; }

n="$(echo "$updates" | jq '.result | length')"
[ "$n" -gt 0 ] 2>/dev/null || exit 0

# 新 offset = 最大 update_id + 1
echo "$updates" | jq -r '.result | max_by(.update_id).update_id + 1' > "$OFFSET_FILE"

echo "$updates" | jq -c '.result[] | {uid:.message.from.id, chat:.message.chat.id, text:(.message.text // "")}' | \
while IFS= read -r row; do
  uid="$(echo "$row" | jq -r '.uid')"
  chat="$(echo "$row" | jq -r '.chat')"
  text="$(echo "$row" | jq -r '.text')"
  is_allowed "$uid" || continue
  [[ "$text" == "!"* ]] || continue
  handle "$chat" "$text"
done
