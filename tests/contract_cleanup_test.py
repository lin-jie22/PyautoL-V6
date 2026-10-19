#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：清理是**可被提议的变异**，而且走**同一套闸**。

═══ 它回答的那个「努力分配」问题 ═══

进化循环每轮都在**评估候选**，而没有任何东西在自己的产出上花一点力。
机制根源不是"没人想起去做"，而是**判据只认增益、不认减负**：

    · 非回归闸      —— 拦"变坏"，清理不变坏 ⇒ 过；
    · 组内相对优势  —— 为有噪声的 reward 采样设计 ⇒ 清理恒 0；
    · 能力地板      —— `gain < 0.01` ⇒ 清理（gain=0）**必死在这一条**；
    · 泛化闸        —— `dev_gain <= 0 ⇒ no_gain ⇒ blocks` ⇒ 清理又被拦一次。

⇒ 四条里三条按构造就拦不死它，一条（能力地板）按构造就必死。
  结果是"通过测试但不产生增益"的机制只增不减。

═══ 本契约守的三件事 ═══

  ① 自指负载真的**扫得出来**（保守口径，宁可漏报不可冤报）；
  ② 清理候选真的**删得干净**（摘链 + 删技能，不留幽灵路由）；
  ③ 减负轴的两道新闸**有判别力**（不是橡皮图章）：
       · 删对了 ⇒ `removal_safe`（放行）
       · 删错了 ⇒ `removal_regress`（held-out 掉了 ⇒ 拦）
     ⛔ 走旧的 `probe()` 会 100% 得 `no_gain` ⇒ 清理永远提交不了。
       那不是闸严，是**用错了闸**：overfit 问的是"涨的是不是真的"，
       而清理根本不涨 ⇒ 对清理该问的是"删完之后 held-out 变差没有"；

  ④ **`APPLIED` 不许被当成「已删除」**（G 组，r60）：
       `APPLIED` 的真实语义是「人已批准，可以去改了」，源码一字未动。
       名字骗人 + 没有办法分辨 ⇒ 链路停在那里，提案永远结不了案
       （实测：30 个独立提案被提了 509 次）。
       ⇒ 终态是 `EXECUTED`，且必须过 `verify_executed` 的**位置级复验**。

跑法：
    python tests/contract_cleanup_test.py
