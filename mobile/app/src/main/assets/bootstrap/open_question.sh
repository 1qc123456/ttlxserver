#!/bin/bash
# ============================================================================
# TTLX 学生端（APK 版）· 进桌面后自动把「本题」摆出来
#
# 用法：open_question.sh <qid>
#
# 做两件事（对应真机反馈"操作题不自动打开题目文件夹和题目"）：
#   ① 用文件管理器打开该题的**考生文件夹**（素材、要保存的地方都在这）
#   ② 如果文件夹里有**题目主文档**（.docx/.xlsx/.pptx/.psd…），用对应程序打开
#      （装了 WPS 就 WPS，没装走 LibreOffice/xdg-open 关联）
#
# 都是 nohup 后台起，本脚本立刻返回，不会卡住 App。
# ============================================================================
set -u
export LANG=C.UTF-8
export HOME=/root

QID="${1:-}"
[ -n "$QID" ] || { echo "[ttlx-open] 缺少 qid"; exit 2; }

# ---- 选一个活着的 X display ----
if [ -e /tmp/.X11-unix/X0 ]; then DISPLAY=":0"
elif [ -e /tmp/.X11-unix/X1 ]; then DISPLAY=":1"
else DISPLAY=":1"; fi
export DISPLAY
for i in $(seq 1 20); do
  [ -e "/tmp/.X11-unix/X${DISPLAY#:}" ] && break
  sleep 1
done

DIR="/root/studytemp/考生文件夹/$QID"
mkdir -p "$DIR"
echo "[ttlx-open] DISPLAY=$DISPLAY 目录=$DIR"

# 等 XFCE 会话起来再开窗口：VNC 一连上时 X server 已经在了，但窗口管理器
# 可能还在启动，太早开窗口会没有标题栏/位置错乱。
for i in $(seq 1 30); do
  pgrep -f xfce4-session >/dev/null 2>&1 && break
  sleep 1
done
echo "[ttlx-open] XFCE: $(pgrep -f xfce4-session >/dev/null 2>&1 && echo 就绪 || echo 未检测到)"

# ---- ① 考生文件夹 ----
if command -v thunar >/dev/null 2>&1; then
  nohup thunar "$DIR" >/dev/null 2>&1 &
else
  nohup xdg-open "$DIR" >/dev/null 2>&1 &
fi
sleep 1

# ---- ② 题目主文档（跳过素材包、虚拟程序 exe、预览图） ----
MAIN=""
for f in "$DIR"/*; do
  [ -f "$f" ] || continue
  b="$(basename "$f")"
  case "$b" in
    material.*|*.exe|answer_sc*|.*) continue ;;
  esac
  case "${b##*.}" in
    doc|docx|wps|rtf|txt|xls|xlsx|et|csv|ppt|pptx|dps|psd|accdb|html|htm)
      MAIN="$f"; break ;;
  esac
done

if [ -n "$MAIN" ]; then
  echo "[ttlx-open] 打开题目：$MAIN"
  nohup bash /root/ttlx-pkg/open_in_container.sh "$MAIN" >/dev/null 2>&1 &
else
  echo "[ttlx-open] 没有找到题目主文档（可能还没下载素材，或本来就是文件夹/虚拟程序题）"
fi
exit 0
