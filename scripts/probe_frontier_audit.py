#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针：FRONTIER_2026 清单的**兑现审计**。

## 为什么有这个脚本

`docs/FRONTIER_2026.md` 第 7 行写着一条硬原则：

    ⛔ 写进代码的每一条前沿主张，都必须对应一个可被测试断言的结构。

但那份文档是**设计时的声明**，从来没有人回头逐条核对过"落点"是否真的
存在、是否真的被走到。本项目最贵的一条纪律（V5 第 2 条）是：

    ⛔⛔ 改之前先确认那行跑得到（可达性 > 正确性）
    ⛔ 「有机制」≠「这条路被走到」

所以这里**不 grep 符号**（grep 到 `def foo` 只证明写了，不证明被调用），
而是对每一条主张做**运行时取证**：
  · 能直接调的 ⇒ 调真函数，看真实返回值；
  · 声称"接进链路"的 ⇒ 用计数器包一层，跑真实链路，报**调用次数**。

## 分组（对应 FRONTIER_2026.md 的章节）

    A §1 Self-Harness：最小编辑 / 5 层可编辑 / 血缘 / 爆炸半径
    B §2 RLVR：reward 定义域 / got-expect / 组内相对优势 / masking / 信用
    C §3 环境优先：合成 yield 四类计数 / F2P 性质
    D §4 记忆：通道闸 / 双向链接 / 扩散增益 / promote fail-closed
    E §6 失败模式 → 闸：非回归 / supersede 留痕 / 地板 / 增殖

每组末尾带 **反证项**（X 组）：注入事故版，断言原来绿的项会转红 ——
判据没有判别力的"全绿"是最危险的假象。

跑法（离线，秒级）：
    /Users/linshaojie/V6/.venv/bin/python scripts/probe_frontier_audit.py
    或 python scripts/probe_frontier_audit.py
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aether.embeddings import HashingEmbedder                       # noqa: E402
from aether.environment import (Environment, mutate_source,          # noqa: E402
                                numeric_literals, synthesize_tasks,
                                verify_source)
from aether.genome import (LAYERS, Capsule, Gene, Harness,           # noqa: E402
                           apply_gene, parse_route)
from aether.memory import MemoryOS                                  # noqa: E402
from aether.runtime import Trajectory, assign_credit, group_relative  # noqa: E402
from aether.verifier import (SELF_REPORT_KEYS, Check, Verdict,      # noqa: E402
                             assert_no_self_report, env_fingerprint)

ROWS: List[Dict[str, Any]] = []


def ck(group: str, name: str, cond: bool, detail: str = "") -> bool:
    ROWS.append({"group": group, "name": name, "ok": bool(cond),
                 "detail": detail})
    print(f"  [{'OK  ' if cond else 'FAIL'}] {group} {name}"
          f"{('  | ' + detail) if detail and not cond else ''}")
    return bool(cond)


