# -*- coding: utf-8 -*-
"""MySQL 环境检测与临时实例配置（学生端用）。

流程：
1. detect_mysql()：检测本机是否装有 MySQL（mysqld 可执行 / 3306 端口 / 服务）。
2. start_temp_instance(datadir)：用本机 mysqld 把题目素材（MySQL 5.7 数据目录快照）
   起成一个临时实例（随机端口、skip-grant-tables 免密），供 Navicat for MySQL 连接作答。
"""
import glob
import os
import socket
import subprocess
import time

MYSQL_DOWNLOAD_URL = "https://downloads.mysql.com/archives/installer/"
NAVICAT_DOWNLOAD_URL = "https://www.navicat.com.cn/download/navicat-for-mysql"

_INSTALL_PATTERNS = (
    r"C:\Program Files\MySQL\MySQL Server*\bin\mysqld.exe",
    r"C:\Program Files (x86)\MySQL\MySQL Server*\bin\mysqld.exe",
    r"D:\Program Files\MySQL\MySQL Server*\bin\mysqld.exe",
    r"D:\Program Files (x86)\MySQL\MySQL Server*\bin\mysqld.exe",
)


def _find_exe(name):
    """在 PATH 与常见安装路径里找 mysql.exe / mysqld.exe，返回第一个命中。"""
    for exe in (shutil_which(name),):
        if exe:
            return exe
    for pat in (p.replace("mysqld.exe", name + ".exe") for p in _INSTALL_PATTERNS):
        for p in glob.glob(pat):
            if os.path.isfile(p):
                return p
    return None


def shutil_which(name):
    try:
        import shutil
        return shutil.which(name) or shutil.which(name + ".exe")
    except Exception:
        return None


def bundled_mysqld():
    """学生端随包内置的 MySQL 5.7 运行时（_MEIPASS/runtime/mysql/bin/mysqld.exe）。

    与题库 datadir（5.7 快照）完全同版本，不依赖学生本机是否安装 MySQL，
    也规避本机 MySQL 8.0 无法启动 5.7 数据目录的兼容问题。
    """
    try:
        import sys
        base = getattr(sys, "_MEIPASS", None)
        if not base:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # pyclient/
        p = os.path.join(base, "runtime", "mysql", "bin", "mysqld.exe")
        if os.path.isfile(p):
            return p
    except Exception:
        pass
    return None


def find_mysqld():
    b = bundled_mysqld()
    if b:
        return b
    return _find_exe("mysqld")


def find_mysql_exe():
    b = bundled_mysqld()
    if b:
        cand = os.path.join(os.path.dirname(b), "mysql.exe")
        if os.path.isfile(cand):
            return cand
    return _find_exe("mysql")


_NAVICAT_PATTERNS = (
    r"C:\Program Files\PremiumSoft\Navicat*\navicat.exe",
    r"C:\Program Files (x86)\PremiumSoft\Navicat*\navicat.exe",
    r"D:\Program Files\PremiumSoft\Navicat*\navicat.exe",
    r"D:\Program Files (x86)\PremiumSoft\Navicat*\navicat.exe",
    r"C:\Program Files\Navicat*\navicat.exe",
    r"D:\Program Files\Navicat*\navicat.exe",
)


def find_navicat():
    """检测本机是否装有 Navicat，返回 navicat.exe 完整路径或 None。

    优先读注册表卸载项的 DisplayIcon / InstallLocation（可拿到真实 exe 路径），
    再回退常见安装路径 glob。
    """
    import glob as _glob
    try:
        import winreg
    except Exception:
        winreg = None
    if winreg is not None:
        try:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    key = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")
                except OSError:
                    continue
                try:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(key, i)
                            i += 1
                        except OSError:
                            break
                        try:
                            sk = winreg.OpenKey(key, sub)
                            try:
                                name = winreg.QueryValueEx(sk, "DisplayName")[0]
                            except OSError:
                                name = ""
                            if "navicat" in (name or "").lower():
                                # DisplayIcon 通常就是 navicat.exe 全路径
                                try:
                                    icon = winreg.QueryValueEx(sk, "DisplayIcon")[0]
                                except OSError:
                                    icon = ""
                                if icon and icon.lower().endswith("navicat.exe") and os.path.isfile(icon):
                                    return icon
                                # 否则用 InstallLocation + navicat.exe
                                try:
                                    loc = winreg.QueryValueEx(sk, "InstallLocation")[0]
                                except OSError:
                                    loc = ""
                                for cand in (loc, os.path.dirname(icon) if icon else ""):
                                    if cand:
                                        p = os.path.join(cand, "navicat.exe")
                                        if os.path.isfile(p):
                                            return p
                                return sub  # 至少说明已安装
                        except OSError:
                            pass
                        finally:
                            try:
                                sk.Close()
                            except Exception:
                                pass
                finally:
                    try:
                        key.Close()
                    except Exception:
                        pass
        except Exception:
            pass
    for pat in _NAVICAT_PATTERNS:
        for p in _glob.glob(pat):
            if os.path.isfile(p):
                return p
    return None


