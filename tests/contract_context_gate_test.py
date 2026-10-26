#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**上下文门**的量具（内联器 + 三档判档）必须守住四条自限。

═══ 存在的理由（2026-10-26 实测取证；⛔ 结论是负的）═══

README 153 记了三道**串联**的门：形态门 / 判据门 / 上下文门。判据门已开
（156）。本轮量的就是第三道：真实断言里 **81.1%（1526/1882）在参照实现上
根本求值不了** —— 因为断言表达式依赖测试函数的局部变量 / fixture，单拎出
来必然 `NameError`。

于是做了「局部赋值常量传播内联」并配了四条自限。结论是**收益 0**：

    内联救回 616 条能求值的探针，但**真有鉴别力的只有 13 条**；
    commit 级 0 → ≥1 条鉴别力的只有 4 个，而那 4 个**形态门全是
    structural** ⇒ 三道门串联 ⇒ **没有一个真实 bug 靠开上下文门变可解**。

⛔ 这份契约不是"锁住一个已上线的能力"，而是**锁住一把已经校准过的尺子**：

    · 内联器将来若被接进导入器，救回率会被拿去当收益报 —— 而实测证明
      「能求值」与「有鉴别力」差 47 倍（616 vs 13）。本契约把这两档
      **钉成两个不同的数**，谁合并谁就是重复 2026-10-26 的误读。
    · 四条自限里任何一条被放宽，救回数都会变好看而语义变脏 ⇒ 每条自限
      都带**反证**：放宽它，就会有本该被拒收的探针被采纳。

═══ ⛔ 金标准的来源（本契约最要紧的一条）═══

    内联有没有改坏语义，唯一的裁判是**测试自己写死的 expected**
    （`chk(actual, expected)` 里那个 expected），不是"能不能跑"。
    能跑但值不对 = 假鉴别力（它在 fixed 上过、buggy 上挂，看起来完美）。

═══ 组 ═══

  A 内联器基本正确性（绑定 / 内联 / 递归内联 / 没得可展返回 None）
  B ⛔ 四条自限，每条各带反证
  C 判档四档（none / mismatch / ok / ok_nogold）
  D ⛔⛔ 反证：能求值 ≠ 语义对；有判据 ≠ 有鉴别力（本轮 616 vs 13 的机制）
  E 量具自检可复现（`selftest()` 全绿）

跑法（离线、秒级，不依赖任何 /tmp 下的真实仓库）：
    python tests/contract_context_gate_test.py
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读取，不 import）。
SUPPORTED_MODES = ("default",)

#: 纯 AST 逻辑，3.9 起即可跑。
MIN_PYTHON = (3, 9)

#: 探针脚本在 scripts/ 下（不是包），按路径加载。
_SPEC = importlib.util.spec_from_file_location(
    "probe_context_gate", ROOT / "scripts" / "probe_context_gate.py")
CG = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CG)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


SRC_SCOPE = """
def t1():
    v = 'a'
    chk(v.upper(), 'A')

def t2():
    v = 'b'
    chk(v.upper(), 'B')
"""

SRC_LOOP = """
def t():
    x = 1
    x = 2
    chk(x, 2)
"""

SRC_CHAIN = """
def t():
    a = 3
    b = a + 1
    chk(b * 2, 8)
"""

SRC_TIME = """
def t():
    d = date.today()
    chk(d.isoformat(), '2020-01-01')
"""


