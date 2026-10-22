#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约 · 「没被闸看过」不许与「被闸拦了」长得一样（r65）。

═══ 起因是生产路径上的一次实测（不是设想）═══

同一个数据根、同一个 `--proposer llm`（真模型 Qwen3.5-4B），三次同参数运行
**逐字可复现**，其中第二名的结算是：

    add_textscore_skill  adv=+1.5882  reason="overfit: …"   ← 闯过闸，被拦
    tune_memory_recall   adv=-0.8552  reason="no_advantage" ← 准入淘汰
    fix_literals_skill   adv=+0.1222  reason=""             ← ？？

第三个是正例：它过了准入闸、优势为正，只是排第二。默认
`gate_tries=1` ⇒ 7.5 只让 `eligible[:1]` 去闯泛化闸 ⇒ **它从没被评估过**，
于是它和被拦的、被淘汰的共享同一个 `reason=""` + `accepted=False`（第 25 条：
一个值两种含义）。

⇒ 这不是"可读性"问题：同一数据根加 `--gate-tries 2`，它判 `generalize`
**并被提交**（`harness_id` 与这次完全相同）。也就是说空 reason 盖住的是
一个**真实存在的提交**，而观测侧读到的是"这条也试过了、也被拦了"。

═══ 本契约守什么 ═══

1. 每个没提交的候选都必须说得出原因（空 reason 是非法状态）；
2. 「没被评估」必须自报 `not_probed`，并写明**怎么让它上**（出路要真有：
   本契约 J4 真跑一次 `--gate-tries` 等价路径证明它不是文案）；
3. 该修法只做"改读数不改判据"——它不许新增裁决名，不许污染 VERDICTS，
   也不许改动任何一个已判刑的裁决（J5 / J6）。

