#!/bin/bash
# ============================================================================
# TTLX 学生端（APK 版）· 容器启动脚本（每次打开桌面都跑）
#
# v1.9 起改为「两条路」：
#   主路（推荐）：App 侧用 Termux:X11 起原生 X server，socket 在 /tmp/.X11-unix/X0
#                （App 的 TMPDIR 指向 rootfs/tmp，所以容器里就是 /tmp）。
#                本脚本只负责在 DISPLAY=:0 上拉起 XFCE —— **没有 VNC、没有 TCP**。
#   备路（回退）：App 侧 X server 没起来时，本脚本自己起 Xvnc(:1) + noVNC，
#                App 会改用内置 VNC 客户端连 5901。
#
# 诊断文件：/var/log/ttlx-desktop-diagnosis.log（手机侧「诊 断」按钮可直接读）
# ============================================================================
export LANG=C.UTF-8
export HOME=/root
export TMPDIR=/tmp
export XKB_CONFIG_ROOT=/usr/share/X11/xkb
export XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
mkdir -p /var/log

VNC_SOCK=/tmp/.ttlx.vnc
DIAG=/var/log/ttlx-desktop-diagnosis.log
: > "$DIAG"
log() { echo "[ttlx] $*" | tee -a "$DIAG"; }

port_up() { timeout 1 bash -c "echo >/dev/tcp/127.0.0.1/$1" 2>/dev/null; }

# ---------------------------------------------------------------------------
# 0. 环境体检
# ---------------------------------------------------------------------------
log "== 环境体检 =="
log "uname: $(uname -m)  |  /tmp 可用: $(df -h /tmp 2>/dev/null | tail -1 | awk '{print $4}')"
log "XKB_CONFIG_ROOT=$XKB_CONFIG_ROOT 存在: $([ -d "$XKB_CONFIG_ROOT" ] && echo 是 || echo 否)"
for b in Xvnc Xvfb x11vnc websockify startxfce4 xfce4-session; do
  p="$(command -v $b 2>/dev/null)"
  log "  $b : ${p:-（未安装）}"
done

# ---------------------------------------------------------------------------
# 1. 等 App 侧 Termux:X11 的 X server（首选路径）
#    App 把 TMPDIR 设成 rootfs/tmp，所以容器里看到的就是 /tmp/.X11-unix/X0
# ---------------------------------------------------------------------------
log "等待 App 侧 X server（/tmp/.X11-unix/X0，最多 15 秒）…"
have_x0=0
for i in $(seq 1 15); do
  if [ -e /tmp/.X11-unix/X0 ]; then have_x0=1; break; fi
  sleep 1
done
log "X0 socket: $([ "$have_x0" = 1 ] && echo 有 ✔ || echo 无)"
log "当前 X11 socket 列表: $(ls -l /tmp/.X11-unix 2>/dev/null | tr '\n' ' ')"

# ---------------------------------------------------------------------------
# 2. 起 XFCE 桌面
# ---------------------------------------------------------------------------
start_desktop() {   # $1 = DISPLAY 号
  export DISPLAY=":$1"
  log "在 DISPLAY=$DISPLAY 上启动 XFCE…"
  if command -v dbus-launch >/dev/null 2>&1; then
    eval "$(dbus-launch --sh-syntax 2>/dev/null)" || true
  fi
  startxfce4 >/var/log/xfce.log 2>&1 &
  sleep 4
  if pgrep -f xfce4-session >/dev/null; then
    log "XFCE 会话已启动 ✔"
  else
    log "!! XFCE 会话未检测到，诊断："
    tail -20 /var/log/xfce.log >> "$DIAG" 2>/dev/null
    if command -v xfwm4 >/dev/null 2>&1; then
      xfwm4 >/var/log/xfwm4.log 2>&1 &
      log "已单独拉起 xfwm4 作为兜底窗口管理器"
    fi
  fi
}

if [ "$have_x0" = 1 ]; then
  log "=== 走主路：Termux:X11 原生 X server ==="
  start_desktop 0
  log "就绪（主路）：DISPLAY=:0，无 VNC / 无 TCP"
  while true; do sleep 3600; done
fi

# ---------------------------------------------------------------------------
# 3. 备路：App 侧 X server 没起来 → 自己起 Xvnc
# ---------------------------------------------------------------------------
log "=== 回退备路：自建 Xvnc ==="
pkill -f "Xvnc :1" 2>/dev/null; pkill -f "Xtigervnc :1" 2>/dev/null
pkill -f "websockify" 2>/dev/null
rm -f /tmp/.X11-unix/X1 /tmp/.X1-lock "$VNC_SOCK" 2>/dev/null
sleep 1

