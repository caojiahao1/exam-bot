# -*- coding: utf-8 -*-
"""
浏览器启动 / 发现
自动寻找 Edge / Chrome，用独立配置目录 + 调试端口启动（不动用户日常用的浏览器）
"""
import os, platform, subprocess, time

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".exam-bot-profile")
DEBUG_PORT = 9222


def _win_candidates():
    out = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        for name in ("Microsoft/Edge/Application/msedge.exe",
                     "Google/Chrome/Application/chrome.exe",
                     "Chromium/Application/chrome.exe"):
            p = os.path.join(base, *name.split("/"))
            if os.path.exists(p):
                out.append(p)
    return out


def _mac_candidates():
    return [p for p in (
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ) if os.path.exists(p)]


def _linux_candidates():
    out = []
    for n in ("microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            p = os.path.join(d, n)
            if os.path.exists(p):
                out.append(p)
    return out


def find_browser():
    sysname = platform.system()
    c = _win_candidates() if sysname == "Windows" else (
        _mac_candidates() if sysname == "Darwin" else _linux_candidates())
    return c[0] if c else None


def probe(port=DEBUG_PORT):
    """检测调试端口是否可用"""
    from cdp import http_json
    try:
        v = http_json("http://127.0.0.1:%d/json/version" % port, timeout=2)
        return {"ok": True, "browser": v.get("Browser", "")}
    except Exception as e:
        return {"ok": False, "why": str(e)[:120]}


def launch(url="about:blank", port=DEBUG_PORT, profile=None):
    st = probe(port)
    if st["ok"]:
        return True, "浏览器已在运行（%s）" % st["browser"]

    exe = find_browser()
    if not exe:
        return False, "没找到 Edge / Chrome，请先安装任意一个 Chromium 系浏览器"

    profile = profile or PROFILE_DIR
    os.makedirs(profile, exist_ok=True)

    args = [
        exe,
        "--remote-debugging-port=%d" % port,
        "--remote-allow-origins=*",
        "--user-data-dir=%s" % profile,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        url,
    ]
    try:
        kw = {}
        if platform.system() == "Windows":
            kw["creationflags"] = 0x00000008 | 0x00000200
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    except Exception as e:
        return False, "启动失败: %s" % e

    for _ in range(50):
        time.sleep(0.4)
        if probe(port)["ok"]:
            return True, "已启动 %s（端口 %d）" % (os.path.basename(exe), port)
    return False, "启动后未检测到调试端口：可能有旧实例占用同一配置目录，请关闭所有该浏览器的窗口后重试"


def close(port=DEBUG_PORT):
    """只关闭本工具配置目录启动的实例"""
    try:
        if platform.system() == "Windows":
            ps = (
                "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" | "
                "Where-Object { $_.CommandLine -like '*%s*' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" % PROFILE_DIR.replace("\\", "\\\\")
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        else:
            subprocess.run(["pkill", "-f", PROFILE_DIR],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        return True
    except Exception:
        return False
