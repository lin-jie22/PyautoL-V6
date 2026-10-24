#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**每一扇你能敲开的门都必须能敲开**（`--help` 不许抛异常）。

═══ 存在的理由（2026-10-24 实测）═══

跑 `aether evolve --help` 得到的是 traceback：

    ValueError: unsupported format character '?' (0xff09) at index 159

起因是 help 字符串里写了一句「120 条里 **105 条（87.5%）**是重复提交」——
argparse 对 help **会执行 `help % dict(vars())`**，于是 `%` 后面那个全角
`）` 被当成格式符。

⛔ 为什么这条空缺值得单独一份契约 —— 87 份契约里**没有一份**碰到它：

  契约测的是"功能对不对"，没有一份测"用户能不能看到说明书"。
  而 `--help` 是所有人第一条会敲的命令；它崩的时候产物是一屏 traceback，
  没有任何一次验收会发现。这是第 133 条的形状：量具覆盖面以外的地方，
  错了就等于没测。

而且这个缺陷**只能由真实运行发现**：静态检查 `ast.parse` 看不出 `%`，
grep "87.5%" 也看不出 —— `%` 是合法字符，只有 argparse 在**渲染那一刻**
才决定是否把它当格式符。⇒ 判据必须是"真的把 --help 跑一遍"。

═══ 组 ═══

  A 每个子命令的 `--help`：rc=0 且输出里有 usage（`-h` 与 `--help` 都跑）
  B 顶层 `--help` 与"不带参数的 usage"同样不许崩
  C 子命令清单**自举**：帮助里列出的命令 = 实际能跑的命令，两边不许漂
  D ⛔ 反证：字面 `%` 在 argparse help 里**会**崩（两种形态）⇒ A 组有牙
     （只修 bug 不加反证，等于断言恒真的老路，第 9 条）

═══ 这份契约自己被验证过没有？（三头，缺一头等于没验）═══

  1. 修复后：15 个子命令 `-h`/`--help` 全 rc=0 → 全绿；
  2. 2026-10-24 手工把修复改回（`87.5%%` → `87.5%`）→ **A1 立刻红**，
     指名 `evolve -h: rc=1 ValueError: unsupported format character '?'`；
  3. 复原 → rc=0。

  ⛔ 第 2 步没做的话，D 组只能证明"argparse 会崩"，证明不了
     "A1 抓得住产品代码的回归" —— 中间那一环是空的。
     ⛔ 注入不写进契约里自动跑：改产品文件的契约一旦中断就是真事故
     （README 144：就地注入前先 cp，别信 finally）。手动验，记在这里。

跑法（离线、秒级）：
    python tests/contract_cli_help_test.py
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

#: ⛔ 不许写死绝对路径（r60 F）：契约会在**副本根**上跑。
ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

SUPPORTED_MODES = ("default",)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def run(args, **kw):
    """在仓库根下跑一次 `python -m aether ...`。"""
    return subprocess.run([PY, "-m", "aether", *args], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=120, **kw)


def _subcommands():
    """从顶层帮助里解析子命令清单，而不是手写（手写会漂，第 46 条）。

    argparse 的 usage 形如：
        {status,init,selfcheck,...,memory,caps,evolve,ui} ...
    """
    p = run([])
    m = re.search(r"\{([a-z][a-z0-9-]*(?:,[a-z][a-z0-9-]*)*)\}\s*\.\.\.",
                  (p.stdout or "") + (p.stderr or ""))
    if not m:
        return []
    return m.group(1).split(",")


