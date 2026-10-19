# -*- coding: utf-8 -*-
"""
天天乐学替代教学系统 - TCP 服务端
单端口 5943，帧协议 = 4字节大端长度 + UTF8 JSON。
依赖：Python 标准库 + sqlite3。启动：python server.py
"""
import asyncio, json, sqlite3, hashlib, os, sys, io, secrets, datetime, base64, zipfile, re, time, socket
from app_paths import BASE_DIR
from concurrent.futures import ThreadPoolExecutor
from operate_grader import docx_features, grade_against_requirements, xlsx_features, pptx_features, grade_xlsx_pptx
from vapp_grader import grade_vapp
from code_grader import grade_python
from mysql_grader import grade_mysql, grade_mysql_folder
from sub6_grader import grade_sub6
from psd_grader import grade_psd
from judge_bridge import grade_by_engine, engine_available, VAPP_TYPES as JB_VAPP, FILE_TYPES as JB_FILE

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB = os.path.join(BASE_DIR, "exam.db")
HOST, PORT = "0.0.0.0", 5943

# Office/文件判分走本机 COM 且单题可达数十秒，60 人集中交卷不能同步等待：
# 交卷立即返回“判分中”，由后台判分池异步判分，完成后回写并刷新总分。
# 旧方案 max_workers=1 全串行；现 judge_bridge 已把 Office 互斥锁按“应用”拆分
# (WinWord/Excel/PowerPnt 各一把)，不同应用可并行，PSD(Aspose) 完全自由并行。
# 因此把池提到 4：Word/Excel/PPT/PSD 四类可同时各跑一个，理论 4 路并行。
OFFICE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="office-judge")
# MySQL 判分会为每份作答启动一个临时 mysqld 实例（占内存），并发不宜过大，3 路并行。
MYSQL_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mysql-judge")  # 判分实例各占数百MB内存，串行防小内存机崩溃
# vapp/轻量判分通用池：asyncio 默认池上限偏小，调大以支撑高并发并行判分。
GENERAL_POOL = ThreadPoolExecutor(max_workers=48, thread_name_prefix="judge")

