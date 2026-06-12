"""汇总 Slurm 作业 / GPU / 最新实验结果。对应原 report_status.sh。"""
import getpass
import glob
import json
import os
import shutil
import subprocess

from . import config


def _run(cmd):
    """跑命令取 stdout;任何失败都吞掉返回空串(尽力而为)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return r.stdout if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _round2(x):
    return round(x * 100) / 100 if isinstance(x, (int, float)) and not isinstance(x, bool) else "?"


def _slurm():
    if not shutil.which("squeue"):
        return ""
    jobs = _run(["squeue", "-u", getpass.getuser(), "-h",
                 "-o", "%.10i %.9P %.20j %.2t %.10M"]).rstrip("\n")
    if jobs:
        return f"**Slurm 作业**\n```\n{jobs}\n```\n"
    return "**Slurm 作业**: 当前无运行/排队作业\n"


def _gpu():
    if not shutil.which("nvidia-smi"):
        return ""
    raw = _run(["nvidia-smi",
                "--query-gpu=index,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits"])
    lines = []
    for ln in raw.splitlines():
        f = [c.strip() for c in ln.split(",")]
        if len(f) >= 4:
            lines.append(f"GPU{f[0]}: {f[1]}% {f[2]}/{f[3]}MiB")
    return f"**GPU**\n```\n" + "\n".join(lines) + "\n```\n" if lines else ""


def _latest_results(repo):
    dirs = sorted(glob.glob(os.path.join(repo, "results", "*", "")),
                  key=lambda p: os.path.getmtime(p), reverse=True)
    if not dirs:
        return ""
    latest = dirs[0]
    out = f"**最新结果** `{os.path.basename(latest.rstrip('/'))}`\n```\n"
    for f in sorted(glob.glob(os.path.join(latest, "*.jsonl"))):
        tps = acc = "?"
        try:
            with open(f, encoding="utf-8") as fh:
                last = fh.readlines()[-1]
            d = json.loads(last)
            tps, acc = _round2(d.get("tokens_per_s")), _round2(d.get("accept_length"))
        except Exception:  # noqa: BLE001
            pass
        name = os.path.basename(f)[:-len(".jsonl")]
        out += f"{name}: tps={tps}  accept={acc}\n"
    return out + "```\n"


def build_report(repo=None):
    repo = repo or config.EAGLE_REPO
    out = _slurm() + _gpu() + _latest_results(repo)
    return out if out else "(没有可报告的内容)"
