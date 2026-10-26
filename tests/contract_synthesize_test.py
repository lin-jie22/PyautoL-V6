#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**判据驱动生成补丁层**（`aether/synthesize.py`）不看基线。

═══ 存在的理由 ═══

README 158 取证出：`revert_*(broken, base)` 只会「按位置对齐回 base」
⇒ `base == broken` 恒等、`base` 写错就跟着错 ⇒ 真实 bug 上「修好」的那一改
是**抄来的**（真实任务域 `env.source` = 修复后源码 = 人类答案）。

本层是补缺口的那一层。它钉死的是一条**结构性**事实：

    synthesize_patch(broken_src, tests)   ← 签名里**没有** base

═══ ⛔ 本契约钉的是机制，不是某次读数 ═══

  A **结构性不偷看**：模块源码里不含 `env.source` / `base_src` / `.source`
    （静态可查 ⇒ 想偷看都偷不到）
  B **量具自检**：一个已知算子级 bug 必须能被本层搜出来（先证尺子可信）
  C ⛔⛔ **刀刃反证（盲从判据）**：把判据的期望改掉 ⇒ 产出必须**跟着判据变**
    —— 与 158 的「盲从基线」对称，恰恰证明信息源**换成了判据**
  D **射程边界（反证）**：结构级缺陷（缺一个分支）⇒ 本层**修不了**
    （它只枚举算子/常量 —— 谁把它当"会修 bug 了"谁就是没读模块头）
  E **不劣化**：`min_gain=1` ⇒ 绝不返回比基线更差的补丁
  F **记账**：`n_ran` / `n_blew` 必须存在（否则「搜不出」与「没跑起来」
    长得一模一样 —— 第 24/25 条）
  G **最小编辑**：产物必须保留格式（行数不变），不是整份 unparse

跑法（离线、秒级）：
    python tests/contract_synthesize_test.py
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读取，不 import）。
SUPPORTED_MODES = ("default",)

#: 用到了 `run_in_sandbox`（子进程沙箱），3.9 起即可跑。
MIN_PYTHON = (3, 9)

from aether.synthesize import (SynthResult, locate_focus,  # noqa: E402
                               score_source, synth_candidates,
                               synthesize_patch)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


#: 已知算子级 bug：`x > 10` 应为 `x >= 10`
TOY_BUGGY = ("def f(x):\n"
             "    if x > 10:\n"
             "        return 'bad'\n"
             "    return 'ok'\n")
#: ⛔ expect 是**值本身**，不是源码字面量（写 `'bad'` 会带上引号，永不匹配）
TOY_TESTS = [{"name": "t1", "call": "f(10)", "expect": "bad"}]

#: 结构级缺陷（缺一个分支）⇒ 本层**射程外**
STRUCT_BUGGY = ("def g(x):\n"
                "    if x < 0:\n"
                "        return 'neg'\n"
                "    return 'pos'\n")
STRUCT_TESTS = [{"name": "s1", "call": "g(0)", "expect": "zero"}]