COURSES = {
    1: "模块1：信息与数据、计算机发展",
    2: "模块2：进制/数据表示/字符编码",
    3: "模块3：操作系统与文件管理",
    4: "模块4：信息安全/病毒/版权",
    5: "模块5：计算机硬件",
    6: "模块6：软件与程序设计基础",
    7: "模块7：多媒体/平面设计",
    8: "模块8：计算机网络",
    9: "模块9：行业概念与术语",
}

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------------- 操作日志（审计） ----------------
def _ensure_audit_table():
    try:
        c = sqlite3.connect(DB, timeout=30.0)
        c.execute("""CREATE TABLE IF NOT EXISTS audit_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, username TEXT, display_name TEXT, role TEXT,
            action TEXT, detail TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts)")
        c.commit(); c.close()
    except Exception as e:
        print("audit 表初始化失败(忽略):", e)

_ensure_audit_table()

def _audit(ctx, action, detail=""):
    """记录一次操作到 audit_logs。ctx 无用户信息时按 system 记录。"""
    try:
        u = (ctx or {}).get("user") or {}
        c = sqlite3.connect(DB, timeout=30.0)
        c.execute("INSERT INTO audit_logs(ts,username,display_name,role,action,detail) VALUES(?,?,?,?,?,?)",
                  (now(), u.get("username", ""), u.get("displayName", ""),
                   u.get("role", ""), action, str(detail)[:1000]))
        c.commit(); c.close()
    except Exception as e:
        print("audit 记录失败(忽略):", e)

# ---------------- 练习/考试实时监控 ----------------
# username -> {ts, name, classId, mode, id, current, qids, answered:[qid], wrong:[qid], submitted}
MONITOR = {}

async def h_heartbeat(msg, ctx):
    """学生端答题心跳：上报当前题/已答/错题，服务端实时聚合供教师端监控"""
    u = ctx.get("user") or {}
    if u.get("role") != "student":
        return {"code": 0}
    try:
        mode = msg.get("mode", "practice")
        st = MONITOR.setdefault(u["username"], {})
        st.update({"ts": time.time(), "name": u.get("displayName", ""),
                   "classId": u.get("classId"), "mode": mode,
                   "id": msg.get("id", 0), "current": str(msg.get("current") or ""),
                   "qids": msg.get("qids") or [], "submitted": bool(msg.get("submitted"))})
        answered = msg.get("answered") or []
        ans_map = {}
        for a in answered:
            if a.get("qid") is not None:
                ans_map[str(a["qid"])] = a
        st["answered"] = list(ans_map.keys())
        # 选择题现场判对错（学生端不上传标准答案，服务端比对）
        wrong = []
        c = sqlite3.connect(DB, timeout=30.0)
        try:
            for qid, a in ans_map.items():
                if a.get("refType") != "obj":
                    continue
                try:
                    sel = int(a.get("sel") or -1)
                except Exception:
                    continue
                if sel < 0 or sel >= 6:
                    continue
                row = c.execute("SELECT standardAnwser FROM questions WHERE queNo=?", (qid,)).fetchone()
                if not row:
                    continue
                ua = ["A", "B", "C", "D", "E", "F"][sel]
                if norm_ans(ua) != norm_ans(row["standardAnwser"] or ""):
                    wrong.append(qid)
        finally:
            c.close()
        st["wrong"] = wrong
    except Exception as e:
        print("heartbeat err:", e)
    return {"code": 0}

async def h_monitor_list(msg, ctx):
    """教师端轮询：返回所有学生的实时作答状态（30 秒内有心跳算在线）"""
    now_ts = time.time()
    out = []
    for uname, st in list(MONITOR.items()):
        online = (now_ts - st.get("ts", 0)) <= 30
        out.append({"username": uname, "name": st.get("name", ""),
                    "classId": st.get("classId"), "online": online,
                    "lastSeen": st.get("ts", 0), "mode": st.get("mode", "practice"),
                    "id": st.get("id", 0), "current": st.get("current", ""),
                    "qids": st.get("qids", []), "answered": st.get("answered", []),
                    "wrong": st.get("wrong", []), "submitted": st.get("submitted", False)})
    out.sort(key=lambda x: (not x["online"], x["username"]))
    return {"code": 0, "students": out}

async def h_audit_logs(msg, ctx):
    """教师端查询账号操作日志"""
    limit = min(int(msg.get("limit", 200)), 1000)
    c = opencur()
    rows = c.execute("SELECT ts,username,display_name,role,action,detail FROM audit_logs ORDER BY id DESC LIMIT ?",
                     (limit,)).fetchall()
    c.close()
    return {"code": 0, "logs": [{"ts": r["ts"], "username": r["username"], "name": r["display_name"],
                                 "role": r["role"], "action": r["action"], "detail": r["detail"]} for r in rows]}

def opencur():
    c = sqlite3.connect(DB, timeout=30.0)
    c.row_factory = sqlite3.Row
    # WAL 下写仍串行，busy_timeout 让锁冲突等待而非立即 OperationalError
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA synchronous=NORMAL")
    return c

def _migrate():
    """兼容旧库：按需加列"""
    if not os.path.exists(DB):
        return
    c = sqlite3.connect(DB)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.commit()
    except Exception as e:
        print("WAL 启用失败(忽略):", e)
    cols = [r[1] for r in c.execute("PRAGMA table_info(homeworks)").fetchall()]
    if "show_result" not in cols:
        c.execute("ALTER TABLE homeworks ADD COLUMN show_result INTEGER DEFAULT 1")
        c.commit()
    if "start_at" not in cols:
        c.execute("ALTER TABLE homeworks ADD COLUMN start_at TEXT")
        c.commit()
    if "end_at" not in cols:
        c.execute("ALTER TABLE homeworks ADD COLUMN end_at TEXT")
        c.commit()
    # 练习成绩记录表：学生每次自主练习提交记一条（练习不占 records，独立于考试）
    c.execute("""CREATE TABLE IF NOT EXISTS practice_records(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER, homework_id INTEGER DEFAULT 0,
        mode TEXT DEFAULT 'practice',
        score REAL DEFAULT 0, total REAL DEFAULT 0, qcount INTEGER DEFAULT 0,
        detail TEXT, created_at TEXT)""")
    c.commit()
    acols = [r[1] for r in c.execute("PRAGMA table_info(answers)").fetchall()]
    if "grade_detail" not in acols:
        c.execute("ALTER TABLE answers ADD COLUMN grade_detail TEXT")
        c.commit()
    # answers 唯一约束：保证 (record_id,question_id,ref_type) 唯一。
    # 旧流程用 INSERT OR REPLACE 在无约束时退化为重复插入，会造成一题多行、
    # 交卷取到错误行；上传判分也依赖该约束做 UPSERT。
    ux = c.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='ux_ans_rqr'").fetchone()
    if not ux:
        try:
            c.execute("""DELETE FROM answers WHERE id NOT IN
                         (SELECT MAX(id) FROM answers GROUP BY record_id,question_id,ref_type)""")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_ans_rqr ON answers(record_id,question_id,ref_type)")
            c.commit()
        except Exception as e:
            c.rollback()
            print("answers 唯一索引迁移失败(忽略，仍可运行):", e)
    c.close()

_migrate()

def norm_ans(s):
    if s is None: return ""
    s = str(s).strip().upper().replace("，", ",").replace(" ", "")
    # 判断题同义词归一
    mp = {"正确":"对","错误":"错","√":"对","×":"错","T":"对","F":"错","TRUE":"对","FALSE":"错","RIGHT":"对","WRONG":"错"}
    if s in mp: s = mp[s]
    if "," in s or s in ("对","错"): return s
    # 多选：A,B -> AB
    return "".join(sorted(s.replace(",", "")))

# ---------------- 各 cmd 处理 ----------------
async def h_login(msg, ctx):
    c = opencur()
    r = c.execute("SELECT * FROM users WHERE username=?", (msg["username"],)).fetchone()
    c.close()
    if not r:
        return {"code":1, "msg":"用户不存在"}
    h = hashlib.sha256((r["salt"]+msg["password"]).encode()).hexdigest()
    if h != r["pwd_hash"]:
        return {"code":2, "msg":"密码错误"}
    ctx["user"] = {"id":r["id"], "username":r["username"], "role":r["role"],
                   "displayName":r["display_name"], "classId":r["class_id"]}
    _audit(ctx, "登录", "登录成功")
    return {"code":0, "msg":"ok", "role":r["role"], "userId":r["id"],
            "displayName":r["display_name"], "classId":r["class_id"]}

async def h_list_courses(msg, ctx):
    return {"code":0, "courses":[{"course":k,"name":v} for k,v in COURSES.items()]}

async def h_list_classes(msg, ctx):
    c = opencur()
    rows = c.execute("SELECT id,name,teacher_id FROM classes").fetchall()
    c.close()
    return {"code":0, "classes":[{"id":r["id"],"name":r["name"]} for r in rows]}

# ---- 学生 ----
def _hw_window_state(start_at, end_at, submitted=False):
    """考试时间窗判定：返回 (state, msg)
    state: 0=可参加 1=未开始 2=已结束 3=已提交(不可重复)"""
    import datetime
    if submitted:
        return 3, "已提交，不可重复参加"
    now_s = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if start_at:
        if now_s < start_at:
            return 1, "考试未开始，开始时间：%s" % start_at
    if end_at:
        if now_s > end_at:
            return 2, "考试已结束，结束时间：%s" % end_at
    return 0, ""

async def h_stu_homeworks(msg, ctx):
    u = ctx["user"]
    c = opencur()
    rows = c.execute("""SELECT h.id,h.title,h.qtype,h.status,h.created_at,h.duration_sec,h.show_result,
                               h.start_at,h.end_at,h.kind,
                               (SELECT score FROM records WHERE homework_id=h.id AND student_id=? ORDER BY id DESC LIMIT 1) AS myscore,
                               (SELECT status FROM records WHERE homework_id=h.id AND student_id=? ORDER BY id DESC LIMIT 1) AS mystatus
                        FROM homeworks h WHERE h.class_id=? AND h.status='published'
                        ORDER BY h.id DESC""", (u["id"], u["id"], u["classId"])).fetchall()
    c.close()
    out=[]
    for r in rows:
        item={"id":r["id"],"title":r["title"],"qtype":r["qtype"],
              "status":r["status"],"createdAt":r["created_at"],"durationSec":r["duration_sec"],
              "kind":r["kind"] or "hw",
              "startAt":r["start_at"] or "","endAt":r["end_at"] or ""}
        state, msg = _hw_window_state(r["start_at"], r["end_at"], r["mystatus"]=="submitted")
        item["windowState"] = state
        item["windowMsg"] = msg
        if r["show_result"] == 0:
            item["myScore"] = None
            item["scoreHidden"] = True
        else:
            item["myScore"] = r["myscore"]
        out.append(item)
    return {"code":0, "homeworks":out}

async def h_start_homework(msg, ctx):
    """学生开始作业：建/取作答记录，返回题目（不含答案）"""
    u = ctx["user"]; hid = int(msg["homeworkId"])
    c = opencur()
    hw = c.execute("SELECT * FROM homeworks WHERE id=? AND class_id=?", (hid, u["classId"])).fetchone()
    if not hw: return {"code":1,"msg":"作业不存在"}
    # 考试时间窗校验：只能在规定时间参加
    state, wmsg = _hw_window_state(hw["start_at"], hw["end_at"])
    if state == 1:
        return {"code":4,"msg":wmsg}
    if state == 2:
        return {"code":5,"msg":wmsg}
    rec = c.execute("SELECT * FROM records WHERE homework_id=? AND student_id=? ORDER BY id DESC LIMIT 1",
                    (hid,u["id"])).fetchone()
    if rec and rec["status"]=="submitted":
        return {"code":2,"msg":"已提交","recordId":rec["id"],"alreadySubmitted":True,"score":rec["score"]}
    if not rec:
        cur=c.execute("INSERT INTO records(homework_id,student_id,started_at,status) VALUES(?,?,?, 'in_progress')",
                      (hid,u["id"],now()))
        rid=cur.lastrowid
    else:
        rid=rec["id"]
    c.commit()
    hqs = c.execute("SELECT question_id,ref_type,score,sort_no FROM homework_questions WHERE homework_id=? ORDER BY sort_no",(hid,)).fetchall()
    out=[]
    for i,hq in enumerate(hqs):
        ref=hq["ref_type"]; qid=hq["question_id"]
        if ref=="operate":
            op=c.execute("SELECT timuTitle,descPlain,timuType,subType,material_files FROM operate_timu WHERE timuID=?",(qid,)).fetchone()
            stem=(op["timuTitle"] or "")+"\n"+(op["descPlain"] or "") if op else "(操作题)"
            tt = op["timuType"] if op else 0
            st = op["subType"] if op else 0
            mf = op["material_files"] if op else ""
            _hz = ("zip" in (mf or "").lower())
            out.append({"no":i+1,"qid":qid,"refType":"operate","score":hq["score"],"type":tt,
                        "subType":st,"hasZip":_hz,
                        "module":op_module_of(tt, st, stem, _hz),
                        "vapp":vapp_of(tt, stem, sub_type=st, has_zip=_hz),"stem":stem,"options":[]})
        else:
            q=c.execute("SELECT queName,optionA,optionB,optionC,optionD,option5,option6,queType,queScore,course FROM questions WHERE queNo=?",(qid,)).fetchone()
            out.append({"no":i+1,"qid":qid,"refType":"obj","score":hq["score"],"type":q["queType"] or 1,
                        "course":q["course"] or 0,
                        "stem":q["queName"],"options":[
                            {"k":"A","t":q["optionA"]},{"k":"B","t":q["optionB"]},
                            {"k":"C","t":q["optionC"]},{"k":"D","t":q["optionD"]},
                            {"k":"E","t":q["option5"]},{"k":"F","t":q["option6"]}]})
    c.close()
    _audit(ctx, "开始考试", "作业ID=%d 标题=%s" % (hid, hw["title"]))
    return {"code":0,"recordId":rid,"title":hw["title"],"qtype":hw["qtype"],"kind":hw["kind"],
            "durationSec":hw["duration_sec"],"questions":out}

async def h_submit(msg, ctx):
    u=ctx["user"]; rid=int(msg["recordId"]); ans=msg["answers"]  # [{qid,refType,answer}]
    c=opencur()
    rec=c.execute("SELECT * FROM records WHERE id=? AND student_id=?",(rid,u["id"])).fetchone()
    if not rec: return {"code":1,"msg":"记录不存在"}
    hw=c.execute("SELECT * FROM homeworks WHERE id=?",(rec["homework_id"],)).fetchone()
    total=0; got=0; detail=[]
    for a in ans:
        qid=a["qid"]; ref=a.get("refType","obj"); ua=a.get("answer","")
        if ref=="obj":
            q=c.execute("SELECT queName,queType,standardAnwser,resolutionTips,queScore,optionA,optionB,optionC,optionD FROM questions WHERE queNo=?",(qid,)).fetchone()
            if not q: continue
            right=q["standardAnwser"] or ""
            ok = norm_ans(ua)==norm_ans(right)
            sc = (q["queScore"] or 0) if ok else 0
            total += q["queScore"] or 0; got += sc
            c.execute("INSERT INTO answers(record_id,ref_type,question_id,student_answer,is_correct,score,graded_at) VALUES(?,?,?,?,?,?,?)",
                      (rid,"obj",qid,ua,1 if ok else 0,sc,now()))
            detail.append({"qid":qid,"refType":"obj","right":right,"your":ua,"correct":ok,
                           "score":sc,"fullScore":q["queScore"] or 0,
                           "tip":q["resolutionTips"],"stem":q["queName"],
                           "options":[{"k":"A","t":q["optionA"]},{"k":"B","t":q["optionB"]},{"k":"C","t":q["optionC"]},{"k":"D","t":q["optionD"]}]})
        else:
            # 操作题：查 uploadOperate 落的行（已判分 / 后台判分中 pending / 无）
            prev=c.execute("SELECT score,student_answer,grade_detail FROM answers WHERE record_id=? AND question_id=? AND ref_type='operate'",(rid,qid)).fetchone()
            opq=c.execute("SELECT timuTitle,descPlain FROM operate_timu WHERE timuID=?",(qid,)).fetchone()
            ostem=(opq["timuTitle"] or "")+"\n"+(opq["descPlain"] or "") if opq else "(操作题)"
            gd={}
            if prev and prev["grade_detail"]:
                try: gd=json.loads(prev["grade_detail"])
                except Exception: gd={}
            if prev and gd.get("pending"):
                # 后台判分中：暂不计分，完成后由 _recalc_record 补总分
                total += 20.0
                detail.append({"qid":qid,"refType":"operate","stem":ostem,
                    "your":"(见上传文件)","right":"自动判分","score":None,"fullScore":20.0,
                    "pending":True,"hits":[],"miss":[],"total":0,"details":[],
                    "note":"判分中，请稍后刷新","correct":False})
            elif prev and prev["score"] is not None:
                sc = float(prev["score"])
                got += sc
                total += 20.0  # 操作题默认满分20
                detail.append({"qid":qid,"refType":"operate","stem":ostem,
                    "your":"(见上传文件)","right":"自动判分","score":sc,"fullScore":20.0,
                    "hits":gd.get("hits",[]),"miss":gd.get("miss",[]),"total":gd.get("total",0),
                    "details":gd.get("details",[]),
                    "note":gd.get("note",prev["student_answer"] or ""),
                    "correct":sc>=12.0})
            else:
                # 没上传文件或没判分，按文本作答给0分
                c.execute("INSERT OR REPLACE INTO answers(record_id,ref_type,question_id,student_answer,is_correct,score,graded_at) VALUES(?,?,?,?,?,?,?)",
                          (rid,"operate",qid,ua,0,0,now()))
                total += 20.0
                detail.append({"qid":qid,"refType":"operate","stem":ostem,
                    "your":ua or "(未上传文件)","right":"自动判分","score":0,"fullScore":20.0,
                    "hits":[],"miss":[],"total":0,"note":"未检测到上传的作答文件",
                    "correct":False})
    # 总分 = 客观题得分 + 操作题已自动判分
    c.execute("UPDATE records SET submitted_at=?, score=?, status='submitted' WHERE id=?",(now(),round(got,1),rid))
    c.commit(); c.close()
    _audit(ctx, "交卷", "作业ID=%d 得分=%.1f" % (hw["id"], got))
    add_wrong(ctx["user"]["id"], detail)
    if hw["show_result"] == 0:
        return {"code":0,"score":None,"total":None,"detail":[],"scoreHidden":True,
                "msg":"交卷成功，成绩待教师公布"}
    return {"code":0,"score":got,"total":total,"detail":detail}

async def h_my_result(msg, ctx):
    u=ctx["user"]; hid=int(msg["homeworkId"])
    c=opencur()
    rec=c.execute("SELECT * FROM records WHERE homework_id=? AND student_id=? ORDER BY id DESC LIMIT 1",(hid,u["id"])).fetchone()
    if not rec: return {"code":1,"msg":"未作答"}
    hw=c.execute("SELECT show_result FROM homeworks WHERE id=?",(hid,)).fetchone()
    if hw and hw["show_result"] == 0:
        c.close()
        return {"code":0,"score":None,"status":rec["status"],"recordId":rec["id"],"detail":[],"scoreHidden":True,
                "submittedAt":rec["submitted_at"],"msg":"成绩待教师公布"}
    rows=c.execute("""SELECT a.question_id,a.ref_type,a.student_answer,a.is_correct,a.score,a.grade_detail,hq.sort_no,
                             (SELECT queScore FROM questions WHERE queNo=a.question_id) AS qscore
                      FROM answers a JOIN homework_questions hq ON a.record_id=? AND a.question_id=hq.question_id
                      WHERE a.record_id=? ORDER BY hq.sort_no""",(rec["id"],rec["id"])).fetchall()
    detail=[]
    for r in rows:
        if r["ref_type"]=="operate":
            op=c.execute("SELECT timuTitle,descPlain FROM operate_timu WHERE timuID=?",(r["question_id"],)).fetchone()
            ostem=(op["timuTitle"] or "")+"\n"+(op["descPlain"] or "") if op else "(操作题)"
            sc=float(r["score"]) if r["score"] is not None else 0
            gd={}
            if r["grade_detail"]:
                try: gd=json.loads(r["grade_detail"])
                except Exception: gd={}
            if gd.get("pending"):
                detail.append({"qid":r["question_id"],"refType":"operate","stem":ostem,"options":[],
                    "your":"(见上传文件)","right":"自动判分","score":None,"fullScore":20.0,
                    "pending":True,"hits":[],"miss":[],"total":0,"details":[],
                    "note":"判分中，请稍后刷新","correct":False})
            else:
                detail.append({"qid":r["question_id"],"refType":"operate","stem":ostem,"options":[],
                    "your":"(见上传文件)","right":"自动判分","score":sc,"fullScore":20.0,
                    "hits":gd.get("hits",[]),"miss":gd.get("miss",[]),"total":gd.get("total",0),
                    "details":gd.get("details",[]),"note":gd.get("note",r["student_answer"] or ""),
                    "correct":sc>=12.0})
        else:
            q=c.execute("SELECT queName,optionA,optionB,optionC,optionD,standardAnwser,resolutionTips FROM questions WHERE queNo=?",(r["question_id"],)).fetchone()
            if q:
                detail.append({"qid":r["question_id"],"refType":"obj","stem":q["queName"],
                    "options":[{"k":"A","t":q["optionA"]},{"k":"B","t":q["optionB"]},{"k":"C","t":q["optionC"]},{"k":"D","t":q["optionD"]}],
                    "your":r["student_answer"] or "","right":q["standardAnwser"] or "","tip":q["resolutionTips"],
                    "score":float(r["score"] or 0),"fullScore":float(r["qscore"] or 0),
                    "correct":(norm_ans(r["student_answer"] or "")==norm_ans(q["standardAnwser"] or ""))})
    c.close()
    return {"code":0,"score":rec["score"],"status":rec["status"],"recordId":rec["id"],"detail":detail,
            "submittedAt":rec["submitted_at"]}

# ---- 教师 ----
async def h_tch_homeworks(msg, ctx):
    u=ctx["user"]; c=opencur()
    rows=c.execute("""SELECT h.*,(SELECT COUNT(*) FROM records r WHERE r.homework_id=h.id AND r.status='submitted') AS done
                      FROM homeworks h WHERE h.creator_id=? ORDER BY h.id DESC""",(u["id"],)).fetchall()
    c.close()
    return {"code":0,"homeworks":[{"id":r["id"],"title":r["title"],"qtype":r["qtype"],"status":r["status"],
            "kind":(r["kind"] or "homework"),
            "createdAt":r["created_at"],"doneCount":r["done"]} for r in rows]}

async def h_create_homework(msg, ctx):
    u=ctx["user"]
    title=msg["title"]; cid=int(msg["classId"]); qtype=msg.get("qtype","obj")
    kind=msg.get("kind","homework")  # exam=正式考试 / homework=平时作业
    courses=msg.get("courses",[1,2,3,4,5,6,7,8,9]); count=int(msg.get("count",10))
    per=float(msg.get("scorePer",3)); dur=int(msg.get("durationSec",0))
    c=opencur()
    show_res = 1 if msg.get("showResult", 1) else 0
    cur=c.execute("""INSERT INTO homeworks(title,creator_id,class_id,qtype,config,kind,status,duration_sec,show_result,created_at)
                     VALUES(?,?,?,?,?,?,'draft',?,?,?)""",
                  (title,u["id"],cid,qtype,json.dumps({"courses":courses,"count":count,"scorePer":per}),kind,dur,show_res,now()))
    hid=cur.lastrowid
    n=0
    parts = msg.get("parts")
    if parts:
        # 混合组卷：parts=[{type:"obj",course:1,count:5}, {type:"operate",opType:"word",count:2,score:20}, ...]
        sort=0
        for part in parts:
            pt = part.get("type","obj")
            cnt = int(part.get("count",0))
            qids = part.get("qids", [])
            sc = float(part.get("score", per if pt=="obj" else 20.0))
            ref = "operate" if pt=="operate" else "obj"
            if qids:
                # 教师手动指定的题目
                for qid in qids:
                    sort+=1
                    c.execute("INSERT INTO homework_questions(homework_id,ref_type,question_id,score,sort_no) VALUES(?,?,?,?,?)",
                              (hid,ref,int(qid),sc,sort)); n+=1
                continue
            if cnt<=0: continue
            sort+=1
            if pt=="operate":
                ot = part.get("opType","all")
                cond,p = op_filter(ot)
                exc,ep = exclude_unsupported()
                p = p + ep + [cnt]
                qs=c.execute("SELECT timuID FROM operate_timu WHERE 1=1 "+cond+exc+" ORDER BY RANDOM() LIMIT ?",p).fetchall()
                for q in qs:
                    sort+=1; c.execute("INSERT INTO homework_questions(homework_id,ref_type,question_id,score,sort_no) VALUES(?,?,?,?,?)",
                              (hid,"operate",q["timuID"],sc,sort)); n+=1
            else:
                crs=int(part.get("course",1))
                qs=c.execute("SELECT queNo FROM questions WHERE course=? AND queType=1 ORDER BY RANDOM() LIMIT ?",(crs,cnt)).fetchall()
                for q in qs:
                    sort+=1; c.execute("INSERT INTO homework_questions(homework_id,ref_type,question_id,score,sort_no) VALUES(?,?,?,?,?)",
                              (hid,"obj",q["queNo"],sc,sort)); n+=1
    elif qtype=="operate":
        opType = msg.get("opType","all")
        cond,p = op_filter(opType)
        exc,ep = exclude_unsupported()
        p = p + ep + [count]
        qs=c.execute("SELECT timuID FROM operate_timu WHERE 1=1 "+cond+exc+" ORDER BY RANDOM() LIMIT ?",p).fetchall()
        for i,q in enumerate(qs):
            c.execute("INSERT INTO homework_questions(homework_id,ref_type,question_id,score,sort_no) VALUES(?,?,?,?,?)",
                      (hid,"operate",q["timuID"],per,i+1)); n+=1
    else:
        ph=",".join("?"*len(courses))
        qs=c.execute(f"""SELECT queNo FROM questions WHERE course IN ({ph}) AND queType=1
                         ORDER BY RANDOM() LIMIT ?""",(*courses,count)).fetchall()
        for i,q in enumerate(qs):
            c.execute("INSERT INTO homework_questions(homework_id,ref_type,question_id,score,sort_no) VALUES(?,?,?,?,?)",
                      (hid,"obj",q["queNo"],per,i+1)); n+=1
    c.commit(); c.close()
    kn = "正式考试" if kind=="exam" else "平时作业"
    _audit(ctx, "布置%s" % kn, "作业ID=%d 题数=%d" % (hid, n))
    return {"code":0,"homeworkId":hid,"picked":n,"msg":("已生成%s %d 题（草稿）"%(kn,n))}

async def h_publish(msg, ctx):
    hid=int(msg["homeworkId"]); c=opencur()
    kind_row=c.execute("SELECT kind FROM homeworks WHERE id=? AND creator_id=?",(hid,ctx["user"]["id"])).fetchone()
    if msg.get("startAt") or msg.get("endAt"):
        # 发布考试时规定参加时间窗
        c.execute("UPDATE homeworks SET status='published',start_at=?,end_at=? WHERE id=? AND creator_id=?",
                  (msg.get("startAt") or "", msg.get("endAt") or "", hid, ctx["user"]["id"]))
    else:
        c.execute("UPDATE homeworks SET status='published' WHERE id=? AND creator_id=?",(hid,ctx["user"]["id"]))
    c.commit(); c.close()
    _audit(ctx, "发布" + ("考试" if (kind_row and kind_row["kind"]=="exam") else "作业"), "作业ID=%d" % hid)
    return {"code":0,"msg":"已发布"}

async def h_close_hw(msg, ctx):
    hid=int(msg["homeworkId"]); c=opencur()
    c.execute("UPDATE homeworks SET status='closed' WHERE id=? AND creator_id=?",(hid,ctx["user"]["id"]))
    c.commit(); c.close()
    _audit(ctx, "结束考试/作业", "作业ID=%d" % hid)
    return {"code":0,"msg":"已结束"}

async def h_class_results(msg, ctx):
    hid=int(msg["homeworkId"]); c=opencur()
    rows=c.execute("""SELECT r.id AS rid,u.username,u.display_name,r.score,r.status,r.submitted_at
                      FROM records r JOIN users u ON r.student_id=u.id
                      WHERE r.homework_id=? ORDER BY (r.score IS NULL), r.score DESC""",(hid,)).fetchall()
    c.close()
    return {"code":0,"results":[{"recordId":r["rid"],"username":r["username"],"name":r["display_name"],"score":r["score"],
            "status":r["status"],"submittedAt":r["submitted_at"]} for r in rows]}

async def h_list_students(msg, ctx):
    c=opencur()
    rows=c.execute("""SELECT u.id,u.username,u.display_name,u.class_id,cl.name AS cn
                      FROM users u LEFT JOIN classes cl ON u.class_id=cl.id
                      WHERE u.role='student' ORDER BY u.id""").fetchall()
    c.close()
    return {"code":0,"students":[{"id":r["id"],"username":r["username"],"name":r["display_name"],
            "class":r["cn"] or ""} for r in rows]}

async def h_add_student(msg, ctx):
    un=msg["username"].strip(); pwd=msg["password"]; name=msg.get("displayName",un); cid=int(msg.get("classId",1))
    if not un or not pwd: return {"code":1,"msg":"账号密码不能为空"}
    salt=secrets.token_hex(8); h=hashlib.sha256((salt+pwd).encode()).hexdigest()
    c=opencur()
    try:
        c.execute("INSERT INTO users(username,salt,pwd_hash,display_name,role,class_id) VALUES(?,?,?,?, 'student',?)",
                  (un,salt,h,name,cid)); c.commit()
    except sqlite3.IntegrityError:
        c.close(); return {"code":2,"msg":"账号已存在"}
    c.close()
    _audit(ctx, "新增学生", "账号=%s 姓名=%s" % (un, name))
    return {"code":0,"msg":"已创建"}

async def h_update_student(msg, ctx):
    uid=int(msg["userId"]); c=opencur()
    u=c.execute("SELECT id,username FROM users WHERE id=? AND role='student'",(uid,)).fetchone()
    if not u: c.close(); return {"code":1,"msg":"学生不存在"}
    if msg.get("password"):
        salt=secrets.token_hex(8); h=hashlib.sha256((salt+msg["password"]).encode()).hexdigest()
        c.execute("UPDATE users SET salt=?,pwd_hash=? WHERE id=?",(salt,h,uid))
    if msg.get("displayName"):
        c.execute("UPDATE users SET display_name=? WHERE id=?",(msg["displayName"].strip(),uid))
    if "classId" in msg:
        c.execute("UPDATE users SET class_id=? WHERE id=?",(int(msg["classId"]),uid))
    c.commit(); c.close()
    _audit(ctx, "修改学生", "账号=%s" % u["username"])
    return {"code":0,"msg":"已保存"}

async def h_delete_student(msg, ctx):
    """删除学生账号（仅教师）：连同其答题记录、作答明细、错题本一并删除，防止残留孤儿数据。"""
    if ctx["user"]["role"] != "teacher":
        return {"code":403,"msg":"仅教师可删除学生账号"}
    uid=int(msg["userId"]); c=opencur()
    u=c.execute("SELECT id,username FROM users WHERE id=? AND role='student'",(uid,)).fetchone()
    if not u: c.close(); return {"code":1,"msg":"学生不存在"}
    c.execute("DELETE FROM answers WHERE record_id IN (SELECT id FROM records WHERE student_id=?)",(uid,))
    c.execute("DELETE FROM records WHERE student_id=?",(uid,))
    c.execute("DELETE FROM wrong_questions WHERE student_id=?",(uid,))
    c.execute("DELETE FROM users WHERE id=? AND role='student'",(uid,))
    c.commit(); c.close()
    _audit(ctx, "删除学生", "账号=%s" % u["username"])
    return {"code":0,"msg":"已删除学生账号 %s" % u["username"]}

async def h_add_class(msg, ctx):
    name=msg.get("name","").strip()
    if not name: return {"code":1,"msg":"班级名不能为空"}
    c=opencur()
    try:
        c.execute("INSERT INTO classes(name) VALUES(?)",(name,)); c.commit()
    except sqlite3.IntegrityError:
        c.close(); return {"code":2,"msg":"班级已存在"}
    c.close()
    _audit(ctx, "新增班级", "班级=%s" % name)
    return {"code":0,"msg":"已添加"}

async def h_delete_class(msg, ctx):
    cid=int(msg["classId"]); c=opencur()
    if c.execute("SELECT COUNT(*) n FROM classes").fetchone()["n"]<=1:
        c.close(); return {"code":1,"msg":"至少保留一个班级"}
    c.execute("UPDATE users SET class_id=1 WHERE class_id=?",(cid,))
    c.execute("DELETE FROM classes WHERE id=? AND id!=1",(cid,))
    c.commit(); c.close()
    _audit(ctx, "删除班级", "班级ID=%d" % cid)
    return {"code":0,"msg":"已删除"}

async def h_record_detail(msg, ctx):
    rid=int(msg["recordId"]); c=opencur()
    rec=c.execute("SELECT student_id FROM records WHERE id=?",(rid,)).fetchone()
    if not rec:
        c.close(); return {"code":1,"msg":"记录不存在"}
    if ctx["user"]["role"]!="teacher" and rec["student_id"]!=ctx["user"]["id"]:
        c.close(); return {"code":1,"msg":"无权限"}
    rows=c.execute("""SELECT a.question_id,a.ref_type,a.student_answer,a.score,a.grade_detail,
                             hq.ref_type AS rt
                      FROM answers a JOIN homework_questions hq ON a.record_id=? AND a.question_id=hq.question_id
                      WHERE a.record_id=? ORDER BY hq.sort_no""",(rid,rid)).fetchall()
    out=[]
    for r in rows:
        if r["ref_type"]=="operate":
            op=c.execute("SELECT timuTitle,descPlain FROM operate_timu WHERE timuID=?",(r["question_id"],)).fetchone()
            ostem=(op["timuTitle"] or "")+"\n"+(op["descPlain"] or "") if op else "(操作题)"
            sc=float(r["score"]) if r["score"] is not None else 0
            gd={}
            if r["grade_detail"]:
                try: gd=json.loads(r["grade_detail"])
                except Exception: gd={}
            if gd.get("pending"):
                out.append({"refType":"operate","qid":r["question_id"],"stem":ostem,
                            "options":[],"your":"(见上传文件)","right":"自动判分","score":None,"fullScore":20.0,
                            "pending":True,"hits":[],"miss":[],"total":0,"details":[],
                            "note":"判分中，请稍后刷新","correct":False})
            else:
                out.append({"refType":"operate","qid":r["question_id"],"stem":ostem,
                            "options":[],"your":"(见上传文件)","right":"自动判分","score":sc,"fullScore":20.0,
                            "hits":gd.get("hits",[]),"miss":gd.get("miss",[]),"total":gd.get("total",0),
                            "details":gd.get("details",[]),
                            "note":gd.get("note",r["student_answer"] or ""),
                            "correct":sc>=12.0})
        else:
            q=c.execute("SELECT queName,optionA,optionB,optionC,optionD,standardAnwser,queScore,resolutionTips FROM questions WHERE queNo=?",(r["question_id"],)).fetchone()
            if q:
                out.append({"refType":"obj","qid":r["question_id"],"stem":q["queName"],
                    "options":[{"k":"A","t":q["optionA"]},{"k":"B","t":q["optionB"]},{"k":"C","t":q["optionC"]},{"k":"D","t":q["optionD"]}],
                    "your":r["student_answer"] or "","right":q["standardAnwser"] or "",
                    "tip":q["resolutionTips"] or "",
                    "score":float(r["score"] or 0),"fullScore":float(q["queScore"] or 0),
                    "correct":(norm_ans(r["student_answer"] or "")==norm_ans(q["standardAnwser"] or ""))})
    c.close()
    return {"code":0,"detail":out}

async def h_operate_list(msg, ctx):
    """教师/学生查看操作题列表（题干+素材提示）"""
    c=opencur()
    rows=c.execute("SELECT timuID,timuTitle,substr(descPlain,1,80) AS head,timuType FROM operate_timu ORDER BY timuID LIMIT 200").fetchall()
    c.close()
    return {"code":0,"items":[{"id":r["timuID"],"title":r["timuTitle"],"type":r["timuType"],"head":r["head"]} for r in rows]}

def grade_answers(answers):
    """对客观题答案即时判分，返回 (got, total, detail)。纯计算不写库。"""
    c = opencur()
    total = got = 0
    detail = []
    for a in answers:
        qid = a["qid"]; ref = a.get("refType", "obj"); ua = a.get("answer", "")
        if ref != "obj":
            continue
        q = c.execute("SELECT queName,standardAnwser,resolutionTips,queScore FROM questions WHERE queNo=?", (qid,)).fetchone()
        if not q:
            continue
        right = q["standardAnwser"] or ""
        ok = norm_ans(ua) == norm_ans(right)
        sc = (q["queScore"] or 0) if ok else 0
        total += q["queScore"] or 0; got += sc
        detail.append({"qid": qid, "right": right, "your": ua,
                       "correct": ok, "tip": q["resolutionTips"], "stem": q["queName"]})
    c.close()
    return got, total, detail

TYPE_GROUPS = {
    "word": [13, 43], "excel": [14, 44], "ppt": [15, 45],
    "mysql": [24], "photoshop": [18], "win10": [11, 12, 31],
    "python": [25],
}
TYPE_NAMES = {"word":"Word文字处理","excel":"Excel电子表格","ppt":"PPT演示文稿",
              "mysql":"MySQL数据库","photoshop":"Photoshop","browser":"浏览器操作","win10":"Windows系统设置",
              "python":"Python编程"}

# 网络模块关键词：VPN/拨号/宽带等归「网络」；注意「远程桌面」按用户口径归「系统设置」，不在此列
NET_RE = re.compile(r"连接到.{0,4}Internet|宽带|PPPoE|拨号|VPN|虚拟专用网络|防火墙|WLAN|WiFi|Wi-Fi|家庭组|工作组|网络位置|网络发现|移动热点|代理|IP地址|TCP/IP|网上邻居|共享文件夹|文件共享|建立网络|设置网络")
# 用户口径（2026-10）：只有「设置 IP」类操作才算 Windows 网络设置题，仅题干出现「网络」不算
IP_SET_RE = re.compile(r"设置IP|配置IP|指定IP|静态IP|手动.{0,6}IP|IP地址|子网掩码|默认网关|首选DNS|备用DNS|自动获得.{0,10}IP|设置.{0,8}(IP|ip)")
IE_RE  = re.compile(r"主页|历史记录|Internet Explorer|cookie|Cookie|安全级别|临时文件|磁盘使用空间")
_WIN10_IDS = None
def win10_ids_split():
    """把 type 11/12/31/32 拆成 (邮箱, 浏览器, 网络, 文件操作, 系统设置) 五个 id 集，惰性缓存。
    以 subType 为主、题干正则为辅：
      文件操作 sub2 独立；邮箱 sub11/题干；浏览器=Edge收藏/Internet选项 sub6/7；
      网络=本地连接/宽带/连接网络(含VPN) sub8/9/10；远程桌面强制归系统设置；其余=系统设置。"""
    global _WIN10_IDS
    if _WIN10_IDS is not None: return _WIN10_IDS
    c=opencur()
    rows=c.execute("SELECT timuID,descPlain,subType FROM operate_timu WHERE timuType IN (11,12,31,32)").fetchall()
    email=set(); browser=set(); net=set(); fileids=set(); allids=set()
    for r in rows:
        tid=str(r["timuID"]); allids.add(tid)
        s=r["descPlain"] or ""; st=int(r["subType"] or 0)
        if st==2:   # 文件和文件夹操作
            fileids.add(tid); continue
        if st==11 or "邮箱" in s or "qq.com" in s or ("邮件" in s and "收件" in s):
            email.add(tid); continue
        if "远程桌面" in s:    # 远程桌面按用户口径归系统设置（即使 subType=10）
            continue
        if st in (6,7):        # IE/Edge 收藏夹、Internet 选项
            browser.add(tid); continue
        if st in (8,9,10) and IP_SET_RE.search(s):     # 本地连接、宽带连接、连接和网络(含 VPN) 且题干含设置IP
            net.add(tid); continue
        lines=[l for l in s.split("\n") if re.match(r"\s*\d+[、\.]",l)]
        if any(IE_RE.search(l) for l in lines):     # 主页/历史记录/临时文件
            browser.add(tid); continue
        if any(NET_RE.search(l) and "打印机" not in l for l in lines) and IP_SET_RE.search(s):  # VPN/拨号漏网兜底，须含设置IP
            net.add(tid); continue
    setids=allids-email-browser-net-fileids
    c.close()
    _WIN10_IDS=(email,browser,net,fileids,setids)
    return _WIN10_IDS

OP_WHERE = ("(material_files LIKE '%.docx%' OR material_files LIKE '%.xlsx%' OR material_files LIKE '%.pptx%' "
 "OR material_files LIKE '%.doc%' OR material_files LIKE '%.xls%' OR material_files LIKE '%.ppt%' "
 "OR material_files LIKE '%.html%' OR material_files LIKE '%.htm%' "
 "OR material_files LIKE '%.accdb%' OR material_files LIKE '%.psd%' "
 "OR timuType IN (11,12,31,32,25,24))")
OP_COUNT_SQL = "SELECT COUNT(*) FROM operate_timu WHERE "+OP_WHERE

def op_filter(ot):
    """按操作题类型返回 (SQL附加条件, 参数列表)；win10=系统设置, win10net=网络, email=邮箱。
    all/空 时返回所有“可自动判分”操作题白名单（排除 MySQL 数据目录快照 .zip）。"""
    if ot in (None, "", "all"):
        return "AND "+OP_WHERE, []
    if ot=="email":
        email_ids,_,_,_,_=win10_ids_split()
        if not email_ids: return "",[]
        ids=list(email_ids); ph=",".join("?"*len(ids))
        return "AND timuID IN ("+ph+")", ids
    if ot=="browser":
        _,browser_ids,_,_,_=win10_ids_split()
        if not browser_ids: return "",[]
        ids=list(browser_ids); ph=",".join("?"*len(ids))
        return "AND timuID IN ("+ph+")", ids
    if ot=="win10net":
        _,_,net_ids,_,_=win10_ids_split()
        if not net_ids: return "",[]
        ids=list(net_ids); ph=",".join("?"*len(ids))
        return "AND timuID IN ("+ph+")", ids
    if ot=="file":
        _,_,_,file_ids,_=win10_ids_split()
        if not file_ids: return "",[]
        ids=list(file_ids); ph=",".join("?"*len(ids))
        return "AND timuID IN ("+ph+")", ids
    if ot=="win10":
        _,_,_,_,set_ids=win10_ids_split()
        if not set_ids: return "AND timuType IN (11,12,31,32)",[]
        ids=list(set_ids); ph=",".join("?"*len(ids))
        return "AND timuID IN ("+ph+")", ids
    if ot=="mysql":
        # MySQL 数据库题（timuType=24，素材为 MySQL 5.7 数据目录快照 material.zip）
        return "AND timuType=24", []
    if ot in TYPE_GROUPS:
        ts=TYPE_GROUPS[ot]; ph=",".join("?"*len(ts))
        return "AND timuType IN ("+ph+")", list(ts)
    return "",[]

_UNSUPPORTED_IDS = None
# 旧版 type12 宽带(PPPoE)“连接属性>选项”里的 PPP 重拨判分点：随包所有版本 Win10.exe
# （学生端 5810KB / ZYDK 3198KB）均未实现该界面、不写作答记录，判分恒 0（官方实际已停用）。
# 随机抽题/组卷/套卷一律排除这些题；教师手动选题仍保留（教师知情）。
_REDIAL_HF = {1206, 1112, 1128, 1213, 1209}

def unsupported_operate_ids():
    global _UNSUPPORTED_IDS
    if _UNSUPPORTED_IDS is not None: return _UNSUPPORTED_IDS
    c = opencur(); ids = set()
    for r in c.execute("SELECT timuID,dfdList FROM operate_timu WHERE timuType=12"):
        try:
            if any(d.get("haveFuction") in _REDIAL_HF for d in json.loads(r["dfdList"])):
                ids.add(str(r["timuID"]))
        except Exception:
            pass
    c.close(); _UNSUPPORTED_IDS = ids
    return ids

def exclude_unsupported():
    """返回 (SQL片段, 参数)：随机抽操作题时排除虚拟程序不支持的旧题。"""
    ids = unsupported_operate_ids()
    if not ids: return "", []
    return " AND timuID NOT IN (" + ",".join("?" * len(ids)) + ")", list(ids)

def vapp_of(timu_type, desc="", sub_type=None, has_zip=False):
    """返回该操作题要启动的虚拟程序: edge/win10/email, 否则 None(Office/文件管理题)。
    文件管理题（subType=2 或 material.zip）只解压考生文件夹，绝不启虚拟程序。
    type32(Wangluo10)按 subType 区分：浏览器收藏/Internet选项(sub6/7)→edge；
    本地连接/宽带/连接网络含VPN(sub8/9/10)→win10；远程桌面→win10。"""
    if has_zip or int(sub_type or 0) == 2:
        return None
    d = (desc or "")
    if int(sub_type or 0) == 11 or "邮箱" in d or "qq.com" in d or ("邮件" in d and "收件" in d):
        return "email"
    if timu_type in (11, 12, 31, 32):
        st = int(sub_type or 0)
        if st in (6, 7):       # IE/Edge 收藏夹、Internet 选项
            return "edge"
        if st in (8, 9, 10):   # 本地连接、宽带连接、连接和网络(含 VPN)
            return "win10"
        # subType 缺失/1：按题干关键词兜底
        if re.search(r"收藏|主页|历史记录|临时文件|Internet\s*选项|cookie|Cookie", d):
            return "edge"
        return "win10"   # VPN/拨号/远程桌面/其余系统设置均在 Win10.exe 内操作
    return None

# 题目模块名（客户端考试界面左侧导航分组用，口径与 h_list_operate_types 一致）
OP_MODULE_NAMES = {"word":"Word文字处理","excel":"Excel电子表格","ppt":"PPT演示文稿",
                   "mysql":"MySQL数据库","photoshop":"Photoshop","browser":"浏览器操作",
                   "win10":"Windows系统设置","win10net":"Windows网络设置","email":"QQ邮箱设置",
                   "file":"文件操作","python":"Python编程","sql":"SQL数据库"}

def op_module_of(timu_type, sub_type=0, desc="", has_zip=False):
    """返回操作题所属模块 key（按 h_list_operate_types 的 win10_ids_split 口径逐题归类）。"""
    if timu_type == 25: return "python"
    if timu_type == 24: return "sql"
    if timu_type == 18: return "photoshop"
    if timu_type == 17: return "mysql"
    if timu_type in (13, 43): return "word"
    if timu_type in (14, 44): return "excel"
    if timu_type in (15, 45): return "ppt"
    if timu_type in (11, 12, 31, 32):
        d = desc or ""; st = int(sub_type or 0)
        if has_zip or st == 2:      # 文件操作
            return "file"
        if st == 11 or "邮箱" in d or "qq.com" in d or ("邮件" in d and "收件" in d):
            return "email"
        if "远程桌面" in d:          # 远程桌面按用户口径归系统设置
            return "win10"
        if st in (6, 7):            # IE/Edge 收藏夹、Internet 选项
            return "browser"
        if st in (8, 9, 10) and IP_SET_RE.search(d):        # 本地连接、宽带连接、连接和网络(含 VPN)，须题干含设置IP
            return "win10net"
        lines = [l for l in d.split("\n") if re.match(r"\s*\d+[、\.]", l)]
        if any(IE_RE.search(l) for l in lines):
            return "browser"
        if any(NET_RE.search(l) and "打印机" not in l for l in lines) and IP_SET_RE.search(d):
            return "win10net"
        return "win10"
    return "operate"

async def h_list_operate_types(msg, ctx):
    c = opencur(); out = []
    out.append({"key":"all","name":"全部操作题","count":c.execute(OP_COUNT_SQL).fetchone()[0]})
    for key, types in TYPE_GROUPS.items():
        if key=="win10":
            email_ids,browser_ids,net_ids,file_ids,set_ids=win10_ids_split()
            out.append({"key":"win10","name":"Windows系统设置","count":len(set_ids)})
            out.append({"key":"file","name":"文件操作","count":len(file_ids)})
            out.append({"key":"win10net","name":"Windows网络设置","count":len(net_ids)})
            out.append({"key":"browser","name":"浏览器操作","count":len(browser_ids)})
            out.append({"key":"email","name":"QQ邮箱设置","count":len(email_ids)})
            continue
        if key=="mysql":
            ph = ",".join("?"*len(TYPE_GROUPS["mysql"]))
            n = c.execute(f"SELECT COUNT(*) FROM operate_timu WHERE timuType IN ({ph})",
                          TYPE_GROUPS["mysql"]).fetchone()[0]
        else:
            ph = ",".join("?"*len(types))
            n = c.execute(f"SELECT COUNT(*) FROM operate_timu WHERE timuType IN ({ph})", types).fetchone()[0]
        out.append({"key":key,"name":TYPE_NAMES[key],"count":n})
    c.close()
    return {"code":0,"types":out}

async def h_start_practice(msg, ctx):
    """自主练习：学生自选课程模块+题量，服务端随机抽题（不绑定作业）"""
    courses = msg.get("courses", [1,2,3,4,5,6,7,8,9])
    count = int(msg.get("count", 10))
    qtype = msg.get("qtype", "obj")   # obj=客观题 / operate=操作题
    c = opencur()
    out = []
    parts = msg.get("parts")
    if parts:
        # 混合练习：parts=[{type:"obj",course:1,count:5},{type:"operate",opType:"word",count:2},...]
        no=0
        for part in parts:
            pt=part.get("type","obj"); cnt=int(part.get("count",0))
            if cnt<=0: continue
            if pt=="operate":
                ot=part.get("opType","all"); p=[]; cond=""
                if ot and ot!="all":
                    cond,p = op_filter(ot)
                exc,ep = exclude_unsupported()
                p = p + ep + [cnt]
                qs=c.execute("SELECT timuID,timuTitle,descPlain,timuType,subType,material_files FROM operate_timu WHERE (material_files LIKE '%.docx%' OR material_files LIKE '%.xlsx%' OR material_files LIKE '%.pptx%' OR material_files LIKE '%.doc%' OR material_files LIKE '%.xls%' OR material_files LIKE '%.ppt%' OR material_files LIKE '%.accdb%' OR material_files LIKE '%.psd%' OR timuType IN (11,12,31,32,25,24)) "+cond+exc+" ORDER BY RANDOM() LIMIT ?",p).fetchall()
                for q in qs:
                    no+=1
                    stem=(q["timuTitle"] or "")+"\n"+(q["descPlain"] or "")
                    _hz=("zip" in (q["material_files"] or "").lower())
                    out.append({"no":no,"qid":q["timuID"],"refType":"operate","score":20,"type":q["timuType"],
                                "subType":q["subType"] or 0,"hasZip":_hz,
                                "module":op_module_of(q["timuType"], q["subType"] or 0, stem, _hz),
                                "vapp":vapp_of(q["timuType"], stem, sub_type=q["subType"] or 0, has_zip=_hz),"stem":stem,"options":[]})
            else:
                crs=int(part.get("course",1))
                qs=c.execute("SELECT queNo,queName,optionA,optionB,optionC,optionD,option5,option6,queType,queScore,course FROM questions WHERE course=? AND queType=1 ORDER BY RANDOM() LIMIT ?",(crs,cnt)).fetchall()
                for q in qs:
                    no+=1
                    out.append({"no":no,"qid":q["queNo"],"refType":"obj","score":q["queScore"],"type":q["queType"],
                                "course":q["course"] or crs,
                                "stem":q["queName"],
                                "options":[{"k":"A","t":q["optionA"]},{"k":"B","t":q["optionB"]},
                                           {"k":"C","t":q["optionC"]},{"k":"D","t":q["optionD"]},
                                           {"k":"E","t":q["option5"]},{"k":"F","t":q["option6"]}]})
        c.close()
        return {"code":0,"questions":out}
    if qtype == "operate":
        tg = msg.get("opType", "all")
        params = []
        cond = ""
        if tg and tg != "all":
            cond, params = op_filter(tg)
        exc, ep = exclude_unsupported()
        params = params + ep + [count]
        qs = c.execute(
            "SELECT timuID,timuTitle,descPlain,timuType,subType,material_files FROM operate_timu "
            "WHERE (material_files LIKE '%.docx%' OR material_files LIKE '%.xlsx%' OR material_files LIKE '%.pptx%' "
            "OR material_files LIKE '%.doc%' OR material_files LIKE '%.xls%' OR material_files LIKE '%.ppt%' "
            "OR material_files LIKE '%.accdb%' OR material_files LIKE '%.psd%' "
            "OR timuType IN (11,12,31,32,25,24)) "
            + cond + exc +
            " ORDER BY RANDOM() LIMIT ?", params).fetchall()
        for i,q in enumerate(qs):
            stem = (q["timuTitle"] or "")+"\n"+(q["descPlain"] or "")
            _hz=("zip" in (q["material_files"] or "").lower())
            out.append({"no":i+1,"qid":q["timuID"],"refType":"operate","score":20,"type":q["timuType"],
                        "subType":q["subType"] or 0,"hasZip":_hz,
                        "module":op_module_of(q["timuType"], q["subType"] or 0, stem, _hz),
                        "vapp":vapp_of(q["timuType"], stem, sub_type=q["subType"] or 0, has_zip=_hz),"stem":stem,"options":[]})
    else:
        ph = ",".join("?"*len(courses))
        qs = c.execute(
            f"SELECT queNo,queName,optionA,optionB,optionC,optionD,option5,option6,queType,queScore,course "
            f"FROM questions WHERE course IN ({ph}) AND queType=1 ORDER BY RANDOM() LIMIT ?",
            (*courses, count)).fetchall()
        for i, q in enumerate(qs):
            out.append({"no": i+1, "qid": q["queNo"], "refType": "obj", "score": q["queScore"], "type": q["queType"],
                        "course": q["course"] or 0,
                        "stem": q["queName"],
                        "options": [{"k":"A","t":q["optionA"]},{"k":"B","t":q["optionB"]},
                                    {"k":"C","t":q["optionC"]},{"k":"D","t":q["optionD"]},
                                    {"k":"E","t":q["option5"]},{"k":"F","t":q["option6"]}]})
    c.close()
    _audit(ctx, "开始练习", "qtype=%s 题数=%d" % (qtype, len(out)))
    return {"code":0, "questions": out}

async def h_submit_practice(msg, ctx):
    """自主练习交卷：即时判分，返回对错+解析，错题自动入错题本，成绩记入练习记录"""
    got, total, detail = grade_answers(msg.get("answers", []))
    _audit(ctx, "提交练习", "得分=%.1f/%d 题数=%d" % (got, total, len(msg.get("answers", []))))
    add_wrong(ctx["user"]["id"], detail)
    try:
        c = opencur()
        c.execute("INSERT INTO practice_records(student_id,score,total,qcount,detail,created_at) VALUES(?,?,?,?,?,?)",
                  (ctx["user"]["id"], got, total, len(msg.get("answers", [])),
                   json.dumps(detail, ensure_ascii=False), now()))
        c.commit(); c.close()
    except Exception:
        pass
    return {"code":0, "score": got, "total": total, "detail": detail}

async def h_practice_history(msg, ctx):
    """教师端查看学生练习记录（练习随时可参加，这里按学生聚合次数/分数）"""
    if ctx["user"]["role"] != "teacher":
        return {"code":403,"msg":"仅教师可查看"}
    c = opencur()
    rows = c.execute(
        """SELECT pr.student_id, u.username, u.display_name,
                  COUNT(pr.id) AS times, MAX(pr.score) AS best,
                  (SELECT score FROM practice_records WHERE student_id=pr.student_id ORDER BY id DESC LIMIT 1) AS latest,
                  (SELECT total FROM practice_records WHERE student_id=pr.student_id ORDER BY id DESC LIMIT 1) AS latest_total,
                  (SELECT created_at FROM practice_records WHERE student_id=pr.student_id ORDER BY id DESC LIMIT 1) AS last_time
           FROM practice_records pr JOIN users u ON pr.student_id=u.id
           GROUP BY pr.student_id ORDER BY pr.student_id""").fetchall()
    c.close()
    return {"code":0, "records":[{"studentId":r["student_id"],"username":r["username"],"name":r["display_name"],
            "times":r["times"],"best":r["best"],"latest":r["latest"],"latestTotal":r["latest_total"],
            "lastTime":r["last_time"]} for r in rows]}

def add_wrong(student_id, detail):
    """把答错的客观题写入错题本（已存在未解决的不重复插入）"""
    c = opencur()
    for d in detail:
        if d.get("correct") is False:
            qid = d["qid"]
            exist = c.execute("SELECT id FROM wrong_questions WHERE student_id=? AND qid=? AND resolved=0",
                              (student_id, qid)).fetchone()
            if exist:
                continue
            c.execute("INSERT INTO wrong_questions(student_id,qid,ref_type,my_answer,right_answer,stem) VALUES(?,?,?,?,?,?)",
                      (student_id, qid, "obj", d["your"], d["right"], d["stem"]))
    c.commit(); c.close()

FONT_WORDS = ["楷体","黑体","宋体","微软雅黑","新宋体","仿宋","幼圆","隶书","Arial","Times New Roman","华文"]
COLOR_WORDS = ["红色","蓝色","绿色","黄色","紫色","橙色","灰色","黑色","白色","粉红","深红","深灰"]

def extract_checkpoints(stem):
    """从操作题题干提取可在文件里检查的关键项：引号文字、URL、字体名"""
    cps = []
    for q in re.findall(r'[“"『]([^"”』]{2,40})[”"』]', stem):
        cps.append(q.strip())
    cps += re.findall(r'https?://[^\s，。）)"]+', stem)
    for f in FONT_WORDS:
        if f in stem and f not in cps:
            cps.append(f)
    return cps

def auto_grade_operate(filename, data, stem, full_score=20.0):
    """解析学生交的结果文件，按题干关键项命中率给分。
    返回 (score_or_None, total_checkpoints, hit_list, note)"""
    fn = filename.lower()
    if not fn.endswith(('.docx', '.xlsx', '.pptx', '.html', '.htm')):
        return None, 0, [], "暂不支持自动判分该类型文件(.docx/.xlsx/.pptx/.html)"
    text = ""
    try:
        if fn.endswith(('.docx','.xlsx','.pptx')):
            z = zipfile.ZipFile(io.BytesIO(data))
            for n in z.namelist():
                if n.endswith('.xml'):
                    text += z.read(n).decode('utf-8','ignore')
        else:
            text = data.decode('utf-8','ignore')
    except Exception as e:
        return None, 0, [], "文件解析失败: "+str(e)
    cps = extract_checkpoints(stem or "")
    if not cps:
        return None, 0, [], "题干无可检查的关键项(无引号文字/URL/字体)"
    hits = [cp for cp in cps if cp and cp in text]
    rate = len(hits)/len(cps)
    score = round(float(full_score)*rate, 1)
    return score, len(cps), hits, "命中 %d/%d 项"%(len(hits),len(cps))

def _material_dirs(folder):
    """素材目录解析：包内 operate_files/<题号> 优先，其次 123/operate_files，其次库内绝对路径。"""
    dirs=[]
    base=os.path.join(BASE_DIR,"operate_files")
    sub=os.path.basename(folder.rstrip("\\/")) if folder else ""
    if sub:
        for root in (base, os.path.normpath(os.path.join(BASE_DIR,"..","..","123","operate_files"))):
            ld=os.path.join(root,sub)
            if os.path.isdir(ld) and ld not in dirs: dirs.append(ld)
    if folder and os.path.isdir(folder) and folder not in dirs: dirs.append(folder)
    return dirs

def load_base_material(qid, exts):
    """读取题目原始素材中首个匹配 exts 的文件字节（Access/PSD 基线对比），失败返回 None。"""
    try:
        c=opencur()
        q=c.execute("SELECT folder FROM operate_timu WHERE timuID=?",(str(qid),)).fetchone()
        c.close()
        if not q: return None
        for d in _material_dirs(q["folder"] or ""):
            for fn in os.listdir(d):
                if fn.startswith("answer_"): continue
                if fn.lower().endswith(exts):
                    with open(os.path.join(d,fn),"rb") as f: return f.read()
    except Exception:
        return None
    return None

def grade_operate_file(filename, data, stem, full=20.0, base_data=None):
    """按扩展名分发操作题自动判分，返回 (score,total,hits,miss,note)。score=None 表示无法判。"""
    fn_low = filename.lower()
    miss = []
    if fn_low.endswith('.accdb'):
        # Access 判分需要 dfdList + DAO probe，统一在 judge_bridge.grade_by_engine 内完成；
        # 此兜底层拿不到 dfdList，返回 None 由主路径处理。
        return None, 0, [], [], "Access 判分由引擎桥接层(judge_bridge)处理"
    elif fn_low.endswith('.psd'):
        score,total,hits,miss,note = grade_psd(data, stem, full, base_data=base_data)
    elif fn_low.endswith('.docx'):
        feats = docx_features(data)
        score,total,hits,miss = grade_against_requirements(feats, stem, full)
        note = "格式判分 命中%d/%d 项; 已掌握:%s; 未达标:%s"%(len(hits),total,"、".join(hits),"、".join(miss))
    elif fn_low.endswith('.xlsx'):
        feats = xlsx_features(data)
        score,total,hits,miss = grade_xlsx_pptx(feats, stem, full)
        note = "表格判分 命中%d/%d 项; 已掌握:%s; 未达标:%s"%(len(hits),total,"、".join(hits),"、".join(miss))
    elif fn_low.endswith('.pptx'):
        feats = pptx_features(data)
        score,total,hits,miss = grade_xlsx_pptx(feats, stem, full)
        note = "PPT判分 命中%d/%d 项; 已掌握:%s; 未达标:%s"%(len(hits),total,"、".join(hits),"、".join(miss))
    else:
        score,total,hits,note = auto_grade_operate(filename, data, stem, full)
    return score,total,hits,miss,note

def _load_operate_q(c, qid):
    """取操作题完整信息并解析 dfdList（原版引擎判分点，camelCase JSON）。"""
    r=c.execute("SELECT timuID,timuTitle,descPlain,timuType,subType,standAnswer,folder,score,dfdList FROM operate_timu WHERE timuID=?",(str(qid),)).fetchone()
    if not r: return None
    dfd=[]
    try:
        if r["dfdList"]:
            v=json.loads(r["dfdList"])
            if isinstance(v,list): dfd=v
    except Exception:
        dfd=[]
    return {"qid":str(r["timuID"]),"title":r["timuTitle"] or "","desc":r["descPlain"] or "",
            "tt":int(r["timuType"] or 0),"sub":int(r["subType"] or 0),
            "standAnswer":r["standAnswer"],"dfd":dfd,
            "folder":r["folder"],"score":float(r["score"]) if r["score"] is not None else 40.0}

def _engine_gd(eng):
    """把引擎结果序列化为 answers.grade_detail（含每判分点明细 details）。"""
    return json.dumps({"score":eng["score"],"fullScore":20.0,"total":eng["total"],
                       "hits":eng["hits"],"miss":eng["miss"],"note":eng["note"],
                       "engine":True,"details":eng["details"]},ensure_ascii=False)

async def _engine_vapp(q, raw, paper_zip=None):
    """虚拟程序判分文件走原版引擎；不适用/无判分点返回 None（调用方回退旧规则）。
    paper_zip: 文件管理题考生文件夹（可选），与 raw 日志一并交给引擎。"""
    if not q or not q["dfd"] or q["tt"] not in JB_VAPP or not engine_available(): return None
    if not raw and not paper_zip: return None
    loop=asyncio.get_event_loop()
    return await loop.run_in_executor(GENERAL_POOL, lambda: grade_by_engine(
        timu_type=q["tt"],sub_type=q["sub"],dfdlist=q["dfd"],vapp_log=raw,
        paper_zip=paper_zip,
        full_score=20.0,timu_id=q["qid"],timu_title=q["title"],timu_desc=q["desc"]))

async def _engine_file(q, data, ext):
    """Office/PSD/Access 作答文件走原版引擎；不适用/无判分点返回 None（回退旧判分器）。"""
    if not q or not q["dfd"] or q["tt"] not in JB_FILE or not engine_available(): return None
    loop=asyncio.get_event_loop()
    return await loop.run_in_executor(GENERAL_POOL, lambda: grade_by_engine(
        timu_type=q["tt"],sub_type=q["sub"],dfdlist=q["dfd"],student_file=data,file_ext=ext,
        full_score=20.0,timu_id=q["qid"],timu_title=q["title"],timu_desc=q["desc"]))

def save_operate_answer(c, rid, qid, score, student_answer, grade_detail, is_correct=1):
    """操作题判分结果落库（UPSERT）。

    ⚠️ 必须用本函数，不要直接写 UPDATE：学生上传判分发生在交卷之前，
    此时 answers 里还没有该行，纯 UPDATE 会命中 0 行、判分结果静默丢失，
    交卷时读不到分数只能记 0（历史 BUG）。这里 UPDATE 未命中则 INSERT。
    """
    if score is None: score = 0
    cur=c.execute("""UPDATE answers SET score=?,is_correct=?,student_answer=?,graded_at=?,grade_detail=?
                     WHERE record_id=? AND question_id=? AND ref_type='operate'""",
                  (score,is_correct,student_answer,now(),grade_detail,rid,str(qid)))
    if cur.rowcount==0:
        c.execute("""INSERT INTO answers(record_id,ref_type,question_id,student_answer,is_correct,score,graded_at,grade_detail)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (rid,"operate",str(qid),student_answer,is_correct,score,now(),grade_detail))