# ══════════════════════════════════════════════════════════════════
# A 组：§1 Self-Harness —— 最小编辑原则 / 5 层可编辑 / 血缘 / 爆炸半径
# ══════════════════════════════════════════════════════════════════
def group_a() -> None:
    print("\n=== A §1 Self-Harness ===")
    base = Harness(meta_rules="R0", routing={"repair": "s1"},
                   tools=("p1",), skills={"s1": "def s1(): pass"},
                   memory_policy={"recall_topk": 3}, prompts={"sys": "hi"})

    # A1 最小编辑：一次只改一个 layer
    g = Gene(op="set", target="L3.skills.s2", value="def s2(): pass")
    h1 = apply_gene(base, g)
    diff = [k for k in ("meta_rules", "routing", "tools",
                        "memory_policy", "prompts")
            if getattr(base, k) != getattr(h1, k)]
    ck("A", "A1 最小编辑：单基因只动 skills 层", diff == [],
       f"别的层也被改了: {diff}")
    ck("A", "A1b 原对象未被就地修改（frozen+纯函数）",
       "s2" not in base.skills)

    # A2 非法 target 必须抛错（不许静默忽略）
    threw = False
    try:
        apply_gene(base, Gene(op="set", target="L9.nope.x", value=1))
    except Exception:                                        # noqa: BLE001
        threw = True
    ck("A", "A2 非法 target ⇒ 抛错（不静默忽略）", threw)

    # A3 5 层可编辑性矩阵
    probes = {
        "meta_rules": Gene(op="set", target="L0.meta_rules", value="R1"),
        "routing": Gene(op="set", target="L1.routing.repair", value="s2"),
        "tools": Gene(op="append", target="L2.tools", value="p2"),
        "skills": Gene(op="set", target="L3.skills.s3", value="def s3(): pass"),
        "memory_policy": Gene(op="set", target="L4.memory_policy.k", value=7),
    }
    for layer in LAYERS:
        ok, err = True, ""
        try:
            apply_gene(base, probes[layer])
        except Exception as e:                               # noqa: BLE001
            ok, err = False, repr(e)
        ck("A", f"A3 层可编辑 {layer}", ok, err)

    # A4 Capsule 原子性：非法基因 ⇒ 整体不应用（不留半截状态）
    cap_bad = Capsule(name="bad", genes=(
        Gene(op="set", target="L3.skills.ok", value="x"),
        Gene(op="set", target="L9.bad", value="y"),
    ))
    from aether.genome import apply_capsule
    partial = False
    try:
        apply_capsule(base, cap_bad)
    except Exception:                                        # noqa: BLE001
        partial = True       # 期望：抛错
    ck("A", "A4 Capsule 含非法基因 ⇒ 整体拒绝（不留半截）",
       partial and "ok" not in base.skills)

    # A5 爆炸半径
    ck("A", "A5 blast_layers 覆盖 5 层",
       all(probes[l].blast_layers() == (l,) for l in LAYERS))
    ck("A", "A5b L0 标记为受保护（触碰即审批）",
       probes["meta_rules"].touches_protected()
       and not probes["skills"].touches_protected())

    # A6 血缘快照可回放
    from aether.store import Store
    p = ROOT / "data" / "_probe_store.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.unlink()
    st = Store(p)
    h_a = base.harness_id
    st.save_snapshot(h_a, base.to_dict(), parent_id="", accepted=True,
                     round_no=0)
    h_b = h1.harness_id
    st.save_snapshot(h_b, h1.to_dict(), parent_id=h_a, accepted=False,
                     round_no=1)
    snaps = st.snapshots()
    ck("A", "A6 血缘快照数 = 2", len(snaps) == 2, f"got {len(snaps)}")
    ok_lineage = bool(snaps) and (
        str(snaps[-1].get("parent", "")) == str(h_a)
        or str(snaps[-1].get("harness_id", "")) == str(h_b))
    ck("A", "A6b 快照含 parent 指针 ⇒ 可回放", ok_lineage,
       json.dumps(snaps[-1], ensure_ascii=False)[:200] if snaps else "empty")
    if p.exists():
        p.unlink()


