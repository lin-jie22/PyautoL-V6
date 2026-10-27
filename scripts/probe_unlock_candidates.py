#!/usr/bin/env python3
"""⛔ **物化率能解锁的真实 bug —— 全部候选、可复现**（README 161）。

═══ 这份脚本回答什么 ═══

161 的形态重测给出了一个**封闭的上界**：形态落在合成层射程内（L3）、
但自动抽取拿不到有鉴别力判据（F2P=0）的真实 bug，一共只有 **3 个**
（boltons 库，`scripts/probe_shape_ruler.py` 跑出来）。

⇒ 也就是说：「提高物化率最高能把 n=1 提到 n=4」，而这 3 个就是**全部**候选。

本脚本把它们的判据写死并跑一遍 `synthesize_patch` ⇒ 谁都能复现那个结果。

═══ ⛔⛔ 分寸（不许跳过的那一段）═══

**这里的判据是人工从测试文件转写的，不是自动抽取的。**

  · `766b554` —— 作者的 `test_bytes2human` 12 条断言照抄，
    只把局部别名 `b2h(...)` 换回顶层名 `bytes2human(...)`。
  · `93185b2` —— 153 注入的那条（`inclusive=True` + 9999 年上溢）+
    4 条回归保护。
  · `64e6430` —— 从作者的两段测试转写（其一我当时写成了 SyntaxError，
    **原样保留**以复现第八节那个现象）。

⇒ 它证明的是「**物化到位之后系统能修**」，**不是**「系统能自动从仓库
端到端修」。自动抽取至今卡在上下文门（局部别名 / fixture / 多步序列）。

═══ 为什么 `64e6430` 也要留着 ═══

它是 **found ≠ 修好了** 的活体实例：`found=True`、`gain>0`、6/7，
可补丁改在 `len(ded) > 384`（L122），人类修复在 L155 —— 两者毫无关系。
成因：只有 2 条能用的 F2P，且其中一条恒失败 ⇒ 全绿不可达 ⇒ 正确答案与
凑数解并列 ⇒ 先到先得选中了凑数解。

跑法：
    python scripts/probe_unlock_candidates.py --repo /tmp/realrepo/boltons
    python scripts/probe_unlock_candidates.py --only 64e6430
"""
from __future__ import annotations

import argparse
import difflib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import run_in_sandbox          # noqa: E402
from aether.synthesize import synthesize_patch         # noqa: E402

D = "__import__('datetime').date"
IS = "__import__('itertools').islice"

#: ⛔ 每个候选：(文件, [判据])。判据来源写在上面，改判据＝改证据。
CANDIDATES = {
    "766b554": {
        "module": "boltons/strutils.py",
        "note": "12 条全来自作者 test_bytes2human（别名已还原）",
        "tests": [
            {"name": "f1", "call": "bytes2human(1024)", "expect": "1K"},
            {"name": "f2", "call": "bytes2human(1024 ** 2)", "expect": "1M"},
            {"name": "f3", "call": "bytes2human(1024 ** 3)", "expect": "1G"},
            {"name": "f4", "call": "bytes2human(1024 ** 4)", "expect": "1T"},
            {"name": "f5", "call": "bytes2human(1023)", "expect": "1023B"},
            {"name": "f6", "call": "bytes2human(1024 ** 2 - 1, 1)",
             "expect": "1024.0K"},
            {"name": "p1", "call": "bytes2human(0)", "expect": "0B"},
            {"name": "p2", "call": "bytes2human(2048)", "expect": "2K"},
            {"name": "p3", "call": "bytes2human(128991)", "expect": "126K"},
            {"name": "f7", "call": "bytes2human(-1024)", "expect": "-1K"},
            {"name": "f8", "call": "bytes2human(-(1024 ** 2))", "expect": "-1M"},
            {"name": "f9", "call": "bytes2human(1024, 2)", "expect": "1.00K"},
        ],
    },
    "93185b2": {
        "module": "boltons/timeutils.py",
        "note": "F2P 逐字来自 153 注入的那条 + 4 条回归保护",
        "tests": [
            {"name": "F2P_overflow",
             "call": f"len(list(daterange({D}(9999,12,25), {D}(9999,12,25),"
                     f" inclusive=True)))", "expect": 1},
            {"name": "fwd",
             "call": f"len(list(daterange({D}(2020,1,1), {D}(2020,1,4))))",
             "expect": 3},
            {"name": "rev",
             "call": f"len(list(daterange({D}(2020,1,4), {D}(2020,1,1),"
                     f" step=-1)))", "expect": 3},
            {"name": "fwd_incl",
             "call": f"len(list(daterange({D}(2020,1,1), {D}(2020,1,4),"
                     f" inclusive=True)))", "expect": 4},
            {"name": "open_ended",
             "call": f"len(list({IS}(daterange({D}(2020,1,1), None), 5)))",
             "expect": 5},
        ],
    },
    "64e6430": {
        "module": "boltons/setutils.py",
        "note": "⛔ 故意保留那条写坏的 F2P_c —— 它是第八节现象的成因",
        "tests": [
            {"name": "F2P_a",
             "call": "(lambda s: str([s.pop(s.index(i)) for i in range(10)"
                     " if i >= 3]))(IndexedSet(range(10)))",
             "expect": "[3, 4, 5, 6, 7, 8, 9]"},
            {"name": "F2P_b",
             "call": "(lambda s: str((s.pop(3), s.index(7), s.pop(s.index(7)),"
                     " s.index(9))))(IndexedSet(range(10)))",
             "expect": "(3, 6, 7, 7)"},
            {"name": "F2P_c",
             "call": "(lambda s: (str([s.pop(s.index(i)) for i in reversed("
                     "range(10)) if not i % 2]), str(list(s))))"
                     "(IndexedSet(range(10)))",
             "expect": "(['8', '6', '4', '2', '0'], [1, 3, 5, 7, 9])"},
            {"name": "p1", "call": "str(list(IndexedSet(range(5))))",
             "expect": "[0, 1, 2, 3, 4]"},
            {"name": "p2", "call": "len(IndexedSet('abcabc'))", "expect": 3},
            {"name": "p3", "call": "IndexedSet(range(10)).index(5)",
             "expect": 5},
            {"name": "p4",
             "call": "(lambda s: str((s.pop(0), s.pop(0), list(s))))"
                     "(IndexedSet(range(5)))",
             "expect": "(0, 1, [2, 3, 4])"},
        ],
    },
}


