"""实验看护:每 0.5hr 由 cron 调用一次(bin/lawn-watch)。

对每个已登记、未归档的实验:
  1. 用 squeue / sacct 刷新状态,检测状态跃迁;
  2. 运行中且非 smoke 的,收集近期日志,交小 agent 判断是否正常;
  3. 推一份全量汇总到 Telegram;
  4. 非 smoke 且判为异常(或作业以失败态结束)的,尝试自动修(默认最多 1 次)。

自动修会 scancel + 改 + 重投真实作业,故:仅非 smoke、判定确凿、次数封顶(LAWN_FIX_MAX,
默认 1),每次都通知;LAWN_AUTOFIX=0 可整体关闭。smoke 实验只登记、从不自动修。
"""
import getpass
import html
import os
import re
import subprocess
import sys
import time

from . import config, experiments, status
from .telegram import Telegram

TAIL_LINES = int(os.environ.get("LAWN_WATCH_TAIL", "60"))
FIX_MAX = int(os.environ.get("LAWN_FIX_MAX", "1"))
AUTOFIX_ON = os.environ.get("LAWN_AUTOFIX", "1") != "0"

_QST = {"R": "running", "PD": "pending", "CG": "completing",
        "CF": "configuring", "S": "suspended"}
_TERMINAL = {"completed", "failed", "timeout", "cancelled", "out_of_memory",
             "node_fail", "deadline", "boot_fail", "preempted", "gone"}
_BAD_END = {"failed", "timeout", "out_of_memory", "node_fail", "deadline", "boot_fail"}


# ── Slurm 探测(复用 status 的 PATH 兜底) ──────────────────────────────

def _squeue():
    """jobid -> {'st': 归一状态, 'time': 已运行时长}。squeue 不可用则空。"""
    if not status._which("squeue"):
        return {}
    raw = status._run(["squeue", "-u", getpass.getuser(), "-h", "-o", "%i|%t|%M"])
    out = {}
    for ln in raw.splitlines():
        p = [x.strip() for x in ln.split("|")]
        if len(p) >= 3 and p[0]:
            out[p[0]] = {"st": _QST.get(p[1], p[1].lower()), "time": p[2]}
    return out


def _sacct_state(jid):
    """作业最终态(COMPLETED/FAILED/...)小写;sacct 不可用或查不到返回 ''。"""
    if not status._which("sacct"):
        return ""
    raw = status._run(["sacct", "-j", str(jid), "-n", "-X", "-o", "State"])
    for ln in raw.splitlines():
        s = ln.strip()
        if s:
            return s.split()[0].lower()          # "CANCELLED by 123" -> cancelled
    return ""


# ── 日志定位与读取 ────────────────────────────────────────────────────

def _resolve_output(rec):
    """把 #SBATCH --output 里的 %j/%x 展开成真实日志路径;拿不到返回 None。"""
    out = rec.get("output") or ""
    if not out:
        return None
    out = (out.replace("%j", rec["jobid"])
              .replace("%x", rec.get("job_name") or "")
              .replace("%u", getpass.getuser()))
    if "%" in out:                                # 还有没法解析的转义(如 %N)
        return None
    if not os.path.isabs(out):
        out = os.path.join(rec.get("submit_cwd") or ".", out)
    return out if os.path.isfile(out) else None


def _log_tail(path, n=TAIL_LINES):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-n:]).rstrip("\n")
    except OSError:
        return ""


# ── 健康判断(小 agent) ───────────────────────────────────────────────

def _claude_bin():
    for p in (os.environ.get("CLAUDE_BIN"),
              os.path.expanduser("~/.local/bin/claude"),
              os.path.expanduser("~/.claude/local/claude")):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


_VERDICT_RE = re.compile(r"^\s*(\S+)\s+(OK|BAD)\b[ \t:-]*(.*)$", re.I)


