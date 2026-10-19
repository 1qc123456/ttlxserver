#!/bin/bash
# 在容器桌面里打开文件 / 文件夹（由手机端「在容器中打开」按钮调用）
# 用法: DISPLAY=:1 bash open_in_container.sh "/root/studytemp/考生文件夹/<题号>/文件.docx"
P="$1"
[ -e "$P" ] || { echo "[ttlx] 文件不存在: $P"; exit 2; }
export DISPLAY="${DISPLAY:-:1}"

# 文件夹 → 文件管理器
if [ -d "$P" ]; then
  exec thunar "$P" 2>/dev/null || exec xdg-open "$P"
fi

# Office 文档 → WPS 对应组件（装的是 LibreOffice 时自动走 xdg-open 的关联）
EXT="$(echo "${P##*.}" | tr 'A-Z' 'a-z')"
case "$EXT" in
  doc|docx|wps|rtf|txt)  APP="wps" ;;
  xls|xlsx|et|csv)       APP="et"  ;;
  ppt|pptx|dps)          APP="wpp" ;;
  *)                     APP=""    ;;
esac
if [ -n "$APP" ] && command -v "$APP" >/dev/null 2>&1; then
  exec "$APP" "$P"
fi
# 网页 / 其它 → 默认程序（浏览器等）
exec xdg-open "$P"
