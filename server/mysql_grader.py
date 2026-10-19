# -*- coding: utf-8 -*-
"""
MySQL 操作题自动判分器（技能高考）。

思路：
  1. 把题目的 material.zip（MySQL 5.7 数据目录快照，基线）解压到临时 datadir；
  2. 用内置的免安装 MySQL 5.7（runtime/mysql）启动一个临时实例（随机端口、skip-grant）；
  3. 执行学生提交的 SQL（可含建表/查询/视图/存储过程/触发器，多语句）；
  4. 逐判分点(dfd)检测：结果集、视图、存储过程、触发器、复制表、字段类型；
  5. 关闭实例并删除临时目录。

对外主函数：grade_mysql(dfdlist, sql_text, material_zip, full_score) -> dict
"""
import os
import re
import sys
import time
import socket
import shutil
import subprocess
import tempfile
import zipfile
import subprocess
from app_paths import BASE_DIR as HERE

try:
    import pymysql
except Exception:  # pragma: no cover
    pymysql = None

RUNTIME_DIR = os.path.join(HERE, "runtime", "mysql")


def safe_extract_zip(zip_path, dst):
    """解压 zip 到目录。兼容原版工具生成的非标准 zip（central 目录条目用正斜杠、
    local file header 用反斜杠，Python zipfile 校验两者一致时抛 BadZipFile，
    表现为「判分异常 / 学生端起不了实例」）：优先用系统自带 tar.exe（bsdtar，
    不做该一致性校验），失败回退 zipfile.extractall。返回是否成功。"""
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


# --------------------------------------------------------------------------- #
# SQL 多语句分割（处理字符串/反引号/注释/DELIMITER/BEGIN..END 等复合块）
# --------------------------------------------------------------------------- #
def _strip_delimiter(sql):
    """预处理 DELIMITER 指令：返回 (清理后的sql, 初始delimiter)。"""
    delim = ";"
    out_lines = []
    for line in sql.splitlines():
        m = re.match(r"\s*DELIMITER\s+(\S+)", line, re.IGNORECASE)
        if m:
            delim = m.group(1)
            continue
        out_lines.append(line)
    return "\n".join(out_lines), delim


_OPEN_KW = re.compile(r"\b(BEGIN|CASE|LOOP|WHILE|REPEAT)\b", re.IGNORECASE)
_IF_KW = re.compile(r"\bIF\b(?!\s*\()", re.IGNORECASE)


def _has_then_before_semicolon(sql, pos):
    """从 pos 扫描到最近的顶层分号，期间（跳过字符串/注释）是否出现 THEN。"""
    i, n = pos, len(sql)
    state = None
    while i < n:
        c = sql[i]
        n2 = sql[i:i + 2]
        if state is None:
            if n2 == "--" and (i + 2 >= n or sql[i + 2] in " \t"):
                state = "line"; i += 1; continue
            if c == "#":
                state = "line"; i += 1; continue
            if n2 == "/*":
                state = "block"; i += 2; continue
            if c in "'\"`":
                state = c; i += 1; continue
            if c == ";":
                return False
            if re.match(r"THEN\b", sql[i:], re.IGNORECASE):
                return True
            i += 1
        else:
            if state == "line":
                if c == "\n":
                    state = None
                i += 1; continue
            if state == "block":
                if n2 == "*/":
                    state = None; i += 2; continue
                i += 1; continue
            if c == "\\":
                i += 2; continue
            if c == state:
                state = None
            i += 1; continue
    return False


