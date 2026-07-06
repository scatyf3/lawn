#!/usr/bin/env bash
# 在指定项目目录里跑 Claude Code 无头模式,把结果回复到 Telegram。
# 由 lawn.commands(!ai)在后台调用:
#   ai_agent.sh "<chat_id>" "<项目名>" "<工作目录>" "<指令文本>"
#
# 全权限(--dangerously-skip-permissions):agent 能跑任意命令。
# 靠「单实例锁 + worktree/项目隔离 + Telegram 用户白名单」兜底。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOTIFY="$HERE/bin/lawn-notify"

# 解析 claude 二进制(优先级:CLAUDE_BIN > 常见独立安装 > PATH)。
resolve_claude() {
  [ -n "${CLAUDE_BIN:-}" ] && { echo "$CLAUDE_BIN"; return; }
  local p
  for p in "$HOME/.local/bin/claude" "$HOME/.claude/local/claude"; do
    [ -x "$p" ] && { echo "$p"; return; }
  done
  command -v claude 2>/dev/null
}
CLAUDE="$(resolve_claude)"
STATE_DIR="${LAWN_STATE_DIR:-$HOME/.cache/lawn}"
LOCK="$STATE_DIR/ai_agent.lock"
LOG_DIR="$STATE_DIR/agent-logs"
SESS_DIR="$STATE_DIR/sessions"
TIMEOUT_SEC="${AI_TIMEOUT_SEC:-900}"

chat="${1:?需要 chat_id}"; proj="${2:?需要项目名}"; workdir="${3:?需要工作目录}"; shift 3
instr="$*"
[ -n "$instr" ] || { echo "空指令"; exit 1; }

send() { "$NOTIFY" "$1" >/dev/null 2>&1 || true; }

mkdir -p "$STATE_DIR" "$LOG_DIR" "$SESS_DIR"
exec 9>"$LOCK"
if ! flock -n 9; then
  send "⚠️ 已有一个 agent 任务在跑,稍后再试(!ai 串行执行)。"
  exit 0
fi

[ -x "$CLAUDE" ] || { send "❌ 找不到 claude 二进制:$CLAUDE"; exit 1; }
[ -d "$workdir" ] || { send "❌ 工作目录不存在:$workdir"; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
run_log="$LOG_DIR/ai_agent_$ts.log"

# 会话:每个项目一个持久 session id。首次 --session-id 新建,之后 --resume 续接,
# 从而跨多条 !ai 保留上下文。!reset 会删掉该文件,下次即开新会话。
sess_file="$SESS_DIR/$proj.id"
if [ -s "$sess_file" ]; then
  sid="$(cat "$sess_file")"
  session_args=(--resume "$sid")
  sess_note="🧵 续接会话 ${sid:0:8}"
else
  sid="$(/usr/bin/python3 -c 'import uuid;print(uuid.uuid4())')"
  printf '%s' "$sid" >"$sess_file"
  session_args=(--session-id "$sid")
  sess_note="🆕 新会话 ${sid:0:8}"
fi

send "🤖 [$proj] $sess_note
开始处理:$instr
(目录 $workdir,最长 ${TIMEOUT_SEC}s)"

cd "$workdir" || { send "❌ 无法进入 $workdir"; exit 1; }

final="$(timeout "$TIMEOUT_SEC" "$CLAUDE" -p "${session_args[@]}" \
  "$instr

完成后用中文简短总结你做了什么(改了哪些文件、为什么)。不要 git commit 或 push,除非我明确要求。" \
  --dangerously-skip-permissions 2>"$run_log")"
rc=$?

diffstat="$(git -C "$workdir" --no-pager diff --stat 2>/dev/null | tail -20)"
untracked="$(git -C "$workdir" ls-files --others --exclude-standard 2>/dev/null | head -20)"

msg="✅ [$proj] 完成 (rc=$rc)
── agent 总结 ──
${final:-(无输出,见日志 $run_log)}"
[ -n "$diffstat" ] && msg="$msg
── git diff --stat ──
$diffstat"
[ -n "$untracked" ] && msg="$msg
── 新增未跟踪文件 ──
$untracked"
[ "$rc" = "124" ] && msg="⏱️ 超时($TIMEOUT_SEC s)被终止。$msg"
# 非超时失败:多半是会话续接不上(id 失效/损坏),提示可重置。
[ "$rc" != "0" ] && [ "$rc" != "124" ] && msg="$msg

（若持续失败,发 !reset 重开会话再试）"

send "$msg"