def _recalc_record(c, rid):
    """后台判分完成后重算某记录总分（SUM(answers.score)；pending 行 score 暂为 0）。
    幂等：每判完一题都重算，全部完成后总分自然准确。"""
    total = c.execute("SELECT COALESCE(SUM(score),0) FROM answers WHERE record_id=?",
                      (rid,)).fetchone()[0]
    c.execute("UPDATE records SET score=? WHERE id=?", (round(float(total), 1), rid))

def _mysql_folder_judge_job(rid, qid, zpath, q):
    """后台 worker：学生上传整个考试文件夹 zip → 起学生 datadir 实例判分 → 落库 → 重算总分。"""
    c = None
    try:
        folder = q["folder"]
        # 素材多路兜底：包内 operate_files / 123/operate_files / 库内绝对路径
        mat = None; mat1 = None
        for d in _material_dirs(folder):
            m = os.path.join(d, "material.zip")
            if mat is None and os.path.isfile(m):
                mat = m
            m1 = os.path.join(d, "material_1.zip")
            if mat1 is None and os.path.isfile(m1):
                mat1 = m1
            if mat and mat1:
                break
        if mat is None:
            for d in _material_dirs(folder):
                zips = [f for f in os.listdir(d) if f.lower().endswith(".zip")] if os.path.isdir(d) else []
                if zips:
                    mat = os.path.join(d, zips[0])
                    break
        if mat is None:
            raise RuntimeError("MySQL 题素材缺失: %s" % qid)
        r = grade_mysql_folder(q["dfd"], zpath, mat, q["score"], answer_zip=mat1)
        # 判分诊断日志：保留学生交卷包 + 判分详情
        try:
            _lg = os.path.join(os.path.dirname(DB), "judge_log")
            os.makedirs(_lg, exist_ok=True)
            _ts = time.strftime("%Y%m%d_%H%M%S")
            _keep = os.path.join(_lg, "mysql_exam_%s_%s" % (qid, _ts))
            import shutil as _sh
            _sh.copy2(zpath, _keep + ".zip")
            with open(_keep + ".json", "w", encoding="utf-8") as _f:
                json.dump({"qid": qid, "rid": rid, "score": r.get("score"),
                           "note": r.get("note"), "hits": r.get("hits"),
                           "miss": r.get("miss"), "details": r.get("details")},
                          _f, ensure_ascii=False, indent=1)
        except Exception as _e:
            print("judge_log 写入失败:", _e)
        c = opencur()
        gd = json.dumps({"score": r["score"], "fullScore": q["score"], "total": r["total"],
                         "hits": r["hits"], "miss": r["miss"], "note": r["note"],
                         "engine": True, "details": r["details"]}, ensure_ascii=False)
        save_operate_answer(c, rid, qid, r["score"], "MySQL考试文件夹 | " + r["note"], gd)
        _recalc_record(c, rid)
        c.commit()
    except Exception as e:
        print("mysql folder judge job error:", e)
        try:
            if c is None:
                c = opencur()
            save_operate_answer(c, rid, qid, 0, "MySQL判分异常", None, is_correct=0)
            _recalc_record(c, rid)
            c.commit()
        except Exception as e2:
            print("mysql folder judge job fallback error:", e2)
    finally:
        try:
            if c is not None:
                c.close()
        except Exception:
            pass


