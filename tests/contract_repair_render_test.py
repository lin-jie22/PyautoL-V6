#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：修法产出必须是**最小编辑**，不许把整份文件重写一遍。

═══ 存在的理由（2026-10-25 实测取证）═══

`aether/repair.py` 五个 `revert_*` 原本都以 `ast.unparse(整棵树)` 收尾。
在真实 bug `93185b2`（boltons #302，`daterange` 在 start == stop 时无限递增）
上，逻辑**改对了**（`start < stop` → `start <= stop`），但产出与人类作者的
真实修复**逐字不一致**：

    人类真实修复 581 行   license 头 32 行在   空行/行内注释在   引号原样
    V6 产出      468 行   **被删**             **被删**          **被重写**

⇒ 这个补丁在真实代码库里**不可提交**。

⛔⛔ 而 87 份契约没有一份会发现它 —— 判据只看**行为**（`verify_source`），
行为是对的 ⇒ 7/7 全绿。更糟的是 V6 自己的题源是 `mutate_source` 用
`ast.unparse` 造出来的 ⇒ **题的源码本身就是规范化的** ⇒ 格式噪声无从显现。
（第 24 / 133 条的形状：量具与被测物同构 ⇒ 缺陷落在量具的盲区里。）

⇒ 判据必须是"拿一段**非规范**的源码（带 license 头、注释、空行、双引号、
分组括号）去修，修完必须与基线**逐字相等**"。

═══ 组 ═══

  A 五个修法各自：修完与基线**逐字一致**（一个字节都不许多/少）
  B ⛔ 反证：把渲染换回旧行为（整份 unparse）⇒ A 组必须转红 ⇒ A 有牙
  C 语义：新旧两种渲染的 **AST 必须相等**（渲染层不许改变语义）
  D fail-closed：修法拒绝动手时**原样返回**（不许顺手重写格式）；
    定位不到改动时回退整份 unparse（丢格式可以，丢语义不行）
  E 改动范围：整个补丁只允许落在**一行**上

═══ 这份契约自己被验证过没有 ═══

  · 修复前（旧行为）：A1–A5 全红（实测：license 头消失、行数 15→9）；
  · 修复后：全绿。
  ⛔ B 组把这个"先红后绿"固化在契约里 —— 只加断言不加反证，就是第 9 条
    （恒真断言）：若哪天有人又把 `ast.unparse(整棵树)` 写回去，A 组必须自己红。

跑法（离线、秒级）：
    python tests/contract_repair_render_test.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aether.repair as R                                          # noqa: E402
from aether.repair import (render_patch, revert_boolops,            # noqa: E402
                          revert_control_flow, revert_literals,
                          revert_strings, revert_unaryops)

#: ⛔ 声明支持的模式（`aether check` 静态读取，不 import）。
#:    不声明 ⇒ 只按 default 跑一遍，且验收会警告"未声明 ≠ 只有一种模式"。
SUPPORTED_MODES = ("default",)

#: ⛔ `ast.unparse` 是 3.9 才有的 —— 本契约在 3.9 上实测全绿（2026-10-25），
#:    低于 3.9 就不该跑（版本闸由框架裁，不在本文件里手写判断，README 145）。
MIN_PYTHON = (3, 9)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


#: ⛔ fixture 必须**非规范**：带 license 头、行内注释、空行、双引号、
#:   分组括号 —— 全是 `ast.unparse` 会抹掉的东西。规范源码测不出这个缺陷。
BASE = '''# Copyright (c) 2024 Example
# SPDX-License-Identifier: BSD-2-Clause
#
# license 头 —— 真实工程每个文件都有，ast.unparse 会整段删掉它。

MODE = "prod"

def step(start, stop):
    """往前走一步。"""
    # 边界：start == stop 时也算一步
    if start == stop and MODE == "prod":
        return 1
    elif start <= stop:        # <- 基线
        return 2
    return 0


def keep(a, b):
    # 分组括号：删掉它语义不变，但那是**人类写的**，不许动
    if keep and (a or b):
        return 3
    return 0
'''

#: (维度名, 修法, 缺陷版源码) —— 缺陷版只改**一个**属于该维度的东西
CASES = [
    ("control_flow", revert_control_flow,
     BASE.replace("elif start <= stop:", "elif start < stop:")),
    ("literals", revert_literals,
     BASE.replace("        return 2", "        return 9")),
    ("boolops", revert_boolops,
     BASE.replace("if start == stop and MODE", "if start == stop or MODE")),
    ("strings", revert_strings,
     BASE.replace('MODE = "prod"', 'MODE = "dev"')),
    ("unaryops", revert_unaryops,
     BASE.replace("if start == stop and MODE", "if start == stop and not MODE")),
]


def _legacy(broken_src, base_src, mutate):
    """旧行为（整份 `ast.unparse`）—— B/C 组的对拍 oracle。"""
    try:
        b = ast.parse(broken_src)
        g = ast.parse(base_src)
        m = mutate(b, g)
    except Exception:                                             # noqa: BLE001
        return broken_src, "unchanged"
    if m is None:
        return broken_src, "unchanged"
    return R._full_unparse(m, broken_src), "full"


