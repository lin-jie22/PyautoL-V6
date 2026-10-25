#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：出题器 `mutate_source` 也必须走**最小编辑**，不许把整份题面重写。

═══ 存在的理由（2026-10-25 实测取证）═══

154 条把**修法**的产出改成最小编辑了，但**出题器** `mutate_source` 自己
仍然以 `ast.unparse(整棵树)` 收尾 ⇒ 合成题的源码是**规范化**的：

    真实源码  251 行    license 头在    注释在    空行在    引号原样
    旧题面    212 行    **被删**        **被删**  **被删**  **被重写**

⛔ 这不只是"不好看"，它是一道**结构性的遮蔽**：题面与 `ast.unparse` 同构
⇒ 154 条那种"补丁把格式全抹掉"的缺陷，在 V6 自己的语料上**永远不显现**
（第 24 / 133 条：量具与被测物同构 ⇒ 缺陷落在量具盲区）。题面必须像真实
代码，判据视野里才会有真实代码的问题。

⛔⛔ 而"题面变了"是有风险的：判据是在题面上**真跑**出来的
（`verify_source` 执行源码）。只改渲染不动语义的推断不成立 —— 必须实测
"格式变了之后判据仍给出同样的结果"。

═══ 组 ═══

  A 在非规范 fixture（license 头 / 行内注释 / 空行 / 双引号）上变异：
    题面必须保留 license 头、注释、空行、引号，且**行数不变**
  B ⛔ 反证：换回旧行为（整份 unparse）⇒ A 必须转红 ⇒ A 不是恒真断言
  C 语义：新旧题面的 **AST 必须相等**（出题只许动那一个变异点）
  D ⛔ **鉴别力**：题面格式变了之后，判据在题面上仍必须**失败**、在基线上
    仍必须**通过** ⇒ 合成出来的仍然是一道"题"，不是一道恒过/恒挂的假题
  E 改动范围：整份题面只允许**一处**改动
  F ⛔ **向下兼容**：`mutate_source` 必须同时接受 **str 与 AST 对象**
    （2026-10-26 全量验收抓到的真回归：`contract_aging_test.py:159` 传的是
    `ast.Module`。旧实现走 `ast.parse()` ⇒ 底层 `compile()` 接受 AST ⇒ 一直
    能用；最小编辑渲染要走 `source.splitlines()` ⇒ 传 Module 直接崩。
    3 处契约依赖这个既有行为 ⇒ 不许为了迁就新实现去改判据。）

═══ 这份契约自己被验证过没有 ═══

  · 真注入验证：把 `mutate_source` 临时改成强制旧行为 ⇒ A/B/D 转红
    （实测报"新题面一处都没更接近基线 ⇒ 不许合"）⇒ 复原回绿。
  · ⛔ 第一版 D 组是**恒真断言**：`verify_source` 的真实签名是
    `verify_source(env, source=...)`，被写成 `verify_source(src, t)`
    ⇒ 每条都抛 AttributeError ⇒ 新旧签名都是一串 `ERR` ⇒ "一致" 恒真。
    ⇒ D 组必须带上 checks 条数与通过数，空了直接判失败。

跑法（离线、秒级）：
    python tests/contract_mutate_render_test.py
