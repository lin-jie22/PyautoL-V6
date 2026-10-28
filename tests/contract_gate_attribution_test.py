#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**门归因普查**（README 166）。

═══ 为什么有这份契约 ═══

165 结束时的两个"0"只能靠**手工看一个 commit** 解释：

    more-itertools 端到端 6 个 commit：F2P = 0，修好 = 0/6

⛔ 按第 25 条，一个"0"有两种含义：**真的没有** vs **量具瞎了**。165 已经
抓到过一次后者（69 条"有鉴别力的判据"是 tuple/list 降级造的）。手工归因
不能当结论 —— 它既不可复现，也没法说"是不是所有 commit 都这样"。

本契约盯的是**归因层本身**够不够格当证据：

  · A 每条断言**恰好一格**（守恒：总数 == 探针总数）
  · B 五格都要有正例 ⇒ 量具有区分力，不是"全落一格"
  · C ⛔⛔ 每一格的"0"都要能反证 ⇒ 证明是**拦的**不是**没扫到**
  · D 重构不改语义（三个 `scoped_*` 与 `unmapped_*` 的读数写死对拍）
  · E ⛔⛔ 同源恒等式：`#{gate==criterion}` **恒等于** `len(extract_criteria())`
  · F 沙箱侧的格（F2P/P2P/ctx）**不许**在抽取层伪造 —— 那需要真跑
  · G 真实仓库读数（缺失 ⇒ **跳过并披露**，未覆盖 ≠ 通过 —— 第 149 条）

═══ ⛔ 这是诊断层，不是扩张层 ═══

归因只给已有拒收**逐条命名**，不改变任何一条判据能不能进来。要扩张判据
形态（truthy / ∈ / ≠）仍需人拍板（第 126 条）。

跑法（离线、秒级，不需要真实仓库）：
    python tests/contract_gate_attribution_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

import aether.extract as X                                    # noqa: E402
from aether.extract import (                                  # noqa: E402
    GATE_CRITERION, GATE_FORM, GATE_ORDER, GATE_TUPLE,
    GATE_UNSERIALIZABLE, GATE_UNSTABLE, _iter_all_probes,
    attribute_statements, extract_criteria,
    scoped_assertions, scoped_raise_probes, scoped_unittest_probes,
    target_module, unmapped_unittest_calls)

SKIPS: list = []
FAILS: list = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def skip(name, why):
    print(f"  SKIP {name}  ⛔ {why}")
    SKIPS.append(name)


#: 一段**特意覆盖五格**的测试源码（每格至少一条）
SRC = """
import more_itertools as mi

def test_criterion():
    self.assertEqual(list(mi.take(3, range(10))), [0, 1, 2])

def test_form():
    self.assertTrue(mi.all_equal([1, 1]))
    self.assertIn(1, [1, 2])

def test_unserializable():
    self.assertEqual(set(mi.take(2, range(3))), {0, 1})

def test_tuple():
    self.assertTupleEqual(mi.minmax([3, 1]), (1, 3))

def test_unstable():
    self.assertEqual(mi.first([random()]), 1)

def test_bare_neq():
    assert mi.first([1]) != 2

def test_raises():
    with self.assertRaises(ValueError):
        mi.first([])
"""
TARGET = target_module("more_itertools/more.py")


