#!/usr/bin/env python3
"""⛔ **上下文门，按成因分类 + 攻「自指模块别名」这一类** —— README 162。

═══ 起因：一个反向 impossibility ═══

153/157 把上下文门量成了「81.1%（1526/1882）的真实断言单拎出来求值不了」，
但它一直是一个**没有成因分布的总数**：失败就是失败，没人问过「为什么失败」。

本轮动手前先做了一个 hypothes-driven 的最小验证（三条探针跑同一个真实 bug
boltons `766b554`）：

    b2h(1024)                       ⇒ NameError: b2h            （原样抽出）
    strutils.bytes2human(1024)      ⇒ NameError: strutils       （现有内联器的产物）
    bytes2human(1024)               ⇒ fixed '1K' / buggy '1024B' ← 有鉴别力

═══ ⛔⛔ 结论：**现有内联器的方向反了** ═══

`run_in_sandbox(source, tests)` 的 source 是**被测模块源码本体**，沙箱命名
空间里只有该模块内部的名字 —— `strutils` 这个**模块对象本身并不存在**。
而 157 的内联器做的是「把局部别名展开成更外层的作用域名」：

    b2h  ⇒  strutils.bytes2human     ← 引进了一个沙箱里没有的新自由变量

⇒ 它不是"没做"，是"朝着唯一错的方向做"。正确的方向是**降回模块内裸名**
  （因为被测模块本体已经在沙箱里了）。这一类叫 **自指模块别名**：

    测试写  from boltons import strutils;  b2h = strutils.bytes2human
    被测源文件就是 boltons/strutils.py ⇒ 沙箱里等价写法是 bytes2human

它是**纯 AST 可判定**的，不需要模型、不需要跑 pytest、不需要联网。

═══ ⛔ 两条自限（不许为了救回率放宽）═══

  1. **只对被测模块自身降级**。指向别的模块（`d = datetime.date`）一律不动
     ⇒ 否则会在沙箱里凭空变出不存在的名字，等于制造假判据。
  2. 降级产物仍要过 157 那**三道验**：能求值 + 跑两遍一致 + 与测试写死的
     expected 对拍一致。缺一条不采纳（fail-closed）。

═══ 分类口径 ═══

对每条仍然失败的探针，按它**剩下的自由变量**归因（⛔ 归因依据是结构不是猜）：

    demoted     rescued by 自指模块别名降级（本轮攻击的那一类）
    fixture     该名字是测试函数的形参 ⇒ 由 pytest 注入，静态不可解
    rebound     同名在同一作用域被赋了两个不同值 ⇒ 值依赖执行到哪一步
    other       其余（属性根未知 / 内建缺失 / 类属性等）

跑法：
    python scripts/probe_alias_demote.py --repo /tmp/realrepo/boltons --limit 40
    python scripts/probe_alias_demote.py --only 766b554,93185b2,64e6430
"""
from __future__ import annotations

import argparse
import ast
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── ⛔ 规则**只有一份**：全部来自产品模块 `aether/extract.py` ─────────────
#    157 的脚本（`probe_context_gate.py`）自己实现过一遍，本轮没有再抄第三份
#    —— 161 的 C 组契约专门盯的就是「两把尺子漂移」。脚本只负责**量**。
from aether.extract import (  # noqa: E402
    attribute, collect_bindings, demote_binds, demote_expr, free_names,
    func_params, gold_of, has_unstable_call, inline_expr, lit,
    module_aliases, rebound_names, resolve_probe, target_module,
)

_pcg_path = ROOT / "scripts" / "probe_context_gate.py"
_spec = importlib.util.spec_from_file_location("pcg", _pcg_path)
_pcg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pcg)

scoped_assertions = _pcg.scoped_assertions
scoped_raise_probes = _pcg.scoped_raise_probes
run_all = _pcg.run_all


