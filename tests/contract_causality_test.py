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

    # ── G 组：记忆跨运行持久化 + 继承闸 ────────────────────────────
    # 2026-10-13 实测：两次 CLI 运行的记忆**完全相同**（L0:1 L2:2 L4:1）
    # ⇒ 记忆是纯进程内的 ⇒ "经验跨运行积累"是假的。
    # ⛔⛔ 但持久化之后马上出现二阶效应：继承的记忆把同一个种子 harness 的
    #    基线从 0.0 抬到 0.6 ⇒ 结果依赖历史 ⇒ 不可复现，且记忆在冒充能力。
    print("G 组：记忆跨运行持久化（含装载污染闸与继承闸）")
    from aether.embeddings import HashingEmbedder, SynonymEmbedder
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "memory.json"
        m1 = MemoryOS(embedder=HashingEmbedder(dim=256), min_content_len=0)
        m1.write("L2", "repair_hint:textscore 这个环境的失败模式集中在控制流，"
                       "保守通用修法可修好其中一部分但修不全。",
                 channel="curated", source="exec:round1")
        m1.write("L3", "这是一条含未验证标记的备忘：失败模式大概是控制流的"
                       "问题，具体原因待确认之后再补充完整证据。",
                 channel="learner", source="round1")
        n_saved = m1.save(mp)
        ck("G1 save 落盘条数 == 库中条数", n_saved == 2, f"{n_saved}")

        m2, r2 = MemoryOS.load(mp, HashingEmbedder(dim=256),
                               min_content_len=0)
        ck("G2 同名同 dim 的 embedder ⇒ 干净条目完整装载（污染条目被拦下）",
           r2["loaded"] == 1, f"{r2}")
        ck("G3 装载的条目带 `inherited` 标记（供继承闸识别）",
           all("inherited" in n.tags for n in m2.notes.values()),
           f"{[n.tags for n in m2.notes.values()]}")
        ck("G4 污染闸：含'大概是/待确认'的条目被拒载（跨运行累积 ≠ 累积噪声）",
           r2["rejected_unverified"] == 1, f"{r2}")
        ck("G5 字段完整性：装载进来的都真的可检索（不是'读出来但没向量'）",
           all(n.has_embedding for n in m2.notes.values()))

        # ⛔ 反证：换个 embedder 必须**整库拒载**，而不是"能读几条读几条"
        m3, r3 = MemoryOS.load(mp, SynonymEmbedder(), min_content_len=0)
        ck("G6 反证：embedder 不同 ⇒ 整库拒载（不得混库，要换须 reindex）",
           r3["loaded"] == 0 and r3["rejected_embedder_mismatch"] == 2,
           f"{r3}")
        # ⛔ 反证：不存在的路径 ⇒ 空库 + 显式说明，不是崩
        m4, r4 = MemoryOS.load(Path(td) / "nope.json", HashingEmbedder(dim=256))
        ck("G7 反证：无快照 ⇒ 空库且注明（首次运行与'库损坏'必须可分）",
           r4["loaded"] == 0 and "no_snapshot" in (r4.get("note") or ""),
           f"{r4}")

    # ⛔⛔ G8：继承闸 —— 继承的记忆**不得**抬高基线（这是本轮最硬的教训）
    #    反证组：同一条记忆若不带 inherited，基线必须真的上去；
    #            带了就上不去。两侧都测 ⇒ 证明闸在拦，而不是"恰好没命中"。
    from aether.agent import HarnessAgent
    from aether.environment import Environment, mutate_source
    env = Environment.from_dir(ROOT / "tasks" / "textscore")
    broken, _ = mutate_source(env.source, which=2)
    SPEC = {"env_id": env.env_id, "broken_source": broken,
            "topic": env.env_id, "task_class": "repair"}
    h_base = Harness(meta_rules="V6 元规则。",
                     tools=("read_file", "write_file", "python_exec"),
                     memory_policy={"recall_topk": 5, "min_content_len": 0})

    def _rew_hit(inherited: bool):
        m = MemoryOS(embedder=HashingEmbedder(dim=256), min_content_len=0)
        m.write("L2", f"repair_hint:{env.env_id} 这个环境的失败模式集中在"
                      f"控制流（比较与运算），保守通用修法可修好其中一部分。",
                channel="curated", source="exec")
        if inherited:
            #: ⛔ `MemNote` 现在是 **frozen**（本轮改动）：直接 `n.tags = ...`
            #:    抛 FrozenInstanceError。冻结的理由是"内容/标签必须与去重表
            #:    同源"，所以修改一律走 `dataclasses.replace()`（原对象替换，
            #:    `note_id` 与 `fingerprint` 都不变，语义与本测试要表达的
            #:    "把这条记忆标成跨运行继承来的"完全一致）。
            import dataclasses as _dc
            for nid, n in list(m.notes.items()):
                m.notes[nid] = _dc.replace(n, tags=tuple(n.tags) + ("inherited",))
        return HarnessAgent({env.env_id: env}, memory=m).run(
            h_base, SPEC).verdict.reward

    r_inh = _rew_hit(True)
    r_sess = _rew_hit(False)
    ck("G8 继承的记忆不进 L4 ⇒ 基线不漂移（可复现性）",
       r_inh == 0.0, f"inherited reward={r_inh}")
    ck("G9 反证：同一条记忆若属本轮（无 inherited）⇒ 基线真的上去"
       "（证明 G8 的 0 来自闸，不是来自'没召回'）",
       r_sess > 0.0, f"session reward={r_sess}")

    # ── H 组：层归因必须挑"提升真的发生"的任务 ────────────────────
    # 2026-10-13 实测：归因固定取发展集前 N 条 ⇒ scheduler/stateflow 组
    # 前 3 条落在 hard 上（reward 恒 0）⇒ 消融任何层 Δ 都是 0 ⇒
    # 输出"L0–L4 全无贡献"。那不是层没贡献，是**在永远读 0 的尺子上量差值**。
    print("H 组：层归因的任务挑选（不在恒 0 的尺子上量差值）")
    from aether.attribution import attribute_layers

    def _zero_always(h, spec):
        """恒 0 的任务：任何 harness 都修不好（hard 到超纲）。"""
        return _bad_runner(h, spec)

    def _needs_skill(h, spec):
        """需要 L1 路由 + L2 工具 + L3 技能齐备才拿 1 的任务。"""
        if "python_exec" not in h.tools:
            return _bad_runner(h, spec)
        r = h.routing.get("repair") or ""
        return _ok_runner(h, spec) if r and r in h.skills else _bad_runner(h, spec)

    def _mixed_runner(h, spec):
        return (_zero_always(h, spec) if spec.get("always_zero")
                else _needs_skill(h, spec))

    h_skill2 = H(skills={"gen": "修法"}, routing={"repair": "gen"})
    # 反证：specs 全选恒 0 的任务 ⇒ 归因必然全 0（仪器盲区，不是层无贡献）
    lc_blind = attribute_layers(_mixed_runner, h_skill2,
                                [{"always_zero": True}] * 3)
    ck("H1 反证：在恒 0 的任务上归因 ⇒ 全 0 且**自己报出**无判别力"
       "（不许把仪器盲区读成'层没贡献'）",
       all(abs(v) < 1e-9 for v in lc_blind.per_layer.values())
       and "没有判别力" in lc_blind.reason, f"{lc_blind.to_dict()}")
    # 正确：挑"提升真的发生"的任务 ⇒ 归因必须显出贡献
    lc_ok = attribute_layers(_mixed_runner, h_skill2, [{}] * 3)
    ck("H2 在提升真的发生的任务上 ⇒ L1/L2/L3 显出贡献（与 H1 对照）",
       lc_ok.per_layer.get("L1", 0) > 0 and lc_ok.per_layer.get("L3", 0) > 0,
       f"{lc_ok.to_dict()}")
    ck("H3 联合消融读数存在（单变量消融会低估互为备份的层）",
       "L3+L4" in lc_ok.joint_ablation, f"{lc_ok.joint_ablation}")

    # 挑选逻辑本身：dev 前面全是恒 0 时，必须跳过它们
    with tempfile.TemporaryDirectory() as td:
        dev_mixed = [FrozenTask(task_id=f"m{i}",
                                spec={"always_zero": True} if i < 3 else {})
                     for i in range(6)]
        loop_m = EvolutionLoop(Ledger(Path(td) / "l.jsonl"),
                               NonRegressionGate([], Baseline(caliber={},
                                                              verdicts={},
                                                              harness_id="s")),
                               dev_mixed, _mixed_runner, FixedProposer([]),
                               config=EvolutionConfig(attribution_tasks=2))
        picked = loop_m._pick_attribution_specs(h_skill2)
        ck("H4 前 N 条全是恒 0 ⇒ 跳过它们，挑到提升真的发生的任务",
           len(picked) == 2 and not any(s.get("always_zero") for s in picked),
           f"{picked}")
        ck("H5 全部任务都恒 0 ⇒ 退回前 N 条（不得返回空 ⇒ 归因静默消失）",
           len(EvolutionLoop(
               Ledger(Path(td) / "l2.jsonl"),
               NonRegressionGate([], Baseline(caliber={}, verdicts={},
                                              harness_id="s")),
               [FrozenTask(task_id="z0", spec={"always_zero": True})],
               _mixed_runner, FixedProposer([]),
               config=EvolutionConfig(attribution_tasks=2)
           )._pick_attribution_specs(h_skill2)) == 1)

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