def split_sql(sql):
    """把一段 SQL 脚本切成顶层语句列表。"""
    sql, delim = _strip_delimiter(sql)
    stmts = []
    buf = []
    depth = 0
    i, n = 0, len(sql)
    state = None  # None / "'" / '"' / '`' / 'line' / 'block'

    def kw_ahead(text, pos, word):
        m = re.match(r"\s*" + word, text[pos:], re.IGNORECASE)
        return bool(m)

    while i < n:
        c = sql[i]
        nxt2 = sql[i:i + 2]
        # 注释
        if state is None:
            if nxt2 == "--" and (i + 2 >= n or sql[i + 2] in " \t"):
                state = "line"; buf.append(c); i += 1; continue
            if c == "#":
                state = "line"; buf.append(c); i += 1; continue
            if nxt2 == "/*":
                state = "block"; buf.append(nxt2); i += 2; continue
            if c in "'\"`":
                state = c; buf.append(c); i += 1; continue
        else:
            if state == "line":
                buf.append(c)
                if c == "\n":
                    state = None
                i += 1; continue
            if state == "block":
                buf.append(c)
                if nxt2 == "*/":
                    buf.append("/"); i += 2; state = None; continue
                i += 1; continue
            if state in ("'", '"', "`"):
                buf.append(c)
                if c == "\\" and i + 1 < n:
                    buf.append(sql[i + 1]); i += 2; continue
                if c == state:
                    state = None
                i += 1; continue

        # 复合块关键字（仅普通 SQL 文本区）
        m = _OPEN_KW.match(sql, i)
        if m:
            w = m.group(1).upper()
            depth += 1
            buf.append(m.group(0)); i = m.end(); continue
        mi = _IF_KW.match(sql, i)
        if mi:
            # 流程控制 IF 的判定：从 IF 向后到最近的顶层分号之间，是否出现配套 THEN。
            # 这样 “END IF;” 里的 IF（其后直接是分号）不会被误计。
            if _has_then_before_semicolon(sql, mi.end()):
                depth += 1
            buf.append(mi.group(0)); i = mi.end(); continue
        em = re.match(r"\bEND\b", sql[i:], re.IGNORECASE)
        if em:
            # 注意：必须用切片 sql[i:] 匹配（切片起点 \b 恒成立），
            # 不能用 .match(sql, i)——原位置前若是词字符，\b 不成立会返回 None
            depth = max(0, depth - 1)
            buf.append(em.group(0)); i += em.end(); continue

        # 分隔符
        if sql.startswith(delim, i) and depth == 0:
            piece = "".join(buf).strip()
            if piece:
                stmts.append(piece)
            buf = []
            i += len(delim)
            continue
        buf.append(c)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


# --------------------------------------------------------------------------- #
# 临时 MySQL 实例
# --------------------------------------------------------------------------- #
def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TempMysql(object):
    def __init__(self, material_zip=None, src_dir=None):
        """material_zip：题库素材快照 zip（基线 datadir）。
        src_dir：现成 datadir 目录（如学生上传的考试文件夹），会被 move 进临时目录。"""
        if pymysql is None:
            raise RuntimeError("缺少 PyMySQL，请先 pip install PyMySQL")
        if not os.path.exists(os.path.join(RUNTIME_DIR, "bin", "mysqld.exe")):
            raise RuntimeError("缺少内置 MySQL 运行时：runtime/mysql")
        self.tmp = tempfile.mkdtemp(prefix="mysql_grade_")
        self.datadir = os.path.join(self.tmp, "data")
        if src_dir:
            shutil.move(src_dir, self.datadir)
        else:
            os.makedirs(self.datadir)
            safe_extract_zip(material_zip, self.datadir)
        self.port = _free_port()
        self.proc = None
        self.conn = None
        self.log = os.path.join(self.tmp, "mysqld.log")

    def start(self):
        args = [
            os.path.join(RUNTIME_DIR, "bin", "mysqld.exe"),
            "--basedir=" + RUNTIME_DIR,
            "--datadir=" + self.datadir,
            "--port=%d" % self.port,
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
        lf = open(self.log, "wb")
        self.proc = subprocess.Popen(args, stdout=lf, stderr=subprocess.STDOUT)
        last = None
        for _ in range(90):
            if self.proc.poll() is not None:
                raise RuntimeError("mysqld 提前退出：\n" + self._tail_log())
            try:
                self.conn = pymysql.connect(host="127.0.0.1", port=self.port, user="root",
                                            charset="utf8", autocommit=True, connect_timeout=2)
                return
            except Exception as e:  # noqa
                last = e
                time.sleep(1)
        raise RuntimeError("等待 MySQL 启动超时：%s\n%s" % (last, self._tail_log()))

    def _tail_log(self):
        try:
            return open(self.log, encoding="utf-8", errors="ignore").read()[-1500:]
        except Exception:
            return ""

    def stop(self):
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=15)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    def cleanup(self):
        self.stop()

        def on_error(func, path, exc_info):
            try:
                os.chmod(path, 0o777)
                if os.path.isdir(path):
                    os.rmdir(path)
                else:
                    os.remove(path)
            except Exception:
                pass

        shutil.rmtree(self.tmp, onerror=on_error)