⛔ 夹具要点：排**第一**的候选必须是 overfit（污染泛化：发展集涨、held-out
不涨），排**第二**的才是真解 —— 这就是实测那一次的形状。顺序不能反，反了
`gate_tries=1` 会直接提交第一名，测不到"第二名被默默丢掉"这件事。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILED: list = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'}" + (f" {name}" if ok else f" **{name}**")
          + (f"  —— {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def main() -> None:
    from aether.evolution import EvolutionConfig, EvolutionLoop
    from aether.generalization import VERDICTS, GeneralizationGate, make_heldout
    from aether.genome import Capsule, Gene, Harness as H, apply_capsule
    from aether.ledger import Ledger
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.verifier import Check, Verdict

    DEV, HO = "A", "B"

    def _ok(tid):
        return Verdict(passed=True, checks=(Check(tid, True, "got=1"),),
                       evidence={"env": tid})

    def _bad(tid):
        return Verdict(passed=False, checks=(Check(tid, False, "got=0"),),
                       evidence={"env": tid})

    def runner(hh, spec):
        if spec.get("frozen"):
            return _ok("frozen")
        env = spec.get("env", "")
        #: `dev_only` 只在发展集环境起作用（= 实测模型提的 `patch:textscore`）；
        #: `genuine` 两个环境都起作用（= 实测的 `fix_literals_skill`）。
        if "genuine" in hh.skills:
            return _ok(env)
        if "dev_only" in hh.skills and env == DEV:
            return _ok(env)
        return _bad(env)

    def cap(name, skill):
        return Capsule(name=name, rationale="契约夹具",
                       genes=[Gene(op="set", target=f"L3.skills.{skill}",
                                   value="body", rationale="夹具")])

    #: ⛔ 顺序即事实：排第一的是 overfit，第二的才是真解。
    CAPS = [cap("overfit_first", "dev_only"),
            cap("genuine_second", "genuine"),
            cap("floor_filler", "nope")]

    class FixedProposer:
        def propose(self, weakness, harness, n=3):
            return list(CAPS)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        frozen = [FrozenTask(task_id="frozen::A",
                             spec={"env": DEV, "frozen": True}),
                  FrozenTask(task_id="frozen::B",
                             spec={"env": HO, "frozen": True})]
        dev = [FrozenTask(task_id=f"dev{i}", spec={"env": DEV})
               for i in range(3)]
        ho_tasks = [FrozenTask(task_id=f"ho{i}", spec={"env": HO})
                    for i in range(3)]
        cal = {"seed": 0, "timeout": 10.0, "split": "frozen"}

        def mk(name, **cfg):
            led = Ledger(td / f"{name}.jsonl")
            base = Baseline(
                caliber=cal,
                verdicts={t.task_id: runner(H(), t.spec) for t in frozen},
                harness_id="seed")
            #: ⛔ 必须是 `GeneralizationGate`（不是裸 `HeldOutSuite`）：
            #:    循环读 `heldout.env_pair`，协议不同。
            gate_ho = GeneralizationGate(
                make_heldout(HO, ho_tasks),
                dev_task_ids=[t.task_id for t in dev], base_at_ceiling=1.0)
            loop = EvolutionLoop(
                led, NonRegressionGate(frozen, base), dev, runner,
                FixedProposer(), caliber=cal, heldout=gate_ho,
                config=EvolutionConfig(group_size=3, cleanup_enabled=False,
                                       **cfg))
            return loop, led

        # ── J1–J3：默认 tries=1（= 与历史逐字同口径）───────────────
        loop, led = mk("t1")
        rep = loop.run_round(H(), round_no=1)
        cands = list(rep.candidates)
        by_name = {c.capsule_name: c for c in cands}

        _pos = [c for c in cands if c.advantage is not None
                and c.advantage > 0 and c.gate_passed]
        ck("J1 夹具成形状：≥2 个候选昂首通过准入"
           "（否则『排第二』这件事根本不存在）",
           len(_pos) >= 2,
           f"{[(c.capsule_name, round(c.advantage, 3), c.gate_passed) for c in cands]}")

        over = by_name.get("overfit_first")
        ck("J2 排第一的是 overfit（夹具形状正确：它按设计被拦）",
           over is not None and "overfit" in (over.reason or ""),
           f"reason={over.reason if over else None}")

        second = by_name.get("genuine_second")
        ck("J3 ⛔⛔ 排第二的真解**没被评估**（generalization 为空）"
           " —— 这是问题的前提，不是要修的东西",
           second is not None and not (second.generalization or {}),
           f"generalization={second.generalization if second else None}")

        ck("J4 ⛔⛔ 没被评估 ⇒ 必须自报 `not_probed`（不许留空）",
           second is not None and "not_probed" in (second.reason or ""),
           f"reason={second.reason!r}")

        ck("J5 ⛔ 每一个没提交的候选都必须说得清为什么（空 reason 非法）",
           all((c.reason or "").strip() for c in cands if not c.accepted),
           f"{[(c.capsule_name, c.reason) for c in cands if not c.accepted]}")

        ck("J6 ⛔ 文案里的出路必须是**真开关**（写了就得能用）",
           second is not None and "--gate-tries" in (second.reason or ""),
           f"reason={second.reason!r}")

        ck("J7 行为不变：tries=1 时仍不提交（改的是读数，不是判据）",
           rep.committed is None and not any(c.accepted for c in cands),
           f"committed={rep.committed} "
           f"accepted={[c.capsule_name for c in cands if c.accepted]}")

        # ── J8–J10：反证 —— 出路真跑一遍 ────────────────────────
        loop2, led2 = mk("t2", gate_tries=2)
        rep2 = loop2.run_round(H(), round_no=1)
        c2 = {c.capsule_name: c for c in rep2.candidates}
        ck("J8 ⛔⛔ 反证：`gate_tries=2` ⇒ 排第二的真解真的会被提交"
           "（证明 J4/J6 指向的出路存在，不是文案）",
           rep2.committed is not None
           and bool(c2.get("genuine_second") and
                    c2["genuine_second"].accepted),
           f"committed={rep2.committed} "
           f"reasons={[(c.capsule_name, c.reason) for c in rep2.candidates]}")

        ck("J9 反证：所有候选都被评估 ⇒ `not_probed` 一个都不该出现"
           "（J4 不是恒真断言）",
           not any("not_probed" in (c.reason or "")
                   for c in rep2.candidates),
           f"{[(c.capsule_name, c.reason) for c in rep2.candidates]}")

        ck("J10 已判刑的裁决不被读数施工覆盖（rank=1 仍写着 overfit）",
           bool(c2.get("overfit_first"))
           and "overfit" in (c2["overfit_first"].reason or ""),
           str(c2.get("overfit_first") and c2["overfit_first"].reason))

        # ── J11：没装 held-out 的运行：文案要跟着变 ─────────────────
        led3 = Ledger(td / "t3.jsonl")
        base3 = Baseline(caliber=cal,
                         verdicts={t.task_id: runner(H(), t.spec)
                                   for t in frozen},
                         harness_id="seed")
        loop3 = EvolutionLoop(
            led3, NonRegressionGate(frozen, base3), dev, runner,
            FixedProposer(), caliber=cal, heldout=None,
            config=EvolutionConfig(group_size=3, cleanup_enabled=False))
        rep3 = loop3.run_round(H(), round_no=1)
        ck("J11 ⛔ 反证：没装 held-out ⇒ 文案讲『没装』，不许套用"
           " tries 的说法（读数不能撒谎）",
           any("held-out" in (c.reason or "") for c in rep3.candidates),
           f"{[(c.capsule_name, c.reason) for c in rep3.candidates]}")

        ck("J12 每一个没提交的候选都仍要说得出原因（heldout=None 路径）",
           all((c.reason or "").strip() for c in rep3.candidates
               if not c.accepted),
           f"{[(c.capsule_name, c.reason) for c in rep3.candidates if not c.accepted]}")

        # ── J13：不新增裁决名 ────────────────────────────────────
        ck("J13 ⛔ `not_probed` 是读数，不是裁决（VERDICTS 封闭集合未被扩）",
           "not_probed" not in set(VERDICTS),
           f"VERDICTS={sorted(VERDICTS)}")
        probes = [e.payload.get("verdict")
                  for e in led.iter_events() if e.kind == "gen.probe"]
        ck("J14 ⛔ 没被评估的候选不许产出 gen.probe（否则死路账会被虚构记录污染）",
           "not_probed" not in {str(v) for v in probes},
           f"probes={probes}")


if __name__ == "__main__":
    main()
    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{FAILED}")
        sys.exit(1)
    print("✅ 全部通过")
