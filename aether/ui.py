#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键起桌面控制台：serve（同源托管 UI）+ 打开桌面窗口。

    python -m aether.ui [--port 8611] [--caps capabilities.example.json]

⛔ 为什么单独有这个入口：控制台要能用，得同时满足三件事 ——
    ① 有 API 在跑；② 有静态页面被托管；③ 浏览器打开的是**同源**地址。
   分开三步做 ⇒ 迟早出现"页面打开了但 API 没起"或"起了但跨域"（实测：
   file:// 打开 index.html 时 fetch localhost 直接被 CORS 拒）。同源托管
   是最省事也最不容易错的做法，这个入口就是把它固化下来。

⛔ "桌面窗口"的实现方式：优先用 Chrome / Edge 的 `--app=<url>` 模式
   （独立窗口、无地址栏、无标签页 ⇒ 用起来像桌面应用）。没有这两个浏览器
   就退化成系统默认浏览器打开普通标签页 —— **不为了"桌面"而引入 Electron**
   （那会多出一条需要维护的构建链，与控制台"零构建"的初衷相反）。

⛔⛔ 代理坑：本机有 `HTTP_PROXY` 时，浏览器会把 127.0.0.1 也走代理 ⇒
   打开就白屏，而服务端明明是好的。所以显式给 `--proxy-bypass-list`。
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from .logging_setup import get_logger
_log = get_logger("aether.ui")         # ⛔ 显式名，理由见 cli.py 同位置

from .entry import (DEFAULT_CAPS, add_log_args, add_root_arg, add_serve_args,
                    add_ui_args, build_forward, normalize_log_args)
from .paths import ROOT, console_dir
#: ⛔ 控制台目录**由本入口决定**（不接受命令行覆盖）：它是"一键起控制台"
#:    的一部分，让人改了就会冒出"页面 404 而 API 正常"的幽灵故障。
#:    ⛔ 位置仍来自 `paths`：手拼 `ROOT / "console"` 就是布局又多一份。
#:    ⛔⛔ r61 补：装成 wheel 之后仓库根的 `console/` 不存在（它不是包资源），
#:      原来这里直接 `paths(ROOT)["console"]` ⇒ `aether ui` 装完起不来
#:      （实测报 `site-packages/console/index.html` 不存在，而 API 是好的）。
#:      `console_dir()` 在仓库那份缺席时落回包内快照，仓库态行为逐字不变。
CONSOLE = console_dir(ROOT)

#: ⛔ 只认这两个浏览器的 app 模式；没有就退化，不硬依赖。
APP_BROWSERS = (
    ("/Applications/Google Chrome.app", "Google Chrome"),
    ("/Applications/Microsoft Edge.app", "Microsoft Edge"),
)


def _free_port(preferred: int) -> int:
    """端口被占 ⇒ 顺延。**不**复用占用着的端口：那会连到别人的服务上，
    而页面上显示的账本却是本仓库的 —— 典型的"读数看着正常其实是别人家"。
    """
    p = preferred
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                p += 1
    raise SystemExit(f"找不到空闲端口（从 {preferred} 开始试了 50 个）")


def _wait_up(port: int, timeout: float = 15.0) -> bool:
    """等 API 真的能应答。**先探活再开浏览器**：否则先弹出一个白屏窗口，
    用户看到的是"页面坏了"，而真实原因只是服务还没起来。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _open_desktop(url: str) -> str:
    """开桌面窗口。返回描述（用于打印“到底是怎么打开的”）。"""
    for path, name in APP_BROWSERS:
        if Path(path).exists():
            subprocess.Popen([
                "open", "-a", name, "--args",
                f"--app={url}",
                #: ⛔ 显式绕代理：本机有 HTTP_PROXY 时 127.0.0.1 会被代理吃掉
                "--proxy-bypass-list=<-loopback>",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"{name} 应用窗口（--app 模式）"
    subprocess.Popen(["open", url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "系统默认浏览器（未找到 Chrome/Edge ⇒ 不是独立窗口）"


def build_parser() -> argparse.ArgumentParser:
    """ui 的 parser。

    ⛔ 与 `cli` 的 `ui` 子命令**逐项同源**（都调 `entry.add_serve_args`）：
       收口前两边各写一遍，cli 侧的 `--log-level` 甚至不在转发清单里
       ⇒ `python -m aether.cli ui --log-level DEBUG` 静默无效。
    ⛔ `--log-level` 的 choices 走 `LEVELS`：此前这里硬编码了一份列表，
       与 cli/serve 分叉（级别表改了它不会跟着改）。
    """
    ap = argparse.ArgumentParser(prog="aether.ui",
                                 description="起 API + 打开桌面控制台")
    #: ⛔ 与 cli 的 `ui` 子命令**逐项同源**（连 SUPPRESS 都一致）：
    #:    一边 SUPPRESS 一边有默认 ⇒ "同参数不同默认"又要靠人去记。
    #:    缺的值由 `normalize_log_args` 一次性补回（见 `entry`）。
    add_root_arg(ap, suppress=True)
    add_serve_args(ap, caps_default=DEFAULT_CAPS, with_console=False)
    add_ui_args(ap)
    #: ⛔ `suppress=True` + `normalize_log_args`：与 cli 的 `ui` 子命令
    #:    **逐项同源**（两边都 SUPPRESS），否则一边 INFO 一边 SUPPRESS，
    #:    "同参数不同默认"又要靠人去记哪个是哪个。
    add_log_args(ap, suppress=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = normalize_log_args(build_parser().parse_args(argv))

    #: ⛔ 父子进程都配：父进程的「端口被占」「服务没起来」是**父进程**的
    #:    诊断，走子进程的日志是看不到的（那是两个进程）。
    try:
        from .logging_setup import setup as _setup
        _setup(level=args.log_level, json_output=args.log_json)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    if not (CONSOLE / "index.html").is_file():
        _log.error(f"❌ 找不到控制台页面：{CONSOLE / 'index.html'}")
        return 1

    port = _free_port(args.port)
    if port != args.port:
        _log.warning(f"⚠️  {args.port} 被占用 ⇒ 改用 {port}")

    caps = args.caps
    if caps and not Path(caps).is_file() and (ROOT / caps).is_file():
        caps = str(ROOT / caps)

    #: ⛔ 透传清单由 `serve.build_parser()` **动态导出**（见 `entry`），
    #:    不手写 —— 手写的失败模式是"开关丢了但一切正常"。
    #:    日志口径也在其中（父子各配一套却级别不同 ⇒ 开关形同虚设）。
    from .serve import build_parser as serve_parser
    args.caps = caps            #: 用解析后的路径（相对 → 相对 ROOT 的绝对）
    cmd = [sys.executable, "-m", "aether.serve"] + build_forward(
        args, parser=serve_parser(), console=str(CONSOLE), port=port)

    proc = subprocess.Popen(cmd, cwd=str(ROOT))
    url = f"http://127.0.0.1:{port}/"
    print(f"① API + 控制台：{url}   （pid {proc.pid}）")
    if not _wait_up(port):
        _log.error("服务没能在 15s 内应答；完整启动命令：\n   "
                   + " ".join(cmd))
        proc.terminate()
        return 1

    if args.no_open:
        print("② --no-open：不开窗口")
    else:
        how = _open_desktop(url)
        print(f"② 已打开：{how}")

    print("\nCtrl-C 退出（会同时关掉服务）")
    try:
        while proc.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n退出中…")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
