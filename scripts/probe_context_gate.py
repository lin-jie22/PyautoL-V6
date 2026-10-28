#!/usr/bin/env python3
"""⛔ **「上下文门」普查** —— 三道门里的第三道，它拦掉多少、能不能开。

═══ 背景（README 153/156 留下的读数）═══

真实 bug 能不能进 V6，要连过三道**串联**的门：

    ① 形态门   修复必须是"算子/常量被换掉" —— 84.9% 的真实修复是结构级
    ② 判据门   F2P 的表达形态（`with raises(...)`）能不能被判据吃下
    ③ 上下文门 断言表达式能不能**脱离测试函数单拎出来求值**

① 已普查（153），② 已开（156）。③ 至今只有一个软读数：
「4 个算子级真实 bug 里 2 个卡在上下文门」。这个数太小，不能拿来做
决定 ⇒ 本脚本把它量成分布。

═══ ⛔ 为什么要量「软失败」这一档（这是本脚本最关键的设计）═══

探针抽出来之后一直只有**一种**失败被记账：在参照实现上求值不了
（`got=None` ⇒ `unusable`）。但还有第二种失败**从未被检查过**：

    探针**能**求值，但求值出来的是**错的**东西。

典型成因正是上下文：`chk(bits.as_bin(), '01')` 里的 `bits` 是循环变量，
单拎出来若碰巧在模块里有同名对象，就会"跑通"却测的是别的东西 ⇒ 它在
fixed 上通过、在 buggy 上失败 ⇒ **看起来有鉴别力，其实是假鉴别力**。

⇒ 本脚本把每条探针分成三档：

    none      —— 求值不了（硬的，已被记账）
    mismatch  —— 能求值，但与**测试自己写死的 expected** 不一致（软的，
                 全新读数。这是判据门里一处**隐形的假绿**）
    ok        —— 能求值且与测试写死的 expected 一致

⛔ **金标准来自测试本身**，不是我的判断：`chk(actual, expected)` 里那个
   expected 只要能 `literal_eval`，就是作者亲手写死的真值。用它对拍，
   「内联有没有改坏语义」才有答案 —— 否则「内联后能跑」什么都证明不了。

═══ 内联器与它的四条自限（不许为了救回率放宽任何一条）═══

    1. **只内联所在函数内的赋值 + 模块级赋值**（同名不同作用域不许混）；
    2. **同名有两个不同 rhs ⇒ 不内联**（循环里重复赋值 ⇒ 语义不确定）；
    3. **内联后表达式里出现 `today/now/random/uuid/time()` ⇒ 拒绝**
       （时间/随机依赖，跨天跨进程会翻 ⇒ 不可复现的判据 ≡ 随机判据）；
    4. **内联后必须过三道验**：能求值 + 跑两遍稳定 + 与测试写死 expected
       一致。三条缺一不采纳（fail-closed）。

跑法：
    python scripts/probe_context_gate.py --repo /tmp/realrepo/boltons --limit 30
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import run_in_sandbox  # noqa: E402


# ── ⛔ 规则**只有一份**：这些函数的本体在 2026-10-28（README 162）迁到了产品
#    模块 `aether/extract.py`。本脚本只保留**量**的部分（run_all / 判档 / 报告）。
#    ⇒ 抄第二份的后果已见过一次：两把尺子漂移，读数无法对拍。
from aether.extract import (  # noqa: E402
    _UNSTABLE_CALLS, collect_bindings, gold_of, has_unstable_call,
    inline_expr, lit, scoped_assertions, scoped_raise_probes,
)


# ─────────────────────────── 求值 / 判档 ─────────────────────────────────
def run_all(source: str, items, timeout: float = 15.0, pkg=None):
    """统一跑一批探针（返回值类 + 期望异常类混跑）。

    `items`: [(key, call, raises|None)]。返回 `{key: {"passed":bool,"got":str|None}}`。
    ⛔⛔ **key 必须含序号**：两条不同的探针可能有相同的 call 文本（不同
    作用域同名变量），用 call 当 key 会互相覆盖 ⇒ 读数凭空少一半。

    `pkg`（README 164）：整包装载上下文，原样透传给沙箱；默认 None ⇒ 与
    加这个参数之前完全一致。
    """
    if not items:
        return {}
    tests = []
    for i, (k, call, raises) in enumerate(items):
        t = {"name": f"q{i}", "call": call}
        if raises:
            t["raises"] = raises
        tests.append(t)
    ex = run_in_sandbox(source, tests, timeout=timeout, pkg=pkg)
    if ex.import_error:
        return {"__import_error__": ex.import_error[:200]}
    out = {}
    for i, r in enumerate(ex.results):
        ev = r.get("evidence") or ""
        got = (ev.split("got=", 1)[1].split(" (truthy check)")[0]
               if ev.startswith("got=") else None)
        out[items[i][0]] = {"passed": bool(r.get("passed")), "got": got}
    return out






def classify_probe(expr: str, exp_text: str, got: str | None) -> str:
    if got is None:
        return "none"
    g = gold_of(exp_text)
    if g is None:
        return "ok_nogold"          #: 能求值，但测试期望不是字面量 ⇒ 无法对拍
    v, ok = lit(got)
    if not ok:
        return "ok_nogold"
    return "ok" if v == g else "mismatch"


# ─────────────────────────── 主流程 ──────────────────────────────────────
def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(repo),
                       capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


def _looks_like_fix(subj: str) -> bool:
    s = subj.lower()
    return any(w in s for w in ("fix", "resolve", "handle", "correct",
                                "bug", "repair", "regression"))


def selftest() -> bool:
    """⛔ 量具自检（第 24 条）。三条已知答案，判错即停机。"""
    ok = True

    def chk(label, got, want):
        nonlocal ok
        flag = "✓" if got == want else "✗"
        if got != want:
            ok = False
        print(f"    {flag} 自检{label}: 期望 {want!r:<28} 实得 {got!r}")

    src = "def t():\n    b = '01'\n    chk(b.upper(), '01'.upper())\n"
    a = scoped_assertions(src)
    chk("A 找出断言", bool(a), True)
    binds = collect_bindings(src)["t"]
    chk("B 作用域绑定", sorted(binds), ["b"])
    chk("C 内联", inline_expr("b.upper()", binds), "'01'.upper()")
    # 同名不同 rhs ⇒ 不内联
    src2 = "def t():\n    x = 1\n    x = 2\n    chk(x, 2)\n"
    chk("D 歧义剔除", collect_bindings(src2)["t"], {})
    # 时间依赖 ⇒ 拒收
    chk("E 时间依赖", has_unstable_call("date.today() + 1"), True)
    chk("F 无依赖", has_unstable_call("Bits('01').as_bin()"), False)
    # 判档
    chk("G 硬失败", classify_probe("x", "1", None), "none")
    chk("H 软失败", classify_probe("x", "1", "2"), "mismatch")
    chk("I 通过", classify_probe("x", "1", "1"), "ok")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--limit", default="30")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()

    _ok = selftest()
    print(f"量具自检：{'✅ 通过' if _ok else '❌ 未通过'}")
    if not _ok:
        print("  ⇒ 内联器/判档函数本身不可信，停机")
        return 1

    allsha = _git(repo, "log", "--format=%H").split()
    cands = []
    for sha in allsha:
        subj = _git(repo, "log", "-1", "--format=%s", sha).strip()
        if not _looks_like_fix(subj):
            continue
        files = [l.strip() for l in
                 _git(repo, "show", "--name-only", "--format=", sha).splitlines()
                 if l.strip()]
        srcs = [f for f in files if f.startswith("boltons/") and f.endswith(".py")
                and "__init__" not in f]
        tests = [f for f in files if f.startswith("tests/") and f.endswith(".py")]
        if len(srcs) != 1 or not tests:
            continue
        cands.append((sha[:7], srcs[0], tests, subj))
        if len(cands) >= int(args.limit):
            break
    print(f"\n候选 commit：{len(cands)}（像 bug fix + 单源文件 + 动过测试）")

    tally = {"none": 0, "mismatch": 0, "ok": 0, "ok_nogold": 0}
    #: ⛔ 救回数**必须分两档记账**：过了金标准对拍的（语义可证）与没金标准
    #:    可对拍的（只是"能求值"，语义未证）。混在一起报 ⇒ 又是一次
    #:    "看起来救回很多"的假读数（第 115 条：单次读数≠结构必然）。
    st = {"rescued_gold": 0, "rescued_nogold": 0, "rescued_raises": 0,
          "disc_gold": 0, "disc_nogold": 0, "disc_raises": 0,
          "rejected_time": 0, "rejected_gold": 0, "unstable": 0,
          "total": 0, "by_kind": {}}
    rows = []
    per_commit: dict = {}
    for sha, src, testfs, subj in cands:
        fixed = _git(repo, "show", f"{sha}:{src}")
        buggy = _git(repo, "show", f"{sha}^:{src}")
        if not fixed or not buggy or fixed == buggy:
            continue

        #: ⛔ 两类探针（返回值 / 期望异常）**一起**进普查。只抽一类会把
        #:    c1c25da 这种"全靠 raises 才有鉴别力"的 bug 记成 0 ⇒ 假结论。
        probes, envs = [], {}
        for tf in testfs:
            tsrc = _git(repo, "show", f"{sha}:{tf}")
            if not tsrc:
                continue
            for act, exp, fn in scoped_assertions(tsrc):
                probes.append({"kind": "expect", "expr": act, "gold": exp,
                               "fn": fn, "tf": tf})
            for act, exc, fn in scoped_raise_probes(tsrc):
                probes.append({"kind": "raises", "expr": act, "exc": exc,
                               "fn": fn, "tf": tf})
            for sc, b in collect_bindings(tsrc).items():
                envs[f"{tf}::{sc}"] = b

        if not probes:
            rows.append({"sha": sha, "note": "没抽到断言"})
            continue

        def items_of(ps, tag, exprs=None):
            """⛔ `exprs` 是**覆盖用的调用文本**：内联后要跑的是内联结果，
            不是原表达式。第一版漏了这个参数 ⇒ 跑的还是原表达式 ⇒
            救回数从 247 直接归零（某维度恒 0，先怀疑量具 —— 第 24 条）。"""
            return [(f"{tag}{i}",
                     (exprs[i] if exprs else p["expr"]), p.get("exc"))
                    for i, p in enumerate(ps)]

        def judge(p, r):
            if p["kind"] == "raises":
                return "ok" if (r and r["passed"]) else "none"
            if r is None or r["got"] is None:
                return "none"
            return classify_probe(p["expr"], p["gold"], r["got"])

        def is_disc(p, rf, rb):
            if p["kind"] == "raises":
                return bool(rf and rf["passed"] and rb and not rb["passed"])
            return bool(rf and rf["got"] is not None
                        and rf["got"] != (rb or {}).get("got"))

        g1 = run_all(fixed, items_of(probes, "a"))
        if "__import_error__" in g1:
            rows.append({"sha": sha, "note": "导入失败"})
            continue
        gb0 = run_all(buggy, items_of(probes, "a"))

        stash = {"none": [], "mismatch": []}
        kinds = {"ok": 0, "ok_nogold": 0}
        disc_before = 0
        for i, p in enumerate(probes):
            st["total"] += 1
            k = judge(p, g1.get(f"a{i}"))
            tally[k] += 1
            st["by_kind"][f"{p['kind']}/{k}"] = \
                st["by_kind"].get(f"{p['kind']}/{k}", 0) + 1
            if k in ("none", "mismatch"):
                stash[k].append((i, p))
            else:
                kinds[k] += 1
                #: ⛔ 鉴别力只对**语义可信**的探针计（mismatch 类的"不同"
                #:   是假鉴别力，计进去等于把噪声当收益）。
                if is_disc(p, g1.get(f"a{i}"), gb0.get(f"a{i}")):
                    disc_before += 1

        # ── 对 none / mismatch 尝试内联 ──────────────────────────
        tries = stash["none"] + stash["mismatch"]
        inlined: list = []
        for i, p in tries:
            binds = dict(envs.get(f"{p['tf']}::<module>", {}))
            binds.update(envs.get(f"{p['tf']}::{p['fn']}", {}))
            new = inline_expr(p["expr"], binds)
            if not new or new == p["expr"]:
                continue
            if has_unstable_call(new):
                st["rejected_time"] += 1
                continue
            inlined.append((i, p, new))

        disc_after = disc_before
        if inlined:
            _ps = [p for _, p, _ in inlined]
            _ex = [n for _, _, n in inlined]
            g2 = run_all(fixed, items_of(_ps, "b", _ex))
            g2b = run_all(fixed, items_of(_ps, "c", _ex))
            gb = run_all(buggy, items_of(_ps, "d", _ex))
            for j, (i, p, new) in enumerate(inlined):
                rf, rf2 = g2.get(f"b{j}"), g2b.get(f"c{j}")
                k = judge(p, rf)
                if k == "none":
                    continue
                if p["kind"] == "expect" and (
                        rf is None or rf2 is None or rf["got"] != rf2["got"]):
                    st["unstable"] += 1
                    continue
                if k == "mismatch":
                    st["rejected_gold"] += 1
                    continue
                disc = is_disc(p, rf, gb.get(f"d{j}"))
                #: ⛔ 三档分开记：金标准对拍过的 / 无金标准的 / raises 类的
                if p["kind"] == "raises":
                    st["rescued_raises"] += 1
                    if disc:
                        st["disc_raises"] += 1
                else:
                    key = "gold" if k == "ok" else "nogold"
                    st[f"rescued_{key}"] += 1
                    if disc:
                        st[f"disc_{key}"] += 1
                if disc:
                    disc_after += 1
                rows.append({"sha": sha, "kind": p["kind"], "orig": p["expr"],
                             "inlined": new,
                             "gold": p.get("gold") or p.get("exc"),
                             "got": (rf or {}).get("got"),
                             "buggy": (gb.get(f"d{j}") or {}).get("got"),
                             "discriminating": disc})

        per_commit[sha] = {"before": disc_before, "after": disc_after,
                           "probes": len(probes),
                           "none": len(stash["none"])}
        rows.append({"sha": sha, "subject": subj[:50],
                     "probes": len(probes),
                     "none": len(stash["none"]),
                     "mismatch": len(stash["mismatch"]),
                     "ok": kinds["ok"], "ok_nogold": kinds["ok_nogold"],
                     "inlined_tried": len(inlined),
                     "disc_before": disc_before, "disc_after": disc_after})

    T = max(st["total"], 1)
    print(f"\n{'=' * 76}\n探针判档（全部 {st['total']} 条真实断言）\n{'=' * 76}")
    for k, v in tally.items():
        print(f"  {k:<12} {v:>4}  ({v / T:.1%})")
    print(f"\n  硬失败（求值不了，即上下文门拦掉的）：{tally['none']}  ({tally['none'] / T:.1%})")
    print(f"  ⛔ 软失败（能求值但与测试写死期望不符 —— 从未被记账的假鉴别力）："
          f"{tally['mismatch']}  ({tally['mismatch'] / T:.1%})")

    print(f"\n  ⛔ 分形态看（`expect` = 返回值断言 / `raises` = 期望异常）：")
    for k in sorted(st["by_kind"]):
        print(f"      {k:<18} {st['by_kind'][k]:>5}")

    print(f"\n{'=' * 76}\n内联救回（分档，不许只报总数）\n{'=' * 76}")
    print(f"  过了金标准对拍的（返回值类）：{st['rescued_gold']:>4}"
          f"   其中真有鉴别力：{st['disc_gold']}")
    print(f"  无金标准可对拍的（返回值类）：{st['rescued_nogold']:>4}"
          f"   其中真有鉴别力：{st['disc_nogold']}"
          f"   ⛔ 语义未证，不算收益")
    print(f"  期望异常类：{st['rescued_raises']:>4}"
          f"   其中真有鉴别力：{st['disc_raises']}"
          f"   ⛔ 语义由'抛对异常'保证，无法金标准对拍")
    print(f"  拒收：时间/随机依赖 {st['rejected_time']}、"
          f"与金标准不符 {st['rejected_gold']}、两次运行不一致 {st['unstable']}")

    #: ⛔ 真正决定方向的读数：**commit 级**。一条判据变多不算数，
    #:    「本来 0 条有鉴别力（⇒ 这个 bug 进不了 V6）变成 ≥1 条」才算。
    gained = [(s, v) for s, v in per_commit.items()
              if v["before"] == 0 and v["after"] > 0]
    grew = [(s, v) for s, v in per_commit.items() if v["after"] > v["before"]]
    print(f"\n{'=' * 76}\ncommit 级增益（这才是决定能不能多修真实 bug 的量）\n"
          f"{'=' * 76}")
    print(f"  候选 commit：{len(per_commit)}")
    print(f"  内联前有鉴别力判据 ≥1 条的："
          f"{sum(1 for v in per_commit.values() if v['before'] > 0)}")
    print(f"  ⇒ 内联后 0 → ≥1 条（**新变成可表达**）：{len(gained)}")
    print(f"  ⇒ 内联后判据变多的（含本来就有）：{len(grew)}")
    for s, v in gained[:12]:
        print(f"      · {s}  0 → {v['after']} 条"
              f"（该 commit 共 {v['probes']} 条断言，{v['none']} 条硬失败）")

    print(f"\n{'=' * 76}\n逐 commit\n{'=' * 76}")
    for r in rows:
        if "probes" in r and "orig" not in r:
            print(f"  {r['sha']}  断言{r['probes']:>3}  none {r['none']:>3}  "
                  f"mismatch {r['mismatch']:>2}  ok {r['ok']:>3}  "
                  f"鉴别力 {r['disc_before']}→{r['disc_after']}  "
                  f"{r.get('subject', '')[:34]}")
        elif "note" in r:
            print(f"  {r['sha']}  （{r['note']}）")

    if args.out:
        Path(args.out).write_text(
            json.dumps({"tally": tally, "stats": st, "per_commit": per_commit,
                        "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\n明细已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
