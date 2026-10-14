#!/usr/bin/env python3
"""取证：链（control_flow + literals）跑完，**还剩什么没修好**，以及**怎么抬高天花板**。

## 为什么要这份数据

上一轮把 L1.routing 从单值升级成有序链之后，端到端闭环在第 3 轮报
`no_weakness_evidence` —— 发展集两层都满分，系统说"没有弱点可挖"。
但同一批候选在 held-out 上**并没有全对**（stateflow hard 0.50 /
scheduler 0.75）。也就是说：

        发展集满分 ≠ 能力完备，只是**当前题面 + 当前能力池被穷尽**

在不知道"剩下的题缺什么"之前去扩 pool，是拿猜的答案当靶子。所以本脚本
分两段取证，都要求**判据同源**（`verify_source` 执行，不是字符串比对）：

  A 段 · 残差明细：链跑完仍失败的题，把**失败断言的名字与 evidence**
        打出来 —— 缺口在哪，由判据说，不由我看代码猜。

  B 段 · 天花板：合成器现在只做**单点**变异 ⇒ 任何题一个技能就能修好
        ⇒ 链"叠加"这件事在题面上**测不出来**，天花板由此压住。
        造**多点变异**题，比较 单技能 vs 链 —— 若只有链能修好，
        就证明天花板是合成器压的，抬它有意义。

⛔ 对照纪律：B 段必须报**对照组**（单技能各自的 reward），
   只有实验组 Δ 没有对照组的结论不算数。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import (Environment, mutate_source, synthesize_tasks,  # noqa: E402
                                verify_source)
from aether.repair import (classify_difficulty, revert_control_flow,  # noqa: E402
                           revert_literals)

ENVS = ("textscore", "eventflow", "stateflow", "scheduler")
MAX_N = int(os.environ.get("PROBE_MAX_N", "12"))
MULTI_CAP = int(os.environ.get("PROBE_MULTI_CAP", "6"))
TIMEOUT = 10.0
CHAIN = [revert_control_flow, revert_literals]


def chain_apply(src: str, base: str) -> str:
    """链是**叠加**：依次应用到同一份源码（与 agent.py 的语义同源）。"""
    out = src
    for fn in CHAIN:
        out = fn(out, base)
    return out


def probe_one(env: Environment, src: str) -> dict:
    """对一条题跑 单技能 ×2 + 链，全部 reward 来自执行判据。"""
    r_flow = verify_source(
        env, source=revert_control_flow(src, env.source), timeout=TIMEOUT).reward
    r_lit = verify_source(
        env, source=revert_literals(src, env.source), timeout=TIMEOUT).reward
    v_chain = verify_source(env, source=chain_apply(src, env.source),
                            timeout=TIMEOUT)
    failed = [c.name for c in v_chain.checks if not c.passed]
    evidence = [str(c.evidence)[:80] for c in v_chain.checks if not c.passed]
    return {"r_control_flow": r_flow, "r_literals": r_lit,
            "r_chain": v_chain.reward,
            "failed_checks": failed[:5], "evidence": evidence[:3]}


def main() -> int:
    rows = []
    for name in ENVS:
        env = Environment.from_dir(str(ROOT / "tasks" / name))
        before = verify_source(env, timeout=TIMEOUT)
        before_names = {c.name for c in before.checks if c.passed}

        #: ── A 段：现状（单点） ───────────────────────────────────
        single = []
        for i in range(MAX_N):
            try:
                src, desc = mutate_source(env.source, which=i)
            except Exception:                                  # noqa: BLE001
                continue
            try:
                after = verify_source(env, source=src, timeout=TIMEOUT)
            except Exception:                                  # noqa: BLE001
                continue
            #: ⛔ Fail-to-Pass 判据与 synthesize_tasks 同源
            if not (before_names - {c.name for c in after.checks if c.passed}):
                continue
            single.append((src, desc, i))

        a_rows, n_hard, n_fixed_by_chain = [], 0, 0
        for src, desc, i in single:
            diff = classify_difficulty(env, src, timeout=TIMEOUT)
            pr = probe_one(env, src)
            pr.update({"task_id": f"{name}::m{i}", "desc": desc,
                       "difficulty": diff})
            a_rows.append(pr)
            if diff == "hard":
                n_hard += 1
                if pr["r_chain"] >= 1.0:
                    n_fixed_by_chain += 1

        #: ── B 段：多点变异 ⇒ 题面需要"叠加" ──────────────────────
        multi = []
        for src0, desc0, i in single:
            if len(multi) >= MULTI_CAP:
                break
            for j in range(MAX_N):
                if j == i:
                    continue
                try:
                    src1, desc1 = mutate_source(src0, which=j)
                except Exception:                              # noqa: BLE001
                    continue
                try:
                    after = verify_source(env, source=src1, timeout=TIMEOUT)
                except Exception:                              # noqa: BLE001
                    continue
                if not (before_names - {c.name for c in after.checks
                                        if c.passed}):
                    continue
                pr = probe_one(env, src1)
                pr.update({"task_id": f"{name}::m{i}+m{j}",
                           "desc": f"{desc0} + {desc1}",
                           "difficulty": classify_difficulty(
                               env, src1, timeout=TIMEOUT)})
                multi.append(pr)
                break

        n_multi = len(multi)
        n_multi_chain_only = sum(
            1 for m in multi
            if m["r_chain"] >= 1.0
            and max(m["r_control_flow"], m["r_literals"]) < 1.0)

        rows.append({"env": name, "n_single": len(single),
                     "n_hard": n_hard, "n_hard_fixed_by_chain": n_fixed_by_chain,
                     "single": a_rows,
                     "n_multi": n_multi,
                     "n_multi_chain_only": n_multi_chain_only,
                     "multi": multi})

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "max_n": MAX_N, "multi_cap": MULTI_CAP,
        "chain": [f.__name__ for f in CHAIN],
        "rows": rows,
    }
    out = ROOT / "docs" / (
        "probe_growth_ceiling_"
        + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    print("链 = control_flow + literals\n")
    print("── A 段 · 现状（单点变异）")
    th = tf = 0
    for r in rows:
        th += r["n_hard"]; tf += r["n_hard_fixed_by_chain"]
        print(f"  {r['env']:10s} tasks={r['n_single']:3d} "
              f"hard={r['n_hard']:3d} 链修好={r['n_hard_fixed_by_chain']:3d}")
    print(f"  合计 hard={th} 链修好={tf} 残差={th - tf}")
    for r in rows:
        for m in r["single"]:
            if m["difficulty"] == "hard" and m["r_chain"] < 1.0:
                print(f"    残差 {m['task_id']} desc={m['desc']} "
                      f"r_chain={m['r_chain']}")
                print(f"      failed={m['failed_checks']} "
                      f"evidence={m['evidence']}")

    print("\n── B 段 · 多点变异（题面需要叠加）")
    for r in rows:
        print(f"  {r['env']:10s} 多点题={r['n_multi']:2d} "
              f"只有链能修好={r['n_multi_chain_only']:2d}")
        for m in r["multi"]:
            print(f"      {m['task_id']} [{m['difficulty']}] "
                  f"flow={m['r_control_flow']} lit={m['r_literals']} "
                  f"chain={m['r_chain']}")
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
