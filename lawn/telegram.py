"""Telegram Bot API 客户端：发消息(分块) + 拉 updates。纯 stdlib(urllib)。"""
import json
import socket
import sys
import time
import urllib.parse
import urllib.request

CHUNK = 3500          # Telegram 单条上限 4096,留余量分块
TIMEOUT = 30          # HTTP 超时(秒)


def host_header(title=""):
    """[主机 mm-dd HH:MM] 标题  —— 与原 notify_telegram.sh 一致。"""
    host = socket.gethostname().split(".")[0]
    header = f"[{host} {time.strftime('%m-%d %H:%M')}]"
    return f"{header} {title}" if title else header


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

    def send(self, chat_id, text, header=None):
        """发送(必要时分块)。header 非空时作为首行前缀。尽力而为,失败打到 stderr。"""
        if header:
            text = f"{header}\n{text}"
        ok = True
        while text:
            chunk, text = text[:CHUNK], text[CHUNK:]
            try:
                self._post("sendMessage", {"chat_id": chat_id, "text": chunk})
            except Exception as e:  # noqa: BLE001 — 推送失败不应中断调用方
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
