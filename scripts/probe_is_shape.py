#!/usr/bin/env python3
"""r107 / ① 扩张判据形态：`is` 27 条的收益上界普查（纯只读取证）。

═══ 起点 ═══
179 把 177 遗留的 119 条 form 账做了**静态描述性分类**，其中 `is` 是最大格
（27 条），并判「需要身份判据、不在已宣布四种形态里」。183 硬结论是
「要测这 119 条下游必须先扩张判据形态 ⇒ 拍板 ①」；用户于 r107 拍板 ① 的
最小切片（只加 `is` 一种形态）。

⛔⛔ **本轮取证改写了这个前提**（详见 README 186）：

1. 产品 `aether/extract.py:61-84` 只把 `assertEqual` 系列收进 `_UNITTEST_SAME`，
   `_bare_assert_parts` 只认 `a == b`；`assert X is None` 被记成
   `mapped=False`（`call=""`）。但**产品沙箱的 truthy 分支**
   （`environment.py:295-299`，不带 `expect` ⇒ `bool(got)`）本来就能判
   `X is None` 这种整条 bool 表达式 —— 合成样本 4/4 判对。
   ⇒ 拒收的是「拆成 (实际, 期望) 二元组」这个动作，不是判据语义；
   「需要身份判据」是把形态表窄当成了判据层窄（第 66 条：判天花板先查
   判据自锁）。⇒ **不改产品、不新增第三种比较语义**。

2. 把 27 条整条表达式直接当探针喂同源沙箱：
   `26 raise/raise + 1 behavior/behavior`，**自动路径 F2P = 0**。
   26 条 `raise` 末行全是测试函数局部名字（`_config` / `f` / `attr` /
   `prefix` / `e_omd` / `alias` / `Child` / `ox` / `meth`）与 4 条
   `function object is not subscriptable` ⇒ 沙箱只装载单文件源码，
   这是 183 预警的**构造必然**（第 122 条），不是证据、不是收益。

3. 对 5 条能把「作者测试里的局部状态」展开成单表达式的，手写 oracle
   （每条逐字写明语义来源，171 教训）+ `oracle_bad` 反证：
   参照实现 5/5 验绿、bad 5/5 转红、**真 F2P 4/5**。
   ⛔ 但 4 条 F2P 的 buggy 侧全是 `raise`（crash 弱题，不是 mismatch
   强题），且判据是**人工转写**的 ⇒ 不是自动端到端修复。

═══ 三本账（绝不合并成布尔，第 ④ 条契约）═══
  A 自动路径账  27 条：整条 bool 表达式探针，两侧真跑
  B oracle 账    5 条：手写 oracle + bad 反证 + 两侧真跑
  C 形态事实账   4 条：产品 truthy 分支合成样本（同源产品沙箱）

⛔ 不回写任何上游存档、不改 `aether/`、不改判据形态、不改安全边界。
⛔ `reach` 是事后 oracle，绝不当 predictor（179 / 177 纪律）。
⛔ 不宣布「扩张判据形态无用」，也不宣布「有效」：B 账测出了 4 条判据，
  只是门不在判据层、而在**装载门**（④ 内联器自限 / ② 多步状态序列）。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aether.environment import run_in_sandbox                    # noqa: E402
from probe_alias_demote import _git                              # noqa: E402
from probe_ctx_downstream import sandbox_read                    # noqa: E402
from probe_package_load import pkg_context                       # noqa: E402
from realrepo_baseline import require_baseline                   # noqa: E402

SOURCE_FORM = ROOT / "scripts" / "form_shape_attribution.json"
SOURCE_GATE = ROOT / "scripts" / "criteria_gate_census.json"
SOURCE_EXTRACT = ROOT / "scripts" / "extract_downstream.json"
OUT = ROOT / "scripts" / "is_shape_census.json"

#: 口径签名含**尺子自身**（174 教训）。全是**只读**引用，不写回。
CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    "aether/environment.py",
    "scripts/probe_form_shape_attribution.py",
    "scripts/probe_extract_downstream.py",
    "scripts/probe_ctx_downstream.py",
    "scripts/probe_package_load.py",
    "scripts/form_shape_attribution.json",
    "scripts/criteria_gate_census.json",
    "scripts/extract_downstream.json",
    "scripts/realrepo_baseline.json",
    "scripts/probe_is_shape.py",
)


def sha256_file(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "__MISSING__"
    return hashlib.sha256(p.read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {f: sha256_file(f) for f in CALIBER_FILES}


def _sources(repo: str, sha: str, src: str,
             cache: Dict[Tuple[str, str], Tuple[str, str, Any]]) -> Tuple[str, str, Any]:
    key = (repo, sha)
    if key not in cache:
        base = Path("/tmp/realrepo") / repo
        fixed = _git(base, "show", "%s:%s" % (sha, src))
        buggy = _git(base, "show", "%s^:%s" % (sha, src))
        pkg: Optional[Dict[str, Any]] = None
        if repo != "boltons":
            pkg = pkg_context(base, sha, src, fixed)
        cache[key] = (fixed, buggy, pkg)
    return cache[key]


def to_probe(expr: str, attr: str) -> Optional[str]:
    """整条 bool 表达式探针：`self.assertIs(A, B)` → `A is B`；裸 assert 原样。

    ⛔ 只做**同形改写**，不引入任何新比较语义、不另写词表（第 68 条）。
    """
    if attr == "assertIs":
        inner = expr[expr.find("(") + 1:expr.rfind(")")]
        depth, cut = 0, -1
        for i, ch in enumerate(inner):
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "," and depth == 0:
                cut = i
                break
        if cut < 0:
            return None
        return "%s is %s" % (inner[:cut].strip(), inner[cut + 1:].strip())
    return expr


#: ⛔⛔ 手写 oracle：**每条都写明语义来源**（作者新增测试的上文），
#:    绝不临场编期望（171 的 `oracle_bad` 教训：第一版 5 条错 2 条）。
#:    `bad` 是把结论反转/换成不相干对象 ⇒ 参照实现必须转红。
ORACLES: List[Dict[str, str]] = [
    {
        "id": "B1", "repo": "boltons", "sha": "8177b93",
        "src": "boltons/dictutils.py",
        "good": "(e := OMD()).addlist(1, []) is None and e.popall(1, None) is None",
        "bad": "(e := OMD()).addlist(1, []) is None and e.popall(1, None) is not None",
        "origin": "8177b93 test_setdefault 新增: e_omd = OMD(); "
                  "e_omd.addlist(1, []); assert e_omd.popall(1, None) is None",
    },
    {
        "id": "B2", "repo": "boltons", "sha": "8177b93",
        "src": "boltons/dictutils.py",
        "good": "OMD().popall('', None) is None",
        "bad": "OMD().popall('', None) is not None",
        "origin": "8177b93 test_pop_all 新增断言 `assert OMD().popall('', None) is None`"
                  "（自足表达式，无需展开测试局部状态）；该 commit 的修复点在 "
                  "poplast/addlist 而非 popall，故两侧同值、不是 F2P",
    },
    {
        "id": "M1", "repo": "more-itertools", "sha": "37bb3ad",
        "src": "more_itertools/more.py",
        "good": "peekable[str].__origin__ is peekable",
        "bad": "peekable[str].__origin__ is not peekable",
        "origin": "37bb3ad test_class_getitem: alias = mi.peekable[str]; "
                  "self.assertIs(alias.__origin__, mi.peekable)",
    },
    {
        "id": "M2", "repo": "more-itertools", "sha": "ddbbea2",
        "src": "more_itertools/more.py",
        "good": "(it := [[[1], [2]], [[1], [2], [3]], [[1], [2], [4]]]) "
                "is not None and (p := list(longest_common_prefix(it)))[0] is it[0][0]",
        "bad": "(it := [[[1], [2]], [[1], [2], [3]], [[1], [2], [4]]]) "
               "is not None and (p := list(longest_common_prefix(it)))[0] is it[1][0]",
        "origin": "ddbbea2 test_prefix_contains_elements_of_the_first_iterable: "
                  "prefix = list(mi.longest_common_prefix(iterables)); "
                  "assertIs(prefix[0], iterables[0][0])",
    },
    {
        "id": "M3", "repo": "more-itertools", "sha": "ddbbea2",
        "src": "more_itertools/more.py",
        "good": "(it := [[[1], [2]], [[1], [2], [3]], [[1], [2], [4]]]) "
                "is not None and (p := list(longest_common_prefix(it)))[1] is it[0][1]",
        "bad": "(it := [[[1], [2]], [[1], [2], [3]], [[1], [2], [4]]]) "
               "is not None and (p := list(longest_common_prefix(it)))[1] is it[1][1]",
        "origin": "ddbbea2 test_prefix_contains_elements_of_the_first_iterable 第 2 条："
                  "iterables = [[[1], [2]], [[1], [2], [3]], [[1], [2], [4]]]; "
                  "prefix = list(mi.longest_common_prefix(iterables)); "
                  "assertIs(prefix[1], iterables[0][1])",
    },
]


def shape_fact(timeout: float) -> Dict[str, Any]:
    """C 账：产品 truthy 分支本来就能判 `is` 表达式（同源产品沙箱）。"""
    src = "X = None\nY = 1\n"
    tests = [
        {"name": "a", "call": "X is None", "want": True},
        {"name": "b", "call": "X is Y", "want": False},
        {"name": "c", "call": "1 is True", "want": False},
        {"name": "d", "call": "None is None", "want": True},
    ]
    ex = run_in_sandbox(src, [{"name": t["name"], "call": t["call"]}
                              for t in tests], timeout=timeout)
    rows = []
    for t, r in zip(tests, ex.results):
        rows.append({"expr": t["call"], "want": t["want"],
                     "passed": bool(r.get("passed")), "why": str(r.get("why")),
                     "evidence": str(r.get("evidence") or "")[:160]})
    return {
        "rows": rows,
        "import_error": str(ex.import_error or "")[:160],
        "n_correct": sum(1 for x in rows if x["passed"] == x["want"]),
        "n_rows": len(rows),
        "note": "不带 expect ⇒ environment.py:295-299 truthy 分支 bool(got)；"
                "证明判据层无需新增第三种比较语义",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-baseline-gate", action="store_true",
                    help="仅供离线反证；正式跑必须验锚")
    args = ap.parse_args()

    health: Dict[str, Any]
    if args.no_baseline_gate:
        health = {"ok": None, "skipped": True}
    else:
        health = require_baseline()
        if not health.get("ok"):
            print("BASELINE_GATE_FAIL", health.get("diffs"))
            raise SystemExit(2)

    form = json.loads(SOURCE_FORM.read_text(encoding="utf-8"))
    gate = json.loads(SOURCE_GATE.read_text(encoding="utf-8"))
    ext = json.loads(SOURCE_EXTRACT.read_text(encoding="utf-8"))
    src_of = {p["sha"]: p.get("src") for p in gate["per_commit"]}
    diag_of = {(r["repo"], r["sha"], r["stmt"]): r
               for r in ext["form_diagnostic_rows"]}

    is_rows = [r for r in form["rows"] if r.get("shape") == "is"]
    cache: Dict[Tuple[str, str], Tuple[str, str, Any]] = {}

    # ─── A 账：自动路径（整条 bool 表达式探针，不加工） ───
    auto: List[Dict[str, Any]] = []
    for i, r in enumerate(is_rows, 1):
        key = (r["repo"], r["sha"], r["stmt"])
        diag = diag_of.get(key, {})
        probe = to_probe(r["expr"], r.get("attr_product") or "")
        sha, src = r["sha"], src_of.get(r["sha"])
        fixed, buggy, pkg = _sources(r["repo"], sha, src, cache)
        fr, _ = sandbox_read(fixed, [{"name": "t0", "call": probe}], args.timeout, pkg)
        br, _ = sandbox_read(buggy, [{"name": "t0", "call": probe}], args.timeout, pkg)
        f, b = fr[0], br[0]
        auto.append({
            "i": i, "repo": r["repo"], "sha": sha, "fn": r.get("fn"),
            "stmt": r["stmt"], "expr": r["expr"], "probe": probe,
            "attr": r.get("attr_product"),
            "shape": r.get("shape"), "reach": r.get("reach"),
            "v175": r.get("v175"), "verdict_177": r.get("verdict_177"),
            "call_from_177": diag.get("call", "__MISSING__"),
            "has_raw_expr_177": diag.get("has_raw_expr", "__MISSING__"),
            "different_caliber_177": diag.get("different_caliber", "__MISSING__"),
            "why_fixed": f["why"], "why_buggy": b["why"],
            "passed_fixed": bool(f["passed"]), "passed_buggy": bool(b["passed"]),
            "evidence_fixed": f["evidence"][:160],
            "evidence_buggy": b["evidence"][:160],
            "f2p_auto": bool(f["passed"]) and not bool(b["passed"]),
        })

    # ─── B 账：手写 oracle（参照实现验绿 + bad 反证 + 两侧真跑） ───
    orc: List[Dict[str, Any]] = []
    for o in ORACLES:
        fixed, buggy, pkg = _sources(o["repo"], o["sha"], o["src"], cache)

        def run(source: str, call: str) -> Dict[str, Any]:
            rows, _ = sandbox_read(source, [{"name": "t0", "call": call}],
                                   args.timeout, pkg)
            r = rows[0]
            return {"passed": bool(r["passed"]), "why": r["why"],
                    "evidence": r["evidence"][:160]}

        fg, bg, fb = run(fixed, o["good"]), run(buggy, o["good"]), run(fixed, o["bad"])
        orc.append({
            "id": o["id"], "repo": o["repo"], "sha": o["sha"], "src": o["src"],
            "origin": o["origin"], "oracle_good": o["good"], "oracle_bad": o["bad"],
            "fixed_good": fg, "buggy_good": bg, "fixed_bad": fb,
            "oracle_green": bool(fg["passed"]),
            "oracle_bad_red": not bool(fb["passed"]),
            "f2p_oracle": bool(fg["passed"]) and not bool(bg["passed"]),
            "f2p_is_crash_weak": (bool(fg["passed"]) and not bool(bg["passed"])
                                  and bg["why"] == "raise"),
        })

    fact = shape_fact(args.timeout)

    # ─── 派生读数（全部从 rows 派生，不藏进程内状态） ───
    by_why = dict(collections.Counter(
        "%s/%s" % (x["why_fixed"], x["why_buggy"]) for x in auto))
    by_pair = dict(collections.Counter(
        "%s/%s" % (x["passed_fixed"], x["passed_buggy"]) for x in auto))
    raise_tail = dict(collections.Counter(
        x["evidence_fixed"].split(":")[0][:70] for x in auto
        if x["why_fixed"] == "raise"))
    n_f2p_auto = sum(1 for x in auto if x["f2p_auto"])
    n_f2p_orc = sum(1 for x in orc if x["f2p_oracle"])
    n_weak = sum(1 for x in orc if x["f2p_is_crash_weak"])

    closure = {
        "eq_denominator": len(auto) == 27,
        "eq_form_is": sum(1 for r in form["rows"] if r.get("shape") == "is") == 27,
        "eq_why_sum": sum(by_why.values()) == len(auto),
        "eq_pair_sum": sum(by_pair.values()) == len(auto),
        "eq_all_call_empty": all(x["call_from_177"] == "" for x in auto),
        "eq_all_has_raw_expr": all(x["has_raw_expr_177"] is True for x in auto),
        "eq_all_reach_X": all(x["reach"] == "X" for x in auto),
        "eq_all_unmeasurable": all(x["verdict_177"] == "unmeasurable" for x in auto),
        "eq_auto_f2p_zero": n_f2p_auto == 0,
        "eq_oracle_green": sum(1 for x in orc if x["oracle_green"]) == len(orc),
        "eq_oracle_bad_red": sum(1 for x in orc if x["oracle_bad_red"]) == len(orc),
        "eq_oracle_n": len(orc) == 5,
        "eq_shape_fact": fact["n_correct"] == fact["n_rows"] == 4,
        "eq_f2p_all_weak": n_weak == n_f2p_orc,
        "eq_caliber_self": "scripts/probe_is_shape.py" in caliber(),
        "eq_three_accounts": len(auto) == 27 and len(orc) == 5 and fact["n_rows"] == 4,
    }

    out = {
        "schema": "is_shape_census_v1",
        "caliber": caliber(),
        "baseline_health": {"ok": health.get("ok"),
                            "diffs": health.get("diffs", []),
                            "caliber_diffs": health.get("caliber_diffs", [])},
        "accounts": {
            "A_auto": {
                "rows": auto,
                "by_why": by_why,
                "by_pair": by_pair,
                "raise_tail": raise_tail,
                "n_rows": len(auto),
                "n_f2p": n_f2p_auto,
                "note": "整条 bool 表达式探针，不加工 ⇒ 26 raise/raise 是构造必然",
            },
            "B_oracle": {
                "rows": orc,
                "n_rows": len(orc),
                "n_green": sum(1 for x in orc if x["oracle_green"]),
                "n_bad_red": sum(1 for x in orc if x["oracle_bad_red"]),
                "n_f2p": n_f2p_orc,
                "n_f2p_crash_weak": n_weak,
                "note": "人工转写判据；buggy 侧全 raise ⇒ crash 弱题，非端到端修复",
            },
            "C_shape_fact": fact,
        },
        "closure": closure,
        "summary": {
            "n_is_rows": len(auto),
            "n_auto_f2p": n_f2p_auto,
            "n_oracle_f2p": n_f2p_orc,
            "n_oracle_f2p_crash_weak": n_weak,
            "n_raise_both": by_why.get("raise/raise", 0),
            "n_behavior_both": by_why.get("behavior/behavior", 0),
            "all_checks": all(closure.values()),
            "conclusion": "判据层本就能判 is；门在装载门（④/②）而非判据形态。",
            "not_said": "不宣布扩张判据形态无用，也不宣布有效；不改 aether/ 一字节。",
        },
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")
    print("WROTE", args.out)
    print("  baseline =", health.get("ok"), "diffs =", health.get("diffs", []))
    print("  A auto   : n=%d  f2p=%d  why=%s" % (len(auto), n_f2p_auto, by_why))
    print("  B oracle : n=%d  green=%d  bad_red=%d  f2p=%d (weak=%d)"
          % (len(orc), sum(1 for x in orc if x["oracle_green"]),
             sum(1 for x in orc if x["oracle_bad_red"]), n_f2p_orc, n_weak))
    print("  C shape  : %d/%d correct" % (fact["n_correct"], fact["n_rows"]))
    print("  closure  :", all(closure.values()),
          [k for k, v in closure.items() if not v])


if __name__ == "__main__":
    main()