def main():
    print("=" * 64)
    print("== A 组：每个子命令的 --help 都必须能打开 ==")
    cmds = _subcommands()
    ck("A0 从 usage 里解析出子命令清单（清单不许是手写的）",
       len(cmds) >= 10, f"只解析到 {len(cmds)} 个：{cmds}")

    broken = []
    for c in cmds:
        for flag in ("-h", "--help"):
            try:
                p = run([c, flag])
            except subprocess.TimeoutExpired:
                broken.append(f"{c} {flag}: 超时")
                continue
            if p.returncode != 0:
                tail = (p.stderr or p.stdout or "").strip().splitlines()[-1:]
                broken.append(f"{c} {flag}: rc={p.returncode} {tail}")
                continue
            if "usage:" not in ((p.stdout or "") + (p.stderr or "")):
                broken.append(f"{c} {flag}: 输出里没有 usage（空帮助）")
    ck("A1 ⛔ 每个子命令的 -h / --help 都 rc=0 且真有内容",
       not broken, "; ".join(broken[:4]))

    print("== B 组：顶层两扇门 ==")
    for args in (["--help"], ["-h"], []):
        try:
            p = run(args)
        except subprocess.TimeoutExpired:
            ck(f"B {' '.join(args) or '(无参数)'}", False, "超时")
            continue
        #: ⛔ 顶层不带参数是**缺参数退出**（rc=2），与"崩"（rc=1/traceback）不同
        want = 0 if args else 2
        ck(f"B {' '.join(args) or '(无参数)'} rc=={want}",
           p.returncode == want, f"rc={p.returncode}")
        ck(f"B {' '.join(args) or '(无参数)'} 输出里没有 Traceback",
           "Traceback" not in ((p.stdout or "") + (p.stderr or "")),
           "顶层帮助抛异常")

    print("== C 组：帮助列的命令 = 实际能跑的命令 ==")
    missing = []
    for c in cmds:
        try:
            p = run([c, "--help"])
        except subprocess.TimeoutExpired:
            missing.append(f"{c}: 超时")
            continue
        usage = (p.stdout or "") + (p.stderr or "")
        if not usage:
            missing.append(f"{c}: 帮助为空")
    ck("C1 usage 里列出的每个命令都真的接得住 --help",
       not missing, "; ".join(missing[:4]))

    print("== D 组：⛔ 反证 —— A 组必须有牙 ==")
    #: 在临时目录里复刻 argparse 的行为：字面 `%` 必须让 --help 崩。
    #: 用**独立的最小脚本**而不是改业务代码，避免"C 组注入污染 A 组"。
    #: ⛔ 两种崩法，别只猜一种：
    #:   · `（87.5%）` —— `%` 后跟全角字符 ⇒ **ValueError**（非法格式符）
    #:   · `命中 %s 条` —— `%s` 是合法格式符但 params 里没有 ⇒ **TypeError**
    #: 只 `except ValueError` 的话，第二种会从"A 组被干掉"变成"探针自己死了"
    #: ⇒ 反证静默失效。except 收得比现实窄，是又一种"量具罢工不自知"。
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "probe_help.py"
        probe.write_text(
            "import argparse, sys\n"
            "CASES = ['命中率（87.5%）是常态', '命中 %s 条（其中有 %d 条重复）']\n"
            "bad = 0\n"
            "for h in CASES:\n"
            "    ap = argparse.ArgumentParser(prog='probe')\n"
            "    ap.add_argument('--x', help=h)\n"
            "    try:\n"
            "        ap.format_help()\n"
            "        print('NO_CRASH', h)\n"
            "    except Exception as e:\n"
            "        bad += 1\n"
            "        print('CRASH', type(e).__name__, e)\n"
            "print('BROKEN', bad, 'OF', len(CASES))\n",
            encoding="utf-8")
        p = subprocess.run([PY, str(probe)], capture_output=True, text=True,
                           timeout=30)
        out = (p.stdout or "").strip()
        n_crash = len([l for l in out.splitlines() if l.startswith("CRASH")])
        ck("D1 ⛔ 字面 `%`（非 `%%`）在 argparse help 里**会**崩（两种形态）",
           n_crash == 2 and "NO_CRASH" not in out,
           f"实际：{out!r} stderr={p.stderr.strip()[-120:]!r}")
        ck("D2 ⛔ 于是 A1 不是恒真：若有人写回单 `%`，A1 必须红豆",
           n_crash == 2,
           "这条反证失败 ⇒ 说明 A 组探测不到这类故障，本契约变成装饰")

    print("=" * 64)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print("  ✗", f)
        print()
        print("⛔ `--help` 崩的时候没有任何一次 `aether check` 会发现 ——")
        print("   契约测功能，没人测说明书。那份特拉凯斯是用户在第一时间看到的东西。")
        return 1
    print(f"✅ {len(cmds)} 个子命令的帮助全部可打开，且 A 组经注入验证有牙")
    return 0


if __name__ == "__main__":
    sys.exit(main())
