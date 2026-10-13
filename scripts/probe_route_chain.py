"""取证：**路由链**能不能让两个通用能力维度叠加，从而解开闭环停滞？

## 疑点（来自实测，不是猜测）

2026-10-14 实测：加了通用字面量修法 `fix_literals` 之后，闭环**仍然**停滞，
只是原因从 `saturated:stagnated` 变成 `no_candidate_passed`。证据：

    fix_literals 的 dev_by_difficulty = {hard: +1.0, easy: −1.0}

它修好了 hard 层，**却把 easy 层打回 0**。根因是 `L1.routing[repair]` 是
**单值**：装一个新技能就把旧的顶掉 ⇒ 两个通用能力维度互斥 ⇒ 加再多通用
修法也叠不上。

## 候选修法

把路由值从「单技能名」升级为「**有序技能链**」：

    routing["repair"] = ["fix_control_flow", "fix_literals"]

链的语义是**能力叠加**：链上每项依次应用到同一个源码，末尾**只跑一次**
判据。新技能 `append` 到链尾，旧的仍在链上。

⛔ 刻意不做「依次尝试直到通过」：那要为每项各跑一次执行判据 ⇒ reward
   变成"试出来的"，违反「reward 只来自一次执行」，且成本随链长线性增长。

## 判据（四条，缺一条就不值得落地）

    J1 叠加性：单技能各自只能修一层，链能**同时**修两层
              （dev 上 easy 与 hard 都不掉）。
    J2 反证（最关键）：把 append 换回 set 覆盖 ⇒ easy 层必须掉回 0
              —— 若不掉 ⇒ 说明这个 bug 根本不存在，整个改动是白做。
    J3 顺序无关（正交性）：链的两项**换序**后分数一致
              —— 若不一致 ⇒ 叠加有隐式耦合，链的正确性依赖运气。
    J4 通用性：链在 **held-out 环境**上同样两层都不掉
              —— 只在 dev 成立 ⇒ 那是记忆/定点化，不是通用能力。

⛔ 本脚本只读 + 就地构造 harness，不改生产数据。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aether.agent import HarnessAgent                      # noqa: E402
from aether.environment import Environment, synthesize_tasks  # noqa: E402
from aether.genome import Harness, parse_route             # noqa: E402
from aether.repair import classify_difficulty              # noqa: E402

ENV_NAMES = ("textscore", "eventflow", "stateflow", "scheduler")
MAX_N = 12
TASK_CLASS = "repair"


def build(seed_env: str) -> Tuple[Dict[str, Environment], Dict[str, List]]:
    """按难度层收集任务：控制流题(easy) 与 常量题(hard)。"""
    envs: Dict[str, Environment] = {}
    by_layer: Dict[str, List] = {"easy": [], "hard": []}
    for n in ENV_NAMES:
        e = Environment.from_dir(ROOT / "tasks" / n)
        envs[n] = e
        for t in synthesize_tasks(e, max_n=MAX_N).tasks:
            lv = classify_difficulty(e, t.broken_source)
            by_layer.setdefault(lv, []).append((n, t))
    return envs, by_layer


def reward(agent: HarnessAgent, h: Harness, env_id: str, broken: str) -> float:
    v = agent.run(h, {"env_id": env_id, "broken_source": broken,
                      "topic": env_id, "task_class": TASK_CLASS})
    return float(v.verdict.reward)


def layer_scores(agent, h: Harness, items: List) -> Dict[str, float]:
    """按难度层算修复率（不是分数）。"""
    out: Dict[str, Dict[str, float]] = {}
    for env_id, t in items:
        lv = classify_difficulty(agent.envs[env_id], t.broken_source)
        out.setdefault(lv, {"n": 0, "ok": 0})
        out[lv]["n"] += 1
        if reward(agent, h, env_id, t.broken_source) >= 1.0:
            out[lv]["ok"] += 1
    return {k: (v["ok"] / v["n"] if v["n"] else 0.0) for k, v in out.items()}


def main() -> int:
    t0 = time.time()
    envs, _ = build(ENV_NAMES[0])
    agent = HarnessAgent(envs)

    # 收集 dev 环境（textscore）的题，按层分
    dev = ENV_NAMES[0]
    dev_tasks = synthesize_tasks(envs[dev], max_n=MAX_N).tasks
    dev_by: Dict[str, List] = {"easy": [], "hard": []}
    for t in dev_tasks:
        dev_by.setdefault(classify_difficulty(envs[dev], t.broken_source),
                          []).append(t)

    SKILLS = {"fix_control_flow": "保守通用修法：只恢复控制流",
              "fix_literals": "通用字面量修法：只对齐数值常量"}

    def H(routing: Any) -> Harness:
        return Harness(
            meta_rules="V6 元规则：奖励只来自执行判据。",
            tools=("read_file", "write_file", "python_exec"),
            skills=dict(SKILLS), routing={"repair": routing} if routing else {},
            memory_policy={})

    def score(h: Harness, tasks_by_layer) -> Dict[str, float]:
        return {lv: (sum(1 for t in ts
                         if reward(agent, h, dev, t.broken_source) >= 1.0)
                     / len(ts) if ts else 0.0)
                for lv, ts in tasks_by_layer.items()}

    rows: List[Dict[str, Any]] = []

    print("== 单技能基线（各修一层，另一层不修）==")
    s_cf = score(H("fix_control_flow"), dev_by)
    s_lit = score(H("fix_literals"), dev_by)
    print(f"  fix_control_flow : {s_cf}")
    print(f"  fix_literals     : {s_lit}")
    rows.append({"case": "single:fix_control_flow", "dev_by_layer": s_cf})
    rows.append({"case": "single:fix_literals", "dev_by_layer": s_lit})

    print("\n== 链：['fix_control_flow','fix_literals']（叠加）==")
    s_chain = score(H(["fix_control_flow", "fix_literals"]), dev_by)
    print(f"  chain            : {s_chain}")
    rows.append({"case": "chain:cf+lit", "dev_by_layer": s_chain})

    print("\n== 反证：换回 set 覆盖（旧单值行为）==")
    # 事故版：链只保留最后一项（等价于旧的"装新的顶掉旧的"）
    s_override = score(H("fix_literals"), dev_by)
    rows.append({"case": "override(set):lit", "dev_by_layer": s_override})

    print("\n== 顺序无关性：换序 ==")
    s_rev = score(H(["fix_literals", "fix_control_flow"]), dev_by)
    print(f"  reversed chain   : {s_rev}")
    rows.append({"case": "chain:lit+cf", "dev_by_layer": s_rev})

    print("\n== 通用性：链在 held-out 环境上（换环境不重配）==")
    ho_rows = []
    for ho in ENV_NAMES[1:]:
        ho_tasks = synthesize_tasks(envs[ho], max_n=MAX_N).tasks
        ho_by: Dict[str, List] = {"easy": [], "hard": []}
        for t in ho_tasks:
            ho_by.setdefault(classify_difficulty(envs[ho], t.broken_source),
                             []).append(t)
        s_ho = {lv: (sum(1 for t in ts
                         if reward(agent, H(["fix_control_flow",
                                             "fix_literals"]),
                                   ho, t.broken_source) >= 1.0) / len(ts)
                     if ts else 0.0)
                for lv, ts in ho_by.items()}
        n_easy, n_hard = len(ho_by.get("easy", [])), len(ho_by.get("hard", []))
        print(f"  {ho:11s} easy={s_ho.get('easy', 0):.2f}({n_easy}) "
              f"hard={s_ho.get('hard', 0):.2f}({n_hard})")
        ho_rows.append({"env": ho, "by_layer": s_ho,
                        "n": {"easy": n_easy, "hard": n_hard}})

    # ── 判据 ──────────────────────────────────────────────────
    def both(s):
        return s.get("easy", 0) > 0 and s.get("hard", 0) > 0

    J1 = both(s_chain) and not both(s_cf) and not both(s_lit)
    J2 = s_override.get("easy", 1) == 0.0 and s_chain.get("easy", 0) > 0
    J3 = s_chain == s_rev
    J4 = all(both(r["by_layer"]) for r in ho_rows if r["n"]["easy"]
             and r["n"]["hard"])

    print("\n== 判据 ==")
    print(f"  J1 叠加性（链两层都修，单技能各只修一层）   : {'PASS' if J1 else 'FAIL'}")
    print(f"  J2 反证（set 覆盖 ⇒ easy 掉回 0）            : {'PASS' if J2 else 'FAIL'}")
    print(f"  J3 顺序无关（换序分数一致）                  : {'PASS' if J3 else 'FAIL'}")
    print(f"  J4 通用性（held-out 各环境两层都不掉）       : {'PASS' if J4 else 'FAIL'}")

    out = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dev_env": dev,
        "max_n": MAX_N,
        "rows": rows,
        "heldout": ho_rows,
        "verdicts": {"J1_stackable": J1, "J2_override_regression": J2,
                     "J3_order_independent": J3, "J4_generalizes": J4},
        "cost_s": round(time.time() - t0, 1),
    }
    p = ROOT / "docs" / f"probe_route_chain_{time.strftime('%Y%m%d_%H%M%S')}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf8")
    print(f"\n📄 {p.name}")
    return 0 if all([J1, J2, J3, J4]) else 1


if __name__ == "__main__":
    sys.exit(main())
