"""项目清单解析与工作区(worktree)解析。对应原 projects_lib.sh。

项目有两个来源,合并后对上层透明:
  1) 静态清单文件(config.PROJECTS_CONF),手动登记;
  2) 动态发现(config.SCAN_ROOTS),扫描根目录下最近有提交的 git 仓库。
同名时静态清单优先。
"""
import os
import subprocess
import time

from . import config

FIELDS = ("name", "path", "mode", "worktree", "branch")


def _static_rows(conf=None):
    """读静态清单:跳过注释/空行,按 | 拆字段并去空格。返回 list[dict]。"""
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


def _git_last_commit(repo):
    """仓库最后一次提交的 unix 时间戳;非 git 或无提交返回 None。"""
    r = subprocess.run(
        ["git", "-C", repo, "log", "-1", "--format=%ct"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _discovered(roots=None, days=None, mode=None):
    """扫描 roots 的直接子目录,把最近 days 天内有提交的 git 仓库登记为项目。

    只认 `.git` 为真实目录的仓库(借此排除 linked worktree / submodule,
    也就顺带排除自身生成的 `<repo>_ai` 隔离工作区)。name=目录名, path=目录,
    mode=SCAN_MODE, worktree/branch 留空走缺省。返回 list[dict]。
    """
    roots = config.SCAN_ROOTS if roots is None else roots
    days = config.SCAN_DAYS if days is None else days
    mode = mode or config.SCAN_MODE
    if not roots:
        return []
    cutoff = time.time() - days * 86400
    rows = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.scandir(root), key=lambda e: e.name):
            if not entry.is_dir(follow_symlinks=False):
                continue
            if not os.path.isdir(os.path.join(entry.path, ".git")):
                continue
            ts = _git_last_commit(entry.path)
            if ts is None or ts < cutoff:
                continue
            rows.append(dict(zip(FIELDS, (entry.name, entry.path, mode, "", ""))))
    return rows


def _rows(conf=None):
    """静态清单 + 动态发现,按 name 去重(静态优先)。返回 list[dict]。"""
    rows = _static_rows(conf)
    seen = {r["name"] for r in rows}
    for r in _discovered():
        if r["name"] not in seen:
            rows.append(r)
            seen.add(r["name"])
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


def repo_path(name, conf=None):
    """项目的基仓库目录(field path),供 status/tail 找 results/logs。无则 None。"""
    row = get(name, conf)
    return (row["path"] or None) if row else None


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