def _assess(cases):
    """cases: list[(rec, log_tail)]。返回 {jobid: (ok_bool_or_None, reason)}。

    None = 无法判断(claude 不可用/超时/没回话)——上层据此不自动动手。
    """
    verdicts = {c[0]["jobid"]: (None, "未评估") for c in cases}
    if not cases:
        return verdicts
    claude = _claude_bin()
    if not claude:
        return {c[0]["jobid"]: (None, "claude 不可用") for c in cases}
    blocks = []
    for rec, tail in cases:
        blocks.append(
            f"### 作业 {rec['jobid']}\n"
            f"目标: {rec.get('exp_goal') or '(未写)'}\n"
            f"配置: {rec.get('exp_config') or '(未写)'}\n"
            f"已运行: {rec.get('elapsed') or '?'}\n"
            f"近期日志(末 {TAIL_LINES} 行):\n{tail or '(无日志/未找到)'}\n"
        )
    prompt = (
        "你在巡检若干正在运行的 Slurm 训练/评测作业,判断每个是否『正常推进』。\n"
        "异常信号:报错/Traceback、CUDA/OOM、长时间无新输出、loss 变 NaN、卡在同一步等。\n"
        "只输出结论,每个作业一行,严格格式:\n"
        "<jobid> <OK|BAD> <不超过20字的中文原因>\n"
        "无法判断就写 OK。不要输出别的。\n\n" + "\n".join(blocks)
    )
    try:
        r = subprocess.run([claude, "-p", prompt, "--dangerously-skip-permissions"],
                           capture_output=True, text=True, timeout=240)
        text = r.stdout if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return {c[0]["jobid"]: (None, "评估超时") for c in cases}
    for ln in text.splitlines():
        m = _VERDICT_RE.match(ln)
        if m and m.group(1) in verdicts:
            verdicts[m.group(1)] = (m.group(2).upper() == "OK", m.group(3).strip() or "-")
    return verdicts


# ── 自动修 ────────────────────────────────────────────────────────────

