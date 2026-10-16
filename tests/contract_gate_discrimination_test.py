#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：非回归闸必须有**判别力**（不是"存在"，是"能咬到变异空间"）。

背景（2026-10-17 实测）：账本里 `gate.result` 连续 234 条 `passed=True`、
`gate.blocked` 0 次、`deltas` 全 0。先做了反证才敢下结论 —— 闸**不是**
没牙（砍工具 / 砍 python_exec / 口径漂移都能转红，见
`scripts/probe_gate_discrimination.py` 反证组 4/5 红）。真根因是：

    冻结基线停在**种子**，而裸种子（只有工具、没有修法）的通过集只含
    "本来就不需要修的题"（原始源码）⇒ 提案器能产生的变异（装技能 /
    改路由 / 改元规则）**全部**不改变这类题的判定 ⇒ 判据集与变异空间
    不相交 ⇒ 闸在结构上恒绿。

修法是**基线随 best 升级**（`evolution._rebaseline`）：best 装上修法后，
冻结套件里那批破坏题才进入"必须守住"的通过集（实测 4 → 7 → 12）。

本契约守三件事，每条都配**反证组**（把机制摘掉，断言必须转红）：

  A 组：冻结套件必须含真破坏题（v1 只有原始源码 ⇒ 恒绿的根）
  B 组：提交后基线必须升级，且通过集必须扩大
  C 组：升级后的基线下，"顶掉已具备的修法"必须被闸判为回归
  D 组：反证 —— 关掉 `_rebaseline` ⇒ B 组断言必须转红（证明它有牙）

用法：
    python tests/contract_gate_discrimination_test.py