def _office_judge_job(rid, qid, data, ext, q, safe):
    """在单 worker 中串行：原版引擎判 Office/PSD/Access 文件 → 落库 → 重算记录总分。
    全程在工作线程内完成（不碰事件循环）；异常落 0，避免任务静默丢失。"""
    c = None
    try:
        eng = grade_by_engine(timu_type=q["tt"], sub_type=q["sub"], dfdlist=q["dfd"],
                              student_file=data, file_ext=ext, full_score=20.0,
                              timu_id=q["qid"], timu_title=q["title"], timu_desc=q["desc"])
        c = opencur()
        if eng and eng.get("auto"):
            save_operate_answer(c, rid, qid, eng["score"], "已上传:"+safe+" | "+eng["note"], _engine_gd(eng))
        else:
            # 引擎不可用/无判分点 → 旧 OOXML/PSD/Access 判分器兜底
            base_data = None
            if ext in (".accdb", ".psd"):
                base_data = load_base_material(qid, (".accdb",) if ext == ".accdb" else (".psd"))
            score, total, hits, miss, note = grade_operate_file(
                "answer"+ext, data, q["desc"], base_data=base_data)
            if score is not None:
                gd = json.dumps({"score": score, "fullScore": 20.0, "total": total,
                                 "hits": hits, "miss": miss, "note": note}, ensure_ascii=False)
                save_operate_answer(c, rid, qid, score, "已上传:"+safe+" | "+note, gd)
            else:
                save_operate_answer(c, rid, qid, 0, "已上传文件待处理:"+safe, None, is_correct=0)
        _recalc_record(c, rid)
        c.commit()
    except Exception as e:
        print("office judge job error:", e)
        try:
            if c is None: c = opencur()
            save_operate_answer(c, rid, qid, 0, "判分异常:"+safe, None, is_correct=0)
            _recalc_record(c, rid); c.commit()
        except Exception as e2:
            print("office judge job fallback error:", e2)
    finally:
        try:
            if c is not None: c.close()
        except Exception:
            pass


