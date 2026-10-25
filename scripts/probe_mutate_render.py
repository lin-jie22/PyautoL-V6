#!/usr/bin/env python3
"""对拍「出题器 mutate_source 走最小编辑」前后的行为（ABBA，第 71 条）。

153/154 条挖出的缺陷：`mutate_source` 以 `ast.unparse(整棵树)` 收尾 ⇒
合成题的源码是**规范化**的（无 license 头 / 注释 / 空行）⇒ 题面不像真实代码
⇒ 连带把「补丁噪声」这类缺陷挡在判据视野之外（第 24 / 133 条的形状）。

本探针回答三件事，任一件不成立就不许合：

  ① 语义零分叉：新旧出题器造出的题，AST 必须**完全相同**
  ② 鉴别力零分叉：每道题的判据在新旧题面上必须给出**同样的结果**
     （题面格式变了，判据是在题面上真跑出来的 ⇒ 必须实测，不能推断）
  ③ 格式确有改善：新题面必须**更接近**人类基线（否则这次改动没有收益）

⛔ 空分母假绿防线（第 57 条）：一组都没比成就报绿 = 假绿。
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys
from typing import List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import aether.environment as E                                  # noqa: E402
from aether.environment import (Environment, synthesize_tasks,   # noqa: E402
                                DEFAULT_MUTATION_KINDS)


# ── 旧行为（改动前的实现，逐字复原）────────────────────────────────────────
def legacy_mutate(source: str, which: int = 0,
                  kinds=E.DEFAULT_MUTATION_KINDS) -> Tuple[str, str]:
    tree = ast.parse(source)
    m = E._Mutator(which, kinds)
    new_tree = m.visit(tree)
    ast.fix_missing_locations(new_tree)
    if not m.done:
        raise ValueError(f"没有可变异点（which={which}）")
    return ast.unparse(new_tree), m.desc


def _dist(a: str, b: str) -> int:
    """两文本的行级编辑距离（越小越像）。"""
    sm = difflib.SequenceMatcher(None, a.splitlines(), b.splitlines())
    return sum(max(i2 - i1, j2 - j1)
               for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


def _outcome(env, src: str, timeout: float) -> Tuple[str, int, int]:
    """在源码 `src` 上跑判据，返回 (签名, 检查条数, 通过条数)。

    ⛔ 必须**真跑**：格式变了之后判据还能不能区分好坏，只能实测。

    ⛔⛔ 恒真断言防线（第 9 / 133 条）：第一版把参数顺序写成
        `verify_source(src, t)`（真实签名是 `verify_source(env, source=...)`）
        ⇒ 每条都抛 AttributeError ⇒ 新旧签名都是一串 `ERR`
        ⇒ "结果一致" **恒真**，16 组全绿却什么都没测到。
        ⇒ 签名里必须带上 checks 条数与通过条数，空了就直接判失败。
    """
    v = E.verify_source(env, source=src, timeout=timeout)
    sig = ";".join(f"{c.name}={c.passed}" for c in (v.checks or ()))
    n_pass = sum(1 for c in (v.checks or ()) if c.passed)
    return f"passed={v.passed} err={v.error} [{sig}]", len(v.checks or ()), n_pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-root", default="tasks")
    ap.add_argument("--which-max", type=int, default=12)
    ap.add_argument("--tasks", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    root = pathlib.Path(args.tasks_root)
    dirs = sorted(d for d in root.iterdir()
                  if d.is_dir() and (d / "task.py").exists())
    if not dirs:
        print(f"✗ {root} 下没有环境目录（每个目录需含 task.py）")
        return 1

    n_cmp = 0
    n_mut_ok = 0
    ast_bad: List[str] = []
    desc_bad: List[str] = []
    both_err_bad: List[str] = []      #: 一边报错一边成功 = 行为分叉
    fmt_gain = fmt_same = fmt_worse = 0
    n_task_cmp = 0
    verdict_bad: List[str] = []
    fmt_task_gain = fmt_task_same = fmt_task_worse = 0

    print(f"对拍 {len(dirs)} 个环境 × which 0..{args.which_max - 1}")

    for d in dirs:
        env = Environment.from_dir(str(d), env_id=d.name)
        base = env.source

        # ── ① / ③：逐变异点对拍 ────────────────────────────────────────
        for which in range(args.which_max):
            try:
                new_src, new_desc = E.mutate_source(base, which=which)
                new_err = None
            except Exception as exc:                            # noqa: BLE001
                new_src, new_desc, new_err = "", "", f"{type(exc).__name__}: {exc}"
            try:
                old_src, old_desc = legacy_mutate(base, which=which)
                old_err = None
            except Exception as exc:                            # noqa: BLE001
                old_src, old_desc, old_err = "", "", f"{type(exc).__name__}: {exc}"

            #: ⛔ 一边报错一边成功 ⇒ 行为分叉，比 AST 不等更严重
            if (new_err is None) != (old_err is None):
                both_err_bad.append(
                    f"{d.name} which={which}: new={new_err or 'OK'} "
                    f"old={old_err or 'OK'}")
                n_cmp += 1
                continue
            if new_err is not None:
                continue                      #: 两边都无变异点，跳过
            n_cmp += 1
            n_mut_ok += 1

            if ast.dump(ast.parse(new_src)) != ast.dump(ast.parse(old_src)):
                ast_bad.append(f"{d.name} which={which}")
            if new_desc != old_desc:
                desc_bad.append(f"{d.name} which={which}: {new_desc!r} vs {old_desc!r}")

            #: ③ 格式：新题面应更接近**人类基线**
            dn, do = _dist(new_src, base), _dist(old_src, base)
            if dn < do:
                fmt_gain += 1
            elif dn == do:
                fmt_same += 1
            else:
                fmt_worse += 1

        # ── ②：合成出来的整批任务，判据结果必须对拍一致 ──────────────────
        try:
            rep_new = synthesize_tasks(env, max_n=args.tasks, timeout=args.timeout)
            tasks_new = list(rep_new.tasks or [])
        except Exception:                                       # noqa: BLE001
            tasks_new = []
        if not tasks_new:
            continue
        #: ⛔ 基线（好版本）的判据结果 —— 题面必须在它上面**通过**，
        #:    在缺陷版上**失败**，否则这道题没有鉴别力，对拍也就没有意义。
        v_base, n_base, pass_base = _outcome(env, base, args.timeout)
        if n_base == 0:
            print(f"  ⚠️ {d.name}: 基线跑出 0 条检查 ⇒ 该环境判据为空，跳过")
            continue

        for t in tasks_new:
            src_new = t.broken_source
            #: 旧出题器的产出 = 新题面的「整份 unparse 版」（AST 相同）
            src_old = ast.unparse(ast.parse(src_new))
            n_task_cmp += 1
            v_new, n_new, pass_new = _outcome(env, src_new, args.timeout)
            v_old, _, pass_old = _outcome(env, src_old, args.timeout)

            if n_new == 0:
                verdict_bad.append(f"{d.name} {t.task_id}: 新题面跑出 0 条检查")
            if v_new != v_old:
                verdict_bad.append(f"{d.name} {t.task_id}: {v_new[:60]} vs {v_old[:60]}")
            #: 鉴别力：缺陷版通过数必须**少于**基线（否则题是坏的）
            if pass_new >= pass_base:
                verdict_bad.append(
                    f"{d.name} {t.task_id}: 无鉴别力（缺陷 {pass_new}/{n_new} "
                    f"≥ 基线 {pass_base}/{n_base}）")
            if pass_new != pass_old:
                verdict_bad.append(
                    f"{d.name} {t.task_id}: 通过数分叉 {pass_new} vs {pass_old}")

            dn = _dist(src_new, base)
            do = _dist(src_old, base)
            if dn < do:
                fmt_task_gain += 1
            elif dn == do:
                fmt_task_same += 1
            else:
                fmt_task_worse += 1

    print()
    print(f"  变异点对拍           {n_cmp} 组（成功 {n_mut_ok}）")
    print(f"  ⛔ 一边报错一边成功   {len(both_err_bad)}")
    print(f"  ⛔ AST 不一致        {len(ast_bad)}")
    print(f"  ⛔ desc 不一致       {len(desc_bad)}")
    print(f"  新题面离【人类基线】更近 {fmt_gain} / 持平 {fmt_same} / 更远 {fmt_worse}")
    print()
    print(f"  合成任务判据对拍     {n_task_cmp} 组")
    print(f"  ⛔ 判据结果不一致     {len(verdict_bad)}")
    print(f"  题面离基线更近       {fmt_task_gain} / 持平 {fmt_task_same} / "
          f"更远 {fmt_task_worse}")

    for label, arr in (("行为分叉", both_err_bad), ("AST", ast_bad),
                       ("desc", desc_bad), ("判据", verdict_bad)):
        for s in arr[:5]:
            print(f"    ✗ [{label}] {s}")

    #: ⛔ 空分母假绿：一组都没比成 ⇒ 不许报绿
    if n_cmp == 0 or n_task_cmp == 0:
        print("\n✗ 没有比成任何一组 ⇒ 假绿，判失败")
        return 1

    ok = not (both_err_bad or ast_bad or desc_bad or verdict_bad)
    if fmt_gain == 0 and fmt_task_gain == 0:
        print("\n✗ 新题面一处都没更接近基线 ⇒ 这次改动没有收益，不许合")
        ok = False

    print("\n" + ("✅ 语义/鉴别力零分叉，且题面确有改善" if ok else "❌ 不许合"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
