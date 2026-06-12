# lawn

通过 Telegram 远程指挥跑在登录节点 / 集群上的 Claude Code agent，以及推送 GPU / Slurm / 实验状态。无常驻进程，靠 cron 每分钟轮询，适合登录节点。

纯 Python 标准库实现（无第三方依赖），入口用系统 `/usr/bin/python3`，对 cron 友好（cron 不走 login shell、不会有 conda PATH）。

## 结构

```
lawn/
  lawn/                  # 包(纯 stdlib)
    config.py            # env / 路径常量 / Settings
    telegram.py          # Bot API:发消息(分块) + 拉 updates(urllib)
    projects.py          # 项目清单解析 + worktree 解析
    status.py            # squeue / nvidia-smi / 最新 results 汇总
    commands.py          # 白名单指令分发(!status/!tail/!ai...)
    poll.py              # 轮询主体
    notify.py / report.py# notify / report 的 main
  bin/
    lawn-poll            # cron 入口:轮询并执行指令
    lawn-report          # cron 入口:状态汇总 + 推送
    lawn-notify          # CLI:通用 Telegram 推送
  ai_agent.sh            # 保留 bash:后台跑 Claude Code 无头模式(锁 + 隔离)
```

## 配置

1. `~/.config/lawn.env`：

   ```bash
   TELEGRAM_BOT_TOKEN="..."
   TELEGRAM_CHAT_ID="..."            # 回复 / 推送发到这个 chat
   TELEGRAM_ALLOWED_USER_ID="..."    # 只响应这些用户 ID(逗号分隔可多个)
   ```

2. `~/.config/lawn-projects.conf`：每行一个项目，`|` 分隔
   `name | path | mode | worktree | branch`。`mode=inplace` 直接用 repo；
   否则按 worktree 模式（缺省 worktree=`<repo>_ai`、branch=`ai/<name>`，按需创建）。
   `!status` / `!tail` / `!ai` 全部针对**当前项目**（`!use` 切换；缺省取配置第一行）。

3. 可选环境变量：
   - `LAWN_ENV` / `LAWN_PROJECTS` / `LAWN_STATE_DIR` — 覆盖配置 / 状态目录路径。
   - `CLAUDE_BIN` / `AI_TIMEOUT_SEC` — ai_agent.sh 用：claude 二进制、单次超时(默认 900s)。
   - `LAWN_TOTAL_RE` / `LAWN_DONE_RE` / `LAWN_DUR_RE` — 进度/ETA 的日志正则(纯正则、不调 AI)。
     按作业号映射到 `logs/*<jobid>.log`，扫出 总数 / 完成数 / 每条耗时 估 ETA。
     默认面向评测日志(`N prompts` / `steps, mean_accept` / `Xs, Y tok/s`),换项目时覆盖。

## 用法

```bash
bin/lawn-notify "一行消息"            # 或 -t 标题，或 echo 多行 | bin/lawn-notify
NOTIFY_STDOUT=1 bin/lawn-report      # 只打印不推送
bin/lawn-poll                        # 处理一批新消息(cron 每分钟)
```

## 白名单指令

```
!status / !jobs   Slurm 作业(含进度/ETA) + GPU + 最新结果摘要
!tail <jobid>     该作业日志最后 40 行
!projects         列出可操作项目，标出当前项目
!use <项目>       切换当前项目(worktree 模式按需创建隔离工作区)
!where            显示当前项目及其工作目录
!ai <自然语言>    在当前项目里跑 Claude Code 改代码(后台执行)
!help             指令列表
```

## cron 示例

cron 不走 login shell，务必用绝对路径的系统 python 入口：

```cron
* * * * * /scratch/yf3005/lawn/bin/lawn-poll
*/30 * * * * /scratch/yf3005/lawn/bin/lawn-report
```
