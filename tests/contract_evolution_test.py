#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：self-harness 进化循环 —— 机制正确性**脱离模型**可被验证。

V5 的纪律：先证明机制跑得通，再谈模型好不好。所以本文件全部用
确定性 stub（无网络、无模型），测的是循环本身：

  · 弱点必须由失败证据支撑（没证据 ⇒ 正确地什么都不做，且记账）
  · 非回归闸是**准入**（闸不过 ⇒ 根本不评估它有没有变好）
  · 组内相对优势（GRPO 式）能区分真修法与假修法
  · L0（元规则）触碰 ⇒ 停等人工，自动循环不得提交
  · 提交与回滚**两种结果都进账本**（V5：只记成功 ⇒ 分不清没机会/没能力）

用法：
    .venv/bin/python tests/contract_evolution_test.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

FAILS = []
E2E = "--e2e" in sys.argv


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main():
    from aether.evolution import (EvolutionConfig, EvolutionLoop, classify_error,
                                  mine_weaknesses)
    from aether.genome import (Capsule, Gene, Harness, apply_capsule,
                               apply_gene, blast_radius)
    from aether.ledger import Ledger
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.providers import TemplateProposer
    from aether.runtime import group_relative
    from aether.store import RunRow, Store
    from aether.verifier import Check, Verdict

    tmp = Path(tempfile.mkdtemp(prefix="v6_evo_"))
    CAL = {"seed": 0, "timeout": 10.0}

    def mk(passed, name="c"):
        return Verdict(passed=passed, checks=(Check(name, passed, "ev"),),
                       evidence={"n": 1})

    # ── A 组：基因/胶囊（最小编辑 + 爆炸半径）─────────────────────
    print("A 组：最小编辑 + 爆炸半径")
    h0 = Harness(meta_rules="元规则", tools=("read_file",))
    h1 = apply_gene(h0, Gene(op="set", target="L3.skills.fix", value="body"))
    ck("A1 加技能只改 L3", blast_radius(h0, h1)["layers_touched"] == ["skills"])
    ck("A2 原对象未被就地修改（纯函数变换 ⇒ 回滚只是指针回退）",
       "fix" not in h0.skills)
    hl0 = apply_gene(h0, Gene(op="append", target="L0.meta_rules", value="新规"))
    ck("A3 改 L0 ⇒ 触碰受保护层（需人工闸门）",
       blast_radius(h0, hl0)["touches_protected"])
    try:
        apply_gene(h0, Gene(op="set", target="L9.unknown.x", value=1))
        ck("A4 非法 target 抛异常（不静默忽略）", False, "没抛")
    except ValueError:
        ck("A4 非法 target 抛异常（不静默忽略）", True)

    # ── B 组：组内相对优势（GRPO 式选择）──────────────────────────
    print("B 组：组内相对优势")
    adv = group_relative([1.0, 0.0, 0.0])
    ck("B1 组内有差异 ⇒ 最优者优势为正", adv[0] > 0, f"{adv}")
    ck("B2 组内劣势者优势为负", adv[1] < 0, f"{adv}")
    ck("B3 全同 ⇒ 优势全 0（没有信息量，不该选任何变体）",
       group_relative([1.0, 1.0]) == [0.0, 0.0])
    ck("B4 单样本 ⇒ 0（不除零爆 NaN）", group_relative([1.0]) == [0.0])
    ck("B5 空 ⇒ 空", group_relative([]) == [])

    # ── C 组：弱点必须由证据支撑 ──────────────────────────────────
    print("C 组：弱点挖掘")
    ck("C1 无 run ⇒ 无弱点（不凭空提议）", mine_weaknesses([]) == [])
    runs = [RunRow(f"r{i}", "h", f"t{i}", 0.0, False, False,
                   {"error": "skill_not_found: fix", "topic": "text"})
            for i in range(3)]
    ws = mine_weaknesses(runs)
    ck("C2 有失败 ⇒ 挖到弱点", len(ws) == 1, f"{len(ws)}")
    ck("C3 弱点带证据（可追溯）", ws[0].evidence and ws[0].n_failed == 3)
    ck("C4 skill_not_found ⇒ missing_skill", ws[0].kind == "missing_skill")
    ck("C5 全绿 ⇒ 无弱点",
       mine_weaknesses([RunRow("r", "h", "t", 1.0, True, True, {})]) == [])
    ck("C6 未知错误 ⇒ 保守默认（缺技能），不是 noop",
       classify_error("一些没见过的错误") == "missing_skill")

    # ── D 组：循环机制（stub runner，可确定性断言）───────────────
    print("D 组：进化循环机制")
    led = Ledger(tmp / "l.jsonl")
    store = Store(tmp / "s.db")
    suite = [FrozenTask(task_id="f1", spec={"x": 1})]
    base = Baseline(caliber=CAL, verdicts={"f1": mk(True)}, harness_id="seed")
    gate = NonRegressionGate(suite, base)
    dev = [FrozenTask(task_id="d1", spec={"x": 10, "topic": "text"}),
           FrozenTask(task_id="d2", spec={"x": 20, "topic": "text"})]

    def runner(h, spec):
        # 有 restore 技能 ⇒ 通过；否则失败（确定性、可断言）
        return mk("restore_from_baseline" in h.skills)

    proposer = TemplateProposer(skills={
        "restore_from_baseline": "恢复到基线实现",
        "useless_skill": "没用的技能"})
    loop = EvolutionLoop(led, gate, dev, runner, proposer, store=store,
                         caliber=CAL, config=EvolutionConfig(group_size=2))
    h = Harness(meta_rules="元规则", memory_policy={"recall_topk": 5})
    rep1 = loop.run_round(h, round_no=1)
    ck("D1 第一轮挖到弱点并提交", rep1.improved, rep1.revert_reason)
    ck("D2 提交的是「真修法」候选（组内优势为正的那个）",
       any(c.accepted and c.capsule_name == "add_skill:restore_from_baseline"
           for c in rep1.candidates),
       f"{[(c.capsule_name, c.advantage, c.reason) for c in rep1.candidates]}")
    ck("D3 无用技能被组内优势淘汰（不是来者不拒）",
       any((not c.accepted) and c.capsule_name == "add_skill:useless_skill"
           for c in rep1.candidates))
    ck("D4 提交/拒绝都进账本（两种结果都可审计）",
       any(e.kind == "harness.commit" for e in led.events())
       and any(e.kind == "gate.result" for e in led.events()))
    ck("D5 账本链在进化后仍完整", led.verify_chain()[0])

    # 第二轮：已修好 ⇒ 无失败证据 ⇒ 正确地停止（而不是继续乱改）
    rep2 = loop.run_round(_committed(led), round_no=2)
    ck("D6 已修好 ⇒ 无弱点 ⇒ 不提交（不是硬凑变异）",
       (not rep2.improved) and "no_weakness" in rep2.revert_reason,
       rep2.revert_reason)

    # ── E 组：L0 阻断 + 非回归闸是准入 ────────────────────────────
    print("E 组：L0 阻断 + 闸为准入")
    cap_l0 = Capsule(name="touch_L0",
                     genes=(Gene(op="append", target="L0.meta_rules",
                                 value="新元规则"),))
    led2 = Ledger(tmp / "l2.jsonl")
    loop2 = EvolutionLoop(led2, gate, dev, runner,
                          _FixedProposer(cap_l0), store=None, caliber=CAL)
    rep3 = loop2.run_round(h, round_no=1)
    ck("E1 触碰 L0 ⇒ 被阻断且不提交",
       (not rep3.improved)
       and any("protected" in (c.reason or "") for c in rep3.candidates),
       f"{[c.reason for c in rep3.candidates]}")
    ck("E2 阻断被记账", any(e.kind == "gate.blocked" for e in led2.events()))

    # 闸不过的候选：**不评估**发展集（n_verdicts=0）
    def runner_regress(h, spec):
        # f1(x=1) 红 = 回归；d1(x=10) 红 = 有失败证据（否则挖不到弱点，
        # 循环会在 mine 阶段就退出，测不到闸门）；d2(x=20) 绿。
        # ⇒ 要验证的是：冻结套件退了，就算发展集有绿也不评估。
        return mk(spec.get("x") not in (1, 10))
    loop3 = EvolutionLoop(Ledger(tmp / "l3.jsonl"), gate, dev, runner_regress,
                          _FixedProposer(Capsule(
                              name="c", genes=(Gene(op="set",
                                                    target="L3.skills.k",
                                                    value="v"),))),
                          store=None, caliber=CAL)
    rep4 = loop3.run_round(h, round_no=1)
    ck("E3 候选让冻结任务变红 ⇒ 闸拦下",
       any((not c.gate_passed) and c.gate_regressions for c in rep4.candidates),
       f"{[c.to_dict() for c in rep4.candidates]}")
    ck("E4 闸未过 ⇒ 不进发展集评估（先证没变坏，再谈变好）",
       all(c.n_verdicts == 0 for c in rep4.candidates if not c.gate_passed))

    # ── F 组（可选）：端到端真跑（合成任务 + 真沙箱执行）──────────
    print("F 组：端到端" + ("" if E2E else "（跳过，加 --e2e 启用）"))
    if E2E:
        from aether.environment import Environment, synthesize_tasks
        from aether.cli import _default_runner
        env = Environment.from_dir(ROOT / "tasks" / "textscore")
        rep = synthesize_tasks(env, max_n=6)
        ck("F1 合成出有效任务（yield > 0）", rep.valid > 0, str(rep.to_dict()))
        # ⛔ 多环境化之后 runner 吃的是 {env_id: Environment} 字典，且 spec
        #    必须带 env_id（它是寻址键）。本组曾经用老的单环境调用方式 ⇒
        #    一直 AttributeError，而因为 e2e 默认跳过，**没人发现它坏了**
        #    （与恒真断言同族：判据假 vs 判据根本没跑）。
        rnr = _default_runner({env.env_id: env})
        dev2 = [FrozenTask(task_id=t.task_id,
                           spec={"env_id": env.env_id,
                                 "broken_source": t.broken_source,
                                 "topic": env.env_id,
                                 "task_class": "repair"}) for t in rep.tasks]
        v_bad = rnr(Harness(), dev2[0].spec)
        ck("F2 无技能 ⇒ 修不好（reward 0）", v_bad.reward == 0.0)
        # ⛔ 5 层都要到位才修得好：L2 没有工具 ⇒ 直接失败；L1 没有路由 ⇒
        #    技能不可达（"有技能"不等于"用得上"，这是 §7 那条纪律）。
        v_good = rnr(Harness(tools=("read_file", "write_file", "python_exec"),
                             routing={"repair": "fix_control_flow"},
                             skills={"fix_control_flow": "x"}),
                     dev2[0].spec)
        ck("F3b 修法名进证据（可观测：到底用了哪条路）",
           v_good.evidence.get("strategy") == "fix_control_flow",
           str(v_good.evidence))
        ck("F3 有技能 ⇒ 修好（reward 1，来自真执行）", v_good.reward == 1.0)
        ck("F4 判定带真实证据", "n_passed" in v_good.evidence)

    # ── G 组：全局预算闸（r30 · 默认执行，不进 E2E）──────────────
    print("G 组：全局预算闸")

    #: ⛔ 每轮**恒有弱点** ⇒ 不会被"无事可做"提前终止，于是"少跑了轮次"
    #:    只可能是预算干的 —— 否则 G2 的"只跑 1 轮"会被 idle 提前终止
    #:    冒充（两种停止原因混在一起 = 分诊方向全错）。
    def runner_always_fail(h, spec):
        return mk(False)

    def _run(budget, rounds=3):
        from aether.evolution import EvolutionLoop, EvolutionConfig
        l = Ledger(tmp / f"lb_{abs(hash(str(budget))) % 99999}.jsonl")
        lp = EvolutionLoop(l, gate, dev, runner_always_fail,
                           TemplateProposer(skills={"restore_from_baseline": "x"}),
                           store=None, caliber=CAL,
                           config=EvolutionConfig(group_size=2), budget=budget)
        return lp, lp.run(h, rounds=rounds)

    from aether.evolution import RunBudget  # noqa: E402

    #: G1 反证底座：**不装预算** ⇒ 跑满 3 轮（既有行为不变，旧基线可比）
    lp0, (_, reps0) = _run(None)
    ck("G1 ⛔ 不装预算 ⇒ 跑满 3 轮（默认 None=不限，旧行为不变）",
       len(reps0) == 3 and lp0.budget is None,
       f"{len(reps0)} 轮 budget={lp0.budget}")

    #: G2 轮次预算：真停
    lp1, (_, reps1) = _run(RunBudget(max_rounds=1))
    ck("G2 ⛔ max_rounds=1 ⇒ 只跑 1 轮（预算真生效）",
       len(reps1) == 1, f"{len(reps1)} 轮")

    #: G3 对照组：放宽到 10 ⇒ 跑满 3（证明 G2 的"1 轮"不是循环本就只能跑 1）
    lp2, (_, reps2) = _run(RunBudget(max_rounds=10))
    ck("G3 对照组：max_rounds=10 ⇒ 跑满 3 轮（不是循环本身跑不动）",
       len(reps2) == 3, f"{len(reps2)} 轮")

    #: G4 评估预算：dev 有 2 个任务 ⇒ 一轮 base 就用光 2 次
    lp3, (_, reps3) = _run(RunBudget(max_eval_runs=2))
    ck("G4 ⛔ max_eval_runs=2 ⇒ 第 1 轮 base 评估后就停",
       len(reps3) == 1 and lp3.budget.eval_runs >= 2,
       f"{len(reps3)} 轮 eval={lp3.budget.eval_runs}")

    #: G5 挂钟预算：max_seconds=0 ⇒ 轮末 seconds>=0 必然耗尽
    lp4, (_, reps4) = _run(RunBudget(max_seconds=0.0))
    ck("G5 ⛔ max_seconds=0 ⇒ 第 1 轮后停（挂钟维度真被检查）",
       len(reps4) == 1, f"{len(reps4)} 轮")

    #: G6 提议预算维度：第 1 轮提议 1 次后即耗尽 ⇒ 1 轮（轮末也查得到）
    lp5, (_, reps5) = _run(RunBudget(max_propose_calls=1))
    ck("G6 ⛔ max_propose_calls=1 ⇒ 第 1 轮提议后就停",
       len(reps5) == 1 and lp5.budget.propose_calls == 1,
       f"{len(reps5)} 轮 propose={lp5.budget.propose_calls}")

    #: G6b ⛔ **花钱之前**就停：base 评估排在 propose **之前**，所以预算
    #:    在轮内就被评估花光 ⇒ 走到"propose 前检查"分支 ⇒ 本轮**根本不提议**。
    #:    （真 LLM 时一次 propose = 一次付费调用，调用完再查钱已经花了。）
    lp6, (_, reps6) = _run(RunBudget(max_eval_runs=2))
    ck("G6b ⛔ 轮内耗尽 ⇒ 在 propose **之前**停（candidates 为空，"
       "不是提议完才停）",
       len(reps6) >= 1
       and "budget_exhausted" in (reps6[0].revert_reason or "")
       and not reps6[0].candidates,
       f"reason={reps6[0].revert_reason!r} cands={len(reps6[0].candidates)}")

    #: G7 停止必须**记账**，且与"无事可做"是两个原因
    l1 = lp1.ledger
    stops = [e for e in l1.events() if e.kind == "evolution.budget_stop"]
    ck("G7 ⛔ 预算耗尽进账本（不是静默少跑几轮 —— 那会被读成'跑完没提升'）",
       len(stops) == 1 and "budget_exhausted" not in
       (stops[0].payload.get("note", "")),
       f"{len(stops)} 条 {stops[0].payload if stops else ''}")
    ck("G8 账本里写明是**预算**停止，不是 idle 停止（两种原因修法不同）",
       bool(stops) and "预算耗尽" in (stops[0].payload.get("note", ""))
       and "rounds" in (stops[0].payload.get("reason", "")),
       str(stops[0].payload.get("reason")) if stops else "无事件")

    #: G9 每轮报告都带预算读数（只在末尾记一次 ⇒ 看不到预算怎么烧掉的）
    ck("G9 每轮报告带 budget 读数且 used 单调递增",
       all(r.budget is not None for r in reps2)
       and [r.budget["used"]["rounds"] for r in reps2] == [1, 2, 3],
       f"{[r.budget['used']['rounds'] if r.budget else None for r in reps2]}")

    #: G10 `limited` 读数：没装预算时必须是 False（否则是假仪表）
    ck("G10 ⛔ 未装预算 ⇒ limited=False（'有没有止损'必须可读，不能靠猜）",
       RunBudget().limited is False and RunBudget(max_rounds=1).limited is True)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


def _committed(led):
    from aether.genome import Harness
    for e in led.events():
        if e.kind == "harness.commit":
            return Harness.from_dict(e.payload["payload"])
    return Harness()


class _FixedProposer:
    """总是提议同一个 capsule（用于隔离变量地测闸门行为）。"""

    def __init__(self, cap):
        self.cap = cap

    def propose(self, weakness, harness, n=3):
        return [self.cap]


if __name__ == "__main__":
    sys.exit(main())