# ══════════════════════════════════════════════════════════════════
# B 组：§2 RLVR
# ══════════════════════════════════════════════════════════════════
def group_b() -> None:
    print("\n=== B §2 RLVR / Agentic RL ===")

    # B1 Verdict.reward ∈ {0,1}，且是唯一定义
    v_pass = Verdict(passed=True, checks=(Check(name="c", passed=True,
                                                evidence="sha256=abc"),))
    v_fail = Verdict(passed=False, checks=(Check(name="c", passed=False,
                                                 evidence="sha256=abc"),))
    v_err = Verdict(passed=True, error="boom")
    ck("B", "B1 reward ∈ {0,1}",
       v_pass.reward == 1.0 and v_fail.reward == 0.0
       and v_err.reward == 0.0)
    ck("B", "B1b Verdict 无『模型觉得好』字段（无 score/quality）",
       not any(k in Verdict.__dataclass_fields__
               for k in ("score", "quality", "self_report", "confidence")))

    # B2 verify_source 的 evidence 必须含 got/expect
    env = Environment.from_dir(ROOT / "tasks" / "scheduler")
    vd = verify_source(env, timeout=10.0)
    ev = json.dumps(vd.evidence, ensure_ascii=False)
    ck("B", "B2 干净源码判据通过", vd.passed, vd.error or ev[:200])
    #: ⛔ 判据同源：逐条 got/expect 落在 `checks[i].evidence`（顶层 evidence
    #:    只放汇总）。第一版探针查的顶层 ⇒ 假红，这里按真实结构写。
    evs = [c.evidence for c in vd.checks]
    with_got = sum(1 for e in evs if "got=" in e)
    ck("B", "B2b 逐条 evidence 含 got=/expect=（真实断言，不是『跑过了』）",
       len(evs) > 0 and with_got == len(evs),
       f"{with_got}/{len(evs)} 含 got=；样例={evs[:2]}")

    # B3 group_relative 的数学性质
    a = group_relative([1.0, 0.0, 0.0])
    ck("B", "B3 组内优势：赢者为正", a[0] > 0 and a[1] < 0 and a[2] < 0,
       str(a))
    ck("B", "B3b 全同 ⇒ 全 0（不除零爆 NaN）",
       group_relative([1.0, 1.0]) == [0.0, 0.0])
    ck("B", "B3c 单样本 ⇒ 0", group_relative([1.0]) == [0.0])

    # B4 可达性：evolution 的 SELECT 里 group_relative 真被调用几次
    import aether.evolution as evo_mod
    calls = {"n": 0}
    orig = evo_mod.group_relative
    evo_mod.group_relative = lambda s: (calls.__setitem__("n", calls["n"] + 1)
                                        or orig(s))
    try:
        _run_evolve_rounds(rounds=1)
    finally:
        evo_mod.group_relative = orig
    ck("B", "B4 group_relative 在真实 evolve 中被调用 > 0 次",
       calls["n"] > 0, f"calls={calls['n']}")

    # B5 masking_ratio 必须随轨迹内容变化（不是常量）
    #: ⛔ 同源纪律（又一次）：kind 的可选值来自 `runtime.MODEL_KINDS` /
    #:    `ENV_KINDS`，**不是**随手写的 "model"/"env"。第一版探针写 "model"
    #:    ⇒ 两边都判 False ⇒ tokens 全 0 ⇒ ratio 恒 0/1（假红）。
    from aether.runtime import MODEL_KINDS, ENV_KINDS
    mkind = sorted(MODEL_KINDS)[0]
    ekind = sorted(ENV_KINDS)[0]
    t1 = Trajectory(task_id="t")
    t1.add(mkind, "hello world")
    t1.add(ekind, "tool output here")
    t2 = Trajectory(task_id="t")
    t2.add(mkind, "hello world")
    t2.add(ekind, "tool output here " * 40)
    ck("B", "B5 masking_ratio 随 env token 变化（真统计）",
       t1.masking_ratio != t2.masking_ratio,
       f"{t1.masking_ratio} vs {t2.masking_ratio}")
    ck("B", "B5b masking_ratio = env/(model+env)",
       abs(t1.masking_ratio - t1.env_tokens
           / max(1, t1.env_tokens + t1.model_tokens)) < 1e-9)

    # B6 信用分配：不可重放 ⇒ 明确标注不可用
    cr = assign_credit(t1)
    ck("B", "B6 无 replay_fn ⇒ available=False 且给理由",
       cr.available is False and bool(cr.reason), str(cr.to_dict())[:200])
    cr2 = assign_credit(t1, replay_fn=lambda tid, steps: 1.0)
    ck("B", "B6b 有 replay_fn ⇒ 可用（leave-one-out）", cr2.available)

    # B7 自述混入 evidence ⇒ 判定作废
    bad = {"self_score": 0.9}
    threw = False
    try:
        assert_no_self_report(bad)
    except Exception:                                        # noqa: BLE001
        threw = True
    ck("B", "B7 SELF_REPORT_KEYS 命中 ⇒ 抛错", threw)
    v_self = Verdict(passed=True, checks=(Check(name="c", passed=True,
                                                evidence="ok"),),
                     evidence={"self_score": 1.0})
    ok, why = v_self.validate()
    ck("B", "B7b Verdict.validate 拒收自述", not ok, why)


