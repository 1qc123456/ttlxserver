# -*- coding: utf-8 -*-
"""Access 操作题 Python 判分器（原版判分 DLL 不含 Access 逐点判分，此处自建）。
数据来源：accdb/access_probe.ps1（32位 DAO，输出全结构 JSON）。"""
import json, os, re, subprocess, sys
from app_paths import BASE_DIR as HERE

PS_X86 = r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
PROBE = os.path.join(HERE, "accdb", "access_probe.ps1")

DAO_TEXT, DAO_MEMO, DAO_BOOL, DAO_DATE, DAO_OLE = 10, 12, 1, 8, 11
DAO_INT, DAO_LONG, DAO_BYTE, DAO_SINGLE, DAO_DOUBLE, DAO_CURRENCY = 3, 4, 2, 6, 7, 5


def _norm(v):
    if v is None:
        return ""
    s = str(v).strip().lower()
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    s = s.replace("；", ";").replace("，", ",").replace("（", "(").replace("）", ")")
    s = re.sub(r"\s+", "", s)
    return s


def _eq(a, b):
    return _norm(a) == _norm(b)


def probe(accdb_path):
    p = subprocess.run([PS_X86, "-ExecutionPolicy", "Bypass", "-File", PROBE,
                        "-DbPath", accdb_path], capture_output=True, timeout=180)
    txt = p.stdout.decode("utf-8", "ignore")
    return json.loads(txt)


def _field(data, table, field):
    t = data.get("tables", {}).get(table)
    if not t:
        return None, None
    for f in t.get("fields", []):
        if _eq(f.get("name"), field):
            return f, t
    return None, t


def _has_table(data, name):
    return any(_eq(k, name) for k in data.get("tables", {}).keys())


def _has_field(data, table, field):
    return _field(data, table, field)[0] is not None


