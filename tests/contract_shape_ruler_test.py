#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：⛔ **「形态」那把尺子本身必须可信** —— 它决定了 V6 射程的天花板。

═══ 为什么必须有这份契约 ═══

153 的形态普查给出「真实修复 84.9% 结构级、真算子级只有 5.5%」，
159/160 都拿这个数当**分寸**用。可那把尺子的口径从来没被质疑过：

    旧口径：整个 Module 树同构 + 差异只在算子/常量 ⇒ 才算「可达」

⇒ 一个修复只要 commit 里顺手改了别处（重构、加 import、调类结构），
  整棵树立刻不同构 ⇒ 被判不可达。**哪怕核心修复就是那个 `>` → `>=`。**

本契约盯的是 `scripts/probe_shape_ruler.py`（重测用新尺子）：

    **不再问「整棵树同不像」，改问「有没有一个顶层 def/class，它内部从
    buggy 到 fixed 的变化全落在算子和常量上 —— 一次 INSERT/DELETE 都不用」**

═══ ⛔⛔ 重测的结论是「否决」：旧的没有偏 ═══

| | 数量 | 占比 |
|---|---|---|
| 旧口径 operator | 11/215 | **5.1%** |
| 新口径（单元级） | 13/215 | 6.0% |

153 报 5.5%，独立重测得 5.1% ⇒ **旧尺子没偏**。放宽到单元级只多救回 2 个，
且那 2 个的差异全是 `const_str`（改错误消息文本），不是假设的「别处连坐」。

⛔ 所以这份契约的首要作用是：**防止有人再来一轮"把口径放宽就能多修几个"
的尝试**。它把这个负值钉住，并且给出形态墙的硬度分布（202 个不可达里，
只有 7 个能靠"扩候选集到搬运已有表达式"解决，143 个必须凭空造新东西）。

═══ 组 ═══

  A 量具离线自检（**同一份实现**，契约不抄第二遍 ⇒ 不会漂移成两把尺子）
  B ⛔ 判档性质：docstring 不算变化 / 别处改动不连坐
  C ⛔⛔ 候选集映射不许漂移（合成层加一类 ⇒ 尺子必须跟着知道）
  D 诚实披露：依赖真实仓库的两条自检**不在**契约覆盖内

═══ 残留（不在契约内，如实记）═══

  · S1/S2 需要 `/tmp/realrepo/boltons`，不可离线回归 ⇒ 只记录，不断言。
  · 全量读数依赖那个仓库的存在与 git history，本契约不做数值断言
    （否则仓库一变就假红）。数值结论记录在 README 161。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

FAILS: list = []
N: int = 0

#: ⛔ 声明支持的模式（`aether check` 静态读取，不 import）。
SUPPORTED_MODES = ("default",)

#: 本契约只做 AST 分析 + 调 `synth_candidates`（**不**跑沙箱）⇒ 秒级。
MIN_PYTHON = (3, 9)


def ck(name: str, cond: bool, detail: str = "") -> bool:
    global N
    N += 1
    if not cond:
        FAILS.append(f"{name}  {detail}".rstrip())
    print(f"  {'✅' if cond else '❌'} {name}" + (f"   {detail}" if detail else ""))
    return bool(cond)


def group(title: str) -> None:
    print(f"\n== {title} ==")


def main() -> int:
    print("=" * 74)
    print("契约：形态尺子本身必须可信（它决定 V6 射程的天花板）")
    print("=" * 74)

    import probe_shape_ruler as R

    group("A 量具离线自检（⛔ 唯一实现，契约不得自己抄一份）")
    st = R.selftest_offline()
    for nm, cond, detail in st:
        ck(nm, cond, detail)
    ck(f"A-汇总 {len(st)} 条离线自检全绿", all(c for _, c, _ in st),
       f"失败 {[n for n, c, _ in st if not c]}")

    group("B ⛔ 判档性质")
    #: B1 曾经造成 8 个假阳性的那个形状：核心是结构改动，顺手改了 docstring
    core_struct = ('def f(x):\n    """old."""\n    return x\n')
    core_struct_fx = ('def f(x):\n    """new and improved, much longer."""\n'
                      '    y = x\n    return y\n')
    ck("B1 ⛔ 核心是结构改动 + 顺手改 docstring ⇒ 必须判 X"
       "（第一版它被那个 docstring 骗成『可达』，是 8 个假阳性的全部来源）",
       R.analyze(core_struct, core_struct_fx)["reach"] == "X",
       str(R.analyze(core_struct, core_struct_fx)["units"]))
    #: B2 反过来：核心是 cmp，别处随便改 —— 不许连坐
    mixed = ('def f(x):\n    if x > 10:\n        return 1\n'
             'def g(y):\n    return y\n')
    mixed_fx = ('def f(x):\n    if x >= 10:\n        return 1\n'
                'def g(y):\n    z = y\n    w = z + y + z\n    return w\n')
    ck("B2 核心 cmp + 另一个函数被大改 ⇒ 仍判 L3（别处不连坐）",
       R.analyze(mixed, mixed_fx)["reach"] == "L3",
       str(R.analyze(mixed, mixed_fx)["units"]))
    #: B3 必须新增语句的，任何档都不许放过去
    ins = ("def f(x):\n    return x\n")
    ins_fx = ("def f(x):\n    if x < 0:\n        x = -x\n    return x\n")
    ck("B3 ⛔ 必须 INSERT 语句 ⇒ X（连 extend 也不许放）",
       R.analyze(ins, ins_fx)["reach"] == "X",
       f"reach={R.analyze(ins, ins_fx)['reach']}"
       f" extend={R.analyze(ins, ins_fx)['extend']}")

    group("C ⛔⛔ 候选集映射不许漂移")
    #: 若有人给 synthesize 加了一类候选而尺子不知道 ⇒ L3 会系统性低估射程
    from aether.synthesize import synth_candidates

    probe = ("def hot(x):\n"
             "    if x > 10:\n"
             "        s = x + 2\n"
             "        t = s\n"
             "        return t\n"
             "    return 0\n")
    got = {d.split("@")[0] for d, _src, _m in synth_candidates(probe, ["hot"])}
    ck("C1 玩具源码上确实枚举出了候选（先证量具有产出）", bool(got), f"{got}")
    known = set(getattr(R, "_SYNTH_DESC_MAP", {}))
    extra = got - known
    ck("C2 ⛔ 合成层的每一类候选，尺子都必须认识"
       "（否则新增类别后 L3 会静默低估射程）",
       not extra, f"未登记的类别：{extra}")
    ck("C3 _SYNTH_DESC_MAP 的值域 == SYNTH_KINDS（两头不许各写一串）",
       set(R._SYNTH_DESC_MAP.values()) == R.SYNTH_KINDS,
       f"map={sorted(set(R._SYNTH_DESC_MAP.values()))} "
       f"kinds={sorted(R.SYNTH_KINDS)}")

    group("D 诚实披露")
    print("  ⓘ S1(c1c25da⇒L3) / S2(5eb6cc0⇒X) 依赖 /tmp/realrepo/boltons，")
    print("    不可离线回归 ⇒ **不在本契约覆盖内**，需手动跑 probe_shape_ruler.py。")
    print("  ⓘ 全量数值结论（5.1% / 6.0% / 7 个可扩 / 143 个需凭空造）")
    print("    记在 README 161，本契约不做数值断言（仓库一变就会假红）。")

    print("\n" + "=" * 74)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for x in FAILS:
            print("  ✗", x)
        return 1
    print(f"✅ 形态尺子可信：{N} 项全绿"
          "（⛔ 结论是**否决** —— 旧口径没偏，别再放宽了）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
