"""CPU 看护节点自愈:检查上次申请的 CPU-only 看护作业是否还在,不在就重新
sbatch 一个,节点里跑 bin/lawn-watch 的紧凑轮询循环。

动机:login node 常年跑在 k8s pod 里,crontab 是本地状态,pod 一重建就整个
丢光(见 2026-07 那次事故:torch-login-a-2 重建,lawn 挂的三条 cron 全部消失,
且没人自愈)。把真正高频的实验轮询挪到一个持久的 Slurm CPU 分配上 ——
只要 Slurm 控制面没重启,这个作业的生命周期就跟 login node pod 无关,能撑到
walltime 上限(默认 cpu_short 这个 QoS 的 6h 上限)。

本脚本自己仍然要靠 login node cron 调(见 bin/lawn-cpu-watch),但足够轻量:
一次 squeue + 顶多一次 sbatch。即使 crontab 又被清空,顶多是错过一次续期,
不影响已经在跑的看护节点在其 walltime 内继续工作 —— 用高频率(建议 */10~15
分钟)换来即使自愈本身偶尔断线也不会立刻失联。

环境变量(建议写在 crontab 该行前面,而不是 ~/.config/lawn.env —— cron 每次
都会重新读 crontab,行前变量天然生效;换项目/换集群时对应改这行即可):
  LAWN_CPU_ACCOUNT    必填,Slurm account(不填直接报错,不瞎猜着提交)
  LAWN_CPU_PARTITION  默认 cpu_short
  LAWN_CPU_TIME       默认 05:45:00(留量,别正好顶到 QoS 上限被拒)
  LAWN_CPU_INTERVAL   节点里两轮 lawn-watch 之间的 sleep 秒数,默认 300
  LAWN_CPU_JOB_NAME   默认 lawn-cpu-watch(squeue -J 用来识别是不是这个作业)
"""
import os
import subprocess

from . import config, experiments, status
from .telegram import Telegram

STATE_FILE = os.path.join(config.STATE_DIR, "cpu_watch_job")
LOG_FILE = os.path.join(config.STATE_DIR, "cpu_watch.log")

ACCOUNT = os.environ.get("LAWN_CPU_ACCOUNT", "")
PARTITION = os.environ.get("LAWN_CPU_PARTITION", "cpu_short")
TIME_LIMIT = os.environ.get("LAWN_CPU_TIME", "05:45:00")
INTERVAL = int(os.environ.get("LAWN_CPU_INTERVAL", "300"))
JOB_NAME = os.environ.get("LAWN_CPU_JOB_NAME", "lawn-cpu-watch")

_ALIVE = {"pending", "running", "configuring", "completing", "suspended"}


def _read_jobid():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _write_jobid(jobid):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(jobid)
    os.replace(tmp, STATE_FILE)


def _is_alive(jobid):
    """squeue 只列活跃作业;查不到(已终止/不存在)一律当作不在了。"""
    if not jobid or not status._which("squeue"):
        return False
    raw = status._run(["squeue", "-h", "-j", str(jobid), "-o", "%T"])
    states = {ln.strip().lower() for ln in raw.splitlines() if ln.strip()}
    return bool(states & _ALIVE)


def _wrap_cmd():
    """看护节点里跑的循环:每 INTERVAL 秒一轮 lawn-watch,踩着 walltime 前留 2
    分钟余量用 timeout 自己收尾,不指望 Slurm 的 SIGKILL 掐在干净的地方。"""
    total_min = experiments._minutes(TIME_LIMIT) or 0
    budget = max(total_min * 60 - 120, INTERVAL)
    watch_bin = os.path.join(config.ROOT, "bin", "lawn-watch")
    return (
        f"timeout {budget} bash -c "
        f"'while true; do /usr/bin/python3 {watch_bin}; sleep {INTERVAL}; done'"
    )


def _submit():
    """提交看护作业,返回 (jobid_or_None, err)。故障安全:任何一步失败都回错误串,不抛。"""
    sbatch = status._which("sbatch")
    if not sbatch:
        return None, "找不到 sbatch"
    if not ACCOUNT:
        return None, "缺 LAWN_CPU_ACCOUNT,没提交"
    os.makedirs(config.STATE_DIR, exist_ok=True)
    cmd = [sbatch, "-A", ACCOUNT, "-p", PARTITION, "-t", TIME_LIMIT,
           "-J", JOB_NAME, "-o", LOG_FILE, "--wrap", _wrap_cmd()]
    # LAWN_NO_HOOK=1:绕过可能装在 PATH 上的 sbatch 包装器,别把这个看护作业自己
    # 登记成一份"实验"(那样它会被自己的 watch 循环巡检到,容易递归出怪状态)。
    env = dict(os.environ, LAWN_NO_HOOK="1")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    except Exception as e:  # noqa: BLE001
        return None, f"sbatch 调用异常: {e}"
    if r.returncode != 0:
        return None, (r.stderr or r.stdout or "sbatch 失败").strip()
    for tok in r.stdout.split():
        if tok.isdigit():
            return tok, ""
    return None, f"没解析出 jobid: {r.stdout.strip()}"


def main():
    try:
        st = config.Settings()
    except FileNotFoundError as e:
        print(e)
        return 1
    st.require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")

    jobid = _read_jobid()
    if _is_alive(jobid):
        return 0                                    # 还在跑,不打扰

    new_jobid, err = _submit()
    tg = Telegram(st.bot_token)
    if new_jobid:
        _write_jobid(new_jobid)
        why = "首次申请" if not jobid else f"上个作业 {jobid} 已不在"
        tg.send(st.chat_id,
                f"🌱 CPU 看护节点{why},已重新申请:job {new_jobid}"
                f"({ACCOUNT}/{PARTITION}, {TIME_LIMIT})", parse_mode="HTML")
        return 0
    tg.send(st.chat_id, f"❌ CPU 看护节点重新申请失败: {err}", parse_mode="HTML")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
