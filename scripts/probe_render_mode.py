#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针：最小编辑渲染层 —— **语义必须与旧行为逐题一致**，格式必须更好。

═══ 为什么要有这份探针（2026-10-25）═══

`aether/repair.py` 的五个 `revert_*` 原本以 `ast.unparse(整棵树)` 收尾：
逻辑改对了，但产出与人类作者的真实修复**逐字不一致**（581 行 → 468 行，
license 头 32 行被删、注释与空行被删、引号与括号被重写）⇒ 补丁不可提交。
改成最小编辑之后，必须回答两个问题：

  ① 语义有没有被改坏？（渲染层写错 ⇒ 静默产出**语义不同**的补丁，
     这比格式噪声坏得多）
  ② 格式真的变好了吗？（否则这次改动是白做）

⛔ ① 的判据必须是**对拍**，不能是"我看了一下"：
   把 `render_patch` 换成旧行为（整份 unparse），逐环境 × 逐修法跑同一批
   变异样本，对拍 `ast.dump` 与 `verify_source(...).reward`。
   两者都必须逐题相等 —— 这是 ABBA（第 71 条），不是"改完再看一眼"。

═══ 用法 ═══

    python scripts/probe_render_mode.py [--repo /tmp/realrepo/boltons]

离线，秒级。
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aether.repair as R                                          # noqa: E402
from aether.environment import (Environment, synthesize_tasks,      # noqa: E402
                               verify_source)

REPAIRS = {
    "revert_control_flow": R.revert_control_flow,
    "revert_literals": R.revert_literals,
    "revert_boolops": R.revert_boolops,
    "revert_strings": R.revert_strings,
    "revert_unaryops": R.revert_unaryops,
}

_orig_render_patch = R.render_patch