def git(repo: Path, *a: str) -> str:
    p = subprocess.run(["git", *a], cwd=str(repo),
                       capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


def changed(before: str, after: str) -> tuple:
    """⛔ 返回这次改动**动了哪些行**（删除集, 新增集）。

    判「改在没改对地方」必须看**改动本身**，不能比整份文件 ——
    `766b554` 的作者在同一 commit 里顺手往 docstring 加了两行 doctest，
    于是「代码改动完全一致」的两个版本**整份文件并不相同**。
    比整份文件会把这种情况误判成「改在别处」（本脚本第一版就判错了）。
    """
    d = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                  lineterm="", n=0))
    dels = frozenset(l[1:].strip() for l in d
                     if l.startswith("-") and not l.startswith("---"))
    adds = frozenset(l[1:].strip() for l in d
                     if l.startswith("+") and not l.startswith("+++"))
    return dels, adds


def score_line(src: str, tests, tag: str) -> None:
    v = run_in_sandbox(src, tests, timeout=25)
    n_ok = sum(1 for r in v.results if r.get("passed"))
    bad = [t["name"] for t, r in zip(tests, v.results) if not r.get("passed")]
    print(f"  {tag:<6} {n_ok}/{len(tests)}" + (f"   未过：{bad}" if bad else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/tmp/realrepo/boltons")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    repo = Path(args.repo)
    if not (repo / ".git").exists():
        print(f"⛔ 找不到仓库 {repo}（本脚本依赖它取真值，无法离线回归）")
        return 1

    picks = {k: v for k, v in CANDIDATES.items()
             if not args.only or k == args.only}
    if not picks:
        print(f"⛔ 没有这个候选：{args.only}（可选 {sorted(CANDIDATES)}）")
        return 1

    print("=" * 76)
    print(f"物化率能解锁的全部候选（{len(picks)} 个）—— 基线＝缺陷版，不看答案")
    print("=" * 76)
    ok_count = 0
    for sha, cfg in picks.items():
        mod = cfg["module"]
        buggy = git(repo, "show", f"{sha}^:{mod}")
        fixed = git(repo, "show", f"{sha}:{mod}")
        if not buggy or not fixed:
            print(f"\n[{sha}] ⛔ 取不到源码，跳过")
            continue
        tests = cfg["tests"]
        print(f"\n── {sha}  {mod}   （{cfg['note']}）")
        score_line(fixed, tests, "fixed")
        score_line(buggy, tests, "buggy")
        r = synthesize_patch(buggy, tests)
        print(f"  synth  found={r.found} full_pass={r.full_pass} "
              f"desc={r.desc}  {r.base_ok}→{r.new_ok}/{r.n_tests} "
              f"候选{r.n_cands} 跑挂{r.n_blew} 定位{r.focus}")
        if r.patch:
            dl = [l for l in difflib.unified_diff(
                      buggy.splitlines(), r.patch.splitlines(),
                      lineterm="", n=0)
                  if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
            for l in dl:
                print(f"      {l}")
            #: ⛔ 两件事分开报，不许混：
            #:   whole_file  —— 整份文件逐字相同（会被作者的顺手改动打穿）
            #:   same_change —— **改动的行**相同（这才叫「改对了地方」）
            pd, pa = changed(buggy, r.patch)
            fd, fa = changed(buggy, fixed)
            same_change = bool(pd) and pd <= fd and pa <= fa
            print(f"      ⛔ 改动的行与人类一致？ {same_change}"
                  f"   （整份文件逐字相同？ {r.patch == fixed}）")
            if same_change and r.full_pass:
                ok_count += 1
            elif not same_change:
                print("        ⇒ 改在别处，**不计入**修好"
                      "（64e6430 是 live example：found=True 也没用）")
        else:
            print("      （无候选优于基线 ⇒ 未动手）")

    print("\n" + "=" * 76)
    print(f"完全达标（全绿 且 **改动的行**与人类一致）：{ok_count}/{len(picks)}")
    print("⛔ 「改动的行」而非「整份文件」：766b554 的作者在同一 commit 里")
    print("  顺手往 docstring 加了两行 doctest ⇒ 文件不等、改动相等。")
    print("⛔ 判据是**人工转写**的 ⇒ 这衡量的是「物化到位后能否修」，")
    print("  不是「能否自动端到端修」（自动抽取仍卡上下文门）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
