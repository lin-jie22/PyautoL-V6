#!/usr/bin/env python3
"""⛔ **第一个「全自动端到端」的真实 bug** —— README 162 的收尾实验。

═══ 它填补的缺口 ═══

到 161 为止，三个"修好的真实 bug"里，**没有一个是自动的**：判据由人手工
从作者的测试文件里转写（161 第七节白纸黑字写了）。所以当时的准确说法是
「物化到位之后系统能修」，**不是**「系统能自动从仓库端到端修」。

本脚本补的就是这一段：

    git 仓库 ──① 自动抽断言──② 自指模块别名降级──③ 沙箱验 semantics
             ──④ synthesize_patch（**不看答案**）──⑤ 与人类 diff 对拍

═══ ⛔⛔ 每一步都不能作弊，写下来是为了可被挑 ═══

  · ①②③ 只用**测试文件** + **缺陷源码**；Criterion 的筛选（"能通过"）
    是在**参照实现**上验的 —— 这正是 P2P/F2P 的标准定义（SWE-bench 口径：
    所有选定的判据必须在修复后的版本上通过），不是泄露补丁内容。
  · ④ `synthesize_patch(broken_src, tests)` 的签名里**没有 base**
    （159 用 AST 契约钉死，本脚本不破坏它）⇒ runner 从未见过修复后源码。
  · ⑤ 对拍比的是**改动的行**（161 第六节的教训：`766b554` 的作者顺手改了
    docstring，比整份文件会误判成"改在别处"）。

═══ ⛔ 判据密度必须报（160 的硬约束）═══

160 实测：同一合成层在 k=1 时 5/5 全错、k≥3 才收敛 ⇒ 谁报"自动修好了"
就必须同时报 **k**（判据条数）与其中有多少条是 F2P（真有鉴别力）。

跑法：
    python scripts/probe_auto_unlock.py --only 766b554
    python scripts/probe_auto_unlock.py            # 全候选
"""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import run_in_sandbox          # noqa: E402
from aether.extract import (                           # noqa: E402
    extract_criteria, gold_of, lit, target_module)
from aether.synthesize import synthesize_patch         # noqa: E402

_p = ROOT / "scripts" / "probe_alias_demote.py"
_sp = importlib.util.spec_from_file_location("pad", _p)
_pad = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(_pad)
run_all = _pad.run_all
select_commits = _pad.select_commits
_git = _pad._git

#: 整包装载上下文（README 164）—— 只此一份实现，本脚本不自带
_pp = ROOT / "scripts" / "probe_package_load.py"
_spp = importlib.util.spec_from_file_location("ppl", _pp)
_ppl = importlib.util.module_from_spec(_spp)
_spp.loader.exec_module(_ppl)
pkg_context = _ppl.pkg_context


def changed_lines(a: str, b: str):
    """⛔ 比**改动的行**，不是整份文件（161 的教训：作者顺手改了 docstring）。"""
    la, lb = a.splitlines(), b.splitlines()
    sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for l in la[i1:i2]:
            out.append(("-", l))
        for l in lb[j1:j2]:
            out.append(("+", l))
    return out