def _legacy_render(broken_src, base_src, mutate):
    """旧行为：整份 `ast.unparse`（本探针的对拍基准 / oracle）。"""
    try:
        b = ast.parse(broken_src)
        g = ast.parse(base_src)
        m = mutate(b, g)
    except Exception:                                              # noqa: BLE001
        return broken_src, "unchanged"
    if m is None:
        return broken_src, "unchanged"
    return R._full_unparse(m, broken_src), "full"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-root", default=str(ROOT / "tasks"),
                    help="环境目录（每个子目录含 task.py + tests.json）")
    ap.add_argument("--envs", type=int, default=14)
    ap.add_argument("--tasks", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    root = Path(args.tasks_root)
    #: ⛔ 从**实际的目录结构**里发现环境，不手写清单（手写会漂，第 46 条）
    mods = sorted(p for p in root.iterdir()
                  if p.is_dir() and (p / "task.py").exists())[:args.envs]
    if not mods:
        print(f"✗ {root} 下没有含 task.py 的环境目录")
        return 1

    print("=" * 74)
    print("对每个模块合成变异题 ⇒ 逐修法对拍【新渲染 vs 旧渲染】")
    print("=" * 74)

    ast_bad: list = []
    reward_bad: list = []
    n_cmp = 0
    n_changed = 0         #: 新渲染真的动过（修法没拒绝）
    n_text_diff = 0       #: 新旧渲染的文本不同（⇒ 最小编辑真的生效了）
    fmt_gain = fmt_same = fmt_worse = 0

    for mp in mods:
        try:
            env = Environment.from_dir(str(mp), env_id=mp.name)
            rep = synthesize_tasks(env, max_n=args.tasks,
                                    timeout=args.timeout)
        except Exception as e:                                      # noqa: BLE001
            print(f"  ⚠️ {mp.name}: 合成失败 {type(e).__name__}: {e}")
            continue
        tasks = list(rep.tasks or [])
        if not tasks:
            continue
        for t in tasks:
            #: ⛔ 合成任务把源码放在**属性**上（`FrozenTask` 才在 spec 里），
            #:    两边都试，别只猜一种（第 135 条：判据拿原始值，别猜键名）
            src = getattr(t, "broken_source", None)
            if not src:
                spec = getattr(t, "spec", None)
                if isinstance(spec, dict):
                    src = spec.get("broken_source")
            if not src:
                continue
            for name, fn in REPAIRS.items():
                R.render_patch = _legacy_render
                try:
                    old = fn(src, env.source)
                except Exception as e:                              # noqa: BLE001
                    old = None
                    old_err = f"{type(e).__name__}: {e}"
                else:
                    old_err = ""
                R.render_patch = _orig_render_patch
                try:
                    new = fn(src, env.source)
                except Exception as e:                              # noqa: BLE001
                    ast_bad.append(f"{mp.name}/{name}: 新渲染抛异常 "
                                   f"{type(e).__name__}: {e}")
                    continue
                if old is None:
                    ast_bad.append(f"{mp.name}/{name}: 旧渲染抛异常 {old_err}")
                    continue
                n_cmp += 1

                #: ① 语义对拍：AST 必须逐题相同
                try:
                    if ast.dump(ast.parse(new)) != ast.dump(ast.parse(old)):
                        ast_bad.append(f"{mp.name}/{name}: AST 不一致")
                except SyntaxError as e:
                    ast_bad.append(f"{mp.name}/{name}: 新渲染不可解析 {e}")
                    continue

                #: ② 行为对拍：reward 必须逐题相同
                try:
                    r_new = verify_source(env, source=new,
                                          timeout=args.timeout).reward
                    r_old = verify_source(env, source=old,
                                           timeout=args.timeout).reward
                except Exception as e:                              # noqa: BLE001
                    reward_bad.append(f"{mp.name}/{name}: 评测抛异常 "
                                      f"{type(e).__name__}: {e}")
                    continue
                if abs(r_new - r_old) > 1e-9:
                    reward_bad.append(f"{mp.name}/{name}: reward "
                                      f"{r_old} → {r_new}")

                #: ③ 格式：⛔⛔ 基准是**人类基线** `env.source`，**不是** broken_src
                #:    （2026-10-25 实测踩到自己：合成题的 broken 源码本身就是
                #:     `mutate_source` 用 `ast.unparse` 造出来的 ⇒ 旧渲染与它
                #:     天然逐字相同 ⇒ "离缺陷版多近"这个尺子恒偏向旧行为，
                #:     一量就是"新渲染更差"。第 24 条：某维度恒站一边，先怀疑尺子。）
                if new != src:
                    n_changed += 1
                if new != old:
                    n_text_diff += 1
                d_new, d_old = _dist(new, env.source), _dist(old, env.source)
                if d_new < d_old:
                    fmt_gain += 1
                elif d_new == d_old:
                    fmt_same += 1
                else:
                    fmt_worse += 1

    print(f"\n对拍 {n_cmp} 组（环境 {len(mods)} × 题 × 修法 {len(REPAIRS)}）")
    #: ⛔⛔ 空分母假绿（第 57 条）：0 组对拍 ⇒ 上面那句 ✅ 是**假的**
    if n_cmp == 0:
        print("❌ 一组都没对拍上 ⇒ 本次结论无效（不许拿 0 当一致）")
        return 1
    print(f"  语义（AST）不一致：{len(ast_bad)}")
    for x in ast_bad[:6]:
        print("    ✗", x)
    print(f"  行为（reward）不一致：{len(reward_bad)}")
    for x in reward_bad[:6]:
        print("    ✗", x)
    print(f"  新渲染产出过改动：{n_changed}/{n_cmp}")
    print(f"  新旧文本不同（最小编辑真的生效）：{n_text_diff}/{n_cmp}")
    print(f"  与原文本的距离：更近 {fmt_gain} / 持平 {fmt_same} / 更远 {fmt_worse}")
    #: ⛔ 一次都没"更近" ⇒ 改动等于没做（合成题本身是 unparse 过的，
    #:    持平是正常的；但**更远**一例都不许有）
    n_fmt_bad = fmt_worse
    if fmt_worse:
        print(f"    ✗ 有 {fmt_worse} 组新渲染比旧渲染离原文本更远")

    ok = not ast_bad and not reward_bad and not n_fmt_bad
    print("\n" + "=" * 74)
    print("✅ 最小编辑与旧行为**逐题语义一致**，且格式更接近原文本"
          if ok else "❌ 语义或行为出现分叉 —— 不许提交")
    return 0 if ok else 1


def _dist(a: str, b: str) -> int:
    """粗距离：不同行数（行级 diff 的可读代理）。"""
    import difflib
    sm = difflib.SequenceMatcher(None, a.splitlines(), b.splitlines())
    return sum(max(i2 - i1, j2 - j1)
               for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


if __name__ == "__main__":
    sys.exit(main())
