# -*- coding: utf-8 -*-
from common.utils import render_detail_html, format_operate_stem
from common.grade_detail import GradeDetailDialog, is_pending
from common import edge_web
from common import mysql_env
import os, base64, time, shutil, secrets, tempfile, zipfile, threading
from PySide6.QtCore import Qt, QTimer, Signal, QEvent, QObject, QThread
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QButtonGroup, QRadioButton, QTextEdit, QFileDialog, QMessageBox, QScrollArea,
    QFrame, QSizePolicy, QDialog, QInputDialog, QProgressBar)
from qfluentwidgets import (PrimaryPushButton, PushButton, FluentIcon as FIF,
    BodyLabel, StrongBodyLabel, CaptionLabel, InfoBar, InfoBarPosition)

# 虚拟程序随包放在 app/pyclient/common/vapp，换机不依赖外部目录
VAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common", "vapp")
# 虚拟程序运行时目录：exe 从 vapp_exes.zip 解压到 %TEMP%/ttlx_vapp/<qid>/ 再启动，
# 每题独立目录 → 判分文件（Win10/Edge/Win10_Email 无后缀 log）互不串题，交卷后清理
VAPP_TEMP_ROOT = os.path.join(tempfile.gettempdir(), "ttlx_vapp")
# 操作题答题工作区：C:\stu\<本次随机文件夹>\<题号>\，交卷后整目录删除
STU_ROOT = r"C:\stu"
OPEN_EXT = (".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt",
            ".html", ".htm", ".accdb", ".psd")
GRADE_EXT = (".docx", ".xlsx", ".pptx", ".accdb", ".psd")

def vapp_exe(vapp):
    return {"edge": "Edge.exe", "win10": "Win10.exe", "email": "Win10_Email.exe"}.get(vapp, "")

def vapp_logfile(vapp):
    return {"edge": "Edge", "win10": "Win10", "email": "Win10_Email"}.get(vapp, "")

def safe_extract_zip(zip_path, dst):
    """解压 zip 到目录。兼容原版工具生成的非标准 zip（central 目录条目用正斜杠、
    local file header 用反斜杠，Python zipfile 校验一致时抛 BadZipFile）：
    优先用系统自带 tar.exe（bsdtar，不做该一致性校验），失败回退 zipfile。"""
    import subprocess
    os.makedirs(dst, exist_ok=True)
    try:
        r = subprocess.run(["tar", "-xf", zip_path, "-C", dst],
                           capture_output=True, timeout=180)
        if r.returncode == 0:
            return True
    except Exception:
        pass
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(dst)
        return True
    except Exception:
        return False

def vapp_of_local(timu_type, stem=""):
    """客户端兜底推断虚拟程序类型（与服务端 vapp_of 保持一致）：
    题干含邮箱关键词 → email；type32 → edge；type 11/12/31 → win10。"""
    d = stem or ""
    if ("邮箱" in d) or ("qq.com" in d) or ("邮件" in d and "收件" in d):
        return "email"
    if timu_type == 32:
        return "edge"
    if timu_type in (11, 12, 31):
        return "win10"
    return ""

def vapp_runtime_dir(qid):
    """该题的虚拟程序运行目录（%TEMP%/ttlx_vapp/<qid>/），exe 与判分文件都在这里"""
    d = os.path.join(VAPP_TEMP_ROOT, str(qid))
    os.makedirs(d, exist_ok=True)
    return d

def _find_pycharm():
    """在 PATH 与常见安装目录找 PyCharm；找不到返回 None（回退系统默认关联打开）。"""
    import glob as _glob
    import shutil as _shutil
    for name in ("pycharm64.exe", "pycharm.exe", "pycharm"):
        p = _shutil.which(name)
        if p:
            return p
    patterns = [
        r"C:\Program Files\JetBrains\PyCharm*\bin\pycharm64.exe",
        r"C:\Program Files (x86)\JetBrains\PyCharm*\bin\pycharm64.exe",
        r"D:\Program Files\JetBrains\PyCharm*\bin\pycharm64.exe",
        os.path.expanduser(r"~\AppData\Local\JetBrains\PyCharm*\bin\pycharm64.exe"),
        os.path.expanduser(r"~\AppData\Local\Programs\PyCharm*\bin\pycharm64.exe"),
    ]
    for pat in patterns:
        for p in _glob.glob(pat):
            if os.path.isfile(p):
                return p
    return None

def ensure_vapp_exe(qid, vapp):
    """确保虚拟程序 exe 就位：优先从 common/vapp/vapp_exes.zip 解压到临时目录；
    zip 不存在时复制随包 exe；都没有返回空串。"""
    exe = vapp_exe(vapp)
    if not exe:
        return ""
    target = os.path.join(vapp_runtime_dir(qid), exe)
    if os.path.exists(target):
        return target
    zip_path = os.path.join(VAPP_DIR, "vapp_exes.zip")
    if os.path.isfile(zip_path):
        try:
            with zipfile.ZipFile(zip_path) as z:
                if exe in z.namelist():
                    with z.open(exe) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    return target
        except Exception as e:
            print("vapp unzip failed:", e)
    # 兜底：复制随包 exe（保证判分文件也落在临时目录，与题隔离）
    src = os.path.join(VAPP_DIR, exe)
    if os.path.isfile(src):
        shutil.copy2(src, target)
        return target
    return ""

class NavBox(QFrame):
    """左侧导航题号方框：点击跳转到对应题"""
    clicked = Signal(int)
    def __init__(self, no, idx, parent=None):
        super().__init__(parent)
        self._idx = idx
        self.setFixedSize(38, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameShape(QFrame.StyledPanel)
        self._lab = QLabel(str(no), self)
        self._lab.setAlignment(Qt.AlignCenter)
        self._lab.setGeometry(0, 0, 38, 34)
        self._lab.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
    def mousePressEvent(self, e):
        self.clicked.emit(self._idx)


class MiniBar(QWidget):
    """最小化后的右下角悬浮条：仅保留一行标题栏，带“恢复”按钮"""
    restore = Signal()
    def __init__(self, title, parent=None):
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setFixedSize(300, 42)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 8, 0)
        lay.setSpacing(8)
        self.lab = QLabel("答题中 · " + title)
        self.lab.setStyleSheet("color:#FFFFFF; font-size:12px; font-weight:bold;")
        lay.addWidget(self.lab)
        lay.addStretch()
        self.btn = QPushButton("恢复")
        self.btn.setFixedSize(64, 28)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setStyleSheet(
            "QPushButton{background:#4F86F8;color:white;border:none;border-radius:6px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#3B72E4;}")
        self.btn.clicked.connect(lambda: self.restore.emit())
        lay.addWidget(self.btn)
        self.setStyleSheet("MiniBar{background:#2D3748;border-radius:8px;}")

    def show_at_bottom_right(self):
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 20, screen.bottom() - self.height() - 80)
        self.show()
        self.raise_()

