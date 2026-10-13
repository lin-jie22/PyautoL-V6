"""held-out **环境敏感性**取证：放行结论是不是只因为我恰好挑了这个环境？

## 为什么怀疑它

生产路径（`aether/cli.py` / `scripts/ab_generalization.py`）里 held-out 是
**一个环境**：`--heldout-env` 单个字符串，默认 `eventflow`。
`tasks/` 下有 4 个环境，默认路径只用到 2 个（dev=textscore / ho=eventflow），
`stateflow`、`scheduler` 在默认路径里**从未被装载**（死库存同族问题）。

但"环境少"本身不是缺陷：实测 4 个环境**各自都有难度跨度**
（easy+hard 都在，见 `probe_spread_audit.py` 的口径），所以单环境 held-out
也能判 `partial`。真正的疑点在另一处：

    同一个候选，换一套 held-out 环境，裁决会不会翻转？

如果会，那么"放行"就是**运气**——它只证明了"恰好在这一套判据上没露馅"，
不是"真的泛化"。而这件事**从来没有被测过**：所有 A/B 都固定 ho=eventflow。

## 判据（不是跑一下看看）

两组候选，行为是已知的、且方向相反：

    general  保守通用修法（只恢复控制流，不动字面量）—— 期望**放行**
    narrow   定点修法（整个文件换回 textscore 基线）—— 期望**拦截**

对**每一个** held-out 环境分别跑一次。判据：

    J1  general 在所有 held-out 环境上裁决一致（不出现有的放行有的拦）
    J2  narrow  在所有 held-out 环境上裁决一致，且**全部拦截**
    J3  两者在每个环境上都**方向相反**（放行/拦截），不能出现同向

⛔ J2 是硬的那一条：定点修法**必须**在每个环境上都被拦。任何一个环境放过它
   ⇒ 那次放行依赖环境选择 ⇒ 缺口成立，泛化闸要升级成"环境集合"。

⛔ 这不是契约测试，是**取证脚本**：它不改生产代码，只把现状量出来。
   结论为"一致"时，这份脚本就是"这个靶子不成立"的证据，别再花预算。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aether.environment import Environment, synthesize_tasks          # noqa: E402
from aether.evolution import EvolutionConfig, EvolutionLoop           # noqa: E402
from aether.generalization import GeneralizationGate, make_heldout    # noqa: E402
from aether.genome import Harness                                     # noqa: E402
from aether.ledger import Ledger                                      # noqa: E402
from aether.nongression import Baseline, FrozenTask, NonRegressionGate  # noqa: E402
from aether.providers import TemplateProposer                         # noqa: E402
from aether.repair import difficulty_report                           # noqa: E402
from aether.cli import _default_runner, TASK_CLASS                    # noqa: E402

DEV_ENV = "textscore"
HO_ENVS = ("eventflow", "stateflow", "scheduler")
POOLS = ("general", "narrow")
MAX_TASKS = 12


def build(root: Path, tmp: Path, ho_env: str, capsules: str):
    """装配一轮进化。与 ab_generalization 同口径，只把 ho 换成参数。"""
    envs = {n: Environment.from_dir(root / "tasks" / n)
            for n in (DEV_ENV, ho_env)}
    led = Ledger(tmp / "ledger.jsonl")
    runner = _default_runner(envs)

    h_seed = Harness(tools=("read_file", "write_file", "python_exec"),
                     memory_policy={"recall_topk": 5})
    frozen = [FrozenTask(task_id=f"frozen::{n}",
                         spec={"env_id": n, "broken_source": e.source,
                               "topic": n, "task_class": TASK_CLASS})
              for n, e in envs.items()]
    caliber = {"seed": 0, "timeout": 10.0, "split": "frozen"}
    baseline = Baseline(caliber=caliber,
                        verdicts={t.task_id: runner(h_seed, t.spec)
                                  for t in frozen},
                        harness_id="seed")
    gate = NonRegressionGate(frozen, baseline)

    rep_dev = synthesize_tasks(envs[DEV_ENV], max_n=MAX_TASKS)
    dev = [FrozenTask(task_id=t.task_id,
                      spec={"env_id": DEV_ENV,
                            "broken_source": t.broken_source,
                            "topic": DEV_ENV, "task_class": TASK_CLASS})
           for t in rep_dev.tasks]
    dev_diff = difficulty_report(envs[DEV_ENV], dev)

    rep_ho = synthesize_tasks(envs[ho_env], max_n=MAX_TASKS)
    ho_tasks = [FrozenTask(task_id=t.task_id,
                           spec={"env_id": ho_env,
                                 "broken_source": t.broken_source,
                                 "topic": ho_env, "task_class": TASK_CLASS})
                for t in rep_ho.tasks]
    ho_diff = difficulty_report(envs[ho_env], ho_tasks)
    heldout = GeneralizationGate(
        make_heldout(ho_env, ho_tasks, difficulty=ho_diff["labels"]),
        dev_task_ids=[t.task_id for t in dev],
        base_at_ceiling=1.0)

    pool = {
        "fix_control_flow": "保守通用修法：只恢复控制流，不动字面量",
        f"patch:{DEV_ENV}": f"定点修法：{DEV_ENV} 整个文件换回基线（换环境即失效）",
    }
    if capsules == "general":
        pool = {k: v for k, v in pool.items() if k == "fix_control_flow"}
    elif capsules == "narrow":
        pool = {k: v for k, v in pool.items() if k.startswith("patch:")}

    loop = EvolutionLoop(led, gate, dev, runner,
                         TemplateProposer(skills=pool, route_key=TASK_CLASS),
                         caliber=caliber, heldout=heldout,
                         dev_difficulty=dev_diff["labels"],
                         config=EvolutionConfig(group_size=3))
    h0 = Harness(meta_rules="V6 元规则：奖励只来自执行判据。",
                 tools=("read_file", "write_file", "python_exec"),
                 memory_policy={"recall_topk": 5})
    return loop, h0, rep_dev, rep_ho, ho_diff


def run_one(root: Path, tmp: Path, ho_env: str, capsules: str) -> Dict[str, Any]:
    loop, h0, rep_dev, rep_ho, ho_diff = build(root, tmp, ho_env, capsules)
    t0 = time.time()
    _final, reports = loop.run(h0, rounds=1)
    r = reports[0]
    g = r.generalization or {}
    return {
        "ho_env": ho_env,
        "pool": capsules,
        "dev_tasks": rep_dev.valid,
        "ho_tasks": rep_ho.valid,
        "ho_easy": ho_diff["n_easy"],
        "ho_hard": ho_diff["n_hard"],
        "verdict": g.get("verdict"),
        "dev_gain": g.get("dev_gain"),
        "ho_gain": g.get("heldout_gain"),
        "by_difficulty": g.get("by_difficulty") or {},
        "discriminative": g.get("discriminative"),
        "committed": r.committed is not None,
        "cost_s": round(time.time() - t0, 2),
    }


def main() -> int:
    root = ROOT
    rows: List[Dict[str, Any]] = []
    import tempfile
    with tempfile.TemporaryDirectory(prefix="v6ho_") as td:
        tmp = Path(td)
        for i, ho in enumerate(HO_ENVS):
            for j, pool in enumerate(POOLS):
                rows.append(run_one(root, tmp / f"{ho}_{pool}", ho, pool))

    print(f"{'held-out':<12}{'候选池':<9}{'ho题':>5}{'e/h':>7}"
          f"{'devΔ':>8}{'heldΔ':>8}{'裁决':<14}{'提交':<6}{'判别力':<7}")
    print("-" * 78)
    for r in rows:
        eh = f"{r['ho_easy']}/{r['ho_hard']}"
        disc = {True: "yes", False: "NO", None: "n/a"}[r["discriminative"]]
        print(f"{r['ho_env']:<12}{r['pool']:<9}{r['ho_tasks']:>5}{eh:>7}"
              f"{(r['dev_gain'] or 0):>8.2f}{(r['ho_gain'] or 0):>8.2f}"
              f"{str(r['verdict']):<14}{('是' if r['committed'] else '否'):<6}"
              f"{disc:<7}")

    by_pool: Dict[str, List[Dict[str, Any]]] = {p: [] for p in POOLS}
    for r in rows:
        by_pool[r["pool"]].append(r)

    print()
    checks: List[Tuple[str, bool]] = []
    for pool, expect_block in (("general", False), ("narrow", True)):
        rs = by_pool[pool]
        vs = {r["verdict"] for r in rs}
        blocks = {r["verdict"] != "generalize" for r in rs}
        label = "放行" if not expect_block else "拦截"
        checks.append((
            f"J1/J3 {pool}: 三个 held-out 环境裁决一致（{sorted(vs)}）",
            len(vs) == 1))
        checks.append((
            f"J2 {pool}: 三个环境**全部{label}**（实测 {sorted(blocks)}）",
            blocks == {expect_block}))
    # 交叉：同一环境下两个池必须方向相反
    for r_g in by_pool["general"]:
        r_n = next(x for x in by_pool["narrow"] if x["ho_env"] == r_g["ho_env"])
        checks.append((
            f"J4 {r_g['ho_env']}: general={r_g['verdict']} 与 "
            f"narrow={r_n['verdict']} 方向相反",
            (r_g["verdict"] == "generalize") != (r_n["verdict"] == "generalize")))

    for name, ok in checks:
        print(f"  {'✅' if ok else '❌'} {name}")

    all_pass = all(ok for _, ok in checks)
    print()
    if all_pass:
        print("结论：✅ 裁决**不依赖**选中哪个 held-out 环境 ⇒ "
              "「升级成环境集合」这个靶子不成立，别花预算。")
    else:
        print("结论：❌ 裁决**依赖** held-out 环境的选择 ⇒ "
              "放行是运气 ⇒ 泛化闸必须升级（多环境 + 不一致即拦）。")

    out = {"dev_env": DEV_ENV, "ho_envs": list(HO_ENVS),
           "max_tasks": MAX_TASKS, "rows": rows,
           "checks": [{"name": n, "ok": bool(o)} for n, o in checks],
           "all_pass": all_pass}
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = ROOT / "docs" / f"probe_env_sensitivity_{ts}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"\n→ {path}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
