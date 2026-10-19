# -*- coding: utf-8 -*-
"""初始化 exam.db：客观题从 questions.jsonl 导入，操作题从 tiku.db 复制，建应用表+默认账号"""
import sqlite3, os, sys, io, hashlib, secrets, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 路径相对本文件（app/server/db_init.py），换机任意目录解压都能重建题库
_HERE   = os.path.dirname(os.path.abspath(__file__))
QJSONL  = os.path.join(_HERE, "..", "..", "123", "questions.jsonl")
TIKU    = os.path.join(_HERE, "..", "..", "123", "tiku.db")
DST     = os.path.join(_HERE, "exam.db")

if os.path.exists(DST):
    os.remove(DST)
dst = sqlite3.connect(DST)
dst.execute("PRAGMA journal_mode=WAL")
cur = dst.cursor()

# 1) questions 表
cur.executescript("""
CREATE TABLE questions(
  queNo TEXT PRIMARY KEY,
  spec INTEGER, course INTEGER, queType INTEGER,
  queName TEXT, questionSubject TEXT,
  optionA TEXT, optionB TEXT, optionC TEXT, optionD TEXT,
  option5 TEXT, option6 TEXT,
  standardAnwser TEXT, resolutionTips TEXT,
  queScore REAL, difficultLevel INTEGER,
  fK_CourseType INTEGER, fK_KnowledgePoint INTEGER
);
""")
cols = ["spec","course","queNo","queName","queType","questionSubject",
        "optionA","optionB","optionC","optionD","option5","option6",
        "resolutionTips","standardAnwser","queScore","difficultLevel",
        "fK_CourseType","fK_KnowledgePoint"]
ph = ",".join("?"*len(cols))
rows = []
with open(QJSONL, encoding="utf-8") as f:
    for line in f:
        line=line.strip()
        if not line: continue
        o=json.loads(line)
        rows.append(tuple(o.get(c,"") for c in cols))
cur.executemany("INSERT INTO questions(%s) VALUES(%s)" % (",".join(cols), ph), rows)
print("questions 导入:", len(rows))

# 2) operate_timu 从 tiku.db 复制（descPlain 非空）
dst.execute("ATTACH DATABASE ? AS t", (TIKU,))
cur.executescript("""
CREATE TABLE operate_timu AS
  SELECT timuID,timuTitle,descPlain,descEnc,score,timuType,subType,level,specialty,
         srcFileName,material_files,screenshots,standAnswer,folder,dfdList
  FROM t.operate_timu WHERE descPlain IS NOT NULL AND descPlain<>'';
""")
no = cur.execute("SELECT COUNT(*) FROM operate_timu").fetchone()[0]
print("operate_timu 复制(非C明文):", no)
dst.execute("DETACH DATABASE t")

# 3) 应用表
cur.executescript("""
CREATE TABLE classes(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, teacher_id INTEGER);
CREATE TABLE users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
  salt TEXT NOT NULL, pwd_hash TEXT NOT NULL, display_name TEXT,
  role TEXT NOT NULL CHECK(role IN('teacher','student')), class_id INTEGER,
  created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE homeworks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, creator_id INTEGER,
  class_id INTEGER, qtype TEXT NOT NULL, config TEXT,
  kind TEXT DEFAULT 'homework',
  status TEXT DEFAULT 'draft', start_at TEXT, end_at TEXT,
  duration_sec INTEGER DEFAULT 0, show_result INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE homework_questions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, homework_id INTEGER,
  ref_type TEXT, question_id TEXT, score REAL, sort_no INTEGER);
CREATE TABLE records(
  id INTEGER PRIMARY KEY AUTOINCREMENT, homework_id INTEGER, student_id INTEGER,
  started_at TEXT, submitted_at TEXT, score REAL, status TEXT DEFAULT 'in_progress');
CREATE TABLE answers(
  id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER,
  ref_type TEXT, question_id TEXT, student_answer TEXT,
  is_correct INTEGER, score REAL DEFAULT 0, graded_at TEXT);
CREATE TABLE wrong_questions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  student_id INTEGER, qid TEXT, ref_type TEXT,
  my_answer TEXT, right_answer TEXT, stem TEXT,
  added_at TEXT DEFAULT (datetime('now','localtime')),
  resolved INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_records_hw ON records(homework_id);
CREATE INDEX IF NOT EXISTS idx_records_stu ON records(student_id);
CREATE INDEX IF NOT EXISTS idx_hwq_hw ON homework_questions(homework_id);
CREATE INDEX IF NOT EXISTS idx_ans_rec ON answers(record_id);
CREATE INDEX IF NOT EXISTS idx_wrong_stu ON wrong_questions(student_id, resolved);
CREATE INDEX IF NOT EXISTS idx_q_course ON questions(course);
CREATE INDEX IF NOT EXISTS idx_q_type ON questions(queType);
""")

def mkuser(un, pwd, name, role, cid=None):
    salt=secrets.token_hex(8); h=hashlib.sha256((salt+pwd).encode()).hexdigest()
    cur.execute("INSERT INTO users(username,salt,pwd_hash,display_name,role,class_id) VALUES(?,?,?,?,?,?)",
                (un,salt,h,name,role,cid)); return cur.lastrowid

cur.execute("INSERT INTO classes(name,teacher_id) VALUES(?,?)",("计算机1班",None)); cid=cur.lastrowid
tid=mkuser("admin","admin123","管理员老师","teacher")
cur.execute("UPDATE classes SET teacher_id=? WHERE id=?",(tid,cid))
mkuser("demo","demo123","演示学生","student",cid)
for i in range(1,6): mkuser("stu%02d"%i,"123456","学生%02d"%i,"student",cid)

dst.commit()
print("默认账号: admin/admin123(教师), demo/demo123(学生), stu01~05/123456")
print("exam.db 大小: %.1f MB" % (os.path.getsize(DST)/1024/1024))
dst.close()