def _mysql_judge_job(rid, qid, code, q):
    """后台 worker：启动临时 MySQL 执行学生 SQL → 判分落库 → 重算总分。"""
    c = None
    try:
        folder = q["folder"]
        mat = None
        for d in _material_dirs(folder):
            m = os.path.join(d, "material.zip")
            if os.path.isfile(m):
                mat = m
                break
        if mat is None:
            for d in _material_dirs(folder):
                zips = [f for f in os.listdir(d) if f.lower().endswith(".zip")] if os.path.isdir(d) else []
                if zips:
                    mat = os.path.join(d, zips[0])
                    break
        r = grade_mysql(q["dfd"], code, mat, q["score"])
        c = opencur()
        gd = json.dumps({"score": r["score"], "fullScore": q["score"], "total": r["total"],
                         "hits": r["hits"], "miss": r["miss"], "note": r["note"],
                         "engine": True, "details": r["details"]}, ensure_ascii=False)
        save_operate_answer(c, rid, qid, r["score"], "MySQL | " + r["note"], gd)
        _recalc_record(c, rid)
        c.commit()
    except Exception as e:
        print("mysql judge job error:", e)
        try:
            if c is None:
                c = opencur()
            save_operate_answer(c, rid, qid, 0, "MySQL判分异常", None, is_correct=0)
            _recalc_record(c, rid)
            c.commit()
        except Exception as e2:
            print("mysql judge job fallback error:", e2)
    finally:
        try:
            if c is not None:
                c.close()
        except Exception:
            pass

