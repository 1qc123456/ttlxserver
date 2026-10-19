# -*- coding: utf-8 -*-
"""手机版学生端：路径与系统打开方式。

运行环境：Android 手机上的 Debian arm64 容器（proot，免 root，
典型载体：小小电脑 / Termux+proot-distro）。也兼容 Windows 开发调试。

目录约定（容器内，与 Windows 版 C:\\studytemp 语义对应）：
    ~/studytemp/stu.json         本地配置（服务器地址记忆 / 节点列表）
    ~/studytemp/考生文件夹/<题号>/ 操作题作答目录（上传给服务端判分的就是它）
"""
import os
import subprocess
import sys


def is_windows():
    return sys.platform.startswith("win")


def is_linux():
    return sys.platform.startswith("linux")


HOME = os.path.expanduser("~")

# 统一根目录（对应 Windows 版的 C:\studytemp）
BASE = os.path.join(HOME, "studytemp")
CFG_FILE = os.path.join(BASE, "stu.json")
WORK_DIR = os.path.join(BASE, "考生文件夹")

# 客户端代码自身所在目录（用于 vapp 素材等随包资源）
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def ensure_dirs():
    """确保基础目录存在（首次运行自动创建）"""
    os.makedirs(WORK_DIR, exist_ok=True)


def q_dir(qid):
    """某道题的作答目录（不存在则建）"""
    d = os.path.join(WORK_DIR, str(qid))
    os.makedirs(d, exist_ok=True)
    return d


def open_in_os(path):
    """用系统默认程序打开文件 / 文件夹。
    容器内 xdg-open 会自动路由到 WPS / 文件管理器 / 浏览器。"""
    try:
        if is_windows():
            os.startfile(path)                     # noqa（Windows 开发调试用）
        else:
            subprocess.Popen(["xdg-open", path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def run_bg(cmd, cwd=None):
    """后台启动外部程序（如 wine / wps），不阻塞界面"""
    try:
        subprocess.Popen(cmd, cwd=cwd,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def which(name):
    """查找可执行文件路径（PATH 里找不到返回 None）"""
    from shutil import which as _w
    return _w(name)
