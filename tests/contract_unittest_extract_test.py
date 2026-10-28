#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**unittest 风格判据抽取**（README 165）。

═══ 为什么有这份契约 ═══

164 把**装载门**从 8% 开到 100%，但开门后端到端收益是 **0** —— 漏斗最后
一格写着 `no_criteria`：more-itertools 25 个 commit **一条判据都抽不到**。
按第 25 条（一个"0"有两种含义），先查是不是抽漏了：

    more-itertools 的 1274 条断言里 **1271 条是 `self.assertX(...)`**，
    而抽取层只认裸 `assert` ⇒ `assertEqual` 921 条全漏。

⛔ 所以"第二个仓库覆盖率 0%"的成因与 boltons **完全不同**：
   · boltons 的 5% —— 卡**形态门**（84.9% 结构级，合成层够不到）
   · more-itertools 的 0% —— 卡**判据表达力门**（断言根本没被读进来）

═══ ⛔⛔ 本契约最要紧的一条：**同形映射，不许扩张** ═══

判据层只有两种形状：`expect`（探针 == 可 JSON 的值）与 `raises`。
所以只有**写法不同、形状相同**的 unittest 断言可以进：

    assertEqual(a, b)      ≡ assert a == b        ✅
    assertTupleEqual(a, b) ≡ assert a == b        ✅
    assertRaises(E)        ≡ with raises(E)       ✅

⛔ 需要**新比较语义**的（`assertNotEqual` ⇒ ≠、`assertTrue` ⇒ truthy、
`assertIn` ⇒ ∈、`assertIs` ⇒ 身份、`assertAlmostEqual` ⇒ 容差）**一律不许
混进来** —— 形状一样、语义不同 = **造假判据**。C 组盯这条，D 组证明 C 组的
"0 条"是**主动拒收**而不是没扫到（第 122 条：构造必然 ≠ 证据）。

═══ 组 ═══

  A 同形映射正确性（assertEqual / TupleEqual / ListEqual）
  B assertRaises 两种写法（with 上下文 / 直接调用式）
  C ⛔⛔ 不扩张：需要新语义的断言不产出
  D ⛔ C 组的反证：放开之后**确实**会产出 ⇒ 0 条是拒收不是漏扫
  E 期望侧也要内联（`expected` 变量）+ 非字面量不收
  F ⛔ 父包降级是**试探性**的（记账 + 默认不开）
  G 记账不静默（n_unmapped / unmapped_kinds / n_pkg_demote）
  H 向后兼容（裸 assert 不受影响、`gold` 原文保留）
  I 依托真实仓库的读数（缺失 ⇒ **跳过并披露**，未覆盖 ≠ 通过 —— 第 149 条）

跑法（离线、秒级，不需要真实仓库）：
    python tests/contract_unittest_extract_test.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

from aether.extract import (  # noqa: E402
    _UNITTEST_SAME, extract_criteria, resolve_probe,
    scoped_unittest_probes, unmapped_unittest_calls,
)

SKIPS: list = []
FAILS: list = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def skip(name, why):
    print(f"  SKIP {name}  ⛔ {why}")
    SKIPS.append(name)


#: 一段 unittest 风格的测试：三种同形 + 五种需要新语义的
TEST_SRC = """
import more_itertools as mi

def test_equal():
    assertEqual(list(mi.take(3, range(10))), [0, 1, 2])
    self.assertEqual(mi.first([1, 2]), 1)

def test_tuple():
    self.assertTupleEqual(mi.minmax([3, 1]), (1, 3))
    self.assertListEqual(list(mi.chunked([1, 2], 1)), [[1], [2]])

def test_raises():
    with self.assertRaises(ValueError):
        mi.first([])
    self.assertRaises(ValueError, mi.first, [])
    with self.assertRaises(ValueError):
        mi.minmax([])

def test_need_new_semantics():
    self.assertTrue(mi.all_equal([1, 1]))
    self.assertFalse(mi.all_equal([1, 2]))
    self.assertIn(1, [1, 2])
    self.assertNotEqual(mi.first([1]), 2)
    self.assertIs(mi.first([1]), 1)
    self.assertAlmostEqual(1.0, 1.0000001)
    self.assertRaisesRegex(ValueError, 'empty', mi.first, [])
    self.assertCountEqual([1, 2], [2, 1])

def test_gold_var():
    expected = [0, 1]
    assertEqual(list(mi.take(2, range(5))), expected)

def test_gold_not_literal():
    expected = mi.first([7])
    assertEqual(mi.first([7]), expected)
"""
TARGET = "more_itertools.more"
PKG = "more_itertools"