"""
import sys
import tempfile
from pathlib import Path

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main():
    import aether.agent as AG
    import aether.generalization as GV
    from aether.cleanup import (cleanup_genes, genome_dead_entries,
                                genome_load)
    from aether.environment import Environment, mutate_source
    from aether.genome import (Capsule, Harness, apply_capsule, apply_gene,
                               gene_remove_skill, gene_route_append,
                               parse_route)

    def H(skills=None, routing=None, **kw):
        base = dict(meta_rules="V6 元规则：奖励只来自执行判据。",
                    tools=("read_file", "write_file", "python_exec"),
                    skills=dict(skills or {}), routing=dict(routing or {}),
                    memory_policy={})
        base.update(kw)
        return Harness(**base)

    # ── A 组：扫描器的判别力（保守口径）────────────────────────────
    print("A 组：基因组死条目扫描（保守口径：宁可漏报，不可冤报）")
    h_clean = H({"fix_control_flow": "保守通用修法"},
                {"repair": "fix_control_flow"})
    ck("A1 干净 harness（技能在链上 + 名字在词汇表）⇒ 0 条死条目",
       genome_load(h_clean) == 0, f"n={genome_load(h_clean)}")

    h_unrouted = H({"fix_control_flow": "x", "fix_literals": "y"},
                   {"repair": "fix_control_flow"})
    e_un = genome_dead_entries(h_unrouted)
    ck("A2 不在任何路由链上的技能 ⇒ 判为 unrouted_skill（装载了也轮不到）",
       [x["kind"] for x in e_un["dead_entries"]] == ["unrouted_skill"]
       and e_un["dead_entries"][0]["name"] == "fix_literals",
       str(e_un["dead_entries"]))

    h_ghost = H({"fix_x": "没人认的技能"}, {"repair": "fix_x"})
    e_gh = genome_dead_entries(h_ghost)
    ck("A3 词汇表外的技能名（且在链上）⇒ ghost_skill，且带它所在的链",
       e_gh["dead_entries"] and e_gh["dead_entries"][0]["kind"] == "ghost_skill"
       and e_gh["dead_entries"][0]["carrying_routes"] == ["repair"],
       str(e_gh["dead_entries"]))

    h_dc = H({"patch::textscore": "定点修法"}, {"repair": "patch::textscore"})
    e_dc = genome_dead_entries(h_dc)
    ck("A4 ⛔ patch::（双冒号）必被报 —— agent 切出来是 ':x'，永不等于 env_id",
       [x["name"] for x in e_dc["dead_entries"]] == ["patch::textscore"],
       str(e_dc["dead_entries"]))

    ck("A5 uci: 前缀 ⇒ 不报（保守豁免：接入层可能不挂路由，静态不可判）",
       genome_load(H({"uci:ext.fixer": "外部能力"}, {})) == 0,
       f"n={genome_load(H({'uci:ext.fixer': 'x'}, {}))}")

    #: 反证：A2 那个技能接上链 ⇒ 它活了。用的是**真实基因操作**，
    #: 顺带证明扫描器读的是基因组本身，而不是某个独立缓存。
    h_fixed = apply_gene(h_unrouted, gene_route_append("repair", "fix_literals"))
    ck("A6 反证：把 A2 那条技能挂上路由 ⇒ 它活了（证明扫描器在判，不是全报）",
       genome_load(h_fixed) == 0, f"n={genome_load(h_fixed)}")

    # ── B 组：清理基因真的删干净 ───────────────────────────────────
    print("B 组：清理候选真的删干净（不留幽灵路由）")
    if not e_gh["dead_entries"]:
        ck("B0 扫描器报出了 A3 的幽灵技能（否则下面全是空真）", False,
           "e_gh 为空")
        return 1
    genes = cleanup_genes(h_ghost, e_gh["dead_entries"][0])
    layers = sorted(g.target.split(".")[0] for g in genes)
    ck("B1 在链上的死技能 ⇒ 基因是【摘链 + 删技能】两条（缺一 ⇒ 制造死条目）",
       layers == ["L1", "L3"] and all(g.op == "remove" for g in genes),
       str([(g.op, g.target, g.value) for g in genes]))
    h_after = apply_capsule(h_ghost, Capsule(name="c", genes=genes,
                                             origin="cleanup"))
    ck("B2 ⛔ 清完后链上不再有已删项（不留幽灵路由）",
       "fix_x" not in parse_route(h_after.routing.get("repair"))
       and "fix_x" not in h_after.skills,
       f"routing={h_after.routing} skills={list(h_after.skills)}")
    ck("B3 减负读数**现扫复核**：真的少 1 条（不是提案自报的数）",
       genome_load(h_ghost) - genome_load(h_after) == 1,
       f"{genome_load(h_ghost)}→{genome_load(h_after)}")
    #: 反证：只删技能不摘链 ⇒ B2 立刻会红 ⇒ B2 不是恒真。
    h_half = apply_capsule(h_ghost, Capsule(
        name="c", genes=(gene_remove_skill("fix_x"),), origin="cleanup"))
    ck("B4 反证：只删技能不摘链 ⇒ 留下幽灵路由（B2 因此不是恒真）",
       "fix_x" in parse_route(h_half.routing.get("repair"))
       and "fix_x" not in h_half.skills,
       f"routing={h_half.routing}")
    #: 链被摘空 ⇒ 整个路由键也要去掉，不留空壳键。
    h_one = H({"fix_x": "x"}, {"repair": "fix_x"})
    e_one = genome_dead_entries(h_one)
    h_none = apply_capsule(h_one, Capsule(
        name="c", genes=cleanup_genes(h_one, e_one["dead_entries"][0]),
        origin="cleanup"))
    ck("B5 链被摘空 ⇒ 整个路由键也去掉（不留空壳键）",
       "repair" not in h_none.routing, f"routing={h_none.routing}")

    # ── C 组：词汇表与执行体同源（扫描器不许另抄一份名单）────────────
    print("C 组：词汇表与 `_apply_one` 同源")
    env = Environment.from_dir(ROOT / "tasks" / "textscore")
    broken, _ = mutate_source(env.source, which=2)
    from aether.agent import HarnessAgent
    agent = HarnessAgent({env.env_id: env})
    spec = {"env_id": env.env_id, "broken_source": broken,
            "topic": env.env_id, "task_class": "repair"}

    out_ok, ok_ok = agent._apply_one("fix_control_flow", broken, env, spec, [])
    ck("C1 词汇表认可的 fix_control_flow 在破损源码上**真改了东西**",
       ok_ok and out_ok != broken, f"ok={ok_ok} changed={out_ok != broken}")
    #: 幽灵名字：恒等，但**被记为"适用"** ⇒ active 非空 ⇒ L4 被压制。
    out_gh, ok_gh = agent._apply_one("totally_made_up", broken, env, spec, [])
    ck("C2 词汇表外的名字 ⇒ `_apply_one` 恒等、**且被记为『适用』**"
       "（正是它压制 L4 记忆通路的原因）",
       out_gh == broken and ok_gh is True, f"ok={ok_gh} changed={out_gh != broken}")
    out_dc, ok_dc = agent._apply_one("patch::textscore", broken, env, spec, [])
    ck("C3 ⛔ patch::x ⇒ 判为『不适用』（结构上永不生效）",
       ok_dc is False and out_dc == broken, f"ok={ok_dc}")
    #: 词汇表与扫描器的一致性：表里认可的名字，扫描器不许报它是幽灵。
    bad = [nm for nm in AG.KNOWN_STRATEGIES if nm != "memory_hint"
           and any(x["name"] == nm and x["kind"] == "ghost_skill"
                   for x in genome_dead_entries(
                       H({nm: "x"}, {"repair": nm}))["dead_entries"])]
    ck("C4 词汇表认可的每一个名字 ⇒ 扫描器都不报它是幽灵（两份名单一致）",
       not bad, f"不一致={bad}")

    # ── D 组：进化循环真的把清理当候选提（且不被「无弱点」短路）──────
    print("D 组：清理是**可被提议的变异**（常设，不看 weakness）")
    from aether.evolution import EvolutionConfig, EvolutionLoop
    from aether.ledger import Ledger
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.providers import CleanupProposer, TemplateProposer
    from aether.verifier import Check, Verdict

    tmp = Path(tempfile.mkdtemp(prefix="v6_cleanup_"))
    CAL = {"seed": 0}

    def mk(passed, name="c"):
        return Verdict(passed=passed, checks=(Check(name, passed, "ev"),),
                       evidence={"n": 1})

    def runner_green(h, spec=None):
        """发展集/held-out **全绿** ⇒ 挖不到弱点 ⇒ 走原来的 noop 路径。"""
        return mk(True)

    ft = FrozenTask("t1", {})
    gate = NonRegressionGate([ft],
                             Baseline(caliber=CAL, verdicts={"t1": mk(True)}))
    h_dirty = H({"fix_control_flow": "x", "fix_x": "没人认的"},
                {"repair": "fix_control_flow|fix_x"})

    loop = EvolutionLoop(ledger=Ledger(tmp / "l_a.jsonl"), gate=gate,
                         dev_suite=[ft], runner=runner_green,
                         proposer=TemplateProposer(skills={}), caliber=CAL,
                         config=EvolutionConfig(cleanup_group_size=2))
    rep = loop.run_round(h_dirty, round_no=1)
    ck("D1 发展集全绿（无弱点）时，循环**不再直接 return**，仍走到清理候选",
       rep.cleanup_proposed >= 1, f"cleanup_proposed={rep.cleanup_proposed}")
    cleanup_cands = [c for c in rep.candidates if c.is_cleanup]
    ck("D2 清理候选真的进过闸（被评估、被记账），不是只提不算",
       bool(cleanup_cands),
       str([(c.capsule_name, c.is_cleanup, c.reason) for c in rep.candidates]))
    ck("D3 减负读数是**现扫**出来的 + 清理候选**不是**因 no_advantage 被拦",
       bool(cleanup_cands) and cleanup_cands[0].load_gain >= 1
       and not any("no_advantage" in (c.reason or "") for c in cleanup_cands),
       str([(c.capsule_name, c.load_gain, c.reason) for c in cleanup_cands]))
    ck("D4 全绿且清理有效 ⇒ 这一轮**提交了**（不再空转 9 轮）",
       rep.improved, f"committed={rep.committed} revert={rep.revert_reason}")

    #: 反证：关掉清理 ⇒ 回到原行为。
    loop_off = EvolutionLoop(ledger=Ledger(tmp / "l_off.jsonl"), gate=gate,
                             dev_suite=[ft], runner=runner_green,
                             proposer=TemplateProposer(skills={}), caliber=CAL,
                             cleanup=None,
                             config=EvolutionConfig(cleanup_enabled=False))
    rep_off = loop_off.run_round(h_dirty, round_no=1)
    ck("D5 反证：cleanup_enabled=False 且未注入 ⇒ 回到原 noop 行为"
       "（D1 因此不是恒真）",
       rep_off.cleanup_proposed == 0 and rep_off.idle
       and rep_off.revert_reason.startswith("no_weakness_evidence"),
       f"proposed={rep_off.cleanup_proposed} idle={rep_off.idle} "
       f"reason={rep_off.revert_reason}")

    # ── E 组：泛化闸的**退化面**（不是放宽，是换职责）────────────────
    print("E 组：清理走泛化闸的退化面（held-out 不许变差）")
    ho = GV.HeldOutSuite(name="ho", tasks=(FrozenTask("h1", {}),),
                         env_id="eventflow")
    g = GV.GeneralizationGate(ho, dev_task_ids=("t1",))
    h_clean_after = apply_capsule(h_dirty, Capsule(
        name="c",
        genes=cleanup_genes(h_dirty, genome_dead_entries(
            h_dirty)["dead_entries"][0]), origin="cleanup"))

    pr_old = g.probe(h_dirty, h_clean_after, runner_green, dev_gain=0.0)
    ck("E1 清理候选走旧 `probe()` ⇒ 必被判 no_gain 拦下（这就是原来的死结）",
       pr_old.verdict == "no_gain" and pr_old.blocks, f"{pr_old.verdict}")
    pr_new = g.probe_removal(h_dirty, h_clean_after, runner_green)
    ck("E2 改走 `probe_removal` ⇒ 放行（held-out 没变差）",
       pr_new.verdict == "removal_safe" and not pr_new.blocks,
       f"{pr_new.verdict} ho={pr_new.heldout_gain:+.2f}")

    #: 反证：删掉一个在 held-out 上**真被用到**的条目 ⇒ 必须拦。
    def runner_uses_dead(h, spec=None):
        """held-out 上真的会用到那个"看起来没人用"的技能。"""
        return mk("fix_x" in (h.skills or {}))

    pr_bad = g.probe_removal(h_dirty, h_clean_after, runner_uses_dead)
    ck("E3 反证：删掉在 held-out 上真被用到的条目 ⇒ removal_regress（拦）",
       pr_bad.verdict == "removal_regress" and pr_bad.blocks,
       f"{pr_bad.verdict} ho={pr_bad.heldout_gain:+.2f}")
    ck("E4 反证（续）：E2 与 E3 的裁决不同 ⇒ E2 不是橡皮图章",
       pr_new.verdict != pr_bad.verdict, f"{pr_new.verdict} vs {pr_bad.verdict}")
    ck("E5 新裁决名已登记进封闭集合（否则 blocks 会把合法放行判成拦截）",
       {"removal_safe", "removal_regress"} <= set(GV.VERDICTS)
       and "removal_safe" in GV.PASS_VERDICTS,
       f"VERDICTS={GV.VERDICTS} PASS={GV.PASS_VERDICTS}")

    # ── F 组：⛔⛔ 钉住**生产默认口径**（不许只是"传了才生效"）──────
    print("F 组：生产默认值本身（推翻默认值 ⇒ 这里必须转红）")
    #: ⛔⛔ 通用形态：**用显式参数构造的测试，只能证明"传了会生效"，
    #:    证明不了"生产默认就启用"。**
    #:    D5 是"显式传 False"的对照组。万一有人把那种写法抄成 D 组的
    #:    构造方式（对照组喧宾夺主），全文件就没有一条还在走默认路径
    #:    ⇒ 生产里这根轴是关的，而契约全绿。⇒ 必须另有一组**直接断言
    #:    默认值本身**的条目：它不依赖任何一条传参路径。
    #:
    #: ⛔⛔ 而每一条的**判别力是实测出来的，不是我以为的**（2026-10-16）。
    #:    逐个翻默认值、在 /tmp 的独立副本里跑本文件，实测转红面：
    #:      A  `cleanup_enabled`    True→False  ⇒ D1 D2 D3 D4 + F1 F4（6 条）
    #:      B  `min_load_gain`      1.0→0.1     ⇒ **只有 F2**（1 条）
    #:      C  `cleanup_group_size` 1→5         ⇒ **只有 F3**（1 条）
    #:    ⇒ 两条结论，与我写下 F 组时的设想**相反**（别照旧注释改回去）：
    #:      ① F1/F4 的价值不是"唯一能看见"，而是**直接命中根因**。
    #:         翻 `cleanup_enabled` 时 D1–D4 也会红，但那 4 条读起来是
    #:         "这一轮无事可做" —— 与关掉清理之前的旧行为**同形**，
    #:         只会把人引向"循环动不了"，而不是"开关默认就是关的"。
    #:      ② F2/F3 才是**全文件唯一的读数**：把减负地板放松到 0.1 条、
    #:         或一次清 5 条，除它们之外 30 条断言一条都不响。
    #:    ⇒ E1–E5 在三个事故版下**都不红**（E 组直接构造 gate，不经过
    #:      EvolutionConfig）—— 预期之内，也说明不该让 E 组来盯默认值。
    dflt = EvolutionConfig()
    ck("F1 ⛔ 生产默认 cleanup_enabled=True"
       "（翻成 False ⇒ 轴空转；D1–D4 连带红，F1/F4 才直接指向根因）",
       dflt.cleanup_enabled is True, f"{dflt.cleanup_enabled}")
    ck("F1x ⛔ 反证：显式关掉 ⇒ F1 转红（F1 不是恒真）",
       EvolutionConfig(cleanup_enabled=False).cleanup_enabled is False)
    ck("F2 ⛔ 生产默认 min_load_gain >= 1.0"
       "（至少真少一条才算减负；放松到 0.1 ⇒ **全文件只有这条会红**）",
       dflt.min_load_gain >= 1.0, f"{dflt.min_load_gain}")
    ck("F3 ⛔ 生产默认 cleanup_group_size == 1"
       "（最小编辑：一次只清一条；改成 5 ⇒ **全文件只有这条会红**）",
       dflt.cleanup_group_size == 1, f"{dflt.cleanup_group_size}")
    #: ⛔ 自动构造也是"默认开"的一部分：没有注入时必须**自己造一个**，
    #:    不构造 ⇒ 这条通路一个候选都提不出来 ⇒ 闸永远不裁决（假仪表）。
    ck("F4 ⛔ 未注入且默认开 ⇒ __init__ 真的自己造了清理提案器",
       isinstance(loop.cleanup, CleanupProposer),
       f"{type(loop.cleanup).__name__}")
    ck("F5 ⛔ 反证：显式关掉 ⇒ 是真的没有提案器（不是造了却不用）",
       EvolutionLoop(ledger=Ledger(tmp / "l_f5.jsonl"), gate=gate,
                     dev_suite=[ft], runner=runner_green,
                     proposer=TemplateProposer(skills={}), caliber=CAL,
                     config=EvolutionConfig(cleanup_enabled=False)
                     ).cleanup is None)

    # ── G 组：⛔⛔ 「批准 ≠ 已删除」—— 执行闭环（r60）──────────────
    print("G 组：人执行之后的复验（APPLIED 不许被当成『已删除』）")
    #: ⛔ 本组针对的形态（2026-10-19 实测）：
    #:    账本里 `sandbox_detail` 早在 10-15 就是 `applied`，而
    #:    `aether/interop.py:473` 的赋值到 10-19 还在原位 —— 批准了，
    #:    但补丁没被执行，账本上却看不出来。
    #:    ⇒ 链路上没有"执行后复验"这一环，`APPLIED` 就成了终点。
    import shutil
    from aether.cleanup import (APPLIED, EXECUTED, CleanupProposal,
                                _base_count, _dedup_key, _is_duplicate,
                                verify_executed)
    from aether.ledger import content_hash
    gdir = Path(tempfile.mkdtemp(prefix="cl_g_"))
    try:
        (gdir / "aether_probe").mkdir()
        gf = gdir / "aether_probe" / "mod.py"
        gf.write_text("class A:\n"
                      "    def __init__(self):\n"
                      "        self.zz = 1\n"
                      "        self.zz = 2\n", encoding="utf-8")

        def mkp():
            p = CleanupProposal(
                kind="write_only", target="zz",
                where="aether_probe/mod.py:3", state=APPLIED,
                gate_passed=True,
                evidence={"scan_entry": {"n_assign": 2}})
            p.prop_id = "g_dummy"
            return p

        ck("G1 ⛔ APPLIED 与 EXECUTED 是两个不同的状态"
           "（不是一个值的两个名字）",
           APPLIED != EXECUTED and bool(EXECUTED), f"{APPLIED}/{EXECUTED}")

        r2 = verify_executed(mkp(), src_root=gdir, at=1)
        ck("G2 ⛔ 目标还在原位 ⇒ 不许结案（保持 APPLIED，不冒充已删）",
           r2.state == APPLIED
           and r2.evidence["execution"]["ok"] is False
           and r2.evidence["execution"]["unverified"] is False,
           f"state={r2.state} {r2.evidence.get('execution')}")

        #: 人真的删掉了那一处（2 处 → 1 处）
        gf.write_text("class A:\n"
                      "    def __init__(self):\n"
                      "        self.zz = 1\n", encoding="utf-8")
        r3 = verify_executed(mkp(), src_root=gdir, at=1)
        ck("G3 目标少了一处 ⇒ 放行结案（state=EXECUTED）",
           r3.state == EXECUTED and r3.evidence["execution"]["ok"] is True,
           f"state={r3.state} {r3.evidence.get('execution')}")

        ck("G4 ⛔ 反证：G2 与 G3 裁决相反 ⇒ 这两条不是恒真/恒假",
           r2.state != r3.state
           and r2.evidence["execution"]["ok"]
           != r3.evidence["execution"]["ok"])

        p5 = CleanupProposal(kind="write_only", target="zz",
                             where="aether_probe/nonexistent.py:1",
                             state=APPLIED, gate_passed=True,
                             evidence={"scan_entry": {"n_assign": 2}})
        r5 = verify_executed(p5, src_root=gdir, at=1)
        ck("G5 ⛔ 读不到目标 ⇒ unverified 且**不结案**（不可判别 ≠ 已删除）",
           r5.state == APPLIED
           and r5.evidence["execution"]["unverified"] is True
           and r5.evidence["execution"]["ok"] is False,
           f"{r5.evidence.get('execution')}")

        #: ⛔ 去重键：行号漂移时 prop_id 会变，语义键不变
        a = CleanupProposal(kind="write_only", target="zz",
                            where="aether_probe/mod.py:3")
        b = CleanupProposal(kind="write_only", target="zz",
                            where="aether_probe/mod.py:25")
        a.prop_id = content_hash(a.identity())[:32]
        b.prop_id = content_hash(b.identity())[:32]
        ck("G6 ⛔ 行号漂到 25 ⇒ prop_id 变了（所以光靠 id 去重不够）",
           a.prop_id != b.prop_id, f"{a.prop_id[:8]} vs {b.prop_id[:8]}")
        ck("G7 同一条（删什么没变）⇒ 语义去重键**不变**（跨行号仍去重）",
           _dedup_key(a) == _dedup_key(b),
           f"{_dedup_key(a)} vs {_dedup_key(b)}")

        ck("G8 ⛔ 基线取不到时退回 1，绝不退回 0"
           "（基线 0 ⇒「还在」被判成「已删」，闸反转为恒绿）",
           _base_count(CleanupProposal(), 1) == 1)

        #: ⛔ G9–G11 测的是**装配**：`build()` 用的就是这个函数
        #:    （只测 `_dedup_key` 的话，忘了用语义键也全绿）
        def mkid(where, target="zz"):
            q = CleanupProposal(kind="write_only", target=target,
                                where=where)
            q.prop_id = content_hash(q.identity())[:32]
            return q

        old = mkid("aether_probe/mod.py:3")
        drift = mkid("aether_probe/mod.py:25")
        other = mkid("aether_probe/mod.py:40", target="yy")
        ex = {old.prop_id: old}
        ck("G9 ⛔ 同一条已提过（id 相同）⇒ 判重复",
           _is_duplicate(old, ex))
        ck("G10 ⛔ 同一条但**行号漂到 25** ⇒ 仍判重复（跨行号去重）",
           _is_duplicate(drift, ex))
        ck("G11 ⛔ 反证：换了个目标（zz→yy）⇒ 不判重复"
           "（证明 G9/G10 不是「有档案就全拦」）",
           not _is_duplicate(other, ex))
        ck("G12 反证（续）：空档案 ⇒ 不判重复（第一次扫要能提得出东西）",
           not _is_duplicate(old, {}))
    finally:
        shutil.rmtree(gdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"❌ 失败 {len(FAILS)} 条：{FAILS}")
    else:
        print("✅ 全部通过")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