# --------------------------------------------------------------------------- #
# 值/结果归一化与比对
# --------------------------------------------------------------------------- #
def _norm_scalar(v):
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", "ignore")
    if hasattr(v, "strftime"):
        # date / datetime
        try:
            import datetime as _dt
            if isinstance(v, _dt.datetime):
                if v.hour == 0 and v.minute == 0 and v.second == 0:
                    return v.strftime("%Y-%m-%d")
                return v.strftime("%Y-%m-%d %H:%M:%S")
            return v.strftime("%Y-%m-%d")
        except Exception:
            return str(v)
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return ("%.4f" % v).rstrip("0").rstrip(".")
    if isinstance(v, int):
        return str(v)
    s = str(v).strip()
    s = s.replace("（", "(").replace("）", ")").replace("，", ",")
    # 日期时间零时刻归一化为日期（学生 date 与题库 datetime 00:00:00 等价）
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T]\d{2}:\d{2}:\d{2}$", s)
    if m and s.endswith("00:00:00"):
        return m.group(1)
    return s


def parse_expected(yq):
    """解析期望：[ {列:值, ...}, ... ]（行）。"""
    rows = []
    for line in (yq or "").split("|"):
        line = line.strip()
        if not line:
            continue
        row = {}
        for part in line.split("#"):
            if "^" in part:
                k, v = part.split("^", 1)
                row[k.strip()] = v.strip()
            elif part:
                row.setdefault("_extra", []).append(part.strip())
        rows.append(row)
    return rows


# 计算列别名：期望列名 -> 学生可能用的列名（小写）
_COL_ALIAS = {
    "等级": ["等级", "level", "grade", "dj", "等第"],
    "age": ["age", "年龄", "nl"],
    "cname": ["cname", "课程名", "课程名称"],
    "stuname": ["stuname", "姓名", "学生姓名", "名字"],
    "gender": ["gender", "性别", "xb"],
    "birthday": ["birthday", "生日", "出生日期"],
    "score": ["score", "成绩", "分数"],
    "cid": ["cid", "课程id", "课程编号"],
}


def _map_columns(exp_cols, stu_cols):
    """期望列名 -> 学生列下标。返回 dict；未匹配的计算列后续按位置补。"""
    stu_low = [str(c).strip().lower() if c is not None else "" for c in stu_cols]
    mapping = {}
    used = set()
    for ec in exp_cols:
        candidates = [ec.lower()] + _COL_ALIAS.get(ec, []) + _COL_ALIAS.get(ec.lower(), [])
        found = None
        for cand in candidates:
            for idx, sc in enumerate(stu_low):
                if idx in used:
                    continue
                if sc == cand or sc.endswith("." + cand) or sc.split(".")[-1] == cand:
                    found = idx
                    break
            if found is not None:
                break
        if found is not None:
            mapping[ec] = found
            used.add(found)
    # 未匹配的期望列 ↔ 未使用的学生列，按位置顺序补
    missing = [ec for ec in exp_cols if ec not in mapping]
    free = [idx for idx in range(len(stu_cols)) if idx not in used]
    for ec, idx in zip(missing, free):
        mapping[ec] = found if False else idx
        used.add(idx)
    return mapping


