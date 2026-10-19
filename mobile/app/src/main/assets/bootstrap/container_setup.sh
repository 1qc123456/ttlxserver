#!/bin/bash
# ============================================================================
# TTLX 学生端（APK 版）· 容器首次初始化
# 由 APK 首启自动调用（proot 容器内 root 身份），无需任何人工操作。
# 顺序：dpkg 自愈 → DNS → 换源 → 系统依赖 → 桌面/VNC → Chromium → WPS → 自检 → 标记
#
# ---- v2.2 关键修复（真机日志驱动） ----
#   ① **dpkg 中断自愈**（本次真机报错的直接原因）
#      初始化中途被打断（重启 App / 系统杀进程 / 息屏断网）会在 dpkg 留下未完成
#      记录，此后**每一次** apt 操作都报：
#        E: dpkg was interrupted, you must manually run 'dpkg --configure -a'
#      旧版只会原地重试 3 次、全部失败 → 桌面组件/Chromium/WPS 全都装不上。
#      现在：装之前先自检并 `dpkg --configure -a`；安装失败时检测到该错误会
#      **自动修复并立刻重试**。用户永远不需要手敲命令。
#   ② **apt 失败原因分流**：dpkg 中断 / apt 锁 / DNS 解析失败，各自有对应处理，
#      不再一律"重试 3 次然后放弃"。
#   ③ `/etc/resolv.conf` 常是**悬空符号链接**，直接 `>` 写入会报
#      `No such file or directory`（真机日志里就是这样）→ 先 rm -f 再写普通文件。
#   ④ **日志分层**：apt 原始输出只进 $APT_LOG，失败时摘最后 20 行；
#      不再把 setup.log 反复摘回自己（原来日志层层套娃，真正原因被淹没）。
#   ⑤ apt 加 `Acquire::Retries` + 超时 + `--force-confold`，弱网/无人值守更稳。
#
# ---- v2.1 修复（保留） ----
#   · 只有确认 X server 与 XFCE 都装上才写 /root/.ttlx_ready（不再"假就绪"）
#   · 同时改 debian.sources 与 sources.list；DNS 每次重写
#   · 全过程写 /var/log/ttlx-setup.log（手机「诊 断」/「分享日志」直接读）
# ============================================================================
set -u
export DEBIAN_FRONTEND=noninteractive
export LANG=C.UTF-8

SETUP_LOG=/var/log/ttlx-setup.log
APT_LOG=/var/log/ttlx-apt.log          # apt 原始输出（单独一个文件，避免日志套娃）
: > "$SETUP_LOG"
: > "$APT_LOG"
LOG() { echo "[ttlx-setup] $*" | tee -a "$SETUP_LOG"; }
# 把 apt 输出的最后 n 行同时写进 setup.log 与 stdout（tee 两头都到）
LOGTAIL() { tail -n "$1" "$APT_LOG" 2>/dev/null | sed 's/^/    | /' | tee -a "$SETUP_LOG"; }

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/mirrors.sh"

APT_OPTS="-o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 \
-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"

write_dns() {
  mkdir -p /etc
  rm -f /etc/resolv.conf                 # 悬空符号链接会导致重定向直接失败
  printf "nameserver 223.5.5.5\nnameserver 119.29.29.29\nnameserver 114.114.114.114\n" \
    > /etc/resolv.conf 2>/dev/null || true
}

# ---------------- 0. DNS + 目录 ----------------
mkdir -p /tmp/.X11-unix /var/log
chmod 1777 /tmp/.X11-unix
write_dns
LOG "DNS: $(tr '\n' ' ' < /etc/resolv.conf 2>/dev/null)"