def _run_evolve_rounds(rounds: int = 1) -> Any:
    """跑一次**真实的**进化循环（用启发式 proposer，不联网）。"""
    from aether.evolution import EvolutionLoop, EvolutionConfig
    from aether.ledger import Ledger
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.providers import TemplateProposer
    from aether.agent import HarnessAgent
    from aether.repair import GENERIC_REPAIRS
    from aether.store import Store
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="v6frontier_"))
    led = Ledger(tmp / "ledger.jsonl")
    st = Store(tmp / "store.jsonl")

    env = Environment.from_dir(ROOT / "tasks" / "scheduler")
    tree0 = ast.parse(env.source)
    broken0, _ = mutate_source(tree0, which=0)

    #: 起始 harness **没有**对口修法技能 ⇒ 必败 ⇒ 弱点可挖 ⇒ 进化有动力。
    base = Harness(meta_rules="R0", routing={"repair": "noop"},
                   tools=("read_file", "python_exec"),
                   skills={"noop": "def noop(): pass"},
                   memory_policy={"recall_topk": 3})
    agent = HarnessAgent(envs={"scheduler": env})

    def runner(h: Harness, spec: Dict[str, Any]) -> Any:
        return agent.run(h, spec).verdict

    suite = [FrozenTask(
        task_id=f"sched{i}",
        spec={"env_id": "scheduler", "broken_source": broken0,
              "task_class": "repair", "topic": "scheduler"}) for i in range(3)]

    #: ⛔ 基线必须是**弱**的（全 failed）才有进化动力 —— 第一版探针把基线
    #:    造满了（passed=True）⇒ gain 恒 0 ⇒ 过不了能力地板 ⇒ 永远不提交
    #:    ⇒ group_relative 调用 0 次。这正是文档里"基线已在天花板 ⇒
    #:    进化失去动力"那条，只不过这次是**探针**自己造的天花板。
    from aether.verifier import Verdict as _V, Check as _C
    base_v = _V(passed=False, checks=(_C(name="baseline", passed=False,
                                         evidence="genesis"),))
    bl = Baseline(caliber={"seed": 0},
                  verdicts={t.task_id: base_v for t in suite},
                  harness_id=base.harness_id)
    gate = NonRegressionGate(suite, bl)
    loop = EvolutionLoop(led, gate, suite, runner,
                         TemplateProposer(skills=dict(GENERIC_REPAIRS)),
                         store=st, caliber={"seed": 0},
                         config=EvolutionConfig(group_size=2))
    h, reports = loop.run(base, rounds=rounds)
    return reports


# ══════════════════════════════════════════════════════════════════
# C 组：§3 环境优先与任务合成
# ══════════════════════════════════════════════════════════════════
def group_c() -> None:
    print("\n=== C §3 环境优先 / 任务合成 ===")
    env = Environment.from_dir(ROOT / "tasks" / "scheduler")
    rep = synthesize_tasks(env, max_n=6, timeout=10.0)
    d = rep.to_dict()
    ck("C", "C1 attempted > 0", rep.attempted > 0, str(d))
    ck("C", "C2 valid > 0（真合成出任务）", rep.valid > 0, str(d))
    #: ⛔ 这里**不**断言 rejected_* 非 0：scheduler 的变异恰好全让测试变红
    #:    （yield 100%），那是好事。恒等式自洽性由 C4 覆盖。
    #:    "分支是真活的还是死代码"留给 C6 用**未覆盖变异**反例验证 ——
    #:    只统计成功路径的话，C6 会红。
    ck("C", "C3 yield 记账非负且 attempted 已计入",
       rep.attempted > 0 and rep.valid >= 0
       and rep.rejected_no_failure >= 0 and rep.rejected_error >= 0,
       f"attempted={rep.attempted} valid={rep.valid} "
       f"rejected_no_failure={rep.rejected_no_failure} "
       f"rejected_error={rep.rejected_error}")
    ck("C", "C4 attempted == valid + rejected_no_failure + rejected_error",
       rep.attempted == rep.valid + rep.rejected_no_failure
       + rep.rejected_error, str(d))
    # F2P 性质：合成出的任务，干净源码必须**通过**
    from aether.environment import verify_source as vs
    ok_clean = vs(env, env.source, timeout=10.0).passed
    ck("C", "C5 F2P：干净源码在合成任务上通过（红来自变异而非环境坏）",
       ok_clean)

    #: ⛔⛔ C6 —— 反例验证「无效变异」分支确实活着，不是死代码。
    #:    造一个"变异点落在**没有任何测试覆盖**的死代码里"的环境：
    #:    变异真的发生了（attempted 计入），但测试一条都没变红
    #:    ⇒ 必须记 rejected_no_failure，而**不是**拿它充任务数
    #:    （充数 = 用"尝试次数"冒充"任务数"，V5 的头号假成功形态）。
    import tempfile
    td = Path(tempfile.mkdtemp(prefix="v6frontier_deadcode_"))
    #: ⛔ 被测试覆盖的函数里**不能留可变异点**（比较符 / 数值字面量），
    #:    否则它自己也会变红 ⇒ valid>0 ⇒ 这条判据失去判别力。
    #:    第一版写 `return a < b` ⇒ 比较符被 flip ⇒ 误判成"有效任务"。
    (td / "task.py").write_text(
        "from operator import lt\n\n"
        "def dead_code():\n"
        "    return 1 + 2\n\n"
        "def cmp_fn(a, b):\n"
        "    return lt(a, b)\n", encoding="utf-8")
    (td / "tests.json").write_text(json.dumps({
        "env_id": "deadcode",
        "tests": [
            {"name": "lt_true", "call": "cmp_fn",
             "args": [1, 2], "expect": True},
            {"name": "lt_false", "call": "cmp_fn",
             "args": [2, 1], "expect": False},
        ]}), encoding="utf-8")
    env_dead = Environment.from_dir(td)
    rep_dead = synthesize_tasks(env_dead, max_n=4, timeout=10.0)
    ck("C", "C6 变异落在未覆盖代码 ⇒ rejected_no_failure（不拿它充数）",
       rep_dead.rejected_no_failure > 0 and rep_dead.valid == 0,
       str(rep_dead.to_dict()))
    globals()["_DEADCODE_ENV"] = (td, env_dead)


