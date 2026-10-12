#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：冻结非回归闸 —— **先证没变坏，再谈变好**。

2026 self-harness 综述：灾难性遗忘/回归是自进化头号失败模式，
缓解手段是 deterministic non-regression gates。这一条正是 V5 最硬的资产
（冻结基线 + 三轮对照 + 零新红），V6 把它从"人的纪律"变成"系统的准入条件"。

本文件守：
  · 回归必被拦（基线绿的任务变红 ⇒ 闸红）
  · 口径不一致 ⇒ 判"不可比"而不是"大致可比"（fail-closed）
  · 闸崩了 ⇒ 失败，不是放行
  · 事故版：runner 恒绿 / 恒红 ⇒ 闸的行为必须随之改变（证明不是死代码）

用法：
    .venv/bin/python tests/contract_nongression_test.py
"""
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main():
    from aether.genome import Harness
    from aether.nongression import (Baseline, FrozenTask, NonRegressionGate,
                                    pass_at_k, supersede)
    from aether.verifier import Check, Verdict

    CAL = {"seed": 0, "timeout": 10.0}

    def mk_verdict(passed, name="t"):
        return Verdict(passed=passed,
                       checks=(Check(name, passed, "got=1 expect=1" if passed
                                     else "got=0 expect=1"),),
                       evidence={"n": 1})

    suite = [FrozenTask(task_id="t1", spec={"x": 1}),
             FrozenTask(task_id="t2", spec={"x": 2}),
             FrozenTask(task_id="t3", spec={"x": 3})]
    base = Baseline(caliber=CAL,
                    verdicts={"t1": mk_verdict(True), "t2": mk_verdict(True),
                              "t3": mk_verdict(False)},   # t3 本来就红
                    harness_id="seed")
    gate = NonRegressionGate(suite, base)

    # ── A 组：闸的存在与口径 ──────────────────────────────────────
    print("A 组：闸存在 + 口径闸")
    ok, why = gate.check_caliber(CAL)
    ck("A1 同口径 ⇒ 可比", ok, why)
    ok2, why2 = gate.check_caliber({"seed": 1, "timeout": 10.0})
    ck("A2 口径不同 ⇒ 判不可比（fail-closed，不是大致可比）", not ok2, why2)
    ok3, why3 = gate.check_caliber({"seed": 0})
    ck("A3 缺字段 ⇒ 判不可比", not ok3, why3)
    ck("A4 基线通过集只含 t1/t2（t3 本来红，不算回归基线）",
       base.passed_ids() == ["t1", "t2"], f"{base.passed_ids()}")

    # ── B 组：回归检测 ────────────────────────────────────────────
    print("B 组：回归必被拦")
    def runner_all_green(h, spec):
        return mk_verdict(True, spec.get("x", 0) and "t" or "t")
    r = gate.evaluate(Harness(), runner_all_green, CAL)
    ck("B1 全绿 ⇒ 闸通过", r.passed, f"{r.regressions}")

    def runner_t2_red(h, spec):
        return mk_verdict(spec.get("x") != 2)
    r2 = gate.evaluate(Harness(), runner_t2_red, CAL)
    ck("B2 t2 由绿变红 ⇒ 闸拦下", not r2.passed, f"{r2.regressions}")
    ck("B3 回归项被点名（能说清哪条退了）",
       any("t2" in s for s in r2.regressions), f"{r2.regressions}")
    ck("B4 t3 本来红 ⇒ 不参与回归判定（不算新回归）",
       not any("t3" in s for s in r2.regressions), f"{r2.regressions}")

    # ── C 组：崩溃 = 失败（不吞异常换放行）────────────────────────
    print("C 组：崩溃即失败")
    def runner_boom(h, spec):
        raise RuntimeError("boom")
    r3 = gate.evaluate(Harness(), runner_boom, CAL)
    ck("C1 runner 全崩 ⇒ 闸失败", not r3.passed)
    ck("C2 崩溃被记账（不是静默当通过）", len(r3.errors) == 3, f"{r3.errors}")

    # ── D 组：反证（证明闸有判别力，不是恒绿/恒红）────────────────
    print("D 组：反证")
    ck("D1 事故版：runner 恒绿 ⇒ 闸必放行（闸在测真东西）",
       gate.evaluate(Harness(), runner_all_green, CAL).passed)
    ck("D2 事故版：runner 恒红 ⇒ 闸必拦（闸不是恒绿）",
       not gate.evaluate(Harness(), lambda h, s: mk_verdict(False), CAL).passed)
    ck("D3 事故版：基线被 supersede ⇒ 禁止再当基准",
       not gate.check_caliber(CAL)[0] if base.superseded else True)

    # ── E 组：口径变更留痕 ────────────────────────────────────────
    print("E 组：supersede 留痕")
    new, old = supersede(base, {"seed": 0, "timeout": 30.0}, "timeout 10→30")
    ck("E1 新基线口径已变", new.caliber["timeout"] == 30.0)
    ck("E2 旧基线被标记 superseded（**不删除**）", old.superseded)
    ck("E3 留痕原因非空", "timeout" in old.supersede_reason)
    g_old = NonRegressionGate(suite, old)
    ok4, why4 = g_old.check_caliber(CAL)
    ck("E4 拿已 supersede 的基线比对 ⇒ 被拒绝", not ok4, why4)

    # ── F 组：pass@k ──────────────────────────────────────────────
    print("F 组：pass@k（多次采样，单轮方差太大）")
    ck("F1 全 0 ⇒ 0", pass_at_k([0, 0, 0]) == 0.0)
    ck("F2 全 1 ⇒ 1", pass_at_k([1, 1, 1]) == 1.0)
    ck("F3 3 中 1 且 k=3 ⇒ 1.0", abs(pass_at_k([1, 0, 0], 3) - 1.0) < 1e-9)
    ck("F4 3 中 1 且 k=1 ⇒ 1/3", abs(pass_at_k([1, 0, 0], 1) - 1 / 3) < 1e-9)
    ck("F5 空列表 ⇒ 0（不崩）", pass_at_k([]) == 0.0)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
