#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：奖励只来自**可执行判据**（RLVR），自述不算、打分不算。

2026 的核心共识："if you can execute a check, you have a reward function."
反面就是 reward hacking —— 文献把它列为自进化系统的头号失败模式。

本文件守：
  · reward 只有一个定义（判据同源，别处不许重写）
  · 没有 evidence 的判定不算判定（V5：`artifact_is_hollow` 的制度化）
  · 自述/LLM 打分字段一出现 ⇒ 判定作废（挡住"自述即奖励"这条通路）
  · 环境指纹：跨环境/跨口径不可比

用法：
    .venv/bin/python tests/contract_verifier_test.py
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
    from aether import verifier as V

    # ── A 组：reward 只来自执行 ───────────────────────────────────
    print("A 组：reward 只来自执行判据")
    v_ok = V.from_exit_code("pytest", 0, stdout="5 passed", stderr="")
    v_bad = V.from_exit_code("pytest", 1, stdout="2 failed", stderr="assert")
    ck("A1 退出码 0 ⇒ reward 1", v_ok.reward == 1.0, f"{v_ok.reward}")
    ck("A2 退出码非 0 ⇒ reward 0", v_bad.reward == 0.0, f"{v_bad.reward}")
    ck("A3 reward 只有 0/1 两种取值（不是连续分）",
       V.all_of("t", [("a", True, "ev")]).reward in (0.0, 1.0))
    ck("A4 evidence 是真实输出（可溯源到 stdout 指纹）",
       "stdout_sha256" in v_bad.evidence and "stdout_head" in v_bad.evidence)

    # ── B 组：没有 evidence 的判定不算判定 ────────────────────────
    print("B 组：evidence 必填（V5：写入端降级成功 ≠ 可用）")
    no_ev = V.Verdict(passed=True, checks=(V.Check("c", True, ""),))
    ok, why = no_ev.validate()
    ck("B1 check 缺 evidence ⇒ 判定无效", not ok, why)
    empty = V.Verdict(passed=True)
    ck("B2 没有任何 check ⇒ 判定无效（那是宣称）", not empty.validate()[0])
    sane = V.assert_verdict_sane(no_ev)
    ck("B3 无效判定 ⇒ reward 归 0（不是照样通过）", sane.reward == 0.0)
    ck("B4 无效原因被显式记录（不静默）",
       "VERDICT_INVALID" in (sane.error or ""), sane.error)

    # ── C 组：自述/打分字段必须被拦 ───────────────────────────────
    print("C 组：反作弊（自述即奖励）")
    for key in sorted(V.SELF_REPORT_KEYS):
        v = V.Verdict(passed=True, checks=(V.Check("c", True, "x"),),
                      evidence={key: 0.9})
        ok_k, why_k = v.validate()
        ck(f"C1 evidence 含 {key} ⇒ 判定作废", not ok_k, why_k)
    try:
        V.assert_no_self_report({"llm_score": 0.99})
        ck("C2 assert_no_self_report 会抛异常", False, "没抛")
    except V.SelfReportDetected:
        ck("C2 assert_no_self_report 会抛异常", True)
    ck("C3 正常 evidence 不误伤",
       V.Verdict(passed=True, checks=(V.Check("c", True, "x"),),
                 evidence={"n_passed": 3}).validate()[0])

    # ── D 组：判据同源（分数只有一处定义）────────────────────────
    print("D 组：判据同源")
    import inspect
    src = inspect.getsource(V.Verdict)
    ck("D1 Verdict.reward 是唯一奖励定义（源码里只出现一次 def）",
       src.count("def reward") == 1)
    ck("D2 reward 不读任何模型输出字段",
       "self_reported" not in src and "llm_score" not in src)
    ck("D3 从 dict 还原的 verdict 走同一套 sanity 闸",
       V.check_verdict({"passed": True, "checks": [{"name": "c", "passed": True,
                                                    "evidence": "ev"}]}).reward == 1.0)

    # ── E 组：环境指纹（跨口径不可比）─────────────────────────────
    print("E 组：环境指纹")
    f1 = V.env_fingerprint({"seed": 1, "timeout": 10})
    f2 = V.env_fingerprint({"seed": 2, "timeout": 10})
    f3 = V.env_fingerprint({"timeout": 10, "seed": 1})
    ck("E1 口径不同 ⇒ 指纹不同", f1 != f2)
    ck("E2 键序不同 ⇒ 指纹相同（确定性）", f1 == f3)
    err = V.error_verdict("env/x", RuntimeError("boom"))
    ck("E3 验证器崩溃 ⇒ reward 0 且**留痕**",
       err.reward == 0.0 and "boom" in err.error)

    # ── F 组：端到端（真实执行一个判据）───────────────────────────
    print("F 组：端到端（沙箱里真跑一次）")
    from aether.environment import Environment, verify_source
    env = Environment.from_dir(ROOT / "tasks" / "textscore")
    v = verify_source(env)
    ck("F1 基线源码 ⇒ 全绿", v.passed and v.reward == 1.0,
       f"{v.n_passed}/{len(v.checks)}")
    ck("F2 判定带真实证据", "n_passed" in v.evidence)
    ck("F3 环境指纹非空（跨环境不可比可被发现）", bool(v.env_fingerprint))

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
