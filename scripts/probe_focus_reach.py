#!/usr/bin/env python3
"""⛔ **定位门**尺子：形态（reach）之外，改动单元在不在 focus 射程内？

═══ 为什么要有这个脚本 ═══

171 的上界实验证明「喂足 oracle 判据后合成层能修好」，172 证明「判据侧
还有别的门」。但 r94 拿 3 个**跨仓库**案例做同样实验时，**3/3 全是
no_fix**，而其中 2 个手动验证过「存在 gain」。⇒ 隔着一道之前没量过的门。

根因（`aether/synthesize.locate_focus`）：focus 只从**判据文本里的标识符**
映射到**源码的顶层 def/class 名**，而合成层的射程严格限定在 focus 内
（`synth_candidates`：`scopes = [defs[f] for f in focus]`）。于是：

    · 修复点在「被调函数的被调函数」里（D：判据写 running_min，
      改动在 _windowed_running_min）            ⇒ 射程外
    · 修复点在**类的方法**里（F：改动在 _ClassBuilder 的某个方法）
                                                ⇒ 射程外

⇒ **reach=L3 只保证「改动形态落在候选集的语法能力内」，不保证「改动单元
  在 focus 射程内」。** 这是两道独立的门，之前被混为一谈。

═══ 它回答什么问题 ═══

对每个案例，跑四组读数：

    1. 人类改动所在**顶层单元**
    2. `locate_focus` 自动定位到的 focus
    3. 改动单元 ∈ focus ？              ← 定位门开没开
    4. 自动 focus / **强制 focus=改动单元** 各自的最佳 gain

⛔⛔ **强制 focus 不是产品路径** —— 它绕过了 `locate_focus`，等于"告诉
   合成层去哪儿找"。它只用于回答：**如果定位是对的，合成层能不能修好？**
   答「能」⇒ no_fix 的根因是定位门，不是合成层能力；答「不能」⇒ 是形态/
   能力上限。**读数必须标注来源，不许混进"修好"计数。**

用法：
    python scripts/probe_focus_reach.py
    python scripts/probe_focus_reach.py --only D-d992be0
    python scripts/probe_focus_reach.py --out scripts/focus_reach.json
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aether.environment import run_in_sandbox                   # noqa: E402
from aether.synthesize import (locate_focus, synth_candidates,   # noqa: E402
                               top_defs)
from probe_upper_bound import CASES, changed_lines               # noqa: E402


def _git(repo: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                       text=True)
    return r.stdout if r.returncode == 0 else ""


def _pkg(repo: str, sha: str, src: str, main_src: str):
    """整包装载描述；不是包内模块 ⇒ None（与不传一字不差）。"""
    from probe_upper_bound import _pkg_context
    return _pkg_context(repo, sha, src, main_src)


def unit_of_line(src: str, lineno: int) -> str:
    """行号 ⇒ 它落在哪个**顶层** def/class 里。找不到 ⇒ `-`。"""
    tree = ast.parse(src)
    spans = []
    for name, node in top_defs(tree).items():
        lo = getattr(node, "lineno", 0)
        hi = max((getattr(n, "lineno", lo) for n in ast.walk(node)
                  if hasattr(n, "lineno")), default=lo)
        spans.append((lo, hi, name))
    for lo, hi, name in spans:
        if lo <= lineno <= hi:
            return name
    return "-"


def changed_units(buggy: str, fixed: str) -> list:
    """人类改动行 ⇒ 所属顶层单元（去重、保序）。"""
    sm = difflib.SequenceMatcher(None, buggy.splitlines(),
                                 fixed.splitlines())
    units, seen = [], set()
    for tag, i1, i2, _j1, _j2 in sm.get_opcodes():
        if tag not in ("replace", "delete"):
            continue
        for ln in range(i1 + 1, i2 + 1):
            u = unit_of_line(buggy, ln)
            if u != "-" and u not in seen:
                seen.add(u)
                units.append(u)
    return units


def best_of(buggy: str, tests: list, focus: list, pkg, timeout: float) -> tuple:
    """候选集里的最佳 (通过数, 描述, 候选数)。focus 为空 ⇒ (-1,'',0)。"""
    if not focus:
        return -1, "", 0
    cands = synth_candidates(buggy, focus, max_cands=400)
    best, desc = -1, ""
    for d, src, _mode in cands:
        r = run_in_sandbox(src, tests, timeout=timeout, pkg=pkg)
        ok = sum(1 for x in r.results if x.get("passed"))
        if ok > best:
            best, desc = ok, d
    return best, desc, len(cands)


def run(c: dict, timeout: float, verbose: bool = True) -> dict:
    repo, sha, src = c["repo"], c["sha"], c["src"]
    buggy = _git(repo, "show", f"{sha}^:{src}")
    fixed = _git(repo, "show", f"{sha}:{src}")
    tests = c["tests"]
    out = {"id": c["id"], "sha": sha, "reach": c["reach"]}
    if not buggy or not fixed:
        out["stage"] = "no_src"
        return out

    #: ⛔ 包内模块必须整包装载，否则 import-failed ⇒ 读数全 0（第 24 条）
    pb = _pkg(repo, f"{sha}^", src, buggy) if c.get("pkg") else None
    texts = [t["name"] + str(t.get("call", "")) for t in tests]
    focus = locate_focus(buggy, texts)
    units = changed_units(buggy, fixed)

    base = run_in_sandbox(buggy, tests, timeout=timeout, pkg=pb)
    b_ok = sum(1 for r in base.results if r.get("passed"))

    a_ok, a_desc, a_n = best_of(buggy, tests, focus, pb, timeout)
    f_ok, f_desc, f_n = best_of(buggy, tests, units, pb, timeout)

    out.update({
        "units": units,
        "focus": focus,
        #: ⛔ 定位门的判定：改动单元里**至少有一个**落在 focus 内
        "in_reach": bool(set(units) & set(focus)),
        "base_ok": b_ok, "k": len(tests),
        "auto_best": a_ok, "auto_gain": a_ok - b_ok,
        "auto_desc": a_desc, "auto_n_cands": a_n,
        "forced_best": f_ok, "forced_gain": f_ok - b_ok,
        "forced_desc": f_desc, "forced_n_cands": f_n,
        #: 四格分类：定位门 / 形态门 / 都不卡 / 都不卡但也没修好
        "stage": ("unreachable_focus" if not (set(units) & set(focus))
                  else ("shape_bound" if f_ok - b_ok < 1
                        else "fixable_if_focused")),
    })
    if verbose:
        print(f"\n── {c['id']}  reach={c['reach']}")
        print(f"   改动单元 {units}   focus {focus}   "
              f"∈? {'是' if out['in_reach'] else '否 ← 射程外'}")
        print(f"   base {b_ok}/{len(tests)}   "
              f"自动 {a_ok} (gain {a_ok - b_ok}, {a_n} 候选)   "
              f"强制 {f_ok} (gain {f_ok - b_ok}, {f_n} 候选) {f_desc}")
        print(f"   ⇒ {out['stage']}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", default="25")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    want = [s for s in args.only.split(",") if s]
    cases = [c for c in CASES if (not want or c["id"] in want)]
    print("=" * 76)
    print("定位门：形态可达的题，改动单元在不在 focus 射程内？")
    print("=" * 76)
    rows = [run(c, float(args.timeout)) for c in cases]

    print("\n" + "=" * 76)
    print("总账")
    print("=" * 76)
    for r in rows:
        print(f"  {r['id']:14s} reach={r.get('reach','-'):2s} "
              f"{r.get('stage',''):20s} "
              f"∈focus={'是' if r.get('in_reach') else '否'} "
              f"gain 自动={r.get('auto_gain')} 强制={r.get('forced_gain')}")
    n = len(rows)
    miss = sum(1 for r in rows if not r.get("in_reach"))
    print(f"\n  定位门（改动单元不在 focus 内）：{miss}/{n}")
    print("  ⛔ 强制 focus 的 gain **不算修复** —— 它是诊断读数，不是产品路径。")

    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False,
                                             indent=1), encoding="utf-8")
        print(f"\n  读数已存档 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