def _row_match(exp_row, stu_row, mapping, age_offsets, ec_name):
    for ec, ev in exp_row.items():
        if ec == "_extra":
            continue
        idx = mapping.get(ec)
        if idx is None or idx >= len(stu_row):
            return False
        sv = _norm_scalar(stu_row[idx])
        evn = _norm_scalar(ev)
        # 年龄列：允许固定年份偏移
        if ec.lower() in ("age",) or ec in ("年龄",):
            try:
                d = int(float(sv)) - int(float(evn))
                age_offsets.setdefault(ec, set()).add(d)
                continue
            except Exception:
                if sv != evn:
                    return False
                continue
        if sv is None and evn in (None, "", "null"):
            continue
        if str(sv) != str(evn):
            # 数字容忍
            try:
                if abs(float(sv) - float(evn)) < 1e-6:
                    continue
            except Exception:
                pass
            return False
    return True


def compare_result(stu_cols, stu_rows, yq):
    """学生结果集 vs 期望。返回 (ok, 学生结果编码字符串)。"""
    exp_rows = parse_expected(yq)
    student_repr = _encode_result(stu_cols, stu_rows)
    if not exp_rows:
        return False, student_repr
    exp_cols = []
    for r in exp_rows:
        for k in r:
            if k != "_extra" and k not in exp_cols:
                exp_cols.append(k)
    mapping = _map_columns(exp_cols, stu_cols)

    used_rows = [False] * len(stu_rows)
    age_offsets = {}
    matched_all = True
    for er in exp_rows:
        hit = False
        for ri, srow in enumerate(stu_rows):
            if used_rows[ri]:
                continue
            local_offsets = {}
            if _row_match(er, srow, mapping, local_offsets, exp_cols):
                used_rows[ri] = True
                for k, s in local_offsets.items():
                    age_offsets.setdefault(k, set()).update(s)
                hit = True
                break
        if not hit:
            matched_all = False
            break

    # 年龄列：允许基准年份不同。所有行偏移应相同；若部分人今年生日未过，偏移会少1，
    # 因此允许偏移集合跨度≤1，且偏移绝对值不超过5（换机年份差）。
    age_ok = True
    for k, s in age_offsets.items():
        vals = list(s)
        if not vals:
            age_ok = False
            continue
        if max(vals) - min(vals) > 1:
            age_ok = False
        if any(abs(v) > 5 for v in vals):
            age_ok = False
    # 行数应一致（完整结果）
    same_count = (sum(1 for u in used_rows if u) == len(exp_rows)) and (len(stu_rows) == len(exp_rows) or True)
    ok = matched_all and age_ok and sum(1 for u in used_rows if u) == len(exp_rows)
    if age_offsets and list(age_offsets.values())[0]:
        off = list(list(age_offsets.values())[0])[0]
        if off:
            student_repr += "  （年龄按当前年份计算，与题库基准相差%d岁）" % off
    return ok, student_repr


def _encode_result(cols, rows, limit=12):
    lines = []
    for r in rows[:limit]:
        parts = []
        for c, v in zip(cols, r):
            parts.append("%s^%s" % (c, _norm_scalar(v)))
        lines.append("#".join(parts))
    s = " | ".join(lines)
    if len(rows) > limit:
        s += " | …共%d行" % len(rows)
    return s


# --------------------------------------------------------------------------- #
# 名称解析
# --------------------------------------------------------------------------- #
def find_object(desc, prefixes, default):
    m = re.search(r"(?:名称为|名为|名字为|叫做|命名为|表名(?:为)?)\s*[`'\"“”]?([A-Za-z_][A-Za-z0-9_]*)", desc)
    if m:
        return m.group(1)
    for pre in prefixes:
        mm = re.search(r"(%s[A-Za-z0-9_]*)" % pre, desc, re.IGNORECASE)
        if mm:
            return mm.group(1)
    return default


def find_table_column(desc):
    m = re.search(r"(?:将|把)?\s*([A-Za-z_][A-Za-z0-9_]*)\s*表?\s*中?\s*的?\s*([A-Za-z_][A-Za-z0-9_]*)\s*字段", desc)
    if m:
        return m.group(1), m.group(2)
    return None, None


