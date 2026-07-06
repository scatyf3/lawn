# lawn 命令参考

两类入口:**Telegram 白名单指令**(手机上发,bot 回)和**命令行入口**(本地 / cron 跑)。

---

## Telegram 白名单指令

在与 bot 的对话里发送,以 `!` 开头。只有 `~/.config/lawn.env` 里
`TELEGRAM_ALLOWED_USER_ID` 列出的用户会被响应;非 `!` 开头的消息一律忽略。
未知的 `!xxx` 会回一份帮助。`lawn-poll` 由 cron 每分钟拉取并执行。

| 指令 | 说明 |
|------|------|
| `!status` / `!jobs` | 状态汇总:Slurm 作业(含 **进度 N/总数** 与 **ETA**)+ GPU 占用 + 最新结果 |
| `!tail <jobid>` | 该作业日志的最后 40 行(在当前项目 `logs/` 下按 jobid 匹配) |
| `!projects` | 列出可操作项目,`★` 标当前项目 |
| `!use <项目>` | 切换当前项目;worktree 模式会按需创建隔离工作区 |
| `!where` | 显示当前项目、工作目录、以及当前会话状态 |
| `!ai <自然语言>` | 在当前项目里后台跑 Claude Code 改代码(锁 + 隔离,不阻塞轮询) |
| `!reset` | 重置当前项目的会话,下次 `!ai` 开一段全新对话 |
| `!help` | 显示指令列表 |

`!status` / `!tail` / `!ai` 都针对**当前项目**(`!use` 切换;缺省取项目清单第一项)。

### 会话(!ai 的上下文记忆)

每个项目维持**一段持续会话**:同一项目连续 `!ai` 会**续接上下文**(第二条 `!ai`
记得第一条做过什么),底层用 Claude Code 的 `--session-id`(首次新建)/ `--resume`
(之后续接),按项目名隔离,id 存 `~/.cache/lawn/sessions/<项目>.id`。

- **开新会话**:`!reset`(删掉当前项目会话文件);或 `!use <别的项目>` 切到另一项目
  (各项目会话互相独立)。下一条 `!ai` 即全新对话。
- **看当前会话**:`!where` 会显示会话 id 前 8 位,或"无(下次 !ai 新建)"。
- 若续接持续报错(id 失效/损坏),`!reset` 后重试。

### 例子

```
!status
!use lcrkv
!tail 10661257_5
!ai 把 results 目录下的空结果清掉，并在 README 里记一笔
```

### 进度 / ETA 怎么来的

`!status` / `lawn-report` 把每个**运行中**的 Slurm 作业号映射到 `logs/*<jobid>.log`,
纯正则扫出 总数 / 完成数 / 每条耗时,算出 `done/total` 和 ETA(剩余 × 平均每条耗时),
**不调用 AI**。默认正则面向评测日志,换项目用环境变量覆盖(见下)。

---

## 命令行入口(`bin/`)

cron 不走 login shell,务必用绝对路径调用(入口为系统 `/usr/bin/python3`,纯 stdlib)。

| 入口 | 用途 |
|------|------|
| `bin/lawn-poll` | 拉一批新消息并执行白名单指令(cron 每分钟) |
| `bin/lawn-report` | 状态汇总 + 推送到 Telegram(可挂 cron 定时跑) |
| `bin/lawn-notify` | 通用 Telegram 推送 CLI |

```bash
bin/lawn-notify "一行消息"           # 或 -t 标题,或 echo 多行 | bin/lawn-notify
NOTIFY_STDOUT=1 bin/lawn-report      # 只打印不推送(终端去掉 HTML 标签)
bin/lawn-poll                        # 处理一批新消息
```

### cron 示例

```cron
* * * * * /scratch/yf3005/lawn/bin/lawn-poll   >> /scratch/yf3005/lawn/cron.log 2>&1
*/30 * * * * /scratch/yf3005/lawn/bin/lawn-report >> /scratch/yf3005/lawn/cron.log 2>&1
```

---

## 相关环境变量

| 变量 | 作用 |
|------|------|
| `LAWN_ENV` / `LAWN_PROJECTS` / `LAWN_STATE_DIR` | 覆盖配置 / 状态目录路径 |
| `CLAUDE_BIN` / `AI_TIMEOUT_SEC` | `!ai` 用:claude 二进制、单次超时(默认 900s) |
| `LAWN_TOTAL_RE` / `LAWN_DONE_RE` / `LAWN_DUR_RE` | 进度/ETA 的日志正则;换项目时覆盖,保持 project-driven |
| `NOTIFY_STDOUT=1` | `lawn-report` 只打印不推送 |

进度正则默认值(面向评测日志):

- `LAWN_TOTAL_RE` = `(\d+)\s+prompts` —— 捕获总数
- `LAWN_DONE_RE` = `\d+\s+steps,\s*mean_accept` —— 每出现一次算完成一条
- `LAWN_DUR_RE` = `([\d.]+)s,\s*[\d.]+\s*tok/s` —— 捕获单条耗时(秒)