def main() -> int:
    print("=" * 74)
    print("契约：判据驱动生成补丁层（`aether/synthesize.py`）不看基线")
    print("=" * 74)

    print("\n== A 组：⛔ 结构性不偷看（静态可查）==")
    # ⛔ 必须查 **AST 里的属性访问**，不能查源码文本：模块头 docstring 里
    #    为了解释而提到 `env.source` 是文档、不是调用 ⇒ 文本匹配必然误报。
    import ast
    tree = ast.parse((ROOT / "aether" / "synthesize.py").read_text(
        encoding="utf-8"))
    ck("A1 没有任何 `.source` 属性访问（偷看基线的唯一入口）",
       not any(isinstance(n, ast.Attribute) and n.attr == "source"
               for n in ast.walk(tree)))
    argnames = {a.arg
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                for a in n.args.args}
    ck("A2 没有任何名为 base / base_src 的形参",
       not (argnames & {"base", "base_src"}), f"{sorted(argnames)}")
    sig = inspect.signature(synthesize_patch)
    ck("A3 ⛔⛔ 签名里没有 base 类参数",
       not any(k in sig.parameters for k in ("base", "base_src", "env")),
       f"实际参数：{list(sig.parameters)}")
    ck("A4 ⛔ 必需参数只有 (broken_src, tests)",
       [p.name for p in sig.parameters.values()
        if p.default is p.empty] == ["broken_src", "tests"],
       f"{list(sig.parameters)}")

    print("\n== B 组：量具自检（先证尺子可信）==")
    base_ok, bad, blew = score_source(TOY_BUGGY, TOY_TESTS)
    ck("B1 装置能跑（读数成立）", not blew, str(bad))
    ck("B2 缺陷版确实挂（0/1）", base_ok == 0, f"通过 {base_ok}")
    cands = synth_candidates(TOY_BUGGY, ["f"])
    ck("B3 候选生成器有产出", len(cands) > 0, f"{len(cands)} 个")
    r = synthesize_patch(TOY_BUGGY, TOY_TESTS)
    ck("B4 ⛔ 能搜出修复", r.found, f"gain={r.gain}")
    if r.found:
        line = [l for l in r.patch.splitlines() if l.strip().startswith("if ")][0]
        ck("B5 方向正确（`>=`）", ">=" in line, line.strip())
        ck("B6 产物是最小编辑（行数不变）",
           len(r.patch.splitlines()) == len(TOY_BUGGY.splitlines()))

    print("\n== C 组：⛔⛔ 刀刃反证 —— 产出由**判据**决定，不由基线决定 ==")
    #: 与 158 的「base 写错 ⇒ 修法跟着错」**对称**：
    #: 这里把判据的期望改掉（要求 `f(10)` 返回 'ok'），本层必须跟着判据走。
    flipped = [{"name": "t1", "call": "f(10)", "expect": "ok"}]
    r2 = synthesize_patch(TOY_BUGGY, flipped)
    ck("C1 判据改向后，本层不再产出 `>=` 那个补丁",
       (not r2.found) or ">=" not in r2.patch,
       f"found={r2.found} desc={r2.desc}")
    #: 且原判据下能找到 —— 两边不能都是"找不到"（那就成了恒真的空判据）
    ck("C2 ⛔ 反证不是空跑：原判据下**确实**能找到（C1 才有意义）",
       r.found and r2.patch != r.patch)
    ck("C3 ⛔ 同一个缺陷源码，换判据 ⇒ 换产出",
       (r.patch or "") != (r2.patch or ""))

    print("\n== D 组：⛔ 射程边界（结构级 = 修不了）==")
    r3 = synthesize_patch(STRUCT_BUGGY, STRUCT_TESTS)
    ck("D1 结构级缺陷搜不出补丁（本层只枚举算子/常量）", not r3.found,
       f"found={r3.found} desc={r3.desc}")
    ck("D2 但**必须**记账它搜过（n_cands>0 ⇒ 不是'没开工'）",
       r3.n_cands > 0, f"n_cands={r3.n_cands}")
    ck("D3 ⛔ 定位失败要显式体现在 focus 里",
       isinstance(r3.focus, tuple))

    print("\n== E 组：不劣化 ==")
    #: 一个已经全绿的源码 ⇒ 本层不许"改进"出任何东西（没有增益就不动手）
    green = TOY_BUGGY.replace("x > 10", "x >= 10")
    r4 = synthesize_patch(green, TOY_TESTS)
    ck("E1 ⛔ 基线已全绿 ⇒ 不产出补丁（min_gain=1）", not r4.found)

    print("\n== F 组：⛔ 记账（没有它，'搜不出'与'没跑起来'不可分）==")
    f = SynthResult()
    for k in ("n_cands", "n_ran", "n_blew", "base_ok", "new_ok", "n_tests"):
        ck(f"F {k} 字段存在", hasattr(f, k))
    ck("F gain = new_ok - base_ok", SynthResult(new_ok=25, base_ok=23).gain == 2)
    bad_src = "def f(:\n"          #: 语法错误 ⇒ 跑分必须报 blew 而不是 0 分
    ok, why, blew3 = score_source(bad_src, TOY_TESTS)
    ck("F ⛔ 语法错误的源码 ⇒ blew=True（不当成'0 分'）", blew3, str(why[:1]))
    ok2, why2, blew4 = score_source(123, TOY_TESTS)     #: 非 str（曾实踩）
    ck("F ⛔ source 不是 str ⇒ blew=True（不当成'0 分'）", blew4)

    print("\n== G 组：定位与渲染 ==")
    focus = locate_focus(TOY_BUGGY, ["t1 f(10)"])
    ck("G1 从失败判据名定位到顶层定义", focus == ["f"], str(focus))
    ck("G2 定位不到 ⇒ 返回空（不做全文件搜索）",
       locate_focus(TOY_BUGGY, ["nope"]) == [])
    ck("G3 ⛔ 渲染模式被记账（splice / fallback 分得开）",
       isinstance(r.render_modes, dict) and sum(r.render_modes.values()) > 0)
    #: ⛔ fallback 是"宁可丢格式不可丢语义"的兜底，允许存在，但**必须可见**
    n_fb = r.render_modes.get("fallback", 0)
    print(f"     （本次 fallback {n_fb} / 共 {sum(r.render_modes.values())} —— "
          f"允许存在，但必须在账上可见）")

    print("\n== H 组：⛔ 默认不上场（机制就绪 ≠ 接入生产）==")
    from aether.agent import KNOWN_STRATEGIES
    ck("H1 策略名被认（`_apply_one` 认得）", "synth_operators" in KNOWN_STRATEGIES)
    cli_txt = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")
    ck("H2 ⛔⛔ 生产 cli.py 的技能池里**没有**它 ⇒ 默认不提名",
       "synth_operators" not in cli_txt,
       "（若出现在此处，说明它已默认上场，需重新评估代价）")

    print("=" * 74)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for x in FAILS:
            print("  ✗", x)
        return 1
    print("✅ 合成层确认**不看基线**：签名无 base、产出随判据变、"
          "结构级修不了（射程如实）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
