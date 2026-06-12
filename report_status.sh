#!/usr/bin/env bash
# 汇总 GPU / Slurm 作业 / 最新实验结果,推到 Telegram(可切换)。
# 适合挂 cron 定时跑。所有部分都是「尽力而为」,某项不可用不会让脚本失败。
# 发送器可用 EAGLE_NOTIFY_CMD 覆盖;NOTIFY_STDOUT=1 时只打印不发送。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${EAGLE_REPO:-/scratch/yf3005/EAGLE_new}"
NOTIFY="${EAGLE_NOTIFY_CMD:-$HERE/notify_telegram.sh}"

out=""

# --- Slurm 作业(只看自己的) ---
if command -v squeue >/dev/null 2>&1; then
  jobs="$(squeue -u "$USER" -h -o '%.10i %.9P %.20j %.2t %.10M' 2>/dev/null)"
  if [ -n "$jobs" ]; then
    out+="**Slurm 作业**\n\`\`\`\n$jobs\n\`\`\`\n"
  else
    out+="**Slurm 作业**: 当前无运行/排队作业\n"
  fi
fi

# --- GPU(登录节点通常没 GPU,有就报) ---
if command -v nvidia-smi >/dev/null 2>&1; then
  gpu="$(nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total \
        --format=csv,noheader,nounits 2>/dev/null | awk '{print "GPU"$1": "$2"% "$4"/"$6"MiB"}')"
  [ -n "$gpu" ] && out+="**GPU**\n\`\`\`\n$gpu\n\`\`\`\n"
fi

# --- 最新结果摘要:取最新 results 目录里每个 jsonl 末行的 tokens_per_s / accept_length ---
latest="$(ls -dt "$REPO"/results/*/ 2>/dev/null | head -1)"
if [ -n "$latest" ]; then
  out+="**最新结果** \`$(basename "$latest")\`\n\`\`\`\n"
  for f in "$latest"*.jsonl; do
    [ -e "$f" ] || continue
    line="$(tail -1 "$f" 2>/dev/null)"
    summary="$(echo "$line" | jq -r \
      'def r(x): if (x|type)=="number" then (x*100|round/100) else "?" end;
       "tps=\(r(.tokens_per_s))  accept=\(r(.accept_length))"' 2>/dev/null)"
    out+="$(basename "$f" .jsonl): $summary\n"
  done
  out+="\`\`\`\n"
fi

[ -z "$out" ] && out="(没有可报告的内容)"

# NOTIFY_STDOUT=1 时只打印(给轮询器用 Bot 回复),否则走 webhook 推送
if [ "${NOTIFY_STDOUT:-0}" = "1" ]; then
  echo -e "$out"
else
  echo -e "$out" | "$NOTIFY" -t "EAGLE 状态报告"
fi
