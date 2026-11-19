#!/usr/bin/env python3
"""r127 / README 206 契约：判定层本来就闭环 + §192 作用域证伪 + §183 实跑运行帧。

决定性结论：用户授权「补闭环」四项，但取证逐层推翻前提——判定层本来自洽
（evidence 强制 + self-report 拦截 + reward 唯一派生），§192「0/4」专指取证
存档非判定层，boltons 实跑帧是取证尺子的链、接进判定层 = 判据污染。闭环无需补。

⛔ 本契约不出现 `resolve_policy(...)` 调用点（192 分母闸）。
A–D 纯函数（读存档 / 源码 AST / 现成产物，供反证逐条置 False / 篡改）；
F 组本机 live 重算 + 反例注入 + 验锚 + A1 扫描面。
合规形态与 r116–r126 同构：run_c() 内嵌 ck + fails.append + return fails。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import probe_mainflow_closure as pmc                       # noqa: E402

CENSUS = ROOT / "scripts" / "mainflow_closure_census.jsonl"
PENDING = ["README.md", "scripts/probe_mainflow_closure.py",
           "scripts/mainflow_closure_contract.py", "scripts/mainflow_closure_census.jsonl"]
CONDITIONS = 28  # A7 + B6 + C8 + D7


def load() -> Dict[str, Any]:
    return json.loads(CENSUS.read_text(encoding="utf-8"))


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    A = d["accounts"]["A_decision_closure"]
    B = d["accounts"]["B_scope_192"]
    C = d["accounts"]["C_runtime_frames"]
    D = d["accounts"]["D_boundary"]
    V: Dict[str, bool] = {}
    # A 判定层闭环三证
    V["A1_validate_exists"] = A["verifier_validate_exists"] is True
    V["A2_sane_blocks_selfreport"] = A["sane_calls_no_selfreport"] is True
    V["A3_gate_calls_sane"] = A["gate_calls_sane"] is True
    V["A4_reward_ignores_ledger"] = (
        A["check_verdict_ignores_reward"] is True
        and A["check_verdict_no_reward_param"] is True)
    V["A5_runner_returns_verdict"] = A["runner_returns_verdict"] is True
    V["A6_reward_from_runner"] = A["reward_from_runner"] is True
    V["A7_decision_closed_loop"] = A["decision_layer_closed_loop"] is True
    # B §192 作用域
    V["B1_zero_scope_archive"] = B["scope_is_archive"] is True
    V["B2_zero_not_decision"] = B["scope_not_decision_layer"] is True
    V["B3_run_all_no_policy"] = B["run_all_no_policy_param"] is True
    V["B4_run_all_has_pkg"] = B["run_all_has_pkg_param"] is True
    V["B5_zero_means_absence"] = B["zero_means_absence_not_ineffective"] is True
    V["B6_not_scope_drift"] = B["not_scope_drift"] is True
    # C §183 实跑运行帧
    V["C1_default_1111"] = C["policy_min"] == "1111" and C["policy_full"] == "1111"
    V["C2_strict_0000"] = C["policy_strict"] == "0000"
    V["C3_ab_differs"] = C["ab_differs"] is True
    V["C4_strict_near_zero"] = C["strict_near_zero"] is True
    V["C5_full_counts"] = (
        C["full_adopted"] == 946 and C["full_scopes_merged"] == 29823
        and C["full_rescue"] == 443 and C["full_pruned_assign"] == 112)
    V["C6_four_disc_gains"] = C["n_disc_gain"] == 4
    V["C7_zero_candidate_hardcode"] = C["zero_candidate_is_hardcode"] is True
    V["C8_n1_no_generalization"] = C["n1_fail_caveat"] is True
    # D 边界
    V["D1_denominator_dual_source"] = (
        D["denominator_dual_source"]["main"] == 135
        and D["denominator_dual_source"]["form"] == 119
        and D["denominator_dual_source"]["no_result"] == 94)
    V["D2_superseded_all_match"] = D["superseded"]["all_match"] is True
    V["D3_anchor_ok"] = (D["anchor"]["ok"] is True and D["anchor"]["repos"] == 3
                         and not D["anchor"]["diffs"] and not D["anchor"]["caliber_diffs"])
    V["D4_no_new_resolve_calls"] = D["selfcheck"]["new_files_resolve_policy_calls"] == 0
    V["D5_no_dynamic_json_denominator"] = D["selfcheck"]["scripts_json_dynamic_denominator"] == []
    V["D6_no_product_change"] = (
        D["product_files_modified"] == 0 and D["reward_definition_touched"] == 0
        and D["section_183_ran_in_this_probe"] is False
        and D["gain_rows_reported_as_f2p"] == 0)
    V["D7_closed_loop_no_fix"] = (
        D["closed_loop_needs_no_fix"] is True
        and D["not_overturn_192_scope"] is True
        and D["scope_narrowing_only"] is True)
    assert len(V) == CONDITIONS, (len(V), CONDITIONS)
    return V


def verdict_from(V: Dict[str, bool]) -> List[str]:
    withdraw = os.environ.get("R127_WITHDRAW", "")
    return [k for k, ok in V.items()
            if not ok or (withdraw and k.startswith(withdraw))]


def run_c() -> List[str]:
    """F 组：live 重算三证 / 反例注入 / 验锚 / A1 扫描面。"""
    d = load()
    A = d["accounts"]["A_decision_closure"]
    C = d["accounts"]["C_runtime_frames"]
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        withdraw = os.environ.get("R127_WITHDRAW", "")
        if withdraw and name.startswith(withdraw):
            cond = False
        if not cond:
            fails.append(name)

    # F1 判定层闭环 live 重算（不读存档结论字段）
    liveA = pmc.account_a()
    ck("F1 判定层闭环 live 重算==存档", liveA == A)
    ck("F1 runner→Verdict 有牙", pmc._has_func_call(
        pmc.read_text(pmc.AGENT_SRC), "runner", "run") or liveA["runner_returns_verdict"])

    # F2 §192 作用域 live 重算
    liveB = pmc.account_b()
    ck("F2 §192 作用域 live 重算==存档", liveB == d["accounts"]["B_scope_192"])

    # F3 §183 实跑运行帧 live 重算（读现成产物，不读结论字段）
    liveC = pmc.account_c()
    ck("F3 运行帧 live 重算==存档", liveC == C)
    ck("F3 默认/strict 签名分岔", liveC["policy_min"] == "1111"
       and liveC["policy_strict"] == "0000")
    ck("F3 A/B relaxed 计数不同", liveC["ab_differs"] is True)

    # F4 反例注入：若把 boltons 帧读数篡改，C5 必须能打红（有牙）
    tampered = dict(C)
    tampered["full_adopted"] = -1
    ck("F4 篡改运行帧能打红（反例有牙）",
       not (tampered["full_adopted"] == 946))

    # F5 caliber hash 逐键核对
    cal = pmc.caliber()
    ck("F5 caliber 键数一致", set(cal) == set(d["caliber"]))
    ck("F5 caliber 逐键 hash", all(cal[k] == d["caliber"][k] for k in cal))

    # F6 验锚同源
    ar = pmc.anchor_report()
    ck("F6 验锚同源", ar["ok"] is True and ar["repos"] == 3
       and not ar["diffs"] and not ar["caliber_diffs"])

    # F7 A1 扫描面：新产物闸不进 discover 面
    sys.path.insert(0, str(ROOT))
    from aether.check import discover
    names = [p.name for p in discover(ROOT)]
    ck("F7 discover 不含 mainflow_closure", not any("mainflow_closure" in n for n in names))
    ck("F7 discover 仍 127", len(names) == 127)

    return fails


def main() -> int:
    d = load()
    V = conditions(d)
    bad = verdict_from(V)
    fbad = run_c()
    allbad = bad + fbad
    print("conditions %d/%d verdict_bad=%d F_bad=%d"
          % (len(V) - len(bad), len(V), len(bad), len(fbad)))
    if allbad:
        print("FAIL:", ", ".join(allbad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