async def h_upload_operate(msg, ctx):
    rid=int(msg["recordId"]); qid=str(msg["qid"])
    _audit(ctx, "上传操作题", "记录ID=%d 题ID=%s" % (rid, qid))
    c=opencur()
    # 虚拟程序（浏览器/Win10/邮箱）判分文件；文件管理题可附带 paperZip（考生文件夹）
    if msg.get("vapp") or msg.get("paperZip"):
        raw=None
        if msg.get("log"):
            try:
                raw=base64.b64decode(msg["log"])
            except Exception as e:
                c.close(); return {"code":1,"msg":"判分文件解码失败:"+str(e)}
        paper_zip=None
        if msg.get("paperZip"):
            try:
                paper_zip=base64.b64decode(msg["paperZip"])
            except Exception as e:
                c.close(); return {"code":1,"msg":"考生文件夹解码失败:"+str(e)}
        q=_load_operate_q(c,qid)
        if q and q["sub"]==6 and paper_zip:
            r=grade_sub6(q["dfd"], paper_zip, msg.get("favorites") or [], q["score"])
            gd=json.dumps({"score":r["score"],"fullScore":q["score"],"total":r["total"],
                           "hits":r["hits"],"miss":r["miss"],"note":r["note"],
                           "engine":True,"details":r["details"]},ensure_ascii=False)
            save_operate_answer(c,rid,qid,r["score"],"网页收藏 | "+r["note"],gd)
            c.commit(); c.close()
            return {"code":0,"auto":True,"score":r["score"],"note":r["note"],
                    "hits":r["hits"],"miss":r["miss"],"total":r["total"],"details":r["details"]}
        eng=None
        try:
            eng=await _engine_vapp(q, raw, paper_zip=paper_zip)
        except Exception as e:
            print("engine vapp error:",e)
        if eng and eng.get("auto"):
            gd=_engine_gd(eng)
            save_operate_answer(c,rid,qid,eng["score"],"原版引擎判分 | "+eng["note"],gd)
            c.commit(); c.close()
            return {"code":0,"auto":True,"score":eng["score"],"note":eng["note"],
                    "hits":eng["hits"],"miss":eng["miss"],"total":eng["total"],"details":eng["details"]}
        # 回退：旧手工规则（仅有 log 时）
        if raw:
            stem=q["desc"] if q else ""
            g=grade_vapp(stem, raw)
            gd=json.dumps({"score":g["score"],"fullScore":20.0,"total":len(g["hit"])+len(g["miss"]),
                           "hits":g["hit"],"miss":g["miss"],"note":g["note"]},ensure_ascii=False)
            save_operate_answer(c,rid,qid,g["score"],"虚拟程序判分 | "+g["note"],gd)
            c.commit(); c.close()
            return {"code":0,"auto":True,"score":g["score"],"note":g["note"],"hits":g["hit"],"miss":g["miss"],"total":len(g["hit"])+len(g["miss"])}
        c.close()
        return {"code":1,"msg":"文件管理题判分失败（无引擎结果）"}
    # MySQL 题（type=24）：学生上传整个考试文件夹 zip（datadir + tj*.sql 等作答文件）
    if msg.get("mysqlZip") is not None:
        q=_load_operate_q(c,qid)
        if not (q and q["tt"]==24):
            c.close(); return {"code":1,"msg":"该题不是 MySQL 操作题"}
        try:
            zdata=base64.b64decode(msg["mysqlZip"])
        except Exception as e:
            c.close(); return {"code":1,"msg":"考试文件夹解码失败:"+str(e)}
        savedir=os.path.join(os.path.dirname(DB),"operate_uploads"); os.makedirs(savedir,exist_ok=True)
        zpath=os.path.join(savedir, "mysqlfolder_%s_%s.zip" % (rid, qid))
        with open(zpath,"wb") as f: f.write(zdata)
        # 判分需启动临时 MySQL 实例（十几秒）：落 pending，后台池判分
        pending_gd=json.dumps({"pending": True, "note": "判分中，请稍后刷新成绩"}, ensure_ascii=False)
        save_operate_answer(c,rid,qid,None,"MySQL判分中",pending_gd,is_correct=None)
        c.commit(); c.close()
        MYSQL_POOL.submit(_mysql_folder_judge_job,rid,qid,zpath,q)
        return {"code":0,"pending":True,"auto":True,"score":None,
                "note":"判分中，请稍后在成绩中刷新"}
    # 编程题（Python OJ / MySQL）：提交代码文本
    if msg.get("code") is not None:
        q=_load_operate_q(c,qid)
        code=msg.get("code") or ""
        if q and q["tt"]==24:
            # MySQL 判分需启动临时实例（约十几秒、占内存）：落 pending，后台池判分
            pending_gd=json.dumps({"pending": True, "note": "判分中，请稍后刷新成绩"}, ensure_ascii=False)
            save_operate_answer(c,rid,qid,None,"MySQL判分中",pending_gd,is_correct=None)
            c.commit(); c.close()
            MYSQL_POOL.submit(_mysql_judge_job,rid,qid,code,q)
            return {"code":0,"pending":True,"auto":True,"score":None,
                    "note":"判分中，请稍后在成绩中刷新"}
        stem=q["desc"] if q else ""
        score,total,hits,miss,note=grade_python(code, stem, 20.0)
        sc=score if score is not None else 0
        gd=json.dumps({"score":sc,"fullScore":20.0,"total":total,"hits":hits,"miss":miss,"note":note},ensure_ascii=False)
        save_operate_answer(c,rid,qid,sc,"Python编程 | "+note,gd)
        c.commit(); c.close()
        return {"code":0,"auto":True,"score":sc,"note":note,"hits":hits,"miss":miss,"total":total}
    filename=msg["filename"]; b64=msg["data"]
    try:
        data=base64.b64decode(b64)
    except Exception as e:
        c.close(); return {"code":1,"msg":"文件解码失败:"+str(e)}
    savedir=os.path.join(os.path.dirname(DB),"operate_uploads"); os.makedirs(savedir,exist_ok=True)
    safe = filename.replace("/","_").replace("\\","_")
    savepath=os.path.join(savedir, f"{rid}_{qid}_{safe}")
    with open(savepath,"wb") as f: f.write(data)
    q=_load_operate_q(c,qid)
    ext=os.path.splitext(filename)[1].lower()
    # Office/PSD/Access 文件题本机 COM 判分慢（PPT 可达数十秒），60 人集中交卷不能同步等待：
    # 先落一条 pending（交卷据此识别、不误记 0），提交单 worker 后台判分，立即返回“判分中”。
    if q and q["tt"] in JB_FILE:
        pending_gd = json.dumps({"pending": True, "note": "判分中，请稍后刷新成绩"}, ensure_ascii=False)
        save_operate_answer(c, rid, qid, None, "判分中:"+safe, pending_gd, is_correct=None)
        c.commit(); c.close()
        OFFICE_POOL.submit(_office_judge_job, rid, qid, data, ext, q, safe)
        return {"code": 0, "pending": True, "auto": True, "score": None,
                "note": "判分中，请稍后在成绩中刷新"}
    c.close()
    return {"code": 0, "auto": False, "score": 0,
            "note": "该文件类型暂不支持自动判分", "hits": [], "miss": [], "total": 0}

async def h_practice_grade_operate(msg, ctx):
    """自主练习操作题即时判分：不入库，直接返回分数"""
    qid=str(msg["timuID"])
    _audit(ctx, "练习操作题判分", "题ID=%s" % qid)
    c=opencur()
    if msg.get("vapp") or msg.get("paperZip"):
        raw=None
        if msg.get("log"):
            try:
                raw=base64.b64decode(msg["log"])
            except Exception as e:
                c.close(); return {"code":1,"msg":"判分文件解码失败:"+str(e)}
        paper_zip=None
        if msg.get("paperZip"):
            try:
                paper_zip=base64.b64decode(msg["paperZip"])
            except Exception as e:
                c.close(); return {"code":1,"msg":"考生文件夹解码失败:"+str(e)}
        q=_load_operate_q(c,qid); c.close()
        if q and q["sub"]==6 and paper_zip:
            r=grade_sub6(q["dfd"], paper_zip, msg.get("favorites") or [], q["score"])
            return {"code":0,"auto":True,"score":r["score"],"note":r["note"],
                    "hits":r["hits"],"miss":r["miss"],"total":r["total"],"details":r["details"]}
        eng=None
        try:
            eng=await _engine_vapp(q, raw, paper_zip=paper_zip)
        except Exception as e:
            print("engine vapp(practice) error:",e)
        if eng and eng.get("auto"):
            return {"code":0,"auto":True,"score":eng["score"],"note":eng["note"],
                    "hits":eng["hits"],"miss":eng["miss"],"total":eng["total"],"details":eng["details"]}
        if raw:
            stem=q["desc"] if q else ""
            g=grade_vapp(stem, raw)
            return {"code":0,"auto":True,"score":g["score"],"note":g["note"],"hits":g["hit"],"miss":g["miss"],"total":len(g["hit"])+len(g["miss"])}
        return {"code":1,"msg":"文件管理题判分失败"}
    # MySQL 题（type=24）：学生上传整个考试文件夹 zip
    if msg.get("mysqlZip") is not None:
        q=_load_operate_q(c,qid); c.close()
        if not (q and q["tt"]==24):
            return {"code":1,"msg":"该题不是 MySQL 操作题"}
        try:
            zdata=base64.b64decode(msg["mysqlZip"])
        except Exception as e:
            return {"code":1,"msg":"考试文件夹解码失败:"+str(e)}
        import tempfile as _tf
        zpath=os.path.join(_tf.mkdtemp(prefix="mysql_prac_"), "folder.zip")
        with open(zpath,"wb") as f: f.write(zdata)
        folder=q["folder"]; mat=os.path.join(folder,"material.zip")
        if not os.path.exists(mat):
            zips=[f for f in os.listdir(folder) if f.lower().endswith(".zip")]
            mat=os.path.join(folder,zips[0]) if zips else mat
        mat1=os.path.join(folder,"material_1.zip")
        if not os.path.exists(mat1):
            mat1=None
        loop=asyncio.get_event_loop()
        r=await loop.run_in_executor(GENERAL_POOL,
            lambda: grade_mysql_folder(q["dfd"], zpath, mat, q["score"], answer_zip=mat1))
        # 判分诊断日志：保留学生交卷包 + 判分详情，供 MySQL 题 0 分/异常排查
        try:
            _lg = os.path.join(os.path.dirname(DB), "judge_log")
            os.makedirs(_lg, exist_ok=True)
            _ts = time.strftime("%Y%m%d_%H%M%S")
            _keep = os.path.join(_lg, "mysql_prac_%s_%s" % (qid, _ts))
            with open(_keep + ".zip", "wb") as _f:
                _f.write(zdata)
            with open(_keep + ".json", "w", encoding="utf-8") as _f:
                json.dump({"qid": qid, "folder": folder, "mat_ok": os.path.isfile(mat),
                           "score": r.get("score"), "note": r.get("note"),
                           "hits": r.get("hits"), "miss": r.get("miss"),
                           "details": r.get("details")}, _f, ensure_ascii=False, indent=1)
        except Exception as _e:
            print("judge_log 写入失败:", _e)
        try:
            import shutil as _sh
            _sh.rmtree(os.path.dirname(zpath), ignore_errors=True)
        except Exception:
            pass
        return {"code":0,"auto":True,"score":r["score"],"note":r["note"],
                "hits":r["hits"],"miss":r["miss"],"total":r["total"],"details":r["details"]}
    if msg.get("code") is not None:
        q=_load_operate_q(c,qid); c.close()
        code=msg.get("code") or ""
        if q and q["tt"]==24:
            # 练习即时判分：启动临时 MySQL（放线程，避免阻塞事件循环）
            folder=q["folder"]; mat=os.path.join(folder,"material.zip")
            if not os.path.exists(mat):
                zips=[f for f in os.listdir(folder) if f.lower().endswith(".zip")]
                mat=os.path.join(folder,zips[0]) if zips else mat
            loop=asyncio.get_event_loop()
            r=await loop.run_in_executor(GENERAL_POOL,
                lambda: grade_mysql(q["dfd"], code, mat, q["score"]))
            return {"code":0,"auto":True,"score":r["score"],"note":r["note"],
                    "hits":r["hits"],"miss":r["miss"],"total":r["total"],"details":r["details"]}
        stem=q["desc"] if q else ""
        score,total,hits,miss,note=grade_python(code, stem, 20.0)
        if score is None:
            return {"code":0,"auto":False,"score":0,"note":note,"hits":[],"miss":[],"total":total}
        return {"code":0,"auto":True,"score":score,"note":note,"hits":hits,"miss":miss,"total":total}
    filename=msg["filename"]; b64=msg["data"]
    try:
        data=base64.b64decode(b64)
    except Exception as e:
        c.close(); return {"code":1,"msg":"文件解码失败:"+str(e)}
    q=_load_operate_q(c,qid); c.close()
    ext=os.path.splitext(filename)[1].lower()
    eng=None
    try:
        eng=await _engine_file(q,data,ext)
    except Exception as e:
        print("engine file(practice) error:",e)
    if eng and eng.get("auto"):
        return {"code":0,"auto":True,"score":eng["score"],"note":eng["note"],
                "hits":eng["hits"],"miss":eng["miss"],"total":eng["total"],"details":eng["details"]}
    stem = q["desc"] if q else ""
    base_data=None
    if filename.lower().endswith(('.accdb','.psd')):
        base_data=load_base_material(qid, ('.accdb',) if filename.lower().endswith('.accdb') else ('.psd',))
    score,total,hits,miss,note = grade_operate_file(filename, data, stem, base_data=base_data)
    if score is None:
        return {"code":0,"auto":False,"score":0,"note":note,"hits":[],"miss":[],"total":total}
    return {"code":0,"auto":True,"score":score,"note":note,"hits":hits,"miss":miss,"total":total}

