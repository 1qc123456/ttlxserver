# -*- coding: utf-8 -*-
"""浏览器集成（网页收藏 / 网页·图片另存类操作题 subType=6）。
- 打开网页：用「系统默认浏览器」（os.startfile 按 .html 关联，不再强制 Edge）。
- 读取收藏：合并 Edge 与 Chrome（Chromium 内核 Bookmarks 结构一致），默认浏览器是哪个都能检测。
"""
import os
import glob
import json
import shutil
import tempfile


def open_default_browser(target):
    """用系统默认浏览器打开本地网页文件。"""
    path = os.path.abspath(target) if os.path.exists(target) else target
    os.startfile(path)


def open_in_edge(target):
    """旧名兼容：统一改为系统默认浏览器。"""
    open_default_browser(target)


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
    profiles = ["Default"] + [os.path.basename(p) for p in glob.glob(os.path.join(base, "Profile*"))]
    for prof in profiles:
        bm = os.path.join(base, prof, "Bookmarks")
        if not os.path.exists(bm):
            continue
        try:
            tmp = os.path.join(tempfile.gettempdir(), "%s_bm_%s.json" % (tag, prof))
            shutil.copy2(bm, tmp)
            data = json.load(open(tmp, encoding="utf-8"))
            roots = data.get("roots", {})
            for key in ("bookmark_bar", "other", "synced"):
                _collect(roots.get(key), out)
        except Exception as e:  # noqa
            print("read %s bookmarks failed: %s" % (tag, e))
    return out


def read_bookmarks():
    """合并系统中 Edge 与 Chrome 所有 profile 的收藏夹（name/url），去重。"""
    local = os.environ.get("LOCALAPPDATA", "")
    out, seen = [], set()
    for base, tag in (
        (os.path.join(local, "Microsoft", "Edge", "User Data"), "edge"),
        (os.path.join(local, "Google", "Chrome", "User Data"), "chrome"),
    ):
        for it in _read_one_browser(base, tag):
            k = (it.get("name", ""), it.get("url", ""))
            if k not in seen:
                seen.add(k)
                out.append(it)
    return out
