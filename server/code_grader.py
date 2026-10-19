# -*- coding: utf-8 -*-
"""Python 编程操作题自动判分（OJ 式）。
题干里带有“示例/样例：输入… 输出…”，本模块解析这些用例，
把学生代码放到临时目录、以子进程运行并喂入 stdin，逐例比对 stdout。
所有异常都被捕获，绝不向上抛，失败时返回 score=None 交由上层兜底。"""
import os
import re
import sys
import shutil
import subprocess
import tempfile

RUN_TIMEOUT = 10          # 单个用例最长运行秒数
MAX_OUTPUT = 2000         # 输出比对最大字符数

_PY_PREFIX = None


def _python_prefix():
    """解析真实 Python 解释器命令前缀。
    源码运行直接用 sys.executable；打包成 exe 后 sys.executable 是 Server.exe
    自身（拿去跑 ans.py 会拉起服务端 GUI 直到超时），必须找真实解释器：
    随包 runtime/python → PATH python/python3 → py 启动器。"""
    global _PY_PREFIX
    if _PY_PREFIX:
        return _PY_PREFIX
    if not getattr(sys, "frozen", False):
        _PY_PREFIX = [sys.executable]
        return _PY_PREFIX
    try:
        from app_paths import BASE_DIR
        cand = os.path.join(BASE_DIR, "runtime", "python", "python.exe")
        if os.path.isfile(cand):
            _PY_PREFIX = [cand]
            return _PY_PREFIX
    except Exception:
        pass
    for name in ("python", "python3"):
        p = shutil.which(name)
        # 排除 WindowsApps 商店占位 stub（执行会打开应用商店）
        if p and "WindowsApps" not in p:
            _PY_PREFIX = [p]
            return _PY_PREFIX
    p = shutil.which("py")
    if p:
        _PY_PREFIX = [p, "-3"]
        return _PY_PREFIX
    _PY_PREFIX = [sys.executable]  # 兜底（大概率超时失败，但不阻断判分流程）
    return _PY_PREFIX


def _clean_block(s):
    if s is None:
        return ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in s.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines).strip()


def _norm_io(s):
    """规范化输入/输出用于比对：统一冒号、去每行首尾空白、去空行。"""
    if s is None:
        return ""
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("：", ":")
    out = []
    for ln in s.split("\n"):
        t = ln.strip().strip('"').strip("'").strip()
        if t:
            out.append(re.sub(r"[ \t]+", " ", t))
    return "\n".join(out)


def parse_examples(stem):
    """解析题干中的示例用例，支持两种排版：
    A. 多行式：示例N 块内“输入：…\\n输出：…”；
    B. 表格行：“示例N:  <输入>     <输出>”（输入/输出在同一行，以 2+ 空格分列）。
    返回 [(input, expected_output), ...]。"""
    if not stem:
        return []
    s = stem.replace("\r\n", "\n")

    # —— 模式 B：表格对齐行 ——
    table_cases = []
    row_re = re.compile(r"^\s*(?:示例|样例)\s*\d*\s*[：:.]?\s+(\S.*?)\s{2,}(\S.*?)\s*$")
    for line in s.split("\n"):
        m = row_re.match(line)
        if not m:
            continue
        inp = _clean_block(m.group(1))
        outp = _clean_block(re.sub(r"\s{2,}", " ", m.group(2)))
        if inp and outp:
            table_cases.append((inp, outp))

    # —— 模式 A：示例块内多行“输入:/输出:” ——
    block_cases = []
    marks = [m.start() for m in re.finditer(r"(?:示例|样例)\s*\d*[：:.]?", s)]
    if marks:
        bounds = marks + [len(s)]
        for k in range(len(marks)):
            blk = s[bounds[k]:bounds[k + 1]]
            mi = re.search(r"输\s*入\s*[:：]?", blk)
            mo = re.search(r"输\s*出\s*[:：]?", blk)
            if not mi or not mo or mo.start() <= mi.end():
                continue
            inp = blk[mi.end():mo.start()]
            tail = blk[mo.end():]
            for kw in ["注意", "说明", "------", "======", '"""', "Program", "End", "要求"]:
                j = tail.find(kw)
                if j != -1:
                    tail = tail[:j]
            inp, outp = _clean_block(inp), _clean_block(tail)
            if not inp or not outp:
                continue
            zh = len(re.findall(r"[\u4e00-\u9fff]", inp))
            if zh > max(2, len(inp) * 0.5) or len(inp) > 200:
                continue
            block_cases.append((inp, outp))

    raw = table_cases if table_cases else block_cases
    uniq, seen = [], set()
    for pair in raw:
        key = (_norm_io(pair[0]), _norm_io(pair[1]))
        if key not in seen:
            seen.add(key)
            uniq.append(pair)
    return uniq