def main():
    print("=" * 74)
    print("== A 组：五个修法修完必须与基线**逐字一致** ==")
    for name, fn, broken in CASES:
        out = fn(broken, BASE)
        ck(f"A-{name} 修后与基线逐字相等（不许重写整份）", out == BASE,
           f"行数 {len(broken.splitlines())}→{len(out.splitlines())}；"
           f"丢掉的={[l for l in BASE.splitlines()[:4] if l not in out][:2]}")
        ck(f"A-{name} license 头还在", out.startswith("# Copyright"),
           f"开头={out.splitlines()[:1]}")
        ck(f"A-{name} 行内注释还在", "# <- 基线" in out and "# 边界" in out,
           "注释被 unparse 抹掉了")
        ck(f"A-{name} 双引号风格没被改成单引号", 'MODE = "prod"' in out,
           [l for l in out.splitlines() if "MODE =" in l][:1])

    print("== B 组：⛔ 反证 —— 旧行为在同一批 fixture 上必须**做不到** ==")
    _orig = R.render_patch
    R.render_patch = _legacy
    try:
        legacy_bad = []
        for name, fn, broken in CASES:
            out = fn(broken, BASE)
            if out == BASE:
                legacy_bad.append(name)
        ck("B1 ⛔ 换回旧渲染（整份 unparse）⇒ A 组必须转红 ⇒ A 有牙",
           len(legacy_bad) < len(CASES),
           f"旧渲染下仍然逐字相等的维度：{legacy_bad}（说明 A 组恒真）")
        out_cf = revert_control_flow(CASES[0][2], BASE)
        ck("B2 ⛔ 旧渲染真的会删掉 license 头（缺陷可复现）",
           not out_cf.startswith("# Copyright"),
           "旧渲染下 license 头居然还在 ⇒ 反证无效")
        ck("B3 ⛔ 旧渲染真的会改行数（缺陷可复现）",
           len(out_cf.splitlines()) != len(BASE.splitlines()),
           f"旧渲染行数 {len(out_cf.splitlines())}")
    finally:
        R.render_patch = _orig

    print("== C 组：渲染层不许改变语义（新旧渲染 AST 必须相等）==")
    for name, fn, broken in CASES:
        new = fn(broken, BASE)
        R.render_patch = _legacy
        try:
            old = fn(broken, BASE)
        finally:
            R.render_patch = _orig
        try:
            same = ast.dump(ast.parse(new)) == ast.dump(ast.parse(old))
        except SyntaxError as e:
            same = False
            ck(f"C-{name} 新渲染可解析", False, str(e))
        ck(f"C-{name} 新旧渲染 AST 相等（渲染只许动格式）", same)

    print("== D 组：fail-closed ==")
    #: D1 修法拒绝动手（两棵树该维度节点数不同 ⇒ 遍历序不可信）⇒ 原样返回
    def _refuse(b, g):
        return None

    out, mode = render_patch(CASES[0][2], BASE, _refuse)
    ck("D1 mutate 拒绝动手 ⇒ 原样返回（mode=unchanged）",
       out == CASES[0][2] and mode == "unchanged", f"mode={mode}")
    ck("D2 原样返回时注释/格式一个字节都没动",
       out.startswith("# Copyright") and "# <- 基线" in out)

    #: D3 mutate 抛异常 ⇒ 原样返回，不许把异常吞成"半改过的源码"
    def _boom(b, g):
        raise RuntimeError("变异器炸了")

    out, mode = render_patch(CASES[0][2], BASE, _boom)
    ck("D3 mutate 抛异常 ⇒ 原样返回（mode=unchanged）",
       out == CASES[0][2] and mode == "unchanged", f"mode={mode}")

    #: D4 定位不到改动（返回一个全新树 ⇒ 没有任何节点在原文本里有位置）
    def _elsewhere(b, g):
        return ast.parse("x = 1\n")

    out, mode = render_patch(CASES[0][2], BASE, _elsewhere)
    ck("D4 ⛔ 定位不到改动 ⇒ 回退整份 unparse（mode=fallback，不丢语义）",
       mode == "fallback" and ast.dump(ast.parse(out)) == ast.dump(ast.parse("x = 1\n")),
       f"mode={mode}")

    print("== E 组：补丁只允许落在一行上 ==")
    import difflib
    for name, fn, broken in CASES:
        out = fn(broken, BASE)
        d = list(difflib.unified_diff(broken.splitlines(), out.splitlines(), n=0))
        changed = [l for l in d if l[:1] in "+-" and not l.startswith(("---", "+++"))]
        ck(f"E-{name} 改动只落在 1 行（+/- 合计 2 行）", len(changed) <= 2,
           f"改动 {len(changed)} 行：{changed[:4]}")

    print("=" * 74)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print("  ✗", f)
        return 1
    print("✅ 五个修法都是最小编辑：修完与基线逐字一致，且反证证明 A 组有牙")
    return 0


if __name__ == "__main__":
    sys.exit(main())