class ExamWindow(QWidget):
    # 检测线程完成后跨线程通知主线程（工作线程无事件循环，QTimer 在那里永不触发）
    mysqlDetectDone = Signal(dict, dict)

    def __init__(self, net, record_id, title, questions, practice=False, duration_sec=0,
                 submit_cmd=None, on_exit=None):
        super().__init__()
        self.net = net
        self.record_id = record_id
        self.title = title
        self.qs = questions
        self.practice = practice
        self.duration_sec = duration_sec
        self.submit_cmd = submit_cmd or ("submitPractice" if practice else "submit")
        self.on_exit = on_exit
        self.idx = 0
        self._wait_dlg = None          # 交卷等待界面
        self._submit_thread = None     # 后台交卷线程（必须持有引用，否则被 GC）
        self._submit_worker = None
        self.sel = [-1] * len(questions)
        # 兜底补全虚拟程序标记：错题重做/老服务端下发的操作题可能缺 vapp/type 字段，
        # 缺失时按题型+题干推断（与服务端 vapp_of 一致），否则 vapp 题会误当文件素材题报“没有素材”
        for _q in self.qs:
            # subType=2 文件管理、=6 网页收藏/另存：走考生文件夹/真实 Edge，绝不启虚拟程序
            if self.is_operate(_q) and not _q.get("vapp") and _q.get("type") != 25 \
                    and int(_q.get("subType") or 0) not in (2, 6):
                _t = _q.get("type")
                if _t in (11, 12, 31, 32):
                    _v = vapp_of_local(_t, _q.get("stem", ""))
                    if _v:
                        _q["vapp"] = _v
        self.op_texts = [""] * len(questions)
        self.vapp_logs = {}
        self.vapp_init = {}       # qid -> 虚拟程序启动时的初始判分文件(base64)；用于判断学生是否真正操作过
        self.op_grades = {}
        self.opened_qids = set()
        self._previews = {}       # qid -> [{name,data}] 题目预览图（answer_sc*）
        self._mat_snaps = {}      # qid -> 素材落地后目录快照；学生改动后 _answered 才变绿
        self.material_ready = set()   # 已成功落地素材的 qid，避免重复拉取覆盖作答
        self.vapp_procs = []
        self.work_tag = str(record_id) if record_id > 0 else f"practice_{int(time.time()*1000)}"
        self.op_mainfile = {}   # qid -> 主作答文件
        self.work_dir = self._make_work_dir()  # C:\stu\<随机名>
        self._cleaned = False
        self.setWindowTitle(f"{title} · 答题中")
        # 强制置顶：操作题在虚拟程序/Office 里操作时，题干窗口始终在最上方可见
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.resize(900, 650)
        # 最小化悬浮条：点击最小化后缩到右下角，只留一行标题+恢复按钮
        self._mini = None
        self._minimizing = False
        # 实时状态心跳：后台线程每 5 秒上报当前题/已答/错题，供教师端监控
        self._hb_stop = threading.Event()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._hb_thread.start()
        self._build_ui()
        if duration_sec > 0:
            self.remain = duration_sec
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._tick)
            self.timer.start(1000)
            self._update_time()
        else:
            self.time_label.setText("练习模式")
            self.time_label.setStyleSheet("color:#888;")
        # MySQL 环境：试卷含 SQL 题（type=24）时检测本机 MySQL，
        # 有则进入 SQL 题时自动配置临时实例；无则弹窗提示并打开官网下载页
        self._sql_env = {}        # qid -> {"port":int,"proc":subprocess.Popen}
        self._sql_env_tried = set()
        self._env_locked = False  # 未安装 MySQL/Navicat 时锁定 SQL 题（答题框只读+交卷警告）
        if any(self.is_operate(q) and q.get("type") == 24 for q in self.qs):
            self.mysqlDetectDone.connect(self._on_mysql_detect)
            QTimer.singleShot(600, self._check_mysql_env)
        # 题目分批加载：>9 题时先显示前 9 题进入答题，其余题目在后台分批渲染到导航
        self._nav_timer = QTimer(self)
        self._nav_timer.timeout.connect(self._append_nav_batch)
        if len(self.qs) > 9:
            self._nav_timer.start(60)
        self.render()

    def _check_mysql_env(self):
        """试卷含 SQL 题时检测 Navicat（MySQL 判分实例使用随包内置的 5.7 运行时，
        无需学生本机安装 MySQL）；Navicat 缺失则弹窗提示并打开官方下载页，
        同时锁定全部 SQL 题（交卷时警告）。"""
        import threading

        def _work():
            try:
                navi = mysql_env.detect_navicat()
            except Exception as e:
                navi = {"found": False, "error": str(e)}
            # 内置 MySQL 5.7 运行时恒可用（随包分发），无需检测本机 MySQL
            self.mysqlDetectDone.emit({"found": True, "bundled": True}, navi)

        threading.Thread(target=_work, daemon=True).start()

    def _on_mysql_detect(self, det, navi):
        import webbrowser
        navi_ok = bool(navi.get("found"))
        if navi_ok:
            return  # Navicat 已安装：进入 SQL 题时自动配置环境并打开 Navicat
        err = navi.get("error") or ""
        msgs = ["未检测到 Navicat for MySQL（绿色图标）。\n请安装 Navicat（推荐 Navicat for MySQL "
                "或 Navicat Premium），即将自动打开 Navicat 官方下载页。"
                + (f"\n（检测异常：{err}）" if err else "")]
        self._env_locked = True
        QMessageBox.warning(
            self, "MySQL 答题环境未就绪",
            "\n\n".join(msgs) +
            "\n\n数据库实例使用随包内置的 MySQL 5.7，无需本机安装 MySQL；"
            "但作答需要 Navicat 连接 localhost / 3306 / root（密码留空）。\n"
            "你仍可点击下方按钮打开考试文件夹，查看素材/模板并提前保存 tj*.sql 文件；\n"
            "安装 Navicat 后**重新打开本窗口**即可正常作答。")
        try:
            webbrowser.open(mysql_env.NAVICAT_DOWNLOAD_URL)
        except Exception:
            pass
        # 检测在首次 render 之后才完成：刷新当前题，让锁定状态真正落到界面上
        # （否则答题框保持可编辑，检测形同虚设）
        try:
            self.render()
        except Exception:
            pass

    def _open_sql_material_dir(self):
        """打开 MySQL 题素材/模板文件夹（含 datadir 与 触发器.txt 等模板）。"""
        q = self.qs[self.idx]
        qid = str(q.get("qid", ""))
        d = self.op_dir(qid)
        os.makedirs(d, exist_ok=True)
        if not os.listdir(d):
            InfoBar.info("素材准备中", "本题素材/模板正在下载，请几秒后重试。",
                         duration=2500, position=InfoBarPosition.TOP, parent=self)
            return
        try:
            os.startfile(d)
        except Exception as e:
            QMessageBox.information(self, "素材文件夹", f"素材目录：\n{d}\n\n（自动打开失败：{e}）")

    def _mysql_log(self, msg):
        """MySQL 判分链路诊断日志：C:\\stu\\logs\\mysql.log（每次交卷必写，
        用于定位"MySQL 题 0 分/未判分"的真实原因，用户可把该文件发给管理员）。"""
        try:
            _d = os.path.join(os.path.dirname(self.work_dir), "logs")
            os.makedirs(_d, exist_ok=True)
            with open(os.path.join(_d, "mysql.log"), "a", encoding="utf-8") as _f:
                _f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        except Exception:
            pass

    def _setup_sql_env(self, qid):
        """进入 SQL 题时配置环境：
        ① 无条件拉取该题素材 zip（MySQL 数据目录快照 + 触发器.txt 等模板）并解压，
           学生随时可查看/补全模板，不依赖本机是否安装 MySQL；
        ② 检测到本机 MySQL 才用 mysqld 起临时实例（3306，root 免密），供 Navicat 连接。"""
        import threading

        def _work():
            note = None
            port = None
            try:
                r = self.net.call("getMaterialZip", timuID=qid, timeout=120)
                if r.get("code") != 0 or not r.get("zip"):
                    note = "素材包获取失败：" + str(r.get("msg", ""))
                    self._mysql_log("进入题 %s: getMaterialZip 失败 code=%s msg=%s"
                                    % (qid, r.get("code"), r.get("msg")))
                else:
                    d = self.op_dir(qid)
                    zp = os.path.join(d, "material.zip")
                    with open(zp, "wb") as fh:
                        fh.write(base64.b64decode(r["zip"]))
                    if not safe_extract_zip(zp, d):
                        note = "素材包解压失败（zip 格式异常）"
                        self._mysql_log("进入题 %s: 素材包解压失败" % qid)
                    else:
                        self._mysql_log("进入题 %s: 素材解压成功，文件夹文件数=%d"
                                        % (qid, sum(len(fs) for _, _, fs in os.walk(d))))
                    # 素材已落地；用内置 MySQL 5.7 或本机 MySQL 起临时实例
                    det = mysql_env.detect_mysql()
                    if det.get("found"):
                        port, proc, err, stopped_svc = mysql_env.start_temp_instance(d)
                        if err:
                            note = err
                            self._mysql_log("进入题 %s: 起临时实例失败 err=%s" % (qid, err))
                        else:
                            self._sql_env[qid] = {"port": port, "proc": proc, "service": stopped_svc}
                            self._mysql_log("进入题 %s: 临时实例已就绪 port=%s" % (qid, port))
                    else:
                        note = "未找到可用的 MySQL 运行时，数据库实例未启动（素材与模板已下载，可先查看模板）。"
                        self._mysql_log("进入题 %s: detect_mysql 未找到运行时 det=%s" % (qid, det))
            except Exception as e:
                note = "配置 MySQL 环境失败：%s" % e
                self._mysql_log("进入题 %s: 配置环境异常 %s" % (qid, e))
            # 回主线程必须带 context：工作线程没有事件循环，
            # QTimer.singleShot(0, cb) 简写形式的回调不会触发（踩坑记录同 waiting.py）
            QTimer.singleShot(0, self, lambda: self._on_sql_env_ready(qid, note, port))

        threading.Thread(target=_work, daemon=True).start()

    def _on_sql_env_ready(self, qid, note, port):
        if note:
            # 未装 MySQL 的安装提示已在进入窗口时弹过，这里不重复弹窗；
            # 仅其它真实失败（起实例失败等）才提示
            if not self._env_locked or "未检测到本机 MySQL" not in note:
                QMessageBox.warning(
                    self, "MySQL 环境配置失败",
                    note + "\n\n可手动启动本机 MySQL 后用 Navicat 连接 localhost 做题；"
                           "若本机 MySQL 版本不是 5.7，题库数据库快照可能不兼容，建议安装 MySQL 5.7。")
            return
        # 实例已就绪：自动打开 Navicat（本机已装时）
        navi_ok, navi_msg = mysql_env.start_navicat()
        port_txt = port if port else 3306
        msg = (f"本题数据库已加载到临时 MySQL 实例：\n\n"
               f"主机：localhost\n端口：{port_txt}\n"
               f"用户名：root（免密）\n\n")
        if port_txt != 3306:
            msg += (f"注意：题面端口 3306 被本机程序占用，本次已改用端口 {port_txt}，"
                    f"请在 Navicat 连接中填写 {port_txt} 端口（不要按题面写 3306）。\n\n")
        if navi_ok:
            msg += f"已自动打开 Navicat，请新建连接填写 localhost / {port_txt} / root（密码留空）后作答。\n"
        else:
            msg += f"自动打开 Navicat 失败（{navi_msg}），请手动打开 Navicat 连接 localhost / {port_txt} / root（密码留空）。\n"
        msg += (f"① 建视图/触发器/存储过程/改表等操作直接在数据库中完成；\n"
                f"② 题面要求保存的 tj*.sql 文件，请保存到本题考试文件夹：\n"
                f"{self.op_dir(qid)}\n"
                f"（点答题区下方按钮可直接打开该文件夹）\n\n"
                f"完成后回到本窗口交卷，系统自动打包整个考试文件夹判分。")
        QMessageBox.information(self, "MySQL 环境已就绪", msg)

    def _stop_sql_env(self):
        """关闭考试窗口时清理临时 MySQL 实例，并恢复被停止的本机 MySQL 服务。"""
        for e in self._sql_env.values():
            try:
                e["proc"].terminate()
            except Exception:
                pass
            if e.get("service"):
                mysql_env.restore_mysql_service(e["service"])
        self._sql_env.clear()

    def _shutdown_sql_env(self, qid):
        """交卷前优雅关停该题临时 MySQL 实例：优先 SQL SHUTDOWN（干净落盘，
        保证 datadir 打包后服务端实例可直接加载），失败再 terminate。
        结束后恢复被停止的本机 MySQL 服务。"""
        import subprocess
        e = self._sql_env.pop(qid, None)
        if not e:
            return
        port = e.get("port") or 3306
        try:
            mysql_exe = mysql_env.find_mysql_exe()
            if mysql_exe:
                subprocess.run([mysql_exe, "-h127.0.0.1", "-P%d" % port, "-uroot", "-e", "SHUTDOWN"],
                               capture_output=True, timeout=15)
        except Exception:
            pass
        proc = e.get("proc")
        if proc:
            try:
                proc.wait(timeout=20)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:
                    pass
        # 等实例端口真正释放、ibdata 解锁
        for _ in range(20):
            if not mysql_env.port_open(port):
                break
            time.sleep(0.5)
        # 恢复被停止的本机 MySQL 服务
        if e.get("service"):
            mysql_env.restore_mysql_service(e["service"])

    # ---------------- Python 题：本地 .py 文件作答 ----------------
    def _py_file(self, qid):
        return os.path.join(self.op_dir(str(qid)), "answer.py")

    def _py_template(self, q):
        return (q.get("stem") or "").replace("\r\n", "\n").replace("\r", "\n")

    def _ensure_py_file(self, qid, q):
        """把题干中的 .py 模板（含 Program/End 标志）落到考生文件夹 answer.py。"""
        p = self._py_file(qid)
        if os.path.exists(p):
            return p
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self._py_template(q))
        except Exception as e:
            print("[py] write template failed:", e)
        return p

    def _trust_pycharm_dirs(self, project_dir=None):
        r"""把当前题目目录（及 C:\stu 根）加入 PyCharm 受信任项目列表，避免弹
        「是否信任此项目」窗口。
        新版 PyCharm(2022.2+) 实际读取 options/trusted-paths.xml（连字符），
        格式为 Trusted.Paths / TRUSTED_PROJECT_PATHS / map<entry key=路径 value=true>，
        且按项目精确路径匹配（PyCharm 自己记录的均为具体项目目录）。
        同时兼容旧版点号文件名（trusted.paths.xml / trusted.projects.xml）。"""
        import glob
        import xml.etree.ElementTree as ET
        entries = set()
        if project_dir:
            entries.add(project_dir.replace("/", "\\"))
        entries.add(r"C:\stu")
        base = os.path.expanduser(r"~\AppData\Roaming\JetBrains")
        if not os.path.isdir(base):
            return
        for cfg in glob.glob(os.path.join(base, "PyCharm*", "options")):
            if not os.path.isdir(cfg):
                continue
            # ---- 主格式：trusted-paths.xml（连字符，新版本读取）----
            fp = os.path.join(cfg, "trusted-paths.xml")
            try:
                if os.path.exists(fp):
                    tree = ET.parse(fp)
                    root = tree.getroot()
                else:
                    root = ET.Element("application")
                    tree = ET.ElementTree(root)
                comp_el = None
                for c in root.findall("component"):
                    if c.get("name") == "Trusted.Paths":
                        comp_el = c
                        break
                if comp_el is None:
                    comp_el = ET.SubElement(root, "component")
                    comp_el.set("name", "Trusted.Paths")
                opt = None
                for o in comp_el.findall("option"):
                    if o.get("name") == "TRUSTED_PROJECT_PATHS":
                        opt = o
                        break
                if opt is None:
                    opt = ET.SubElement(comp_el, "option")
                    opt.set("name", "TRUSTED_PROJECT_PATHS")
                mp = None
                for m in opt.findall("map"):
                    mp = m
                    break
                if mp is None:
                    mp = ET.SubElement(opt, "map")
                existing = {e.get("key") for e in mp.findall("entry")}
                changed = False
                for k in entries:
                    if k not in existing:
                        en = ET.SubElement(mp, "entry")
                        en.set("key", k)
                        en.set("value", "true")
                        changed = True
                if changed:
                    xml_bytes = ET.tostring(root, encoding="utf-8")
                    with open(fp, "wb") as f:
                        f.write(b"\xef\xbb\xbf" + xml_bytes)
            except Exception as e:
                print("[trust] 写入 trusted-paths.xml 失败: %r" % (e,))
            # ---- 兼容旧格式（点号文件名，老版本读取）----
            for fname, comp, key in (
                ("trusted.paths.xml", "Trusted.Paths", "trustedPaths"),
                ("trusted.projects.xml", "Trusted.Projects", "trustedProjects"),
            ):
                fp2 = os.path.join(cfg, fname)
                try:
                    if os.path.exists(fp2):
                        tree2 = ET.parse(fp2)
                        root2 = tree2.getroot()
                    else:
                        root2 = ET.Element("application")
                        tree2 = ET.ElementTree(root2)
                    comp_el2 = None
                    for c in root2.findall("component"):
                        if c.get("name") == comp:
                            comp_el2 = c
                            break
                    if comp_el2 is None:
                        comp_el2 = ET.SubElement(root2, "component")
                        comp_el2.set("name", comp)
                    opt2 = None
                    for o in comp_el2.findall("option"):
                        if o.get("name") == key:
                            opt2 = o
                            break
                    if opt2 is None:
                        opt2 = ET.SubElement(comp_el2, "option")
                        opt2.set("name", key)
                    lst2 = None
                    for l in opt2.findall("list"):
                        lst2 = l
                        break
                    if lst2 is None:
                        lst2 = ET.SubElement(opt2, "list")
                    vals = {v.get("value") for v in lst2.findall("option")}
                    changed2 = False
                    for k in entries:
                        kk = k.replace("\\", "/")
                        if kk not in vals:
                            v = ET.SubElement(lst2, "option")
                            v.set("value", kk)
                            changed2 = True
                    if changed2:
                        xml_bytes = ET.tostring(root2, encoding="utf-8")
                        with open(fp2, "wb") as f:
                            f.write(b"\xef\xbb\xbf" + xml_bytes)
                except Exception as e:
                    print("[trust] 写入 %s 失败: %r" % (fname, e))

    def _open_py_file(self):
        """用 PyCharm 以项目方式打开本题考生文件夹（文件夹内 answer.py 为作答文件）。"""
        q = self.qs[self.idx]
        qid = str(q.get("qid", ""))
        p = self._ensure_py_file(qid, q)
        d = self.op_dir(str(qid))
        # 先把考生工作根目录加入受信任项目列表，避免 PyCharm 弹“信任此项目”窗口
        self._trust_pycharm_dirs(d)
        editor = _find_pycharm()
        opened = False
        if editor:
            try:
                import subprocess
                # 以项目方式打开考生文件夹：PyCharm 会把该目录作为项目根，
                # 学生在项目树中打开 answer.py 编辑保存
                subprocess.Popen([editor, d, p])
                opened = True
            except Exception:
                opened = False
        if not opened:
            try:
                os.startfile(d)
                opened = True
            except Exception as e:
                QMessageBox.warning(self, "提示", f"打开代码项目失败：{e}\n文件夹位置：{d}")
                return
        QMessageBox.information(
            self, "Python 作答说明",
            f"已用 PyCharm 打开本题项目并自动打开 answer.py：\n{p}\n\n"
            "仅在其 #**********Program********** 与 #**********End**************\n"
            "标志之间填写代码；不要删除标志、不要改动标志之外的源程序（题面要求，否则不得分）。\n\n"
            "用 input() 读取输入、print() 输出结果；写完保存后回到本窗口交卷，自动判分。")


    def _build_ui(self):
        # 外层：左侧题目导航 + 右侧主答题区
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 左侧题目导航：按模块分组，方框显示题号，绿=已答 白=未答，点击跳转
        self.nav_panel = QWidget()
        self.nav_panel.setFixedWidth(152)
        nv = QVBoxLayout(self.nav_panel)
        nv.setContentsMargins(8, 12, 8, 12)
        nv.setSpacing(6)
        head = CaptionLabel("题目导航")
        head.setStyleSheet("color:#4A5568; font-weight:bold;")
        nv.addWidget(head)
        self.nav_scroll = QScrollArea()
        self.nav_scroll.setWidgetResizable(True)
        self.nav_scroll.setFrameShape(QFrame.NoFrame)
        self.nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_body = QWidget()
        self.nav_layout = QVBoxLayout(self.nav_body)
        self.nav_layout.setContentsMargins(0, 2, 0, 2)
        self.nav_layout.setSpacing(10)
        self.nav_scroll.setWidget(self.nav_body)
        nv.addWidget(self.nav_scroll, 1)
        # 侧边栏折叠按钮：点一下展示题目导航，再点一下缩到旁边（醒目样式）
        self.nav_toggle = PushButton("题\n目\n导\n航")
        self.nav_toggle.setFixedWidth(46)
        self.nav_toggle.setToolTip("点击展开/收起题目导航")
        self.nav_toggle.setStyleSheet(
            "QPushButton{border:none;background:#3B6FE0;color:white;font-size:12px;font-weight:bold;"
            "border-top-right-radius:8px;border-bottom-right-radius:8px;}"
            "QPushButton:hover{background:#2E5BC9;}"
            "QPushButton:pressed{background:#2750B0;}")
        self.nav_toggle.clicked.connect(self._toggle_nav)
        outer.addWidget(self.nav_toggle)
        outer.addWidget(self.nav_panel)

        # 右侧主区
        self.main_panel = QWidget()
        layout = QVBoxLayout(self.main_panel)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.addWidget(StrongBodyLabel(self.title))
        top.addStretch()
        self.time_label = BodyLabel("")
        self.time_label.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        top.addWidget(self.time_label)
        layout.addLayout(top)

        # 题干
        self.stem_box = QTextEdit()
        self.stem_box.setReadOnly(True)
        self.stem_box.setFont(QFont("Microsoft YaHei UI", 10.5))
        self.stem_box.setStyleSheet("border:1px solid #ddd; border-radius:6px; padding:8px; background:#fff;")
        layout.addWidget(self.stem_box, 2)

        # 选择题选项
        self.option_widget = QWidget()
        self.option_layout = QVBoxLayout(self.option_widget)
        self.option_layout.setContentsMargins(10, 0, 10, 0)
        self.option_layout.setSpacing(6)
        self.rb_group = QButtonGroup(self)
        self.rbs = []
        for i in range(6):
            rb = QRadioButton()
            rb.setFont(QFont("Microsoft YaHei UI", 10.5))
            rb.setVisible(False)
            self.rb_group.addButton(rb, i)
            self.option_layout.addWidget(rb)
            self.rbs.append(rb)
        self.rb_group.buttonClicked.connect(self._on_rb)
        layout.addWidget(self.option_widget, 1)

        # 操作题作答区
        self.op_widget = QWidget()
        op_layout = QVBoxLayout(self.op_widget)
        op_layout.setContentsMargins(10, 0, 10, 0)
        op_layout.setSpacing(8)
        self.op_text = QTextEdit()
        self.op_text.setPlaceholderText("可在此填写操作说明或代码...")
        self.op_text.setFont(QFont("Microsoft YaHei UI", 10))
        self.op_text.textChanged.connect(self._on_op_text_changed)
        op_layout.addWidget(self.op_text, 1)
        # 题目预览图（素材目录里的 answer_sc* 图，如标准答案效果图）：下载后在题目右侧显示
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedHeight(150)
        self.preview_label.setVisible(False)
        op_layout.addWidget(self.preview_label)
        op_btn_row = QHBoxLayout()
        self.open_mat_btn = PrimaryPushButton("打开素材文件")
        self.open_mat_btn.clicked.connect(self.open_material)
        op_btn_row.addWidget(self.open_mat_btn)
        self.sql_mat_btn = PushButton("打开考试文件夹（素材/保存tj*.sql）")
        self.sql_mat_btn.clicked.connect(self._open_sql_material_dir)
        op_btn_row.addWidget(self.sql_mat_btn)
        self.upload_btn = PushButton("上传结果文件（自动判分）")
        self.upload_btn.clicked.connect(self.upload_file)
        op_btn_row.addWidget(self.upload_btn)
        op_btn_row.addStretch()
        op_layout.addLayout(op_btn_row)
        layout.addWidget(self.op_widget, 1)

        # 底部导航
        bottom = QHBoxLayout()
        self.prev_btn = PushButton("上一题")
        self.prev_btn.setFixedWidth(110)
        self.prev_btn.clicked.connect(self.prev)
        bottom.addWidget(self.prev_btn)
        bottom.addStretch()
        self.qno_label = CaptionLabel("")
        bottom.addWidget(self.qno_label)
        bottom.addStretch()
        self.submit_btn = PushButton("交卷")
        self.submit_btn.setFixedWidth(90)
        self.submit_btn.setStyleSheet("background:#FF5252; color:white;")
        self.submit_btn.clicked.connect(self.submit)
        bottom.addWidget(self.submit_btn)
        self.next_btn = PrimaryPushButton("下一题")
        self.next_btn.setFixedWidth(110)
        self.next_btn.clicked.connect(self.next)
        bottom.addWidget(self.next_btn)
        layout.addLayout(bottom)
        outer.addWidget(self.main_panel, 1)

        self._build_nav(limit=9)   # 先显示前 9 题，>9 题时其余分批加载

    def _tick(self):
        self.remain -= 1
        self._update_time()
        if self.remain <= 0:
            self.timer.stop()
            QMessageBox.information(self, "提示", "时间到，自动交卷！")
            self.submit()

    def _update_time(self):
        m, s = divmod(max(0, self.remain), 60)
        self.time_label.setText(f"⏱ {m:02d}:{s:02d}")
        self.time_label.setStyleSheet("color:#FF5252;" if self.remain < 60 else "color:#4F86F8;")

    def _on_rb(self, btn):
        self.sel[self.idx] = self.rb_group.id(btn)
        self._refresh_nav()

    def is_operate(self, q):
        if q.get("refType") == "operate":
            return True
        return len(q.get("options", [])) == 0

    def format_stem(self, raw):
        return format_operate_stem(raw)

    # ---------------- 左侧题目导航 ----------------
    OP_NAV_NAMES = {"word":"Word文字处理","excel":"Excel电子表格","ppt":"PPT演示文稿",
        "mysql":"MySQL数据库","photoshop":"Photoshop","browser":"浏览器操作",
        "win10":"Windows系统设置","win10net":"Windows网络设置","email":"QQ邮箱设置",
        "file":"文件操作","python":"Python编程","sql":"SQL数据库"}
    COURSE_NAV_NAMES = {1:"模块1",2:"模块2",3:"模块3",4:"模块4",5:"模块5",
                        6:"模块6",7:"模块7",8:"模块8",9:"模块9"}

    def _toggle_nav(self):
        """侧边栏按钮：展示/缩回左侧题目导航面板"""
        vis = self.nav_panel.isVisible()
        self.nav_panel.setVisible(not vis)
        self.nav_toggle.setText("▶" if vis else "题\n目\n导\n航")
        self.nav_toggle.setToolTip("展开题目导航" if vis else "收起题目导航")

    def _q_group(self, q):
        if not self.is_operate(q):
            c = int(q.get("course") or 0)
            return "选择题·" + (self.COURSE_NAV_NAMES.get(c) or ("模块" + str(c)))
        m = q.get("module") or ""
        return self.OP_NAV_NAMES.get(m, "操作题")

    def _snap_dir(self, d):
        """对考生目录做内容快照（相对路径 -> md5），用于判断文件是否被学生改动过。"""
        import hashlib as _hl
        snap = {}
        try:
            for root, _ds, files in os.walk(d):
                for fn in files:
                    low = fn.lower()
                    if low.startswith("~$") or low.endswith((".tmp", ".temp", ".keep")):
                        continue
                    if low == "material.zip":
                        continue
                    p = os.path.join(root, fn)
                    rel = os.path.relpath(p, d)
                    try:
                        with open(p, "rb") as f:
                            snap[rel] = _hl.md5(f.read()).hexdigest()
                    except Exception:
                        pass
        except Exception:
            pass
        return snap

    def _mat_changed(self, qid, d):
        """普通文件操作题：素材快照变化（学生保存/新建文件）才算已答。"""
        if not os.path.isdir(d):
            return False
        base = self._mat_snaps.get(qid)
        cur = self._snap_dir(d)
        if base is None:
            # 没记录到初始快照：目录里有非素材文件（学生新建的结果文件）视为已答
            return any(k for k in cur if not os.path.basename(k).lower().startswith("material"))
        return cur != base

    def _vapp_answered(self, qid):
        """虚拟程序题：判分文件内容与启动时的初始状态不同（真正操作过）才算已答。"""
        if qid not in self.vapp_logs:
            return False
        init = self.vapp_init.get(qid)
        if init is None:
            return False   # 初始状态没记录到 → 不标已答（判分完成后由 op_grades 变绿）
        return self.vapp_logs[qid] != init

    def _answered(self, i):
        q = self.qs[i]
        if not self.is_operate(q):
            return self.sel[i] >= 0
        qid = str(q.get("qid", ""))
        if qid in self.op_grades:      # 已判分 / 判分中 → 已答
            return True
        if q.get("vapp"):              # 虚拟程序题：操作记录变化才算已答
            return self._vapp_answered(qid)
        # Python 题：answer.py 被学生改过（不同于模板）视为已答
        if q.get("type") == 25:
            p = self._py_file(qid)
            try:
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        cur = f.read().decode("utf-8-sig", "replace").strip()
                    return cur != self._py_template(q).strip()
            except Exception:
                return False
            return False
        # MySQL 题：考试文件夹根级出现学生保存的 .sql 文件视为已答
        if q.get("type") == 24:
            d = os.path.join(self.work_dir, qid)
            try:
                return any(fn.lower().endswith(".sql") for fn in os.listdir(d))
            except Exception:
                return False
        # 普通文件操作题（word/excel/ppt/psd/文件管理）：素材被改动或新建了结果文件才算已答
        return self._mat_changed(qid, os.path.join(self.work_dir, qid))

    def _build_nav(self, limit=None):
        """按模块分组生成题号方框导航（方框点击跳转）。
        支持分批：limit=None 一次性建全部；limit=N 只建前 N 题，
        剩余题目由 _append_nav_batch 分批追加（>9 题时先显示前 9 题，其余慢慢加载）。"""
        self.nav_boxes = []
        self._nav_built = 0
        self._nav_groups = {}     # 组名 -> (CaptionLabel, 容器QWidget, QGridLayout)
        self._nav_cnt = {}        # 组名 -> 已放题数
        # 加载进度提示（>9 题时显示"题目加载中… x/N"）
        self._nav_prog = CaptionLabel("")
        self._nav_prog.setStyleSheet("color:#718096;")
        self.nav_layout.insertWidget(0, self._nav_prog)
        end = len(self.qs) if limit is None else min(limit, len(self.qs))
        for i in range(end):
            self._nav_add_box(i)
        self._refresh_nav()
        self._nav_update_prog()

    def _nav_add_box(self, i):
        """把第 i 题加入导航（组不存在则新建组标题与格子，插到末尾弹性空白之前）"""
        q = self.qs[i]
        g = self._q_group(q)
        if g not in self._nav_groups:
            lb = CaptionLabel(g)
            lb.setStyleSheet("color:#4F86F8; font-weight:bold;")
            pos = self.nav_layout.count() - 1
            if pos < 0:
                pos = 0
            self.nav_layout.insertWidget(pos, lb)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(6)
            wc = QWidget()
            wc.setLayout(grid)
            pos = self.nav_layout.count() - 1
            self.nav_layout.insertWidget(pos, wc)
            self._nav_groups[g] = (lb, wc, grid)
            self._nav_cnt[g] = 0
        lb, wc, grid = self._nav_groups[g]
        k = self._nav_cnt[g]
        box = NavBox(q.get("no") or (i + 1), i)
        box.clicked.connect(self.jump_to)
        grid.addWidget(box, k // 3, k % 3)
        self.nav_boxes.append((i, box, box._lab))
        self._nav_cnt[g] = k + 1
        self._nav_built = i + 1

    def _append_nav_batch(self):
        """分批追加导航题号框（QTimer 驱动）：每次追加一批，全部完成后停止"""
        start = self._nav_built
        end = min(start + 6, len(self.qs))
        for i in range(start, end):
            self._nav_add_box(i)
        self._refresh_nav()
        self._nav_update_prog()
        if self._nav_built >= len(self.qs):
            try:
                self._nav_timer.stop()
            except Exception:
                pass
            self._nav_prog.setText("")

    def _nav_update_prog(self):
        if len(self.qs) > 9 and self._nav_built < len(self.qs):
            self._nav_prog.setText("题目加载中… %d/%d" % (self._nav_built, len(self.qs)))
        else:
            self._nav_prog.setText("")

    def _refresh_nav(self):
        """刷新题号方框颜色：当前题蓝框、已答绿、未答白"""
        for i, box, lab in self.nav_boxes:
            cur = (i == self.idx)
            ans = self._answered(i)
            if cur:
                box.setStyleSheet("QFrame{border:2px solid #4F86F8; border-radius:6px; background:#EAF1FE;}")
                lab.setStyleSheet("color:#2D5FE8;")
            elif ans:
                box.setStyleSheet("QFrame{border:1px solid #27AE60; border-radius:6px; background:#27AE60;}")
                lab.setStyleSheet("color:white;")
            else:
                box.setStyleSheet("QFrame{border:1px solid #B8C4D0; border-radius:6px; background:white;}")
                lab.setStyleSheet("color:#2D3748;")

    def _close_question_apps(self):
        """切题时关闭上一题打开的窗口，只保留当前答题窗口。
        虚拟程序进程精确回收；MySQL 临时实例优雅关停（回到该题会重新配置）；
        外部程序（Navicat/PyCharm/Office/PS）按进程名关闭。"""
        # 1) 虚拟程序进程
        for p in self.vapp_procs:
            try:
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(1.5)
                    except Exception:
                        pass
                    if p.poll() is None:
                        try:
                            p.kill()
                        except Exception:
                            pass
            except Exception:
                pass
        self.vapp_procs = []
        q = self.qs[self.idx] if 0 <= self.idx < len(self.qs) else None
        if not q:
            return
        qid = str(q.get("qid", ""))
        t = q.get("type")
        if q.get("vapp"):
            return   # 虚拟程序已回收，无外部程序
        import subprocess
        names = set()
        if t == 25:
            names |= {"pycharm64.exe", "pycharm.exe"}
        elif t == 24:
            names |= {"navicat.exe", "navicat16.exe", "navicat15.exe", "navicat.exe"}
            # 关掉本机 MySQL 临时实例，回到该题时 _setup_sql_env 会重新配置
            try:
                self._shutdown_sql_env(qid)
            except Exception:
                pass
            self._sql_env_tried.discard(qid)
        elif t in (13, 14, 15, 43, 44, 45, 18):
            names |= {"winword.exe", "wps.exe", "et.exe", "wpp.exe",
                      "EXCEL.EXE", "POWERPNT.EXE", "Photoshop.exe", "photoshop.exe"}
        for n in names:
            try:
                subprocess.run(["taskkill", "/IM", n, "/F"],
                               timeout=5, capture_output=True)
            except Exception:
                pass

    def jump_to(self, i):
        if i == self.idx:
            return
        self._save_op_text()
        self._capture_vapp()
        self._close_question_apps()
        self.idx = i
        self.render()

    def _on_op_text_changed(self):
        self._refresh_nav()

    def _show_preview(self, qid):
        """在题目右侧显示素材目录里的预览图（answer_sc*，标准答案效果图）。"""
        try:
            _pv = self._previews.get(str(qid))
            if not _pv:
                self.preview_label.setVisible(False)
                return
            from PySide6.QtGui import QPixmap
            _img = QPixmap()
            for _it in _pv:
                _d = _it.get("data") or ""
                if _d:
                    if _img.loadFromData(base64.b64decode(_d)):
                        break
            if _img.isNull():
                self.preview_label.setVisible(False)
                return
            self.preview_label.setPixmap(_img.scaled(
                self.preview_label.width(), self.preview_label.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.preview_label.setVisible(True)
        except Exception:
            self.preview_label.setVisible(False)

    def render(self):
        q = self.qs[self.idx]
        op = self.is_operate(q)
        vapp = q.get("vapp", "") if op else ""
        is_vapp = bool(vapp)
        is_py = op and q.get("type") == 25
        is_sql = op and q.get("type") == 24

        # 题干（操作题内部自带步骤编号，不再外加题号前缀，避免“1. … 1、…”编号混乱）
        if op:
            self.stem_box.setVisible(True)
            self.stem_box.setPlainText(self.format_stem(q.get('stem', '')))
        else:
            self.stem_box.setVisible(True)
            self.stem_box.setPlainText(f"{self.idx+1}. {q.get('stem','')}")

        # 选项 / 操作区
        self.option_widget.setVisible(not op)
        self.op_widget.setVisible(op)
        if op:
            self.op_text.setPlainText(self.op_texts[self.idx])
            is_py = q.get("type") == 25
            is_sql = q.get("type") == 24
            if is_vapp:
                self.open_mat_btn.setText("重新打开虚拟环境")
                self.open_mat_btn.setVisible(True)
                self.upload_btn.setVisible(False)
                self.op_text.setVisible(False)
            elif is_py or is_sql:
                # 编程/MySQL 题：按题面要求在本地完成——Python 在 PyCharm 中编辑 .py 文件
                # （仅在 Program/End 标志之间填写）；MySQL 用 Navicat 操作数据库并把
                # tj*.sql 保存到考试文件夹。均不提供答题框，交卷自动打包判分。
                self.upload_btn.setVisible(False)
                self.op_text.setVisible(False)
                if is_py:
                    self.sql_mat_btn.setVisible(False)
                    self.open_mat_btn.setText("打开代码文件（PyCharm）")
                    self.open_mat_btn.setVisible(True)
                    self._ensure_py_file(q.get("qid"), q)
                else:
                    self.open_mat_btn.setVisible(False)
                    self.sql_mat_btn.setVisible(True)
                    # SQL 题：自动配置本地 MySQL 临时实例（每题一次，供 Navicat 实操）
                    _sqid = str(q.get("qid", ""))
                    if _sqid and _sqid not in self._sql_env_tried:
                        self._sql_env_tried.add(_sqid)
                        QTimer.singleShot(300, lambda: self._setup_sql_env(_sqid))
            else:
                self.open_mat_btn.setText("打开素材文件")
                self.open_mat_btn.setVisible(True)
                self.upload_btn.setVisible(True)
                # 按要求移除“可在此填写操作说明或代码”输入框：判分按题目要求，
                # 保存文件就保存文件，不让学生在框里输入
                self.op_text.setVisible(False)
            # 自动打开素材（Python/SQL 题无素材，跳过）
            qid = str(q.get("qid", ""))
            if not is_py and not is_sql and qid not in self.opened_qids:
                self.opened_qids.add(qid)
                QTimer.singleShot(300, lambda: self.auto_open_material(qid))
        else:
            opts = q.get("options", [])
            for i in range(6):
                if i < len(opts):
                    o = opts[i]
                    t = o.get("t", "") or ""
                    k = o.get("k", "") or ""
                    self.rbs[i].setText(f"{k}. {t}")
                    self.rbs[i].setVisible(len(t) > 0)
                    self.rbs[i].setChecked(self.sel[self.idx] == i)
                else:
                    self.rbs[i].setVisible(False)
                    self.rbs[i].setChecked(False)

        # 按题型调整题干/作答区比例：编程/MySQL 题作答在本地完成，作答区只剩按钮，
        # 题干给足空间
        self._show_preview(q.get("qid"))
        _lay = self.main_panel.layout()
        if op and (is_py or is_sql):
            _lay.setStretchFactor(self.stem_box, 4)
            _lay.setStretchFactor(self.op_widget, 1)
            _lay.setStretchFactor(self.option_widget, 0)
        elif op:
            _lay.setStretchFactor(self.stem_box, 2)
            _lay.setStretchFactor(self.op_widget, 1)
            _lay.setStretchFactor(self.option_widget, 0)
        else:
            _lay.setStretchFactor(self.stem_box, 2)
            _lay.setStretchFactor(self.option_widget, 1)
            _lay.setStretchFactor(self.op_widget, 0)
        self.qno_label.setText(f"{self.idx+1} / {len(self.qs)}")
        self.prev_btn.setEnabled(self.idx > 0)
        # 最后一题只保留红色“交卷”按钮，避免出现两个交卷
        is_last = self.idx == len(self.qs) - 1
        self.next_btn.setVisible(not is_last)
        self.next_btn.setText("下一题")
        self._refresh_nav()

    def _save_op_text(self):
        """保存当前操作题的文本作答"""
        if self.idx < len(self.op_texts) and self.op_widget.isVisible():
            self.op_texts[self.idx] = self.op_text.toPlainText()

    def prev(self):
        if self.idx > 0:
            self._save_op_text()
            self._capture_vapp()
            self._close_question_apps()
            self.idx -= 1
            self.render()

    def next(self):
        self._save_op_text()
        if self.idx < len(self.qs) - 1:
            self._capture_vapp()
            self._close_question_apps()
            self.idx += 1
            self.render()
        else:
            self._capture_vapp()
            self.submit()

    def _capture_vapp(self):
        try:
            q = self.qs[self.idx]
            vapp = q.get("vapp", "")
            if not vapp:
                return
            fn = vapp_logfile(vapp)
            if not fn:
                return
            qid = str(q.get("qid", ""))
            # 判分文件在虚拟程序运行目录（%TEMP%/ttlx_vapp/<qid>/）生成；兜底旧的随包目录
            for d in (os.path.join(VAPP_TEMP_ROOT, qid), VAPP_DIR):
                path = os.path.join(d, fn)
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        data = f.read()
                    if data:
                        self.vapp_logs[qid] = base64.b64encode(data).decode()
                        # 若启动初始快照还没记录上（2.5s 内切题等极端场景），
                        # 把当前内容记为初始：之后判分文件再变化才算已答
                        self.vapp_init.setdefault(qid, self.vapp_logs[qid])
                        return
        except:
            pass

    def _make_work_dir(self):
        root = STU_ROOT
        try:
            os.makedirs(root, exist_ok=True)
        except Exception:
            # C 盘根无权限时回退到用户目录
            root = os.path.join(os.path.expanduser("~"), "stu_work")
            os.makedirs(root, exist_ok=True)
        d = os.path.join(root, secrets.token_hex(4))
        os.makedirs(d, exist_ok=True)
        return d

    def op_dir(self, qid):
        d = os.path.join(self.work_dir, str(qid))
        os.makedirs(d, exist_ok=True)
        return d

    def _needs_material_folder(self, q):
        """文件管理题（subType=2 或带 material.zip）：需自动解压素材到考生文件夹。"""
        if q.get("hasZip"):
            return True
        if int(q.get("subType") or 0) == 2:
            return True
        return False

    def _write_material_files(self, qdir, files):
        """把 getMaterial 返回的文件写到考生目录；支持相对路径（zip 展开后的目录树）。"""
        first = None
        for f in files:
            name = (f.get("name") or "").replace("\\", "/").lstrip("/")
            if not name or ".." in name.split("/"):
                continue
            # .keep 仅用于建空目录
            if name.endswith("/.keep"):
                os.makedirs(os.path.join(qdir, name[:-6].replace("/", os.sep)), exist_ok=True)
                continue
            p = os.path.join(qdir, name.replace("/", os.sep))
            os.makedirs(os.path.dirname(p) or qdir, exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(base64.b64decode(f["data"]))
            if first is None and name.lower().endswith(OPEN_EXT):
                first = p
        return first

    def _zip_workdir(self, qdir, exclude=None):
        """把考生文件夹打成 zip 字节（相对路径，UTF-8 文件名）。
        exclude：要跳过的文件名集合（仅按文件名匹配，如原始素材 material.zip）。"""
        import io
        exclude = set(exclude or [])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for root, dirs, files in os.walk(qdir):
                for fn in files:
                    if fn == ".keep" or fn in exclude:
                        continue
                    full = os.path.join(root, fn)
                    rel = os.path.relpath(full, qdir).replace("\\", "/")
                    # ZIP UTF-8 flag
                    info = zipfile.ZipInfo(filename=rel)
                    info.flag_bits |= 0x800
                    # 传递 Windows 只读/隐藏属性（zip external_attr DOS 位：bit0=只读 bit1=隐藏）
                    try:
                        _fa = getattr(os.stat(full), "st_file_attributes", 0) or 0
                        _dos = int(_fa) & 0x03
                        if _dos:
                            info.external_attr = (_dos & 0xFFFF) << 16
                    except Exception:
                        pass
                    with open(full, "rb") as fh:
                        z.writestr(info, fh.read())
                # 为每个目录写条目（含 DOS 目录位 0x10 + 只读/隐藏位），
                # 非空目录也写，保证文件夹隐藏/只读属性（hf52 等）能随 zip 传递
                for dname in dirs:
                    dfull = os.path.join(root, dname)
                    rel = os.path.relpath(dfull, qdir).replace("\\", "/") + "/"
                    info = zipfile.ZipInfo(filename=rel)
                    info.flag_bits |= 0x800
                    try:
                        _fa = getattr(os.stat(dfull), "st_file_attributes", 0) or 0
                        _dos = 0x10 | (int(_fa) & 0x03)
                        info.external_attr = (_dos & 0xFFFF) << 16
                    except Exception:
                        info.external_attr = (0x10 & 0xFFFF) << 16
                    z.writestr(info, b"")
        return buf.getvalue()

    def _find_office(self, qdir):
        for root, _dirs, files in os.walk(qdir):
            for fn in sorted(files):
                if fn.lower().endswith(OPEN_EXT) and not fn.startswith("answer_"):
                    return os.path.join(root, fn)
        return None

    def _cleanup_workdir(self):
        """交卷/关窗后删除本次随机工作文件夹 + 各题虚拟程序临时运行目录"""
        if self._cleaned:
            return
        self._cleaned = True
        # 虚拟程序运行目录（%TEMP%/ttlx_vapp/<qid>/）：判分文件已读入 vapp_logs，可安全删除
        try:
            for _q in self.qs:
                _d = os.path.join(VAPP_TEMP_ROOT, str(_q.get("qid", "")))
                if os.path.isdir(_d):
                    shutil.rmtree(_d, ignore_errors=True)
        except Exception as e:
            print("cleanup vapp temp", e)
        d = getattr(self, "work_dir", None)
        if not d or not os.path.isdir(d):
            return
        try:
            import stat as _stat
            # 先清除所有文件的只读属性，否则只读文件会让 rmtree 残留（文件管理题常含只读设置）
            for _r, _ds, _fs in os.walk(d):
                for _fn in _fs:
                    try: os.chmod(os.path.join(_r, _fn), _stat.S_IWRITE)
                    except Exception: pass
            shutil.rmtree(d, ignore_errors=True)
        except Exception as e:
            print("cleanup workdir", e)

    def _record_vapp_init(self, qid):
        """虚拟程序启动 2.5s 后抓一次初始判分文件快照：
        之后 _capture_vapp 抓到内容与初始不同，才判定学生真正操作过（已答变绿）。"""
        try:
            q = self._q_by_id(qid)
            if not q:
                return
            vapp = q.get("vapp", "")
            fn = vapp_logfile(vapp)
            if not fn:
                return
            for d in (os.path.join(VAPP_TEMP_ROOT, str(qid)), VAPP_DIR):
                p = os.path.join(d, fn)
                if os.path.isfile(p):
                    with open(p, "rb") as f:
                        data = f.read()
                    if data and qid not in self.vapp_init:
                        self.vapp_init[qid] = base64.b64encode(data).decode()
                    return
        except Exception:
            pass

    def launch_vapp(self, vapp):
        q = self.qs[self.idx]
        qid = str(q.get("qid", ""))
        if not vapp_exe(vapp):
            return
        path = ensure_vapp_exe(qid, vapp)
        if not path:
            QMessageBox.warning(self, "提示",
                f"找不到虚拟程序 {vapp_exe(vapp)}：\n缺少 common/vapp/vapp_exes.zip\n{VAPP_DIR}")
            return
        rt = os.path.dirname(path)
        # 启动前清掉上次遗留的判分文件，避免 _capture_vapp 抓到旧数据
        try:
            old = os.path.join(rt, vapp_logfile(vapp))
            if os.path.exists(old):
                os.remove(old)
        except Exception:
            pass
        import subprocess
        try:
            # exe 从临时目录启动，cwd=运行目录 → 原版判分文件落在该题独立目录，不串题
            proc = subprocess.Popen([path, "sa@123456"], cwd=rt)
            self.vapp_procs.append(proc)
        except Exception as e:
            QMessageBox.warning(self, "提示", f"启动虚拟程序失败: {e}")
        # 延迟抓初始判分文件，用于区分「打开过」和「真正操作过」
        QTimer.singleShot(2500, lambda: self._record_vapp_init(qid))

    def _q_by_id(self, qid):
        for q in self.qs:
            if str(q.get("qid", "")) == str(qid):
                return q
        return None

    def _fetch_material(self, qid):
        """向服务端拉取素材并写入考生文件夹，返回 (目录, 主文件|None)。

        用 _write_material_files 而不是逐个裸写：material.zip（文件管理题）展开后
        是带子目录的相对路径（如 t1/user/img.bmp），裸写会因父目录不存在抛异常被
        except 吞掉，表现为「素材一个都不落地 / 不自动创建复制文件」。
        """
        d = self.op_dir(qid)
        r = self.net.call("getMaterial", timuID=qid)
        # 预览图（answer_sc*）单独缓存，题目右侧显示用
        pv = r.get("previews") or []
        if pv:
            self._previews[str(qid)] = pv
            if str(qid) == str(self.qs[self.idx].get("qid", "")):
                QTimer.singleShot(200, lambda: self._show_preview(str(qid)))
        files = r.get("files", []) or []
        if not files:
            self._mat_snaps[str(qid)] = self._snap_dir(d)
            return d, None
        first = self._write_material_files(d, files)
        # zip 展开后主文件可能在子目录，_write_material_files 只挑了根级后缀匹配，
        # 这里再全目录找一次（排除 answer_ 前缀的标准答案）
        if not first:
            first = self._find_office(d)
        if first:
            self.op_mainfile[qid] = first
        self.material_ready.add(qid)
        # 素材落地后记录初始快照：学生改动前导航不显示已答
        self._mat_snaps[str(qid)] = self._snap_dir(d)
        return d, first

    def _ensure_material(self, qid, q=None):
        """保证该题素材已落地到考生文件夹；已下载过则不重复拉取（避免覆盖作答）。"""
        if qid in self.material_ready:
            return self.op_dir(qid), self.op_mainfile.get(qid)
        d = self.op_dir(qid)
        try:
            if os.listdir(d):
                self.material_ready.add(qid)
                # 目录已有素材（提前下载/上次会话遗留）：记录初始快照，
                # 学生改动后 _answered 才判定已答（覆盖保存素材也算）
                self._mat_snaps.setdefault(str(qid), self._snap_dir(d))
                main = self.op_mainfile.get(qid)
                if not (main and os.path.exists(main)):
                    main = self._find_office(d)
                    if main:
                        self.op_mainfile[qid] = main
                return d, main
        except Exception:
            pass
        try:
            return self._fetch_material(qid)
        except Exception as e:
            print("[material] qid=%s 拉取素材失败: %r" % (qid, e))
            return d, None

    def _is_file_manage(self, qid, q=None):
        """文件管理题：需自动解压素材到考生文件夹并整目录打包上传。"""
        q = q or self._q_by_id(qid)
        if not q:
            return False
        return self._needs_material_folder(q)

    def _is_web_edge(self, qid, q=None):
        """网页收藏/另存题（subType=6）：素材是网页，用真实 Edge 打开，交卷带收藏夹。"""
        q = q or self._q_by_id(qid)
        return bool(q and int(q.get("subType") or 0) == 6)

    def _web_index(self, d):
        """在解压目录找网页主页（index/主页 htm/html）。"""
        for root, _dirs, files in os.walk(d):
            for fn in files:
                low = fn.lower()
                if low in ("index.htm", "index.html", "default.htm", "default.html"):
                    return os.path.join(root, fn)
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if fn.lower().endswith((".htm", ".html")):
                    return os.path.join(root, fn)
        return ""

    def auto_open_material(self, qid):
        # QTimer 延迟触发时当前题可能已切走，按 qid 定位而不是 self.idx
        q = self._q_by_id(qid) or self.qs[self.idx]
        vapp = q.get("vapp", "")
        if vapp:
            self.launch_vapp(vapp)
            return
        try:
            d, first = self._ensure_material(str(qid), q)
            if self._is_web_edge(str(qid), q):
                # 网页收藏/另存题：用系统默认浏览器打开本地网页主页
                idx = self._web_index(d)
                if idx:
                    edge_web.open_default_browser(idx)
                return
            if self._is_file_manage(str(qid), q):
                # 文件管理题：素材已铺到考生文件夹，直接打开文件夹让考生在里面操作
                try:
                    os.startfile(d)
                except Exception:
                    pass
                return
            if first:
                try:
                    os.startfile(first)
                except Exception:
                    pass
        except Exception as e:
            print("[material] auto_open qid=%s 失败: %r" % (qid, e))

    def open_material(self):
        q = self.qs[self.idx]
        if q.get("type") == 25 and self.is_operate(q):
            # Python 题：打开考生文件夹中的 answer.py（PyCharm 编辑）
            self._open_py_file()
            return
        vapp = q.get("vapp", "")
        if vapp:
            self.launch_vapp(vapp)
            QMessageBox.information(self, "提示", "已启动虚拟环境，请按题干要求在虚拟程序中完成操作。")
            return
        qid = str(q.get("qid", ""))
        try:
            d, first = self._ensure_material(qid, q)
            if not os.listdir(d):
                QMessageBox.warning(self, "提示", "此题没有可打开的素材文件")
                return
            if self._is_web_edge(qid, q):
                idx = self._web_index(d)
                if not idx:
                    QMessageBox.warning(self, "提示", "素材中未找到网页主页")
                    return
                edge_web.open_default_browser(idx)
                QMessageBox.information(self, "提示",
                    "已用系统默认浏览器打开网页，请在浏览器中按题干要求：\n"
                    "• 用 Ctrl+D 添加收藏（使用题目指定的名称）\n"
                    "• 将网页/图片「另存为」到当前试题文件夹（使用题目指定的文件名）\n"
                    "完成后返回交卷即自动判分。")
                return
            main = self.op_mainfile.get(qid)
            if not (main and os.path.exists(main)):
                main = self._find_office(d)
            if main and not self._is_file_manage(qid, q):
                os.startfile(main)
                QMessageBox.information(self, "提示", f"素材已打开：\n{main}\n\n请在 Office 中按题干要求操作，保存关闭后交卷即自动判分。")
            else:
                os.startfile(d)
                if self._is_file_manage(qid, q):
                    QMessageBox.information(self, "提示",
                        f"素材已解压到考生文件夹：\n{d}\n\n请按题干要求在文件夹内新建/复制/重命名/移动文件，"
                        f"交卷时自动整目录打包上传判分。")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开素材失败: {e}")

    def upload_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择结果文件", "",
            "结果文件 (*.docx *.xlsx *.pptx *.accdb *.psd *.html *.htm);;所有文件 (*.*)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            q = self.qs[self.idx]
            qid = q.get("qid")
            cmd = "practiceGradeOperate" if self.practice else "uploadOperate"
            payload = {"filename": os.path.basename(path), "data": base64.b64encode(data).decode()}
            if self.practice:
                payload["timuID"] = qid
            else:
                payload["recordId"] = self.record_id
                payload["qid"] = qid
            r = self.net.call(cmd, timeout=120, **payload)
            if r.get("pending"):
                self.op_grades[str(qid)] = {"pending": True, "score": None,
                                            "note": r.get("note", "判分中，请稍后刷新成绩"),
                                            "hits": [], "miss": [], "total": 0}
                self._refresh_nav()
                QMessageBox.information(self, "已受理（判分中）",
                    f"{r.get('note', '判分中，请稍后刷新成绩')}\n"
                    f"文件题已在后台自动判分，交卷后在「成绩明细」中刷新即可看到逐点得分。")
            elif r.get("auto"):
                self.op_grades[str(qid)] = {"score": r.get("score", 0), "hits": r.get("hits", []),
                                            "miss": r.get("miss", []), "total": r.get("total", 0),
                                            "note": r.get("note", "")}
                self._refresh_nav()
                QMessageBox.information(self, "完成", f"自动判分完成！\n{r.get('note','')}\n得分: {r.get('score',0)}")
            else:
                QMessageBox.warning(self, "提示", f"文件已接收，但无法自动判分：\n{r.get('note','')}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"上传失败: {e}")

    def _kill_vapp(self):
        """终止所有虚拟程序子进程"""
        for proc in self.vapp_procs:
            try:
                proc.terminate()
            except:
                pass
        self.vapp_procs = []

    def _heartbeat_loop(self):
        """后台线程：每 5 秒上报一次实时作答状态（失败静默，不影响答题）"""
        while not self._hb_stop.is_set():
            try:
                self._send_heartbeat()
            except Exception:
                pass
            self._hb_stop.wait(5)

    def _send_heartbeat(self):
        """上报当前题目/已答列表/作答内容（选择题带选项，服务端判对错）"""
        qids = []
        for q in self.qs:
            if self.is_operate(q):
                m = q.get("module") or "操作题"
            else:
                c = int(q.get("course") or 0)
                m = "选择·模块%d" % c
            qids.append({"qid": str(q.get("qid")), "module": m})
        answered = []
        for i, q in enumerate(self.qs):
            if self.is_operate(q):
                if (self.op_texts[i] or "").strip():
                    answered.append({"qid": str(q.get("qid")), "refType": "operate", "sel": -1})
            else:
                if self.sel[i] >= 0:
                    answered.append({"qid": str(q.get("qid")), "refType": "obj", "sel": self.sel[i]})
        cur = str(self.qs[self.idx].get("qid")) if self.qs else ""
        self.net.call("heartbeat", timeout=8,
                      mode="practice" if self.practice else "homework",
                      id=self.record_id, current=cur, qids=qids, answered=answered)

    def changeEvent(self, e):
        """最小化时：隐藏主窗，只保留一行悬浮条缩到右下角（含“恢复”按钮）"""
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange and self.isMinimized() and not self._minimizing:
            self._minimizing = True
            QTimer.singleShot(120, self._show_mini_bar)
            e.ignore()

    def _show_mini_bar(self):
        if not self.isMinimized():
            self._minimizing = False
            return
        if self._mini is None:
            self._mini = MiniBar(self.title)
            self._mini.restore.connect(self._restore_from_mini)
        self._mini.show_at_bottom_right()
        self.hide()          # 任务栏不再占位
        self._minimizing = False

    def _restore_from_mini(self):
        if self._mini:
            self._mini.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self._hb_stop.set()
        if self._mini:
            self._mini.hide()
        self._kill_vapp()
        self._stop_sql_env()
        if hasattr(self, "timer"):
            self.timer.stop()
        self._cleanup_workdir()
        event.accept()

    def submit(self):
        if not QMessageBox.question(self, "确认交卷", "确定要交卷吗？还在做的题不会计分。") == QMessageBox.Yes:
            return
        # 未安装 MySQL/Navicat 时：提示 SQL 题无法作答后再交
        if self._env_locked:
            sql_n = sum(1 for q in self.qs if self.is_operate(q) and q.get("type") == 24)
            if sql_n:
                r2 = QMessageBox.question(
                    self, "确认交卷",
                    f"本机未安装 MySQL / Navicat，{sql_n} 道 MySQL 数据库操作题已锁定未作答。\n"
                    f"确定仍要交卷吗？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if r2 != QMessageBox.Yes:
                    return
        self._save_op_text()
        self._capture_vapp()
        # 构造答案
        answers = []
        for i, q in enumerate(self.qs):
            if self.is_operate(q):
                answers.append({"qid": q.get("qid"), "refType": "operate", "answer": self.op_texts[i]})
            else:
                a = ""
                if self.sel[i] >= 0:
                    a = ["A", "B", "C", "D", "E", "F"][self.sel[i]]
                answers.append({"qid": q.get("qid"), "refType": "obj", "answer": a})

        # 退出答题界面，显示「正在交卷」等待窗口（交卷可能几分钟，必须有进度反馈）
        op_n = sum(1 for q in self.qs if self.is_operate(q))
        total_steps = max(3, op_n + 2)
        self._wait_dlg = SubmitWaitDialog(total_steps, parent=None)
        self.hide()                       # 退出答题界面
        self._wait_dlg.set_step("正在准备交卷…", 1)
        self._wait_dlg.show()

        self._submit_thread = QThread(self)
        worker = SubmitWorker(self, answers, total_steps)
        self._submit_worker = worker
        worker.moveToThread(self._submit_thread)
        self._submit_thread.started.connect(worker.run)
        worker.step.connect(self._wait_dlg.set_step)
        worker.done.connect(self._on_submit_done)
        worker.failed.connect(self._on_submit_failed)
        worker.done.connect(self._submit_thread.quit)
        worker.failed.connect(self._submit_thread.quit)
        self._submit_thread.start()

    def _on_submit_done(self, res):
        """交卷成功：关等待窗 → 显示成绩 → 退出"""
        try:
            if self._wait_dlg:
                self._wait_dlg.close()
                self._wait_dlg = None
        except Exception:
            pass
        try:
            self._kill_vapp()
        except Exception:
            pass
        try:
            dlg = ResultDialog(res.get("score"), res.get("total"), res.get("detail", []),
                               score_hidden=res.get("score_hidden", False),
                               notes=res.get("notes", ""), parent=self)
            dlg.exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"生成成绩失败: {e}")
        if self.on_exit:
            try:
                self.on_exit()
            except Exception:
                pass
        self.close()

    def _on_submit_failed(self, msg):
        """交卷失败：关等待窗 → 回到答题界面，允许重试"""
        try:
            if self._wait_dlg:
                self._wait_dlg.close()
                self._wait_dlg = None
        except Exception:
            pass
        self.show()
        self.raise_()
        QMessageBox.critical(self, "交卷失败", f"交卷失败: {msg}\n\n请检查网络后重新交卷。")

    def _refresh_homework_result(self):
        """交卷后刷新：重拉 recordDetail，返回 {score,total,detail}（未判完的题仍为 pending）。"""
        r = self.net.call("recordDetail", recordId=self.record_id, timeout=120)
        if r.get("code") != 0:
            raise RuntimeError(r.get("msg") or "刷新失败")
        detail = r.get("detail", []) or []
        sc = round(sum(float(d.get("score") or 0) for d in detail), 1) if detail else 0.0
        tot = round(sum(float(d.get("fullScore") or 0) for d in detail), 1) if detail else 0.0
        return {"score": sc, "total": tot, "detail": detail}

    def _auto_upload_operate(self, progress_cb=None):
        notes = []
        op_list = [q for q in self.qs if self.is_operate(q)]
        op_done = 0
        def prog(text):
            if progress_cb:
                try:
                    progress_cb(text)
                except Exception:
                    pass
        def tick(q, kind):
            nonlocal op_done
            op_done += 1
            prog("正在处理第 %d/%d 题：%s" % (op_done, len(op_list), kind))
        for _qi, q in enumerate(self.qs):
            if not self.is_operate(q):
                continue
            qid = str(q.get("qid", ""))
            vapp = q.get("vapp", "")
            if q.get("type") == 24:
                tick(q, "MySQL 数据库操作题（打包上传，服务端起实例判分）")
                # MySQL 题：优雅关停临时实例后，把整个考试文件夹（datadir + tj*.sql
                # 等作答文件）打包上传，服务端起学生 datadir 实例判分
                d = os.path.join(self.work_dir, qid)
                if not os.path.isdir(d) or not os.listdir(d):
                    _why = "考试文件夹不存在" if not os.path.isdir(d) else "考试文件夹为空"
                    self._mysql_log("交卷题 %s: %s（d=%s），跳过上传 → MySQL题未作答"
                                    % (qid, _why, d))
                    notes.append("MySQL题未作答（%s，未下载到素材或工作目录被清理）" % _why)
                    continue
                _nfiles = sum(len(fs) for _, _, fs in os.walk(d))
                self._mysql_log("交卷题 %s: 开始打包，文件夹文件数=%d" % (qid, _nfiles))
                try:
                    self._shutdown_sql_env(qid)
                    zb = self._zip_workdir(d, exclude={"material.zip", "_tmp_mysqld.log"})
                    self._mysql_log("交卷题 %s: 打包完成 zip=%d 字节" % (qid, len(zb)))
                    cmd = "practiceGradeOperate" if self.practice else "uploadOperate"
                    payload = {"mysqlZip": base64.b64encode(zb).decode()}
                    if self.practice:
                        payload["timuID"] = qid
                    else:
                        payload["recordId"] = self.record_id
                        payload["qid"] = qid
                    r = self.net.call(cmd, timeout=180, **payload)
                    self._mysql_log("交卷题 %s: %s 返回 code=%s auto=%s score=%s note=%s"
                                    % (qid, cmd, r.get("code"), r.get("auto"),
                                       r.get("score"), str(r.get("note"))[:120]))
                    if r.get("pending"):
                        self.op_grades[qid] = {"pending": True, "score": None,
                            "note": r.get("note", "判分中，请稍后刷新成绩"),
                            "hits": [], "miss": [], "total": 0}
                        notes.append(f"MySQL题: {r.get('note','判分中')}")
                    elif r.get("auto"):
                        self.op_grades[qid] = {"score":r.get("score",0),"hits":r.get("hits",[]),
                            "miss":r.get("miss",[]),"total":r.get("total",0),"note":r.get("note","")}
                        notes.append(f"MySQL题: {r.get('note','')}")
                    else:
                        notes.append(f"MySQL题未能判分: {r.get('note') or r.get('msg','')}")
                except Exception as e:
                    self._mysql_log("交卷题 %s: 上传异常 %s" % (qid, e))
                    notes.append(f"MySQL题上传失败: {e}")
                continue
            if q.get("type") == 25:
                tick(q, "Python 编程题（上传代码，服务端 OJ 判分）")
                # Python 题：上传 PyCharm 中保存的 answer.py，服务端 OJ 判分
                code = ""
                p = self._py_file(qid)
                if os.path.exists(p):
                    try:
                        with open(p, "rb") as f:
                            raw = f.read()
                        for enc in ("utf-8-sig", "gbk"):
                            try:
                                code = raw.decode(enc)
                                break
                            except Exception:
                                continue
                        else:
                            code = raw.decode("utf-8", "replace")
                    except Exception:
                        code = ""
                if not code.strip() or code.strip() == self._py_template(q).strip():
                    notes.append("Python编程题未作答")
                    continue
                try:
                    cmd = "practiceGradeOperate" if self.practice else "uploadOperate"
                    payload = {"code": code}
                    if self.practice:
                        payload["timuID"] = qid
                    else:
                        payload["recordId"] = self.record_id
                        payload["qid"] = qid
                    r = self.net.call(cmd, timeout=120, **payload)
                    if r.get("auto"):
                        self.op_grades[qid] = {"score":r.get("score",0),"hits":r.get("hits",[]),
                            "miss":r.get("miss",[]),"total":r.get("total",0),"note":r.get("note","")}
                        notes.append(f"Python编程题: {r.get('note','')}")
                    else:
                        notes.append(f"Python编程题未能判分: {r.get('note','')}")
                except Exception as e:
                    notes.append(f"Python编程题上传失败: {e}")
                continue
            if vapp:
                tick(q, "虚拟程序操作题（上传操作记录判分）")
                if qid in self.vapp_logs:
                    try:
                        cmd = "practiceGradeOperate" if self.practice else "uploadOperate"
                        payload = {"vapp": vapp, "log": self.vapp_logs[qid]}
                        if self.practice:
                            payload["timuID"] = qid
                        else:
                            payload["recordId"] = self.record_id
                            payload["qid"] = qid
                        r = self.net.call(cmd, timeout=120, **payload)
                        if r.get("auto"):
                            self.op_grades[qid] = {"score":r.get("score",0),"hits":r.get("hits",[]),
                                "miss":r.get("miss",[]),"total":r.get("total",0),"note":r.get("note","")}
                            notes.append(f"虚拟程序题: {r.get('note','')}")
                        else:
                            notes.append("虚拟程序题未能判分")
                    except Exception as e:
                        notes.append(f"虚拟程序题上传失败: {e}")
                else:
                    notes.append("虚拟程序题未检测到作答记录")
                continue
            # Office / Access / Photoshop 文件题，取本次 C:\stu 工作目录
            d = os.path.join(self.work_dir, qid)
            if not os.path.isdir(d):
                continue
            # 文件管理题（subType=2 / material.zip）：整目录打包成 zip 上传，
            # 引擎按 paperFolder 判分（新建/复制/重命名/移动/删除等文件系统操作）
            if self._is_file_manage(qid, q):
                tick(q, "文件管理操作题（打包上传判分）")
                if not os.listdir(d):
                    notes.append("文件管理题未检测到考生文件夹内容")
                    continue
                try:
                    zb = self._zip_workdir(d)
                    cmd = "practiceGradeOperate" if self.practice else "uploadOperate"
                    payload = {"paperZip": base64.b64encode(zb).decode()}
                    if self._is_web_edge(qid, q):
                        # 网页收藏题：附带浏览器收藏夹（Edge+Chrome），用于 hf3381 判定
                        try:
                            payload["favorites"] = edge_web.read_bookmarks()
                        except Exception as e:  # noqa
                            print("read browser bookmarks failed:", e)
                    if self.practice:
                        payload["timuID"] = qid
                    else:
                        payload["recordId"] = self.record_id
                        payload["qid"] = qid
                    r = self.net.call(cmd, timeout=180, **payload)
                    if r.get("auto"):
                        self.op_grades[qid] = {"score":r.get("score",0),"hits":r.get("hits",[]),
                            "miss":r.get("miss",[]),"total":r.get("total",0),"note":r.get("note","")}
                        notes.append(f"文件管理题: {r.get('note','')}")
                    elif r.get("pending"):
                        self.op_grades[qid] = {"pending": True, "score": None,
                            "note": r.get("note", "判分中，请稍后刷新成绩"),
                            "hits": [], "miss": [], "total": 0}
                        notes.append(f"文件管理题: {r.get('note','判分中')}")
                    else:
                        notes.append(f"文件管理题判分失败: {r.get('msg') or r.get('note','')}")
                except Exception as e:
                    notes.append(f"文件管理题上传失败: {e}")
                continue
            doc = self.op_mainfile.get(qid)
            if not (doc and os.path.exists(doc)):
                doc = None
                import glob
                for ext in ["*.docx", "*.xlsx", "*.pptx", "*.accdb", "*.psd"]:
                    fs = [x for x in glob.glob(os.path.join(d, ext))
                          if not os.path.basename(x).startswith("answer_")]
                    if fs:
                        doc = fs[0]
                        break
            if not doc:
                continue
            tick(q, "Office / 设计软件操作题（上传文件判分）")
            try:
                with open(doc, "rb") as f:
                    data = f.read()
                cmd = "practiceGradeOperate" if self.practice else "uploadOperate"
                payload = {"filename": os.path.basename(doc), "data": base64.b64encode(data).decode()}
                if self.practice:
                    payload["timuID"] = qid
                else:
                    payload["recordId"] = self.record_id
                    payload["qid"] = qid
                r = self.net.call(cmd, timeout=120, **payload)
                if r.get("pending"):
                    self.op_grades[qid] = {"pending": True, "score": None,
                        "note": r.get("note", "判分中，请稍后刷新成绩"),
                        "hits": [], "miss": [], "total": 0}
                    notes.append(f"操作题: {r.get('note','判分中')}")
                elif r.get("auto"):
                    self.op_grades[qid] = {"score":r.get("score",0),"hits":r.get("hits",[]),
                        "miss":r.get("miss",[]),"total":r.get("total",0),"note":r.get("note","")}
                    notes.append(f"操作题: {r.get('note','')}")
                else:
                    notes.append(f"操作题未能判分: {r.get('note','')}")
            except Exception as e:
                notes.append(f"操作题上传失败: {e}")
        return "\n".join(notes)


# ==================== 交卷等待界面 ====================
class SubmitWaitDialog(QDialog):
    """交卷等待界面：答题窗口退出后显示，实时告诉学生「现在正在做什么」。

    交卷可能耗时几十秒到几分钟（打包上传操作题、服务端 Office/MySQL 判分），
    学生看不到进度会以为卡死甚至强杀程序，所以必须给出明确的分步反馈。
    """
    def __init__(self, total_steps=1, parent=None):
        super().__init__(parent)
        self.total = max(1, int(total_steps))
        self.setWindowTitle("正在交卷")
        self.setFixedSize(520, 260)
        self.setWindowModality(Qt.ApplicationModal)
        # 交卷过程中不允许关闭，避免答题已上传却被中断
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)

        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(12)

        title = StrongBodyLabel("正在交卷，请稍候…")
        title.setStyleSheet("font-size:20px;")
        v.addWidget(title)

        self.step_label = BodyLabel("正在准备…")
        self.step_label.setStyleSheet("font-size:15px; color:#2D3748;")
        self.step_label.setWordWrap(True)
        v.addWidget(self.step_label)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)          # 不确定模式：走一步动一下
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        v.addWidget(self.bar)

        self.cnt_label = CaptionLabel("")
        self.cnt_label.setStyleSheet("color:#718096;")
        v.addWidget(self.cnt_label)

        v.addStretch()
        tip = CaptionLabel("交卷过程中请勿关闭程序或断开网络；操作题需要先上传再由服务端判分，可能需要几十秒。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#A0AEC0;")
        v.addWidget(tip)

    def set_step(self, text, index=None):
        self.step_label.setText(text)
        if index is not None:
            self.cnt_label.setText("进度：第 %d / %d 步" % (min(index, self.total), self.total))
        # 强制重绘，保证阻塞期间也能看到最新文案
        self.repaint()
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
        except Exception:
            pass


class SubmitWorker(QObject):
    """后台交卷：上传操作题 → 提交客观题 → 组装成绩明细。全程不碰 UI。"""
    step = Signal(str, int)        # (当前步骤文案, 第几步)
    done = Signal(dict)            # 成功：{score,total,detail,score_hidden}
    failed = Signal(str)           # 失败原因

    def __init__(self, exam, answers, total_steps):
        super().__init__()
        self.exam = exam
        self.answers = answers
        self.total = total_steps
        self._n = 0

    def _emit(self, text):
        self._n += 1
        self.step.emit(text, min(self._n, self.total))

    def run(self):
        ex = self.exam
        try:
            self._emit("正在上传操作题作答…（打包并上传你的考试文件夹）")
            notes = ex._auto_upload_operate(progress_cb=self._emit)

            self._emit("正在提交答卷…")
            if ex.submit_cmd == "wrongSubmit":
                r = ex.net.call("wrongSubmit", answers=self.answers, timeout=120)
            elif ex.submit_cmd == "submitPractice":
                r = ex.net.call("submitPractice", answers=self.answers, timeout=120)
            else:
                r = ex.net.call("submit", recordId=ex.record_id, answers=self.answers, timeout=120)

            score_hidden = bool(r.get("scoreHidden", False))
            score = r.get("score", 0) if not score_hidden else None
            total = r.get("total", 0) if not score_hidden else None
            detail = list(r.get("detail", []) or []) if not score_hidden else []

            if not score_hidden:
                self._emit("正在汇总操作题得分…")
                have_op = {str(d.get("qid")) for d in detail if d.get("refType") == "operate"}
                for q in ex.qs:
                    if not ex.is_operate(q):
                        continue
                    qid = str(q.get("qid"))
                    if qid in have_op:
                        continue
                    g = ex.op_grades.get(qid)
                    if g:
                        pend = bool(g.get("pending")) or ("score" in g and g.get("score") is None)
                        sc_g = None if pend else (g.get("score", 0) or 0)
                        detail.append({"qid": q.get("qid"), "refType": "operate", "stem": q.get("stem", ""),
                            "your": "(见上传文件)", "right": "自动判分", "score": sc_g,
                            "fullScore": 20.0, "hits": g.get("hits", []), "miss": g.get("miss", []),
                            "total": g.get("total", 0), "note": g.get("note", ""),
                            "pending": pend,
                            "correct": (sc_g is not None and sc_g >= 12)})
                    else:
                        detail.append({"qid": q.get("qid"), "refType": "operate", "stem": q.get("stem", ""),
                            "your": "(未上传文件)", "right": "自动判分", "score": 0, "fullScore": 20.0,
                            "hits": [], "miss": [], "total": 0, "note": "未检测到作答文件", "correct": False})
                if ex.submit_cmd == "submitPractice":
                    op_got = sum(d.get("score", 0) or 0 for d in detail if d.get("refType") == "operate")
                    op_full = sum(d.get("fullScore", 20) or 20 for d in detail if d.get("refType") == "operate")
                    score = (score or 0) + op_got
                    total = (total or 0) + op_full

            self._emit("交卷完成，正在生成成绩…")
            self.done.emit({"score": score, "total": total, "detail": detail,
                            "score_hidden": score_hidden, "notes": notes})
        except Exception as e:
            self.failed.emit(str(e))


# ==================== 交卷结果对话框 ====================
class ResultDialog(QDialog):
    def __init__(self, score, total, detail, score_hidden=False, parent=None, refresh_cb=None,
                 notes=""):
        super().__init__(parent)
        self.setWindowTitle("交卷结果")
        self.resize(750, 600)
        self.refresh_cb = refresh_cb
        self._win = parent  # ExamWindow：取 net 拉标准答案、取 op_dir 存文件
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self.detail = detail
        detail = detail or []
        pending_n = sum(1 for d in detail if is_pending(d))
        if notes and str(notes).strip():
            # 操作题上传/判分过程中的提示（如"MySQL题未作答/上传失败"），
            # 之前被静默丢弃导致学生只看到 0 分却不知道原因
            nt = BodyLabel("⚠️ " + str(notes).replace("\n", "　·　"))
            nt.setWordWrap(True)
            nt.setStyleSheet("background:#FFF7E6;border:1px solid #FFE0A3;border-radius:6px;"
                             "padding:8px;color:#8a5a00;")
            layout.addWidget(nt)
        if score_hidden:
            title = StrongBodyLabel("交卷成功")
            title.setStyleSheet("font-size:22px;")
            layout.addWidget(title)
            hint = BodyLabel("本次考试成绩暂未公布，请等待教师发布。")
            hint.setStyleSheet("color:#888;")
            layout.addWidget(hint)
            layout.addStretch(1)
        else:
            # score/total 可能为 None（如后台判分中），统一转 float 防止格式化崩溃
            try:
                sc_v = float(score) if score is not None else 0.0
            except (TypeError, ValueError):
                sc_v = 0.0
            try:
                tot_v = float(total) if total is not None else 0.0
            except (TypeError, ValueError):
                tot_v = 0.0
            if tot_v > 0:
                rate = sc_v / tot_v
                sc_color = "#27AE60" if rate>=0.6 else "#E67E22" if rate>=0.3 else "#E74C3C"
                title = StrongBodyLabel(f"本次得分：{sc_v:g} / {tot_v:g}")
            else:
                sc_color = "#2E6FF2"
                title = StrongBodyLabel(f"本次得分：{sc_v:g}")
            title.setStyleSheet(f"font-size:24px;color:{sc_color};")
            layout.addWidget(title)
            obj = [d for d in detail if d.get("refType") != "operate"]
            op  = [d for d in detail if d.get("refType") == "operate"]
            if obj:
                ro = sum(1 for d in obj if d.get("correct"))
                layout.addWidget(BodyLabel(f"📝 客观题：答对 <b style='color:#27AE60;'>{ro}</b> / {len(obj)} 题"))
            if op:
                parts = []
                for i, d in enumerate(op, 1):
                    if is_pending(d):
                        parts.append(f"操作题{i}:判分中")
                    else:
                        parts.append(f"操作题{i}:{(d.get('score') or 0):g}/{(d.get('fullScore') or 20):g}")
                lb = CaptionLabel("🛠 " + "　".join(parts)); lb.setWordWrap(True)
                layout.addWidget(lb)
            if pending_n:
                pt = BodyLabel(f"⏳ <b style='color:#E67E22;'>{pending_n}</b> 道操作题正在后台判分，"
                               f"当前得分仅含已判完部分；点击「刷新成绩」可查看最终结果。")
                pt.setTextFormat(Qt.RichText); pt.setWordWrap(True)
                pt.setStyleSheet("background:#FFF7E6;border:1px solid #FFE0A3;border-radius:6px;"
                                 "padding:8px;color:#8a5a00;")
                layout.addWidget(pt)
            if op:
                std_btn = PrimaryPushButton("📄 查看操作题标准答案")
                std_btn.setFixedHeight(40)
                std_btn.clicked.connect(self._open_std_answer)
                layout.addWidget(std_btn)
            detail_btn = PrimaryPushButton("📊 查看成绩明细（分类查看对错）")
            detail_btn.setFixedHeight(44)
            detail_btn.clicked.connect(lambda: GradeDetailDialog(score, total, detail,
                                            title="成绩明细", parent=self,
                                            refresh_cb=self.refresh_cb).exec())
            layout.addWidget(detail_btn)
            layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        if (not score_hidden) and self.refresh_cb and pending_n:
            rb = PrimaryPushButton("🔄 刷新成绩")
            rb.setFixedWidth(130)
            rb.clicked.connect(self._do_refresh)
            btn_row.addWidget(rb)
        close_btn = PrimaryPushButton("关闭")
        close_btn.setFixedWidth(120)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _do_refresh(self):
        """后台判分完成后刷新交卷结果（重建对话框）。"""
        if not self.refresh_cb:
            return
        try:
            new = self.refresh_cb() or {}
        except Exception as e:
            QMessageBox.warning(self, "刷新失败", str(e)); return
        dlg = ResultDialog(new.get("score"), new.get("total"), new.get("detail") or [],
                           score_hidden=False, parent=self.parent(), refresh_cb=self.refresh_cb)
        dlg.exec()
        self.accept()

    # ---------- 查看操作题标准答案 ----------
    def _open_std_answer(self):
        ops = [d for d in (self.detail or []) if d.get("refType") == "operate" and d.get("qid")]
        if not ops:
            QMessageBox.information(self, "标准答案", "本卷没有操作题。")
            return
        qid = None
        if len(ops) == 1:
            qid = str(ops[0]["qid"])
        else:
            items = []
            for i, d in enumerate(ops, 1):
                stem = (d.get("stem") or "").replace("\n", " ").strip()[:36]
                items.append(f"操作题{i}：{stem or d.get('qid')}")
            pick, ok = QInputDialog.getItem(self, "选择操作题",
                                            "选择要查看标准答案的操作题：", items, 0, False)
            if not ok:
                return
            qid = str(ops[items.index(pick)]["qid"])
        self._open_std_file(qid)

    def _open_std_file(self, qid):
        win = self._win
        if win is None or not getattr(win, "net", None):
            QMessageBox.warning(self, "标准答案", "无法连接服务端，请稍后重试。")
            return
        try:
            r = win.net.call("getStandardAnswer", timuID=qid, timeout=30)
        except Exception as e:
            QMessageBox.warning(self, "标准答案", f"获取标准答案失败：{e}")
            return
        if r.get("code") != 0 or not r.get("data"):
            QMessageBox.information(self, "标准答案", r.get("msg") or "该题暂无标准答案文件。")
            return
        name = r.get("name", "标准答案.txt")
        d = win.op_dir(qid)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        try:
            with open(p, "wb") as fh:
                fh.write(base64.b64decode(r["data"]))
        except Exception as e:
            QMessageBox.warning(self, "标准答案", f"保存标准答案失败：{e}")
            return
        try:
            os.startfile(p)
        except Exception as e:
            QMessageBox.information(self, "标准答案",
                                    f"标准答案已保存到：\n{p}\n\n（自动打开失败：{e}）")


