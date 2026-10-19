#!/bin/bash
# ============================================================================
# TTLX 学生端（APK 版）· 办公套件安装（容器内静默执行）
#
# v3.0 重写（真机日志驱动）：
#   ① **WPS 官方 CDN 的三个老地址全部失效**（404 / 403 / 超时），
#      改用 **openkylin 镜像池**（已验证可下：SJTU / 阿里云 / 南大），
#      文件名 wps-office_11.1.0.11720_arm64.deb。
#   ② **失败原因不再吞掉**：原来 `apt-get install >/dev/null 2>&1` 把真实报错
#      全丢了，日志只剩一句"安装失败"，根本没法查。现在完整落盘。
#   ③ 多下载器兜底：wget → curl → **容器里已装的 chromium**（真机日志里
#      chromium 是装好的，用它下载能绕过部分 CDN 的 UA/重定向限制）。
#   ④ WPS 装不上时回退 **LibreOffice**（Debian 官方源，195 个包可用），
#      并把 apt 的真实报错写进日志。
# ============================================================================
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/mirrors.sh"
export DEBIAN_FRONTEND=noninteractive

WPS_LOG=/var/log/ttlx-wps.log
: > "$WPS_LOG"
LOG()  { echo "[ttlx-wps] $*" | tee -a "$WPS_LOG"; }
RAW()  { echo "[ttlx-wps] $*" >> "$WPS_LOG"; }

APT_OPTS="-o Acquire::Retries=3 -o Acquire::http::Timeout=30 \
-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"

# ---------------------------------------------------------------------------
# 0. 预装 WPS 的 postinst 依赖的命令行工具（★ 真机日志定位到的关键一步）
#
#    /var/lib/dpkg/info/wps-office.postinst: line 193: hexdump: command not found
#    dpkg: error processing package wps-office (--configure):
#      installed wps-office package post-installation script ... exit status 127
#
#    351MB 的包下载、解包、字体都成功了，**只差这一步**：postinst 要用 hexdump
#    （Debian 里属于 bsdextrautils），容器里没装 → 配置脚本 127 → 包停在
#    "not fully installed"，WPS 用不了。
# ---------------------------------------------------------------------------
ensure_wps_tools() {
  LOG "预装 WPS 安装脚本需要的工具（hexdump 等）…"
  apt-get install -y --no-install-recommends $APT_OPTS \
      bsdextrautils util-linux-extra util-linux procps file xz-utils \
      >>"$WPS_LOG" 2>&1 || true
  if command -v hexdump >/dev/null 2>&1; then
    LOG "  hexdump ✔（$(command -v hexdump)）"
    return 0
  fi
  LOG "  !! hexdump 仍缺失（WPS 的 postinst 会失败）"
  return 1
}
ensure_wps_tools

# ---------------------------------------------------------------------------
# 1. WPS Office arm64
# ---------------------------------------------------------------------------
WPS_NAME=wps-office_11.1.0.11720_arm64.deb
WPS_URLS=(
  "https://mirror.sjtu.edu.cn/openkylin/pool/pty/w/wps-office/$WPS_NAME"
  "https://mirrors.aliyun.com/openkylin/pool/pty/w/wps-office/$WPS_NAME"
  "https://mirrors.nju.edu.cn/openkylin/pool/pty/w/wps-office/$WPS_NAME"
  # 下面三个是官方 CDN（历史地址，多数已失效，留作最后尝试）
  "https://wdl1.pcfg.cache.wpscdn.cn/mweb/wps/wps-office_11.1.0.11719_arm64.deb"
  "https://wdl1.cache.wps.cn/wps/download/ep/Linux2019/11719/wps-office_11.1.0.11719_arm64.deb"
  "https://pubwps-wps365-obs.wpscdn.cn/download/Linux/21176/wps-office_12.8.2.21176.AK.preload.sw_arm64.deb"
)
DEB=/tmp/wps-office_arm64.deb

is_deb() {   # Debian 包是 ar 归档，magic = "!<arch>"
  [ -s "$1" ] && [ "$(head -c 7 "$1")" = '!<arch>' ]
}

# 复用上次下好的包：WPS 有 351MB，每次重装都重下一遍太浪费（真机日志里就是重下的）
WPS_CACHED=0
if is_deb "$DEB" && [ "$(stat -c%s "$DEB" 2>/dev/null || echo 0)" -gt 100000000 ]; then
  WPS_CACHED=1
  LOG "复用上次下载的 WPS 包（$(du -h "$DEB" 2>/dev/null | cut -f1)）"
else
  rm -f "$DEB"
fi

# --- 下载器：wget → curl → chromium（浏览器下载，最后手段） ---
try_download() {
  local u="$1"
  LOG "  ← $u"
  if command -v wget >/dev/null 2>&1; then
    wget -q -T 120 --tries=2 -O "$DEB" "$u" >>"$WPS_LOG" 2>&1 && is_deb "$DEB" && return 0
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -sL --max-time 180 --retry 2 -o "$DEB" "$u" >>"$WPS_LOG" 2>&1 && is_deb "$DEB" && return 0
  fi
  return 1
}