def main() -> int:
    print("=" * 74)
    print("契约：unittest 风格判据抽取（README 165）")
    print("=" * 74)

    print("\n== A 组：同形映射正确性 ==")
    st: dict = {}
    rows = extract_criteria(TEST_SRC, TARGET, stats=st)
    eq = [r for r in rows if r["kind"] == "expect"]
    ck("A1 assertEqual（裸名写法）被抽出",
       any(r["call"] == "list(take(3, range(10)))"
           and r["gold_value"] == [0, 1, 2] for r in eq), str(eq[:2]))
    ck("A2 assertEqual（self. 写法）被抽出",
       any(r["call"] == "first([1, 2])" and r["gold_value"] == 1 for r in eq),
       str(eq[:4]))
    #: ⛔ A3 的口径在 J 组之后**必须**改：`assertTupleEqual` 形状上同形
    #:    （== 就是 ==），但它的期望**必然是 tuple**，而 tuple 在 JSON 层
    #:    表达不了（第五节）⇒ **结构性不可达**：映射表收它没错，下游恒拦。
    #:    ⛔ 不放进映射表会造成相反的错误印象（"表里能抽、实际抽不到"）。
    ck("A3 ⛔ assertTupleEqual 同形但**结构性不可达**（tuple 期望 ⇒ J 组拦）",
       "assertTupleEqual" in _UNITTEST_SAME
       and not any(r["call"] == "minmax([3, 1])" for r in eq)
       and st.get("n_tuple_gold", 0) >= 1,
       f"{st.get('n_tuple_gold')} / {[r['call'] for r in eq]}")
    ck("A4 assertListEqual 映射到 expect",
       any(r["call"] == "list(chunked([1, 2], 1))" for r in eq), str(eq))
    ck("A5 ⛔ 映射表只收同形的三种",
       set(_UNITTEST_SAME.values()) == {"expect"}
       and "assertNotEqual" not in _UNITTEST_SAME
       and "assertTrue" not in _UNITTEST_SAME, str(_UNITTEST_SAME))

    print("\n== B 组：assertRaises 两种写法 ==")
    rz = [r for r in rows if r["kind"] == "raises"]
    calls = {r["call"] for r in rz}
    ck("B1 with 上下文形态（unittest 名）", "first([])" in calls, str(calls))
    ck("B2 with 上下文形态（第二条）", "minmax([])" in calls, str(calls))
    ck("B3 ⛔ 直接调用式 `self.assertRaises(E, f, *a)` 也认",
       "first([])" in calls and sum(1 for r in rz if r["call"] == "first([])")
       >= 2, str([(r["call"], r["raises"]) for r in rz]))
    ck("B4 异常名取自第一个实参",
       all(r["raises"] == "ValueError" for r in rz), str(rz))

    print("\n== C 组：⛔⛔ 不扩张（需要新语义的一律不产出）==")
    bad = ("assertTrue", "assertFalse", "assertIn", "assertNotEqual",
           "assertIs", "assertAlmostEqual", "assertRaisesRegex",
           "assertCountEqual")
    leaked = [r for r in rows
              if any(k in r["orig"] for k in ("all_equal([1, 1])",
                                              "all_equal([1, 2])"))]
    ck("C1 truthy 类不产出（assertTrue/False）", not leaked, str(leaked))
    ck("C2 ⛔ 没有一条判据来自需要新语义的断言",
       not any(r["fn"] == "test_need_new_semantics" for r in rows),
       str([r for r in rows if r["fn"] == "test_need_new_semantics"]))
    um = st.get("unmapped_kinds", {})
    ck("C3 它们被**记账**而不是静默丢弃（第 149 条）",
       all(um.get(k, 0) > 0 for k in bad), str(um))
    ck("C4 记账总数与分布一致",
       st.get("n_unmapped") == sum(um.values()), f"{st.get('n_unmapped')}")

    print("\n== D 组：⛔ C 组的反证（0 条 = 拒收，不是没扫到）==")
    #: ⛔ 用 **assertNotEqual** 做反证，不用 assertTrue：后者是**单参**
    #:   断言，放进映射表也产不出判据（第一版就这么试的，反证恒假 ——
    #:   它证明的是"参数个数不匹配"，**不是**"我们主动拒收"，第 122 条）。
    #:   assertNotEqual 是双参 ⇒ 放开它**必然**产出，且产出的东西正好
    #:   演示了为什么必须拦：语义是 "≠ 2"，塞进 expect 就成了 "== 2"。
    try:
        saved = dict(_UNITTEST_SAME)
        _UNITTEST_SAME["assertNotEqual"] = "expect"
        rows2 = extract_criteria(TEST_SRC, TARGET, stats={})
        grew = [r for r in rows2 if r["fn"] == "test_need_new_semantics"]
        ck("D1 ⛔ 放开就产出 ⇒ C 组的 0 条**是拦的**，不是没扫到",
           len(grew) >= 1, f"放开后仍 {len(grew)} 条 ⇒ C 组可能是恒真")
        ck("D2 ⛔ 而且产出的是**语义相反**的判据（≠2 变成 ==2）"
           " ⇒ 这就是不许扩张的理由",
           any(r["gold_value"] == 2 for r in grew), str(grew))
        peek = [(r["call"], r["gold_value"]) for r in grew][:3]
        print(f"      （放开后冒出：{peek}）")
    finally:
        _UNITTEST_SAME.clear()
        _UNITTEST_SAME.update(saved)
    ck("D3 反证结束后映射表已复原（不许污染后续组）",
       "assertNotEqual" not in _UNITTEST_SAME, str(_UNITTEST_SAME))

    print("\n== E 组：期望侧也要内联 ==")
    ck("E1 `expected` 是字面量 ⇒ 解得出值",
       any(r["call"] == "list(take(2, range(5)))" and r["gold_value"] == [0, 1]
           for r in eq), str([r for r in eq if "take(2" in r["call"]]))
    ck("E2 ⛔ 期望侧依赖被测模块（`expected = first([7])`）⇒ 不收",
       not any(r["fn"] == "test_gold_not_literal" for r in rows),
       str([r for r in rows if r["fn"] == "test_gold_not_literal"]))
    ck("E3 救回的条数被记账",
       st.get("n_gold_resolved", 0) >= 1, str(st.get("n_gold_resolved")))
    ck("E4 `gold` 保留原文、`gold_value` 才是值（调用方别用错）",
       all("gold" in r for r in eq)
       and any(r["gold"] == "expected" for r in eq), str(eq[:1]))

    print("\n== F 组：⛔ 父包降级是试探性的 ==")
    ck("F1 `mi.first(...)` ⇒ `first(...)`（包 API → 模块内裸名）",
       any(r["call"] == "first([1, 2])" for r in eq), str(eq[:2]))
    ck("F2 ⛔ 它**默认不开**：`resolve_probe` 不传 pkg 时不降",
       resolve_probe("mi.first([1])", {}, TARGET,
                     {"mi": "more_itertools"}) is None, "不该默认降")
    ck("F3 传了 pkg 才降",
       resolve_probe("mi.first([1])", {}, TARGET, {"mi": "more_itertools"},
                     pkg=PKG) == "first([1])")
    ck("F4 ⛔ 多段属性不许降（`mi.recipes.f` 不是 more.py 里的名字）",
       resolve_probe("mi.recipes.f(1)", {}, TARGET,
                     {"mi": "more_itertools"}, pkg=PKG) is None)
    ck("F5 记账：救回多少条是**单独**记的，不混进产出总数",
       st.get("n_pkg_demote", 0) >= 1, str(st.get("n_pkg_demote")))

    print("\n== G 组：记账不静默 ==")
    ck("G1 stats 四个计数都在",
       all(k in st for k in ("n_raw", "n_unjsonable", "n_unmapped",
                             "n_pkg_demote")), str(sorted(st)))
    ck("G2 unmapped 分布按名字给出",
       isinstance(um, dict) and um.get("assertTrue") == 1, str(um))
    ck("G3 `unmapped_unittest_calls` 单独可用（调用方可自查）",
       unmapped_unittest_calls(TEST_SRC).get("assertIn") == 1,
       str(unmapped_unittest_calls(TEST_SRC)))

    print("\n== H 组：向后兼容（裸 assert 不许被改坏）==")
    BARE = ("from boltons import strutils\n"
            "def t():\n"
            "    exp = 'x'\n"
            "    assert strutils.strip_ansi('x') == exp\n")
    st2: dict = {}
    rows3 = extract_criteria(BARE, "boltons.strutils", stats=st2)
    ck("H1 裸 assert 仍抽得出",
       len(rows3) == 1 and rows3[0]["call"] == "strip_ansi('x')", str(rows3))
    ck("H2 gold 原文保留（契约按原文对拍）",
       rows3 and rows3[0]["gold"] == "exp", str(rows3[:1]))
    ck("H3 gold_value 已解出",
       rows3 and rows3[0]["gold_value"] == "x", str(rows3[:1]))
    ck("H4 没有 unittest 断言 ⇒ n_unmapped 为 0（不误报）",
       st2.get("n_unmapped") == 0, str(st2))

    print("\n== J 组：⛔⛔ tuple 期望是**假判据**，必须拦（README 165 第五节）==")
    TUP = ("import more_itertools as mi\n"
           "def t():\n"
           "    assertEqual(mi.minmax([2, 3, 1]), (1, 3))\n"
           "    assertEqual(mi.windowed([1, 2], 2), [(1, 2)])\n"
           "    assertEqual(list(mi.take(2, [1, 2])), [1, 2])\n")
    st4: dict = {}
    rows5 = extract_criteria(TUP, TARGET, stats=st4)
    calls = [r["call"] for r in rows5]
    ck("J1 ⛔ 期望是 tuple 的判据**不产出**（JSON 会把 tuple 降成 list）",
       not any("minmax" in c for c in calls), str(calls))
    ck("J2 嵌套 tuple 也拦",
       not any("windowed" in c for c in calls), str(calls))
    ck("J3 ⛔ 反证：list 期望**不**被误拦 ⇒ J1/J2 不是『把所有序列都丢了』",
       any("take" in c for c in calls), str(calls))
    ck("J4 记账且只记 tuple 那两条",
       st4.get("n_tuple_gold") == 2, str(st4))
    try:
        s = (ROOT / "scripts" / "probe_auto_unlock.py").read_text(
            encoding="utf-8")
        ck("J5 ⛔ 三验第 1 步在脚本侧**也**含 `passed`（两处口径必须一致）",
           'a.get("passed")' in s, "脚本侧没验 passed ⇒ 假 F2P 会再回来")
    except OSError as e:  # noqa: BLE001
        skip("J5 脚本侧三验", f"读不到：{e}")

    print("\n== I 组：真实仓库读数（缺失则跳过并披露）==")
    repo = Path("/tmp/realrepo/more-itertools/tests/test_more.py")
    if not repo.exists():
        skip("I 组 真实仓库读数", f"缺 {repo}（未覆盖 ≠ 通过）")
    else:
        try:
            src = repo.read_text(encoding="utf-8", errors="replace")
            st3: dict = {}
            rows4 = extract_criteria(src, TARGET, stats=st3)
            ck("I1 ⛔ 164 那个「0 条」已被打开（同形断言读进来了）",
               len(rows4) > 0, f"{len(rows4)}")
            ck("I2 抽出来的判据**不剩包前缀**",
               sum(1 for r in rows4
                   if r["call"].split("(")[0].startswith("mi.")) < 20,
               str(sum(1 for r in rows4
                       if r["call"].split("(")[0].startswith("mi."))))
            ck("I3 raises 类也抽到了（more-itertools 有 184 条）",
               any(r["kind"] == "raises" for r in rows4))
            brief = {k: v for k, v in st3.items() if k != "unmapped_kinds"}
            top5 = sorted(st3["unmapped_kinds"].items(),
                          key=lambda x: -x[1])[:5]
            print(f"      读数：{brief}")
            print(f"      拒收分布 top5：{top5}")
        except Exception as e:  # noqa: BLE001
            skip("I 组 真实仓库读数", f"跑不起来：{type(e).__name__}: {e}")

    print("\n" + "=" * 74)
    if FAILS:
        print(f"❌ 失败 {len(FAILS)} 项：{FAILS}")
        return 1
    msg = "✅ 同形映射在位、非同形被拦且记账、父包降级试探性且默认不开"
    if SKIPS:
        msg += f"（{len(SKIPS)} 项跳过：未覆盖 ≠ 通过）"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
