#!/usr/bin/env python3
"""README 164 —— **装载门**普查：换仓库后到底还有多少能进得了沙箱。

═══ 为什么有这个脚本 ═══

163 在 boltons 上量出「覆盖率 5%」。用户拍板换第二个仓库验证这个数是不是
boltons 特有。结果**根本读不到**：

| 仓库 | 取到源码 | 沙箱装得起 | 抽得到判据 |
|---|---|---|---|
| boltons（对照） | 30/30 | 29/30 (97%) | 27/30 (90%) |
| more-itertools | 25/25 | **2/25 (8%)** | **0/25** |
| attrs | 25/25 | **2/25 (8%)** | 2/25 |

按第 24/133 条，这首先是「查询没命中」，不是「覆盖率低」。Root cause 只有
一处，且与能力无关：

    more_itertools/more.py:46
    ImportError: attempted relative import with no known parent package

沙箱把被测模块当 `task.py` **单独 exec** ⇒ 没有父包 ⇒ 任何包内相对导入都炸。
boltons 恰好不用相对导入（当初选它的理由正是「113 文件零依赖」）⇒ **5% 是
选择偏差的产物**。

⛔ 于是「最大的那道门」被重新排序 —— 在抽取/鉴别力/形态**之前**还站着一道
**装载门**，它拦掉 92%。本脚本量它：整包装载（`run_in_sandbox(pkg=...)`）
能把这道门开到多宽。

═══ 口径 ═══

· **装载率** = `run_in_sandbox(src, [1+1 探针]).import_error` 为空的比例。
  ⛔ 用同一条探针判「装得起」，前后两档唯一变量只有 `pkg` 传不传。
· **抽得到判据** = `extract_criteria` 在该 commit 的测试文件上产出的条数 > 0
  （纯 AST，与装载无关 ⇒ 这一列**不该**因本轮改动而变，它是**对照组**：
  如果它变了，说明改装载时误伤了别的东西）。
· ⛔ 装载失败**不许静默**：`import_error` 非空即记失败，不跳过、不算通过。

用法：
    python3 scripts/probe_package_load.py                    # 三仓库全量
    python3 scripts/probe_package_load.py --only more-itertools
    python3 scripts/probe_package_load.py --limit 10 --out /tmp/x.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import run_in_sandbox                 # noqa: E402
from aether.extract import (extract_criteria, needed_modules,                # noqa: E401,E501
                            target_module)

_p = ROOT / "scripts" / "probe_alias_demote.py"
_sp = importlib.util.spec_from_file_location("pad", _p)
_pad = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(_pad)
#: ⛔ 候选选取只用这一份实现（换仓库只换 `subdir`，词表不动）
select_commits = _pad.select_commits

REPOS = {
    "boltons": ("/tmp/realrepo/boltons", "boltons/"),
    "more-itertools": ("/tmp/realrepo/more-itertools", "more_itertools/"),
    "attrs": ("/tmp/realrepo/attrs", "src/attr/"),
}

PROBE = [{"name": "load", "call": "1 + 1", "expect": 2}]


def git(repo: Path, *a: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), *a],
                              capture_output=True, text=True).stdout
    except Exception:                                          # noqa: BLE001
        return ""


def pick(repo: Path, subdir: str, limit: int = 25):
    """⛔ 复用 `probe_alias_demote.select_commits`（**规则只有一份**）。

    第一版这里自带了一份 ⇒ 词表/测试目录判据一漂移就是两把尺子（162 的
    F 组契约专门盯这件事）。⛔ 另外：第一版用 `git log -- <subdir>` 做路径
    过滤 ⇒ **三个仓库全是 0 条** —— 候选是 `--filter=blob:none` 克隆的，
    按路径过滤要按需拉 blob，拉不到就静默返回空 ⇒ 「零命中」看起来像
    「这个仓库没有 fix commit」。第 133 条：零命中先证查询能命中。
    """
    return select_commits(repo, limit, subdir)


def pkg_context(repo: Path, sha: str, src: str, main_src: str) -> dict:
    """把「仓库里的一个模块」翻成沙箱的整包装载描述。

    `src = "more_itertools/more.py"` ⇒ 包名 `more_itertools`、模块名 `more`、
    同目录下其它 `.py` 作为候选池，再按**依赖闭包裁剪**
    （`extract.needed_modules`）。

    ⛔⛔ 为什么必须裁剪：第一版把整个包都塞进去 ⇒ boltons 从 **100% 掉到
    0%**，原因与能力无关 —— 无关模块 `ecoutils.py` 会 `import getpass →
    termios`，而 `termios` 在沙箱 deny-modules 里 ⇒ **一个八竿子打不着的
    模块让整批装载失败**（第 25 条：一个异常两种含义，先查再改）。

    ⛔ 被测模块自己**不进** modules（它由 stdin 传，是要被替换/合成的那一
    份）。⛔ 源码全部来自 `git show`，本函数不读工作区、不联网。
    """
    p = Path(src)
    pkgname, mod = p.parent.name, p.stem
    d = str(p.parent)
    listing = git(repo, "ls-tree", "-r", "--name-only", sha, "--", d)
    pool: dict = {}
    for f in sorted(listing.splitlines()):
        f = f.strip()
        if not f.endswith(".py"):
            continue
        rel = f[len(d) + 1:]
        key = rel[:-3]
        if key == "__init__":
            key = ""
        if key == mod:
            continue
        body = git(repo, "show", f"{sha}:{f}")
        if body:
            pool[key] = body
    return {"name": pkgname, "module": mod,
            "modules": dict(needed_modules(main_src, pkgname, pool))}


def selftest() -> bool:
    """⛔ 量具自检：读数出来之前先证量具本身是对的（判错停机）。"""
    ok = True

    def chk(name, got, want):
        nonlocal ok
        if got != want:
            ok = False
            print(f"  ⛔ 自检失败 {name}: got={got!r} want={want!r}")
        else:
            print(f"  ✅ {name}")

    #: ⓵ 单文件（无相对导入）不受影响 —— 向后兼容的底线
    plain = "def f(x):\n    return x * 2\n"
    r = run_in_sandbox(plain, [{"name": "a", "call": "f(3)", "expect": 6}])
    chk("A 单文件仍可用（无 pkg）", bool(r.results and r.results[0]["passed"]),
        True)

    #: ⓶ 相对导入：不给 pkg 必须炸（否则本轮这条读数就是恒真的）
    rel = "from .helper import f\n\ndef g(x):\n    return f(x) + 1\n"
    r0 = run_in_sandbox(rel, [{"name": "a", "call": "g(1)", "expect": 3}])
    chk("B 相对导入·无 pkg ⇒ import_error", bool(r0.import_error), True)
    chk("B2 且错误信息就是那一条",
        "no known parent package" in (r0.import_error or ""), True)

    #: ⓷ 给了 pkg 必须能装且语义对
    pkg = {"name": "tp", "module": "core",
           "modules": {"helper": "def f(x):\n    return x * 2\n"}}
    r1 = run_in_sandbox(rel, [{"name": "a", "call": "g(1)", "expect": 3}],
                        pkg=pkg)
    chk("C 相对导入·有 pkg ⇒ 装得起", r1.import_error, "")
    chk("C2 且结果正确", bool(r1.results and r1.results[0]["passed"]), True)

    #: ⓸ `__init__` 里 `from .<被测> import *` 必须成立（顺序不变量的反证）
    init = "from .core import g\n\ndef h(x):\n    return g(x) * 10\n"
    pkg2 = {"name": "tp2", "module": "core",
            "modules": {"": "from .core import g\n", "helper":
                        "def f(x):\n    return x * 2\n"}}
    r2 = run_in_sandbox(rel, [{"name": "a", "call": "g(1)", "expect": 3}],
                        pkg=pkg2)
    chk("D `__init__` 能 import 被测模块（先注册后 exec）", r2.import_error, "")

    #: ⓹ ⛔ 隔离不被削弱：传了 pkg 也仍然拦越界读
    esc = ("open('/etc/hosts').read(4)\n")
    r3 = run_in_sandbox(esc, [], pkg={"name": "tp3", "module": "m",
                                      "modules": {}})
    chk("E 传 pkg 时越界读仍被拒", bool(r3.import_error), True)
    chk("E2 且是 PermissionError", "PermissionError" in (r3.import_error or ""),
        True)

    #: ⓺ 同包模块自己装不起来 ⇒ 不许静默
    bad = {"name": "tp4", "module": "core",
           "modules": {"helper": "def f(:\n"}}
    r4 = run_in_sandbox(rel, [], pkg=bad)
    chk("F 同包模块语法错误 ⇒ 如实报 import_error", bool(r4.import_error), True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--timeout", default="20")
    args = ap.parse_args()

    if not selftest():
        print("\n⛔ 量具自检未过，停机。")
        return 2

    rows = []
    for name, (rp, sub) in REPOS.items():
        if args.only and args.only not in name:
            continue
        repo = Path(rp)
        if not repo.exists():
            print(f"  （跳过 {name}：仓库不在 {rp}）")
            continue
        cands = pick(repo, sub, args.limit)
        st = {"repo": name, "n": len(cands), "before": 0, "after": 0,
              "crit": 0, "crit_after": 0, "errs": {}, "errs_after": {}}
        for sha, src, tfs, subj in cands:
            fixed = git(repo, "show", f"{sha}:{src}")
            if not fixed:
                continue
            #: ⓵ before：老路径（单文件 task.py）
            e0 = run_in_sandbox(fixed, PROBE, timeout=float(args.timeout))
            before_ok = not e0.import_error
            st["before"] += before_ok
            if not before_ok:
                key = (e0.import_error or "").strip().splitlines()[-1][:60]
                st["errs"][key] = st["errs"].get(key, 0) + 1

            #: ⓶ after：整包装载（按需裁剪过的同包模块）
            pkg = pkg_context(repo, sha, src, fixed)
            e1 = run_in_sandbox(fixed, PROBE, timeout=float(args.timeout),
                                pkg=pkg)
            after_ok = not e1.import_error
            st["after"] += after_ok
            if not after_ok:
                #: ⛔ 失败**不许静默**：after 这一档也要给原因分布
                k1 = (e1.import_error or "").strip().splitlines()[-1][:60]
                st["errs_after"][k1] = st["errs_after"].get(k1, 0) + 1

            #: ⓷ 判据（纯 AST ⇒ 对照组，不该因本轮改动而变）
            tm = target_module(src)
            n = 0
            for tf in tfs:
                t = git(repo, "show", f"{sha}:{tf}")
                if t:
                    n += len(extract_criteria(t, tm))
            st["crit"] += (n > 0)
        rows.append(st)

    def pct(a, b):
        return f"{a}/{b} ({(a / b * 100):.0f}%)" if b else "-"

    print(f"\n{'=' * 78}\n装载门：整包装载前后对照\n{'=' * 78}")
    print(f"  {'仓库':<16} {'n':>3}  {'装得起·前':>12} {'装得起·后':>12}"
          f"  {'抽得到判据':>10}")
    for s in rows:
        print(f"  {s['repo']:<16} {s['n']:>3}  {pct(s['before'], s['n']):>12}"
              f" {pct(s['after'], s['n']):>12}  {pct(s['crit'], s['n']):>10}")

    print(f"\n{'=' * 78}\n装载失败的原因分布（按 traceback 末行）\n{'=' * 78}")
    for s in rows:
        for tag, key in (("before（单文件）", "errs"), ("after（整包）",
                                                      "errs_after")):
            if not s[key]:
                continue
            print(f"  ── {s['repo']} · {tag} ──")
            for k, v in sorted(s[key].items(), key=lambda x: -x[1])[:6]:
                print(f"    {v:>4}×  {k}")

    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False,
                                             indent=2), encoding="utf-8")
        print(f"\n  已落盘：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
