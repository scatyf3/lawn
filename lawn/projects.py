"""项目清单解析与工作区(worktree)解析。对应原 projects_lib.sh。"""
import os
import subprocess

from . import config

FIELDS = ("name", "path", "mode", "worktree", "branch")


def _rows(conf=None):
    """读配置:跳过注释/空行,按 | 拆字段并去空格。返回 list[dict]。"""
    conf = conf or config.PROJECTS_CONF
    rows = []
    if not os.path.isfile(conf):
        return rows
    with open(conf, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = [p.strip() for p in s.split("|")]
            parts += [""] * (len(FIELDS) - len(parts))
            rows.append(dict(zip(FIELDS, parts)))
    return rows


def names(conf=None):
    return [r["name"] for r in _rows(conf)]


def get(name, conf=None):
    for r in _rows(conf):
        if r["name"] == name:
            return r
    return None


def exists(name, conf=None):
    return any(r["name"] == name for r in _rows(conf))


def workdir(name, conf=None):
    """解析项目 -> 可用工作目录(必要时建 worktree)。

    返回 (workdir, None);失败返回 (None, 错误信息)。
    """
    row = get(name, conf)
    if row is None:
        return None, f"未知项目: {name}"
    repo, mode = row["path"], row["mode"]
    if not os.path.isdir(repo):
        return None, f"仓库不存在: {repo}"

    if mode == "inplace":
        return repo, None

    # worktree 模式
    wt = row["worktree"] or f"{repo}_ai"
    br = row["branch"] or f"ai/{name}"
    if not os.path.isdir(wt):
        has_branch = subprocess.run(
            ["git", "-C", repo, "show-ref", "--verify", "--quiet", f"refs/heads/{br}"]
        ).returncode == 0
        add = ["git", "-C", repo, "worktree", "add", wt] + ([br] if has_branch else ["-b", br])
        if subprocess.run(add).returncode != 0:
            return None, "建 worktree 失败"
    return wt, None