def _launch_fix(chat, rec, reason, tail):
    """后台起一个修复 agent(复用 ai_agent.sh:锁 + 隔离 + 会话)。"""
    jid = rec["jobid"]
    wd = rec.get("submit_cwd") or ""
    if not os.path.isdir(wd):
        return False
    instr = (
        f"Slurm 实验 {jid} 疑似异常({reason})。\n"
        f"实验目标: {rec.get('exp_goal') or '(未写)'}\n"
        f"配置: {rec.get('exp_config') or '(未写)'}\n"
        f"提交脚本: {rec.get('script') or '(--wrap)'}\n"
        f"当前状态: {rec.get('state')}\n"
        f"近期日志(末 {TAIL_LINES} 行):\n{tail or '(未找到日志)'}\n\n"
        "请诊断根因。若确有必要且你有把握,可以 `scancel " + jid + "` 取消当前作业,"
        "修改脚本/代码后重新 `sbatch` 提交(重投会被自动重新登记)。"
        "只动这个实验相关的东西,别碰无关代码。完成后用中文说明:你判断的问题、改了什么、是否重投。"
        "如果判断其实正常或无从下手,就说明原因、不要瞎改。"
    )
    os.makedirs(config.STATE_DIR, exist_ok=True)
    log = open(os.path.join(config.STATE_DIR, "watch_fix.log"), "a")  # noqa: SIM115
    subprocess.Popen(["bash", config.AI_AGENT_SH, chat, f"exp-{jid}", wd, instr],
                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return True


# ── 主流程 ────────────────────────────────────────────────────────────

def _short(s, n):
    s = s or ""
    return s if len(s) <= n else s[:n - 1] + "…"


def _refresh(rec, sq):
    """就地更新 rec 的 state/elapsed,返回 (旧态, 新态)。"""
    jid, prev = rec["jobid"], rec.get("state")
    if jid in sq:
        new, rec["elapsed"] = sq[jid]["st"], sq[jid]["time"]
    else:
        new = _sacct_state(jid) or "gone"
    rec["state"] = new
    rec["last_check"] = int(time.time())
    return prev, new


def run():
    """跑一轮看护(刷新状态 + 评估健康)。

    无活跃实验返回 '';否则返回 (报告HTML, recs, fix_targets, verdicts),
    由 main 负责自动修、归档、写回与推送。
    """
    recs = [r for r in experiments.all_records() if not r.get("archived")]
    if not recs:
        return ""
    sq = _squeue()

    events, fix_notes = [], []
    for r in recs:
        prev, new = _refresh(r, sq)
        if new != prev:
            events.append(f"{r['jobid']} {prev}→{new}"
                          + (" 🔬" if r.get("smoke") else ""))

    # 健康评估:仅运行中、非 smoke
    cases = []
    for r in recs:
        if r.get("state") == "running" and not r.get("smoke"):
            cases.append((r, _log_tail(_resolve_output(r) or "")))
    verdicts = _assess(cases)

    # 自动修候选:非 smoke,且(以坏态结束 或 运行中被判 BAD)
    fix_targets = []
    for r in recs:
        if r.get("smoke"):
            continue
        st = r.get("state")
        ok, reason = verdicts.get(r["jobid"], (None, ""))
        bad = (st in _BAD_END) or (st == "running" and ok is False)
        if bad:
            fix_targets.append((r, reason or st))

    # 组装每行的健康列
    def health_cell(r):
        if r.get("smoke"):
            return "🔬smoke"
        if r.get("state") != "running":
            return "-"
        ok, _ = verdicts.get(r["jobid"], (None, ""))
        return {True: "✓", False: "✗", None: "?"}[ok]

    rows = [[r["jobid"],
             _short(r.get("exp_name") or r.get("job_name") or "-", 18),
             r.get("state") or "?",
             r.get("elapsed") or "-",
             health_cell(r),
             str(r.get("fix_attempts", 0))]
            for r in recs]
    table = status._pre(status._table(
        ["JOBID", "NAME", "STATE", "TIME", "OK", "FIX"], rows))

    host = status.socket.gethostname().split(".")[0]
    parts = [f"🔭 <b>实验看护</b> · {html.escape(host)} · {time.strftime('%m-%d %H:%M')}",
             table]
    if events:
        parts.append("<b>状态变化</b>\n" + html.escape("\n".join(events)))

    return "\n\n".join(parts), recs, fix_targets, verdicts


def main():
    try:
        st = config.Settings()
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    st.require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")

    packed = run()
    if not packed:
        return 0                                   # 没有活跃实验,不打扰
    report, recs, fix_targets, verdicts = packed
    chat = st.chat_id

    # 自动修 + 归档,写回记录
    fix_lines = []
    for r, reason in fix_targets:
        n = r.get("fix_attempts", 0)
        if AUTOFIX_ON and n < FIX_MAX:
            tail = _log_tail(_resolve_output(r) or "")
            r["fix_attempts"] = n + 1
            experiments.save(r)                    # 先记次数,避免崩溃后重复触发
            if _launch_fix(chat, r, reason, tail):
                fix_lines.append(f"🔧 {r['jobid']} 自动修第 {n + 1}/{FIX_MAX} 次({_short(reason, 24)})")
            else:
                fix_lines.append(f"⚠️ {r['jobid']} 想修但目录不存在,跳过")
        elif n >= FIX_MAX:
            fix_lines.append(f"🚨 {r['jobid']} 仍异常,已达自动修上限({FIX_MAX}),需人工")

    for r in recs:
        if r.get("state") in _TERMINAL:
            r["archived"] = True
        experiments.save(r)

    if fix_lines:
        report += "\n\n<b>自动修</b>\n" + html.escape("\n".join(fix_lines))

    Telegram(st.bot_token).send(chat, report, parse_mode="HTML")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
