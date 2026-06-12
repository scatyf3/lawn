"""从作业日志估算进度与 ETA。纯正则,不调 AI(日志格式规整,AI 又慢又贵)。

把 squeue 的作业号映射到 logs/ 下同名日志(文件名内嵌 jobid),扫一遍取:
  总数  = 第一处 LAWN_TOTAL_RE 捕获组
  完成  = LAWN_DONE_RE 出现次数
  每条耗时 = LAWN_DUR_RE 捕获组(秒) → 平均 → ETA = 剩余 × 平均

默认正则面向「评测类」日志(N prompts / 完成行含 'steps, mean_accept' /
每条 'Xs, Y tok/s')。换项目时用环境变量覆盖即可,保持 project-driven:
  LAWN_TOTAL_RE  默认 (\\d+)\\s+prompts
  LAWN_DONE_RE   默认 \\d+\\s+steps,\\s*mean_accept
  LAWN_DUR_RE    默认 ([\\d.]+)s,\\s*[\\d.]+\\s*tok/s
"""
import glob
import os
import re

_TOTAL_RE = re.compile(os.environ.get("LAWN_TOTAL_RE", r"(\d+)\s+prompts"))
_DONE_RE = re.compile(os.environ.get("LAWN_DONE_RE", r"\d+\s+steps,\s*mean_accept"))
_DUR_RE = re.compile(os.environ.get("LAWN_DUR_RE", r"([\d.]+)s,\s*[\d.]+\s*tok/s"))


def _find_log(repo, jobid):
    """logs/ 下文件名以该 jobid 结尾的日志(如 *_10661257_5.log),取最新。"""
    cands = [c for c in glob.glob(os.path.join(repo, "logs", f"*{jobid}.log"))
             if os.path.isfile(c)]
    return max(cands, key=os.path.getmtime) if cands else None


def _fmt_eta(sec):
    if sec is None:
        return "-"
    sec = int(sec)
    if sec < 60:
        return "~<1m"
    h, m = sec // 3600, (sec % 3600) // 60
    return f"~{h}h{m:02d}m" if h else f"~{m}m"


def job_progress(repo, jobid):
    """返回 (done, total, eta_str);拿不到 log 或解析不出则返回 None。"""
    if not repo:
        return None
    log = _find_log(repo, jobid)
    if not log:
        return None
    try:
        with open(log, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    m = _TOTAL_RE.search(text)
    total = int(m.group(1)) if m else None
    done = len(_DONE_RE.findall(text))
    durs = [float(x) for x in _DUR_RE.findall(text)]
    eta = None
    if total and durs and done < total:
        eta = (total - done) * (sum(durs) / len(durs))
    return done, total, _fmt_eta(eta)
