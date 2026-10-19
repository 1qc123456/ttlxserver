# -*- coding: utf-8 -*-
"""TTLX 学生端（APK 版）· 一键构建

做的事：
  ① 准备 assets：proot + 依赖库（从 D:\\android-toolchain\\apkassets）
                  + bootstrap（客户端代码 / 容器脚本 / 镜像脚本 / vapp 资源）
  ② 调 Gradle（阿里云镜像）编译 release APK（debug 签名，可侧载）
  ③ 产物复制到 release\\手机\\ 与 android\\dist\\

依赖工具链（一次性安装，脚本只读使用）：
  D:\\android-toolchain\\jdk-17.0.20.1+1      JDK17（清华 Adoptium 镜像）
  D:\\android-toolchain\\sdk                   Android SDK (platform-34 + build-tools 34)
  D:\\android-toolchain\\gradle-8.5             Gradle 8.5（腾讯镜像）
  D:\\android-toolchain\\gradle-home           Gradle 缓存（依赖走阿里云 maven）
用法：python build_apk.py
"""
import os
import shutil
import subprocess
import sys
import tarfile
import io

ROOT = r"D:\ttlxserver"
AP = os.path.join(ROOT, "app", "p")                      # 手机端源码
ANDROID = os.path.join(AP, "android")                    # 本工程
PYCLIENT = os.path.join(ROOT, "app", "pyclient")         # 桌面端（公共模块单一来源）
TOOLCHAIN = r"D:\android-toolchain"
APKASSETS = os.path.join(TOOLCHAIN, "apkassets")         # proot + 库
ASSETS = os.path.join(ANDROID, "app", "src", "main", "assets")
RES = os.path.join(ANDROID, "app", "src", "main", "res")
REL_PHONE = os.path.join(ROOT, "release", "手机")

JDK = os.path.join(TOOLCHAIN, "jdk-17.0.20.1+1")
SDK = os.path.join(TOOLCHAIN, "sdk")
GRADLE = os.path.join(TOOLCHAIN, "gradle-8.5", "bin", "gradle.bat")
GRADLE_HOME = os.path.join(TOOLCHAIN, "gradle-home")

COMMON_FILES = ["net_client.py", "waiting.py", "node_switch.py", "utils.py"]


def log(s):
    print(s, flush=True)


def ensure_loader():
    """proot deb 里的 loader / loader32（动态链接程序必需！），提取到 apkassets。
    ⚠ 曾经因为 deb 不在预期位置而静默跳过 → APK 缺 loader → 真机 proot
      execve 全部 ENOENT。现在失败必须中断构建。"""
    deb = os.path.join(TOOLCHAIN, "dl", "proot.deb")
    if not os.path.isfile(deb):
        raise SystemExit("缺少 %s —— 请先从清华 Termux 镜像下载 proot aarch64 deb" % deb)
    need = [n for n in ("loader", "loader32")
            if not os.path.isfile(os.path.join(APKASSETS, n))]
    data = open(deb, "rb").read()
    pos, members = 8, {}
    while pos + 60 <= len(data):
        hdr = data[pos:pos + 60]
        size = int(hdr[48:58].decode("ascii").strip())
        members[hdr[:16].decode("ascii", "replace").strip().rstrip("/")] = data[pos + 60:pos + 60 + size]
        pos += 60 + size + (size % 2)
    tf = tarfile.open(fileobj=io.BytesIO(members["data.tar.xz"]), mode="r:xz")
    for m in tf.getmembers():
        bn = os.path.basename(m.name)
        if bn in need and m.isfile():
            with open(os.path.join(APKASSETS, bn), "wb") as f:
                f.write(tf.extractfile(m).read())
            log("  提取 loader: %s" % bn)
    missing = [n for n in ("loader", "loader32")
               if not os.path.isfile(os.path.join(APKASSETS, n))]
    if missing:
        raise SystemExit("loader 提取失败（缺 %s）——APK 缺它会真机必挂" % missing)


