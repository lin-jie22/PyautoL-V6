"""审计探针：当前沙箱到底是"隔离"还是"只是用了 subprocess"？

V6 的 `environment.run_in_sandbox` 自称"沙箱隔离"，实际是
`subprocess.run(..., timeout=..., cwd=临时目录)`。本探针逐条实测它
**隔离了什么、没隔离什么**，每条都给出可执行证据。

⛔ 判据：能被子进程**读到的东西**才算泄漏；"看起来应该安全"不算。

用法：python3 scripts/probe_sandbox_audit.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether.environment import run_in_sandbox  # noqa: E402

#: ⛔ 探针源码的输出必须带 `PROBE:` 前缀：runner 自己也 print 一行 JSON，
#:    取"最后一行"拿到的是 runner 的，不是探针的（第一版就栽在这，
#:    六条结果全是 None，看起来像"都拦住了"）。
MARK = "PROBE:"
PROBES = {
    "读环境变量（密钥）": (
        "import os, json\n"
        "keys = sorted(k for k in os.environ if 'KEY' in k.upper() or "
        "'TOKEN' in k.upper() or 'SECRET' in k.upper() or k.startswith('V6_'))\n"
        "print('" + MARK + "' + json.dumps({'leaked': keys[:8],"
        " 'n_env': len(os.environ)}))\n"),
    "写工作目录之外": (
        "import json, os\n"
        "p = '/tmp/v6_sandbox_escape_test.txt'\n"
        "try:\n"
        "    open(p, 'w').write('pwned')\n"
        "    ok = os.path.exists(p)\n"
        "    os.remove(p)\n"
        "except Exception as e:\n"
        "    ok = 'ERR:' + type(e).__name__\n"
        "print('" + MARK + "' + json.dumps({'wrote_outside': ok}))\n"),
    "读工作目录之外": (
        "import json\n"
        "try:\n"
        "    d = open('/etc/hosts').read()[:20]\n"
        "except Exception as e:\n"
        "    d = 'ERR:' + type(e).__name__\n"
        "print('" + MARK + "' + json.dumps({'read_outside': d}))\n"),
    "无限循环（CPU）": "while True:\n    pass\n",
    # ⛔⛔ 探针**自己不许成为 DoS**：第一版写的是 `while True` 无限吃，
    #    而在 RLIMIT_AS 设不上的环境里它真的把系统内存吃光（实测把整台
    #    机器拖到 load 9.3、后续命令全部 SIGTERM）。所以这里**自限**到
    #    MEM_CAP_MB：达到上限就报告"能吃到这么多（未被拦）"然后退出。
    "无限吃内存": (
        # ⛔ 必须用 **json.dumps**：第一版用了 repr()，输出的是单引号字典，
        #    `json.loads` 解析失败 ⇒ payload 为空 ⇒ mb=0 ⇒ 被误判成
        #    "已拦住"（假绿灯）。判据解析失败必须 fail-closed，别让它绿。
        "import json\n"
        "b = bytearray()\n"
        "cap = 512 * 1024 * 1024\n"
        "got = 0\n"
        "blocked = False\n"
        "try:\n"
        "    while got < cap:\n"
        "        b.extend(b'x' * (8 * 1024 * 1024))\n"
        "        got = len(b)\n"
        "except MemoryError:\n"
        "    blocked = True\n"
        "print('PROBE:' + json.dumps({'mb': got // (1024 * 1024),"
        " 'blocked': blocked}))\n"),
    "海量输出": (
        "print('x' * (20 * 1024 * 1024))\n"),
    "fork 子进程": (
        "import subprocess, json, sys\n"
        "try:\n"
        "    r = subprocess.run([sys.executable, '-c', 'print(1)'],\n"
        "                       capture_output=True, text=True, timeout=5)\n"
        "    out = r.stdout.strip()\n"
        "except Exception as e:\n"
        "    out = 'ERR:' + type(e).__name__\n"
        "print('" + MARK + "' + json.dumps({'subprocess': out}))\n"),
}


def _probe_payload(stdout: str) -> dict:
    """从 stdout 里取出**探针自己**打印的那一行（带 PROBE: 前缀）。"""
    import json
    for line in (stdout or "").splitlines():
        s = line.strip()
        if s.startswith(MARK):
            try:
                return json.loads(s[len(MARK):])
            except Exception:  # noqa: BLE001
                return {}
    return {}


def _run(src: str, timeout: float = 5.0):
    t0 = time.time()
    ex = run_in_sandbox(src, [], timeout=timeout)
    return ex, time.time() - t0


def main() -> int:
    n_leak = 0
    print("=" * 68)
    print("沙箱实测（run_in_sandbox 当前实现）")
    print("=" * 68)

    for name, src in PROBES.items():
        ex, wall = _run(src)
        leak = False
        detail = ""
        if name == "读环境变量（密钥）":
            d = _probe_payload(ex.stdout)
            detail = f"env 变量 {d.get('n_env')} 个，疑似敏感 {d.get('leaked')}"
            leak = bool(d.get("leaked")) or (d.get("n_env", 0) > 20)
        elif name == "写工作目录之外":
            d = _probe_payload(ex.stdout)
            detail = f"wrote_outside={d.get('wrote_outside')}"
            leak = d.get("wrote_outside") is True
        elif name == "读工作目录之外":
            d = _probe_payload(ex.stdout)
            detail = f"read_outside={str(d.get('read_outside'))[:30]!r}"
            leak = not str(d.get("read_outside", "")).startswith("ERR:")
        elif name == "无限循环（CPU）":
            ok = ex.rc == 124 or wall < 8
            detail = f"rc={ex.rc} 耗时 {wall:.1f}s（timeout 生效："
            detail += "是" if ok else "否"
            detail += "）"
            leak = not ok
        elif name == "无限吃内存":
            d = _probe_payload(ex.stdout)
            if not d:
                # ⛔ 探针没回话 ⇒ **无从判别**，按未隔离处理（fail-closed）。
                #    判成"已拦住"就是假绿灯 —— 实测踩过一次。
                detail = ("⚠ 探针未返回结果 ⇒ 无从判别，按未隔离处理"
                          f"（stdout 尾部：{ex.stdout[-80:]!r}）")
                leak = True
            else:
                mb = int(d.get("mb", 0))
                # ⛔ 探针**自限**到 512 MB：能吃满 512 MB 而没被拦
                #    ⇒ 没有有效的内存限制（只剩 timeout 兜底）。
                detail = (f"吃到 {mb} MB，被内存限制拦住={d.get('blocked')}"
                          f"（探针自限 512 MB，避免把机器拖垮）")
                leak = (not d.get("blocked")) and mb >= 500
        elif name == "海量输出":
            mb = len(ex.stdout) / (1024 * 1024)
            cut = (ex.sandbox or {}).get("output_truncated")
            detail = (f"请求 20 MB → 实际读回 {mb:.1f} MB，"
                      f"truncated={cut}"
                      + ("（按 MAX_OUTPUT_BYTES 封顶，不再全量进内存）"
                         if cut else "（⚠ 未封顶 ⇒ 会 OOM）"))
            leak = not cut
        elif name == "fork 子进程":
            d = _probe_payload(ex.stdout)
            detail = f"子进程输出={d.get('subprocess')}"
            leak = str(d.get("subprocess")) == "1"

        flag = "❌ 未隔离" if leak else "✅ 已拦住"
        if leak:
            n_leak += 1
        print(f"\n{flag}  {name}")
        print(f"      {detail}")

    print("\n" + "=" * 68)
    # ⛔ 分层结论：**哪一层生效**比"有没有未隔离项"更重要。
    #    进程层能挡住密钥/资源，挡不住文件越界；后者只有 OS 层能挡，
    #    而 OS 层在部分环境不可用 ⇒ 必须把 tier 打出来，不许只报一个总数。
    sb = _run("print(1)\n")[0].sandbox or {}
    print(f"沙箱层级：tier={sb.get('tier')}  "
          f"os_sandbox={sb.get('os_sandbox')}  "
          f"fs_isolated={sb.get('fs_isolated')}  "
          f"env_purged={sb.get('env_purged')}")
    print(f"未隔离项：{n_leak} / {len(PROBES)}")
    if not sb.get("fs_isolated"):
        print("⚠️ 文件系统**未隔离**：OS 层沙箱在本环境不可用"
              "（实测 macOS sandbox-exec 的 deny-default 一律 "
              "Operation not permitted）⇒ 越界读/写与 fork 仍可能。"
              "这是已知边界，不是「已隔离」。")
    print("结论："
          + ("❌ 仍有未隔离项（见上）" if n_leak else "✅ 全部拦住"))
    return 1 if n_leak else 0


if __name__ == "__main__":
    sys.exit(main())
