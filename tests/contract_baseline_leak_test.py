#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：修法族是「对齐基线」，不是「生成修复」—— 这条必须钉死。

═══ 存在的理由（2026-10-26 对照实验）═══

真实任务域里 `env.source`（基线）用的是**修复后**的源码，而五个 `revert_*`
的语义是"把 broken 按位置对齐回 base"。于是：

    base 里有答案 ⇒ 修得好；base 里没有 ⇒ 一个字节也造不出来。

同一道真实 bug（boltons `c1c25da`），只改基线来源：

| 组 | 基线 | 提议器 | 结果 |
|---|---|---|---|
| A | 修复后源码 | 模板 | 轮 1 `fix_control_flow` reward=1.0 ⇒ **25/25 修好** |
| B | 缺陷版本身 | 模板 | 3 轮 **0 提交**，23/25 依旧失败 |
| C | 缺陷版本身 | 真模型（已确认调用成功） | 3 轮 **0 提交**，23/25 依旧失败 |

⇒ 「第 1 轮修好真实 bug」里，**改对那一行是从基线抄的**；系统自己贡献的是
「定位到那一处」。自造题上这没区别（题就照基线变异而来），真实 bug 上它是
抄答案。

═══ ⛔ 本契约钉的是机制，不是某次读数 ═══

  · **恒等性**：`base == broken` ⇒ 五个修法必须返回输入原文（无操作）。
    若哪天某个修法在 base=broken 时还产出改动，说明它开始"自作主张"
    ——那要单独评估，不许悄悄混进"通用修法"。
  · **盲从性（反证）**：base 换成一份**错的**源码（`>=` 写成 `<=`）⇒
    修法必须跟着产出那个错的值。这证明改动的来源**是 base**，不是判据、
    不是语义、不是"修" —— 这就是"抄"的可执行定义。

═══ 组 ═══

  A 恒等：base=broken ⇒ 五个修法逐字返回输入
  B 有效：base=fixed ⇒ 至少 control_flow 产出改动（证明 A 不是"修法本就空转"）
  C ⛔⛔ 反证（盲从）：base 是**错的** ⇒ 修法产出那个错的值
  D 实验开关：`--baseline {fixed,buggy}` 两档都在（否则对照无从复现）

跑法（离线、秒级）：
    python tests/contract_baseline_leak_test.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读取，不 import）。
SUPPORTED_MODES = ("default",)

#: 纯 AST 逻辑，3.9 起即可跑。
MIN_PYTHON = (3, 9)

from aether.repair import (revert_boolops, revert_control_flow,  # noqa: E402
                           revert_literals, revert_strings,
                           revert_unaryops)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


#: boltons `c1c25da` 的真实修复形态：`val > 2 ** len_` ⇒ `val >= 2 ** len_`
BUGGY = ("def check(val, len_):\n"
         "    if val > 2 ** len_:\n"
         "        raise ValueError(val)\n"
         "    return val\n")
FIXED = BUGGY.replace("val > 2", "val >= 2")
#: ⛔ **错的反面**：基线里放一个错的值，用来证明修法盲从 base
WRONG = BUGGY.replace("val > 2", "val <= 2")

ALL = (revert_control_flow, revert_literals, revert_boolops,
       revert_strings, revert_unaryops)


def main() -> int:
    print("=" * 74)
    print("契约：修法族 = 「对齐基线」而非「生成修复」（基线泄漏）")
    print("=" * 74)

    print("\n== A 组：⛔ base == broken ⇒ 五个修法必须逐字返回输入 ==")
    for fn in ALL:
        out = fn(BUGGY, BUGGY)
        ck(f"A {fn.__name__} 恒等（无操作）", out == BUGGY,
           f"产出 {len(out.splitlines())} 行，与输入{'相同' if out == BUGGY else '不同'}")

    print("\n== B 组：base = 修复版 ⇒ 必须产出改动（A 组不是'本就空转'）==")
    out_f = revert_control_flow(BUGGY, FIXED)
    ck("B1 control_flow 在 base=fixed 时产出改动", out_f != BUGGY)
    ck("B2 改动就是基线里的那个值（>=）", "val >= 2 ** len_" in out_f,
       f"{[l for l in out_f.splitlines() if 'val' in l and '2 **' in l]}")
    #: 其余四个维度在这道题上应当无改动（它只改了比较符）——这是**形态**
    #: 决定的，不是缺陷。记下来，免得将来误以为它们坏了。
    for fn in (revert_literals, revert_boolops, revert_strings,
               revert_unaryops):
        ck(f"B3 {fn.__name__} 在这道题上无改动（形态所然，非缺陷）",
           fn(BUGGY, FIXED) == BUGGY)

    print("\n== C 组：⛔⛔ 反证 —— 基线是**错的**时，修法跟着错 ==")
    out_w = revert_control_flow(BUGGY, WRONG)
    ck("C1 基线写 `<=` ⇒ 修法产出 `<=`", "val <= 2 ** len_" in out_w,
       f"{[l for l in out_w.splitlines() if '2 **' in l]}")
    ck("C2 且不再含原来的 `>` 语义", "val > 2" not in out_w, "")
    #: ⛔ 这一条是整个契约的刀刃：改动的来源**只能是 base**。若修法是"修"，
    #:    它就不该把一个 raise 条件从 `>` 改成 `<=`（那只会让更多输入通过
    #:    检查，方向完全相反）。它照做了 ⇒ 它在对齐，不在修复。
    ck("C3 ⛔ 产出由 base 唯一决定（换个 base 就换个产出）",
       out_f != out_w and "val >= 2 ** len_" in out_f
       and "val <= 2 ** len_" in out_w)

    print("\n== D 组：对照实验的开关必须在（否则结论无从复现）==")
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "realbug_experiment.py"),
         "--help"], capture_output=True, text=True, timeout=60)
    ck("D1 实验脚本可跑 --help", p.returncode == 0, f"rc={p.returncode}")
    ck("D2 ⛔ 有 --baseline 开关", "--baseline" in p.stdout)
    ck("D3 ⛔ 两档都在（fixed / buggy）",
       "fixed" in p.stdout and "buggy" in p.stdout)
    ck("D4 ⛔ 有 --proposer 开关（llm 那一格要能复现）",
       "--proposer" in p.stdout)

    print("=" * 74)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print("  ✗", f)
        return 1
    print("✅ 修法族确认是『对齐基线』：base 无答案 ⇒ 不产出，base 错 ⇒ 跟着错")
    return 0


if __name__ == "__main__":
    sys.exit(main())