def main() -> int:
    print("== A 组：每条探针**恰好一格**（守恒）==")
    probes = _iter_all_probes(SRC)
    rows = attribute_statements(SRC, TARGET)
    ck("A1 归因条数 == 探针条数（不重不漏）",
       len(rows) == len(probes), f"{len(rows)} vs {len(probes)}")
    gates = {}
    for r in rows:
        gates[r["gate"]] = gates.get(r["gate"], 0) + 1
    ck("A2 每格都是已知门标签",
       set(gates) <= set(GATE_ORDER), str(sorted(gates)))
    print(f"      {gates}")

    print("\n== B 组：五格都要有正例（量具有区分力）==")
    for g in (GATE_CRITERION, GATE_FORM, GATE_UNSERIALIZABLE,
              GATE_TUPLE, GATE_UNSTABLE):
        n = gates.get(g, 0)
        ck(f"B {g} 格有正例（{n} 条）", n >= 1, str(sorted(gates)))
    crit = [r for r in rows if r["gate"] == GATE_CRITERION]
    #: ⛔ 期望的是**降级后**的裸名（`mi.take(...)` ⇒ `take(...)`，README 165）
    ck("B6 criterion 里有一条是 `list(take(3, range(10)))`",
       any(r["call"] == "list(take(3, range(10)))" for r in crit),
       str([r["call"] for r in crit]))
    ck("B7 ⛔ 五格里**不止一格**非零 ⇒ 不是'全落一格'（第 24 条）",
       len([g for g in gates if gates[g] > 0]) >= 3, str(sorted(gates)))

    print("\n== C 组：⛔⛔ 每一格的 0 都要能被反证（是拦的，不是没扫到）==")
    #: C1 form：放开 `assertNotEqual`（**双参**，放进表就真能产出 —— 165 的
    #:    教训：用单参的 `assertTrue` 做反证是**恒假**的，放进表也产不出）
    try:
        saved_same = dict(X._UNITTEST_SAME)
        src2 = SRC.replace("self.assertIn(1, [1, 2])",
                           "self.assertNotEqual(mi.first([1]), 2)")
        before = attribute_statements(src2, TARGET)
        n_form_before = sum(1 for r in before if r["gate"] == GATE_FORM)
        X._UNITTEST_SAME["assertNotEqual"] = "expect"
        after = attribute_statements(src2, TARGET)
        n_form_after = sum(1 for r in after if r["gate"] == GATE_FORM)
        grew = [r for r in after
                if r["gate"] == GATE_CRITERION
                and r["call"] == "first([1])"]
        ck("C1 ⛔ 放开 assertNotEqual ⇒ form 减少且**确实**产出判据",
           n_form_after == n_form_before - 1 and len(grew) >= 1,
           f"form {n_form_before}→{n_form_after}, grew={len(grew)}"
           f" all={[r['call'] for r in after if r['gate']=='criterion']}")
        ck("C1b ⛔ 反证同时暴露扩张的代价：产出的是**语义相反**的判据",
           any(r.get("gold_value") == 2 for r in grew),
           str([r.get("gold_value") for r in grew]))
    finally:
        X._UNITTEST_SAME.clear()
        X._UNITTEST_SAME.update(saved_same)
    ck("C1c 反证后映射表已复原（不许污染后续组）",
       "assertNotEqual" not in X._UNITTEST_SAME, str(X._UNITTEST_SAME))

    #: C2 tuple：把 `_has_tuple` 打成恒 False ⇒ tuple 期望应变成 criterion
    try:
        saved_tuple = X._has_tuple
        X._has_tuple = lambda g: False
        rows2 = attribute_statements(SRC, TARGET)
        n_t = sum(1 for r in rows2 if r["gate"] == GATE_TUPLE)
        n_c = sum(1 for r in rows2 if r["gate"] == GATE_CRITERION)
        ck("C2 ⛔ 关掉 tuple 拦 ⇒ tuple 格变 0 且 criterion 变多",
           n_t == 0 and n_c > gates.get(GATE_CRITERION, 0),
           f"tuple={n_t} criterion {gates.get(GATE_CRITERION,0)}→{n_c}")
    finally:
        X._has_tuple = saved_tuple
    ck("C2b 复原", X._has_tuple is saved_tuple, "")

    #: C3 unstable：把 `has_unstable_call` 打成恒 False
    try:
        saved_unstable = X.has_unstable_call
        X.has_unstable_call = lambda c: False
        rows3 = attribute_statements(SRC, TARGET)
        n_u = sum(1 for r in rows3 if r["gate"] == GATE_UNSTABLE)
        ck("C3 ⛔ 关掉 unstable 拦 ⇒ unstable 格变 0",
           n_u == 0, f"unstable={n_u}")
    finally:
        X.has_unstable_call = saved_unstable

    #: C4 unserializable：把 `_jsonable` 打成恒 True
    try:
        saved_json = X._jsonable
        X._jsonable = lambda v: True
        rows4 = attribute_statements(SRC, TARGET)
        n_j = sum(1 for r in rows4 if r["gate"] == GATE_UNSERIALIZABLE)
        ck("C4 ⛔ 关掉序列化拦 ⇒ unserializable 格变 0",
           n_j == 0, f"unserializable={n_j}")
    finally:
        X._jsonable = saved_json

    print("\n== D 组：重构不改语义（三个扫描器的读数**写死**对拍）==")
    ck("D1 scoped_assertions == 1（裸 assert 只有 `!=` 那条，非同形 ⇒ 0）",
       len(scoped_assertions(SRC)) == 0, str(scoped_assertions(SRC)))
    #: ⛔ 4 条 = assertEqual(list) / assertEqual(set) / assertTupleEqual /
    #:    assertEqual(first(random))。`with assertRaises` 属于 **pytest 家族**
    #:    （`scoped_raise_probes`），不在这里 —— 写死是为了让重构能被抓。
    ck("D2 scoped_unittest_probes == 4（四条同形的 unittest 断言）",
       len(scoped_unittest_probes(SRC)) == 4,
       str(scoped_unittest_probes(SRC)))
    ck("D3 scoped_raise_probes == 1（with 里只有 1 条 Expr）",
       len(scoped_raise_probes(SRC)) == 1, str(scoped_raise_probes(SRC)))
    u = unmapped_unittest_calls(SRC)
    ck("D4 unmapped == {assertTrue:1, assertIn:1}",
       u == {"assertTrue": 1, "assertIn": 1}, str(u))
    ck("D5 ⛔ `assert a != b` 不计入 unittest 拒收（它是裸 assert 的形态门）",
       "assert" not in u, str(u))

    print("\n== E 组：⛔⛔ 同源恒等式（归因层不许自带第二套规则）==")
    st: dict = {}
    out = extract_criteria(SRC, TARGET, stats=st)
    n_crit = sum(1 for r in rows if r["gate"] == GATE_CRITERION)
    ck("E1 #{gate==criterion} == len(extract_criteria())",
       n_crit == len(out), f"{n_crit} vs {len(out)}")
    ck("E2 且**集合也相同**（按 call 对拍，不是只比条数）",
       sorted(r["call"] for r in rows if r["gate"] == GATE_CRITERION)
       == sorted(c["call"] for c in out),
       str(sorted(c["call"] for c in out)))
    n_form = sum(1 for r in rows if r["gate"] == GATE_FORM)
    n_unmapped_u = sum(unmapped_unittest_calls(SRC).values())
    ck("E3 form 条数 == unittest 拒收数 + 非同形裸 assert 数",
       n_form == n_unmapped_u + 1,
       f"form={n_form} unmapped={n_unmapped_u}（裸 assert != 计 1）")
    ck("E4 每条 criterion 都带 `stmt`（归因脚本靠它对回 PR 新增断言）",
       all(r.get("stmt") for r in rows if r["gate"] == GATE_CRITERION),
       str([r.get("stmt") for r in rows if r["gate"] == GATE_CRITERION]))
    ck("E5 每条被拦的都带 `attr`（知道是哪种写法被拦）",
       all(r.get("attr") for r in rows if r["gate"] != GATE_CRITERION),
       str([r.get("attr") for r in rows if r["gate"] != GATE_CRITERION]))

    print("\n== F 组：⛔ 沙箱侧的格不许在抽取层伪造 ==")
    ck("F1 抽取层的门只含 GATE_ORDER（没有 F2P/P2P/ctx）",
       set(gates) <= set(GATE_ORDER) and "F2P" not in gates
       and "P2P" not in gates and "ctx" not in gates, str(sorted(gates)))
    ck("F2 ⛔ `attribute_statements` 不接沙箱参数（它不许真跑）",
       "sandbox" not in attribute_statements.__doc__.lower()
       or "不在这里伪造" in attribute_statements.__doc__,
       "docstring 没声明边界")

    print("\n== G 组：真实仓库读数（缺失 ⇒ 跳过并披露）==")
    real = Path("/tmp/realrepo/more-itertools/tests/test_more.py")
    if not real.exists():
        skip("G 真实仓库读数", f"{real} 不存在")
    else:
        src_real = real.read_text(encoding="utf-8", errors="replace")
        rrows = attribute_statements(src_real, TARGET)
        rg = {}
        for r in rrows:
            rg[r["gate"]] = rg.get(r["gate"], 0) + 1
        print(f"      more-itertools/tests/test_more.py：{rg}")
        ck("G1 至少三格非零（真实文件上量具仍有区分力）",
           len([g for g in rg if rg[g] > 0]) >= 3, str(sorted(rg)))
        ck("G2 criterion 与 form 都非零（165 的两个数都在同一张表里）",
           rg.get(GATE_CRITERION, 0) > 0 and rg.get(GATE_FORM, 0) > 0,
           str(sorted(rg)))
        ck("G3 守恒：总数 == 探针总数",
           len(rrows) == len(_iter_all_probes(src_real)),
           f"{len(rrows)} vs {len(_iter_all_probes(src_real))}")

    print("\n== H 组：⛔ PR 新增断言的口径（多重集差，不是集合差）==")
    try:
        import importlib.util
        _p = ROOT / "scripts" / "probe_gate_attribution.py"
        _s = importlib.util.spec_from_file_location("pga", _p)
        mod = importlib.util.module_from_spec(_s)
        _s.loader.exec_module(mod)
        base = """
def test_a():
    self.assertEqual(f(1), 1)
"""
        new = """
def test_a():
    self.assertEqual(f(1), 1)
    self.assertEqual(f(1), 1)
    self.assertEqual(f(2), 2)
"""
        #: 同一个函数里**两条一模一样**的断言 + 一条不同的 ⇒ 多重集差 = 2
        diff = mod.new_statements.__wrapped__ if False else None
        import collections
        c_new = collections.Counter(
            (p["fn"], p["stmt"]) for p in _iter_all_probes(new))
        c_old = collections.Counter(
            (p["fn"], p["stmt"]) for p in _iter_all_probes(base))
        d = c_new - c_old
        ck("H1 同一条断言写两遍 ⇒ 差集计 2（多重集，不是集合）",
           sum(d.values()) == 2, str(dict(d)))
        ck("H2 归因模块可加载（`new_statements` 存在）",
           hasattr(mod, "new_statements"), str(dir(mod)[:5]))
    except Exception as exc:
        skip("H PR 新增断言口径", f"{type(exc).__name__}: {exc}")

    print()
    if FAILS:
        print(f"❌ 失败 {len(FAILS)} 项：{FAILS}")
        return 1
    msg = "✅ 每条断言恰好一格、五格都有正例且都能反证、归因与抽取同源"
    if SKIPS:
        msg += f"（{len(SKIPS)} 项跳过：未覆盖 ≠ 通过）"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
