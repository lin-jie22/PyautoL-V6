#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：因果链可达性 —— **"有机制"不等于"这条路被走到"**。

2026-10-13 实测普查（端到端 evolve 三轮，89 次执行）：

    L4 记忆通路被触发            0 次（memory 恒为 None）
    assign_credit 被调用         0 次（生产链路没有 replay_fn）
    CLI 端到端可用环境           2 个（已造 4 个）
    三轮是否提交过任何进化        0 次（improved=False）

三组数字指向同一件事：模块写好了，但**没有一条端到端路径真的走到它**。
这个文件的职责就是钉死修复后的因果链，让"再退化成装饰"立刻变红。

守两件事：
  ① **反事实层归因**（`aether/attribution.py`）：变体的提升来自哪一层，
     由逐层消融的**真实执行**回答，不靠启发式打分。
  ② **跨轮经验记忆**（`providers.MemoryAwareProposer`）：被闸拒的变异
     记进记忆 ⇒ 下一轮不再重复提，记忆因此进入因果链。

跑法：
    python tests/contract_causality_test.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main():
    import random

    from aether.attribution import (LAYERS, LayerCredit, attribute_layers,
                                    mean_reward)
    from aether.environment import Environment, mutate_source
    from aether.genome import Harness
    from aether.memory import MemoryOS
    from aether.providers import MemoryAwareProposer

    env = Environment.from_dir(ROOT / "tasks" / "textscore")
    broken, _ = mutate_source(env.source, which=2)
    SPEC = {"env_id": env.env_id, "broken_source": broken,
            "topic": env.env_id, "task_class": "repair"}
    SPECS = [SPEC]

    def H(**kw):
        base = dict(meta_rules="V6 元规则：奖励只来自执行判据。",
                    routing={}, tools=("read_file", "write_file", "python_exec"),
                    skills={}, memory_policy={})
        base.update(kw)
        return Harness(**base)

    from aether.agent import HarnessAgent
    agent = HarnessAgent({env.env_id: env})
    runner = agent.as_runner()

    h_skill = H(skills={"fix_control_flow": "修法"},
                routing={"repair": "fix_control_flow"})

    # ── A 组：归因的结构 ──────────────────────────────────────────
    print("A 组：反事实层归因的结构")
    lc = attribute_layers(runner, h_skill, SPECS)
    ck("A1 归因覆盖 5 层且可用", set(lc.per_layer) == set(LAYERS)
       and lc.available, f"{lc.per_layer}")
    ck("A2 基线 reward 被记录（归因必须有参照点）",
       lc.baseline_reward == 1.0, f"{lc.baseline_reward}")
    ck("A3 可重放性检查通过（同一 harness 两次 reward 一致）",
       lc.replay_consistent and lc.available)

    # ── B 组：判别力（哪一層真的带来了提升）──────────────────────
    print("B 组：归因能指出真正有用的层")
    ck("B1 去掉 L3（技能清空）⇒ 贡献为 1（能力确实来自技能）",
       abs(lc.per_layer.get("L3", 0) - 1.0) < 1e-9, f"{lc.per_layer.get('L3')}")
    ck("B2 去掉 L1（路由清空）⇒ 贡献为 1（技能不可达 = 没有技能）",
       abs(lc.per_layer.get("L1", 0) - 1.0) < 1e-9, f"{lc.per_layer.get('L1')}")
    ck("B3 去掉 L2（缺 python_exec）⇒ 贡献为 1（连判据都跑不了）",
       abs(lc.per_layer.get("L2", 0) - 1.0) < 1e-9, f"{lc.per_layer.get('L2')}")
    ck("B4 L0 / L4 与本次提升无关 ⇒ 贡献 0（归因不是一律给高分）",
       abs(lc.per_layer.get("L0", -1)) < 1e-9
       and abs(lc.per_layer.get("L4", -1)) < 1e-9,
       f"L0={lc.per_layer.get('L0')} L4={lc.per_layer.get('L4')}")
    ck("B5 top_layers 按贡献排序（可直接读）",
       lc.top_layers[:2] == ("L1", "L2") or set(lc.top_layers[:2]) <= {"L1", "L2", "L3"},
       f"{lc.top_layers}")

    # ── C 组：记忆通路也算一层（归因此前恒为 0 的那条路）────────────
    print("C 组：L4 记忆通路的贡献可以被归因出来")
    mem = MemoryOS(min_content_len=0)
    mem.write("L2", f"repair_hint:{env.env_id} 环境指纹 {env.fingerprint}："
                    f"失败模式是控制流，用保守修法 revert_control_flow。",
              channel="curated", source="test", tags=("experience", "exec"))
    agent_mem = HarnessAgent({env.env_id: env}, memory=mem)
    h_mem = H(memory_policy={"recall_topk": 5})
    lc_mem = attribute_layers(agent_mem.as_runner(), h_mem, SPECS)
    ck("C1 无技能时 reward 全靠记忆通路 ⇒ L4 贡献 > 0"
       "（端到端里 L4 触发 0 次的机制现在可被归因）",
       lc_mem.per_layer.get("L4", 0) > 0, f"{lc_mem.per_layer}")
    ck("C2 归因到 L4 的同时，L3 贡献为 0（harness 本来就没技能）",
       abs(lc_mem.per_layer.get("L3", -1)) < 1e-9,
       f"{lc_mem.per_layer.get('L3')}")

    # ── D 组：反证（证明 B/C 不是恒值）───────────────────────────
    print("D 组：反证 —— 改掉判据，归因必须翻转")
    # D1：runner 恒 1 ⇒ 所有层贡献 0，且必须**标注无从判别**
    flat = attribute_layers(lambda h, s: _ok_runner(h, s), h_skill, SPECS)
    ck("D1 事故版：runner 恒 1 ⇒ 所有层贡献 0 且标注『无从判别』",
       all(abs(v) < 1e-9 for v in flat.per_layer.values())
       and "没有判别力" in flat.reason, f"{flat.per_layer} / {flat.reason[:40]}")

    # D2：把 L3 的消融函数改成 no-op ⇒ L3 贡献必须变 0
    noop = {"L3": lambda h: h}
    lc_noop = attribute_layers(runner, h_skill, SPECS, ablations=noop)
    ck("D2 事故版：L3 消融改 no-op ⇒ L3 贡献翻转成 0（B1 不是恒值）",
       abs(lc_noop.per_layer.get("L3", -1)) < 1e-9,
       f"{lc_noop.per_layer.get('L3')}")

    # D3：环境不可重放 ⇒ **不给数字**（V5：编出来的归因比不归因更有害）
    # ⛔ 用**计数器交替**而不是 random（random 有 50% 概率两次撞成一样，
    #    那样测不出"不可重放"，是假的反证）
    state = {"n": 0}

    def flaky(h, s):
        state["n"] += 1
        return _ok_runner(h, s) if state["n"] % 2 == 0 else _bad_runner(h, s)

    lc_flaky = attribute_layers(flaky, h_skill, SPECS)
    ck("D3 事故版：环境不可重放 ⇒ available=False 且 per_layer 为空",
       not lc_flaky.available and not lc_flaky.per_layer
       and not lc_flaky.replay_consistent, f"{lc_flaky.per_layer}")

    # D4：没有任务 ⇒ 不可用（不是返回全 0 假装归了因）
    ck("D4 specs 为空 ⇒ available=False（不拿空集冒充归因）",
       not attribute_layers(runner, h_skill, []).available)

    # ── E 组：接进进化循环（且不参与选择）──────────────────────────
    print("E 组：归因接进进化循环 —— 提交**之后**才算，不参与选择")
    from aether.evolution import EvolutionConfig, EvolutionLoop
    from aether.generalization import GeneralizationGate, make_heldout
    from aether.genome import Capsule, Gene
    from aether.ledger import Ledger
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.verifier import Check, Verdict

    def _ok(tid="t"):
        return Verdict(passed=True, checks=(Check(tid, True, "got=1 expect=1"),),
                       evidence={"env": tid})

    def _bad(tid="t"):
        return Verdict(passed=False, checks=(Check(tid, False, "got=0 expect=1"),),
                       evidence={"env": tid})

    def mem_runner(h, spec):
        """纯内存确定性 runner：有技能且可达 ⇒ 1，否则 0。"""
        if "python_exec" not in h.tools:
            return _bad("L2")
        r = h.routing.get("repair") or ""
        return _ok("skill") if r and r in h.skills else _bad("none")

    class FixedProposer:
        def __init__(self, caps):
            self.caps = caps

        def propose(self, weakness, harness, n=3):
            return list(self.caps)

    cap = Capsule(name="gen", genes=(
        Gene(op="set", target="L3.skills.gen", value="修法", rationale="t"),
        Gene(op="set", target="L1.routing.repair", value="gen", rationale="t")))

    with tempfile.TemporaryDirectory(prefix="v6attr_") as td:
        led = Ledger(Path(td) / "ledger.jsonl")
        dev = [FrozenTask(task_id=f"d{i}", spec={"env": "A"}) for i in range(3)]
        frozen = [FrozenTask(task_id="fz", spec={"env": "A", "frozen": True})]
        cal = {"seed": 0, "timeout": 10.0, "split": "frozen"}
        base = Baseline(caliber=cal,
                        verdicts={"fz": mem_runner(H(), {"frozen": True})},
                        harness_id="seed")
        ho = GeneralizationGate(
            make_heldout("B", [FrozenTask(task_id="h0", spec={"env": "B"})]),
            dev_task_ids=[t.task_id for t in dev])
        # ⛔ 起始 harness **必须带工具**：否则候选在 mem_runner 里连 L2 闸
        #    都过不去 ⇒ reward 恒 0 ⇒ 永远不提交 ⇒ 归因永远没机会跑
        #    （这正是"机制写好了但这条路没被走到"的测试版）
        h0 = H()
        loop = EvolutionLoop(led, NonRegressionGate(frozen, base), dev,
                             mem_runner, FixedProposer([cap]), caliber=cal,
                             heldout=ho,
                             config=EvolutionConfig(group_size=2,
                                                    attribute_layers=True,
                                                    attribution_tasks=3))
        rep = loop.run_round(h0, round_no=1)
        ck("E1 提交后 round report 带 layer_credit（归因真的被走到）",
           rep.layer_credit is not None and rep.layer_credit.get("available"),
           f"{rep.layer_credit}")
        ck("E2 归因指出 L1/L3 是贡献层（与 B 组一致）",
           rep.layer_credit is not None
           and rep.layer_credit.get("per_layer", {}).get("L3", 0) > 0
           and rep.layer_credit.get("per_layer", {}).get("L1", 0) > 0,
           f"{rep.layer_credit}")
        # ⛔ 归因必须**晚于**提交：它一旦参与选择就变成第二张发展集
        seqs = {}
        with open(Path(td) / "ledger.jsonl", encoding="utf-8") as f:
            import json
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                seqs.setdefault(e.get("kind"), []).append(e.get("seq", 0))
        ck("E3 账本里 attribution.layer 晚于 harness.commit"
           "（归因不参与选择）",
           bool(seqs.get("attribution.layer"))
           and max(seqs["attribution.layer"])
           > max(seqs.get("harness.commit", [0])),
           f"{seqs.get('attribution.layer')} vs {seqs.get('harness.commit')}")
        ck("E4 关掉归因开关 ⇒ 不产生 layer_credit（开关有效）",
           EvolutionLoop(led, NonRegressionGate(frozen, base), dev, mem_runner,
                         FixedProposer([cap]), caliber=cal, heldout=ho,
                         config=EvolutionConfig(attribute_layers=False)
                         ).run_round(H(), round_no=2).layer_credit is None)

    # ── F 组：跨轮经验记忆进入因果链 ──────────────────────────────
    print("F 组：跨轮经验记忆（被闸拒的变异不再重复提）")
    mem2 = MemoryOS(min_content_len=0)

    class Stub:
        name = "stub"

        def __init__(self, caps):
            self.caps = caps

        def propose(self, weakness, harness, n=3):
            return list(self.caps)

    caps = [Capsule(name="c1", genes=()), Capsule(name="c2", genes=())]
    p = MemoryAwareProposer(Stub(caps), mem2)
    w = {"kind": "missing_skill", "topic": "A"}
    ck("F1 第一轮：记忆为空 ⇒ 原样提议（不改变行为）",
       [c.name for c in p.propose(w, Harness())] == ["c1", "c2"])
    p.remember(w, "c1", "gate_reject: 非回归闸拦下")
    p.remember(w, "c2", "no_advantage(0.000)")
    ck("F2 被闸拒的 c1 记进记忆；no_advantage 的 c2 **不**记"
       "（不阻断'没用但无害'的探索）",
       sum(1 for n in mem2.notes.values()
           if "tried:" in n.content and "c1" in n.content) == 1
       and not any("c2" in n.content for n in mem2.notes.values()))
    out2 = [c.name for c in p.propose(w, Harness())]
    ck("F3 第二轮：c1 被记忆跳过 ⇒ 候选集真的变了（记忆进入因果链）",
       out2 == ["c2"] and p.skipped == ["c1"], f"{out2} / {p.skipped}")
    ck("F4 memory=None ⇒ 行为与内层完全一致（同协议可替换）",
       [c.name for c in MemoryAwareProposer(Stub(caps), None).propose(
           w, Harness())] == ["c1", "c2"])
    ck("F5 diagnostics 暴露跳过数（'记忆没生效'必须可观测）",
       p.diagnostics().get("memory_skipped_n") == 1, f"{p.diagnostics()}")

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


def _ok_runner(h, spec):
    from aether.verifier import Check, Verdict
    return Verdict(passed=True, checks=(Check("x", True, "got=1 expect=1"),),
                   evidence={"env": "x"})


def _bad_runner(h, spec):
    from aether.verifier import Check, Verdict
    return Verdict(passed=False, checks=(Check("x", False, "got=0 expect=1"),),
                   evidence={"env": "x"})


if __name__ == "__main__":
    sys.exit(main())