"""
import ast
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aether.environment as E                                     # noqa: E402
from aether.environment import (Environment, mutate_source,         # noqa: E402
                                synthesize_tasks)

#: ⛔ 声明支持的模式（`aether check` 静态读取，不 import）。
SUPPORTED_MODES = ("default",)

#: ⛔ `ast.unparse` 是 3.9 才有的；本契约在 3.9 上实测全绿（2026-10-25）。
MIN_PYTHON = (3, 9)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


#: ⛔ fixture 必须**非规范**：全是 `ast.unparse` 会抹掉的东西。
#:   用规范源码（例如直接 `ast.unparse` 出来的）测不出本条缺陷。
FIXTURE = '''# Copyright (c) 2024 Example
# SPDX-License-Identifier: BSD-2-Clause
#
# license 头 —— 真实工程每个文件都有，ast.unparse 会整段删掉它。

MODE = "prod"


def step(start, stop):
    """往前走一步。"""
    # 边界：start == stop 时也算一步
    if start == stop and MODE == "prod":
        return 1
    elif start <= stop:        # <- 变异点
        return 2
    return 0
'''


def legacy_mutate(source: str, which: int = 0,
                  kinds=E.DEFAULT_MUTATION_KINDS):
    """旧行为（改动前），逐字复原 —— B 组反证与 C 组对拍都要用它。"""
    tree = ast.parse(source)
    m = E._Mutator(which, kinds)
    new_tree = m.visit(tree)
    ast.fix_missing_locations(new_tree)
    if not m.done:
        raise ValueError(f"没有可变异点（which={which}）")
    return ast.unparse(new_tree), m.desc


def _changed_lines(a: str, b: str):
    d = difflib.unified_diff(a.splitlines(), b.splitlines(), n=0)
    return [l for l in d if l[:1] in "+-" and not l.startswith(("---", "+++"))]


def _verdict(env, src: str, timeout: float = 10.0):
    """在源码 `src` 上真跑判据。

    ⛔ 签名是 `verify_source(env, source=...)`。写反了不会报错 —— 只会
    让每条检查都抛异常，于是新旧都变成 `ERR`，"一致" 恒真（第 9 条）。
    """
    v = E.verify_source(env, source=src, timeout=timeout)
    return v, len(v.checks or ()), sum(1 for c in (v.checks or ()) if c.passed)


def main() -> int:
    print("=" * 74)
    print("契约：出题器 mutate_source 必须是最小编辑")
    print("=" * 74)

    # ── 找出哪些 which 在这个 fixture 上真的能变异 ──────────────────────
    sites = []
    for which in range(12):
        try:
            mutate_source(FIXTURE, which=which)
            sites.append(which)
        except Exception:                                          # noqa: BLE001
            continue
    print(f"\n== 前置：fixture 上可变异点 {len(sites)} 个（which={sites}）==")
    ck("前置 fixture 上至少有 3 个可变异点", len(sites) >= 3,
       f"实得 {sites}")

    print("\n== A 组：题面必须保留格式（license 头 / 注释 / 空行 / 引号）==")
    for w in sites[:3]:
        out, desc = mutate_source(FIXTURE, which=w)
        ck(f"A-w{w} license 头还在", out.startswith("# Copyright (c) 2024"),
           repr(out[:40]))
        ck(f"A-w{w} 行内注释还在", "# <- 变异点" in out and "# 边界" in out)
        ck(f"A-w{w} 空行还在（源码含 '\\n\\n'）", "\n\n" in out)
        ck(f"A-w{w} 双引号还在", 'MODE = "prod"' in out)
        ck(f"A-w{w} 行数不变", len(out.splitlines()) == len(FIXTURE.splitlines()),
           f"{len(FIXTURE.splitlines())}→{len(out.splitlines())}")
        ck(f"A-w{w} 改动只有一处", len(_changed_lines(FIXTURE, out)) <= 2,
           f"{_changed_lines(FIXTURE, out)}")

    print("\n== B 组：⛔ 反证 —— 旧行为做不到，A 组才有牙 ==")
    w0 = sites[0]
    new_src, _ = mutate_source(FIXTURE, which=w0)
    old_src, _ = legacy_mutate(FIXTURE, which=w0)
    ck("B1 旧行为真的会删掉 license 头",
       not old_src.startswith("# Copyright (c) 2024"), repr(old_src[:40]))
    ck("B2 旧行为真的会改行数",
       len(old_src.splitlines()) != len(FIXTURE.splitlines()),
       f"{len(FIXTURE.splitlines())}→{len(old_src.splitlines())}")
    ck("B3 旧行为真的会删行内注释", "# <- 变异点" not in old_src)
    ck("B4 旧行为真的会把双引号改成单引号", 'MODE = "prod"' not in old_src)
    #: ⛔ 两条**都**要：B1–B4 证明旧行为做不到，B5 证明新旧产出确实不同
    #:    （只写 B5 而不写 B1–B4 ⇒ "不同" 可能只是改了别的地方）
    ck("B5 ⛔ 新旧产出确实不同（否则 A 组可能只是恒真）", old_src != new_src)
    ck("B6 新行为满足 B1–B4 的**反面**（真的保住了格式）",
       new_src.startswith("# Copyright (c) 2024")
       and "# <- 变异点" in new_src
       and 'MODE = "prod"' in new_src
       and len(new_src.splitlines()) == len(FIXTURE.splitlines()))

    print("\n== C 组：语义 —— 新旧题面 AST 必须相等 ==")
    for w in sites[:3]:
        new_src, _ = mutate_source(FIXTURE, which=w)
        old_src, _ = legacy_mutate(FIXTURE, which=w)
        ck(f"C-w{w} AST 相等",
           ast.dump(ast.parse(new_src)) == ast.dump(ast.parse(old_src)))

    print("\n== D 组：⛔ 鉴别力 —— 格式变了，题还是不是题 ==")
    root = ROOT / "tasks"
    dirs = sorted(d for d in root.iterdir()
                  if d.is_dir() and (d / "task.py").exists()) if root.is_dir() else []
    ck("D0 仓库内有环境目录可跑", len(dirs) > 0, f"{root} 下 {len(dirs)} 个")

    n_env = 0
    n_task_total = 0
    for d in dirs[:4]:
        env = Environment.from_dir(str(d), env_id=d.name)
        base = env.source
        v_base, n_base, pass_base = _verdict(env, base)
        if n_base == 0:
            continue
        n_env += 1
        ck(f"D-{d.name} 基线判据非空（{n_base} 条）", n_base > 0)

        #: ⛔⛔ 判据必须是 **ABBA 对拍**（新旧出题器出题数相等），不是
        #:    "必须 > 0"。实测 `_inspect` 环境**新旧都出 0 道**（有变异点
        #:    但变异后判据无鉴别力，被 synthesize_tasks 滤掉）—— 那是环境
        #:    本身的属性，不是本次改动造成的。写 "必须 > 0" 会对着一个
        #:    与本次改动无关的既有事实报错（第 71 条）。
        tasks_new, tasks_old = [], []
        try:
            tasks_new = list(
                (synthesize_tasks(env, max_n=2, timeout=10.0).tasks or []))
        except Exception:                                          # noqa: BLE001
            pass
        _orig = E.mutate_source
        try:
            E.mutate_source = legacy_mutate
            tasks_old = list(
                (synthesize_tasks(env, max_n=2, timeout=10.0).tasks or []))
        except Exception:                                          # noqa: BLE001
            pass
        finally:
            E.mutate_source = _orig
        ck(f"D-{d.name} ⛔ 出题数与旧出题器一致",
           len(tasks_new) == len(tasks_old),
           f"新 {len(tasks_new)} 旧 {len(tasks_old)}")
        n_task_total += len(tasks_new)

        tasks = tasks_new
        for t in tasks:
            src = t.broken_source
            v_new, n_new, pass_new = _verdict(env, src)
            src_old = ast.unparse(ast.parse(src))
            _, _, pass_old = _verdict(env, src_old)
            ck(f"D-{d.name}/{t.task_id} 判据条数非空", n_new > 0,
               f"{n_new} 条")
            ck(f"D-{d.name}/{t.task_id} ⛔ 有鉴别力（缺陷 {pass_new} < 基线 "
               f"{pass_base}）", pass_new < pass_base,
               f"缺陷 {pass_new}/{n_new} 基线 {pass_base}/{n_base}")
            ck(f"D-{d.name}/{t.task_id} 与规范化题面的通过数一致",
               pass_new == pass_old, f"{pass_new} vs {pass_old}")
            ck(f"D-{d.name}/{t.task_id} 题面保留原格式（离基线更近）",
               len(src.splitlines()) >= len(src_old.splitlines()),
               f"新 {len(src.splitlines())} 行 vs 旧 {len(src_old.splitlines())} 行")

    #: ⛔ 空分母假绿（第 57 条）：一道题都没比过就报绿 = 假绿
    ck("D-末尾 至少跑完 1 个环境", n_env >= 1, f"实得 {n_env}")
    ck("D-末尾 ⛔ 至少对拍了 3 道题（空分母 = 假绿）", n_task_total >= 3,
       f"实得 {n_task_total}")

    print("\n== E 组：整份题面只允许一处改动 ==")
    for w in sites[:3]:
        out, _ = mutate_source(FIXTURE, which=w)
        ch = _changed_lines(FIXTURE, out)
        ck(f"E-w{w} 改动 ≤ 一处（+/- 合计 ≤ 2 行）", len(ch) <= 2, f"{ch}")

    print("\n== F 组：⛔ 向下兼容 —— 传 AST 对象也必须能用 ==")
    tree = ast.parse(FIXTURE)
    #: 旧实现能接受 AST（`ast.parse` → `compile` 接受 AST），新实现若只认 str
    #: 就会 AttributeError。3 处既有契约（`contract_aging_test.py:159` 等）
    #: 就是这么调的 ⇒ 这条是**真回归**钉死的，不是假想场景。
    try:
        out_ast, desc_ast = mutate_source(tree, which=sites[0])
        err_ast = None
    except Exception as exc:                                      # noqa: BLE001
        out_ast, desc_ast, err_ast = "", "", f"{type(exc).__name__}: {exc}"
    ck("F1 ⛔ 传 ast.Module 不抛异常", err_ast is None, f"{err_ast}")
    if err_ast is None:
        ck("F2 返回值是 str", isinstance(out_ast, str),
           f"{type(out_ast).__name__}")
        ck("F3 能重新解析", isinstance(ast.parse(out_ast), ast.Module))
        #: ⛔ 与传 str 的结果必须**语义一致**（AST 相等）
        out_str, desc_str = mutate_source(FIXTURE, which=sites[0])
        ck("F4 ⛔ 与传 str 的产出 AST 相等",
           ast.dump(ast.parse(out_ast)) == ast.dump(ast.parse(out_str)))
        ck("F5 desc 一致", desc_ast == desc_str, f"{desc_ast!r} vs {desc_str!r}")
    #: ⛔ 反证：这条必须真的有牙 —— 若哪天有人去掉归一化，F1 必须红
    ck("F6 旧实现（ast.parse）确实吃 AST ⇒ F1 不是凭空要求",
       isinstance(ast.parse(tree), ast.Module))

    print("=" * 74)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print("  ✗", f)
        return 1
    print("✅ 出题器也是最小编辑：题面保留真实格式，且判据鉴别力不变")
    return 0


if __name__ == "__main__":
    sys.exit(main())