def auto_extract(repo: Path, sha: str, src: str, testfs: list,
                 pkg: dict | None = None):
    """① 自动抽断言 → ② 降级 → ③ 在参照实现上验 semantics。

    返回 `(tests, stats)`。前两步**完全由产品层的 `extract_criteria` 做**
    （脚本不自带第二份规则），本函数只负责"跑一遍验语义"。
    ⛔ 每条入选判据都过了三验：能求值 + 跑两遍一致 + 与测试写死的 expected
    一致（或抛对异常）。

    `pkg`（README 164）：整包装载上下文。⛔ 不传 ⇒ 与 162/163 的 boltons
    读数**完全一致**（boltons 不需要它）；换到 attrs / more-itertools 这类
    用包内相对导入的仓库则**必须**传，否则参照实现根本装不起来 ⇒ 抽到 0 条。
    """
    fixed = _git(repo, "show", f"{sha}:{src}")
    target = target_module(src)
    stx: dict = {"n_raw": 0, "n_unstable": 0, "n_unjsonable": 0}
    cands: list = []
    for tf in testfs:
        tsrc = _git(repo, "show", f"{sha}:{tf}")
        if not tsrc:
            continue
        cands.extend(extract_criteria(tsrc, target, stats=stx))
    stx["n_cand"] = len(cands)
    if not cands:
        return [], stx

    #: 三验第 1、2 步：能求值 + 两次一致
    it = [(f"x{j}", c["call"], c.get("raises")) for j, c in enumerate(cands)]
    r1 = run_all(fixed, it, pkg=pkg)
    r2 = run_all(fixed, it, pkg=pkg)
    if "__import_error__" in r1:
        stx["import_error"] = True
        return [], stx
    tests, n_stable = [], 0
    n_unpassed = 0
    for j, c in enumerate(cands):
        a, b = r1.get(f"x{j}"), r2.get(f"x{j}")
        if not a:
            continue
        if c["kind"] == "raises":
            if not (a["passed"] and b and b["passed"]):
                continue
            n_stable += 1
            tests.append({"name": f"auto{j}", "call": c["call"],
                          "raises": c["raises"]})
            continue
        if a.get("got") is None or (b and b.get("got") != a.get("got")):
            continue
        #: ⛔⛔ 三验第 1 步现在**必须含 `passed`**（README 165 第五节）。
        #:    只验"能求值 + 两次一致 + 与写死期望一致"是不够的：判据真正
        #:    执行时比的是 **JSON 往返后**的值，而三验比的是 `literal_eval`
        #:    的值 ⇒ 两套口径不一致。实测后果：`expect` 是 tuple 的判据
        #:    （JSON 会把它降级成 list）三验全过，进了判据集后在**任何**
        #:    版本上都失败 ⇒ 被当成 F2P ⇒ more-itertools 那 69 条"F2P"
        #:    几乎全是这个 ⇒ 合成层对着一堆永假目标烧光预算。
        if not (a.get("passed") and b and b.get("passed")):
            n_unpassed += 1
            continue
        #: 三验第 3 步：与测试**自己写死**的 expected 对拍
        #: ⛔ 用 `gold_value`（可能经内联解出），不是 `gold`（原文）——
        #:    期望侧写成变量的判据（more-itertools 293 条）用原文会全丢。
        g = c.get("gold_value")
        if g is None and c.get("gold") is not None:
            g = gold_of(c["gold"])
        if g is None or not lit(a["got"])[1] or lit(a["got"])[0] != g:
            continue
        n_stable += 1
        tests.append({"name": f"auto{j}", "call": c["call"], "expect": g})
    return tests, {"n_probe": stx.get("n_raw", 0), "n_cand": len(cands),
                   "n_unstable": stx.get("n_unstable", 0),
                   "n_unserializable": stx.get("n_unjsonable", 0),
                   "n_unpassed_on_fixed": n_unpassed,
                   "n_semantics_ok": n_stable, "n_tests": len(tests)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/tmp/realrepo/boltons")
    #: ⛔ 换仓库**只改这两个**，筛选词表与"同 commit 必须动 tests/"不许跟着变
    #:    （改了 ⇒ 跨仓库的覆盖率数字不可比）。
    ap.add_argument("--subdir", default="boltons/")
    ap.add_argument("--limit", default="40")
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", default="20")
    #: ⛔ 读数必须**落盘**、且由**仓库里的脚本**产出。
    #:   162 那版分布表是在 /tmp 的一次性脚本上取的（当时的提交也没有这份代码）
    #:   ⇒ 后来口径一变，那张表就**不可复现**了，只能整节作废重记（第 49-52 条）。
    ap.add_argument("--out", default="")
    #: `--reach` 附带**形态档**（L1/L2/L3/X + extend），让这张表自洽 ——
    #: 163 的合取结论（形态 ∧ 密度）必须能在一次运行里同时读到两端。
    ap.add_argument("--reach", action="store_true")
    args = ap.parse_args()

    ruler = None
    if args.reach:
        _rp = ROOT / "scripts" / "probe_shape_ruler.py"
        _rs = importlib.util.spec_from_file_location("psr", _rp)
        ruler = importlib.util.module_from_spec(_rs)
        _rs.loader.exec_module(ruler)
    repo = Path(args.repo).resolve()

    cands = select_commits(repo, int(args.limit), args.subdir)
    if args.only:
        want = set(args.only.split(","))
        cands = [c for c in cands if c[0] in want]
    print(f"候选 commit：{len(cands)}\n")

    rows = []
    for sha, src, testfs, subj in cands:
        buggy = _git(repo, "show", f"{sha}^:{src}")
        fixed = _git(repo, "show", f"{sha}:{src}")
        if not buggy or not fixed or buggy == fixed:
            continue

        #: ⛔ 整包装载上下文（README 164）：boltons 不需要，attrs 这类
        #:    用包内相对导入的仓库**必须**有它，否则参照实现装不起来。
        pkg = pkg_context(repo, sha, src, fixed) if args.subdir != "boltons/" \
            else None
        tests, stx = auto_extract(repo, sha, src, testfs, pkg=pkg)
        print(f"── {sha}  {subj[:44]}")
        print(f"   抽取：断言 {stx.get('n_probe', 0)} → 候选"
              f" {stx.get('n_cand', 0)}"
              f" → 过三验 {stx.get('n_semantics_ok', 0)}"
              f"（{stx.get('n_unserializable', 0)} 条期望值不可序列化 + "
              f"{stx.get('n_unpassed_on_fixed', 0)} 条在参照实现上就没过"
              f" ⇒ 显式丢弃）→ **可用判据 {stx.get('n_tests', 0)}**")
        if not tests:
            rows.append({"sha": sha, "stage": "no_criteria"})
            continue

        base_ok = run_in_sandbox(buggy, tests, pkg=pkg).results
        n_f2p = sum(1 for r in base_ok if not r.get("passed"))
        print(f"   ⛔ 判据密度 k={len(tests)}（其中在缺陷版上失败、即 F2P：{n_f2p}）")
        if n_f2p == 0:
            print("   ⇒ 无鉴别力判据 ⇒ 这道题进不了 V6（不是垫了 Meng）")
            rows.append({"sha": sha, "stage": "no_f2p", "k": len(tests)})
            continue

        res = synthesize_patch(buggy, tests, timeout=float(args.timeout),
                               pkg=pkg)
        print(f"   合成：base_ok={res.base_ok}/{len(tests)}  "
              f"found={res.found}  new_ok={res.new_ok}  "
              f"full_pass={res.full_pass}  focus={res.focus}  "
              f"desc={res.desc}")
        print(f"   量具：候选 {res.n_cands}、实跑 {res.n_ran}、"
              f"跑挂 {res.n_blew}、渲染 {res.render_modes}")
        verdict = "no_fix"
        if res.found and res.patch:
            human_del = [l for t, l in changed_lines(buggy, fixed) if t == "-"]
            human_add = [l for t, l in changed_lines(buggy, fixed) if t == "+"]
            patch_del = [l for t, l in changed_lines(buggy, res.patch)
                         if t == "-"]
            patch_add = [l for t, l in changed_lines(buggy, res.patch)
                         if t == "+"]
            exact = res.patch == fixed
            #: ⛔⛔ 判定口径（161 第七节的教训，这次在自己的产物上又踩了一次）：
            #:   作者可能在同一 commit 里**顺手**改别处（`766b554` 加了 2 行
            #:   doctest）⇒ 比"改动集合相等"会把它判成 elsewhere。正确的判法是：
            #:   **机器的改动是不是人类改动的子集**（机器只做了核心那一改、
            #:   没有做无关的润色 ⇒ 就是同一个修复）。
            m_set = {("-", l) for l in patch_del} | {("+", l) for l in patch_add}
            h_set = {("-", l) for l in human_del} | {("+", l) for l in human_add}
            if exact:
                verdict = "exact_file"
            elif m_set and m_set <= h_set:
                verdict = "same_core_fix"
            elif m_set & h_set:
                verdict = "partial_overlap"
            else:
                verdict = "elsewhere"
            print(f"   ⛔ 与人类修复对拍：{verdict}")
            print(f"      人类删：{[l.strip()[:50] for l in human_del][:3]}")
            print(f"      人类加：{[l.strip()[:50] for l in human_add][:3]}")
            print(f"      机器删：{[l.strip()[:50] for l in patch_del][:3]}")
            print(f"      机器加：{[l.strip()[:50] for l in patch_add][:3]}")
        rows.append({"sha": sha, "k": len(tests), "f2p": n_f2p,
                     "stage": verdict, "base_ok": res.base_ok,
                     "new_ok": res.new_ok, "found": res.found,
                     "full_pass": res.full_pass,
                     "n_unjson": stx.get("n_unjsonable", 0),
                     "desc": res.desc, "focus": str(res.focus)})
        print()

    if ruler:
        for r in rows:
            src_i = next((c[1] for c in cands if c[0] == r["sha"]), None)
            if not src_i:
                continue
            b = _git(repo, "show", f"{r['sha']}^:{src_i}")
            f = _git(repo, "show", f"{r['sha']}:{src_i}")
            try:
                info = ruler.analyze(b, f)
                r["reach"] = info["reach"]
                r["extend"] = info["extend"]
            except Exception as e:                            # noqa: BLE001
                r["reach"] = f"err:{type(e).__name__}"

    print(f"{'=' * 74}\n总账（stage 的口径：全绿且在同一行才算**修好**）\n{'=' * 74}")
    for r in rows:
        fp = r.get("full_pass")
        print(f"  {r['sha']:<9} {r['stage']:<16} k={r.get('k', '-'):<4} "
              f"f2p={r.get('f2p', '-'):<4} "
              f"{str(r.get('reach', '-')):<4} "
              f"{r.get('base_ok', '-')}→{r.get('new_ok', '-')}  "
              f"full_pass={'-' if fp is None else fp}")
    okrow = [r for r in rows if r["stage"] in ("exact_file", "same_core_fix")
             and r.get("full_pass")]
    print(f"\n  ⇒ **全自动端到端修好**（全绿 + 改动落在人类那一行）："
          f"{len(okrow)}/{len(rows)}  {[r['sha'] for r in okrow]}")

    if args.out:
        Path(args.out).write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  读数已落盘：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
