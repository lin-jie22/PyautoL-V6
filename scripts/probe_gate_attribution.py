#!/usr/bin/env python3
"""⛔⛔ **门归因普查** —— README 166：把 165 那个 n=1 手工归因做成可复现的。

═══ 它回答什么 ═══

165 结束时有两个"0"没法解释，只能手工看一个 commit：

    more-itertools 端到端 6 个 commit：F2P = 0，修好 = 0/6

按第 25 条，一个"0"有两种含义：**真的没有** vs **量具瞎了**。165 已经抓到
过一次后者（69 条假 F2P 是 tuple/list 降级造的）。所以本脚本不问"是不是 0"，
而是把**每个 commit 里 PR 自己新增的每一条断言**，逐个落到**恰好一格门**：

    ── 抽取层（纯 AST，aether/extract.py）──
    form            判据形态不支持（assertTrue / assertIn / assert a != b …）
    unserializable  期望值不可 JSON（沙箱走 JSON，结构性限制）
    tuple           期望是 tuple（JSON 往返降级成 list ⇒ 恒失败）
    unstable        时间/随机依赖 ⇒ 不可复现
    ── 沙箱层（真跑，复用 probe_auto_unlock.auto_extract 的三验）──
    ctx             在**参照实现**上就跑不过 / 跑不稳 / 与写死期望不符
    P2P             参照实现过、缺陷版本也过 ⇒ **无鉴别力**
    F2P             参照实现过、缺陷版本挂 ⇒ **真有鉴别力** ✅

═══ ⛔⛔ 三条不许违反的纪律 ═══

  1. **只统计 PR 自己新增的断言**。统计整个测试文件的门分布毫无意义 ——
     那测的是"这个仓库的测试长什么样"，不是"修这个 bug 需要什么判据"。
     ⇒ 新增 = fixed 版测试文件里有、父提交里没有的 `(函数, 语句)` 多重集差。
  2. **三验不许自带第二份**。直接复用 `auto_extract(detail=...)` 的逐条裁决
     —— 165 的假 F2P 就是两套口径的产物（一处比 literal_eval、一处比 JSON
     往返值）。本脚本只负责把裁决**对回**测试源码，不重新判一次。
  3. **每一格都要能被反证**。0 条必须区分"真的没有"与"扫不到"（第 25 条）
     ⇒ 契约里对每一格都做了注入反证（`tests/contract_gate_attribution_test.py`）。

跑法：
    python scripts/probe_gate_attribution.py --repo /tmp/realrepo/more-itertools \
        --subdir more_itertools/ --limit 10 --out /tmp/gate_mi.json
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import run_in_sandbox                 # noqa: E402
from aether.extract import (                                  # noqa: E402
    GATE_CRITERION, GATE_FORM, GATE_ORDER, GATE_TUPLE,
    GATE_UNSERIALIZABLE, GATE_UNSTABLE, _iter_all_probes,
    attribute_statements, target_module)

_p = ROOT / "scripts" / "probe_alias_demote.py"
_sp = importlib.util.spec_from_file_location("pad", _p)
_pad = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(_pad)
run_all = _pad.run_all
select_commits = _pad.select_commits
_git = _pad._git

_pp = ROOT / "scripts" / "probe_package_load.py"
_spp = importlib.util.spec_from_file_location("ppl", _pp)
_ppl = importlib.util.module_from_spec(_spp)
_spp.loader.exec_module(_ppl)
pkg_context = _ppl.pkg_context

_pa = ROOT / "scripts" / "probe_auto_unlock.py"
_spa = importlib.util.spec_from_file_location("pau", _pa)
_pau = importlib.util.module_from_spec(_spa)
_spa.loader.exec_module(_pau)
auto_extract = _pau.auto_extract


#: 沙箱一侧的三格（抽取层之外）。⛔ 与抽取层的格**互斥**：一条断言只能落一格。
SAND_GATES = ("F2P", "P2P", "ctx")
ALL_GATES = GATE_ORDER + SAND_GATES


def new_statements(repo: Path, sha: str, testfs: list):
    """⛔ PR **自己新增**的断言语句（`(函数名, 语句)` 的多重集）。

    ⛔ 为什么按 `(fn, stmt)` 而不是只按 `stmt`：`assertEqual(a, b)` 这种写法
    在不同测试函数里重名率极高，只按语句文本会把"别处早就有的断言"误算成
    新增 ⇒ 把仓库的存量分布当成 PR 的增量，读数就废了。

    ⛔ 用**多重集**（Counter）不是集合：同一个函数里写两条一模一样的断言是
    合法的，按集合去重会少算。
    """
    new: collections.Counter = collections.Counter()
    old: collections.Counter = collections.Counter()

    def keys(src: str):
        return ((p["fn"], p["stmt"]) for p in _iter_all_probes(src))

    for tf in testfs:
        fx = _git(repo, "show", f"{sha}:{tf}")
        bs = _git(repo, "show", f"{sha}^:{tf}")
        if fx:
            new.update(keys(fx))
        if bs:
            old.update(keys(bs))
    diff = new - old
    return {k for k, v in diff.items() if v > 0}


def attribute_commit(repo: Path, sha: str, src: str, testfs: list,
                     pkg: dict | None = None, timeout: int = 20):
    """把**一个 commit** 新增的每条断言落到恰好一格门。"""
    fixed = _git(repo, "show", f"{sha}:{src}")
    buggy = _git(repo, "show", f"{sha}^:{src}")
    if not fixed or not buggy:
        return None
    target = target_module(src)

    #: ① 新增断言（纯 AST，不碰被测代码）
    newkeys = new_statements(repo, sha, testfs)

    #: ② 抽取层归因（纯 AST ⇒ 便宜，先做）
    attr, stats = [], {}
    for tf in testfs:
        tsrc = _git(repo, "show", f"{sha}:{tf}")
        if not tsrc:
            continue
        for r in attribute_statements(tsrc, target, stats=stats):
            if (r["fn"], r["stmt"]) in newkeys:
                attr.append(r)

    #: ③ 沙箱侧：**复用** 165 修好的那版三验（不许自带第二份口径）
    detail: dict = {}
    tests, stx = auto_extract(repo, sha, src, testfs, pkg=pkg, detail=detail)
    verdict: dict = {}
    for row in detail.get("rows", []):
        verdict[row["key"]] = row["verdict"]

    #: ④ 只对"新增断言里过了三验的"在缺陷版本上跑一遍 ⇒ F2P / P2P
    sel_tests, sel_keys = [], []
    for t, k in zip(tests, detail.get("tests_keys", [])):
        if (k[0], k[1]) in newkeys:
            sel_tests.append(t)
            sel_keys.append(k)
    f2p: set = set()
    if sel_tests:
        res = run_in_sandbox(buggy, sel_tests, pkg=pkg,
                             timeout=timeout).results
        for k, r in zip(sel_keys, res):
            if not r.get("passed"):
                f2p.add(k)

    #: ⑤ 归并：抽取层的格 + 沙箱层的格，**一条断言一格**
    rows = []
    for r in attr:
        key = (r["fn"], r["stmt"], r["call"])
        if r["gate"] != GATE_CRITERION:
            rows.append({**r, "gate_final": r["gate"], "verdict": None})
            continue
        v = verdict.get(key)
        if v is None:
            #: 判据在新增集合里，但三验没给它裁决 ⇒ 说明两边**对不上**。
            #: ⛔ 不许静默归到 ctx —— 那是把"量具没跑到"说成"被测不行"。
            rows.append({**r, "gate_final": "unmatched", "verdict": None})
            continue
        if v != "ok":
            rows.append({**r, "gate_final": "ctx", "verdict": v})
            continue
        rows.append({**r, "gate_final": ("F2P" if key in f2p else "P2P"),
                     "verdict": v})
    return {"sha": sha[:9], "src": src, "subject": None,
            "n_new": len(attr), "n_tests_total": len(tests),
            "stats": {k: v for k, v in stats.items() if k != "unmapped_kinds"},
            "extract_stats": stx, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/tmp/realrepo/boltons")
    ap.add_argument("--subdir", default="boltons/")
    ap.add_argument("--limit", default="10")
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", default="20")
    ap.add_argument("--out", default="")
    #: `auto` = 只有非 boltons 的仓库才开整包装载（与 164 的口径一致）
    ap.add_argument("--pkg", default="auto", choices=["auto", "on", "off"])
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    cands = select_commits(repo, int(args.limit), args.subdir)
    if args.only:
        want = set(args.only.split(","))
        cands = [c for c in cands if c[0] in want]
    print(f"候选 commit：{len(cands)}  （{repo.name} / {args.subdir}）\n")

    all_rows: list = []
    per_commit = []
    #: ⛔ 跳过必须**计数并披露**（第 149 条：跳过 ≠ 通过）。10 个候选里
    #:    取不到源码对（父提交里没这个文件）的会被静默丢掉 ⇒ 不披露就会
    #:    把"8 个 commit 的读数"当成"10 个 commit 的读数"。
    n_skip = 0
    for sha, src, testfs, subj in cands:
        pkg = None
        if args.pkg == "on" or (args.pkg == "auto"
                                and args.subdir != "boltons/"):
            pkg = pkg_context(repo, sha, src,
                              _git(repo, "show", f"{sha}:{src}"))
        try:
            got = attribute_commit(repo, sha, src, testfs, pkg=pkg,
                                   timeout=int(args.timeout))
        except Exception as exc:                       # 单个 commit 炸了不连坐
            print(f"  {sha[:9]} 异常：{type(exc).__name__}: {exc}")
            n_skip += 1
            continue
        if got is None:
            n_skip += 1
            continue
        got["subject"] = subj
        all_rows.extend(got["rows"])
        per_commit.append(got)
        cnt = collections.Counter(r["gate_final"] for r in got["rows"])
        print(f"  {sha[:9]} 新增断言 {got['n_new']:>3}  "
              + " ".join(f"{g}={cnt.get(g, 0)}" for g in ALL_GATES
                         if cnt.get(g, 0))
              + (f"  unmatched={cnt.get('unmatched', 0)}"
                 if cnt.get("unmatched", 0) else ""))

    #: ── 汇总 ──
    tot = collections.Counter(r["gate_final"] for r in all_rows)
    print(f"\n═══ 汇总：{len(per_commit)} 个 commit / "
          f"{len(all_rows)} 条新增断言 ═══")
    if n_skip:
        print(f"⛔ **{n_skip} 个候选被跳过**（取不到 fixed/buggy 源码对 或 "
              f"执行异常）⇒ 上面的分母是 {len(per_commit)}，不是 "
              f"{len(per_commit) + n_skip}（未覆盖 ≠ 通过）")
    for g in ALL_GATES + ("unmatched",):
        n = tot.get(g, 0)
        pct = (100.0 * n / len(all_rows)) if all_rows else 0.0
        print(f"  {g:<16} {n:>5}  {pct:5.1f}%")

    print("\n── form 门细分（哪些断言写法被挡在外面）──")
    for k, v in collections.Counter(
            r["attr"] for r in all_rows
            if r["gate_final"] == GATE_FORM).most_common(12):
        print(f"  {k:<24} {v}")

    print("\n── ctx 门细分（参照实现上为什么跑不过）──")
    for k, v in collections.Counter(
            r["verdict"] for r in all_rows
            if r["gate_final"] == "ctx").most_common(8):
        print(f"  {k:<24} {v}")

    n_f2p = tot.get("F2P", 0)
    print(f"\n⛔ **真有鉴别力的新增断言（F2P）：{n_f2p}**")
    print(f"⛔ 有 F2P 的 commit："
          f"{sum(1 for c in per_commit if any(r['gate_final'] == 'F2P' for r in c['rows']))}"
          f"/{len(per_commit)}")

    if args.out:
        out = Path(args.out)
        out.write_text(json.dumps(
            {"repo": str(repo), "subdir": args.subdir,
             "n_commit": len(per_commit), "n_skip": n_skip,
             "n_rows": len(all_rows),
             "totals": dict(tot), "per_commit": per_commit},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n落盘：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