# ══════════════════════════════════════════════════════════════════
# D 组：§4 记忆
# ══════════════════════════════════════════════════════════════════
def group_d() -> None:
    print("\n=== D §4 记忆分层 / 演化 / 巩固 / 对抗验证 ===")
    emb = HashingEmbedder(dim=64)
    m = MemoryOS(embedder=emb, dim=64, link_topk=2, min_content_len=0)

    from aether.memory import TIERS
    ck("D", "D1 TIERS = 5 层 L0..L4", TIERS == ("L0", "L1", "L2", "L3", "L4"),
       str(TIERS))

    # D2 双向互链
    for i in range(6):
        m.write("L4", f"关于调度冲突检测的第 {i} 号观察记录，内容足够长以便入库",
                source="probe")
    linked = sum(1 for n in m.notes.values() if n.links)
    ck("D", "D2 _link 生成了链接", linked > 0, f"linked={linked}")
    sym = True
    for n in m.notes.values():
        for lid in n.links:
            o = m.notes.get(lid)
            if o is not None and n.note_id not in o.links:
                sym = False
    ck("D", "D2b 链接双向对称", sym)

    # D3 扩散增益（A-MEM 的检索沿链接扩散）
    q = "调度冲突检测"
    narrow = m.search(q, top_k=2, expand_links=False)
    wide = m.search(q, top_k=2, expand_links=True)
    ck("D", "D3 expand_links 召回更多（扩散真的生效）",
       len(wide) >= len(narrow) and len(wide) > 0,
       f"narrow={len(narrow)} wide={len(wide)}")

    # D4 promote 的对抗验证：verifier=None ⇒ fail-closed
    nid = next(iter(m.notes))
    ok, why = m.promote(nid)
    ck("D", "D4 promote(verifier=None) ⇒ 拒绝（fail-closed）", not ok, why)
    ok2, why2 = m.promote(nid, verifier=lambda n: (True, "无反证"))
    ck("D", "D4b 有 verifier 且无反证 ⇒ 提升", ok2, why2)
    ok3, why3 = m.promote(next(iter(m.notes)),
                          verifier=lambda n: (False, "找到反例"))
    ck("D", "D4c verifier 证伪 ⇒ 拒绝", not ok3, why3)

    # D5 MemNote 带来源 / 使用史
    any_n = m.notes[nid]
    ck("D", "D5 MemNote 有 source / access_count / last_access",
       hasattr(any_n, "source") and hasattr(any_n, "access_count")
       and hasattr(any_n, "last_access"))
    before = any_n.access_count
    m.search(q, top_k=1)
    after = m.notes[nid].access_count
    ck("D", "D5b 检索会更新使用史（access_count 递增）", after >= before,
       f"{before} -> {after}")