def stage_assets():
    os.makedirs(ASSETS, exist_ok=True)
    # 1) 二进制（proot + 依赖库 + loader）
    #    proot        = tiny_container 打过补丁的 proot-latest（主用）
    #    proot-legacy = 原来的 Termux proot（App 会自检，主用跑不起来时自动切它）
    for n in ("proot", "proot-legacy", "libtalloc.so.2", "libandroid-shmem.so",
              "loader", "loader32"):
        src = os.path.join(APKASSETS, n)
        if not os.path.isfile(src):
            raise SystemExit("缺少 %s —— APK 缺这个文件真机必挂（loader 缺失会导致 proot "
                             "execve ENOENT）" % src)
        shutil.copy2(src, os.path.join(ASSETS, n))
    log("  二进制就位: proot(+legacy) + 2 库 + loader")

    # 2) bootstrap：镜像脚本 + 容器脚本 + 客户端 + vapp
    boot = os.path.join(ASSETS, "bootstrap")
    if os.path.isdir(boot):
        shutil.rmtree(boot)
    os.makedirs(boot)

    shutil.copy2(os.path.join(AP, "setup", "mirrors.sh"), boot)
    for f in ("container_setup.sh", "ttlx_start.sh", "install_wps_quiet.sh",
              "open_in_container.sh", "open_question.sh", "vapp_run.sh"):
        shutil.copy2(os.path.join(AP, "android", "container", f), boot)
    log("  引导脚本就位")

    cli = os.path.join(boot, "client")
    os.makedirs(cli)
    for f in COMMON_FILES:                       # 公共模块从桌面端同步
        shutil.copy2(os.path.join(PYCLIENT, "common", f), cli)
    for f in sorted(os.listdir(os.path.join(AP, "client"))):
        if f.endswith(".py"):
            shutil.copy2(os.path.join(AP, "client", f), cli)
    log("  客户端代码就位: %d 个 .py" % len(os.listdir(cli)))

    # 虚拟程序（Win10 设置 / Edge / 邮件）：exe 单独放一份，容器里直接可用
    # （散装 exe 比 zip 更直接，容器不必依赖 unzip 成功；也省掉一份重复体积）
    vad = os.path.join(boot, "vapp")
    os.makedirs(vad)
    vapp_src = os.path.join(PYCLIENT, "common", "vapp")
    n_vapp = 0
    for f in ("Win10.exe", "Edge.exe", "Win10_Email.exe"):
        p = os.path.join(vapp_src, f)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(vad, f))
            n_vapp += 1
            log("  虚拟程序: %s (%.2f MB)" % (f, os.path.getsize(p) / 1048576))
        else:
            raise SystemExit("缺少虚拟程序 %s（%s）—— Win10设置/Edge/邮件 类题将无法作答" % (f, p))
    log("  虚拟程序共 %d 个" % n_vapp)

    ad = os.path.join(boot, "assets")
    os.makedirs(ad)
    icon = os.path.join(PYCLIENT, "common", "assets", "stu.jpg")
    if os.path.isfile(icon):
        shutil.copy2(icon, ad)

    # 3) 应用图标
    dr = os.path.join(RES, "drawable")
    os.makedirs(dr, exist_ok=True)
    if os.path.isfile(icon):
        shutil.copy2(icon, os.path.join(dr, "stu.jpg"))
    log("  图标就位")


def build():
    env = dict(os.environ)
    env["JAVA_HOME"] = JDK
    env["ANDROID_HOME"] = SDK
    env["ANDROID_SDK_ROOT"] = SDK
    env["GRADLE_USER_HOME"] = GRADLE_HOME
    env["PATH"] = os.path.join(JDK, "bin") + os.pathsep + env.get("PATH", "")
    log("开始 Gradle 构建（首次会下载依赖，走阿里云镜像）…")
    r = subprocess.run([GRADLE, "-p", ANDROID, "assembleRelease", "--no-daemon", "-q"],
                       env=env, cwd=ANDROID,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log((r.stdout or "")[-4000:])
        log((r.stderr or "")[-4000:])
        raise SystemExit("Gradle 构建失败")
    apk = os.path.join(ANDROID, "app", "build", "outputs", "apk", "release", "app-release.apk")
    if not os.path.isfile(apk):
        alt = os.path.join(ANDROID, "app", "build", "outputs", "apk", "release")
        log("产物目录: %s -> %s" % (alt, os.listdir(alt) if os.path.isdir(alt) else "不存在"))
        raise SystemExit("未找到 APK 产物")
    os.makedirs(REL_PHONE, exist_ok=True)
    dst = os.path.join(REL_PHONE, "TTLX学生端-v3.3.apk")
    shutil.copy2(apk, dst)
    os.makedirs(os.path.join(ANDROID, "dist"), exist_ok=True)
    shutil.copy2(apk, os.path.join(ANDROID, "dist", "TTLX学生端-v3.3.apk"))
    log("APK 产物: %s (%.1f MB)" % (dst, os.path.getsize(dst) / 1048576))
    return dst


def main():
    log("=" * 64)
    log("构建 TTLX 学生端 APK")
    log("=" * 64)
    for p, name in ((JDK, "JDK17"), (SDK, "Android SDK"), (GRADLE, "Gradle")):
        if not os.path.exists(p):
            raise SystemExit("工具链缺失: %s (%s)" % (name, p))
    ensure_loader()
    log("① 准备 assets …")
    stage_assets()
    log("② 构建 APK …")
    apk = build()
    log("完成 ✔  %s" % apk)


if __name__ == "__main__":
    main()
