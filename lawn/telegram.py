"""Telegram Bot API 客户端：发消息(分块) + 拉 updates。纯 stdlib(urllib)。"""
import html
import json
import re
import socket
import sys
import time
import urllib.parse
import urllib.request

CHUNK = 3500          # Telegram 单条上限 4096,留余量分块
TIMEOUT = 30          # HTTP 超时(秒)

_TAG_RE = re.compile(r"<[^>]+>")


def host_header(title=""):
    """[主机 mm-dd HH:MM] 标题  —— 与原 notify_telegram.sh 一致。"""
    host = socket.gethostname().split(".")[0]
    header = f"[{host} {time.strftime('%m-%d %H:%M')}]"
    return f"{header} {title}" if title else header


def to_plain(s):
    """去掉 HTML 标签 + 反转义实体,用于终端打印或解析失败兜底。"""
    return html.unescape(_TAG_RE.sub("", s))


def _chunks(text, size=CHUNK):
    """按行边界切块,尽量不切断一行;单行超长则硬切。至少返回一块。"""
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:               # 单行超长:硬切
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:size])
            line = line[size:]
        if len(cur) + len(line) > size:
            out.append(cur)
            cur = line
        else:
            cur += line
    if cur:
        out.append(cur)
    return out or [""]


class Telegram:
    def __init__(self, token):
        self.api = f"https://api.telegram.org/bot{token}"

    def _post(self, method, params):
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(f"{self.api}/{method}", data=data)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)

    def _get(self, method, params):
        url = f"{self.api}/{method}?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            return json.load(resp)

    def send(self, chat_id, text, header=None, parse_mode=None):
        """发送(必要时按行分块)。header 非空作首行前缀;parse_mode 如 'HTML'。

        用 parse_mode 时若某块解析失败(例如分块切断了标签),自动退回纯文本
        重发一次,保证内容送达。尽力而为,最终失败打到 stderr。
        """
        if header:
            text = f"{header}\n{text}"
        ok = True
        for chunk in _chunks(text):
            params = {"chat_id": chat_id, "text": chunk}
            if parse_mode:
                params["parse_mode"] = parse_mode
            try:
                self._post("sendMessage", params)
            except Exception as e:  # noqa: BLE001 — 推送失败不应中断调用方
                if parse_mode:                    # 退回纯文本再试一次
                    try:
                        self._post("sendMessage",
                                   {"chat_id": chat_id, "text": to_plain(chunk)})
                        continue
                    except Exception as e2:  # noqa: BLE001
                        e = e2
                print(f"tg 推送失败: {e}", file=sys.stderr)
                ok = False
        return ok

    def get_updates(self, offset, timeout=0):
        """长轮询取消息;只要 message。返回解析后的 dict,失败返回 None。"""
        try:
            return self._get("getUpdates", {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": json.dumps(["message"]),
            })
        except Exception as e:  # noqa: BLE001
            print(f"getUpdates 失败: {e}", file=sys.stderr)
            return None
