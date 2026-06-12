# lawn

通过 Telegram 远程指挥跑在登录节点 / 集群上的 Claude Code agent，以及推送 GPU / Slurm / 实验状态。无常驻进程，靠 cron 每分钟轮询，适合登录节点。

## 组成

- `notify_telegram.sh` — 通用 Telegram 推送（一行参数或 stdin 多行）。
- `report_status.sh` — 汇总 Slurm 作业 / GPU / 最新实验结果并推送，适合挂 cron。
- `poll_commands_tg.sh` — 轮询 bot 收到的消息，匹配白名单指令并回复，适合挂 cron。
- `ai_agent.sh` — 在指定项目目录里跑 Claude Code 无头模式，结果回复到 Telegram（由 `poll_commands_tg.sh` 后台调用）。
- `projects_lib.sh` — 项目清单解析与工作区（worktree）解析，被上面两个脚本 source。

## 配置

1. `~/.config/eagle-notify.env`：

   ```bash
   TELEGRAM_BOT_TOKEN="..."
   TELEGRAM_CHAT_ID="..."            # 回复发到这个 chat
   TELEGRAM_ALLOWED_USER_ID="..."    # 只响应这些用户 ID（逗号分隔可多个）
   ```

2. `~/.config/eagle-notify-projects.conf`：每行一个项目，`|` 分隔字段
   `name | path | mode | worktree | branch`。

3. 可选环境变量：
   - `EAGLE_REPO` — `!tail` 找日志、report 找最新 results 的默认仓库目录（默认 `/scratch/yf3005/EAGLE_new`）。
   - `CLAUDE_BIN` — 指定 claude 二进制路径。
   - `EAGLE_NOTIFY_ENV` — 覆盖 env 文件路径。

## 白名单指令

```
!status / !jobs   Slurm 作业 + GPU + 最新结果摘要
!tail <jobid>     该作业日志最后 40 行
!projects         列出可操作项目，标出当前项目
!use <项目>       切换当前项目（worktree 模式按需创建隔离工作区）
!where            显示当前项目及其工作目录
!ai <自然语言>    在当前项目里跑 Claude Code 改代码（后台执行）
!help             指令列表
```

## cron 示例

```cron
* * * * * bash /scratch/yf3005/lawn/poll_commands_tg.sh
*/30 * * * * bash /scratch/yf3005/lawn/report_status.sh
```