def _run_one(path, inp, workdir):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # -I 隔离用户站点/环境变量，-X utf8 强制标准流 UTF-8（命令行 -X 不被 -I 忽略）
    p = subprocess.run(
        _python_prefix() + ["-I", "-X", "utf8", path],
        input=inp, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=RUN_TIMEOUT, cwd=workdir, env=env,
    )
    return (p.stdout or "")[:MAX_OUTPUT], (p.stderr or "")


def grade_python(code, stem, full_score=20.0):
    """返回 (score,total,hits,miss,note)。score=None 表示无法判分。"""
    try:
        code = code or ""
        # 题面硬规则：“仅在 Program-End 标志之间填入语句，删除标志不得分”。
        # 题干预留标志而学生代码中标志丢失 → 直接 0 分。
        if re.search(r"#\*{3,}\s*Program\s*\*{3,}", stem or "") and \
                not (re.search(r"#\*{3,}\s*Program\s*\*{3,}", code) and
                     re.search(r"#\*{3,}\s*End\s*\*{3,}", code)):
            if code.strip():
                return 0, 1, [], ["未保留 Program/End 标志"], \
                    "Python题：删除/修改了 Program-End 标志，按题面要求不得分"
            return 0, 1, [], ["未提交代码"], "Python题：未作答"
        cases = parse_examples(stem or "")
        if not cases:
            if not code.strip():
                return 0, 1, [], ["未提交代码"], "Python题：未作答"
            try:
                compile(code, "<answer>", "exec")
            except Exception as e:
                return 0, 1, [], [f"代码存在语法错误: {e}"], "Python题：语法错误"
            sc = round(full_score * 0.6, 1)
            return sc, 1, ["代码语法正确（本题无标准用例，按可运行性给基础分）"], [], \
                "Python题：无标准示例，按语法正确给60%基础分"
        try:
            compile(code, "<answer>", "exec")
        except Exception as e:
            return 0, len(cases), [], [f"代码存在语法错误: {e}"], \
                "Python题：语法错误，0/%d 例通过" % len(cases)
        hits, miss, passed = [], [], 0
        with tempfile.TemporaryDirectory(prefix="pygrade_") as td:
            path = os.path.join(td, "ans.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write(code)
            for i, (inp, exp) in enumerate(cases, 1):
                try:
                    got, err = _run_one(path, inp, td)
                    if _norm_io(got) == _norm_io(exp):
                        passed += 1
                        hits.append("示例%d通过（输入 %s）" % (
                            i, _norm_io(inp)[:20].replace("\n", " ")))
                    else:
                        tail = ("；报错:" + err.strip().splitlines()[-1][:40]) if err.strip() else ""
                        miss.append("示例%d输出不符：期望「%s」，得到「%s」%s" % (
                            i, _norm_io(exp)[:40].replace("\n", " "),
                            _norm_io(got)[:40].replace("\n", " "), tail))
                except subprocess.TimeoutExpired:
                    miss.append("示例%d运行超时（>%ds，疑似死循环）" % (i, RUN_TIMEOUT))
                except Exception as e:
                    miss.append("示例%d运行异常: %s" % (i, type(e).__name__))
        total = len(cases)
        score = round(full_score * passed / total, 1)
        note = "Python运行判分：通过 %d/%d 个示例" % (passed, total)
        if passed < total:
            note += "（请核对输入输出格式）"
        return score, total, hits, miss, note
    except Exception as e:
        return None, 0, [], [], "Python判分异常: %s" % e