# ───────────────────── ③ 量具自检（第 24 条）───────────────────────────
def selftest() -> bool:
    ok = True

    def chk(label, got, want):
        nonlocal ok
        if got != want:
            ok = False
        flag = "✓" if got == want else "✗"
        print(f"    {flag} 自检{label}: 期望 {want!r:<34} 实得 {got!r}")

    t = ast.parse("from boltons import strutils\nimport boltons.setutils\n"
                  "import boltons.timeutils as tu\n")
    al = module_aliases(t)
    chk("A from-import 限定名", al.get("strutils"), "boltons.strutils")
    chk("B 二级路径根名", al.get("boltons"), "boltons")
    chk("C as-import", al.get("tu"), "boltons.timeutils")

    src = ("from boltons import strutils\n"
           "def t():\n    b2h = strutils.bytes2human\n    h = b2h\n")
    tree = ast.parse(src)
    al = module_aliases(tree)
    binds = collect_bindings(src)["t"]
    d = demote_binds(binds, "boltons.strutils", al)
    chk("D 别名降级", ast.unparse(d["b2h"]), "bytes2human")
    #: ⛔ 职责切分：**链式**不在降级器里做 —— 降级器只管"mod.attr ⇒ attr"，
    #:   `h = b2h` 这种"别名指向别名"由 `inline_expr` 的递归展开负责。
    #:   第一版把两者混在 demote_binds 里 ⇒ 自检 E 失败（分段 bug）。
    chk("E 链式交给内联", ast.unparse(d["h"]), "b2h")
    chk("E2 端到端链式", inline_expr("h(1024)", d), "bytes2human(1024)")

    # ⛔ 自限 1 的反证：别的模块不许降级
    src2 = ("import datetime\n"
            "def t():\n    dd = datetime.date\n")
    al2 = module_aliases(ast.parse(src2))
    b2 = collect_bindings(src2)["t"]
    d2 = demote_binds(b2, "boltons.strutils", al2)
    chk("F 他模块不降级", ast.unparse(d2["dd"]), "datetime.date")

    # fixture / rebound 归因
    src3 = ("def test_x(tmpdir):\n"
            "    s = 1\n    s = 2\n"
            "    assert f(tmpdir, s) == 1\n")
    tr3 = ast.parse(src3)
    chk("G fixture 参数", func_params(tr3).get("test_x"), {"tmpdir"})
    chk("H 多值重绑", rebound_names(tr3).get("test_x"), {"s"})
    chk("I fixture 优先于 rebound",
        attribute({"tmpdir", "s"}, "test_x", func_params(tr3),
                  rebound_names(tr3)), "fixture")
    chk("J 纯自由变量", attribute({"zzz"}, "test_x", func_params(tr3),
                                 dict()), "other")
    # 自由变量提取
    chk("K 自由变量", sorted(free_names("a.b(c) + d")), ["a", "c", "d"])
    chk("L 已绑定不算", sorted(free_names("(lambda x: x + y)(1)")), ["y"])
    # ⛔ A 形态（直写前缀）—— 第一版漏掉它，56 条全落 other
    al3 = module_aliases(ast.parse("from boltons import strutils\n"))
    chk("M 直写前缀降级",
        demote_expr("strutils.strip_ansi('x')", "boltons.strutils", al3),
        "strip_ansi('x')")
    chk("N 他模块的直写前缀不动",
        demote_expr("boltons.timeutils.daterange(a, b)",
                    "boltons.strutils", al3), None)
    chk("O 组合 A+B",
        inline_expr("b2h(1024)", demote_binds(
            collect_bindings("from boltons import strutils\n"
                             "def t():\n    b2h = strutils.bytes2human\n")["t"],
            "boltons.strutils", al3)),
        "bytes2human(1024)")
    return ok


# ───────────────────── ④ 主流程 ────────────────────────────────────────
def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(repo),
                       capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


#: ⛔ 候选筛选词表只此一份（跨仓库可比性的前提）：162/163 是 boltons，
#:    164 起要在 attrs / more-itertools 上跑同一套装置 ⇒ 换仓库时**只改
#:    `subdir`**，词表与"同 commit 必须动测试"这两条不许跟着变。
FIX_WORDS = ("fix", "resolve", "handle", "correct", "bug", "repair",
             "regression")


