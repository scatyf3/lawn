#!/usr/bin/env bash
# 项目清单解析与工作区解析。供 poll_commands_tg.sh / ai_agent.sh source。
PROJECTS_CONF="${PROJECTS_CONF:-$HOME/.config/eagle-notify-projects.conf}"

# 去掉注释/空行,按 | 拆字段(去空格)
_proj_rows() {
  grep -vE '^\s*#|^\s*$' "$PROJECTS_CONF" 2>/dev/null | \
    sed -E 's/\s*\|\s*/|/g; s/^\s+//; s/\s+$//'
}

# 列出所有项目名
proj_names() { _proj_rows | cut -d'|' -f1; }

# 取某项目某字段(1=name 2=path 3=mode 4=worktree 5=branch)
proj_field() {
  local name="$1" idx="$2"
  _proj_rows | awk -F'|' -v n="$name" -v i="$idx" '$1==n{print $i; exit}'
}

proj_exists() { proj_names | grep -qxF "$1"; }

# 解析项目 -> 可用工作目录(必要时建 worktree)。
# 成功打印工作目录到 stdout;失败打印错误到 stderr 并返回 1。
proj_workdir() {
  local name="$1"
  proj_exists "$name" || { echo "未知项目: $name" >&2; return 1; }
  local repo mode wt br
  repo="$(proj_field "$name" 2)"
  mode="$(proj_field "$name" 3)"
  [ -d "$repo" ] || { echo "仓库不存在: $repo" >&2; return 1; }

  if [ "$mode" = "inplace" ]; then
    echo "$repo"; return 0
  fi

  # worktree 模式
  wt="$(proj_field "$name" 4)"; [ -n "$wt" ] || wt="${repo}_ai"
  br="$(proj_field "$name" 5)"; [ -n "$br" ] || br="ai/$name"
  if [ ! -d "$wt" ]; then
    if git -C "$repo" show-ref --verify --quiet "refs/heads/$br"; then
      git -C "$repo" worktree add "$wt" "$br" >&2 || { echo "建 worktree 失败" >&2; return 1; }
    else
      git -C "$repo" worktree add "$wt" -b "$br" >&2 || { echo "建 worktree 失败" >&2; return 1; }
    fi
  fi
  echo "$wt"; return 0
}
