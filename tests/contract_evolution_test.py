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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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

    # ── C7 组：提议器必须能表达"无可用变异"（空 genes 是哨兵，不是错误）──
    #    ⛔ 2026-10-14（r33）实测事故：给 `Capsule` 加"genes 不得为空"的构造
    #       校验，把 `TemplateProposer` 的收尾哨兵 `Capsule(name="noop",
    #       genes=())` 判成非法 ⇒ 技能已全部接通时 `propose()` 当场抛
    #       ValueError，而 `evolution._run_round` 对 `propose` 的调用**没有**
    #       try/except ⇒ 进化循环在最该保守停手时整体炸掉。
    #    ⛔ 本组守两条：① 空 genes 的 Capsule 语义良定义（恒等应用）；
    #       ② "没有可用变异"这条路径真的被走到（不是靠巧合不触发）。
    print("C7 组：空 genes 哨兵（无可用变异）")
    from aether.genome import Gene as _Gene, apply_capsule as _apply
    noop_cap = Capsule(name="noop", genes=())
    ck("C7 空 genes 的 Capsule 可以构造（它是哨兵，不是残缺对象）",
       noop_cap.genes == () and bool(noop_cap.capsule_id))
    ck("C7b 空 genes ⇒ 爆炸半径为 0、不碰受保护层",
       noop_cap.blast_layers() == () and not noop_cap.touches_protected())
    h_noop = Harness(meta_rules="元规则")
    ck("C7c 空 genes ⇒ 应用是**恒等**（harness_id 不变）",
       _apply(h_noop, noop_cap) is h_noop
       and _apply(h_noop, noop_cap).harness_id == h_noop.harness_id)

    #: 走到哨兵：技能为空时 `if s not in cur` 滤空全部候选（真实可达路径）
    p_empty = TemplateProposer(skills={})
    caps_empty = p_empty.propose({"kind": "missing_skill", "topic": "t"},
                                 Harness())
    ck("C7d 无可用变异 ⇒ propose 返回 noop 哨兵（不抛异常、不返回空）",
       [c.name for c in caps_empty] == ["noop"]
       and caps_empty[0].genes == (), f"{[c.name for c in caps_empty]}")
    #: 反证：技能**已全部在路由链上**时同样走哨兵（第二条可达路径）。
    #: ⛔ 判据读的是 `harness.routing[route_key]`（链），不是"L3 里有没有技能"
    #:    ⇒ 构造必须把技能**接到链上**，否则技能装了但不可达 —— 那正是
    #:    C4/H 组反复守过的那个坑（技能 + 路由必须一起改）。
    h_all2 = _apply(Harness(), Capsule(
        name="wire",
        genes=(_Gene(op="set", target="L3.skills.s1", value="b"),
               _Gene(op="append", target="L1.routing.repair", value="s1"))))
    p_all = TemplateProposer(skills={"s1": "b"})
    caps_all = p_all.propose({"kind": "missing_skill", "topic": "t"}, h_all2)
    ck("C7e 反证：技能已在链上 ⇒ 也不重复提，走哨兵（不是恒不提）",
       [c.name for c in caps_all] == ["noop"], f"{[c.name for c in caps_all]}")
    ck("C7f 对照：同一条技能**不在**链上时确实会提（C7e 不是恒真）",
       [c.name for c in p_all.propose({"kind": "missing_skill"}, Harness())]
       == ["add_skill:s1"])

    # ── I 组：L2（工具层）**可达性** —— 词表漏检修复的反证 ─────────
    #: ⛔ 2026-10-15 实测：`agent.require_tools` 缺工具时报的是
    #:    `PermissionError: 缺工具 ['python_exec']`，而旧词表的 needle 是
    #:    `tool_not_allowed` / `primitive`（**全代码库 0 生产者**）⇒ 真实
    #:    生产者被漏掉 ⇒ L2 结构上不可达：哪怕真缺工具，也只会被归成"缺技能"
    #:    ⇒ 去加一个不解决病因的技能（"看着在动"）。
    #:    本组守：真实生产者 ⇒ 正确归类 ⇒ 提议开**真正缺的那个**工具。
    print()
    print("I 组：L2（工具层）可达性")
    from aether.evolution import _ERROR_TO_KIND
    real_err = "PermissionError: 缺工具 ['python_exec']"
    ck("I1 ⛔ 真实生产者（缺工具）归类为 missing_tool（旧词表漏检）",
       classify_error(real_err) == "missing_tool", classify_error(real_err))
    caps_tool = TemplateProposer().propose(
        {"kind": "missing_tool", "topic": "t", "evidence": [real_err]},
        Harness())
    ck("I2 ⛔ 提议开的是**真正缺的**工具（不是写死的 read_file）",
       [c.name for c in caps_tool] == ["tool:python_exec"],
       f"{[c.name for c in caps_tool]}")
    br_tool = blast_radius(Harness(), _apply(Harness(), caps_tool[0]))
    ck("I3 该变异真的落在 tools 层（L2 被碰到，不是空转）",
       "tools" in br_tool["layers_touched"], f"{br_tool['layers_touched']}")
    ck("I4 不触碰受保护层（L0）", not br_tool["touches_protected"],
       f"{br_tool}")
    #: 反证：拿不到工具名 ⇒ 不提（走 noop 哨兵），不是"开个默认工具"。
    caps_noname = TemplateProposer().propose(
        {"kind": "missing_tool", "topic": "t"}, Harness())
    ck("I5 反证：证据里没有工具名 ⇒ 不提（不回退到开默认工具）",
       [c.name for c in caps_noname] == ["noop"],
       f"{[c.name for c in caps_noname]}")

    # ── J 组：L4（记忆策略层）**接口可达性** ────────────────────────
    #: ⛔ 如实标注：当前**没有自动分类路径**产出 `recall_miss`（L4 通路只在
    #:    链上无技能时才走，那时真正的缺口是技能）⇒ 它作为**显式接口**保留。
    #:    本组守：指定它 ⇒ 真的产出含 memory_policy 的变异（不是假入口）。
    print()
    print("J 组：L4（记忆策略层）接口可达性")
    caps_recall = TemplateProposer().propose(
        {"kind": "recall_miss", "topic": "t"}, Harness())
    ck("J1 显式指定 recall_miss ⇒ 产出 L4 变异",
       [c.name for c in caps_recall] == ["tune:recall"],
       f"{[c.name for c in caps_recall]}")
    br_rec = blast_radius(Harness(), _apply(Harness(), caps_recall[0]))
    ck("J2 该变异落在 memory_policy 层",
       "memory_policy" in br_rec["layers_touched"],
       f"{br_rec['layers_touched']}")
    ck("J3 ⛔ 词表里**没有** recall_miss 的自动路径（如实标注，不是假仪表）",
       all(k != "recall_miss" for _, k in _ERROR_TO_KIND), f"{_ERROR_TO_KIND}")

    # ── K 组：词表诚实性 —— 每条 needle 必须有**代码生产者** ─────────
    #: ⛔ 判据设计（本组自身踩过的坑）：**必须排除词表自身所在的常量**，
    #:    否则 needle 自己就是"生产者"，判据恒真。而且**只扫字符串字面量**
    #:    ——注释里提到某个 needle 不算生产者（生产者是"代码会产出这个串"）。
    print()
    print("K 组：词表诚实性（每条 needle 必须有生产者）")
    import ast as _ast
    _aeth = ROOT / "aether"
    _evo_p = _aeth / "evolution.py"
    _evo_lines = _evo_p.read_text(encoding="utf-8").splitlines()
    _i0 = next(i for i, l in enumerate(_evo_lines)
               if l.startswith("_ERROR_TO_KIND"))
    _i1 = next(i for i in range(_i0, len(_evo_lines))
               if _evo_lines[i].strip() == ")")
    _table_lines = set(range(_i0, _i1 + 1))
    _lits = []
    for _p in sorted(_aeth.rglob("*.py")):
        try:
            _tree = _ast.parse(_p.read_text(encoding="utf-8"))
        except Exception:                          # noqa: BLE001
            continue
        _is_evo = (_p == _evo_p)
        for _n in _ast.walk(_tree):
            if not (isinstance(_n, _ast.Constant)
                    and isinstance(_n.value, str)):
                continue
            if _is_evo and (getattr(_n, "lineno", 0) - 1) in _table_lines:
                continue                           # 词表自身不是生产者
            _lits.append(_n.value)
    _corpus = "\n".join(_lits).lower()

    def _orphans(table):
        return [n for n, _ in table if n.lower() not in _corpus]

    ck("K1 ⛔ 词表每条 needle 在代码里有字符串生产者（否则=假仪表）",
       _orphans(_ERROR_TO_KIND) == [], f"无生产者：{_orphans(_ERROR_TO_KIND)}")
    ck("K2 反证：注入假条目 ⇒ 同一判据变红（K1 有判别力，不是恒真）",
       _orphans((("__no_such_needle__", "x"),)) == ["__no_such_needle__"])

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

    # ── H 组：并发评估（r31 · 默认执行）────────────────────────
    print("H 组：并发评估 —— 只并行计算，run_id / 账本必须串行")

    import threading as _th

    class _Probe:
        """包装 runner，记录**真实并发度**。

        ⛔ 为什么需要它：`parallel.batches>0` 只证明"走了并行分支"，不证明
        "真的并发跑了"。配了 worker 却仍然串行（比如池大小写死 1）在
        读数上完全看不出来 —— 与「落点要兑现」同一条纪律。
        """

        def __init__(self, inner):
            self.inner = inner
            self.lock = _th.Lock()
            self.cur = 0
            self.max = 1
            self.n = 0
            self.errors = 0

        def __call__(self, h, spec):
            with self.lock:
                self.cur += 1
                self.max = max(self.max, self.cur)
                self.n += 1
            try:
                time.sleep(0.08)          #: 撑开并发窗口，让并发度可观测
                return self.inner(h, spec)
            except Exception:             # noqa: BLE001
                #: ⛔ 吞异常会让 H4 变成"全 error_verdict 比全 error_verdict"
                #:    ⇒ 一致性断言近乎恒真（与 B2 同款）。所以**记账**。
                with self.lock:
                    self.errors += 1
                raise
            finally:
                with self.lock:
                    self.cur -= 1

    def _mk(workers, r, name):
        from aether.evolution import EvolutionLoop, EvolutionConfig
        return EvolutionLoop(
            Ledger(tmp / name), gate, dev, r,
            TemplateProposer(skills={"restore_from_baseline": "恢复到基线"}),
            store=None, caliber=CAL,
            config=EvolutionConfig(group_size=2, parallel_workers=workers))

    ps = _Probe(runner)
    lp_s = _mk(0, ps, "hs.jsonl")
    rep_s = lp_s.run_round(h, round_no=1)
    ck("H1 ⛔ 默认串行 ⇒ parallel.batches=0（配了 worker 却一次没并行 = 空账）",
       rep_s.parallel["configured"] == 0 and rep_s.parallel["batches"] == 0,
       str(rep_s.parallel))
    ck("H1b 探针自证：串行下最大并发度 == 1（否则探针本身是坏的，"
       "H3 的 >1 就没有判别力）",
       ps.max == 1, f"max={ps.max}")

    pp = _Probe(runner)
    lp_p = _mk(4, pp, "hp.jsonl")
    rep_p = lp_p.run_round(h, round_no=1)
    ck("H2 开 4 workers ⇒ 走了并行分支（batches>0, runs>0）",
       rep_p.parallel["configured"] == 4 and rep_p.parallel["batches"] > 0
       and rep_p.parallel["runs"] > 0, str(rep_p.parallel))
    ck("H3 ⛔ **真并发**：最大并发度 > 1（不是'进了分支但仍串行'）",
       pp.max > 1, f"max={pp.max} n={pp.n}")

    ck("H3b ⛔ 判别力自检：探针内部**没抛异常**（否则本组是在 "
       "error_verdict 上对拍 ⇒ H4 的一致性近乎恒真）",
       ps.errors == 0 and pp.errors == 0,
       f"ser_err={ps.errors} par_err={pp.errors}")

    ck("H4 ⛔ 并行与串行**结果一致**（基线分 / 逐候选 reward / 是否提交）",
       abs(rep_p.baseline_reward - rep_s.baseline_reward) < 1e-12
       and [c.reward for c in rep_p.candidates]
       == [c.reward for c in rep_s.candidates]
       and (rep_p.committed is not None) == (rep_s.committed is not None),
       f"ser={rep_s.baseline_reward}/{[c.reward for c in rep_s.candidates]} "
       f"par={rep_p.baseline_reward}/{[c.reward for c in rep_p.candidates]}")

    ids = [r.run_id for r in lp_p._runs]
    ck("H5 ⛔ 并行下 run_id 仍唯一（`_run_seq += 1` 非原子 ⇒ 分配必须串行）",
       len(ids) == len(set(ids)) and len(ids) > 0,
       f"{len(ids)} 条 / 去重后 {len(set(ids))} 条")

    def _task_seq(lp):
        return [e.payload.get("task_id")
                for e in lp.ledger.events() if e.kind == "eval.verdict"]

    ck("H6 ⛔ 账本里 eval.verdict 的**任务顺序**与串行完全一致"
       "（顺序漂移 ⇒ 账本不再逐条可复现，而它是唯一事实源）",
       _task_seq(lp_p) == _task_seq(lp_s) and len(_task_seq(lp_s)) > 0,
       f"ser={_task_seq(lp_s)} par={_task_seq(lp_p)}")

    #: 边界：workers=1 与 0 同义（不该建线程池）
    p1 = _Probe(runner)
    lp_1 = _mk(1, p1, "h1.jsonl")
    rep_1 = lp_1.run_round(h, round_no=1)
    ck("H7 workers=1 ⇒ 与串行同义（batches=0，不建池）",
       rep_1.parallel["batches"] == 0 and p1.max == 1,
       f"{rep_1.parallel} max={p1.max}")

    print(f"  ℹ️  并发读数：串行 {ps.n} 次调用 max={ps.max} / "
          f"并行 {pp.n} 次调用 max={pp.max}（加速比依赖宿主，不作断言）")

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
