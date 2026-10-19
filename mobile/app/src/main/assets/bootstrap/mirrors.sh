#!/bin/bash
# ============================================================================
# 手机版学生端 · 镜像站自动择优（被其它脚本 source）
#
# 按用户要求：不用死一个源——把所有常见国内镜像都列上，逐个探测，
# 选用第一个连得上的；apt / pip 都带故障转移。
# 探测只用 bash 内建 /dev/tcp，不依赖 curl/wget（容器里可能还没装）。
# ============================================================================

# ---- Debian 镜像候选（会自动拼 /debian 与 /debian-security）------------------
DEB_MIRROR_BASES=(
  "https://mirrors.tuna.tsinghua.edu.cn"     # 清华 TUNA
  "https://mirrors.aliyun.com"               # 阿里云
  "https://mirrors.ustc.edu.cn"              # 中科大
  "https://mirrors.cloud.tencent.com"        # 腾讯云
  "https://mirrors.huaweicloud.com"          # 华为云
  "https://mirrors.163.com"                  # 网易
  "https://mirrors.bfsu.edu.cn"              # 北外
  "https://mirror.nju.edu.cn"                # 南京大学
)

# ---- PyPI 镜像候选 -----------------------------------------------------------
PYPI_MIRRORS=(
  "https://pypi.tuna.tsinghua.edu.cn/simple"          # 清华
  "https://mirrors.aliyun.com/pypi/simple/"           # 阿里云
  "https://mirrors.cloud.tencent.com/pypi/simple/"    # 腾讯云
  "https://pypi.mirrors.ustc.edu.cn/simple/"          # 中科大
  "https://repo.huaweicloud.com/repository/pypi/simple/"  # 华为云
  "https://mirrors.bfsu.edu.cn/pypi/web/simple/"      # 北外
)

# ---- GitHub 加速代理（下载 box64/Hangover 之类用）-----------------------------
GH_PROXIES=(
  "https://ghproxy.net/"
  "https://gh-proxy.com/"
  "https://ghfast.top/"
  ""                                                  # 直连兜底
)

ttlx_tcp_ok() {
  # $1=host  $2=port(默认443)  仅用 bash 内建测试，不依赖 curl/wget
  local host="$1" port="${2:-443}"
  timeout 6 bash -c "echo >/dev/tcp/$host/$port" 2>/dev/null
}

ttlx_pick_deb_mirror() {
  # 选出可用的 Debian 镜像基址 → 输出到 DEB_BASE / DEB_MIRROR / DEB_SEC
  local m host
  for m in "${DEB_MIRROR_BASES[@]}"; do
    host="${m#https://}"; host="${host%%/*}"
    if ttlx_tcp_ok "$host"; then
      DEB_BASE="$m"
      DEB_MIRROR="$m/debian"
      DEB_SEC="$m/debian-security"
      echo "$DEB_MIRROR"
      return 0
    fi
  done
  # 全都不通：兜底用清华（至少让 apt 给出明确报错）
  DEB_BASE="https://mirrors.tuna.tsinghua.edu.cn"
  DEB_MIRROR="$DEB_BASE/debian"
  DEB_SEC="$DEB_BASE/debian-security"
  echo "$DEB_MIRROR"
  return 1
}

ttlx_pick_pypi_mirror() {
  local m host
  for m in "${PYPI_MIRRORS[@]}"; do
    host="${m#https://}"; host="${host%%/*}"
    if ttlx_tcp_ok "$host"; then
      echo "$m"
      return 0
    fi
  done
  echo "${PYPI_MIRRORS[0]}"
  return 1
}

ttlx_pip_install() {
  # 带故障转移的 pip 安装：ttlx_pip_install <pip命令> <包...>
  #   例：ttlx_pip_install "$VENV/bin/pip" PySide6 cryptography
  local pip="$1"; shift
  local m
  for m in $(ttlx_pick_pypi_mirror_ordered); do
    echo "[ttlx] pip ← $m"
    if "$pip" install -i "$m" --timeout 60 --retries 2 "$@" 2>/dev/null; then
      return 0
    fi
  done
  # 最后再试一次官方源（外国网络环境用）
  echo "[ttlx] pip ← 官方 PyPI"
  "$pip" install --timeout 60 --retries 2 "$@"
}

ttlx_pick_pypi_mirror_ordered() {
  # 按可用性排序输出全部候选（连得上的放前面，全部输出兜底）
  local m host ok_list=""
  for m in "${PYPI_MIRRORS[@]}"; do
    host="${m#https://}"; host="${host%%/*}"
    if ttlx_tcp_ok "$host"; then ok_list="$ok_list $m"; fi
  done
  [ -n "$ok_list" ] && echo "$ok_list" || printf '%s\n' "${PYPI_MIRRORS[@]}"
}

ttlx_gh_url() {
  # 给出可尝试的 GitHub 下载地址列表：ttlx_gh_url <owner/repo/...路径>
  local path="${1#https://github.com/}"
  local p
  for p in "${GH_PROXIES[@]}"; do
    if [ -z "$p" ]; then
      echo "https://github.com/$path"
    else
      echo "${p}https://github.com/$path"
    fi
  done
}