# chromium 兜底：headless 打开 URL 并让它下载到指定目录
# （真机日志里 chromium 已装好；部分 CDN 只认浏览器 UA/重定向链）
try_download_via_chromium() {
  local u="$1"
  local CR=""
  for c in chromium chromium-browser; do
    command -v "$c" >/dev/null 2>&1 && { CR="$c"; break; }
  done
  [ -z "$CR" ] && { LOG "  （没有 chromium，跳过浏览器下载）"; return 1; }
  local DL=/root/Downloads
  mkdir -p "$DL"
  LOG "  用 chromium 下载…"
  rm -f "$DL"/*.deb 2>/dev/null
  timeout 240 "$CR" --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
      --user-data-dir=/tmp/cr-dl --no-first-run --no-default-browser-check \
      "$u" >>"$WPS_LOG" 2>&1
  local f
  f="$(ls -1 "$DL"/*.deb 2>/dev/null | head -1)"
  if [ -n "$f" ] && is_deb "$f"; then
    mv -f "$f" "$DEB"
    return 0
  fi
  return 1
}

install_wps_deb() {
  # WPS 的 deb 是给 Ubuntu/openkylin 打的，依赖名与 Debian 不完全一致，
  # `apt-get install ./x.deb` 常因"依赖不满足"直接失败。这里三层兜底：
  #   ① 直接装  ② 先补常见依赖再装  ③ dpkg -i --force-depends + 收尾配置
  # 每一层结束后都跑一次 `dpkg --configure -a` —— 真机上失败过一次的包
  # 会停在 "not fully installed"，必须靠它把 postinst 补完。
  LOG "① 直接安装 WPS 包…"
  if apt-get install -y $APT_OPTS "$DEB" >>"$WPS_LOG" 2>&1; then
    return 0
  fi
  LOG "  失败，尾部："; tail -12 "$WPS_LOG" | sed 's/^/    | /' | tee -a "$WPS_LOG"
  dpkg --configure -a >>"$WPS_LOG" 2>&1 || true
  wps_ok && return 0

  LOG "② 补常见依赖后重试…"
  apt-get install -y --no-install-recommends $APT_OPTS \
      libcups2 libglib2.0-0 libgtk-3-0 libx11-6 libxext6 libxrender1 \
      libxdamage1 libxcomposite1 libxfixes3 libxrandr2 libxi6 libxtst6 \
      libxcb1 libxcb-render0 libxcb-shm0 libxcb-icccm4 libxcb-util1 \
      libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libatk1.0-0 \
      libgdk-pixbuf-2.0-0 libasound2 libnspr4 libnss3 libdbus-1-3 \
      libxkbcommon0 libxslt1.1 fonts-wqy-microhei \
      >>"$WPS_LOG" 2>&1 || true
  if apt-get install -y $APT_OPTS "$DEB" >>"$WPS_LOG" 2>&1; then
    return 0
  fi
  dpkg --configure -a >>"$WPS_LOG" 2>&1 || true
  wps_ok && return 0

  LOG "③ dpkg --force-depends 强装 + 收尾配置…"
  dpkg -i --force-depends "$DEB" >>"$WPS_LOG" 2>&1 || true
  dpkg --configure -a >>"$WPS_LOG" 2>&1 || true
  apt-get -f install -y $APT_OPTS >>"$WPS_LOG" 2>&1 || true
  wps_ok
}

# WPS 是否真的可执行（postinst 跑完才会有 /usr/bin/wps 这些入口）
wps_ok() {
  for b in wps et wpp; do
    command -v "$b" >/dev/null 2>&1 && return 0
  done
  for p in /usr/bin/wps /opt/kingsoft/wps-office/office6/wps; do
    [ -x "$p" ] && return 0
  done
  return 1
}

LOG "尝试下载 WPS Office arm64（openkylin 镜像优先）…"
OK=$WPS_CACHED
if [ "$OK" = 0 ]; then
  for u in "${WPS_URLS[@]}"; do
    if try_download "$u"; then OK=1; break; fi
  done
fi
if [ "$OK" = 0 ]; then
  LOG "常规下载都失败，改用 chromium 兜底…"
  for u in "${WPS_URLS[@]:0:3}"; do
    if try_download_via_chromium "$u"; then OK=1; break; fi
  done
fi

if [ "$OK" = 1 ]; then
  [ "$WPS_CACHED" = 1 ] || LOG "下载成功（$(du -h "$DEB" 2>/dev/null | cut -f1)）"
  if install_wps_deb; then
    LOG "WPS Office 安装完成 ✔"
    apt-get install -y fonts-wqy-microhei >>"$WPS_LOG" 2>&1 || true
    # WPS 的可执行文件在 /usr/bin/{wps,et,wpp}（有些版本是 wps-office-*）
    for b in wps et wpp wps-office; do
      command -v "$b" >/dev/null 2>&1 && LOG "  可执行：$(command -v "$b")"
    done
    rm -f "$DEB"          # 装好了就把 351MB 的包删掉，别占着 App 存储
    exit 0
  fi
  LOG "!! WPS 安装失败，apt 输出尾部："
  tail -25 "$WPS_LOG" | sed 's/^/    | /'
else
  LOG "WPS 下载失败（所有地址 + 浏览器兜底都不行）"
fi

# ---------------------------------------------------------------------------
# 2. 回退 LibreOffice（Debian 官方源，最稳）
# ---------------------------------------------------------------------------
LOG "回退安装 LibreOffice（Debian 源）…"
if apt-get install -y --no-install-recommends $APT_OPTS \
      libreoffice-writer libreoffice-calc libreoffice-impress \
      libreoffice-l10n-zh-cn fonts-wqy-microhei >>"$WPS_LOG" 2>&1; then
  LOG "LibreOffice 安装完成 ✔（soffice：$(command -v soffice || echo 未找到)）"
  exit 0
fi

LOG "!! LibreOffice 也失败了，真实报错："
tail -25 "$WPS_LOG" | sed 's/^/    | /'
exit 1
