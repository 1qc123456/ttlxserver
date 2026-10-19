# -*- coding: utf-8 -*-
"""手机版学生端本地配置：~/studytemp/stu.json

与 Windows 版（C:\\studytemp\\stu.json）同文件名、同结构：
    {"last_host": "xxx:5943", "nodes": ["a.ofalias.net:1234", ...]}
写入采用「临时文件 + 原子替换」，避免写一半留下坏 JSON。
"""
import json
import os

from paths import BASE, CFG_FILE


def load():
    try:
        with open(CFG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg, dict):
            return cfg
    except Exception:
        pass
    return {}


def save(cfg):
    try:
        os.makedirs(BASE, exist_ok=True)
        tmp = CFG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CFG_FILE)
    except Exception:
        try:
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def get_last_host():
    return (load().get("last_host") or "").strip()


def set_last_host(host):
    cfg = load()
    cfg["last_host"] = host
    save(cfg)


def get_nodes():
    nodes = load().get("nodes") or []
    seen, out = set(), []
    for n in nodes:
        if isinstance(n, str) and n.strip() and n.strip() not in seen:
            seen.add(n.strip())
            out.append(n.strip())
    return out


def set_nodes(nodes):
    cfg = load()
    seen, out = set(), []
    for n in (nodes or []):
        if isinstance(n, str) and n.strip() and n.strip() not in seen:
            seen.add(n.strip())
            out.append(n.strip())
    cfg["nodes"] = out
    save(cfg)
