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
    experiments.py       # sbatch 实验登记:解析 #SBATCH/#EXP,写实验文档
    watch.py             # 实验看护:刷新状态 + 汇总 + 异常自动修
    notify.py / report.py# notify / report 的 main
  bin/
    lawn-poll            # cron 入口:轮询并执行指令
    lawn-report          # cron 入口:状态汇总 + 推送
    lawn-watch           # cron 入口(每 0.5hr):实验看护
    lawn-notify          # CLI:通用 Telegram 推送
    sbatch               # sbatch 包装器(装到 ~/.local/bin 拦截所有提交)
    lawn-sbatch          # 管理包装器:install/uninstall/status/template
    lawn-sbatch-register # 包装器内部调用:落盘实验文档
  templates/
    experiment.sbatch    # 实验模板:GPU/账号 + #EXP 目标/配置
  ai_agent.sh            # 保留 bash:后台跑 Claude Code 无头模式(锁 + 隔离 + 会话)
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

   也可(默认即启用)靠**动态发现**免手写清单:扫描 `LAWN_SCAN_ROOTS` 里各根目录
   的直接子目录,把最近 `LAWN_SCAN_DAYS`(默认 30)天内有 git 提交的仓库自动
   登记(name=目录名,mode=`LAWN_SCAN_MODE`,默认 `worktree`)。`LAWN_SCAN_ROOTS`
   缺省 = lawn 仓库的父目录(即放各项目的公共目录),设为空串可关闭。静态 conf 同名优先。
   只认 `.git` 为真实目录的仓库,故自动排除 `<repo>_ai` 隔离工作区与 submodule。
   上面的静态清单文件可完全不建,纯靠发现运行。

3. 可选环境变量：
   - `LAWN_ENV` / `LAWN_PROJECTS` / `LAWN_STATE_DIR` — 覆盖配置 / 状态目录路径。
   - `LAWN_SCAN_ROOTS`(冒号分隔) / `LAWN_SCAN_DAYS` / `LAWN_SCAN_MODE` — 动态项目发现(见上)。
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
!where            显示当前项目、工作目录、当前会话状态
!ai <自然语言>    在当前项目里跑 Claude Code 改代码(后台执行,续接该项目会话)
!reset            重置当前项目会话,下次 !ai 开新对话
!help             指令列表
```

完整命令参考(含命令行入口、例子、进度/ETA 说明)见 [COMMANDS.md](COMMANDS.md)。

## 实验登记与看护

让**任何终端 / agent** 的 `sbatch` 提交都自动登记成一份实验文档,并每 0.5hr 巡检状态。

1. **装包装器**(在 `~/.local/bin/sbatch` 建链接,盖过真 sbatch):

   ```bash
   bin/lawn-sbatch install      # status 查看 / uninstall 卸载 / template 打印模板
   ```

   之后每次 `sbatch` 都会:先原样跑真 sbatch,提交成功再把实验写到
   `~/.cache/lawn/experiments/<jobid>.json`(+ 人读 `.md`)。**故障安全**:登记相关的
   任何错误都不影响提交,退出码原样透传;`LAWN_NO_HOOK=1 sbatch ...` 可临时绕过。

2. **在 sbatch 里写实验元信息**(见 `templates/experiment.sbatch`)。除 `#SBATCH`
   的 GPU/账号外,加几行 `#EXP`:

   ```bash
   #EXP name: 投机解码-温度扫描
   #EXP goal: 验证 draft 温度 0.7 把接受长度提到 3.2 以上
   #EXP config: model=llama3-8b temp=0.7 bs=16 dataset=mtbench
   #EXP smoke: false          # true=冒烟测试,不进看护/不自动修
   ```

   **smoke 判定**优先级:`LAWN_SMOKE=1 sbatch ...` > `#EXP smoke:` > 启发式(名字含
   smoke/test/debug 或 `--time≤15min`)。smoke 实验照样登记,但不巡检、不自动修。

3. **看护**(`bin/lawn-watch`,挂 cron 每 0.5hr):刷新每个实验的 Slurm 状态、对
   运行中的非 smoke 实验开个小 agent 判断是否正常,推一份**以实验为单位**的汇总到
   Telegram —— 每个实验下面列出它的 squeue 子任务(数组作业各 task)的
   **进度 N/总数 + ETA**(复用 `progress.py` 的日志正则),再附「未登记作业」与 GPU,
   等于把原来 `lawn-report` 的进度展示并了进来(可据此不再单挂 report)。对
   **非 smoke 且判为异常**(卡住/报错,或以失败态结束)的实验**尝试自动修**
   (诊断 → 可 scancel+改+重投),默认**每个实验最多 1 次**,每步通知。
   `LAWN_FIX_MAX` 调次数,`LAWN_AUTOFIX=0` 整体关闭自动修(只通知)。

## cron 示例

cron 不走 login shell，务必用绝对路径的系统 python 入口：

```cron
* * * * * /scratch/yf3005/lawn/bin/lawn-poll
*/30 * * * * /scratch/yf3005/lawn/bin/lawn-report
*/30 * * * * /scratch/yf3005/lawn/bin/lawn-watch
```