async def h_browse_questions(msg, ctx):
    """教师浏览题库手动选题。type=obj按course；type=operate按opType；支持关键词+分页"""
    qtype = msg.get("type", "obj")
    keyword = (msg.get("keyword", "") or "").strip()
    offset = max(0, int(msg.get("offset", 0)))
    limit = min(max(1, int(msg.get("limit", 100))), 500)
    c = opencur()
    if qtype == "operate":
        opType = msg.get("opType", "all")
        cond, params = op_filter(opType)
        if keyword:
            cond += " AND (timuTitle LIKE ? OR descPlain LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        total = c.execute(f"SELECT COUNT(*) FROM operate_timu WHERE 1=1 {cond}", params).fetchone()[0]
        rows = c.execute(f"SELECT timuID,timuTitle,descPlain,timuType FROM operate_timu WHERE 1=1 {cond} ORDER BY timuID LIMIT ? OFFSET ?",
                         params + [limit, offset]).fetchall()
        items = [{"id": r["timuID"], "title": r["timuTitle"] or "",
                  "desc": (r["descPlain"] or ""), "type": r["timuType"]} for r in rows]
    else:
        course = int(msg.get("course", 0))
        cond = " WHERE queType=1"; params = []
        if course > 0:
            cond += " AND course=?"; params.append(course)
        if keyword:
            cond += " AND queName LIKE ?"; params.append(f"%{keyword}%")
        total = c.execute(f"SELECT COUNT(*) FROM questions{cond}", params).fetchone()[0]
        rows = c.execute(f"SELECT queNo,queName,standardAnwser,course,optionA,optionB,optionC,optionD FROM questions{cond} ORDER BY queNo LIMIT ? OFFSET ?",
                         params + [limit, offset]).fetchall()
        items = [{"id": r["queNo"], "stem": r["queName"] or "",
                  "answer": r["standardAnwser"] or "", "course": r["course"],
                  "options": [r["optionA"] or "", r["optionB"] or "", r["optionC"] or "", r["optionD"] or ""]}
                 for r in rows]
    c.close()
    return {"code": 0, "items": items, "total": total, "offset": offset, "limit": limit}

def _fix_zip_filename(name, flag_bits=0):
    """还原 material.zip 内中文文件名。原版导出的 zip 多为 CP437 存 GBK 字节，无 UTF-8 标志。"""
    if flag_bits & 0x800:
        return name  # 已是 UTF-8
    try:
        return name.encode("cp437").decode("gbk")
    except Exception:
        try:
            return name.encode("cp437").decode("gb2312")
        except Exception:
            return name

def _extract_material_zip_to_list(zip_path):
    """解压 material.zip，返回 [{name:相对路径(正斜杠), data:b64}, ...]。空目录也建占位。"""
    out = []
    with zipfile.ZipFile(zip_path, "r") as z:
        for info in z.infolist():
            name = _fix_zip_filename(info.filename, info.flag_bits)
            name = name.replace("\\", "/").lstrip("/")
            if not name or ".." in name.split("/"):
                continue
            if info.is_dir() or name.endswith("/"):
                # 空目录：放一个 .keep 让客户端能建出目录树
                dname = name.rstrip("/") + "/.keep"
                out.append({"name": dname, "data": base64.b64encode(b"").decode()})
                continue
            data = z.read(info)
            out.append({"name": name, "data": base64.b64encode(data).decode()})
    return out

async def h_get_material(msg, ctx):
    """下发操作题素材。
    - Office/PSD/Access：原样返回文件
    - material.zip（文件管理题）：服务端按 GBK 还原中文名后展开为相对路径文件列表，
      客户端写入考生文件夹即可「自动创建/复制」出题干要求的目录结构（含 user/stu 等英文目录）。
    """
    tid=str(msg["timuID"]); c=opencur()
    q=c.execute("SELECT folder,material_files,subType FROM operate_timu WHERE timuID=?",(tid,)).fetchone()
    c.close()
    folder=q["folder"] if q else ""
    out=[]
    # 素材根：优先包内 operate_files\<题号>（随包分发），兼容旧库内的绝对路径
    base = os.path.join(BASE_DIR, "operate_files")
    # 也查 123/operate_files（换机后常见布局）
    base123 = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "123", "operate_files"))
    sub = os.path.basename(folder.rstrip("\\/")) if folder else tid
    dirs=[]
    for root in (base, base123):
        local_dir=os.path.join(root,sub)
        if os.path.isdir(local_dir) and local_dir not in dirs:
            dirs.append(local_dir)
    if folder and os.path.isdir(folder) and folder not in dirs:
        dirs.append(folder)
    OFFICE_EXT = (".docx",".doc",".xlsx",".xls",".pptx",".ppt",".html",".htm",".txt",".accdb",".psd",
                  ".bmp",".jpg",".jpeg",".png",".gif",".rar",".zip",".pdf",".csv")
    # 预览图：素材目录里的 answer_sc* 图片（标准答案效果图）单独下发用于界面右侧显示，
    # 不落入考生文件夹，不影响判分
    PREVIEW_EXT = (".jpg",".jpeg",".png",".bmp",".gif")
    previews=[]
    for d in dirs:
        try:
            for fn in os.listdir(d):
                low=fn.lower()
                if low.startswith("answer_sc") and low.endswith(PREVIEW_EXT):
                    p=os.path.join(d,fn)
                    if os.path.isfile(p):
                        previews.append({"name":fn,"data":base64.b64encode(open(p,"rb").read()).decode()})
        except Exception:
            pass
        if previews: break
    for d in dirs:
        # 优先展开 material.zip（文件管理题）
        zp = os.path.join(d, "material.zip")
        if os.path.isfile(zp):
            try:
                out = _extract_material_zip_to_list(zp)
                if out:
                    return {"code":0,"files":out,"fromZip":True,"subType": (q["subType"] if q else 0),
                            "previews":previews}
            except Exception as e:
                print("extract material.zip failed:", e)
        for fn in os.listdir(d):
            if fn.startswith("answer_"): continue
            # 文件名含 _1（如 material_1.docx / material_1.accdb）为题库标准答案，
            # 一律不下发学生端（判分端不使用这些文件，无功能损失）
            if "_1" in fn: continue
            if fn.lower() == "material.zip": continue
            low = fn.lower()
            if low.endswith(OFFICE_EXT) or (not os.path.splitext(fn)[1] and os.path.isfile(os.path.join(d,fn))):
                p=os.path.join(d,fn)
                if not os.path.isfile(p): continue
                out.append({"name":fn,"data":base64.b64encode(open(p,"rb").read()).decode()})
        if out: break
    return {"code":0,"files":out,"fromZip":False,"subType": (q["subType"] if q else 0),"previews":previews}

async def h_get_material_zip(msg, ctx):
    """下发操作题原始素材压缩包（material.zip 字节）。

    供学生端 MySQL 题（timuType=24）配置本地环境使用：素材是 MySQL 数据目录
    快照，学生端拿到 zip 解压成 datadir，用本机 mysqld 起临时实例作答。
    """
    tid = str(msg["timuID"])
    c = opencur()
    q = c.execute("SELECT folder FROM operate_timu WHERE timuID=?", (tid,)).fetchone()
    c.close()
    folder = q["folder"] if q else ""
    base = os.path.join(BASE_DIR, "operate_files")
    base123 = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "123", "operate_files"))
    sub = os.path.basename(folder.rstrip("\\/")) if folder else tid
    candidates = [os.path.join(base, sub)]
    if base123 != base:
        candidates.append(os.path.join(base123, sub))
    if folder and os.path.isdir(folder):
        candidates.append(folder)
    for d in candidates:
        if not os.path.isdir(d):
            continue
        zp = os.path.join(d, "material.zip")
        if not os.path.isfile(zp):
            # 兜底找 zip 时排除标准答案包（material_1.zip 等含 _1 的文件）
            zips = [f for f in os.listdir(d)
                    if f.lower().endswith(".zip") and not f.lower().startswith("answer_")
                    and "_1" not in f]
            zp = os.path.join(d, zips[0]) if zips else zp
        if os.path.isfile(zp):
            try:
                with open(zp, "rb") as fh:
                    return {"code": 0, "zip": base64.b64encode(fh.read()).decode(),
                            "name": os.path.basename(zp)}
            except Exception as e:
                return {"code": 1, "msg": "素材包读取失败: %s" % e}
    return {"code": 1, "msg": "未找到该题素材包"}

async def h_get_standard_answer(msg, ctx):
    """下发操作题标准答案文件（交卷后学生端查看用）。

    素材目录中名字含 _1 的文件（material_1.docx 等）或 answer_ 前缀文件即标准答案；
    找到后原样下发（base64），学生端保存后用系统默认程序打开。
    """
    tid = str(msg["timuID"])
    c = opencur()
    q = c.execute("SELECT folder FROM operate_timu WHERE timuID=?", (tid,)).fetchone()
    c.close()
    folder = q["folder"] if q else ""
    base = os.path.join(BASE_DIR, "operate_files")
    base123 = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "123", "operate_files"))
    sub = os.path.basename(folder.rstrip("\\/")) if folder else tid
    dirs = []
    for root in (base, base123):
        local_dir = os.path.join(root, sub)
        if os.path.isdir(local_dir) and local_dir not in dirs:
            dirs.append(local_dir)
    if folder and os.path.isdir(folder) and folder not in dirs:
        dirs.append(folder)
    ANS_EXT = (".docx",".doc",".xlsx",".xls",".pptx",".ppt",".accdb",".mdb",".psd",".pdf",".txt",
               ".csv",".zip",".rar",".bmp",".jpg",".jpeg",".png",".gif",".html",".htm")
    for d in dirs:
        files = sorted(os.listdir(d))
        # 优先 material_1.* 等含 _1 的完成态标准答案文件，其次 answer_* 前缀文件
        for fn in files:
            low = fn.lower()
            if "_1" in fn and low.endswith(ANS_EXT):
                p = os.path.join(d, fn)
                if os.path.isfile(p):
                    try:
                        with open(p, "rb") as fh:
                            return {"code": 0, "name": fn,
                                    "data": base64.b64encode(fh.read()).decode()}
                    except Exception as e:
                        return {"code": 1, "msg": "标准答案读取失败: %s" % e}
        for fn in files:
            low = fn.lower()
            if low.startswith("answer_") and low.endswith(ANS_EXT):
                p = os.path.join(d, fn)
                if os.path.isfile(p):
                    try:
                        with open(p, "rb") as fh:
                            return {"code": 0, "name": fn,
                                    "data": base64.b64encode(fh.read()).decode()}
                    except Exception as e:
                        return {"code": 1, "msg": "标准答案读取失败: %s" % e}
    return {"code": 1, "msg": "该题暂无标准答案文件"}

async def h_wrong_list(msg, ctx):
    u=ctx["user"]; c=opencur()
    rows=c.execute("""SELECT w.qid,w.my_answer,w.right_answer,w.stem,w.added_at,w.ref_type,q.course
                      FROM wrong_questions w LEFT JOIN questions q ON w.qid=q.queNo
                      WHERE w.student_id=? AND w.resolved=0 ORDER BY w.id DESC""",(u["id"],)).fetchall()
    out=[]
    for r in rows:
        q=c.execute("SELECT optionA,optionB,optionC,optionD,resolutionTips FROM questions WHERE queNo=?",(r["qid"],)).fetchone()
        item={"qid":r["qid"],"stem":r["stem"],"refType":r["ref_type"] or "obj","course":r["course"],
                    "my_answer":r["my_answer"] or "","right_answer":r["right_answer"] or "",
                    "tip":(q["resolutionTips"] or "") if q else "",
                    "options":[{"k":"A","t":q["optionA"]},{"k":"B","t":q["optionB"]},{"k":"C","t":q["optionC"]},{"k":"D","t":q["optionD"]}] if q else []}
        # 操作题错题补题型信息：错题重做 ExamWindow 需要 type/subType/vapp 才能识别
        # 虚拟程序题（自动启动 Win10/Edge/邮箱）与 Python 编程题，否则按文件素材题处理报“没有素材”
        if item["refType"]=="operate":
            ot=c.execute("SELECT timuType,subType,descPlain,material_files FROM operate_timu WHERE timuID=?",(r["qid"],)).fetchone()
            if ot:
                _stem = ot["descPlain"] or r["stem"] or ""
                item["type"]=ot["timuType"]; item["subType"]=ot["subType"]
                # 文件管理题（material.zip）需要客户端自动解压素材到考生文件夹并整目录打包上传
                item["hasZip"] = ("zip" in (ot["material_files"] or "").lower())
                item["module"] = op_module_of(ot["timuType"], ot["subType"] or 0, _stem, item["hasZip"])
                _v=vapp_of(ot["timuType"], _stem, sub_type=ot["subType"], has_zip=item["hasZip"])
                if _v: item["vapp"]=_v
        out.append(item)
    c.close()
    return {"code":0,"wrong":out}

async def h_wrong_submit(msg, ctx):
    u=ctx["user"]; answers=msg.get("answers",[])
    got,total,detail=grade_answers(answers)
    _audit(ctx, "错题练习提交", "得分=%.1f/%d 题数=%d" % (got, total, len(answers)))
    c=opencur()
    for d in detail:
        if d.get("correct"):
            c.execute("UPDATE wrong_questions SET resolved=1 WHERE student_id=? AND qid=?",(u["id"],d["qid"]))
    c.commit(); c.close()
    return {"code":0,"score":got,"total":total,"detail":detail}

async def h_my_history(msg, ctx):
    u=ctx["user"]; c=opencur()
    rows=c.execute("""SELECT r.id,r.score,r.status,r.submitted_at,h.title,h.kind,h.qtype
                      FROM records r JOIN homeworks h ON r.homework_id=h.id
                      WHERE r.student_id=? AND r.status='submitted' ORDER BY r.id DESC""",(u["id"],)).fetchall()
    c.close()
    return {"code":0,"history":[{"id":r["id"],"title":r["title"],"kind":r["kind"],"qtype":r["qtype"],"score":r["score"],"at":r["submitted_at"]} for r in rows]}

async def h_operate_detail(msg, ctx):
    c=opencur()
    r=c.execute("SELECT timuID,timuTitle,descPlain,material_files,screenshots FROM operate_timu WHERE timuID=?",(str(msg["timuID"]),)).fetchone()
    c.close()
    if not r: return {"code":1,"msg":"未找到"}
    return {"code":0,"timuID":r["timuID"],"title":r["timuTitle"],"desc":r["descPlain"],
            "materials":r["material_files"] or "[]","screenshots":r["screenshots"] or "[]"}

async def h_operate_submissions(msg, ctx):
    hid=int(msg["homeworkId"]); c=opencur()
    rows=c.execute("""SELECT rec.id AS rec_id,u.username,u.display_name,a.question_id,a.student_answer,a.score
                      FROM records rec JOIN users u ON rec.student_id=u.id
                      LEFT JOIN answers a ON a.record_id=rec.id AND a.ref_type='operate'
                      WHERE rec.homework_id=? ORDER BY u.username, a.question_id""",(hid,)).fetchall()
    out={}
    for r in rows:
        k=r["username"]
        if k not in out: out[k]={"name":r["display_name"],"recordId":r["rec_id"],"answers":[]}
        if r["question_id"]: out[k]["answers"].append({"qid":r["question_id"],"text":r["student_answer"] or "","score":r["score"]})
    c.close()
    return {"code":0,"students":list(out.values())}

async def h_grade_operate(msg, ctx):
    rid=int(msg["recordId"]); qid=str(msg["qid"]); score=float(msg["score"])
    _audit(ctx, "教师手动判分", "记录ID=%d 题ID=%s 得分=%.1f" % (rid, qid, score))
    c=opencur()
    c.execute("UPDATE answers SET score=?,is_correct=1,graded_at=? WHERE record_id=? AND question_id=?",(score,now(),rid,qid))
    c.commit(); c.close()
    return {"code":0,"msg":"已评分"}