# --------------------------------------------------------------------------- #
# 主判分
# --------------------------------------------------------------------------- #
def grade_mysql(dfdlist, sql_text, material_zip, full_score):
    tm = TempMysql(material_zip=material_zip)
    return _grade_with_instance(dfdlist, sql_text, tm, full_score)


def _read_text_any(path):
    """读学生保存的文本文件：优先 UTF-8，回退 GBK（记事本 ANSI 默认）。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def grade_mysql_folder(dfdlist, student_zip_path, material_zip, full_score, answer_zip=None):
    """原版工作流判分：学生上传整个考试文件夹 zip。
    文件夹内含：① Navicat 操作后的 datadir（库内改动已在其中）；
    ② 按题面要求保存的 tj*.sql / 触发器.txt / 存储过程.txt 等文件。
    判分：直接用学生 datadir 起实例检测库内状态（视图/触发器/存储过程/
    字段类型/复制表）；同时把根级 .sql/.txt 文件作为 SQL 文本执行（容错，
    覆盖学生只保存文件、未在库里执行的场景），SELECT 结果用于查询判分点。
    数据修改点（hf1111）用 answer_zip（material_1 标准答案快照）起对照实例，
    整表比对最终状态。找不到 datadir 时回退基线快照 + 执行文件 SQL。"""
    notes = []
    tmp = tempfile.mkdtemp(prefix="mysql_stu_")
    tm = None
    try:
        safe_extract_zip(student_zip_path, tmp)
        # 定位 datadir：包含 ibdata1 的目录（正常即在 zip 根级）
        datadir = None
        for root, _dirs, files in os.walk(tmp):
            if "ibdata1" in files:
                datadir = root
                break
        # 收集学生保存的 SQL 文本：仅根级（zip 根 = 考试文件夹）的 .sql/.txt，
        # 不下钻 db 子目录，避免误读 datadir 内部文件
        rootdir = datadir or tmp
        sql_files = []
        try:
            for fn in sorted(os.listdir(rootdir)):
                low = fn.lower()
                if low.endswith((".sql", ".txt")) and low not in ("_tmp_mysqld.log",):
                    p = os.path.join(rootdir, fn)
                    if os.path.isfile(p):
                        sql_files.append(fn)
        except Exception:
            pass
        sql_parts = []
        for fn in sql_files:
            try:
                sql_parts.append("-- ===== %s =====\n%s" % (fn, _read_text_any(os.path.join(rootdir, fn))))
            except Exception:
                pass
        sql_text = "\n".join(sql_parts)
        if sql_files:
            notes.append("已读取作答文件：" + "、".join(sql_files[:10]))
        else:
            notes.append("未在考试文件夹中找到 tj*.sql / 模板 .txt 作答文件")
        tm = None
        if datadir:
            try:
                cand = TempMysql(src_dir=datadir)
                cand.start()
                tm = cand
                notes.append("使用学生数据库目录判分")
            except Exception as e:
                notes.append("学生数据库目录加载失败（%s），改用基线快照 + 作答文件判分"
                             % str(e).splitlines()[0][:60])
                try:
                    cand.cleanup()
                except Exception:
                    pass
        if tm is None:
            if not datadir:
                notes.append("未找到学生数据库目录，按基线快照 + 作答文件判分")
            tm = TempMysql(material_zip=material_zip)
            tm.start()
        r = _grade_with_instance(dfdlist, sql_text, tm, full_score,
                                 started=True, answer_zip=answer_zip)
        r["note"] = "；".join(notes) + "。" + r["note"]
        return r
    finally:
        # 任何路径都必须停掉临时实例（异常时 _grade_with_instance 可能未执行，
        # 不清理会残留 mysqld 进程 → 内存耗尽 → 后续判分全部 VirtualAlloc 失败）
        if tm is not None:
            try:
                tm.cleanup()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def _norm_table_rows(conn, table):
    """读整表并归一化排序，用于学生库 vs 标准答案库比对。"""
    cur = conn.cursor()
    cur.execute("SELECT * FROM `%s`" % str(table).replace("`", ""))
    rows = sorted(tuple(_norm_scalar(v) for v in r) for r in cur.fetchall())
    return rows


def _modify_table_name(desc):
    """从数据修改点题干中解析目标表名，如「将test1表中…」「把tb_xx表…」。"""
    m = re.search(r"(?:将|把|对|在)?\s*([A-Za-z_][A-Za-z0-9_]*)\s*表", desc or "")
    return m.group(1) if m else None


def _grade_with_instance(dfdlist, sql_text, tm, full_score, started=False, answer_zip=None):
    result = {"auto": True, "score": 0.0, "total": len(dfdlist), "note": "",
              "hits": [], "miss": [], "details": []}
    answer_tm = None
    try:
        if not started:
            tm.start()
        conn = tm.conn
        # 选择题库（dfd 的 startMark，通常为业务库名）
        dbname = None
        for d in dfdlist:
            if d.get("startMark"):
                dbname = d["startMark"]
                break
        if dbname:
            conn.cursor().execute("USE `%s`" % str(dbname).replace("`", ""))

        def get_answer_conn():
            """标准答案快照对照实例（惰性启动，仅数据修改点需要）。"""
            nonlocal answer_tm
            if not answer_zip or not os.path.exists(answer_zip):
                return None
            if answer_tm is None:
                answer_tm = TempMysql(material_zip=answer_zip)
                answer_tm.start()
                if dbname:
                    answer_tm.conn.cursor().execute("USE `%s`" % str(dbname).replace("`", ""))
            return answer_tm.conn

        # 1) 执行学生 SQL
        exec_notes = []
        select_results = []  # (sql, cols, rows)
        for st in split_sql(sql_text or ""):
            try:
                cur = conn.cursor()
                cur.execute(st)
                # 只要该语句返回结果集（cursor.description 非空）就收集，
                # 不依赖语句开头关键字（语句可能带 -- 注释前缀）。
                if cur.description:
                    cols = [d[0] for d in cur.description]
                    rows = cur.fetchall()
                    select_results.append((st, cols, rows))
                while cur.nextset():
                    pass
                cur.close()
            except Exception as e:  # noqa
                exec_notes.append("%s…：%s" % (st[:24], e))

        used_select = [False] * len(select_results)

        def find_matching_select(yq):
            for idx, (s, cols, rows) in enumerate(select_results):
                if used_select[idx]:
                    continue
                ok, _ = compare_result(cols, rows, yq)
                if ok:
                    used_select[idx] = True
                    return True, _encode_result(cols, rows)
            # 未命中：返回最接近的第一条未用 SELECT 展示
            for idx, (s, cols, rows) in enumerate(select_results):
                if not used_select[idx]:
                    return False, _encode_result(cols, rows)
            return False, ""

        # 2) 逐判分点
        score = 0.0
        for d in dfdlist:
            hf = d.get("haveFuction")
            point = float(d.get("score") or 0)
            yq = d.get("yuqizhi") or ""
            desc = d.get("timuDesc") or ""
            correct = False
            student = ""
            try:
                if hf == 1111:
                    # 数据修改点：有标准答案快照时整表比对最终状态（忠实原版
                    # 双实例对照）；无快照/解析不到表名时回退作答文件 SELECT 匹配
                    correct, student = None, ""
                    try:
                        ac = get_answer_conn()
                    except Exception as e:
                        ac = None
                        student = "答案快照实例启动失败:%s" % e
                    if ac is not None:
                        t2 = _modify_table_name(desc)
                        if t2:
                            try:
                                srows = _norm_table_rows(conn, t2)
                                arows = _norm_table_rows(ac, t2)
                                correct = (srows == arows)
                                student = "[%s 学生%d行/答案%d行] %s" % (
                                    t2, len(srows), len(arows),
                                    "与标准答案一致" if correct else "与标准答案不一致")
                            except Exception as e:
                                student = "整表比对失败:%s" % e
                    if correct is None:
                        correct, student = find_matching_select(yq)
                elif hf == 1105:
                    correct, student = find_matching_select(yq)
                elif hf == 1107:
                    vname = find_object(desc, ["v_"], "v_scores")
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM `%s`" % vname)
                    cols = [c[0] for c in cur.description]
                    rows = cur.fetchall()
                    correct, student = compare_result(cols, rows, yq)
                    student = "[视图%s] " % vname + student
                elif hf == 1115:
                    correct, student = _grade_procedure(conn, desc, yq)
                elif hf == 1113:
                    correct, student = _grade_trigger(conn, desc, yq)
                elif hf == 1104:
                    bname = find_object(desc, ["tb_.*bak", "tb_.*_bak"], "tb_scores_bak1")
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=%s",
                        (bname,))
                    exists = cur.fetchone()[0]
                    if exists:
                        cur.execute("SELECT COUNT(*) FROM `%s`" % bname)
                        n = cur.fetchone()[0]
                    else:
                        n = None
                    exp = parse_expected(yq)
                    exp_n = None
                    if exp and "count(*)" in exp[0]:
                        try:
                            exp_n = int(float(exp[0]["count(*)"]))
                        except Exception:
                            exp_n = None
                    student = "count(*)^%s" % n
                    correct = n is not None and (exp_n is None or n == exp_n)
                elif hf == 1109:
                    correct, student = _grade_column_type(conn, desc, yq)
                else:
                    student = "暂不支持判分点 hf%s" % hf
            except Exception as e:  # noqa
                student = "检测异常：%s" % e
                correct = False

            if correct:
                score += point
                result["hits"].append("hf%s 达标" % hf)
            else:
                result["miss"].append("hf%s 未达标" % hf)
            result["details"].append({
                "dfdNo": d.get("dfdNo") or hf, "code": hf, "point": point,
                "get": point if correct else 0.0, "correct": correct,
                "student": student, "expect": yq,
            })

        # 按满分缩放（dfd 分值合计通常==full_score）
        raw_sum = sum(float(d.get("score") or 0) for d in dfdlist) or full_score
        result["score"] = round(score * (float(full_score) / raw_sum) if raw_sum else score, 1)
        notes = ["MySQL自动判分：%d/%d 个判分点达标" % (len(result["hits"]), len(dfdlist))]
        if exec_notes:
            notes.append("部分SQL执行报错：" + "；".join(exec_notes[:3]))
        result["note"] = "；".join(notes)
    finally:
        tm.cleanup()
        if answer_tm is not None:
            try:
                answer_tm.cleanup()
            except Exception:
                pass
    return result


def _grade_column_type(conn, desc, yq):
    exp = parse_expected(yq)
    if not exp:
        return False, yq
    table, column = find_table_column(desc)
    # 兜底：从期望反推不到表名时，用 desc 中 tb_ 开头
    if not table:
        mt = re.search(r"(tb_[A-Za-z0-9_]+)", desc)
        table = mt.group(1) if mt else ""
    if not column:
        mc = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*字段", desc)
        column = mc.group(1) if mc else ""
    cur = conn.cursor()
    cur.execute(
        """SELECT DATA_TYPE, COLUMN_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE
           FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s""",
        (table, column))
    row = cur.fetchone()
    if not row:
        return False, "未找到 %s.%s" % (table, column)
    data_type, column_type, prec, scale = row
    # 构造学生实际类型描述
    if data_type in ("float", "double", "decimal"):
        actual = "Type^%s(%s,%s)" % (data_type, prec, scale)
    else:
        actual = "COLUMN_TYPE^%s#" % column_type
    # 期望
    want = yq.strip()
    ok = False
    if want.startswith("Type^"):
        wt = want[len("Type^"):]
        m = re.match(r"([a-z]+)\((\d+),(\d+)\)", wt)
        if m:
            ok = data_type == m.group(1) and int(prec) == int(m.group(2)) and int(scale) == int(m.group(3))
    elif want.startswith("COLUMN_TYPE^"):
        wt = want[len("COLUMN_TYPE^"):].rstrip("#").replace(" ", "")
        ok = column_type.replace(" ", "") == wt
    return ok, actual


def _grade_procedure(conn, desc, yq):
    pname = find_object(desc, ["pro_", "proc_", "sp_"], "pro_GetTopStudent")
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=DATABASE() AND ROUTINE_NAME=%s",
        (pname,))
    if not cur.fetchone()[0]:
        return False, "存储过程 %s 不存在" % pname
    # 遍历课程，验证过程返回该课程最高分学生；并找期望姓名
    want = yq.strip()
    cur.execute("SELECT cid FROM tb_courses ORDER BY CAST(cid AS UNSIGNED)")
    cids = [r[0] for r in cur.fetchall()]
    correct_courses = 0
    found_want = False
    for cid in cids:
        # 该课程真实最高分学生
        cur.execute(
            """SELECT s.stuname FROM tb_scores sc JOIN tb_students s ON s.stuid=sc.student_id
               WHERE sc.course_id=%s ORDER BY sc.score DESC LIMIT 1""", (cid,))
        rr = cur.fetchone()
        top = rr[0] if rr else None
        try:
            cur.execute("CALL `%s`(%s, @_out)" % (pname, _sql_lit(cid)))
            while cur.nextset():
                pass
            cur.execute("SELECT @_out")
            got = cur.fetchone()[0]
        except Exception:
            got = None
        if got is not None and got == top and top is not None:
            correct_courses += 1
        if got == want and want:
            found_want = True
    ok = correct_courses >= max(1, int(len(cids) * 0.6)) and (found_want or not want)
    return ok, "存储过程%s：正确返回%d/%d门课最高分学生，期望姓名“%s”%s" % (
        pname, correct_courses, len(cids), want, "已出现" if found_want else "未出现")


def _grade_trigger(conn, desc, yq):
    tname = find_object(desc, ["tri_", "trg_"], "tri_updateAgeGroup")
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME=%s",
        (tname,))
    if not cur.fetchone()[0]:
        return False, "触发器 %s 不存在" % tname
    exp = parse_expected(yq)
    want_col, want_val = None, None
    if exp:
        for k, v in exp[0].items():
            if k != "_extra":
                want_col, want_val = k, v
    # 在事务里：更新一个学生生日为“中年”区间，看 age_group
    conn.autocommit(False)
    got = None
    try:
        cur.execute("SELECT stuid FROM tb_students LIMIT 1")
        sid = cur.fetchone()[0]
        cur.execute("UPDATE tb_students SET birthday='1990-06-01' WHERE stuid=%s", (sid,))
        cur.execute("SELECT %s FROM tb_students WHERE stuid=%s" % (want_col or "age_group", sid))
        got = cur.fetchone()[0]
        conn.rollback()
    finally:
        conn.autocommit(True)
    ok = str(got) == str(want_val)
    return ok, "%s^%s（更新生日为1990-06-01触发）" % (want_col or "age_group", got)


def _sql_lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


# 自测
if __name__ == "__main__":  # pragma: no cover
    here = HERE
    import sqlite3
    import json as _json
    db = sqlite3.connect(os.path.join(here, "exam.db"))
    db.row_factory = sqlite3.Row
    tid = "202312011842463092"
    q = db.execute("SELECT * FROM operate_timu WHERE timuID=?", (tid,)).fetchone()
    dfd = _json.loads(q["dfdList"])
    mat = os.path.join(q["folder"], "material.zip")
    test_sql = open(os.path.join(here, "..", "..", "_work", "correct_mysql.sql"), encoding="utf-8").read()
    r = grade_mysql(dfd, test_sql, mat, float(q["score"]))
    print(r["score"], r["note"])
    for d in r["details"]:
        print(("✓" if d["correct"] else "✗"), d["code"], "::", str(d["student"])[:90])
