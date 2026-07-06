"""实验登记:解析 sbatch 脚本 + #EXP 指令,为每个作业写一份实验文档。

由 sbatch 包装器(bin/sbatch)在提交成功后调用,也供 watch 读取/更新。
纯 stdlib,不依赖 Telegram / env 文件。故障安全:解析尽量不抛,登记失败绝不
该影响已提交的作业(调用方已 best-effort 包裹,这里再自保一层)。
"""
import glob
import json
import os
import re
import shlex
import time

from . import config

# #SBATCH / sbatch 命令行里我们关心的选项 -> 归一化字段名。长短选项都认。
_SBATCH_OPTS = {
    "account": ("-A", "--account"),
    "partition": ("-p", "--partition"),
    "job_name": ("-J", "--job-name"),
    "output": ("-o", "--output"),
    "time": ("-t", "--time"),
    "gres": ("--gres",),
    "gpus": ("-G", "--gpus"),
    "nodes": ("-N", "--nodes"),
}
_ALIAS = {n: field for field, names in _SBATCH_OPTS.items() for n in names}
_EXP_RE = re.compile(r"^#EXP\s+([A-Za-z_][\w-]*)\s*:\s*(.*)$")


def _extract(tokens):
    """从一串 token(#SBATCH 行拆出的、或 sbatch 命令行)提取已知选项 -> dict。

    支持 --opt=val / --opt val / -A val / -Aval 四种写法;未知选项忽略。
    """
    out = {}
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        key = val = None
        if t.startswith("--") and "=" in t:
            key, _, val = t.partition("=")
        elif t in _ALIAS:
            key = t
            val = tokens[i + 1] if i + 1 < n else ""
            i += 1
        elif t.startswith("-") and not t.startswith("--") and len(t) > 2:
            key, val = t[:2], t[2:]           # -Aacct 粘一起
        if key in _ALIAS and val is not None:
            out[_ALIAS[key]] = val
        i += 1
    return out