async def h_analysis(msg, ctx):
    hid=int(msg["homeworkId"]); c=opencur()
    rows=c.execute("SELECT score FROM records WHERE homework_id=? AND status='submitted' AND score IS NOT NULL",(hid,)).fetchall()
    scores=[r["score"] for r in rows]
    if not scores: return {"code":0,"n":0,"avg":0,"max":0,"min":0,"perQuestion":[]}
    n=len(scores)
    qrows=c.execute("""SELECT a.question_id,
                              SUM(CASE WHEN a.is_correct=1 THEN 1 ELSE 0 END) AS ok, COUNT(*) AS total
                       FROM answers a JOIN records r ON a.record_id=r.id
                       WHERE r.homework_id=? AND a.ref_type='obj' GROUP BY a.question_id""",(hid,)).fetchall()
    perq=[{"qid":r["question_id"],"rate":round(r["ok"]*100.0/r["total"],0) if r["total"] else 0} for r in qrows]
    c.close()
    return {"code":0,"n":n,"avg":round(sum(scores)/n,1),"max":max(scores),"min":min(scores),"perQuestion":perq}

async def h_preview_paper(msg, ctx):
    hid=int(msg["homeworkId"]); c=opencur()
    rows=c.execute("""SELECT hq.question_id,hq.ref_type,q.queName,q.optionA,q.optionB,q.optionC,q.optionD,q.standardAnwser
                      FROM homework_questions hq LEFT JOIN questions q ON hq.question_id=q.queNo
                      WHERE hq.homework_id=? ORDER BY hq.sort_no""",(hid,)).fetchall()
    out=[]
    for r in rows:
        stem = r["queName"]
        if r["ref_type"]=="operate":
            op=c.execute("SELECT timuTitle,descPlain FROM operate_timu WHERE timuID=?",(r["question_id"],)).fetchone()
            if op:
                stem = (op["timuTitle"] or "")+"\n"+(op["descPlain"] or "")
        out.append({"qid":r["question_id"],"refType":r["ref_type"],"stem":stem,
                    "options":[{"k":"A","t":r["optionA"]},{"k":"B","t":r["optionB"]},{"k":"C","t":r["optionC"]},{"k":"D","t":r["optionD"]}],
                    "answer":r["standardAnwser"]})
    c.close()
    return {"code":0,"questions":out}

async def h_delete_paper(msg, ctx):
    hid=int(msg["homeworkId"]); c=opencur()
    c.execute("DELETE FROM homework_questions WHERE homework_id=?",(hid,))
    c.execute("DELETE FROM homeworks WHERE id=? AND creator_id=?",(hid,ctx["user"]["id"]))
    c.commit(); c.close()
    return {"code":0,"msg":"已删除"}

def read_xlsx(data):
    """纯标准库解析 xlsx，返回二维列表（每行是单元格值列表）"""
    import zipfile, xml.etree.ElementTree as ET
    z=zipfile.ZipFile(io.BytesIO(data))
    NS="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ss=[]
    if "xl/sharedStrings.xml" in z.namelist():
        root=ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.iter("{%s}si"%NS):
            ss.append("".join(t.text or "" for t in si.iter("{%s}t"%NS)))
    sheet_name="xl/worksheets/sheet1.xml"
    for n in z.namelist():
        if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"):
            sheet_name=n; break
    root=ET.fromstring(z.read(sheet_name))
    rows=[]
    for row in root.iter("{%s}row"%NS):
        cells={}; maxcol=0; nxt=1
        for c in row.findall("{%s}c"%NS):
            ref=c.get("r")
            if ref:
                col=0
                for ch in ref:
                    if ch.isalpha(): col=col*26+(ord(ch.upper())-64)
                    else: break
                nxt=col+1
            else:
                # 部分国产软件生成的 xlsx 省略 r 属性，按出现顺序递增列号
                col=nxt; nxt+=1
            t=c.get("t"); v=c.find("{%s}v"%NS); val=""
            if t=="s" and v is not None and v.text is not None:
                idx=int(v.text); val=ss[idx] if idx<len(ss) else ""
            elif t=="inlineStr":
                isel=c.find("{%s}is"%NS)
                if isel is not None: val="".join(t2.text or "" for t2 in isel.iter("{%s}t"%NS))
            elif v is not None and v.text is not None:
                val=v.text
            cells[col]=val
            if col>maxcol: maxcol=col
        rows.append([cells.get(i,"") for i in range(1,maxcol+1)])
    return rows

async def h_import_students(msg, ctx):
    """通过 xlsx 一键导入学生账号+班级。
    智能识别表头列：考号/学号/账号→登录账号，姓名→姓名，密码→密码(空则123456)，班级→班级(不存在自动创建)。
    无表头或表头未识别时回退固定顺序：姓名|账号|密码|班级。"""
    b64=msg.get("data","")
    try:
        data=base64.b64decode(b64)
    except Exception as e:
        return {"code":1,"msg":"文件解码失败:"+str(e)}
    try:
        rows=read_xlsx(data)
    except Exception as e:
        return {"code":1,"msg":"xlsx解析失败，请确认是 .xlsx 格式:"+str(e)}
    # ---- 表头识别与列映射（按表头名找列，考号优先作账号）----
    col_map=None
    if rows:
        head=[str(x).strip() for x in rows[0]]
        keys=[("考号","username"),("学号","username"),("账号","username"),("用户名","username"),
              ("姓名","name"),("名字","name"),("密码","password"),("班级","classname"),
              ("username","username"),("account","username"),("name","name"),
              ("password","password"),("classname","classname")]
        mapped={}
        for i,h in enumerate(head):
            lh=h.lower()
            for k,v in keys:
                if lh==k or lh.startswith(k):
                    if v not in mapped:
                        mapped[v]=i
        if "username" in mapped or "name" in mapped or "classname" in mapped:
            col_map=mapped
    if col_map is not None:
        rows=rows[1:]
    c=opencur()
    added=0; updated=0; skipped=0; new_classes=[]
    for row in rows:
        if not row or all(str(x).strip()=="" for x in row): continue
        if col_map is not None:
            def gc(key):
                idx=col_map.get(key)
                return str(row[idx]).strip() if idx is not None and idx<len(row) else ""
            username=gc("username")
            name=gc("name")
            password=gc("password") or "123456"
            classname=gc("classname")
        else:
            name=str(row[0]).strip() if len(row)>0 else ""
            username=str(row[1]).strip() if len(row)>1 else ""
            password=str(row[2]).strip() if len(row)>2 and str(row[2]).strip() else "123456"
            classname=str(row[3]).strip() if len(row)>3 else ""
        if not username: skipped+=1; continue
        cid=None
        if classname:
            cr=c.execute("SELECT id FROM classes WHERE name=?",(classname,)).fetchone()
            if cr: cid=cr["id"]
            else:
                cur=c.execute("INSERT INTO classes(name) VALUES(?)",(classname,)); cid=cur.lastrowid; new_classes.append(classname)
        ex=c.execute("SELECT id FROM users WHERE username=? AND role='student'",(username,)).fetchone()
        salt=secrets.token_hex(8); h=hashlib.sha256((salt+password).encode()).hexdigest()
        disp=name if name else username
        if ex:
            c.execute("UPDATE users SET salt=?,pwd_hash=?,display_name=?,class_id=? WHERE id=?",(salt,h,disp,cid,ex["id"])); updated+=1
        else:
            c.execute("INSERT INTO users(username,salt,pwd_hash,display_name,role,class_id) VALUES(?,?,?,?, 'student',?)",(username,salt,h,disp,cid)); added+=1
    c.commit(); c.close()
    _audit(ctx, "导入学生", "新增%d人 更新%d人 跳过%d行" % (added, updated, skipped))
    return {"code":0,"added":added,"updated":updated,"skipped":skipped,"newClasses":new_classes,
            "msg":"导入完成：新增%d人，更新%d人，跳过%d行"%(added,updated,skipped)}

async def h_ping(msg, ctx):
    """连通性自检（免登录）：frpc 穿透后由服务端 GUI / 客户端回连公网地址，验证隧道是否真通"""
    return {"code": 0, "pong": True, "ts": now()}

async def h_get_nodes(msg, ctx):
    """客户端登录成功后调用：返回服务端配置的候选节点列表（用于登录超时后「更换节点」）。
    节点列表在服务端 GUI「内网穿透 → 管理节点」里维护，存于 frpc_config.json。"""
    nodes = []
    try:
        p = os.path.join(BASE_DIR, "frpc_config.json")
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            nodes = [n for n in (cfg.get("nodes") or []) if isinstance(n, str) and n.strip()]
    except Exception:
        pass
    return {"code": 0, "nodes": nodes}

ROUTES = {
    "login":h_login, "listCourses":h_list_courses, "listClasses":h_list_classes,
    "stuHomeworks":h_stu_homeworks, "startHomework":h_start_homework,
    "submit":h_submit, "myResult":h_my_result,
    "startPractice":h_start_practice, "submitPractice":h_submit_practice, "practiceHistory":h_practice_history,
    "wrongList":h_wrong_list, "wrongSubmit":h_wrong_submit, "myHistory":h_my_history,
    "operateDetail":h_operate_detail,
    "uploadOperate":h_upload_operate,
    "practiceGradeOperate":h_practice_grade_operate,
    "listOperateTypes":h_list_operate_types,
    "getMaterial":h_get_material, "getMaterialZip":h_get_material_zip,
    "getStandardAnswer":h_get_standard_answer,
    "tchHomeworks":h_tch_homeworks, "createHomework":h_create_homework,
    "publish":h_publish, "closeHomework":h_close_hw,
    "classResults":h_class_results, "analysis":h_analysis,
    "operateSubmissions":h_operate_submissions, "gradeOperate":h_grade_operate,
    "listStudents":h_list_students, "addStudent":h_add_student, "updateStudent":h_update_student,
    "deleteStudent":h_delete_student,
    "importStudents":h_import_students,
    "addClass":h_add_class, "deleteClass":h_delete_class, "recordDetail":h_record_detail,
    "operateList":h_operate_list, "previewPaper":h_preview_paper, "deletePaper":h_delete_paper,
    "browseQuestions":h_browse_questions,
    "heartbeat":h_heartbeat, "monitorList":h_monitor_list, "auditLogs":h_audit_logs,
    "ping":h_ping, "getNodes":h_get_nodes,
}

# ---------- 传输加密：AES-256-GCM 认证加密（机密性 + 完整性），
# 帧 = 4字节大端长度 + nonce(12) + GCM tag(16) + 密文 ----------
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
_CRYPTO_KEY = hashlib.pbkdf2_hmac("sha256", b"ttlx-secure-transport-2026", b"ttlx-transport", 120000, 32)
_aesgcm = AESGCM(_CRYPTO_KEY)

def _encrypt(body: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + _aesgcm.encrypt(nonce, body, None)

def _decrypt(raw: bytes) -> bytes:
    """解密并校验完整性（GCM tag 不符抛异常）。"""
    return _aesgcm.decrypt(raw[:12], raw[12:], None)

async def read_msg(reader):
    hdr = await reader.readexactly(4)
    n = int.from_bytes(hdr, "big")
    if n <= 0 or n > MAX_FRAME:
        raise ConnectionError("非法帧长度:%d" % n)
    body = await reader.readexactly(n)
    try:
        return json.loads(_decrypt(body).decode("utf-8"))
    except Exception as e:
        raise ConnectionError("解密失败或数据被篡改: %s" % e)

def write_msg(writer, obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    raw = _encrypt(body)
    writer.write(len(raw).to_bytes(4, "big") + raw)

# ---------- 连接参数（经 frp / 公网转发时必须调整） ----------
MAX_FRAME = 64 * 1024 * 1024   # 单帧上限，防止被伪造长度拖死
READ_IDLE_TIMEOUT = 900        # 单个连接最长静默时间（秒），超时回收，避免半开连接堆积
WRITE_TIMEOUT = 60             # 单次响应写出最长等待：慢客户端不得拖死整个事件循环
SLOW_LOG = 3.0                 # 慢请求阈值，便于定位经穿透后的卡顿

def tune_socket(sock):
    """公网转发友好：禁 Nagle、放大缓冲、开启 TCP keepalive。
    frp/运营商 NAT 会静默掐掉长时间无数据的连接，keepalive 保活。"""
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass
    for opt in (socket.SO_RCVBUF, socket.SO_SNDBUF):
        try:
            sock.setsockopt(socket.SOL_SOCKET, opt, 1 << 20)
        except Exception:
            pass
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 3000))
        else:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
    except Exception:
        pass

async def handle(reader, writer):
    ctx = {"user": None}
    peername = writer.get_extra_info("peername")
    sock = writer.get_extra_info("socket")
    if sock is not None:
        tune_socket(sock)
    print(f"[{now()}] 连接 {peername}")
    try:
        while True:
            try:
                # 半开连接（frp 断连但本机收不到 FIN）会永久挂住 readexactly，
                # 必须加空闲超时回收，否则连接数越堆越多、事件循环越来越慢。
                msg = await asyncio.wait_for(read_msg(reader), READ_IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                print(f"[{now()}] 空闲超时({READ_IDLE_TIMEOUT}s)断开 {peername}")
                break
            except (asyncio.IncompleteReadError, ConnectionResetError, ConnectionError):
                break
            cmd = msg.get("cmd","")
            handler = ROUTES.get(cmd)
            t0 = time.time()
            try:
                if handler is None:
                    out = {"code":99,"msg":"未知命令:"+cmd}
                elif cmd!="login" and cmd!="ping" and ctx["user"] is None:
                    out = {"code":401,"msg":"未登录"}
                else:
                    out = await handler(msg, ctx)
            except Exception as e:
                import traceback; traceback.print_exc()
                out = {"code":500,"msg":"服务器异常:"+str(e)}
            dt = time.time() - t0
            if dt > SLOW_LOG:
                print(f"[{now()}] 慢请求 {cmd} 耗时 {dt:.1f}s（来自 {peername}）")
            write_msg(writer, out)
            try:
                # 客户端（尤其经公网穿透）读得慢时 drain 会无限阻塞，
                # 而所有连接共用一个事件循环——一个慢客户端会拖垮全部客户端。
                await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT)
            except asyncio.TimeoutError:
                print(f"[{now()}] 写出超时({WRITE_TIMEOUT}s)断开 {peername}")
                break
            except (ConnectionResetError, BrokenPipeError):
                break
    finally:
        try: writer.close()
        except: pass
        print(f"[{now()}] 断开 {peername}")

async def main():
    if not os.path.exists(DB):
        print("exam.db 不存在，请先运行 db_init.py"); return
    port = PORT
    argv = sys.argv[1:]
    for k, a in enumerate(argv):                      # 支持 --port=1234 或 --port 1234
        if a.startswith("--port="):
            port = int(a.split("=", 1)[1])
        elif a == "--port" and k + 1 < len(argv):
            port = int(argv[k + 1])
    srv = await asyncio.start_server(handle, HOST, port)
    print(f"[{now()}] 教学系统服务端已启动: 0.0.0.0:{port}")
    print(f"[{now()}] 数据库: {DB}")
    async with srv:
        await srv.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("服务端已停止")
