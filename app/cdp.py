# -*- coding: utf-8 -*-
"""
CDP 客户端（纯标准库，零依赖）
连接 Chromium 系浏览器的远程调试端口，执行 JS、读页面状态。
"""
import base64, json, os, socket, struct, time
from urllib.parse import urlparse


def http_json(url, timeout=5):
    """极简 HTTP GET → JSON"""
    import urllib.request
    req = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


class CDP:
    def __init__(self, ws_url, timeout=30):
        self.timeout = timeout
        self.sock = None
        self.raw = b""
        self._id = 0
        self.events = []
        self._connect(ws_url)

    def _connect(self, ws_url):
        u = urlparse(ws_url)
        host, port = u.hostname or "127.0.0.1", u.port or 80
        path = u.path + (("?" + u.query) if u.query else "")
        s = socket.create_connection((host, port), timeout=self.timeout)
        s.settimeout(self.timeout)
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((
            "GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
            % (path, host, port, key)
        ).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 握手失败：连接被关闭")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise RuntimeError("WebSocket 握手失败：" + head.decode("latin1")[:200])
        self.sock, self.raw = s, rest

    def _read_exact(self, n):
        buf = self.raw
        while len(buf) < n:
            chunk = self.sock.recv(max(4096, n - len(buf)))
            if not chunk:
                raise EOFError("连接已关闭")
            buf += chunk
        out, self.raw = buf[:n], buf[n:]
        return out

    def _send_frame(self, payload, opcode=0x1):
        b = bytearray([0x80 | opcode])
        ln = len(payload)
        mask = os.urandom(4)
        if ln < 126:
            b.append(0x80 | ln)
        elif ln < 65536:
            b.append(0x80 | 126); b += struct.pack(">H", ln)
        else:
            b.append(0x80 | 127); b += struct.pack(">Q", ln)
        b += mask
        b += bytes(c ^ mask[i & 3] for i, c in enumerate(payload))
        self.sock.sendall(bytes(b))

    def _recv_message(self):
        data = b""
        while True:
            b1, b2 = self._read_exact(2)
            fin, opcode, masked, ln = b1 & 0x80, b1 & 0x0F, b2 & 0x80, b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else None
            payload = self._read_exact(ln) if ln else b""
            if mask:
                payload = bytes(c ^ mask[i & 3] for i, c in enumerate(payload))
            if opcode == 0x9:
                self._send_frame(payload, 0xA); continue
            if opcode == 0x8:
                raise EOFError("服务端关闭连接")
            data += payload
            if fin:
                return data

    def call(self, method, params=None, timeout=None):
        self._id += 1
        mid = self._id
        self._send_frame(json.dumps({"id": mid, "method": method,
                                     "params": params or {}}).encode("utf-8"))
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            msg = json.loads(self._recv_message().decode("utf-8", "replace"))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})
            self.events.append(msg)
            if len(self.events) > 200:
                self.events = self.events[-100:]
        raise TimeoutError(method + " 超时")

    def evaluate(self, expr, await_promise=True, timeout=None):
        r = self.call("Runtime.evaluate", {
            "expression": expr, "awaitPromise": await_promise,
            "returnByValue": True, "userGesture": True,
        }, timeout=timeout)
        res = r.get("result", {})
        if r.get("exceptionDetails"):
            ed = r["exceptionDetails"]
            detail = (ed.get("exception") or {}).get("description") or ed.get("text") or str(ed)
            raise RuntimeError("页面 JS 异常: " + str(detail)[:600])
        return res.get("value")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def pages(port=9222, timeout=4):
    return [x for x in http_json("http://127.0.0.1:%d/json" % port, timeout) if x.get("type") == "page"]


SKIP_PREFIX = ("devtools://", "edge://", "chrome://", "about:blank", "chrome-extension://")


def pick_page(port=9222, match=None):
    ps = pages(port)
    if not ps:
        return None
    if match:
        hit = [p for p in ps if match in (p.get("url") or "")]
        if hit:
            return hit[0]
    cand = [p for p in ps if not (p.get("url") or "").startswith(SKIP_PREFIX)]
    return (cand or ps)[0]
