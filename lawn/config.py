"""配置加载：env 文件、路径常量、Settings。纯 stdlib。"""
import os
import re

# 包目录(lawn/)与仓库根(放 ai_agent.sh)
PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG_DIR)

ENV_FILE = os.environ.get("EAGLE_NOTIFY_ENV", os.path.expanduser("~/.config/eagle-notify.env"))
PROJECTS_CONF = os.environ.get("PROJECTS_CONF", os.path.expanduser("~/.config/eagle-notify-projects.conf"))
# !tail 找日志、report 找最新 results 的默认仓库目录
EAGLE_REPO = os.environ.get("EAGLE_REPO", "/scratch/yf3005/EAGLE_new")

STATE_DIR = os.path.expanduser("~/.cache/eagle-notify")
OFFSET_FILE = os.path.join(STATE_DIR, "tg_offset")
ACTIVE_FILE = os.path.join(STATE_DIR, "active_project")

AI_AGENT_SH = os.path.join(ROOT, "ai_agent.sh")

_ENV_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_]\w*)=(.*)$")


def _strip_quotes(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def load_env(path=None):
    """解析 shell 风格的 KEY=VALUE env 文件,返回 dict。不存在则抛 FileNotFoundError。"""
    path = path or ENV_FILE
    if not os.path.isfile(path):
        raise FileNotFoundError(f"缺少 {path}")
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = _ENV_RE.match(line)
            if m:
                out[m.group(1)] = _strip_quotes(m.group(2))
    return out


class Settings:
    """从 env 文件读出的运行配置。"""

    def __init__(self, env=None):
        env = env if env is not None else load_env()
        self.bot_token = env.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = env.get("TELEGRAM_CHAT_ID", "")
        raw = env.get("TELEGRAM_ALLOWED_USER_ID", "")
        self.allowed_user_ids = [x for x in (s.strip() for s in raw.split(",")) if x]

    def require(self, *names):
        """缺哪个必填项就抛 SystemExit,跟 bash 的 :? 行为一致。"""
        miss = [n for n in names if not getattr(self, _ATTR.get(n, n), None)]
        if miss:
            raise SystemExit("env 缺 " + ", ".join(miss))


_ATTR = {
    "TELEGRAM_BOT_TOKEN": "bot_token",
    "TELEGRAM_CHAT_ID": "chat_id",
    "TELEGRAM_ALLOWED_USER_ID": "allowed_user_ids",
}