def detect_navicat():
    """检测本机 Navicat。返回 dict：found / where（exe 路径或 None）。"""
    where = find_navicat()
    return {"found": bool(where), "where": where}


def start_navicat(exe=None):
    """自动打开 Navicat。exe 为空时自动查找；返回 (成功?, 路径或错误)。"""
    exe = exe or find_navicat()
    if not exe or not os.path.isfile(exe):
        return False, "未找到 navicat.exe"
    try:
        subprocess.Popen([exe])
        return True, exe
    except Exception as e:
        return False, "启动 Navicat 失败: %s" % e


def port_open(port, host="127.0.0.1", timeout=1.0):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _find_mysql_service():
    """sc query 找 MySQL 相关服务名；找不到返回 None。

    注意 sc 输出 DISPLAY_NAME 行带缩进、冒号前有空格（'        DISPLAY_NAME       : MySQL57'），
    必须用正则匹配 DISPLAY_NAME + 冒号（\s*），不能 startswith 紧贴冒号（永远匹配不到）。
    """
    import re
    try:
        out = subprocess.run(
            ["sc", "query", "type=", "service", "state=", "all"],
            capture_output=True, timeout=20)
        text = out.stdout.decode("utf-8", "replace") + out.stderr.decode("utf-8", "replace")
        upper = text.upper()
        if "MYSQL" not in upper:
            return None
        names = []
        cur = None
        for line in text.splitlines():
            line = line.strip()
            if re.match(r"SERVICE_NAME\s*:", line, re.IGNORECASE):
                cur = line.split(":", 1)[1].strip()
            elif re.match(r"DISPLAY_NAME\s*:", line, re.IGNORECASE) and cur:
                if "MYSQL" in line.upper():
                    names.append(cur)
                cur = None
        return names[0] if names else None
    except Exception:
        return None


def detect_mysql():
    """检测 MySQL 可用性。返回 dict：found / mysqld / mysql_exe / port3306 / service。
    found 包含随包内置运行时（有内置 5.7 即可起实例，无需本机安装）。"""
    mysqld = find_mysqld()
    mysql_exe = find_mysql_exe()
    p3306 = port_open(3306)
    svc = _find_mysql_service()
    found = bool(bundled_mysqld() or mysqld or p3306 or svc or mysql_exe)
    return {"found": found, "mysqld": mysqld, "mysql_exe": mysql_exe,
            "port3306": p3306, "service": svc, "bundled": bool(bundled_mysqld())}


def _kill_port_owner(port, wait=6):
    """强制结束占用指定端口的进程：netstat 找 LISTENING PID → taskkill /F。

    考试场景专用：MySQL 题固定要求端口 3306，若被本机其它程序（含本机 MySQL
    服务）占用则强制结束，确保临时实例一定起在 3306。返回是否已释放。
    """
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=10)
        text = out.stdout.decode("utf-8", "replace") + out.stderr.decode("utf-8", "replace")
        target = ":%d" % port
        pids = set()
        for line in text.splitlines():
            if target in line and "LISTENING" in line.upper():
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(parts[-1])
        for pid in pids:
            try:
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True, timeout=10)
            except Exception:
                pass
    except Exception:
        pass
    for _ in range(wait):
        if not port_open(port):
            return True
        time.sleep(0.5)
    return not port_open(port)


def stop_mysql_service(name=None):
    """停止本机 MySQL 服务（net stop）。name 为空时自动查找。返回 (是否成功, 服务名或错误)。"""
    name = name or _find_mysql_service()
    if not name:
        return False, "未找到 MySQL 服务"
    try:
        r = subprocess.run(["net", "stop", name], capture_output=True, timeout=30)
        if r.returncode == 0:
            return True, name
        return False, r.stdout.decode("utf-8", "replace")[-200:] or r.stderr.decode("utf-8", "replace")[-200:]
    except Exception as e:
        return False, str(e)


