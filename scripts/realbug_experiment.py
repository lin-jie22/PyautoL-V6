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
    #: ⛔⛔ **基线来源**（r80 取证）—— 本实验此前唯一没被质疑过的前提。
    #:    `fixed`（默认，现状）：`env.source` = **修复后**的源码。
    #:       五个通用修法的语义全是"把 broken 对齐基线"，而基线就是人类
    #:       修复 ⇒ 它们有权**读到答案**，只要定位对就能改对。
    #:    `buggy`：`env.source` = **缺陷版本身** ⇒ 基线里没有答案可对齐
    #:       ⇒ 用于回答："第 1 轮修好真实 bug"里，有多少是**照答案抄的**。
    #:    ⛔ 三种结果都要如实记，不许只报对自己有利的那个：
    #:       · buggy 基线下依然修好 ⇒ 真有修复能力；
    #:       · buggy 基线下修不好 ⇒ 现状的成就是"定位 + 抄"，不是"修复"。
    ap.add_argument("--baseline", choices=("fixed", "buggy"), default="fixed")
    #: ⛔ **提议器**：`llm` 是"不看答案"实验里唯一可能的退路 —— 若连它也
    #:    修不好，才可以说"V6 没有不依赖基线的修复能力"，而不是
    #:    "模板提议器不行"。⛔ 端点不可达**必须**显式打印并让结论作废，
    #:    不许静默回退成模板（r62 教训：留 `fallback=""` 会造出假绿）。
    ap.add_argument("--proposer", choices=("template", "llm", "synth"),
                    default="template")
    #: ⛔ 必须带 tag：本机 `ollama list` 里是 `qwen2.5:7b`，只写 `qwen2.5`
    #:    ⇒ ollama 返回 **404 `model not found`**（不是端点挂了，是名字没对上）
    #:    ⇒ 表现成"模型没输出"，实际一次都没调用（第 25 条：一个异常两种含义）。
    ap.add_argument("--model", default="qwen2.5:7b")
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

    if args.baseline == "buggy":
        #: ⛔ 换基线**必须在 buggy 定下来之后**：合成模式的 buggy 由出题器
        #:    现造，早于此处替换会拿 fixed 当基线 ⇒ 实验组与对照组变成一回事。
        env = Environment(env_id=env.env_id, source=buggy, tests=env.tests,
                          note=(env.note or "") + " ｜基线=缺陷版（不许读答案）")
        envs = {args.env: env}

    print("=" * 70)
    print(f"环境：{args.env}   ⛔ 题源：{origin}")
    print(f"  ⛔ 基线来源：{args.baseline}"
          + ("（**修复后源码** ⇒ 修法能读到答案）" if args.baseline == "fixed"
             else "（**缺陷版自身** ⇒ 答案不在基线里）"))
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
    proposer_desc = "template"
    if args.proposer == "synth":
        #: ⛔ 只提 `synth_operators`，且**同时**把基线换成缺陷版才有意义：
        #:    否则它跟 `revert_*` 一样能"修好"，无法区分"搜出来的"与"抄来的"。
        pool = {"synth_operators": "判据驱动生成：不看基线，只用判据反馈搜补丁"}
        proposer = TemplateProposer(skills=pool, route_key=TASK_CLASS)
        proposer_desc = "synth（不看基线）"
        print("  提议器：synth_operators —— ⛔ 参数里没有 env.source，"
              "只看判据反馈")
    if args.proposer == "llm":
        #: ⛔ 与 `aether/cli.py:1135` 同构（route_keys / skill_vocab / env_ids
        #:    全部**由调用方注入**，否则模型会猜接口）
        from aether.providers import LLMProposer, OllamaProvider
        try:
            _probe = OllamaProvider(model=args.model, think=False)
            proposer = LLMProposer(_probe, route_keys=(TASK_CLASS,),
                                   skill_vocab=tuple(sorted(pool)),
                                   env_ids=(args.env,))
            proposer_desc = f"llm({args.model})"
        except Exception as e:  # noqa: BLE001
            print(f"⛔ 真模型不可用（{type(e).__name__}: {e}）⇒ **本次实验作废**"
                  f"（不许静默回退成模板，否则读数会被当成 llm 的成绩）")
            return 1
        #: ⛔ 跑完之后必须回看 `last_error`：构造成功 ≠ 真的调通了。
        print(f"  提议器：真模型 {args.model}（跑完会回看 last_error / 重试数）")

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

    if args.proposer == "llm":
        #: ⛔ 回看记账是**必须的**：r62 实测过"端点不可达但构造成功" ⇒
        #:    last_error 空着、候选全空 ⇒ 看起来像"模型认为无需修改"。
        le = getattr(proposer, "last_error", "")
        print(f"\n⛔ 模型记账：last_error={le or '(空)'}  重试="
              f"{getattr(proposer, 'n_retries_used', '?')}  原始输出长度="
              f"{getattr(proposer, 'last_raw_len', '?')}")
        if le:
            print("  ⇒ 模型没真跑起来 ⇒ 这一组的 llm 读数**不成立**")

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
