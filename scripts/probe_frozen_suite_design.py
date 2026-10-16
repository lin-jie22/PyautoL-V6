#!/usr/bin/env python3
"""冻结套件设计探针（只读，不改生产）。

上一支探针（`probe_gate_discrimination.py`）已经证明：**闸有牙**
（砍工具/砍 python_exec/口径漂移 都能转红）。所以 234 条恒绿的
根因不在闸，而在**题面**：

    冻结任务用的是 `env.source`（**原始、未被破坏**的源码）
    ⇒ 任何工具齐全的 harness 都拿满分 ⇒ 基线在天花板
    ⇒ delta 恒 0 ⇒ 闸的 4 条判据对真实变异全部不敏感。

本探针回答：**换成真被破坏的源码后，判别力能不能回来**。
测的是「基线上通过的条目数」与「候选之间的差异能否被看见」两组读数：

  A 段：现有冻结题（原源码）—— 基线 reward
  B 段：真破坏题（synthesize_tasks 确定性生成）—— 各候选 reward
  C 段：混合套件（原源码 + 破坏题）—— 各候选 reward 与闸门裁决

⛔ 只做测量，不写账本、不改生产口径。
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aether.agent import HarnessAgent                      # noqa: E402
from aether.cli import DEFAULT_ENVS, TASK_CLASS, _paths    # noqa: E402
from aether.environment import (                           # noqa: E402
    Environment, synthesize_tasks)
from aether.genome import Harness                          # noqa: E402
from aether.nongression import (                           # noqa: E402
    Baseline, FrozenTask, NonRegressionGate)

CALIBER = {"seed": 0, "timeout": 10.0, "split": "frozen"}


def seed_harness() -> Harness:
    return Harness(
        meta_rules="V6 元规则：奖励只来自执行判据；改 L0 需人工审批。",
        tools=("read_file", "write_file", "python_exec"),
        memory_policy={"recall_topk": 5, "min_content_len": 150})


def with_chain(skill: str) -> Harness:
    """种子 + 一条通用修法链（用于看「链被顶掉」是否会被闸看见）。"""
    h = seed_harness()
    return Harness(meta_rules=h.meta_rules, tools=h.tools,
                   routing={"repair": skill},
                   skills={skill: f"通用修法 {skill}"},
                   memory_policy=h.memory_policy)


def main() -> int:
    p = _paths(ROOT)
    envs = {n: Environment.from_dir(p["tasks_root"] / n) for n in DEFAULT_ENVS}
    runner = HarnessAgent(envs, memory=None, timeout=10.0).as_runner()
    out: dict = {}

    # ── A 段：现有冻结题（原源码）──────────────────────────────
    frozen_a = [FrozenTask(task_id=f"frozen::{n}",
                           spec={"env_id": n, "broken_source": e.source,
                                 "topic": n, "task_class": TASK_CLASS})
                for n, e in envs.items()]
    print("── A 段：现有冻结题（= 原始未破坏源码）──")
    res_a = {}
    for t in frozen_a:
        v = runner(seed_harness(), dict(t.spec))
        res_a[t.task_id] = v.reward
        print(f"  {t.task_id:22s} h0 reward={v.reward:.0f}")
    print(f"  ⇒ 基线满分 {sum(res_a.values()):.0f}/{len(res_a)}（天花板）")
    out["A_current"] = res_a

    # ── B 段：真破坏题（确定性合成）────────────────────────────
    #: ⛔ `max_n` 必须给够：每环境前几次变异可能全部"没让测试变红"
    #:    （rejected_no_failure），max_n=2 时只有 scheduler 出得来题
    #:    ⇒ 会误判成"三个环境合成不出任务"。实测 max_n=8 时四环境都有。
    print("\n── B 段：真破坏题（synthesize_tasks，max_n=8，每环境取 4 条）──")
    broken_tasks: list[FrozenTask] = []
    for n, e in envs.items():
        rep = synthesize_tasks(e, max_n=8, timeout=10.0, depth=1)
        print(f"  {n:11s} attempted={rep.attempted} valid={rep.valid} "
              f"rejected={rep.rejected_no_failure} ⇒ 取 {min(4, len(rep.tasks))}")
        for i, t in enumerate(rep.tasks[:4]):
            broken_tasks.append(FrozenTask(
                task_id=f"broken::{n}::{i}",
                spec={"env_id": n, "broken_source": t.broken_source,
                      "topic": n, "task_class": TASK_CLASS}))
    cands = {
        "h0": seed_harness(),
        "cf": with_chain("fix_control_flow"),
        "lit": with_chain("fix_literals"),
    }
    res_b: dict = {}
    for t in broken_tasks:
        row = {k: runner(h, dict(t.spec)).reward for k, h in cands.items()}
        res_b[t.task_id] = row
        print(f"  {t.task_id:26s} " +
              "  ".join(f"{k}={v:.0f}" for k, v in row.items()))
    out["B_broken"] = res_b

    # ── C 段：混合冻结套件下的闸门裁决 ─────────────────────────
    print("\n── C 段：混合套件（原源码 + 破坏题）的闸门行为 ──")
    mixed = frozen_a + broken_tasks
    base_h = with_chain("fix_control_flow")
    base_v = {t.task_id: runner(base_h, dict(t.spec)) for t in mixed}
    baseline = Baseline(caliber=CALIBER, verdicts=base_v, harness_id="seed+cf")
    gate = NonRegressionGate(mixed, baseline)
    n_pass = sum(1 for v in base_v.values() if v.reward > 0)
    print(f"  基线通过 {n_pass}/{len(base_v)}"
          f"（⇒ 判别力上限 = {n_pass} 条，天花板时为 4/4 但 delta 恒 0）")

    groups = [
        ("C0 恒等（基线自身）", base_h, CALIBER),
        ("C1 砍掉 python_exec",
         Harness(meta_rules=base_h.meta_rules, tools=("read_file", "write_file"),
                 routing=base_h.routing, skills=base_h.skills,
                 memory_policy=base_h.memory_policy), CALIBER),
        ("C2 链被顶掉（换 fix_literals）", with_chain("fix_literals"), CALIBER),
        ("C3 链被清空（无路由）", seed_harness(), CALIBER),
        ("C4 技能没了但路由还在",
         Harness(meta_rules=base_h.meta_rules, tools=base_h.tools,
                 routing=base_h.routing, skills={},
                 memory_policy=base_h.memory_policy), CALIBER),
    ]
    res_c = []
    for label, h, cal in groups:
        g = gate.evaluate(h, runner, cal)
        rec = {"group": label, "passed": g.passed,
               "n_regressions": len(g.regressions),
               "regressions": [r[:90] for r in g.regressions],
               "n_errors": len(g.errors), "deltas": g.deltas}
        res_c.append(rec)
        print(f"  {label:28s} {'🟢绿' if g.passed else '🔴红'}  "
              f"reg={len(g.regressions)} err={len(g.errors)}")
        for r in g.regressions[:3]:
            print(f"        · {r[:100]}")
    out["C_gate_mixed"] = {"n_pass_baseline": n_pass, "groups": res_c}

    (ROOT / "data" / "probe_frozen_suite_design.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n读数已写入 data/probe_frozen_suite_design.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
