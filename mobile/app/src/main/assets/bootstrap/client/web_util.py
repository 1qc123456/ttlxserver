# -*- coding: utf-8 -*-
"""手机版浏览器工具（网页收藏 / 另存类操作题 subType=6）。

背景（已实测）：**微软官方 Edge 没有 Linux arm64 版本**（packages.microsoft.com
的 edge 仓库 arm64 索引为空，beta/dev 通道 404，pool 里只有 amd64）。
因此容器内的浏览器按优先级自动选择：

    ① microsoft-edge      —— 若装了 Edge（实验性的 box64 版 / 未来官方版）
    ② chromium            —— Edge 同内核（Chromium），书签文件格式与 Edge 完全一致，
                              "网页收藏"题（hf3381 favorites 判定）可正常判分
    ③ xdg-open 兜底

read_bookmarks() 会把所有能找到的浏览器收藏夹都读出来（Chromium JSON 格式，
Edge/Chrome/Chromium 通用），包括：
    · 容器内 Edge      ~/.config/microsoft-edge/…
    · 容器内 Chromium  ~/.config/chromium/…
    · Wine 里的 Windows Edge（若用 Wine 跑 Edge）
    · 开发机上 Windows 的 Edge / Chrome（本机调试用）
最后按 (name,url) 去重合并，随交卷 uploadOperate 的 favorites 字段上传。
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

# 各种环境下的 Edge/Chromium profile 根目录（"User Data" 或直接用 "Default"）
def _profile_bases():
    home = os.path.expanduser("~")
    bases = [
        # Linux 原生（Edge 在 Linux 下 user data dir 就是 ~/.config/microsoft-edge）
        (os.path.join(home, ".config", "microsoft-edge"), "edge"),
        (os.path.join(home, ".config", "chromium"), "chromium"),
        (os.path.join(home, ".config", "chromium-browser"), "chromium"),
        (os.path.join(home, ".config", "google-chrome"), "chrome"),
    ]
    # Wine 里的 Windows Edge（Hangover/box64 场景）
    for pfx in glob.glob(os.path.join(home, ".wine", "drive_c", "users", "*")):
        bases.append((os.path.join(pfx, "AppData", "Local", "Microsoft", "Edge", "User Data"), "edge-wine"))
    # 开发机（Windows）
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        bases.append((os.path.join(local, "Microsoft", "Edge", "User Data"), "edge-win"))
        bases.append((os.path.join(local, "Google", "Chrome", "User Data"), "chrome-win"))
    return bases


def browser_exe():
    """返回可用的浏览器命令名（优先 Edge，其次 Chromium），都没有返回 None"""
    for name in ("microsoft-edge", "microsoft-edge-stable", "msedge",
                 "chromium", "chromium-browser", "google-chrome"):
        if shutil.which(name):
            return name
    return None


def browser_label():
    """给界面显示用：当前会用哪个浏览器"""
    exe = browser_exe()
    if exe is None:
        return "系统默认浏览器"
    if "edge" in exe or "msedge" in exe:
        return "Edge"
    return "Chromium（Edge 同内核）"


def open_url(target):
    """用浏览器打开网页文件/网址：优先 Edge → Chromium → xdg-open"""
    exe = browser_exe()
    try:
        if exe:
            subprocess.Popen([exe, target],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception:
        pass
    try:
        if sys.platform.startswith("win"):
            os.startfile(target)            # noqa
        else:
            subprocess.Popen(["xdg-open", target],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _collect(node, out):
    if not isinstance(node, dict):
        return
    if node.get("type") == "url":
        out.append({"name": node.get("name", ""), "url": node.get("url", "")})
    for ch in node.get("children", []) or []:
        _collect(ch, out)


def _read_one_browser(base, tag):
    out = []
    if not os.path.isdir(base):
        return out
    profiles = ["Default"] + [os.path.basename(p)
                              for p in glob.glob(os.path.join(base, "Profile*"))]
    for prof in profiles:
        bm = os.path.join(base, prof, "Bookmarks")
        if not os.path.exists(bm):
            continue
        try:
            tmp = os.path.join(tempfile.gettempdir(), "%s_bm_%s.json" % (tag, prof))
            shutil.copy2(bm, tmp)
            with open(tmp, encoding="utf-8") as f:
                data = json.load(f)
            roots = data.get("roots", {})
            for key in ("bookmark_bar", "other", "synced"):
                _collect(roots.get(key), out)
        except Exception as e:
            print("read %s bookmarks failed: %s" % (tag, e))
    return out


def read_bookmarks():
    """合并所有浏览器 profile 的收藏夹（name/url），去重后返回列表。"""
    out, seen = [], set()
    for base, tag in _profile_bases():
        for it in _read_one_browser(base, tag):
            k = (it.get("name", ""), it.get("url", ""))
            if k not in seen:
                seen.add(k)
                out.append(it)
    return out
