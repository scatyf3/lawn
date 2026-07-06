"""白名单指令分发。对应原 poll_commands_tg.sh 的 handle()。"""
import glob
import html
import os
import re
import subprocess

from . import config, projects, status

JOBID_RE = re.compile(r"^[0-9_]+$")

# HTML 格式(send 时带 parse_mode="HTML")。占位符尖括号要转义成 &lt;/&gt;。
HELP = (
    "<b>lawn 指令</b>\n"
    "<code>!status</code> · <code>!jobs</code>  状态:Slurm 作业(含进度/ETA)+ GPU + 最新结果\n"
    "<code>!tail &lt;jobid&gt;</code>  指定作业日志最后 40 行\n"
    "<code>!projects</code>  列出可操作项目(★=当前)\n"
    "<code>!use &lt;项目&gt;</code>  切换当前项目(worktree 模式按需创建隔离工作区)\n"
    "<code>!where</code>  显示当前项目、工作目录、当前会话号\n"
    "<code>!ai &lt;自然语言&gt;</code>  在当前项目后台跑 Claude Code 改代码(续接该项目会话)\n"
    "<code>!reset</code>  重置当前项目会话,下次 !ai 开新对话\n"
    "<code>!help</code>  显示本帮助\n"
    "\n"
    "<b>关于会话</b>\n"
    "· 每个项目一段持续会话:连续 <code>!ai</code> 会记得上文,不必重复交代。\n"
    "· <code>!use</code> 切项目 = 换成那个项目自己的独立会话。\n"
    "· 想开新对话就 <code>!reset</code>;会话号(前 8 位)看 <code>!where</code>,"
    "或每条 <code>!ai</code> 开头也会显示(🆕新 / 🧵续)。"
)


def _session_file(name):
    return os.path.join(config.SESSION_DIR, f"{name}.id")


def _session_short(name):
    """当前项目会话 id 的前 8 位;无则 None。"""
    try:
        with open(_session_file(name), encoding="utf-8") as fh:
            sid = fh.read().strip()
            return sid[:8] if sid else None
    except OSError:
        return None


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
        sid = _session_short(cur or "")
        sess = f"会话: {sid}(!ai 续接)" if sid else "会话: 无(下次 !ai 新建)"
        tg.send(chat, f"当前项目: {cur}\n工作目录: {wd or '?'}\n{sess}")

    elif cmd == "!reset":
        cur = active_project()
        if not cur:
            tg.send(chat, "没有当前项目")
            return
        existed = _session_short(cur) is not None
        try:
            os.remove(_session_file(cur))
        except OSError:
            pass
        tail = "" if existed else "(本就没有会话历史)"
        tg.send(chat, f"🧹 已重置 [{cur}] 会话{tail}\n下次 !ai 将开新对话")

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
        tg.send(chat, HELP, parse_mode="HTML")

    else:  # 未知 ! 指令:回帮助(poll 已确保只有 ! 开头的消息进来)
        tg.send(chat, f"未知指令 <code>{html.escape(cmd)}</code>\n\n{HELP}",
                parse_mode="HTML")