def restore_mysql_service(name):
    """恢复之前被停止/强杀的 MySQL 服务（net start）。返回是否成功。"""
    if not name:
        return True
    try:
        # 已在运行则直接视为恢复成功（避免 net start 报“服务已经启动”）
        r = subprocess.run(["sc", "query", name], capture_output=True, timeout=15)
        if b"RUNNING" in r.stdout.upper():
            return True
        r = subprocess.run(["net", "start", name], capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def start_temp_instance(datadir, mysqld=None, port=None, wait=60):
    """用 mysqld 在 datadir 起临时实例（--skip-grant-tables 免密）。

    MySQL 题题面固定要求端口 3306：优先用 3306。被占用时按序处理：
    ① 识别占用者是否本机 MySQL 服务 → 先 net stop（干净、答题后 net start 恢复）；
    ② 不是服务（普通进程）→ taskkill /F 强杀；
    ③ 都失败 → 兜底改用随机端口起实例（保证能做题），由调用方把实际端口展示给学生。

    返回 (port, proc, err, stopped_service)：
    成功时 err=None；失败时 proc 可能非空（进程残留，调用方负责清理），
    stopped_service 为被停止的本机 MySQL 服务名（需要时用于恢复）。
    """
    mysqld = mysqld or find_mysqld()
    if not mysqld:
        return None, None, "未找到 mysqld.exe，无法启动临时 MySQL 实例", None
    if not os.path.isdir(datadir):
        return None, None, "素材数据目录不存在: %s" % datadir, None
    if port is None:
        port = 3306
    stopped_service = None
    if port_open(port):
        svc = _find_mysql_service()
        freed = False
        if svc:
            # ① 占用者是本机 MySQL 服务：先停服务（干净，进程/端口一起释放）
            ok, svc2 = stop_mysql_service(svc)
            if ok:
                stopped_service = svc2
                for _ in range(12):
                    if not port_open(port):
                        freed = True
                        break
                    time.sleep(0.5)
                freed = not port_open(port)
        if not freed:
            # ② 普通进程占用（或停服务失败）：强杀占用 3306 的进程
            freed = _kill_port_owner(port)
        if not freed:
            # ③ 兜底：随机端口起实例（保证学生能做题，端口由 UI 提示）
            rnd = free_port()
            port2, proc2, err2, svc2 = start_temp_instance(
                datadir, mysqld=mysqld, port=rnd, wait=wait)
            if port2:
                return port2, proc2, None, (stopped_service or svc2)
            return None, proc2, ("端口 %d 被占用且无法释放，随机端口也启动失败：%s"
                                 % (port, err2 or ""))[:300], stopped_service
    basedir = os.path.dirname(os.path.dirname(os.path.abspath(mysqld)))
    log_path = os.path.join(datadir, "_tmp_mysqld.log")
    args = [
        mysqld,
        "--basedir=" + basedir,
        "--datadir=" + datadir,
        "--port=%d" % port,
        "--skip-grant-tables",
        "--skip-log-bin",
        "--sql-mode=",
        "--innodb-buffer-pool-size=64M",
        "--performance-schema=OFF",
        "--innodb-flush-method=normal",
        "--innodb-flush-log-at-trx-commit=0",
        "--max_connections=200",
        "--console",
    ]
    proc = None
    try:
        logf = open(log_path, "wb")
        proc = subprocess.Popen(
            args, stdout=logf, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return None, proc, "启动 mysqld 失败: %s" % e, stopped_service
    for _ in range(wait):
        if proc.poll() is not None:
            err = _tail(log_path)
            return None, proc, "mysqld 提前退出：%s" % err[-800:], stopped_service
        if port_open(port, timeout=0.5):
            return port, proc, None, stopped_service
        time.sleep(1)
    err = _tail(log_path)
    return None, proc, "等待 MySQL 启动超时（%ds）：%s" % (wait, err[-800:]), stopped_service


def _tail(path, n=1500):
    try:
        with open(path, "rb") as f:
            data = f.read()
        return data.decode("utf-8", "replace")[-n:]
    except Exception:
        return ""
