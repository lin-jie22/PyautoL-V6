#!/usr/bin/env python3
"""契约测试：到顶不许空转 + 已知死路不许反复探测。

对应两条**实测**出来的缺口（2026-10-13，10 轮压力跑）：

  I1 饱和空转：r2–r10 全部是 `no_advantage(0.000)`（base 恒 1.0），
     每轮挖同一弱点、提同一候选、得同一结果。但报的是
     `no_candidate_passed` ⇒ 看起来像"努力了没成功"，实际是"已到顶"。
     ⛔ 混在一起会让"空转 9 轮"看起来和"被闸拦 9 次"一模一样。

  I2 死路重提：`add_skill:patch:textscore` 在真实账本上 15 次探测 /
     15 次 overfit / **0 次通过**，仍被反复提议（占掉 55% 探测预算）。

每组都配反证：把修复退掉，判据必须转红。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.evolution import EvolutionConfig, EvolutionLoop          # noqa: E402
from aether.generalization import GeneralizationGate, make_heldout   # noqa: E402
from aether.genome import Capsule, Gene, Harness                     # noqa: E402
from aether.ledger import Ledger                                     # noqa: E402
from aether.nongression import Baseline, FrozenTask, NonRegressionGate  # noqa: E402
from aether.verifier import Check, Verdict                           # noqa: E402

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

FAILS: list = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✅ {name}")
    else:
        FAILS.append(name)
        print(f"  ❌ {name}" + (f"  ← {detail}" if detail else ""))


def _ok(tid: str = "t") -> Verdict:
    return Verdict(passed=True, checks=(Check(tid, True, "got=1"),),
                   evidence={"env": tid})


def _bad(tid: str = "t") -> Verdict:
    return Verdict(passed=False, checks=(Check(tid, False, "got=0"),),
                   evidence={"env": tid})


CAL = {"seed": 0, "timeout": 10.0, "split": "frozen"}


# ══════════════════════════════════════════════════════════════════
# A 组：饱和必须被识别成"到顶"，不是"没成功"
# ══════════════════════════════════════════════════════════════════
def group_a() -> None:
    print("\nA 组：发展集到顶 ⇒ 必须报 saturated（不是 no_candidate_passed）")

    d = Path(tempfile.mkdtemp(prefix="v6_sat_"))
    led = Ledger(d / "ledger.jsonl", session="sess-SAT")

    def base_fails_cand_fails(h, spec):
        """基线失败（⇒ 有弱点可挖），候选也一样失败 ⇒ 没人能超过基线。

        ⛔ 这正是"停滞"形态：有失败证据、有候选、但没有一个赢得过基线。
        ⛔ 不能让基线全绿 —— 全绿时弱点挖掘返回空，轮次在
           `no_weakness_evidence` 就结束了，根本走不到这条判定
           （第一版测试就是这么写的，构造出来的是永假场景）。
        ⛔ 冻结任务必须判**通过**：否则非回归闸判"回归" ⇒ 候选根本进不了
           scored ⇒ 走不到停滞判定（第二版构造又栽在这里）。
        """
        if spec.get("frozen"):
            return _ok("fz")
        return _bad("dev")

    class FixedProposer:
        def __init__(self, caps):
            self.caps = caps

        def propose(self, weakness, harness, n=3):
            return list(self.caps)

    cap = Capsule(name="any", genes=(
        Gene(op="set", target="L3.skills.any", value="x", rationale="t"),))

    dev = [FrozenTask(task_id=f"A::d{i}", spec={"env": "A"}) for i in range(3)]
    frozen = [FrozenTask(task_id="fz", spec={"env": "A", "frozen": True})]
    base = Baseline(caliber=CAL, verdicts={"fz": _ok("fz")}, harness_id="seed")
    ho = GeneralizationGate(
        make_heldout("B", [FrozenTask(task_id="h0",
                                      spec={"env": "B", "heldout": True})]),
        dev_task_ids=[t.task_id for t in dev])

    loop = EvolutionLoop(led, NonRegressionGate(frozen, base), dev,
                         base_fails_cand_fails, FixedProposer([cap]),
                         caliber=CAL, heldout=ho,
                         config=EvolutionConfig(group_size=2,
                                                stop_on_saturation=False))
    rep = loop.run_round(Harness(tools=("read_file", "write_file",
                                        "python_exec")), round_no=1)

    ck("A1 有弱点但无候选能超过基线 ⇒ saturated=True（停滞，不是失败）",
       rep.saturated is True, f"revert_reason={rep.revert_reason}")
    ck("A2 原因里点名 saturated，不是含糊的 no_candidate_passed",
       "saturated" in rep.revert_reason and
       "no_candidate_passed" not in rep.revert_reason,
       rep.revert_reason)
    ck("A3 饱和读数带 dev 通过数/总数（不是只给一句话）",
       (rep.saturation or {}).get("dev_total") == 3
       and isinstance((rep.saturation or {}).get("dev_passed"), int),
       str(rep.saturation))
    ck("A3b 停滞（基线有失败）与到顶（基线全绿）在 reason 里分开写",
       "stagnated" in rep.revert_reason,
       rep.revert_reason)

    evs = Ledger(d / "ledger.jsonl").events()
    kinds = [e.kind for e in evs]
    ck("A4 saturation 进账本（事实源，不只写在内存报告里）",
       "evolution.saturated" in kinds, str(kinds))
    #: held-out 读数：dev 全绿而 held-out 不绿是**真信号**，必须能读出来
    ck("A5 饱和时跑一次 held-out 只读读数（记 saturation.probe 不是 gen.probe）",
       "saturation.probe" in kinds and "gen.probe" not in kinds, str(kinds))

    #: 反证：候选**真的**超过基线时不得报饱和（那是成功路径）
    def cand_wins(h, spec):
        if spec.get("frozen"):
            return _ok("fz")
        return _ok("dev") if "any" in h.skills else _bad("dev")

    led2 = Ledger(d / "l2.jsonl", session="sess-SAT2")
    loop2 = EvolutionLoop(led2, NonRegressionGate(frozen, base), dev, cand_wins,
                          FixedProposer([cap]), caliber=CAL, heldout=ho,
                          config=EvolutionConfig(group_size=2))
    rep2 = loop2.run_round(Harness(tools=("read_file", "write_file",
                                          "python_exec")), round_no=1)
    ck("A6 反证：候选能超过基线 ⇒ 不得报饱和（那是该提交，不是到顶）",
       rep2.saturated is False and rep2.committed is not None,
       f"committed={rep2.committed} reason={rep2.revert_reason}")

    #: 反证：被泛化闸拦下 ⇒ 是失败不是停滞，必须报拦下原因
    def dev_win_ho_lose(h, spec):
        if spec.get("heldout"):
            return _bad("ho")
        if spec.get("frozen"):
            return _ok("fz")
        return _ok("dev") if "any" in h.skills else _bad("dev")

    led4 = Ledger(d / "l4.jsonl", session="sess-SAT4")
    loop4 = EvolutionLoop(led4, NonRegressionGate(frozen, base), dev,
                          dev_win_ho_lose, FixedProposer([cap]), caliber=CAL,
                          heldout=ho, config=EvolutionConfig(group_size=2))
    rep4 = loop4.run_round(Harness(tools=("read_file", "write_file",
                                          "python_exec")), round_no=1)
    ck("A7 反证：被泛化闸拦下 ⇒ 报拦下原因，不是饱和（失败≠停滞）",
       rep4.saturated is False
       and "generalization_gate" in (rep4.revert_reason or ""),
       f"reason={rep4.revert_reason}")


# ══════════════════════════════════════════════════════════════════
# B 组：到顶即停（不许把空转记成努力）
# ══════════════════════════════════════════════════════════════════
def group_b() -> None:
    print("\nB 组：到顶即停 + 停得有解释")

    d = Path(tempfile.mkdtemp(prefix="v6_stop_"))
    led = Ledger(d / "ledger.jsonl", session="sess-STOP")

    def base_fails_cand_fails(h, spec):
        if spec.get("frozen"):
            return _ok("fz")       # 冻结任务必须过，否则非回归闸判回归
        return _bad("dev")

    class FixedProposer:
        def propose(self, weakness, harness, n=3):
            return [Capsule(name="any", genes=(
                Gene(op="set", target="L3.skills.any", value="x",
                     rationale="t"),))]

    dev = [FrozenTask(task_id=f"A::d{i}", spec={"env": "A"}) for i in range(3)]
    frozen = [FrozenTask(task_id="fz", spec={"env": "A", "frozen": True})]
    base = Baseline(caliber=CAL, verdicts={"fz": _ok("fz")}, harness_id="seed")
    ho = GeneralizationGate(
        make_heldout("B", [FrozenTask(task_id="h0",
                                      spec={"env": "B", "heldout": True})]),
        dev_task_ids=[t.task_id for t in dev])

    loop = EvolutionLoop(led, NonRegressionGate(frozen, base), dev,
                         base_fails_cand_fails, FixedProposer(), caliber=CAL,
                         heldout=ho,
                         config=EvolutionConfig(group_size=2,
                                                stop_on_saturation=True))
    _, reports = loop.run(Harness(tools=("read_file", "write_file",
                                         "python_exec")), rounds=6)
    #: ⛔ 连续 2 轮才停：只跑 1 轮就停会误杀"刚提交完、新 harness 还没
    #:    暴露失败"这种正常的一轮空档。
    ck("B1 请求 6 轮但连续 2 轮无事可做 ⇒ 只跑 2 轮（不再空转 4 轮）",
       len(reports) == 2, f"实跑 {len(reports)} 轮")

    evs = Ledger(d / "ledger.jsonl").events()
    kinds = [e.kind for e in evs]
    ck("B2 提前终止留事件（否则'为什么少跑了 4 轮'无从解释）",
       "evolution.stopped" in kinds, str(kinds))
    #: ⛔ 取不到不许 IndexError 崩掉整轮 —— 反证注入时它会缺失，
    #:    崩了就看不到后面几组（表象：只红了 B2，实际 C 组也没跑）
    stopped = next((e.payload for e in evs
                    if e.kind == "evolution.stopped"), None) or {}
    ck("B3 终止事件带请求轮数（可看出被砍了多少）",
       stopped.get("rounds_requested") == 6, str(stopped))

    #: 反证：关掉 stop_on_saturation 不得提前停（开关真有效）
    led3 = Ledger(d / "l3.jsonl", session="sess-STOP3")
    loop3 = EvolutionLoop(led3, NonRegressionGate(frozen, base), dev,
                          base_fails_cand_fails, FixedProposer(), caliber=CAL,
                          heldout=ho,
                          config=EvolutionConfig(group_size=2,
                                                 stop_on_saturation=False))
    _, rep3 = loop3.run(Harness(tools=("read_file", "write_file",
                                       "python_exec")), rounds=4)
    ck("B4 反证：stop_on_saturation=False ⇒ 跑满 4 轮（开关不是摆设）",
       len(rep3) == 4, f"实跑 {len(rep3)} 轮")


# ══════════════════════════════════════════════════════════════════
# C 组：已知死路冷却（连续被拦 ⇒ 不再花探测预算）
# ══════════════════════════════════════════════════════════════════
def group_c() -> None:
    print("\nC 组：同一候选连续被泛化闸拦 ⇒ 冷却（带出口，不许永久封死）")

    d = Path(tempfile.mkdtemp(prefix="v6_dead_"))
    led = Ledger(d / "ledger.jsonl", session="sess-DEAD")

    def dev_gain_heldout_flat(h, spec):
        """发展集：加了 `special` 技能就过；held-out：加了也不过 ⇒ overfit。

        ⛔ 关键是 base 在发展集上**失败**（否则会判饱和，走不到探测）。
        """
        if spec.get("heldout"):
            return _bad("ho")
        if spec.get("frozen"):
            return _ok("fz")
        if "special" in h.skills:
            return _ok("dev")
        return _bad("dev")

    class FixedProposer:
        def propose(self, weakness, harness, n=3):
            return [Capsule(name="add_special", genes=(
                Gene(op="set", target="L3.skills.special", value="x",
                     rationale="t"),))]

    dev = [FrozenTask(task_id=f"A::d{i}", spec={"env": "A"}) for i in range(3)]
    frozen = [FrozenTask(task_id="fz", spec={"env": "A", "frozen": True})]
    base = Baseline(caliber=CAL, verdicts={"fz": _ok("fz")}, harness_id="seed")
    ho = GeneralizationGate(
        make_heldout("B", [FrozenTask(task_id="h0",
                                      spec={"env": "B", "heldout": True})]),
        dev_task_ids=[t.task_id for t in dev])

    loop = EvolutionLoop(led, NonRegressionGate(frozen, base), dev,
                         dev_gain_heldout_flat, FixedProposer(), caliber=CAL,
                         heldout=ho,
                         config=EvolutionConfig(group_size=2,
                                                deadend_streak=3,
                                                deadend_probation_every=0))
    for i in range(5):
        loop.run_round(Harness(tools=("read_file", "write_file",
                                      "python_exec")), round_no=i + 1)

    evs = Ledger(d / "ledger.jsonl").events()
    probes = [e for e in evs if e.kind == "gen.probe"]
    cools = [e for e in evs if e.kind == "deadend.cooldown"]
    print(f"     （探测 {len(probes)} 次 / 冷却 {len(cools)} 次）")

    ck("C1 前 3 次照常探测（冷却前不抢跑）", len(probes) == 3,
       f"实探 {len(probes)} 次")
    ck("C2 连续 3 次被拦后 ⇒ 第 4、5 轮不再探测（省预算）",
       len(cools) == 2, f"冷却 {len(cools)} 次")

    #: ⛔ 环境对必须进账本：没有它，冷却只能按候选全局生效 ⇒ 换环境被误伤
    ck("C3 gen.probe 事件带 env_pair（冷却的维度，不能是 unknown）",
       all((e.payload or {}).get("env_pair") not in (None, "unknown")
           for e in probes),
       str([(e.payload or {}).get("env_pair") for e in probes]))
    ck("C4 env_pair 形如 'dev->heldout'（真读到两侧环境）",
       all("->" in str((e.payload or {}).get("env_pair")) for e in probes),
       str([(e.payload or {}).get("env_pair") for e in probes]))

    #: 出口：不许永久封死 —— probation 轮必须放行
    d2 = Path(tempfile.mkdtemp(prefix="v6_prob_"))
    led2 = Ledger(d2 / "ledger.jsonl", session="sess-PROB")
    loop2 = EvolutionLoop(led2, NonRegressionGate(frozen, base), dev,
                          dev_gain_heldout_flat, FixedProposer(), caliber=CAL,
                          heldout=ho,
                          config=EvolutionConfig(group_size=2,
                                                 deadend_streak=3,
                                                 deadend_probation_every=5))
    for i in range(5):
        loop2.run_round(Harness(tools=("read_file", "write_file",
                                       "python_exec")), round_no=i + 1)
    ev2 = Ledger(d2 / "ledger.jsonl").events()
    r5 = [e for e in ev2 if e.kind == "gen.probe"
          and (e.payload or {}).get("round_no") == 5]
    ck("C5 出口：第 5 轮是试探轮 ⇒ 仍会探测（不是永久封死）",
       len(r5) == 1, f"第 5 轮探测 {len(r5)} 次")

    #: 反证：退掉冷却 ⇒ 5 轮必须全探测（判据能检出缺失）
    d3 = Path(tempfile.mkdtemp(prefix="v6_nocool_"))
    led3 = Ledger(d3 / "ledger.jsonl", session="sess-NOCOOL")
    loop3 = EvolutionLoop(led3, NonRegressionGate(frozen, base), dev,
                          dev_gain_heldout_flat, FixedProposer(), caliber=CAL,
                          heldout=ho,
                          config=EvolutionConfig(group_size=2,
                                                 deadend_streak=99))  # 关掉
    for i in range(5):
        loop3.run_round(Harness(tools=("read_file", "write_file",
                                       "python_exec")), round_no=i + 1)
    ev3 = Ledger(d3 / "ledger.jsonl").events()
    ck("C6 反证：streak 阈值调到 99（等于关掉）⇒ 5 轮全探测 + 0 冷却",
       len([e for e in ev3 if e.kind == "gen.probe"]) == 5
       and not [e for e in ev3 if e.kind == "deadend.cooldown"],
       f"探测 {len([e for e in ev3 if e.kind == 'gen.probe'])} 次")


def main() -> int:
    print("=" * 60)
    print("契约测试 · 到顶不许空转 / 死路不许重提")
    print("=" * 60)
    group_a()
    group_b()
    group_c()
    print()
    print("=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   · {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
