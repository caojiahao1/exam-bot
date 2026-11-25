# -*- coding: utf-8 -*-
"""
exam-bot 本地控制服务
==================================================
一个进程同时提供：
  1) 控制台网页（http://127.0.0.1:8778）
  2) 题库查询 API（/api/query，供注入脚本调用）
  3) 浏览器/CDP 编排（启动浏览器、注入脚本、开始答题）

用法：python app/server.py
"""
import json, os, sys, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(APP_DIR)
sys.path.insert(0, APP_DIR)

import engine
import browser as br
from cdp import CDP, pick_page

PORT = 8778
DEBUG_PORT = 9222
INJECT_PATH = os.path.join(APP_DIR, 'inject.js')
INDEX_PATH = os.path.join(BASE_DIR, 'assets', 'index.html')

_state = {"page_url": "", "injected": False}


def _read(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _cdp(match=None, retries=4):
    """连接目标页（每次请求重建）。浏览器刚启动时 target 可能还没就绪，故重试。"""
    last = None
    for i in range(retries):
        t = pick_page(DEBUG_PORT, match=match)
        if not t:
            last = RuntimeError("没有可用的标签页：请先点「启动浏览器」并打开考试页面")
            time.sleep(0.6)
            continue
        try:
            c = CDP(t['webSocketDebuggerUrl'], timeout=25)
            c.call("Runtime.enable", timeout=20)
            return c, t
        except Exception as e:
            last = e
            time.sleep(0.5)
    raise last or RuntimeError("连接浏览器失败")


def _eval(expr, match=None, await_promise=True, timeout=60):
    c, t = _cdp(match)
    try:
        return c.evaluate(expr, await_promise=await_promise, timeout=timeout), t
    finally:
        c.close()


def inject():
    js = _read(INJECT_PATH)
    c, t = _cdp()
    try:
        c.call("Runtime.enable")
        # 已注入则不重复
        try:
            v = c.evaluate("!!(window.__EB && window.__EB.ver)", await_promise=False)
        except Exception:
            v = False
        if not v:
            c.evaluate(js + "\n;true", await_promise=False, timeout=60)
        _state["injected"] = True
        _state["page_url"] = t.get('url') or ''
        return {"ok": True, "url": _state["page_url"], "title": t.get('title') or ''}
    finally:
        c.close()


class H(BaseHTTPRequestHandler):
    server_version = "exam-bot/2.0"

    def log_message(self, *a):
        pass

    # ---------- 基础 ----------
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode('utf-8', 'replace') or '{}')
        except Exception:
            return {}

    def do_OPTIONS(self):
        self._send(200, b'')

    # ---------- GET ----------
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path

        if p in ('/', '/index.html'):
            try:
                return self._send(200, _read(INDEX_PATH), "text/html; charset=utf-8")
            except Exception as e:
                return self._send(500, "控制台页面缺失: %s" % e, "text/plain; charset=utf-8")

        if p == '/api/health':
            st = br.probe(DEBUG_PORT)
            info = {"browser": st, "rows": engine.rows_count(), "port": PORT, "debug_port": DEBUG_PORT}
            if st.get("ok"):
                try:
                    t = pick_page(DEBUG_PORT)
                    if t:
                        info["page"] = {"url": (t.get('url') or '')[:160], "title": t.get('title') or ''}
                except Exception:
                    pass
            return self._json(info)

        if p == '/api/status':
            try:
                v, _ = _eval("(window.__EB && window.__EB.status) ? window.__EB.status() : null", await_promise=False)
                return self._json({"ok": True, "status": v, "injected": bool(v)})
            except Exception as e:
                return self._json({"ok": False, "why": str(e)[:200]}, 200)

        if p == '/api/diagnose':
            try:
                v, _ = _eval("(window.__EB && window.__EB.diagnose) ? window.__EB.diagnose() : null", await_promise=False)
                return self._json({"ok": True, "diag": v})
            except Exception as e:
                return self._json({"ok": False, "why": str(e)[:200]}, 200)

        if p == '/api/export':
            rows = engine.export_rows()
            return self._json({"ok": True, "rows": rows})

        return self._send(404, '{"ok":false,"why":"not found"}')

    # ---------- POST ----------
    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        d = self._body()

        try:
            # 注入脚本调用：取答案
            if p == '/api/query':
                r = engine.query(d.get('stem', ''), d.get('type', ''), d.get('options') or [])
                return self._json(r)

            if p == '/api/browser/start':
                url = d.get('url') or 'about:blank'
                ok, msg = br.launch(url, DEBUG_PORT)
                return self._json({"ok": ok, "msg": msg})

            if p == '/api/browser/close':
                return self._json({"ok": br.close(DEBUG_PORT), "msg": "已关闭本工具的浏览器实例"})

            if p == '/api/inject':
                return self._json(inject())

            if p == '/api/run':
                # 保证已注入
                inj = inject()
                if not inj.get('ok'):
                    return self._json(inj)
                if d.get('human') is not None:
                    _eval("window.__EB.CFG.human = %s; true" % ('true' if d['human'] else 'false'), await_promise=False)
                v, _ = _eval("window.__EB.run()", await_promise=False)
                return self._json({"ok": True, "started": v})

            if p == '/api/stop':
                v, _ = _eval("window.__EB.stop()", await_promise=False)
                return self._json({"ok": True, "stopped": v})

            if p == '/api/pause':
                on = bool(d.get('on'))
                v, _ = _eval("window.__EB.pause(%s)" % ('true' if on else 'false'), await_promise=False)
                return self._json({"ok": True, "paused": v})

            if p == '/api/answer-current':
                inj = inject()
                if not inj.get('ok'):
                    return self._json(inj)
                v, _ = _eval("window.__EB.answerCurrent()", await_promise=False, timeout=120)
                return self._json({"ok": True, "result": v})

            if p == '/api/reset':
                v, _ = _eval("window.__EB.reset()", await_promise=False)
                return self._json({"ok": True})

            if p == '/api/bank/import':
                items = d.get('items') or []
                new, upd = engine.import_rows(items, source=d.get('source') or 'manual')
                return self._json({"ok": True, "new": new, "updated": upd, "rows": engine.rows_count()})

        except Exception as e:
            return self._json({"ok": False, "why": str(e)[:400]}, 200)

        return self._send(404, '{"ok":false,"why":"not found"}')


def main():
    engine.init_db()
    os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)
    port = PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except Exception:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print("=" * 60)
    print("  exam-bot 控制台已启动")
    print("  地址: http://127.0.0.1:%d" % port)
    print("  题库: %s（%d 题）" % (engine.DB_PATH, engine.rows_count()))
    print("  浏览器调试端口: %d" % DEBUG_PORT)
    print("=" * 60)
    print("按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == '__main__':
    main()
