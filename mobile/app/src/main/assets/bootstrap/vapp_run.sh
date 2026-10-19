#!/bin/bash
# ============================================================================
# TTLX 学生端（APK 版）· 启动「虚拟程序」操作题（Win10 设置 / Edge / 邮件）
#
# 这三个题型的作答程序是 **.NET Framework WinForms exe**
# （Win10.exe / Edge.exe / Win10_Email.exe），Windows 上直接双击运行。
# 手机容器里没有 Windows，但 Debian arm64 有 **mono**（.NET 兼容运行时，自带
# System.Windows.Forms 的 X11 实现），所以用 `mono xxx.exe` 跑，窗口渲染在容器桌面上。
#
# 用法：vapp_run.sh <qid> <vapp>        vapp ∈ win10 | edge | email
#
# 关键点：**exe 要在「考生文件夹/<qid>/」里、以该目录为工作目录运行** ——
#   它会把操作记录（判分文件，名字分别是 Win10 / Edge / Win10_Email）
#   写在工作目录里。App 交卷时读这个文件并以 vapp_log 上传判分。
#
# v3.1：每一步都写进 /var/log/ttlx-vapp.log，失败时能直接看出卡在哪。
# ============================================================================
set -u
export LANG=C.UTF-8
export HOME=/root
export MONO_WINFORMS_XIM_STYLE=disabled      # 避免 X11 输入法相关崩溃

VAPP_LOG=/var/log/ttlx-vapp.log
INSTALL_LOG=/var/log/ttlx-vapp-install.log
: > "$VAPP_LOG"
LOG() { echo "[ttlx-vapp] $*" | tee -a "$VAPP_LOG"; }

# ---- 选一个真正活着的 X server：优先 App 侧 Termux:X11(:0)，否则容器自建 Xvnc(:1) ----
pick_display() {
  if [ -e /tmp/.X11-unix/X0 ]; then echo ":0"; return; fi
  if [ -e /tmp/.X11-unix/X1 ]; then echo ":1"; return; fi
  echo ":1"
}
DISPLAY="$(pick_display)"
export DISPLAY
for i in $(seq 1 30); do
  [ -e "/tmp/.X11-unix/X${DISPLAY#:}" ] && break
  sleep 1
done
LOG "DISPLAY=$DISPLAY  socket列表: $(ls /tmp/.X11-unix 2>/dev/null | tr '\n' ' ')"

QID="${1:-}"
VAPP="${2:-}"
[ -n "$QID" ] || { LOG "缺少 qid"; exit 2; }

case "$VAPP" in
  win10) EXE=Win10.exe ;;
  edge)  EXE=Edge.exe ;;
  email) EXE=Win10_Email.exe ;;
  *)     LOG "未知虚拟程序类型：$VAPP"; exit 2 ;;
esac

VAPP_DIR=/root/ttlx-student/vapp
WORK="/root/studytemp/考生文件夹/$QID"
mkdir -p "$WORK"

SRC="$VAPP_DIR/$EXE"
if [ ! -f "$SRC" ]; then
  LOG "!! 找不到 $SRC（APK 里没带上这个虚拟程序）"
  ls -l "$VAPP_DIR" 2>&1 | head -20 | tee -a "$VAPP_LOG"
  exit 3
fi
cp -f "$SRC" "$WORK/$EXE"
LOG "exe 就位：$WORK/$EXE"

# ---- 确保 mono 就绪（容器是旧版本装的时可能没装）----
if ! command -v mono >/dev/null 2>&1; then
  LOG "mono 未安装，开始安装（首次约 1~3 分钟）…"
  export DEBIAN_FRONTEND=noninteractive
  : > "$INSTALL_LOG"
  # ⚠ 先修 dpkg：真机日志里 mono 安装直接失败在
  #   `E: dpkg was interrupted, you must manually run 'dpkg --configure -a'`
  #   —— 那是之前某个包（WPS）的 postinst 失败留下的烂摊子，
  #   不修的话后面**所有** apt 操作都会被挡住。
  LOG "先检查并修复 dpkg 状态…"
  dpkg --configure -a >>"$INSTALL_LOG" 2>&1 || true
  apt-get -f install -y >>"$INSTALL_LOG" 2>&1 || true

  apt-get update -y >>"$INSTALL_LOG" 2>&1 || true
  apt-get install -y --no-install-recommends \
      mono-runtime libmono-system-windows-forms4.0-cil \
      libmono-system-drawing4.0-cil libgdiplus \
      >>"$INSTALL_LOG" 2>&1
  rc=$?
  if [ "$rc" != 0 ]; then
    LOG "mono 第一次安装失败（退出码=$rc），修复 dpkg 后重试一次…"
    dpkg --configure -a >>"$INSTALL_LOG" 2>&1 || true
    apt-get -f install -y >>"$INSTALL_LOG" 2>&1 || true
    apt-get install -y --no-install-recommends \
        mono-runtime libmono-system-windows-forms4.0-cil \
        libmono-system-drawing4.0-cil libgdiplus \
        >>"$INSTALL_LOG" 2>&1
    rc=$?
  fi
  LOG "mono 安装退出码=$rc"
  if [ "$rc" != 0 ]; then
    LOG "!! mono 安装失败，apt 输出尾部："
    tail -20 "$INSTALL_LOG" | sed 's/^/    | /' | tee -a "$VAPP_LOG"
  fi
fi
if ! command -v mono >/dev/null 2>&1; then
  LOG "!! 仍然没有 mono —— 无法运行虚拟程序"
  LOG "   （可点桌面菜单「修 复」重跑一遍容器初始化）"
  exit 4
fi
LOG "mono: $(command -v mono) | $(mono --version 2>&1 | head -1)"

# ---- 杀掉同一题的旧实例，避免多个窗口叠着 ----
pkill -f "$WORK/$EXE" 2>/dev/null || true

cd "$WORK" || exit 5
LOG "启动：mono $WORK/$EXE（工作目录 $WORK）"
nohup mono "$WORK/$EXE" >>"$VAPP_LOG" 2>&1 &
sleep 5
if pgrep -f "$WORK/$EXE" >/dev/null 2>&1; then
  LOG "已启动 ✔（判分文件会生成在 $WORK/）"
  exit 0
fi
LOG "!! 启动后进程不在了 —— mono 输出如下："
tail -40 "$VAPP_LOG" | sed 's/^/    | /'
exit 6
