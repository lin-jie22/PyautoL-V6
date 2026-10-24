#!/usr/bin/env python3
"""⛔ **真实任务域实验**：把真实开源 bug 交给 V6 修，看它修不修得动。

═══ 这个脚本要回答什么 ═══

V6 到目前为止的 121 次"成功提交"全部建立在**它自己变异出来的题**上。
那种题的结构是：正确答案是原实现、缺陷由随机算子注入、每次修回去就行。

本实验换掉唯一的那一环 —— **题**：

    bug 来源    真实仓库 git 历史里的一个 bug fix commit
    broken      该 commit 父提交的源码（历史真实存在过的 broken 状态）
    参照修复    人类作者当时的真实修改（不告诉系统）
    判据        该仓库真实 pytest 里真实存在的断言

    dev_suite = [FrozenTask(spec={"broken_source": <真实 bug 源码>})]

⇒ 其余全部用 V6 的真实组件：真 runner（HarnessAgent）、真非回归闸
  （NonRegressionGate）、真提议器（TemplateProposer）、真 EvolutionLoop。
  **不改任何判据，不新增任何开关。**

═══ ⛔ 读数纪律 ═══

  · 一切写进 `--root` 指定的临时工作区，⛔ 不碰生产 `data/`；
  · 「修好了」= 提交后的源码在**全部 20 条真实断言**上通过，
    并且**逐条**给出通过的证据（不是"reward 涨了"）；
  · reward 提升**不算成功** —— 只修好 2 条有鉴别力的题而退回其它 18 条，
    reward 一样会涨。必须看**全部**判据；
  · 记账每条 Skyline 数据，拒绝一次性算总数。

跑法：
    python scripts/realbug_experiment.py \\
        --tasks-root /tmp/realbug_run/tasks \\
        --env real_boltons_mathutils_5eb6cc0 \\
        --rounds 10 --root /tmp/realbug_run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TASK_CLASS = "repair"


def to_dict(c) -> dict:
    """候选可能是 dataclass 也可能是 dict ⇒ 统一成精简 dict 再打印。"""
    d = c.to_dict() if hasattr(c, "to_dict") else dict(c or {})
    keep = ("capsule", "name", "reward", "gain", "advantage", "passed",
            "gate_ok", "verdict", "why", "size_delta", "error")
    out = {k: d.get(k) for k in keep if d.get(k) is not None}
    if len(out) > 6:
        out = dict(list(out.items())[:6])
    return out or {"raw": str(d)[:120]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-root", required=True)
    ap.add_argument("--env", required=True)
    ap.add_argument("--rounds", default="10")
    ap.add_argument("--group", default="3")
    ap.add_argument("--root", required=True, help="⛔ 临时工作区，不许指向生产")
    ap.add_argument("--seed", default="0")
    #: ⛔⛔ **对照组**（本实验可信度的根本）：同一套源码、同一套修法胶囊、
    #:    同一条 loop —— 唯一差别是"题从哪来"。
    #:      real      = 真实仓库 git 历史里的真实 bug
    #:      synthetic = V6 自己 `synthesize_tasks` 变异出来的题
    #:    没有这个对照，"0 条修好"无法区分「这套修法迁移不到真实 bug」与
    #:    「我这个脚本把装置搭错了」——两者表现一模一样。
    ap.add_argument("--mode", choices=("real", "synthetic"), default="real")
    args = ap.parse_args()

    work = Path(args.root).resolve()
    if work == ROOT:
        print("✗ --root 不许指向仓库根（会污染生产 data/）")
        return 1

    from aether.environment import Environment
    from aether.genome import Harness
    from aether.ledger import Ledger
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.providers import TemplateProposer
    from aether.store import Store
    from aether.agent import HarnessAgent
    from aether.evolution import EvolutionConfig, EvolutionLoop

    task_dir = Path(args.tasks_root) / args.env
    env = Environment.from_dir(task_dir)
    envs = {args.env: env}

    if args.mode == "real":
        buggy = (task_dir / "broken_source.py").read_text(encoding="utf-8")
        origin = "真实 git 历史 bug"
    else:
        #: ⛔ **对照组**：同一个环境的源码，用 V6 自己的变异器出题。
        #:    变异点是固定的 which=0 ⇒ 可复现。
        #: ⛔ 必须走 `synthesize_tasks`（**带 F2P 筛选**的标准出题），不能
        #:    直接挑一个变异点：实测 `which=0`（比较符反转）落在 20 条判据
        #:    **未被覆盖**的位置 ⇒ 变异后仍是 20/20 ⇒ 基线 1.0 ⇒ 根本不是题
        #:    （baseline=1.000 + no_weakness_evidence）。让 V6 自己筛出
        #:    "确实会让既有测试变红"的那条，对照才公平。
        from aether.environment import synthesize_tasks
        rep_s = synthesize_tasks(env, max_n=8, timeout=15.0)
        if not rep_s.tasks:
            print("✗ V6 的标准出题器在这个环境上 yield=0 ⇒ 对照组无从建立")
            return 1
        t0 = rep_s.tasks[0]
        buggy = t0.broken_source
        origin = f"V6 自造变异（synthesize_tasks F2P 筛出：{t0.task_id}）"

    print("=" * 70)
    print(f"环境：{args.env}   ⛔ 题源：{origin}")
    print(f"  判据 {len(env.tests)} 条（真实 pytest 断言，expect 由真实源码校准）")
    print(f"  bug 源码 {len(buggy.splitlines())} 行 / 基线 {len(env.source.splitlines())} 行")
    print(f"  工作区 {work}（生产 data/ 不受影响）")
    print("=" * 70)

    # ── 真实组件（与 `aether evolve` 同一套）─────────────────────
    work.mkdir(parents=True, exist_ok=True)
    led = Ledger(work / "data" / "ledger.jsonl")
    store = Store(work / "data" / "aether.db")
    runner = HarnessAgent(envs, timeout=15.0).as_runner()

    h0 = Harness(meta_rules="V6 元规则：奖励只来自执行判据；改 L0 需人工审批。",
                 tools=("read_file", "write_file", "python_exec"),
                 memory_policy={"recall_topk": 5, "min_content_len": 150})

    #: 冻结套件 = 基线源码（防"整份退回去"的粗回归）
    frozen = [FrozenTask(
        task_id=f"frozen::{args.env}",
        spec={"env_id": args.env, "broken_source": env.source,
              "topic": args.env, "task_class": TASK_CLASS})]
    caliber = {"seed": int(args.seed), "timeout": 15.0, "split": "frozen"}
    baseline = Baseline(
        caliber=caliber,
        verdicts={t.task_id: runner(h0, dict(t.spec)) for t in frozen},
        harness_id="seed")
    gate = NonRegressionGate(frozen, baseline)

    #: ⛔ 发展集 = **真实 bug**（本实验唯一的改动就在这里）
    dev = [FrozenTask(
        task_id=f"real::{args.env}",
        spec={"env_id": args.env, "broken_source": buggy,
              "topic": args.env, "task_class": TASK_CLASS})]
    spec = dict(dev[0].spec)

    #: ⛔⛔ **必须去掉 `patch:<env>` 胶囊**：它的语义是"整个文件换回基线"，
    #:    而本实验的 env.source 就是**修复后的源码** ⇒ 开了它等于把标准
    #:    答案直接塞给系统，任务变得平凡可解，读数毫无意义。
    #:    （V6 内部保留它是有正当理由的：它是 reward hacking 的可执行形态，
    #:     靠 held-out 闸去识别。但本实验不问"会不会 hack"，本实验问的是
    #:     "通用修法能不能修真实 bug"。）
    pool = {
        "fix_control_flow": "保守通用修法：只恢复控制流（比较/运算），不动字面量",
        "fix_literals": "通用字面量修法：只把数值常量对齐基线，不动结构/字符串",
        "fix_boolops": "通用布尔运算修法：只把 and/or 对齐基线",
        "fix_strings": "通用字符串修法：只把字符串常量对齐基线",
        "fix_unaryops": "通用取反修法：只把 not 的增删对齐基线",
    }
    #: ⛔ 必须**关键字**传 `skills=`：TemplateProposer 的第一个位置参数是
    #:    `name: str`。写成 `TemplateProposer(pool)` 会把整个技能字典当成
    #:    名字吃掉，`skills` 为空 ⇒ 提议器只能提 `noop` ⇒ 表现得像"系统
    #:    提不出修法"，其实是脚本自己写错（cli.py:1089 用的就是关键字）。
    proposer = TemplateProposer(skills=pool, route_key=TASK_CLASS)

    #: ⛔ `rounds` 在 `loop.run()` 上，不在 config（两者别混）
    loop = EvolutionLoop(led, gate, dev, runner, proposer, store=store,
                         caliber=caliber,
                         config=EvolutionConfig(group_size=int(args.group)))

    # ── 起跑前的基线读数（必须有，不然"涨了"无从谈起）────────────
    from aether.environment import verify_source
    v_bug = verify_source(env, buggy, timeout=15.0)
    print(f"\n起跑前：{origin} 源码通过 "
          f"{v_bug.evidence.get('n_passed')}/{v_bug.evidence.get('n_total')} 条")
    fails0 = sorted(k for k, w in (v_bug.evidence.get("why") or {}).items()
                    if w != "behavior")
    print(f"        失败的判据：{fails0}")
    if not fails0:
        print("        ⛔ 起点就有 0 条失败 ⇒ 这个题不构成任务，别往下跑")

    h_best, reps = loop.run(h0, rounds=int(args.rounds))

    # ── 结论：提交后的源码在**全部真实断言**上的表现 ────────────
    print("\n" + "=" * 70)
    print("== 每轮 ==")
    for r in reps:
        cv = getattr(r, "commit_verdict", None)
        cv = cv() if callable(cv) else cv
        print(f"  轮 {r.round_no}: baseline={r.baseline_reward:.3f} "
              f"committed={r.committed} verdict={cv} "
              f"revert={(r.revert_reason or '')[:70]}")
        #: ⛔ 每个候选的**实际分数**必须逐条打印：只看"有没有提交"会把
        #:    "没有候选能修"和"能修但被闸拦"混成同一个读数，而这两者的
        #:    下一步完全相反（加能力 vs 放行）。
        for c in (r.candidates or []):
            print(f"      · {to_dict(c)}")

    #: ⛔ 用 `loop.run` 返回的**最终 harness** 直接重跑一遍真实判据，
    #:    不看 reward 涨没涨 —— 涨了不代表修好了（可能只是部分分），
    #:    只有"20 条真实断言全部通过"才算。
    v_end = runner(h_best, dict(spec))
    n = v_end.evidence.get("n_passed")
    t = v_end.evidence.get("n_total")
    print(f"\n{'=' * 70}")
    print(f"最终 harness（{origin}）：")
    print(f"  真实断言通过 {n}/{t}  "
          f"{'✅ 全部通过 ⇒ 这个真 bug 被修好了' if n == t else '❌ 仍有失败'}")
    fails = sorted(k for k, w in (v_end.evidence.get("why") or {}).items()
                   if w != "behavior")
    if fails:
        print(f"  失败：{fails}")
    committed = [r for r in reps if r.committed]
    if committed:
        print(f"  提交次数：{len(committed)}（{', '.join(str(r.committed)[:8] for r in committed)}）")
    else:
        print("  提交次数：0")
    Path(args.root + "_result.json").write_text(json.dumps({
        "env": args.env, "mode": args.mode, "origin": origin,
        "rounds": int(args.rounds),
        "before_n_passed": v_bug.evidence.get("n_passed"),
        "after_n_passed": n, "total": t,
        "committed": len(committed),
        "remaining_failures": fails,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 70)
    return 0 if (n == t and t) else 1


if __name__ == "__main__":
    sys.exit(main())
