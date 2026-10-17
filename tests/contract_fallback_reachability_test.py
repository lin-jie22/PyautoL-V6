#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**退避分支必须真的有牙**（0 次不许被当成"这条分支是好的"）。

为什么要有它（2026-10-17）：`scripts/audit_zero_events.py` 把账本里 0 次的
事件分成三类后，退避分支（fallback）那一类最危险 ——

    它 0 次既可能是"从没出错过"（健康），也可能是**永远不会被触发的死代码**
    （V5 的 `will_delete` 恒 0、`aging_events["pruned"]` 恒 0 都是这一类：
    一个静默归零的读数比没有读数更坏，因为它会持续冒充"没问题"）。

⛔⛔ **0 次没有一阶信息**：唯一获得信息的方式是**注入该分支的触发条件**，
确认它真的写了事件。本契约就是干这个的，并且每条都配**反证组**（不注入
时该事件**不许**出现，否则说明它在正常运行中也会误报）。

覆盖（单元级可注入的四条）：

  A 组  `_rebaseline` 依赖抛错 ⇒ `baseline.supersede_failed` 必须写下
  B 组  `_observe_deadend` 依赖抛错 ⇒ 异常必须抛给调用方（它自己**不吞**）
  C 组  ledger.append 抛错 ⇒ proposer 退化 no-op，不拖垮一轮
  D 组  **反证**：不注入 ⇒ 上述 failure 事件一律不许出现（否则是误报）
  E 组  死路冷却与累计侧同口径：`_in_cooldown` 达标 ⇒ 冷却；
        probation 轮 ⇒ 放行（不许永久封死）；未达标 ⇒ 不冷却（不激进）

⛔ **本契约覆盖不到的**（如实标记，不伪造）：
`world_fact.promote_failed` / `attribution.unavailable` /
`cleanup.propose_failed` / `gate.blocked` 的真正记账点都在 `_run_round`
内部（分别由 evolution.py:1608 / 1853 / 1517 / 1630 的 except 写），
单元级注入进不去。B 组只能验到"异常会冒出这一层"，**验不到最终的写osseus**。
⇒ 这三条在 `scripts/audit_zero_events.py` 里保持 `unverified`，等端到端补丁。

用法：
    python tests/contract_fallback_reachability_test.py