def _parse_script(path):
    """读脚本,返回 (sbatch字段 dict, exp指令 dict)。#EXP 同键多次出现则拼接。"""
    fields_tokens, exp = [], {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                s = line.strip()
                if s.startswith("#SBATCH"):
                    try:
                        fields_tokens += shlex.split(s[len("#SBATCH"):])
                    except ValueError:
                        pass
                m = _EXP_RE.match(s)
                if m:
                    k, v = m.group(1).lower(), m.group(2).strip()
                    exp[k] = f"{exp[k]} ; {v}" if exp.get(k) else v
    except OSError:
        pass
    return _extract(fields_tokens), exp


def _minutes(t):
    """把 Slurm 时限字符串粗略换算成分钟(用于 smoke 启发式)。无法解析返回 None。"""
    t = (t or "").strip()
    if not t:
        return None
    try:
        if "-" in t:                                   # days-hours[:min[:sec]]
            d, _, rest = t.partition("-")
            days = int(d)
            parts = rest.split(":") if rest else ["0"]
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            return days * 1440 + h * 60 + m
        parts = t.split(":")
        if len(parts) == 1:                            # minutes
            return int(parts[0])
        if len(parts) == 2:                            # minutes:seconds
            return int(parts[0])
        if len(parts) == 3:                            # hours:minutes:seconds
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None
    return None


_SMOKE_RE = re.compile(r"\b(smoke|debug|test)\b", re.I)


def _classify_smoke(exp, fields, script, argv, env_smoke):
    """判定是否 smoke,返回 (bool, 原因)。显式(env / #EXP smoke)优先于启发式。"""
    if env_smoke is not None:
        return bool(env_smoke), "env LAWN_SMOKE"
    v = (exp.get("smoke") or "").strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True, "#EXP smoke"
    if v in ("0", "false", "no", "n", "off"):
        return False, "#EXP smoke=false"
    hay = " ".join([exp.get("name", ""), fields.get("job_name", ""),
                    os.path.basename(script or ""), " ".join(argv)])
    if _SMOKE_RE.search(hay):
        return True, "名称含 smoke/test/debug"
    mins = _minutes(fields.get("time", ""))
    if mins is not None and mins <= 15:
        return True, f"时限 {mins}min ≤15"
    return False, ""


def _find_script(argv, cwd):
    """sbatch argv 里第一个存在的普通文件,当作批处理脚本。--wrap 情形返回 None。"""
    for a in argv:
        if a.startswith("-"):
            continue
        p = a if os.path.isabs(a) else os.path.join(cwd, a)
        if os.path.isfile(p):
            return p
    return None


def path_for(jobid):
    return os.path.join(config.EXPERIMENTS_DIR, f"{jobid}.json")


def load(jobid):
    try:
        with open(path_for(jobid), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def all_records():
    """按提交时间排序的全部实验记录(list[dict])。"""
    recs = []
    for f in glob.glob(os.path.join(config.EXPERIMENTS_DIR, "*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                recs.append(json.load(fh))
        except (OSError, ValueError):
            continue
    return sorted(recs, key=lambda r: r.get("submit_time", 0))


def save(rec):
    """原子写回 JSON + 重渲染 markdown 文档。"""
    os.makedirs(config.EXPERIMENTS_DIR, exist_ok=True)
    p = path_for(rec["jobid"])
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    _write_doc(rec)
    return rec


def _write_doc(rec):
    def _t(ts):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "?"

    md = (
        f"# 实验 {rec['jobid']} · {rec.get('exp_name') or rec.get('job_name') or '(未命名)'}"
        f"{'  🔬smoke' if rec.get('smoke') else ''}\n\n"
        f"- 状态: {rec.get('state', '?')}"
        f"{'    (smoke: ' + rec['smoke_reason'] + ')' if rec.get('smoke') and rec.get('smoke_reason') else ''}\n"
        f"- 提交: {_t(rec.get('submit_time'))}\n"
        f"- 账号 / 分区: {rec.get('account') or '?'} / {rec.get('partition') or '?'}\n"
        f"- GPU: {rec.get('gpus') or '?'}    时限: {rec.get('time_limit') or '?'}\n"
        f"- 目录: {rec.get('submit_cwd') or '?'}\n"
        f"- 脚本: {rec.get('script') or '(无脚本 / --wrap)'}\n"
        f"- 日志: {rec.get('output') or '?'}\n\n"
        f"## 目标\n{rec.get('exp_goal') or '(未在 #EXP goal 里写)'}\n\n"
        f"## 配置\n{rec.get('exp_config') or '(未在 #EXP config 里写)'}\n"
    )
    if rec.get("note"):
        md += f"\n## 看护备注\n{rec['note']}\n"
    with open(os.path.join(config.EXPERIMENTS_DIR, f"{rec['jobid']}.md"), "w", encoding="utf-8") as fh:
        fh.write(md)


def register(jobid, cwd, argv, env_smoke=None):
    """提交成功后登记一条实验。脚本 #EXP/#SBATCH 为底,命令行选项覆盖。

    env_smoke: None=按脚本/启发式判定;True/False=调用方(LAWN_SMOKE)显式指定。
    """
    jobid = str(jobid).strip()
    if not jobid:
        return None
    script = _find_script(argv, cwd)
    fields, exp = _parse_script(script) if script else ({}, {})
    for k, v in _extract(argv).items():            # 命令行覆盖脚本指令
        if v:
            fields[k] = v
    smoke, smoke_reason = _classify_smoke(exp, fields, script, argv, env_smoke)
    rec = {
        "smoke": smoke,
        "smoke_reason": smoke_reason,
        "jobid": jobid,
        "state": "submitted",
        "submit_time": int(time.time()),
        "submit_cwd": cwd,
        "script": script or "",
        "argv": list(argv),
        "account": fields.get("account", ""),
        "partition": fields.get("partition", ""),
        "gpus": fields.get("gpus", "") or fields.get("gres", ""),
        "job_name": fields.get("job_name", "") or exp.get("name", ""),
        "output": fields.get("output", ""),
        "time_limit": fields.get("time", ""),
        "exp_name": exp.get("name", ""),
        "exp_goal": exp.get("goal", ""),
        "exp_config": exp.get("config", ""),
        "last_check": 0,
        "note": "",
    }
    return save(rec)
