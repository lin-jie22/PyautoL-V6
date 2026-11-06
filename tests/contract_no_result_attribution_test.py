#!/usr/bin/env python3
"""契约：**94 条 `no_result_both` 结构化归因**（README 178）必须同源、有牙。

═══ 钉死什么 ═══

177 留下 222 条仍未知，其中主账 94 条 `no_result_both` 连「为什么没
结果」都没记账（`sandbox_verdict / got_*` 全 `None`）。178 用两本
**正交**的账补上：

  ① 静态归因账：产品同源 `_iter_all_probes` / `collect_bindings`，
     对产品 `call` 做自由变量出身分析，每条恰好一格 + 正交记
     「产品是否内联过」。
  ② 运行时硬证据账：177 同源 truthy 沙箱，但取**产出方写死的 `why`**
     （`environment.py:302` 禁止解析 evidence 文案猜分类）+ evidence 原文。

三条硬约束：

  A. **两本账不许合并成一个布尔**：交叉表单独报；
     `unparseable / unmatched / skipped` 是量具格，不参与归因推断。
  B. **不报收益**：本轮只回答「被什么挡住」，不回答「解开能修几个」
     （177 已证 10 条 diff 全 `reach=X`）。
  C. **反证组**：B 组全部离线合成，逐项注入事故确认按预期转红；
     C 组才碰本机基线与真实沙箱，且用正例证明查询能命中（第 133 条）。

⛔ 5 条 `unparseable` 是 176 存档 `call[:240]` 截断造成的**构造必然**
   SyntaxError，不是上下文门；按第 83 条不擅自改上游尺子，只如实留痕。

用法：python tests/contract_no_result_attribution_test.py
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_no_result_attribution as pna          # noqa: E402

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

READING = ROOT / "scripts" / "no_result_attribution.json"

PASSES: list = []
FAILS: list = []
SKIPS: list = []

EXPECT_GATES = {
    "ambiguous_rhs": 4, "imported_name": 22, "loop_var": 17,
    "param_or_fixture": 13, "self_attr": 4, "unbound": 23,
    "unparseable": 5, "with_binding": 6,
}
EXPECT_STATES = {"none": 43, "partial": 42, "unknown": 4,
                 "unparseable": 5}


def ck(name: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  | " + detail) if detail and not ok else ""))
    #: ⛔ 必须写成字面 `FAILS.append`：静态闸 contract_assert_wiring B1
    #: 要能定位失败记账（写成 `(PASSES if ok else FAILS).append` 会判
    #: 「失败不落地」⇒ 全量验收真红）。
    if not ok:
        FAILS.append(name)
    else:
        PASSES.append(name)


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print("  SKIP %s  | %s" % (name, why))


# ─────────────────────────── A 组：钉死全量读数 ───────────────────────────
def group_a() -> None:
    d = json.loads(READING.read_text(encoding="utf-8"))
    s, rows = d["summary"], d["rows"]
    print("A 组：全量读数（⛔ 改尺子必须重跑普查）")
    ck("A1 schema 版本", d["schema"] == "no_result_attribution/v1",
       str(d.get("schema")))
    ck("A2 分母 94 条", s["n_rows"] == 94 and len(rows) == 94,
       "%s / %s" % (s["n_rows"], len(rows)))
    ck("A3 静态主格分布", s["gates"] == EXPECT_GATES, str(s["gates"]))
    ck("A4 内联状态正交账", s["inline_state"] == EXPECT_STATES,
       str(s["inline_state"]))
    ck("A5 两侧都抛异常 94/94", s["sandbox_pair"] == {"both_raise": 94},
       str(s["sandbox_pair"]))
    ck("A6 why_fixed 全 raise", s["why_fixed"] == {"raise": 94},
       str(s["why_fixed"]))
    ck("A7 why_buggy 全 raise",
       all(r["why_buggy"] == "raise" for r in rows))
    ck("A8 evidence 一条不空",
       all(r["evidence_fixed"] and r["evidence_buggy"] for r in rows))
    ck("A9 inlinable_rhs = 0（内联器能做的都做了）",
       s["gates"].get("inlinable_rhs", 0) == 0, str(s["gates"]))
    ck("A10 主格封闭", all(r["main_gate"] in pna.GATES for r in rows))
    ck("A11 运行时格封闭",
       all(r["sandbox_pair"] in pna.PAIRS for r in rows))
    ck("A12 内联状态封闭",
       all(r["inline_state"] in pna.INLINE_STATES for r in rows))
    ck("A13 两本账交叉表全是 both_raise/*",
       all(k.startswith("both_raise/") for k in s["pair_x_gate"]),
       str(s["pair_x_gate"]))
    unp = [r for r in rows if r["main_gate"] == "unparseable"]
    ck("A14 5 条截断的都是 240 字符",
       len(unp) == 5 and all(len(r["call"]) == 240 for r in unp),
       str([len(r["call"]) for r in unp]))
    ck("A15 截断条真异常是 SyntaxError",
       all("SyntaxError" in r["evidence_fixed"] for r in unp))
    ck("A16 unmatched 9 且全是 probe_index miss",
       s["n_unmatched"] == 9 and len(d["unmatched"]) == 9
       and all(x["reason"] == "probe_index miss" for x in d["unmatched"]),
       str(s["n_unmatched"]))
    ck("A17 量具健康：scope 全找到、无跳过的 commit",
       s["n_scope_missing"] == 0 and s["n_skipped_commits"] == 0,
       "%s/%s" % (s["n_scope_missing"], s["n_skipped_commits"]))
    ck("A18 基线锚当读数报出",
       s["baseline_health"] == {"ok": True, "diffs": 0, "repos": 3},
       str(s["baseline_health"]))
    ck("A19 零命中先证查询能命中：evidence 有多样性",
       s["n_distinct_evidence"] > 1 and bool(s["top_evidence"]),
       str(s["n_distinct_evidence"]))
    ck("A20 口径签名与当前源码一致", d["caliber"] == pna.caliber(),
       "尺子改过 ⇒ 存档过期")
    ck("A21 分寸写死「不报收益」",
       "不报收益" in s["note"] and "不合并成布尔" in s["note"], s["note"])
    ck("A22 两本账都在每条 row 上",
       all("main_gate" in r and "sandbox_pair" in r
           and "inline_state" in r for r in rows))
    typ = [r for r in rows if "typing" in r["evidence_fixed"]]
    ck("A23 typing 缺失 12 条", len(typ) == 12, str(len(typ)))
    ck("A24 可归因分母 89（94 − 5 截断）",
       sum(v for k, v in s["gates"].items() if k != "unparseable") == 89,
       str(sum(v for k, v in s["gates"].items()
               if k != "unparseable")))


# ─────────────────── B 组：离线注入反证（不依赖 /tmp/realrepo） ───────────────────
def group_b() -> None:
    print("B 组：离线反证（判据先证明自己会响）")

    #: free_names
    ck("B1 普通表达式取 Load 名", pna.free_names("a + b") == ["a", "b"],
       str(pna.free_names("a + b")))
    ck("B2 属性链只取根名", pna.free_names("obj.attr(1)") == ["obj"],
       str(pna.free_names("obj.attr(1)")))
    ck("B3 语法不完整返回 None（不是空集）",
       pna.free_names("(") is None, str(pna.free_names("(")))
    ck("B4 空表达式返回 None", pna.free_names("") is None)
    ck("B5 字面量无自由变量", pna.free_names("1 + 2") == [])

    #: classify_name
    empty, binds, params = {}, {}, set()
    ck("B6 self ⇒ self_attr",
       pna.classify_name("self", empty, binds, binds, params) == "self_attr")
    ck("B7 cls ⇒ self_attr",
       pna.classify_name("cls", empty, binds, binds, params) == "self_attr")
    ck("B8 参数 ⇒ param_or_fixture",
       pna.classify_name("cmp", empty, binds, binds, {"cmp"})
       == "param_or_fixture")
    ck("B9 loop ⇒ loop_var",
       pna.classify_name("i", {"i": {"loop"}}, binds, binds, params)
       == "loop_var")
    ck("B10 with ⇒ with_binding",
       pna.classify_name("stream", {"stream": {"with"}}, binds, binds,
                         params) == "with_binding")
    ck("B11 import ⇒ imported_name",
       pna.classify_name("typing", {"typing": {"import"}}, binds, binds,
                         params) == "imported_name")
    ck("B12 assign 但不在绑定表 ⇒ ambiguous_rhs",
       pna.classify_name("dates", {"dates": {"assign"}}, binds, binds,
                         params) == "ambiguous_rhs")
    ck("B13 assign 且在绑定表 ⇒ inlinable_rhs",
       pna.classify_name("x", {"x": {"assign"}}, {"x": 1}, binds,
                         params) == "inlinable_rhs")
    ck("B14 内建 ⇒ builtin_only",
       pna.classify_name("list", empty, binds, binds, params)
       == "builtin_only")
    ck("B15 未知 ⇒ unbound",
       pna.classify_name("zz_unknown", empty, binds, binds, params)
       == "unbound")
    ck("B16 反证：出身改掉分类必须跟着变",
       pna.classify_name("i", {"i": {"loop"}}, binds, binds, params)
       != pna.classify_name("i", {"i": {"import"}}, binds, binds, params))
    ck("B17 self 优先级高于 loop",
       pna.classify_name("self", {"self": {"loop"}}, binds, binds,
                         {"self"}) == "self_attr")
    ck("B18 classify_name 返回值全在封闭集合内",
       all(pna.classify_name(n, {}, {}, {}, set()) in pna.GATES
           for n in ("self", "cls", "list", "zz_unknown")))

    #: main_gate
    ck("B19 解析不了 ⇒ unparseable",
       pna.main_gate(None, {}) == "unparseable")
    ck("B20 无自由变量 ⇒ no_free_var",
       pna.main_gate([], {}) == "no_free_var")
    ck("B21 混合时取最拦路格",
       pna.main_gate(["i", "list"],
                     {"i": "loop_var", "list": "builtin_only"})
       == "loop_var")
    ck("B22 全内建 ⇒ builtin_only",
       pna.main_gate(["list"], {"list": "builtin_only"}) == "builtin_only")
    try:
        pna.main_gate(["x"], {"x": "NOT_A_GATE"})
        bad = False
    except ValueError:
        bad = True
    ck("B23 反证：封闭集合外的类必须 fail-closed", bad)
    saved = pna.PRIORITY
    try:
        pna.PRIORITY = tuple(reversed(saved))
        flipped = pna.main_gate(
            ["i", "list"], {"i": "loop_var", "list": "builtin_only"})
    finally:
        pna.PRIORITY = saved
    ck("B24 反证：优先级倒置会让结论反转（不是恒真）",
       flipped != "loop_var", str(flipped))

    #: pair_verdict
    cases = [
        (("raise", "raise"), "both_raise"),
        (("raise", "mismatch"), "mixed"),
        (("mismatch", "raise"), "mixed"),
        (("behavior", "behavior"), "not_raise"),
        (("import", "raise"), "import"),
        (("missing_result", "raise"), "missing"),
        ((None, "raise"), "skipped"),
        (("raise", None), "skipped"),
    ]
    for i, ((wf, wb), want) in enumerate(cases, 1):
        ck("B25.%d why 合取 %r/%r ⇒ %s" % (i, wf, wb, want),
           pna.pair_verdict(wf, wb) == want,
           str(pna.pair_verdict(wf, wb)))
    ck("B26 反证：单侧 raise 不许判 both_raise",
       pna.pair_verdict("raise", "behavior") != "both_raise")
    ck("B27 pair_verdict 全组合落在封闭集合内",
       all(pna.pair_verdict(a, b) in pna.PAIRS
           for a in ("raise", "mismatch", "behavior", "import", None)
           for b in ("raise", "mismatch", "behavior", "import", None)))

    #: inline_state_of
    ck("B28 call == orig ⇒ none",
       pna.inline_state_of("x", "x", ["x"]) == "none")
    ck("B29 内联过但残留 ⇒ partial",
       pna.inline_state_of("f(x)", "y", ["x"]) == "partial")
    ck("B30 内联过且无残留 ⇒ complete",
       pna.inline_state_of("f(1)", "y", []) == "complete")
    ck("B31 orig 缺失 ⇒ unknown",
       pna.inline_state_of("x", None, ["x"]) == "unknown")
    ck("B32 解析不了 ⇒ unparseable",
       pna.inline_state_of("(", "y", None) == "unparseable")

    #: 结构性反证：默认路径必须验锚、脚本不许偷偷改产品
    script = (ROOT / "scripts" / "probe_no_result_attribution.py").read_text(
        encoding="utf-8")
    ck("B33 默认路径真的调 require_baseline",
       "require_baseline()" in script and "no_baseline_gate" in script)
    ck("B34 沙箱读的是产出方 why，不解析文案分类",
       'r.get("why")' in script and 'pair_verdict' in script)
    ck("B35 不许拿 evidence 文案猜分类",
       "evidence" in script and 'startswith("got=")' not in script)
    ck("B36 两本账不许合并成一个布尔",
       '"cross"' in script and '"pair_x_gate"' in script)


# ─────────────────────── C 组：本机健康 + 真实沙箱正例 ───────────────────────
def group_c() -> None:
    print("C 组：本机验锚 + 真实沙箱正例（零命中先证查询能命中）")
    try:
        from realrepo_baseline import require_baseline
        h = require_baseline()
        ck("C1 本机基线锚健康",
           bool(h.get("ok")) and not h.get("diffs"), str(h)[:160])
    except Exception as exc:                            # noqa: BLE001
        ck("C1 本机基线锚健康", False,
           "%s: %s" % (type(exc).__name__, exc))

    #: ⛔ 真实沙箱正例：同一批里既要有「缺名 ⇒ raise」也要有
    #:    「能求值 ⇒ 非 raise」，否则「94 条全 raise」可能只是量具坏了。
    tests = [{"name": "q0", "call": "definitely_missing_name"},
             {"name": "q1", "call": "x"}]
    try:
        out = pna.sandbox_read("x = 1", tests, 15.0, None)
    except Exception as exc:                            # noqa: BLE001
        out = []
        ck("C2 真实沙箱可跑", False, "%s: %s" % (type(exc).__name__, exc))
    else:
        ck("C2 真实沙箱可跑且长度恒等", len(out) == len(tests), str(out))
    if len(out) == 2:
        ck("C3 缺名探针判 raise 且 evidence 有 NameError",
           out[0][0] == "raise" and "NameError" in out[0][1], str(out[0]))
        ck("C4 可求值探针不判 raise（证查询能命中另一侧）",
           out[1][0] != "raise", str(out[1]))

    d = json.loads(READING.read_text(encoding="utf-8"))
    rows = d["rows"]
    imp = [r for r in rows if r["main_gate"] == "imported_name"]
    unp = [r for r in rows if r["main_gate"] == "unparseable"]
    #: ⛔ 主格是「自由变量**出身**」，不是「运行时失败**原因**」——两本账
    #:    本来就是不同口径，不许要求它们全等（否则就是把两本账合并成布尔）。
    #:    22 条里 20 条真缺名；2 条例外必须**精确钉死**，不许用 all() 糊过：
    #:      93185b2 → StopIteration（date/daterange 在沙箱里其实可解析）
    #:      f560500 → AttributeError（attr 被产品源码里的同名对象遮蔽）
    #:    ⛔ 这里只对 evidence 做**证据计数**，不重分类 `why`
    #:    （`environment.py:302` 的纪律由产出方守着）。
    kinds = collections.Counter(
        "NameError" if "NameError" in r["evidence_fixed"]
        else "StopIteration" if "StopIteration" in r["evidence_fixed"]
        else "AttributeError" if "AttributeError" in r["evidence_fixed"]
        else "other" for r in imp)
    ck("C5 imported_name 的运行时证据精确计数 20/1/1",
       kinds == {"NameError": 20, "StopIteration": 1,
                 "AttributeError": 1}, str(dict(kinds)))
    ck("C6 量具格 unparseable 与真实 SyntaxError 对拍",
       bool(unp) and all("SyntaxError" in r["evidence_fixed"] for r in unp))
    with_b = [r for r in rows if r["main_gate"] == "with_binding"]
    ck("C7 with_binding 的真实异常是缺名",
       bool(with_b) and all("NameError" in r["evidence_fixed"]
                            for r in with_b))
    ck("C8 出身格 ≠ 失败原因：2 条例外逐条单列",
       kinds["StopIteration"] == 1 and kinds["AttributeError"] == 1
       and any(r["sha"] == "93185b2" for r in imp)
       and any(r["sha"] == "f560500" for r in imp))


def main() -> int:
    group_a()
    group_b()
    group_c()
    print("\npass=%d  fail=%d  skip=%d" % (len(PASSES), len(FAILS),
                                           len(SKIPS)))
    if SKIPS:
        print("  ⛔ 跳过项（未覆盖 ≠ 通过）：" + ", ".join(SKIPS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