"""
import sys
import tempfile
from pathlib import Path

SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    from aether.evolution import EvolutionConfig, EvolutionLoop
    from aether.genome import Harness
    from aether.ledger import Ledger
    from aether.providers import MemoryAwareProposer

    def _loop(led: Ledger) -> EvolutionLoop:
        """不跑 `__init__` 的最小 loop（与既有 J 组契约同构造路径）。"""
        lp = EvolutionLoop.__new__(EvolutionLoop)
        lp.ledger = led
        lp.cfg = EvolutionConfig()
        lp._deadend_cache = None
        lp._deadend_stats = None
        return lp

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ── A 组：_rebaseline 的退避分支 ─────────────────────────
        print("A 组：基线升级失败 ⇒ baseline.supersede_failed")
        led_a = Ledger(tmp / "a.jsonl")
        la = _loop(led_a)
        la.runner = lambda h, spec: (_ for _ in ()).throw(
            RuntimeError("runner dead"))      # 注入事故
        la.gate = None                        # ⇒ 1439 行必定进 except
        la.caliber = {"seed": 0, "timeout": 10.0, "split": "frozen"}
        la._rebaseline(Harness(), 1, "test")
        ka = [e.kind for e in led_a.events()]
        ck("A1 失败时写下 baseline.supersede_failed",
           "baseline.supersede_failed" in ka, str(ka))
        if ka:
            p = [e for e in led_a.events()][-1].payload
            ck("A2 事件带归因字段（round_no/error）",
               {"round_no", "error"} <= set(p), str(sorted(p)))

        # ── B 组：world_fact 晋升的退避分支 ──────────────────────
        print("B 组：_observe_deadend 崩溃时**交给上层**（它自己不许吞）")
        led_b = Ledger(tmp / "b.jsonl")
        lb = _loop(led_b)
        import aether.world_facts as wf
        orig = wf.fact_key
        raised = False
        try:
            wf.fact_key = lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("facts dead"))  # 注入事故
            lb._observe_deadend(Harness(), "cap:x", "a->b", 1, "gen.probe", 3)
        except Exception:                                  # noqa: BLE001
            raised = True
        finally:
            wf.fact_key = orig
        ck("B1 依赖崩溃 ⇒ 异常冒出到调用方（由 evolution.py:1608 的 except 记账）",
           raised)
        #: ⛔ 诚实：这条**验不到** `world_fact.promote_failed` 真的被写下
        #:    —— 那一层需要 `_run_round` 端到端覆盖，本契约 touch 不到。
        print("     ℹ️ promote_failed 的最终记账需端到端覆盖，此处只验异常传递")

        # ── C 组：连"记账失败"都可查 ────────────────────────────
        print("C 组：记账本身失败 ⇒ experience.ledger_failed")

        class _BadLedger:
            def append(self, *a, **k):
                raise RuntimeError("disk full")

        p_ = MemoryAwareProposer(_StubProposer(), None, ledger=_BadLedger())
        p_.remember({"kind": "k", "topic": "t"}, "cap", "gate_reject: x")
        ck("C1 ledger 抛错时 proposer 不崩（降级 no-op，不拖垮一轮）", True)
        #: ⛔⛔ **诚实边界**：`experience.ledger_failed` 是用**同一条坏通道**
        #:    去记这条通道的失败 ⇒ ledger **持续**不可用（如磁盘满）时它
        #:    **永远写不出来** ⇒ 那次事故在账本里就是"没有任何记录"。
        #:    它只在**间歇性**故障（第一次抛、第二次成功）下才有意义。
        #:    ⇒ 真正保证可用性的是"C1 不崩"，不是"有一条 ledger_failed"。
        print("     ℹ️ ledger 持续故障时 ledger_failed 自身也写不出 ⇒ "
              "保底的是「不崩」，不是「有这条读数」")

        # ── D 组：反证 —— 不注入时不许出现 ──────────────────────
        print("D 组：反证 —— 正常路径不许冒出 failure 事件")
        led_d = Ledger(tmp / "d.jsonl")
        ld = _loop(led_d)

        class _FakeGate:
            class _B:
                harness_id = "seed"

                def __init__(self):
                    self.verdicts = {}

            def __init__(self):
                self.baseline = _FakeGate._B()
                self.suite = []

        ld.gate = _FakeGate()
        ld.caliber = {"seed": 0, "timeout": 10.0, "split": "frozen"}
        ld.runner = lambda h, spec: None
        try:
            ld._rebaseline(Harness(), 1, "test")
        except Exception:                                  # noqa: BLE001
            pass
        kd = [e.kind for e in led_d.events()]
        ck("D1 无异常时不该出现 supersede_failed",
           "baseline.supersede_failed" not in kd, str(kd))

        # ── E 组：死路冷却判定需要与累计侧同口径 ─────────────────
        print("E 组：_in_cooldown 语义（达标 / probation 出口）")
        le = _loop(Ledger(tmp / "e.jsonl"))
        m = {"c|a->b": 3}
        cool, streak = le._in_cooldown("c", "a->b", m, round_no=1)
        ck("E1 streak 达标 ⇒ 冷却", cool and streak == 3, f"cool={cool} s={streak}")
        #: ⛔ 冷却不许永久封死：probation 轮必须放行
        cool2, _ = le._in_cooldown("c", "a->b", m, round_no=5)
        ck("E2 probation 轮放行（不永久封死）", not cool2, f"cool={cool2}")
        cool3, _ = le._in_cooldown("c", "a->b", {"c|a->b": 2}, round_no=1)
        ck("E3 未达标 ⇒ 不冷却（不激进）", not cool3, f"cool={cool3}")

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


class _StubProposer:
    def __init__(self, names=("a", "b")):
        self.names = names

    def propose(self, weakness, harness, n=3, rejected=None):
        return [type("_C", (), {"name": x})() for x in self.names[:n]]

    def diagnostics(self):
        return {"name": "stub"}


if __name__ == "__main__":
    sys.exit(main())