XVNC_BIN="$(command -v Xvnc || command -v Xtigervnc || true)"
started=0
wait_ready() {
  for i in $(seq 1 "$2"); do
    sleep 1
    port_up 5901 && return 0
    [ -S "$VNC_SOCK" ] && return 0
  done
  log "!! $1：${2}s 内既没监听 5901 也没有 unix socket。Xvnc 输出尾部："
  tail -25 /var/log/xvnc.log >> "$DIAG" 2>/dev/null
  return 1
}

if [ -n "$XVNC_BIN" ]; then
  log "Xvnc: $XVNC_BIN  |  $("$XVNC_BIN" -version 2>&1 | head -1)"
  miss="$("$XVNC_BIN" --help >/dev/null 2>&1; ldd "$XVNC_BIN" 2>/dev/null | grep 'not found' | head -8)"
  [ -n "$miss" ] && log "!! Xvnc 缺库: $miss" || log "Xvnc 依赖库完整"

  log "第1轮：unix socket + TCP…"
  "$XVNC_BIN" :1 -geometry 1280x800 -depth 24 \
       -SecurityTypes None -localhost=1 -rfbport 5901 -rfbunixpath "$VNC_SOCK" \
       >/var/log/xvnc.log 2>&1 &
  wait_ready "第1轮" 12 && started=1

  if [ "$started" = 0 ]; then
    pkill -f "Xvnc :1" 2>/dev/null; pkill -f "Xtigervnc :1" 2>/dev/null
    rm -f /tmp/.X11-unix/X1 /tmp/.X1-lock "$VNC_SOCK" 2>/dev/null; sleep 1
    log "第2轮：只 TCP（老版本不认 -rfbunixpath）…"
    "$XVNC_BIN" :1 -geometry 1280x800 -depth 24 \
         -SecurityTypes None -localhost=1 -rfbport 5901 \
         >/var/log/xvnc.log 2>&1 &
    wait_ready "第2轮" 12 && started=1
  fi

  if [ "$started" = 0 ]; then
    pkill -f "Xvnc :1" 2>/dev/null; pkill -f "Xtigervnc :1" 2>/dev/null
    rm -f /tmp/.X11-unix/X1 /tmp/.X1-lock "$VNC_SOCK" 2>/dev/null; sleep 1
    log "第3轮：最保守参数（去 -localhost）…"
    "$XVNC_BIN" :1 -rfbport 5901 -geometry 1024x768 -depth 24 >/var/log/xvnc.log 2>&1 &
    wait_ready "第3轮" 12 && started=1
  fi
fi

# 第 4 轮：Xvfb + x11vnc
if [ "$started" = 0 ] && command -v Xvfb >/dev/null 2>&1 && command -v x11vnc >/dev/null 2>&1; then
  pkill -f "Xvnc :1" 2>/dev/null; pkill -f "Xtigervnc :1" 2>/dev/null
  rm -f /tmp/.X11-unix/X1 /tmp/.X1-lock "$VNC_SOCK" 2>/dev/null; sleep 1
  log "第4轮（兜底）：Xvfb + x11vnc…"
  Xvfb :1 -screen 0 1280x800x24 -nolisten tcp >/var/log/xvfb.log 2>&1 &
  for i in $(seq 1 10); do sleep 1; [ -e /tmp/.X11-unix/X1 ] && break; done
  if [ -e /tmp/.X11-unix/X1 ]; then
    x11vnc -display :1 -rfbport 5901 -unixsock "$VNC_SOCK" -nopw -forever -shared -noxdamage -quiet \
           >/var/log/x11vnc.log 2>&1 &
    wait_ready "第4轮(x11vnc)" 12 && started=1
  else
    log "!! Xvfb 也没起来"
    tail -20 /var/log/xvfb.log >> "$DIAG" 2>/dev/null
  fi
fi

if [ "$started" = 1 ]; then
  if port_up 5901; then tcpmsg=通; else tcpmsg=不通; fi
  if [ -S "$VNC_SOCK" ]; then sockmsg=有; else sockmsg=无; fi
  log "Xvnc 就绪 ✔  TCP:5901=$tcpmsg  unix:$VNC_SOCK=$sockmsg"
  if command -v websockify >/dev/null 2>&1; then
    log "启动 noVNC 入口 :6080…"
    websockify --web=/usr/share/novnc 6080 127.0.0.1:5901 >/var/log/websockify.log 2>&1 &
    for i in 1 2 3 4 5; do sleep 1; port_up 6080 && break; done
    port_up 6080 && log "noVNC 就绪 ✔" || log "!! noVNC 未就绪"
  fi
  start_desktop 1
  log "就绪（备路）：DISPLAY=:1，VNC 127.0.0.1:5901 / $VNC_SOCK"
else
  log "!! 两条路都失败：App 侧 X server 没起来，自建 Xvnc 也失败 → 桌面无法显示。"
fi

while true; do
  sleep 3600
done
