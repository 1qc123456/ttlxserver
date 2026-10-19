# 湖北省计算机技能高考（天天乐学替代系统）

> 湖北省计算机技能高考考试 / 练习系统：服务端 + 教师端 + 学生端，全题型全自动机器判分。
> 三端均为 Python 应用（服务端 asyncio 单 TCP 端口，客户端 PySide6 GUI），
> 可打包为单 exe（内嵌 Python 运行时，目标机免装 Python）。

## 项目简介

- **服务端**：Python `asyncio`，单 TCP 端口（默认 5943），帧格式 = 4 字节大端长度 + UTF-8 JSON；SQLite 数据库。
- **教师端 / 学生端**：Python GUI，`PySide6 + PySide6-Fluent-Widgets`（Win11 扁平化风格、全中文）。
- **判分**：客观题（单选/多选/判断）服务端即时判分；操作题（Word/Excel/PPT/PS/Access/MySQL/Python/Win10设置/网络/邮箱/浏览器收藏等）全自动机器判分。
- **内置 MySQL 运行时**：MySQL 题无需本机安装 MySQL，服务端内置免安装 MySQL 5.7 临时实例判分。
- **本仓库包含完整服务端**：判分引擎（judge/）、MySQL 运行时（runtime/）、题库（tiku/：客观题 questions.jsonl + 操作题 tiku.db + 解密技术文档）、初始数据库（exam.db）、全部操作题素材（server/operate_files/，3315 个文件 / 约 1.1GB）。

## 目录结构

```
github\
├─ server\                # 服务端源码
│  ├─ server.py           # 核心服务（TCP 协议、判分调度）
│  ├─ server_gui.py       # 服务端 GUI 控制台（tkinter）
│  ├─ app_paths.py        # 路径解析（exe 同级 / 源码同级）
│  ├─ db_init.py          # 题库重建（从题库源生成 exam.db）
│  ├─ judge_bridge.py     # 判分桥接
│  ├─ mysql_grader.py     # MySQL 题判分器
│  ├─ sub6_grader.py      # 浏览器收藏/另存题判分器
│  ├─ accdb_grader.py     # Access 题判分器（DAO）
│  ├─ code_grader.py      # Python 题判分器（沙箱 OJ）
│  ├─ psd_grader.py       # Photoshop 题判分器
│  ├─ operate_grader.py   # 操作题通用判分器
│  ├─ vapp_grader.py      # Win10 设置/网络/邮箱题判分器
│  ├─ pia_enum.txt        # Office 枚举名=数值表（判分用）
│  ├─ frpc_config.json    # 内网穿透配置模板（占位）
│  ├─ judge\              # 判分引擎（原版 DLL + Python 桥接）
│  ├─ runtime\            # 内置免安装 MySQL 5.7 运行时（MySQL 题判分用）
│  ├─ frpc\frpc.exe       # 内网穿透客户端（frpc）
│  ├─ exam.db             # 初始数据库（题库 + 账号，SQLite）
│  └─ accdb\              # Access DAO 判分 PowerShell 脚本
├─ tiku\                  # 题库源
│  ├─ questions.jsonl     # 客观题库（单选/多选/判断）
│  ├─ tiku.db             # 操作题库
│  └─ 解密技术文档.md     # 原版数据库解密说明
└─ pyclient\              # 客户端源码
   ├─ common\             # 学生端/教师端共用
   │  ├─ net_client.py    # TCP 客户端协议
   │  ├─ node_switch.py   # 服务器节点切换
   │  ├─ client_cfg.py    # 客户端配置
   │  ├─ edge_web.py      # 浏览器操作（收藏/另存）
   │  ├─ grade_detail.py  # 成绩明细
   │  ├─ mysql_env.py     # MySQL 环境检测
   │  ├─ waiting.py       # 请稍等界面
   │  ├─ app_icon.py      # 任务栏图标
   │  ├─ utils.py         # 工具函数
   │  └─ assets\          # 学生端图标（stu.ico / stu.jpg）
   ├─ student\            # 学生端（登录、主界面、答题窗口）
   └─ teacher\            # 教师端（登录、主界面、班级/作业/监控管理）
```

## 运行方式

### 源码运行（开发 / 调试）

1. 安装 **Python 3.10+**，勾选 *Add Python to PATH*。
2. `pip install -r requirements.txt`。
3. 启动：

```
# 服务端（cwd = server）
python server_gui.py          # 或 python server.py 控制台版
# 教师端 / 学生端（cwd = pyclient）
python -m teacher.main
python -m student.main
```

### 打包单 exe（可选）

用 PyInstaller 按 `_build/*.spec` 规格打包，三端均为 onefile 单 exe，内嵌 Python 运行时。

## 默认账号

| 角色 | 账号 / 密码 |
|---|---|
| 教师 | `admin` / `admin123` |
| 学生 | `demo` / `demo123` |
| 学生 | `stu01` ~ `stu05` / `123456` |

登录成功返回示例：`{"code":0,"msg":"ok","role":"teacher","userId":1,"displayName":"管理员老师","classId":null}`。

## 题型与判分

| 类型 | 题型 | 判分方式 |
|---|---|---|
| 1/2/3 | 单选 / 多选 / 判断 | 服务端即时判 |
| 31/32 | Win10 设置 / Win10 网络（宽带/VPN/IPv4/打印机/邮箱） | 虚拟程序判分文件 → 判分引擎 |
| 11/12 | Win10 / 网络（旧版） | 同上 |
| 13/33/43 | Word（旧/2016/WPS） | COM 判分 |
| 14/34/44 | Excel（旧/2016/WPS） | COM 判分 |
| 15/35/45 | PPT（旧/2016/WPS） | COM 判分 |
| 17/37 | Access（旧/2016） | 自建 DAO 判分器 |
| 18 | Photoshop (PSD) | 判分引擎 + Python 兜底 |
| 24 | MySQL | 内置 MySQL 临时实例逐点检测 |
| 25 | Python | 沙箱 OJ |
| sub6 | 浏览器收藏 / 网页图片另存 | 真实 Edge 收藏 + 固定名文件检测 |
| sub2 | 文件操作 | 素材解压 + 属性恢复 + 引擎判 |

## 常见问题

1. **客户端登录提示「无法连接服务器」**：确认服务端已启动并监听 5943；学生机「服务器地址」填教师机 IP；防火墙放行 5943。
2. **Word/Excel/PPT 判分为 0 或报错**：确认装了 Office 或 WPS；判分前会自动清理残留 Office 进程。
3. **Access 题无法判分**：安装 32 位 Access Database Engine (ACE) / Access Runtime。
4. **成绩显示「判分中」**：文件题后台排队判分，数分钟后在成绩页刷新即可，不是错误。
5. **MySQL 题**：无需本机装 MySQL，内置 `runtime/mysql` 免安装 MySQL 5.7 运行时已随仓库提供，服务端自动起停临时实例判分。

## 协议

TCP 自定义协议：4 字节大端长度前缀 + UTF-8 JSON。接口文档见 `app/接口文档.md`（原项目内）。

## License

见 [LICENSE](LICENSE)。