"""
import sys
import tempfile
from pathlib import Path

SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _build_frozen():
    """⛔ 与 `cli.py:cmd_evolve` 同源的冻结套件构造（不另抄一套）。"""
    from aether.cli import (DEFAULT_ENVS, FROZEN_BROKEN_PER_ENV,
                            FROZEN_SYNTH_N, TASK_CLASS, _paths)
    from aether.environment import (DEFAULT_MUTATION_KINDS, Environment,
                                    synthesize_tasks)
    from aether.nongression import FrozenTask
    p = _paths(ROOT)
    envs = {n: Environment.from_dir(p["tasks_root"] / n) for n in DEFAULT_ENVS}
    frozen = [FrozenTask(task_id=f"frozen::{n}",
                         spec={"env_id": n, "broken_source": e.source,
                               "topic": n, "task_class": TASK_CLASS})
              for n, e in envs.items()]
    broken = []
    for n, e in envs.items():
        rep = synthesize_tasks(e, max_n=FROZEN_SYNTH_N, timeout=10.0,
                               depth=1, kinds=DEFAULT_MUTATION_KINDS)
        for i, t in enumerate(rep.tasks[:FROZEN_BROKEN_PER_ENV]):
            broken.append(FrozenTask(
                task_id=f"frozen::{n}::m{i}",
                spec={"env_id": n, "broken_source": t.broken_source,
                      "topic": n, "task_class": TASK_CLASS}))
    return envs, frozen, broken


def main():
    from aether.agent import HarnessAgent
    from aether.cli import TASK_CLASS
    from aether.genome import Harness
    from aether.nongression import Baseline, NonRegressionGate

    tmp = Path(tempfile.mkdtemp(prefix="v6_gatedisc_"))
    envs, src_tasks, broken_tasks = _build_frozen()
    frozen = src_tasks + broken_tasks
    runner = HarnessAgent(envs, memory=None, timeout=10.0).as_runner()

    def seed():
        return Harness(
            meta_rules="V6 元规则：奖励只来自执行判据；改 L0 需人工审批。",
            tools=("read_file", "write_file", "python_exec"),
            memory_policy={"recall_topk": 5, "min_content_len": 150})

    def with_skill(s):
        h = seed()
        return Harness(meta_rules=h.meta_rules, tools=h.tools,
                       routing={TASK_CLASS: s}, skills={s: f"修法 {s}"},
                       memory_policy=h.memory_policy)

    caliber = {"seed": 0, "timeout": 10.0, "split": "frozen"}

    # ── A 组：冻结套件必须含真破坏题 ─────────────────────────────
    print("A 组：冻结套件不只是原始源码")
    ck("A1 冻结套件含真破坏题", len(broken_tasks) > 0,
       f"broken={len(broken_tasks)}")
    h_seed = seed()
    v_src = {t.task_id: runner(h_seed, dict(t.spec)) for t in src_tasks}
    v_br = {t.task_id: runner(h_seed, dict(t.spec)) for t in broken_tasks}
    ck("A2 原始源码题裸种子全过（= 天花板，这是 v1 的根）",
       all(v.reward > 0 for v in v_src.values()),
       str({k: v.reward for k, v in v_src.items()}))
    ck("A3 破坏题裸种子**过不了**（否则它们对变异也不敏感）",
       not all(v.reward > 0 for v in v_br.values()),
       str({k: v.reward for k, v in v_br.items()}))

    # ── B 组：基线随 best 升级 ──────────────────────────────────
    print("B 组：基线必须随 best 升级（判别力的来源）")
    base0 = Baseline(caliber=caliber,
                     verdicts={t.task_id: runner(h_seed, dict(t.spec))
                               for t in frozen},
                     harness_id="seed")
    gate = NonRegressionGate(frozen, base0)
    n0 = sum(1 for v in base0.verdicts.values() if v.reward > 0)
    h_best = with_skill("fix_control_flow")
    #: 手动复刻 `_rebaseline` 的动作（不牵 Ledger，隔离变量）
    v1 = {t.task_id: runner(h_best, dict(t.spec)) for t in frozen}
    base1 = Baseline(caliber=caliber, verdicts=v1, harness_id=h_best.harness_id)
    n1 = sum(1 for v in base1.verdicts.values() if v.reward > 0)
    ck("B1 升级后通过集扩大（否则闸仍无从判别）", n1 > n0,
       f"before={n0} after={n1}")

    # ── C 组：升级后的基线下，顶掉修法必须被判回归 ───────────────
    print("C 组：顶掉已具备的修法 ⇒ 闸必须转红")
    gate.baseline = base1
    g_keep = gate.evaluate(h_best, runner, caliber)
    g_drop = gate.evaluate(seed(), runner, caliber)
    g_swap = gate.evaluate(with_skill("fix_literals"), runner, caliber)
    ck("C1 保持修法 ⇒ 绿", g_keep.passed, str(g_keep.regressions[:2]))
    ck("C2 清空修法链 ⇒ 红（这是 v1 下看不见的回归）", not g_drop.passed,
       f"regressions={len(g_drop.regressions)}")
    ck("C3 换掉修法（顶掉 cf）⇒ 红", not g_swap.passed,
       f"regressions={len(g_swap.regressions)}")

    # ── D 组：反证 —— 不升级基线 ⇒ C 组判别力归零 ────────────────
    print("D 组：反证（不升级基线 ⇒ 上述回归一条都看不见）")
    gate0 = NonRegressionGate(frozen, base0)
    d_drop = gate0.evaluate(seed(), runner, caliber)
    d_swap = gate0.evaluate(with_skill("fix_literals"), runner, caliber)
    ck("D1 反证：基线停在种子时，C2 的候选**是绿的** ⇒ 证明 B1 有牙",
       d_drop.passed, f"regressions={len(d_drop.regressions)}")
    ck("D2 反证：基线停在种子时，C3 的候选**是绿的**",
       d_swap.passed, f"regressions={len(d_swap.regressions)}")
    ck("D3 反证成立 ⇒ 升级前后判别力不同（不是恒等断言）",
       (not g_drop.passed) and d_drop.passed)

    # ── E 组：口径漂移仍然要被拦（原有能力不许被这次改动弄丢）────
    print("E 组：口径闸不受冻结套件变更影响")
    bad = {"seed": 999, "timeout": 10.0, "split": "frozen"}
    g_cal = gate.evaluate(h_best, runner, bad)
    ck("E1 口径漂移 ⇒ 红", not g_cal.passed and not g_cal.caliber_ok,
       g_cal.caliber_reason[:60])

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
