#!/usr/bin/env python3
"""闸门判别力探针（常驻）。

回答一个具体问题：**非回归闸到底有没有牙**。

账本里 `gate.result` 连续 234 条 `passed=True` / `gate.blocked` 0 次，
这有两种截然不同的解释，必须靠实测分开：

  H1「闸是死的」    —— 闸在任何输入下都绿（判据空转 / 恒等）
  H2「闸没被用到」  —— 闸有牙，但生产上从没喂过真正变坏的候选

分开的办法是**反证组**：构造几个已知「明确变坏」的候选 harness 喂进去，
看闸是否转红。红了 ⇒ H2（闸有牙，问题是生产没走到/题面太软）；
不红 ⇒ H1（闸形同虚设，假绿灯）。

⛔ 元纪律：
  · 探针必须与生产同源：envs / h0 / frozen / caliber / runner 全部按
    `aether/cli.py:cmd_evolve` 的真实构造抄，**不另起一套**；
  · 每组必须打印 passed + regressions + errors，不许只看 bool；
  · 结论只从实测读数出，不从代码语义猜。

用法：
    python scripts/probe_gate_discrimination.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aether.agent import HarnessAgent                     # noqa: E402
from aether.cli import DEFAULT_ENVS, TASK_CLASS, _paths   # noqa: E402
from aether.environment import Environment                # noqa: E402
from aether.genome import Harness                         # noqa: E402
from aether.nongression import (                          # noqa: E402
    Baseline, FrozenTask, NonRegressionGate)


def seed_harness() -> Harness:
    """⛔ 与 `cli.py:533` 逐字一致 —— 基线必须用同一个种子跑。"""
    return Harness(
        meta_rules="V6 元规则：奖励只来自执行判据；改 L0 需人工审批。",
        tools=("read_file", "write_file", "python_exec"),
        memory_policy={"recall_topk": 5, "min_content_len": 150})


def main() -> int:
    p = _paths(ROOT)
    envs = {name: Environment.from_dir(p["tasks_root"] / name)
            for name in DEFAULT_ENVS}
    h0 = seed_harness()
    frozen = [FrozenTask(task_id=f"frozen::{name}",
                         spec={"env_id": name, "broken_source": e.source,
                               "topic": name, "task_class": TASK_CLASS})
              for name, e in envs.items()]
    caliber = {"seed": 0, "timeout": 10.0, "split": "frozen"}
    runner = HarnessAgent(envs, memory=None, timeout=10.0).as_runner()

    base_verdicts = {t.task_id: runner(h0, dict(t.spec)) for t in frozen}
    baseline = Baseline(caliber=caliber, verdicts=base_verdicts,
                        harness_id="seed")
    gate = NonRegressionGate(frozen, baseline)

    out: dict = {"baseline": {}, "groups": []}

    print("── 冻结基线（种子 harness h0）──")
    for tid, v in base_verdicts.items():
        out["baseline"][tid] = {"reward": v.reward,
                                "passed": v.passed,
                                "error": (v.error or "")[:80],
                                "n_checks": len(v.checks)}
        print(f"  {tid:24s} reward={v.reward:.0f} "
              f"checks={len(v.checks)} err={(v.error or '')[:60]}")
    n_pass = sum(1 for v in base_verdicts.values() if v.reward > 0)
    print(f"  ⇒ 基线通过 {n_pass}/{len(base_verdicts)}"
          f"（未通过项不参与回归判定 ⇒ 判别力上限 = {n_pass} 条）")

    # ── 候选组：C0 恒等 / C1 砍工具 / C2 砍技能路由 / C3 口径漂移 ──
    cases = [
        ("C0 恒等（基线自身）", h0, caliber),
        ("C1 砍掉全部工具", Harness(meta_rules=h0.meta_rules, tools=(),
                                    memory_policy=h0.memory_policy), caliber),
        ("C2 砍掉 python_exec", Harness(meta_rules=h0.meta_rules,
                                        tools=("read_file", "write_file"),
                                        memory_policy=h0.memory_policy),
         caliber),
        ("C3 口径漂移（换 seed）", h0,
         {"seed": 999, "timeout": 10.0, "split": "frozen"}),
        ("C4 口径多出字段", h0,
         {"seed": 0, "timeout": 10.0, "split": "frozen", "model": "x"}),
    ]

    print("\n── 反证组（喂已知变坏的候选，看闸转不转红）──")
    for label, h, cal in cases:
        g = gate.evaluate(h, runner, cal)
        rec = {"group": label, "passed": g.passed,
               "caliber_ok": g.caliber_ok,
               "caliber_reason": g.caliber_reason[:120],
               "n_regressions": len(g.regressions),
               "regressions": [r[:100] for r in g.regressions],
               "n_errors": len(g.errors),
               "errors": [e[:100] for e in g.errors],
               "deltas": g.deltas}
        out["groups"].append(rec)
        flag = "🟢绿" if g.passed else "🔴红"
        print(f"  {label:24s} {flag}  caliber_ok={g.caliber_ok} "
              f"reg={len(g.regressions)} err={len(g.errors)} "
              f"deltas={g.deltas}")
        for r in g.regressions[:4]:
            print(f"        · regression: {r[:110]}")
        for e in g.errors[:4]:
            print(f"        · error: {e[:110]}")

    n_red = sum(1 for r in out["groups"] if not r["passed"])
    print(f"\n⇒ 反证组 {n_red}/{len(out['groups'])} 转红")
    if n_red == 0:
        print("  ⛔ 闸在任何输入下都绿 ⇒ **假绿灯**（H1），准入闸形同虚设")
    elif n_red == len(out["groups"]):
        print("  ⛔ 连恒等候选也红 ⇒ 闸过严/基线不可达（另一类坏）")
    else:
        print("  ⇒ 闸有牙（H2）：判别力真实存在，问题是生产没喂到它")

    (ROOT / "data" / "probe_gate_discrimination.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n读数已写入 data/probe_gate_discrimination.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