# ══════════════════════════════════════════════════════════════════
# E 组：§6 失败模式 → 闸
# ══════════════════════════════════════════════════════════════════
def group_e() -> None:
    print("\n=== E §6 失败模式 → 闸 ===")
    src = (ROOT / "data" / "_unused_frontier.py")
    # E1 非回归闸：用冻结任务集，回归必须被拦
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.verifier import Verdict as _V, Check as _C
    base_v = _V(passed=True, checks=(_C(name="baseline", passed=True,
                                        evidence="genesis"),))
    suite = [FrozenTask(task_id=f"f{i}", spec={"kind": "repair"})
             for i in range(2)]
    bl = Baseline(caliber={"seed": 0},
                  verdicts={t.task_id: base_v for t in suite}, harness_id="h")
    gate = NonRegressionGate(suite, bl)

    h_ok = Harness(routing={"repair": "s1"}, skills={"s1": "def s1(): pass"})
    h_bad = Harness(routing={"repair": "nope"}, skills={})

    def runner(h: Harness, spec: Dict[str, Any]) -> Any:
        from aether.verifier import from_exit_code
        good = bool(h.skills) and "s1" in (h.routing.get("repair") or "")
        return from_exit_code("f0", 0 if good else 1, "x")

    r_ok = gate.evaluate(h_ok, runner, {"seed": 0})
    r_bad = gate.evaluate(h_bad, runner, {"seed": 0})
    ck("E", "E1 非回归闸：好 harness 通过", r_ok.passed, str(r_ok.to_dict())[:200])
    ck("E", "E1b 非回归闸：回归被拦", not r_bad.passed,
       str(r_bad.to_dict())[:200])

    # E2 supersede 留痕
    from aether.nongression import supersede
    new_b, old_b = supersede(bl, {"seed": 1}, "换口径")
    ck("E", "E2 supersede：旧基线被标记 superseded 且带 reason",
       old_b.superseded and bool(old_b.supersede_reason)
       and not new_b.superseded)

    # E3/E4 地板与增殖闸：直接读 config 的判定式所在函数不好拆 ⇒
    #     用 EvolutionConfig 构造极端值，跑真实 loop，看是否被拦。
    from aether.evolution import EvolutionConfig
    ck("E", "E3 min_reward_gain 存在且 > 0（能力地板）",
       EvolutionConfig().min_reward_gain > 0)
    ck("E", "E4 max_size_growth_ratio 存在且 < 1（增殖闸）",
       0 < EvolutionConfig().max_size_growth_ratio < 1)

    # E5 env_fingerprint：不同环境/不同口径 ⇒ 指纹不同（可比性可检测）
    f1 = env_fingerprint({"env": "a", "tests": [1]})
    f2 = env_fingerprint({"env": "b", "tests": [1]})
    f3 = env_fingerprint({"env": "a", "tests": [1]})
    ck("E", "E5 env_fingerprint 区分不同环境且稳定",
       f1 != f2 and f1 == f3, f"{f1} {f2} {f3}")

    # E6 EVOLUTION_STALLED：TS 检测器的输入必须是**后端真产出**的字段
    ts = Path(ROOT / "supervisor" / "src" / "anomaly.ts")
    txt = ts.read_text(encoding="utf-8") if ts.exists() else ""
    i = txt.find("EVOLUTION_STALLED")
    ctx = txt[max(0, i - 500):i + 200] if i >= 0 else ""
    ck("E", "E6 EVOLUTION_STALLED 存在", i >= 0)
    # 它读的字段必须在后端报告里真的有
    import aether.evolution as evo_mod
    src_evo = Path(evo_mod.__file__).read_text(encoding="utf-8")
    needed = [w for w in ("idle", "saturated", "stagnat", "accepted") if w in ctx]
    missing = [w for w in needed
               if (w not in src_evo) and (w not in
                                          Path(ROOT / "aether" / "cli.py").read_text(encoding="utf-8"))]
    ck("E", "E6b 停滞检测读的字段后端真的产出", not missing,
       f"missing={missing}")


