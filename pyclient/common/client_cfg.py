# -*- coding: utf-8 -*-
"""客户端本地配置（教师端/学生端共用）：
记录「上次成功连接的服务器地址」和「从服务器获取的节点列表」。

统一存放（2026-10-09 改）：
    C:\\studytemp\\tea.json    教师端
    C:\\studytemp\\stu.json    学生端
  · 目录不存在自动创建；若 C:\\studytemp 无法创建（权限等），
    自动退回 exe 同目录同名文件，保证功能不受影响、绝不崩。
  · 兼容旧版：首次运行会读取原 exe 同目录的 client_config.json 并迁移过来。

角色识别顺序：
  ① 入口脚本显式调用 set_role("tea"/"stu")（teacher/main.py、student/main.py 已加）；
  ② 兜底自动判断：frozen 看 exe 名（Teacher.exe/Student.exe），源码看启动脚本路径。
"""
import os, sys, json

BASE_DIR = r"C:\studytemp"
_ROLE = None                       # "tea" / "stu"
_ROLE_FILE = {"tea": "tea.json", "stu": "stu.json"}


def set_role(role):
    """入口脚本调用：教师端 set_role("tea")，学生端 set_role("stu")。"""
    global _ROLE
    if role in _ROLE_FILE:
        _ROLE = role


def _detect_role():
    """未显式设置时的兜底判断（保证任何时候都能定位到正确的文件）。"""
    if _ROLE:
        return _ROLE
    try:
        if getattr(sys, "frozen", False):
            nm = os.path.basename(sys.executable).lower()
            if "teacher" in nm:
                return "tea"
            if "student" in nm:
                return "stu"
        mf = (getattr(sys.modules.get("__main__"), "__file__", "") or "").replace("\\", "/").lower()
        if "teacher" in mf:
            return "tea"
        if "student" in mf:
            return "stu"
    except Exception:
        pass
    return "tea"


def _exe_dir():
    """exe 所在目录（frozen）/ pyclient 根目录（源码模式）——旧配置与兜底落盘用。"""
    try:
        if getattr(sys, "frozen", False):
            return os.path.dirname(os.path.abspath(sys.executable))
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        return os.getcwd()


def _cfg_path():
    """主配置路径：C:\\studytemp\\<role>.json（目录自动建；失败退回 exe 同目录）。"""
    name = _ROLE_FILE[_detect_role()]
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
        return os.path.join(BASE_DIR, name)
    except Exception:
        return os.path.join(_exe_dir(), name)


def _legacy_paths():
    """旧版配置位置（exe 同目录 / 源码 pyclient 根的 client_config.json），仅迁移时读。"""
    out = []
    for d in (_exe_dir(), os.path.join(_exe_dir(), "common")):
        p = os.path.join(d, "client_config.json")
        if p not in out:
            out.append(p)
    return out


def load():
    p = _cfg_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                return cfg
        except Exception:
            pass
        # 新配置存在但损坏：返回空，不回读旧位置（防止旧数据回灌覆盖）
        return {}
    # 新配置还不存在：尝试从旧版 client_config.json 迁移一次，之后都走新位置
    for lp in _legacy_paths():
        try:
            with open(lp, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                save(cfg)
                return cfg
        except Exception:
            continue
    return {}


def save(cfg):
    """原子写入（先写 .tmp 再替换），避免写一半掉电/被杀留下坏 JSON。"""
    p = _cfg_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
    except Exception:
        pass
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:
        try:
            with open(p, "w", encoding="utf-8") as f:
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
    out = []
    for n in nodes:
        if isinstance(n, str) and n.strip():
            out.append(n.strip())
    # 去重保序
    seen = set(); res = []
    for n in out:
        if n not in seen:
            seen.add(n); res.append(n)
    return res


def set_nodes(nodes):
    cfg = load()
    seen, out = set(), []
    for n in (nodes or []):
        if isinstance(n, str) and n.strip():
            v = n.strip()
            if v not in seen:
                seen.add(v); out.append(v)
    cfg["nodes"] = out            # 写入时就去重，文件内容保持干净
    save(cfg)
