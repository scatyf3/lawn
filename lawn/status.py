"""汇总 Slurm 作业 / GPU / 最新实验结果,产出适配 Telegram 的 HTML。

输出用 HTML parse_mode 而非 Markdown:内容里大量 `_ . [ ] -`(文件名/作业号),
MarkdownV2 要逐字符转义太脆;HTML 只需转义 `& < >`,稳得多。
"""
import getpass
import glob
import html
import json
import os
import shutil
import socket
import subprocess
import time

from . import progress

# cron / 无头环境不走 login shell,PATH 常只有 /usr/bin:/bin,缺 Slurm/CUDA 的 bin 目录,
# 导致 which("squeue") 失败、Slurm 段被整段丢掉。这里在 PATH 之外再兜底找这些常见目录。
# 需要时用 LAWN_EXTRA_PATH(冒号分隔)追加。
_EXTRA_BIN = [
    p for p in (
        os.environ.get("LAWN_EXTRA_PATH", "").split(os.pathsep)
        + ["/opt/slurm/bin", "/usr/local/bin", "/cm/shared/apps/slurm/current/bin"]
    ) if p
]


def _which(name):
    """先按 PATH 找,找不到再到 _EXTRA_BIN 里兜底,返回绝对路径或 None。"""
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_BIN:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _run(cmd):
    """跑命令取 stdout;任何失败都吞掉返回空串(尽力而为)。
    cmd[0] 是裸命令名时,先用 _which 解析成绝对路径,避免 PATH 缺目录跑不起来。"""
    cmd = list(cmd)
    if cmd and os.sep not in cmd[0]:
        cmd[0] = _which(cmd[0]) or cmd[0]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return r.stdout if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _round2(x):
    return round(x * 100) / 100 if isinstance(x, (int, float)) and not isinstance(x, bool) else "?"


def _table(headers, rows):
    """把行对齐成等宽表格(纯文本;放进 <pre> 前由 _pre 负责转义)。"""
    grid = [list(map(str, headers))] + [list(map(str, r)) for r in rows]
    widths = [max(len(row[i]) for row in grid) for i in range(len(headers))]
    return "\n".join(
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in grid
    )


def _pre(table):
    return f"<pre>{html.escape(table)}</pre>"


def _header():
    host = socket.gethostname().split(".")[0]
    return f"🖥 <b>{html.escape(host)}</b> · {time.strftime('%m-%d %H:%M')}"


def _slurm(repo=None):
    if not _which("squeue"):
        return ""
    # 用 | 分隔便于解析:JOBID|NAME|STATE|TIME
    raw = _run(["squeue", "-u", getpass.getuser(), "-h", "-o", "%i|%j|%t|%M"]).strip()
    if not raw:
        return "📋 <b>Slurm</b> · 无作业"
    rows = [ln.split("|") for ln in raw.splitlines() if ln]
    running = sum(1 for r in rows if r[2] == "R")
    pending = sum(1 for r in rows if r[2] == "PD")
    other = len(rows) - running - pending
    bits = []
    if running:
        bits.append(f"{running} ▶ running")
    if pending:
        bits.append(f"{pending} ⏳ pending")
    if other:
        bits.append(f"{other} · other")
    summary = " · ".join(bits) or f"{len(rows)} 作业"
    table_rows = []
    for jobid, _name, st, tm in (r[:4] for r in rows):
        prog = eta = "-"
        if st == "R":                          # 只对运行中的作业读 log 估进度
            p = progress.job_progress(repo, jobid)
            if p:
                done, total, eta = p
                prog = f"{done}/{total}" if total else str(done)
        table_rows.append([jobid, st, prog, eta, tm])
    table = _table(["JOBID", "ST", "PROG", "ETA", "TIME"], table_rows)
    return f"📋 <b>Slurm</b> · {summary}\n{_pre(table)}"


def _gpu():
    if not _which("nvidia-smi"):
        return ""
    raw = _run(["nvidia-smi",
                "--query-gpu=index,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits"])
    rows = []
    for ln in raw.splitlines():
        f = [c.strip() for c in ln.split(",")]
        if len(f) >= 4:
            rows.append([f"GPU{f[0]}", f"{f[1]}%", f"{f[2]}/{f[3]} MiB"])
    if not rows:
        return ""
    return f"🎮 <b>GPU</b>\n{_pre(_table(['GPU', 'UTIL', 'MEM'], rows))}"


def _latest_results(repo):
    if not repo:
        return ""
    dirs = sorted(glob.glob(os.path.join(repo, "results", "*", "")),
                  key=lambda p: os.path.getmtime(p), reverse=True)
    if not dirs:
        return ""
    name = os.path.basename(dirs[0].rstrip("/"))
    head = f"📊 <b>最新结果</b> · <code>{html.escape(name)}</code>"
    rows = []
    for f in sorted(glob.glob(os.path.join(dirs[0], "*.jsonl"))):
        tps = acc = "?"
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.loads(fh.readlines()[-1])
            tps, acc = _round2(d.get("tokens_per_s")), _round2(d.get("accept_length"))
        except Exception:  # noqa: BLE001
            pass
        rows.append([os.path.basename(f)[:-len(".jsonl")][:30], str(tps), str(acc)])
    if not rows:
        return head
    return f"{head}\n{_pre(_table(['FILE', 'TPS', 'ACCEPT'], rows))}"


def build_report(repo=None):
    """汇总状态(HTML)。repo 为当前项目基目录(取最新 results);None 时只报 Slurm/GPU。"""
    body = "\n\n".join(p for p in (_slurm(repo), _gpu(), _latest_results(repo)) if p)
    return f"{_header()}\n\n{body}" if body else f"{_header()}\n\n（没有可报告的内容）"