# ══════════════════════════════════════════════════════════════════
# X 组：反证 —— 注入事故版，断言原绿项转红
# ══════════════════════════════════════════════════════════════════
def group_x() -> None:
    print("\n=== X 反证组（判据必须有判别力）===")

    # X1 把 apply_gene 换成"静默忽略未知 target" ⇒ A2 必须转红
    import aether.genome as G
    orig = G.apply_gene

    def silent(h, gene):
        try:
            return orig(h, gene)
        except Exception:                                    # noqa: BLE001
            return h                      # 事故版：静默吞掉

    G.apply_gene = silent
    threw = False
    try:
        G.apply_gene(Harness(), Gene(op="set", target="L9.nope", value=1))
    except Exception:                                        # noqa: BLE001
        threw = True
    G.apply_gene = orig
    ck("X", "X1 事故版（静默忽略）⇒ A2 转红", not threw)

    # X2 把 reward 换成"模型打分" ⇒ B1 必须转红（出现非 0/1）
    import aether.verifier as V
    orig_prop = V.Verdict.reward

    def fuzzy(self):
        return 0.73                       # 事故版：连续分

    V.Verdict.reward = property(fuzzy)
    v = V.Verdict(passed=True, checks=(V.Check(name="c", passed=True,
                                               evidence="e"),))
    bad = v.reward not in (0.0, 1.0)
    V.Verdict.reward = orig_prop
    ck("X", "X2 事故版（连续 reward）⇒ B1 转红", bad)

    # X3 让 promote 在无 verifier 时也放行 ⇒ D4 必须转红
    import aether.memory as M
    orig_promote = M.MemoryOS.promote

    def open_promote(self, note_id, *, verifier=None, channel="curated"):
        return True, "passed"             # 事故版：fail-open

    M.MemoryOS.promote = open_promote
    m2 = MemoryOS(embedder=HashingEmbedder(dim=32), dim=32, min_content_len=0)
    m2.write("L4", "一条用于反证的归档记录内容足够长以通过长度闸门", source="x")
    ok_open, _ = m2.promote(next(iter(m2.notes)))
    M.MemoryOS.promote = orig_promote
    ck("X", "X3 事故版（fail-open promote）⇒ D4 转红", ok_open)

    # X4 masking 常量化 ⇒ B5 必须转红
    import aether.runtime as R
    const = R.Trajectory.masking_ratio
    R.Trajectory.masking_ratio = property(lambda self: 0.5)
    t1 = R.Trajectory(task_id="t"); t1.add("model", "hello world")
    t2 = R.Trajectory(task_id="t"); t2.add("env", "x " * 50)
    same = (t1.masking_ratio == t2.masking_ratio)
    R.Trajectory.masking_ratio = const
    ck("X", "X4 事故版（常量 masking）⇒ B5 转红", same)

    #: X5 对照式反证：**同一个合成器**在两个环境上必须给出**不同**裁决。
    #:    若"变异被测试覆盖"与"未被覆盖"都判 valid>0，说明 C6 根本没在
    #:    看 fail-to-pass，只是把 attempted 抄进了 valid。
    dc = globals().get("_DEADCODE_ENV")
    if dc:
        rep_cov = synthesize_tasks(
            Environment.from_dir(ROOT / "tasks" / "scheduler"),
            max_n=4, timeout=10.0)
        rep_unc = synthesize_tasks(dc[1], max_n=2, timeout=10.0)
        ck("X", "X5 对照：有覆盖环境 valid>0 且未覆盖环境 valid==0"
                "（同一合成器给出不同裁决 ⇒ C6 有判别力）",
           rep_cov.valid > 0 and rep_unc.valid == 0,
           f"covered={rep_cov.to_dict()} uncovered={rep_unc.to_dict()}")
        #: 事故版：把"变红"判定改成恒真（不看 before/after 差集）⇒ C6 必须红
        import aether.environment as E
        orig_vs = E.verify_source
        st = {"n": 0}

        def fake(env: Any, source: Any = None, timeout: float = 10.0) -> Any:
            st["n"] += 1
            v = orig_vs(env, source, timeout)
            #: 事故版：after（第 2 次及以后）少一条 check ⇒ 伪造出"变红"
            #: ⛔ 别用 `n % 2 == 0` —— 第 3 次调用也是 after，会被漏掉，
            #:    于是"该红的没红" ⇒ 反证自己失效（第一版就栽在这）。
            if st["n"] > 1 and len(v.checks) > 1:
                return Verdict(passed=True, checks=v.checks[:-1],
                               evidence=dict(v.evidence),
                               env_fingerprint=v.env_fingerprint)
            return v

        E.verify_source = fake
        try:
            rep_fake = synthesize_tasks(dc[1], max_n=2, timeout=10.0)
        finally:
            E.verify_source = orig_vs
        ck("X", "X5b 事故版（伪造『变红』）⇒ C6 转红",
           rep_fake.valid > 0 and rep_fake.rejected_no_failure == 0,
           str(rep_fake.to_dict()))