# ---------------- 0.5 dpkg 中断自愈（v2.2 新增） ----------------
# 幂等且很快。没坏也跑一遍，代价极小；坏了就是救命的一步。
repair_dpkg() {
  if ls /var/lib/dpkg/updates/* >/dev/null 2>&1; then
    LOG "!! 检测到 dpkg 有未完成记录（上次初始化被打断过）→ 执行 dpkg --configure -a 修复"
  else
    LOG "  dpkg 状态自检（--configure -a 兜底）…"
  fi
  dpkg --configure -a >>"$APT_LOG" 2>&1 || true
  apt-get -f install -y $APT_OPTS >>"$APT_LOG" 2>&1 || true
  LOG "  dpkg 修复完成 ✔"
}
repair_dpkg

# ---------------- 1. Debian 镜像择优 + 换源 ----------------
CODENAME="$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-bookworm}")"
LOG "发行版代号：$CODENAME"

write_sources() {   # $1=主镜像  $2=安全镜像
  if [ -f /etc/apt/sources.list.d/debian.sources ]; then
    cat > /etc/apt/sources.list.d/debian.sources <<EOF
Types: deb
URIs: $1
Suites: $CODENAME $CODENAME-updates
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: $2
Suites: $CODENAME-security
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
  fi
  # 两个文件都写：不同 rootfs 用的是不同形式，只改一个可能完全不生效
  cat > /etc/apt/sources.list <<EOF
deb $1 $CODENAME main contrib non-free non-free-firmware
deb $1 $CODENAME-updates main contrib non-free non-free-firmware
deb $2 $CODENAME-security main contrib non-free non-free-firmware
EOF
  LOG "已写入 apt 源：$1"
}

LOG "探测镜像站…"
DEB_MIRROR="$(ttlx_pick_deb_mirror || true)"
[ -n "${DEB_MIRROR:-}" ] || DEB_MIRROR="https://mirrors.tuna.tsinghua.edu.cn/debian"
LOG "首选镜像：$DEB_MIRROR"
write_sources "$DEB_MIRROR" "${DEB_SEC:-https://mirrors.tuna.tsinghua.edu.cn/debian-security}"

apt_update() {
  local try
  for try in 1 2 3; do
    LOG "apt update（第 $try 次）…"
    : > "$APT_LOG"
    if apt-get update -y $APT_OPTS >>"$APT_LOG" 2>&1; then
      LOG "apt update 成功 ✔"
      return 0
    fi
    LOG "!! apt update 失败（第 $try 次）"
    if grep -q "dpkg was interrupted" "$APT_LOG" 2>/dev/null; then
      LOG "   原因是 dpkg 被中断 → 自动修复后重试"
      repair_dpkg
    fi
    sleep 2
  done
  return 1
}

if ! apt_update; then
  LOG "首选镜像不行，逐个换镜像重试…"
  for m in "${DEB_MIRROR_BASES[@]}"; do
    host="${m#https://}"; host="${host%%/*}"
    ttlx_tcp_ok "$host" || { LOG "  跳过（连不上）: $host"; continue; }
    write_sources "$m/debian" "$m/debian-security"
    apt_update && break
  done
fi

if ! apt-get update -y $APT_OPTS >>"$APT_LOG" 2>&1; then
  LOG "!! apt 源仍然不可用 —— 后续安装都会失败"
fi

# ---------------- apt 安装（v2.2：按错误原因分流处理） ----------------
apt_install() {   # $1=说明，后面是包名
  local what="$1"; shift
  local try
  for try in 1 2 3; do
    LOG "安装 $what（第 $try 次）：$*"
    : > "$APT_LOG"
    if apt-get install -y --no-install-recommends $APT_OPTS "$@" >>"$APT_LOG" 2>&1; then
      LOG "  $what 安装成功 ✔"
      return 0
    fi

    # —— 失败原因分流（v2.2 核心）——
    if grep -q "dpkg was interrupted" "$APT_LOG" 2>/dev/null; then
      LOG "  !! dpkg 被中断（上次初始化没跑完）→ 自动修复后立即重试"
      repair_dpkg
      continue
    fi
    if grep -qE "Could not get lock|Unable to acquire the dpkg frontend lock|is another process" "$APT_LOG" 2>/dev/null; then
      LOG "  !! 另一个 apt 进程还在跑，等 10 秒后重试"
      sleep 10
      continue
    fi
    if grep -qE "Temporary failure resolving|Could not resolve|Temporary failure in name resolution" "$APT_LOG" 2>/dev/null; then
      LOG "  !! DNS 解析失败 → 重写 resolv.conf 后重试"
      write_dns
      sleep 2
      continue
    fi

    LOG "  !! $what 第 $try 次失败，apt 输出尾部："
    LOGTAIL 20
    apt-get -f install -y $APT_OPTS >>"$APT_LOG" 2>&1 || true
    sleep 2
  done
  LOG "  !! $what 三次都失败 ✗"
  return 1
}

# ---------------- 2. 系统依赖（含 Xvnc 需要的 xkb 组件） ----------------
apt_install "系统依赖" \
  ca-certificates wget curl unzip zip xz-utils file procps \
  bsdextrautils util-linux \
  fonts-noto-cjk fonts-wqy-microhei \
  dbus-x11 xdg-utils \
  x11-xkb-utils xkb-data \
  libgl1 libegl1 libfontconfig1 libfreetype6 \
  libx11-xcb1 libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
  libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
  libxcb-sync1 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1 \
  libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 libglib2.0-0 \
  || LOG "!! 部分系统依赖安装失败（继续尝试桌面部分）"

# ---------------- 3. 桌面 + VNC（最关键） ----------------
DESKTOP_OK=0
if apt_install "XFCE 桌面与 TigerVNC/noVNC" \
     xfce4 xfce4-terminal thunar \
     tigervnc-standalone-server novnc websockify; then
  DESKTOP_OK=1
fi

# 兜底显示方案：个别设备 Xvnc 起不来，Xvfb + x11vnc 通常没问题
apt_install "兜底显示组件(Xvfb+x11vnc)" xvfb x11vnc || true

# ---------------- 4. 【必装】Chromium（网页题） ----------------
if apt_install "Chromium" chromium || apt_install "Chromium(备用包名)" chromium-browser; then
  LOG "Chromium 就绪 ✔（$(command -v chromium || command -v chromium-browser)）"
else
  LOG "!! Chromium 安装失败（网页题可能不可用）"
fi

# ---------------- 5. 【必装】WPS Office ----------------
LOG "安装 WPS Office（多地址轮询，包约 351MB，可能要十几分钟）…"
# 心跳：WPS 安装期间脚本自身几乎不往 stdout 输出（详细输出都写进了它自己的日志），
# App 侧的"停滞看门狗"会误判卡死。这里每 30 秒吐一行，既报进度又保活。
( while true; do sleep 30; echo "[ttlx-setup] …WPS 安装中（$(date +%H:%M:%S)，详见 /var/log/ttlx-wps.log）" | tee -a "$SETUP_LOG"; done ) &
HB=$!
WPSRC=1
bash "$HERE/install_wps_quiet.sh" >>"$SETUP_LOG" 2>&1 && WPSRC=0
kill "$HB" 2>/dev/null
wait "$HB" 2>/dev/null
if [ "$WPSRC" = 0 ]; then LOG "WPS 安装完成 ✔"; else LOG "!! WPS 安装失败（Office 题会不可用，详见 ttlx-wps.log）"; fi

# 收尾：如果某个包的 postinst 失败过，dpkg 会停在 "not fully installed"
# （dpkg -l 里状态显示成 iF / iU / iH），之后**任何** apt 操作都会被它挡住。
# 这里补一次配置（幂等）。
if dpkg -l 2>/dev/null | grep -qE '^i[FUH] '; then
  LOG "检测到未完成配置的包（iF/iU/iH）→ dpkg --configure -a 收尾…"
  dpkg --configure -a >>"$SETUP_LOG" 2>&1 || true
  dpkg -l 2>/dev/null | grep -E '^i[FUH] ' | awk '{print "    仍未完成: "$2}' | tee -a "$SETUP_LOG"
fi

# ---------------- 5.5 虚拟程序运行时 mono（Win10/Edge/邮件 题） ----------------
# 这三个题型的作答程序是 .NET WinForms exe；Debian arm64 有 mono 可以跑。
# 装不上不算致命（只影响这几类题），所以不阻断就绪判定。
MONO_OK=0
if apt_install "虚拟程序运行时 mono" \
     mono-runtime libmono-system-windows-forms4.0-cil \
     libmono-system-drawing4.0-cil libgdiplus; then
  MONO_OK=1
fi

# ---------------- 6. 考生文件夹 / 虚拟程序 / 桌面图标 ----------------
mkdir -p /root/studytemp/考生文件夹
mkdir -p /root/ttlx-student/vapp

# 虚拟程序 exe：zip 与散装文件都收（双保险，任一路径可用即可）
if [ -f /root/ttlx-pkg/vapp/vapp_exes.zip ]; then
  unzip -oq /root/ttlx-pkg/vapp/vapp_exes.zip -d /root/ttlx-student/vapp || true
fi
for f in /root/ttlx-pkg/vapp/*.exe; do
  [ -f "$f" ] && cp -f "$f" /root/ttlx-student/vapp/
done
chmod +x /root/ttlx-student/vapp/*.exe 2>/dev/null || true
LOG "虚拟程序："
for e in Win10.exe Edge.exe Win10_Email.exe; do
  if [ -f "/root/ttlx-student/vapp/$e" ]; then
    LOG "  $e ✔ ($(du -h "/root/ttlx-student/vapp/$e" | cut -f1))"
  else
    LOG "  $e ✗ 缺失（该类题无法作答）"
  fi
done
LOG "  mono: $(command -v mono || echo '未安装（虚拟程序无法运行）')"

mkdir -p /root/Desktop
cat > /root/Desktop/考生文件夹.desktop <<'EOS'
[Desktop Entry]
Type=Application
Name=考生文件夹
Comment=操作题作答目录
Exec=thunar /root/studytemp/考生文件夹
Icon=Folder
Terminal=false
EOS
chmod +x /root/Desktop/考生文件夹.desktop

# ---------------- 7. Xvnc 启动配置 ----------------
mkdir -p /root/.vnc
cat > /root/.vnc/xstartup <<'EOS'
#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
exec startxfce4
EOS
chmod +x /root/.vnc/xstartup

# ---------------- 8. 组件自检 ----------------
LOG "组件自检："
have() { command -v "$1" >/dev/null 2>&1; }
X_OK=0; XFCE_OK=0
if have Xvnc;      then LOG "  Xvnc       ✔"; X_OK=1; else LOG "  Xvnc       ✗ 缺失！"; fi
if have Xvfb;      then LOG "  Xvfb(兜底) ✔"; X_OK=1; else LOG "  Xvfb       ✗"; fi
if have x11vnc;    then LOG "  x11vnc(兜底) ✔"; else LOG "  x11vnc     ✗"; fi
if have startxfce4; then LOG "  XFCE       ✔"; XFCE_OK=1; else LOG "  XFCE       ✗ 缺失！"; fi
have websockify  && LOG "  websockify ✔" || LOG "  websockify ✗"
have thunar      && LOG "  文件管理   ✔" || LOG "  文件管理   ✗"
have mono        && LOG "  虚拟程序   ✔ (mono)" || LOG "  虚拟程序   ✗ 无 mono"
have chromium    && LOG "  Chromium   ✔" || { have chromium-browser && LOG "  Chromium   ✔" || LOG "  Chromium   ✗"; }
have wps         && LOG "  WPS        ✔" || { have et && LOG "  WPS(et)    ✔" || LOG "  WPS        ✗"; }

# ---------------- 9. 完成标记（**必须真的装上了才写**） ----------------
if [ "$X_OK" = 1 ] && [ "$XFCE_OK" = 1 ]; then
  touch /root/.ttlx_ready
  LOG "初始化完成 ✔（X server + XFCE 均已就位）"
  exit 0
fi

LOG "=========================================================="
LOG "!! 初始化未完成：X server 或 XFCE 缺失，**不写就绪标记**。"
LOG "   这样 App 会保持「环境未就绪」，可以再次点重试/重新初始化。"
LOG "   上面 [ttlx-setup] 与 apt 的完整输出见本文件。"
LOG "=========================================================="
exit 3