def main() -> int:
    print("=" * 74)
    print("契约：上下文门量具 —— 内联器四条自限 + 三档判档")
    print("=" * 74)

    print("\n== A 组：内联器基本正确性 ==")
    a = CG.scoped_assertions(SRC_SCOPE)
    ck("A1 抽到断言并记下所属函数",
       [x[2] for x in a] == ["t1", "t2"], f"{[x[2] for x in a]}")
    binds = CG.collect_bindings(SRC_SCOPE)
    ck("A2 绑定按作用域分开",
       [sorted(v) for v in (binds["t1"], binds["t2"])] == [["v"], ["v"]],
       f"{ {k: sorted(v) for k, v in binds.items()} }")
    ck("A3 内联用**本作用域**的绑定（t1 的 v 是 'a'）",
       CG.inline_expr("v.upper()", binds["t1"]) == "'a'.upper()",
       f"{CG.inline_expr('v.upper()', binds['t1'])}")
    ch = CG.collect_bindings(SRC_CHAIN)["t"]
    ck("A4 递归内联（b → a+1 → 3+1）",
       CG.inline_expr("b * 2", ch) == "(3 + 1) * 2",
       f"{CG.inline_expr('b * 2', ch)}")
    ck("A5 没有可展的名字 ⇒ 返回 None（不许原样返回冒充救回）",
       CG.inline_expr("zzz(1)", ch) is None,
       f"{CG.inline_expr('zzz(1)', ch)!r}")

    print("\n== B 组：⛔ 四条自限，每条带反证 ==")
    ck("B1 自限① 作用域隔离：t2 的 v 不串到 t1",
       CG.inline_expr("v.upper()", binds["t2"]) == "'b'.upper()",
       f"{CG.inline_expr('v.upper()', binds['t2'])}")
    lb = CG.collect_bindings(SRC_LOOP)
    ck("B2 自限② 同名两个不同 rhs ⇒ 整条剔除（循环里重复赋值）",
       lb["t"] == {}, f"{ {k: sorted(v) for k, v in lb.items()} }")
    #: ⛔ 反证：若放宽②，`x` 会被内联成第一个 rhs（1）⇒ 与测试写死的 2 不符
    #:    ⇒ 造出一条"能求值但语义错"的探针。这证明②不是洁癖。
    if lb["t"]:
        ck("B2b ⛔ 反证：放宽②必然产出与金标准不符的探针", False,
           "歧义绑定没被剔除")
    else:
        ck("B2b ⛔ 反证（构造）：歧义若被内联为首个 rhs=1 ⇒ 与 gold=2 不符",
           CG.classify_probe("x", "2", "1") == "mismatch",
           f"{CG.classify_probe('x', '2', '1')}")
    tb = CG.collect_bindings(SRC_TIME)["t"]
    inl = CG.inline_expr("d.isoformat()", tb)
    ck("B3 自限③ 时间依赖 ⇒ 拒收",
       inl is not None and CG.has_unstable_call(inl), f"{inl}")
    ck("B3b 无时间依赖的普通表达式不受影响",
       not CG.has_unstable_call("Bits('01').as_bin()"))
    ck("B4 自限④ 与测试写死 expected 不符 ⇒ 判 mismatch（不算救回）",
       CG.classify_probe("x", "7", "7") == "ok"
       and CG.classify_probe("x", "7", "8") == "mismatch", "")

    print("\n== C 组：判档四档 ==")
    ck("C1 求值不了 ⇒ none", CG.classify_probe("x", "1", None) == "none")
    ck("C2 能求值但与 gold 不符 ⇒ mismatch",
       CG.classify_probe("x", "1", "2") == "mismatch")
    ck("C3 能求值且与 gold 一致 ⇒ ok",
       CG.classify_probe("x", "'a'", "'a'") == "ok")
    ck("C4 gold 不是字面量 ⇒ ok_nogold（**不许猜**，也不许当已验证）",
       CG.classify_probe("x", "expected_var", "5") == "ok_nogold")
    ck("C5 gold 提取：非字面量 ⇒ None",
       CG.gold_of("expected_var") is None and CG.gold_of("3") == 3)

    print("\n== D 组：⛔⛔ 反证 —— 能求值 ≠ 语义对；有判据 ≠ 有鉴别力 ==")
    #: ⛔ 这是本轮最要紧的一条不变量：实测 616 条"救回"里只有 13 条真有
    #:    鉴别力。若把两者合并计数，就会得到"上下文门救回 616 条"的假收益。
    #: ⛔ 一律走脚本自己的 `run_all` 解析（它在 `got=` 后面还要剥掉
    #:    ` (truthy check)` 后缀）。契约里另写一份解析 ⇒ 两边不一致时
    #:    判据会假红（第一版就是这样红的），而红的原因与被测物无关。
    fixed = "def f(x):\n    return x * 2\n"
    r1 = CG.run_all(fixed, [("k0", "f(3)", None)], timeout=10.0)
    got = (r1.get("k0") or {}).get("got")
    ck("D1 探针在参照实现上能求值（先证装置有效）", got == "6", f"{got!r}")
    ck("D2 ⛔ 能求值 + 稳定，但值与测试写死期望不符 ⇒ mismatch ⇒ 不进救回",
       CG.classify_probe("f(3)", "7", "6") == "mismatch",
       "mismatch 若被算作救回，616 这个数就是假的")
    #: 有判据但无鉴别力：fixed 与 buggy 给出同一个值（P2P 回归保护，不是 F2P）
    buggy = "def f(x):\n    return x * 2\n    # 缺陷在别处\n"
    rb = CG.run_all(buggy, [("k0", "f(3)", None)], timeout=10.0)
    g_bug = (rb.get("k0") or {}).get("got")
    ck("D3 ⛔ 语义对（ok）但 fixed 与 buggy 同值 ⇒ **不算收益**",
       CG.classify_probe("f(3)", "6", got) == "ok" and got == g_bug == "6",
       f"fixed={got!r} buggy={g_bug!r}")
    ck("D4 判档函数对 raises 类：不 passed ⇒ none（没有 mismatch 这一说）",
       CG.classify_probe("f(3)", "ValueError", None) == "none")

    print("\n== E 组：量具自检可复现 ==")
    ck("E1 selftest() 全绿", CG.selftest() is True)

    print("=" * 74)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print("  ✗", f)
        return 1
    print("✅ 内联器四条自限在位，且『能求值』与『有鉴别力』是分开的两个数")
    return 0


if __name__ == "__main__":
    sys.exit(main())
