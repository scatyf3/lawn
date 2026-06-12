"""白名单指令分发。对应原 poll_commands_tg.sh 的 handle()。"""
import glob
import os
import re
import subprocess

from . import config, projects, status

JOBID_RE = re.compile(r"^[0-9_]+$")

HELP = """指令:
!status | !jobs   状态
!tail <jobid>     作业日志
!projects         项目清单
!use <项目>       切换项目
!where            当前项目
!ai <自然语言>    让 agent 改代码
!help"""


def active_project():
    """当前项目:active 文件优先,否则取 projects 配置里的第一个;都没有返回 None。"""
    try:
        with open(config.ACTIVE_FILE, encoding="utf-8") as fh:
            name = fh.read().strip()
            if name:
                return name
    except OSError:
        pass
    names = projects.names()
    return names[0] if names else None


def _set_active(name):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(config.ACTIVE_FILE, "w", encoding="utf-8") as fh:
        fh.write(name)


def _find_log(repo, rest):
    cands = glob.glob(os.path.join(repo, "logs", f"*{rest}*")) + \
            glob.glob(os.path.join(repo, "logs", f"slurm-{rest}*"))
    cands = [c for c in cands if os.path.isfile(c)]
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def _tail(path, n=40):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-n:]).rstrip("\n")
    except OSError:
        return ""


def _launch_ai(chat, proj, wd, instr):
    """后台跑 ai_agent.sh,脱离当前进程(setsid 等价),不阻塞轮询。"""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    launch_log = open(os.path.join(config.STATE_DIR, "ai_agent_launch.log"), "a")  # noqa: SIM115
    subprocess.Popen(
        ["bash", config.AI_AGENT_SH, chat, proj, wd, instr],
        stdout=launch_log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def handle(chat, text, tg):
    """处理一条 ! 指令,通过 tg 回复到 chat。"""
    cmd, _, rest = text.partition(" ")
    rest = rest.strip()

    if cmd in ("!status", "!jobs"):
        # results 取当前项目的基目录;Slurm/GPU 是全局的,repo 为 None 也能报
        tg.send(chat, status.build_report(projects.repo_path(active_project() or "")),
                parse_mode="HTML")

    elif cmd == "!tail":
        if not JOBID_RE.match(rest):
            tg.send(chat, "用法: !tail <jobid>")
            return
        repo = projects.repo_path(active_project() or "")
        if not repo:
            tg.send(chat, "当前项目无 path,无法找日志")
            return
        log = _find_log(repo, rest)
        if not log:
            tg.send(chat, f"找不到含 '{rest}' 的日志")
            return
        tg.send(chat, f"{os.path.basename(log)} 最后 40 行:\n{_tail(log)}")

    elif cmd == "!projects":
        cur = active_project()
        lines = "\n".join(("★ " + n if n == cur else "  " + n) for n in projects.names())
        tg.send(chat, f"可操作项目(★=当前):\n{lines}\n切换: !use <项目>")

    elif cmd == "!use":
        if not projects.exists(rest):
            tg.send(chat, f"未知项目: '{rest}'。可选: {' '.join(projects.names())}")
            return
        wd, err = projects.workdir(rest)
        if err:
            tg.send(chat, f"切换失败: {err}")
            return
        _set_active(rest)
        tg.send(chat, f"✅ 当前项目: {rest}\n工作目录: {wd}")

    elif cmd == "!where":
        cur = active_project()
        wd, _ = projects.workdir(cur)
        tg.send(chat, f"当前项目: {cur}\n工作目录: {wd or '?'}")

    elif cmd == "!ai":
        if not rest:
            tg.send(chat, "用法: !ai <要做的事>")
            return
        cur = active_project()
        wd, err = projects.workdir(cur)
        if err:
            tg.send(chat, f"无法解析项目 '{cur}': {err}")
            return
        _launch_ai(chat, cur, wd, rest)

    elif cmd == "!help":
        tg.send(chat, HELP)
    # 其余非白名单指令:忽略