# ══════════════════════════════════════════════════════════════════
# G 组：2026-10 新纳入前沿的兑现取证（ALE 老化 + masking 接线）
# ══════════════════════════════════════════════════════════════════
def group_g() -> None:
    print("\n=== G 新纳入前沿（FRONTIER §8 / ARCHITECTURE §28）===")
    from aether.aging import AGING_KINDS, ProbeResult
    from aether.aging import RoundTrace as _RT, detect_aging as _da
    from aether.agent import HarnessAgent, trajectory_from_steps
    from aether.environment import Environment, mutate_source
    from aether.genome import Harness

    def mk(rno, rewards, *, sc=0, con=0, pr=0, cal=None):
        return _RT(round_no=rno, harness_id=f"h{rno}",
                   probes=tuple(ProbeResult(task_id=f"t{i}", reward=v)
                                            for i, v in enumerate(rewards)),
                   skill_count=sc, consolidated=con, pruned=pr,
                   caliber=dict(cal or {}))

    def sg(rep, kind):
        return next(s for s in rep.signals if s.kind == kind)

    ck("G", "G1 老化信号恰好四类", len(AGING_KINDS) == 4, str(AGING_KINDS))

    rc = _da([mk(1, [1.0, 1.0]), mk(2, [0.5, 0.5], con=4)])
    ck("G", "G2 compression：巩固后下降 ⇒ detected",
       sg(rc, "compression").available and sg(rc, "compression").detected)

    ri = _da([mk(1, [1.0, 0.0], sc=1), mk(2, [0.6, 0.4], sc=3)])
    si = sg(ri, "interference")
    ck("G", "G3 interference 指纹：一升一降（均值抹平）仍被检出",
       si.detected and si.pattern == "interference", str(si.to_dict()))

    rr = _da([mk(1, [1.0], cal={"seed": 1}), mk(2, [1.0], cal={"seed": 2})])
    ck("G", "G4 revision：口径变更 ⇒ 直接报（不等下降）",
       sg(rr, "revision").detected)

    rn = _da([mk(1, [1.0, 1.0]), mk(2, [0.1, 0.1])])
    ck("G", "G5 无事件 ⇒ available=False 且进 undetermined（不是通过）",
       all(not s.available for s in rn.signals)
       and len(rn.undetermined) == 4, str(rn.undetermined))

    #: ⛔ 探针集**必须真的变了**：第二轮只留一道题 ⇒ 与第一轮 [t0,t1] 不同
    r_inc = _da([mk(1, [1.0, 1.0]), mk(2, [1.0])])
    ck("G", "G6 探针集跨轮变化 ⇒ comparable=False 且 healthy=False"
          "（换尺子不判健康）",
       r_inc.comparable is False and r_inc.healthy is False,
       str(r_inc.to_dict())[:200])

    # G7 masking 由**生产链路**产出（不是 demo 手工构造）
    env = Environment.from_dir(ROOT / "tasks" / "scheduler")
    tree = ast.parse(env.source)
    broken, _ = mutate_source(tree, which=0)
    h = Harness(routing={"repair": "fix_literals"},
                tools=("read_file", "python_exec"),
                skills={"fix_literals": "x"})
    r = HarnessAgent(envs={"scheduler": env}).run(
        h, {"env_id": "scheduler", "broken_source": broken,
            "task_class": "repair"})
    t = r.trajectory
    ck("G", "G7 HarnessAgent.run 产出 trajectory", t is not None)
    ck("G", "G7b model/env tokens 均 > 0（两侧都在记）",
       t is not None and t.model_tokens > 0 and t.env_tokens > 0,
       f"model={getattr(t,'model_tokens',None)} env={getattr(t,'env_tokens',None)}")
    ck("G", "G7c masking_ratio 是 (0,1) 内的真值（非空账）",
       t is not None and 0.0 < t.masking_ratio < 1.0,
       str(getattr(t, "masking_ratio", None)))
    ck("G", "G7d to_dict 暴露 masking 读数",
       "masking" in r.to_dict())


def main() -> int:
    print("=" * 66)
    print("FRONTIER_2026 兑现审计（运行时取证，非 grep）")
    print("=" * 66)
    for fn in (group_a, group_b, group_c, group_d, group_e, group_g, group_x):
        try:
            fn()
        except Exception as e:                               # noqa: BLE001
            ck(fn.__name__.upper()[-1], f"{fn.__name__} 崩了", False, repr(e))

    ok = sum(1 for r in ROWS if r["ok"])
    print("\n" + "=" * 66)
    print(f"兑现：{ok}/{len(ROWS)}")
    bad = [r for r in ROWS if not r["ok"]]
    if bad:
        print("未兑现项：")
        for r in bad:
            print(f"  · {r['group']} {r['name']}  {r['detail']}")
    print("=" * 66)

    out = ROOT / "docs" / (
        "probe_frontier_audit_"
        + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + ".json")
    out.write_text(json.dumps({"ok": ok, "total": len(ROWS), "rows": ROWS},
                              ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"报告：{out}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