def check(hf, d, data):
    sm = d.get("startMark"); em = d.get("endMark"); yq = d.get("yuqizhi")
    table = sm
    field = em or yq

    def F():
        return _field(data, table, field)[0]

    if hf == 811:
        return _has_field(data, sm, yq), ("存在" if _has_field(data, sm, yq) else "不存在"), yq
    if hf == 809:
        ok = not _has_field(data, sm, (em or yq))
        return ok, ("已删除" if ok else "仍存在"), em or yq
    if hf == 810:
        t = data.get("tables", {}).get(sm)
        pk = [str(x) for x in (t or {}).get("primary", [])]
        ok = any(_eq(x, (em or yq)) for x in pk)
        return ok, ";".join(pk), em or yq
    if hf in (823, 865):
        f = F(); sz = (f or {}).get("FieldSize")
        return _eq(sz, yq), sz, yq
    if hf == 813:
        f = F(); ok = f is not None and f.get("type") == DAO_TEXT
        return ok, (f.get("type") if f else None), "文本(10)"
    if hf == 812:
        f = F()
        return (f is not None and f.get("type") == DAO_TEXT), (f.get("type") if f else None), yq
    if hf == 817:
        f = F(); ok = f is not None and f.get("type") == DAO_BOOL
        return ok, (f.get("type") if f else None), "是/否(1)"
    if hf == 818:
        f = F(); ok = f is not None and f.get("type") == DAO_OLE
        return ok, (f.get("type") if f else None), "OLE(11)"
    if hf == 814:
        f = F(); ok = f is not None and f.get("type") == DAO_MEMO
        return ok, (f.get("type") if f else None), "备注(12)"
    if hf == 862:
        f = F(); ok = f is not None and str(f.get("ColumnHidden")).lower() == "true"
        return ok, (f or {}).get("ColumnHidden"), "隐藏"
    if hf == 863:
        f = F()
        return _eq((f or {}).get("Caption"), yq), (f or {}).get("Caption"), yq
    if hf == 3307:
        f = F()
        return _eq((f or {}).get("Description"), yq), (f or {}).get("Description"), yq
    if hf == 843:
        f = F()
        return _eq((f or {}).get("DecimalPlaces"), yq), (f or {}).get("DecimalPlaces"), yq
    if hf == 3457:
        f = F(); ok = f is not None and str(f.get("AllowZeroLength")).lower() == "false"
        return ok, (f or {}).get("AllowZeroLength"), "False"

    if hf == 887:
        f = F(); dv = (f or {}).get("DefaultValue")
        sv = str(dv).strip()
        if len(sv) >= 2 and sv[0] == '"' and sv[-1] == '"':
            sv = sv[1:-1]  # 文本默认值 DAO 带成对引号
        return _eq(sv, yq), dv, yq
    if hf == 835:
        f = F()
        return _eq((f or {}).get("ValidationRule"), yq), (f or {}).get("ValidationRule"), yq
    if hf == 834:
        f = F()
        return _eq((f or {}).get("ValidationText"), yq), (f or {}).get("ValidationText"), yq
    if hf == 826:
        f = F()
        return _eq((f or {}).get("InputMask"), yq), (f or {}).get("InputMask"), yq
    if hf == 842:
        f = F()
        return _eq((f or {}).get("Format"), yq), (f or {}).get("Format"), yq
    if hf == 841:
        f = F()
        return _eq((f or {}).get("RowSource"), yq), (f or {}).get("RowSource"), yq
    if hf == 867:
        f = F()
        return _eq((f or {}).get("RowSourceType"), yq), (f or {}).get("RowSourceType"), yq

    if hf in (888, 889):
        found = None
        for r in data.get("relations", []):
            if str(r.get("table", "")).startswith("MSys"):
                continue
            if _eq(r.get("table"), sm) and _eq(r.get("foreignTable"), yq):
                found = r; break
        if found is None:
            return False, "无关系", (str(sm) + "→" + str(yq))
        if hf == 889 and not found.get("referentialIntegrity"):
            return False, "未实施参照完整性", "实施参照完整性"
        return True, (str(sm) + "→" + str(found.get("foreignTable"))), (str(sm) + "→" + str(yq))

    if hf == 821:
        ok = any(_eq(k, (sm or yq)) for k in data.get("queries", {}).keys())
        return ok, ("存在" if ok else "不存在"), sm or yq
    if hf == 886:
        sql = next((v for k, v in data.get("queries", {}).items() if _eq(k, sm)), None)
        if sql is None:
            return False, "无查询", str(yq)
        parts = [x for x in re.split(r"\s+", str(yq)) if x]
        missing = [x for x in parts if _norm(x) not in _norm(sql)]
        return (len(missing) == 0), sql, yq
    if hf == 2003:
        sql = next((v for k, v in data.get("queries", {}).items() if _eq(k, sm)), None)
        want = "orderby" + _norm(em) + _norm(yq)
        return (sql is not None and want in _norm(sql)), sql, (str(em) + " " + str(yq))
    if hf == 3452:
        sql = next((v for k, v in data.get("queries", {}).items() if _eq(k, sm)), None)
        return (sql is not None and _norm(yq) in _norm(sql)), sql, yq

    if hf == 890:
        try:
            spec = json.loads(yq); tname = next(iter(spec.keys()))
            rows = (data.get("tables", {}).get(tname) or {}).get("rows", [])
            for wantrow in spec[tname]:
                if not any(all(_eq(r.get(k), v) for k, v in wantrow.items()) for r in rows):
                    return False, "缺少记录", json.dumps(wantrow, ensure_ascii=False)
            return True, "记录齐全", yq
        except Exception as e:
            return False, str(e), yq
    if hf == 2005:
        try:
            spec = json.loads(yq); cond = spec["rows"][0]
            tname = spec.get("table") or sm
            t = data.get("tables", {}).get(tname) or data["tables"][sm]
            for r in t.get("rows", []):
                if all(_eq(r.get(k), v) for k, v in cond.items()):
                    return False, "记录仍存在", json.dumps(cond, ensure_ascii=False)
            return True, "已删除", json.dumps(cond, ensure_ascii=False)
        except Exception as e:
            return False, str(e), yq

    if hf == 808:
        ok = _has_table(data, yq)
        return ok, ("存在" if ok else "不存在"), yq
    if hf == 2004:
        ok = not _has_table(data, yq)
        return ok, ("已删除" if ok else "仍存在"), yq

    return None, None, None


FORM_REPORT_HF = {3258, 3260, 3261, 3262, 3263, 3277, 3278, 3279, 3422, 3423,
                  3424, 3425, 3426, 3427, 3429, 3430, 3435, 3436, 3440, 3441,
                  3442, 3443, 3445, 3446, 3447, 3448, 3449, 3451, 3558, 3561,
                  3562, 3563, 3564, 3326, 3327, 3329, 3330, 3331, 3332}


def grade(dfdlist, accdb_path):
    data = probe(accdb_path)
    details = []
    score = 0.0
    covered = 0
    for d in dfdlist:
        hf = d.get("haveFuction")
        point = float(d.get("score") or 0)
        try:
            ok, stu, std = check(hf, d, data)
        except Exception as e:
            ok, stu, std = False, ("判分异常:" + str(e)), d.get("yuqizhi")
        if ok is None:
            status = "pending" if hf in FORM_REPORT_HF else "unsupported"
            get = 0.0
        else:
            covered += 1
            status = "right" if ok else "wrong"
            get = round(point, 2) if ok else 0.0
            score += get
        details.append({"dfdNo": d.get("dfdId"), "point": round(point, 2),
                        "get": round(get, 2), "status": status,
                        "student": "" if stu is None else str(stu),
                        "standard": "" if std is None else str(std)})
    return {"auto": True, "total": len(dfdlist), "covered": covered,
            "score": round(score, 2), "details": details}


if __name__ == "__main__":
    r = grade(json.load(open(sys.argv[2], encoding="utf-8")), sys.argv[1])
    print(json.dumps(r, ensure_ascii=False, indent=2))