def select_commits(repo: Path, limit: int, subdir: str = "boltons/"):
    """挑「修了 `subdir` 下某个 .py、且同 commit 动了 `tests/`」的 commit。

    ⛔ `subdir` 默认 `boltons/` ⇒ 162/163 的读数**一字不变**。
    ⛔ 换仓库时只传 `subdir`，不许为了挑到更多候选去改词表 —— 那会让
       跨仓库的覆盖率读起来像变好了，实际是选取口径变松了。
    """
    out = []
    for sha in _git(repo, "log", "--format=%H").split():
        subj = _git(repo, "log", "-1", "--format=%s", sha).strip().lower()
        if not any(w in subj for w in FIX_WORDS):
            continue
        files = [l.strip() for l in
                 _git(repo, "show", "--name-only", "--format=", sha).splitlines()
                 if l.strip()]
        srcs = [f for f in files if f.startswith(subdir)
                and f.endswith(".py") and "__init__" not in f]
        tests = [f for f in files if f.startswith("tests/")
                 and f.endswith(".py")]
        if len(srcs) != 1 or not tests:
            continue
        out.append((sha[:7], srcs[0], tests, subj))
        if len(out) >= limit:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/tmp/realrepo/boltons")
    ap.add_argument("--limit", default="40")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()

    _ok = selftest()
    print(f"量具自检：{'✅ 通过' if _ok else '❌ 未通过'}")
    if not _ok:
        print("  ⇒ 降级器/归因函数本身不可信，停机")
        return 1

    cands = select_commits(repo, int(args.limit))
    if args.only:
        want = set(args.only.split(","))
        cands = [c for c in cands if c[0] in want]
    print(f"\n候选 commit：{len(cands)}")

    st = {"total": 0, "failed": 0,
          "cause": {}, "disc_by_cause": {}}
    #: ⛔ 两个机制**各一套**完整计数（缺一项就无法公平对照）
    for tag in ("old", "new"):
        st.update({f"{tag}_try": 0, f"{tag}_ok": 0, f"{tag}_gold": 0,
                   f"{tag}_nogold": 0, f"{tag}_raises": 0, f"{tag}_disc": 0,
                   f"rejected_time_{tag}": 0})
    st.update({"rejected_gold": 0, "unstable": 0})
    per_commit: dict = {}
    samples: list = []

    for sha, src, testfs, subj in cands:
        fixed = _git(repo, "show", f"{sha}:{src}")
        buggy = _git(repo, "show", f"{sha}^:{src}")
        if not fixed or not buggy or fixed == buggy:
            continue
        target = target_module(src)

        probes, envs = [], {}
        for tf in testfs:
            tsrc = _git(repo, "show", f"{sha}:{tf}")
            if not tsrc:
                continue
            tree = ast.parse(tsrc)
            al = module_aliases(tree)
            params = func_params(tree)
            rebound = rebound_names(tree)
            for act, exp, fn in scoped_assertions(tsrc):
                probes.append({"kind": "expect", "expr": act, "gold": exp,
                               "fn": fn, "tf": tf, "params": params,
                               "rebound": rebound, "tree": tree, "aliases": al})
            for act, exc, fn in scoped_raise_probes(tsrc):
                probes.append({"kind": "raises", "expr": act, "exc": exc,
                               "fn": fn, "tf": tf, "params": params,
                               "rebound": rebound, "tree": tree, "aliases": al})
            for sc, b in collect_bindings(tsrc).items():
                envs[f"{tf}::{sc}"] = b

        if not probes:
            continue

        #: ⛔ **每个 commit 只起固定几次沙箱**（不是每条探针一次）。
        #:   第一版写成"每条探针单独 run_all" ⇒ 一个 commit 要 4×N 次进程，
        #:   全量普查根本跑不完。这里按批来：批量有自己的坑 —— **不同探针
        #:   可能有相同的 call 文本**（不同作用域同名变量）⇒ key 必须带序号
        #:   （157 踩过：读数凭空少一半）。
        disc_before = 0
        fails = []
        items = [(f"a{i}", p["expr"], p.get("exc"))
                 for i, p in enumerate(probes)]
        g1 = run_all(fixed, items)
        if "__import_error__" in g1:
            continue
        gb0 = run_all(buggy, items)
        for i, p in enumerate(probes):
            st["total"] += 1
            r = g1.get(f"a{i}")
            if p["kind"] == "raises":
                good = bool(r and r["passed"])
                got_s = r["passed"] if r else None
            else:
                got_s = (r or {}).get("got")
                gold = gold_of(p["gold"])
                good = bool(got_s is not None and gold is not None
                            and lit(got_s)[1] and lit(got_s)[0] == gold)
            if good:
                rb = gb0.get(f"a{i}")
                if p["kind"] == "raises":
                    if rb and not rb["passed"]:
                        disc_before += 1
                elif rb and rb.get("got") != got_s:
                    disc_before += 1
            else:
                fails.append((i, p))
        st["failed"] += len(fails)

        #: ⛔⛔ **候选按「去重后的表达式」统一评测，两个机制走完全同一套验收。**
        #:   第一版给老内联器只量了"能不能求值"，给新降级器量了"过不过金标准"
        #:   ⇒ 第二节的两行数字口径不同，不能比。⇒ 改成：同一表达式只跑一次、
        #:   只计一次，标注它来自哪个机制（old / new / both）。
        cand: dict = {}          #: (i, expr) -> {"p":p, "src": set()}
        for i, p in fails:
            binds = dict(envs.get(f"{p['tf']}::<module>", {}))
            binds.update(envs.get(f"{p['tf']}::{p['fn']}", {}))
            old = inline_expr(p["expr"], binds)
            new = resolve_probe(p["expr"], binds, target, p["aliases"])
            if old == p["expr"]:
                old = None
            if new == p["expr"]:
                new = None
            if not old and not new:
                cause = attribute(free_names(p["expr"]) - set(binds),
                                  p["fn"], p["params"], p["rebound"])
                st["cause"][cause] = st["cause"].get(cause, 0) + 1
                continue
            for e, tag in ((old, "old"), (new, "new")):
                if not e:
                    continue
                if has_unstable_call(e):
                    st[f"rejected_time_{tag}"] += 1
                    continue
                key = (i, e)
                cand.setdefault(key, {"p": p, "src": set()})["src"].add(tag)

        keys = list(cand)
        items_for = lambda tg: [(f"{tg}{j}", k[1], cand[k]["p"].get("exc"))  # noqa: E731
                                for j, k in enumerate(keys)]
        st["old_try"] += sum(1 for k in keys if "old" in cand[k]["src"])
        st["new_try"] += sum(1 for k in keys if "new" in cand[k]["src"])
        g2 = run_all(fixed, items_for("b")) if keys else {}
        g2b = run_all(fixed, items_for("c")) if keys else {}
        gb = run_all(buggy, items_for("d")) if keys else {}

        def tally_src(j, tag, key):
            st[f"{tag}_ok"] += 1
            p = cand[keys[j]]["p"]
            if p["kind"] == "raises":
                st[f"{tag}_raises"] += 1
            else:
                st[f"{tag}_{'gold' if gold_of(p['gold']) is not None else 'nogold'}"] += 1
            if disc[j]:
                st[f"{tag}_disc"] += 1

        disc = [False] * len(keys)
        disc_after = disc_before
        for j, k in enumerate(keys):
            p = cand[k]["p"]
            rf = g2.get(f"b{j}")
            rb = gb.get(f"d{j}")
            okv = False
            if rf:
                if p["kind"] == "raises":
                    okv = bool(rf["passed"])
                elif rf.get("got") is not None:
                    rf2 = g2b.get(f"c{j}")
                    if not rf2 or rf2["got"] != rf["got"]:
                        st["unstable"] += 1
                    else:
                        g = gold_of(p["gold"])
                        if g is not None and lit(rf["got"])[1] \
                                and lit(rf["got"])[0] != g:
                            st["rejected_gold"] += 1
                        else:
                            okv = True
            if not okv:
                cause = attribute(free_names(k[1]), p["fn"], p["params"],
                                  p["rebound"])
                st["cause"][cause] = st["cause"].get(cause, 0) + 1
                continue
            disc[j] = (bool(rb and not rb["passed"]) if p["kind"] == "raises"
                       else bool(rb and rb.get("got") != rf.get("got")))
            if disc[j]:
                disc_after += 1
                samples.append({"sha": sha, "orig": p["expr"], "new": k[1],
                                "gold": p.get("gold") or p.get("exc"),
                                "got": rf.get("got"),
                                "buggy": (rb or {}).get("got")})
            for tag in sorted(cand[k]["src"]):
                tally_src(j, tag, k)

        per_commit[sha] = {"before": disc_before, "after": disc_after,
                           "probes": len(probes), "failed": len(fails),
                           "subject": subj[:44]}

    T = max(st["total"], 1)
    F = max(st["failed"], 1)
    print(f"\n{'=' * 74}\n一、总体（共 {st['total']} 条真实断言）\n{'=' * 74}")
    print(f"  能直接用的（=== 158 的口径）：{st['total'] - st['failed']}"
          f"  ({(st['total'] - st['failed']) / T:.1%})")
    print(f"  上下文门拦掉的：              {st['failed']}  ({st['failed'] / T:.1%})")

    O, N = max(st["old_try"], 1), max(st["new_try"], 1)
    print(f"\n{'=' * 74}\n二、⛔⛔ 公平对照：两个机制走**同一套**验收\n{'=' * 74}")
    print(f"  {'':<22}{'尝试':>6}{'可求值':>8}{'过金标准':>10}"
          f"{'有鉴别力':>10}{'拒收':>6}")
    for tag, lab in (("old", "157 内联器(往外展)"), ("new", "162 降级(降回裸名)")):
        print(f"  {lab:<22}{st[f'{tag}_try']:>6}{st[f'{tag}_ok']:>8}"
              f"{st[f'{tag}_gold']:>10}{st[f'{tag}_disc']:>10}"
              f"{st[f'rejected_time_{tag}']:>6}")
    print(f"\n  ⛔ 鉴别力才是决定性的量（能求值≠能用，相差 "
          f"{max(st['new_ok'], 1) // max(st['new_disc'], 1)} 倍）："
          f"  157 = {st['old_disc']}，162 = {st['new_disc']}")
    print(f"  ⛔ 两机制的产物有大量重叠 —— 「谁都能产出」的表达式只测一次、"
          f"只计一次（标注 both），否则比值无意义。")

    print(f"\n{'=' * 74}\n三、降级救回（162 侧，分档，不许只报总数）\n{'=' * 74}")
    print(f"  返回值类·过金标准对拍：{st['new_gold']:>4}"
          f"   其中真有鉴别力：{st['new_disc']}")
    print(f"  返回值类·无金标准：    {st['new_nogold']:>4}   ⛔ 语义未证，不算收益")
    print(f"  期望异常类：           {st['new_raises']:>4}")
    print(f"  拒收：时间/随机 {st['rejected_time_new']}、"
          f"与金标准不符 {st['rejected_gold']}、两次不一致 {st['unstable']}")

    print(f"\n{'=' * 74}\n四、⛔ 剩下拦不住的，按成因分（{sum(st['cause'].values())} 条）\n"
          f"{'=' * 74}")
    for k, v in sorted(st["cause"].items(), key=lambda x: -x[1]):
        print(f"  {k:<10} {v:>5}  ({v / F:.1%} of failed)")
    print("    demote  = 本轮救回的（自指模块别名）｜fixture = pytest 注入，"
          "静态不可解")
    print("    rebound = 同作用域多值重绑（多步序列）｜other = 其余")

    gained = [(s, v) for s, v in per_commit.items()
              if v["before"] == 0 and v["after"] > 0]
    grew = [(s, v) for s, v in per_commit.items() if v["after"] > v["before"]]
    print(f"\n{'=' * 74}\n五、commit 级（决定能不能多修真实 bug 的量）\n{'=' * 74}")
    print(f"  候选 commit：{len(per_commit)}")
    print(f"  降级前 ≥1 条鉴别力判据："
          f"{sum(1 for v in per_commit.values() if v['before'] > 0)}")
    print(f"  ⇒ 降级后 0 → ≥1 条（**新变成可表达**）：{len(gained)}")
    print(f"  ⇒ 判据变多的（含本来就有）：{len(grew)}")
    for s, v in gained[:15]:
        print(f"      · {s}  0 → {v['after']} 条（共 {v['probes']} 断言、"
              f"{v['failed']} 条失败）  {v['subject']}")

    if samples:
        print(f"\n{'=' * 74}\n六、样例（最多 12 条）\n{'=' * 74}")
        for s in samples[:12]:
            print(f"  {s['sha']}  {s['orig'][:40]:<40} → {s['new'][:44]:<44} "
                  f"fixed={s['got']} buggy={s['buggy']}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"stats": st, "per_commit": per_commit, "samples": samples},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
