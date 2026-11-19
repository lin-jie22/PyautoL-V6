#!/usr/bin/env python3
"""r126 / README 205 契约：帧词命中数依赖第五个正交口径——run 状态面。

§199–§204/r125 的命中数只在 status=pass 的成功分支 tail（末4行/400字截断）
上量过。`_summarize_out(ok=False)` 失败分支另走保信号行 + 头尾 + 省略计数，
从没被当成独立口径量过。本契约核 10 份产物里同一条 contract_signature_impostor
的跨状态对拍：9 pass 尾 200 字命中 0、1 fail 尾 801 字命中 selftest。

⛔ 本契约不出现 `resolve_policy(...)` 调用点（192 分母闸）。
A–D 纯函数（读存档、供反证逐条置 False / 篡改）；F 组本机实战。
合规形态与 r116–r125 同构：run_c() 内嵌 ck + fails.append + return fails。
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

import probe_run_status_surface as prss                  # noqa: E402

CENSUS = ROOT / "scripts" / "run_status_surface_census.jsonl"
PENDING = ["README.md", "scripts/probe_run_status_surface.py",
           "scripts/run_status_surface_contract.py", "scripts/run_status_surface_census.jsonl"]
CONDITIONS = 24  # A4 + B6 + C7 + D7


def load() -> Dict[str, Any]:
    return json.loads(CENSUS.read_text(encoding="utf-8"))


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    A = d["accounts"]["A_status_def"]
    B = d["accounts"]["B_cross_status"]
    C = d["accounts"]["C_attribution"]
    D = d["accounts"]["D_boundary"]
    V: Dict[str, bool] = {}
    # A 状态面定义层
    V["A1_summarize_found"] = A["summarize_found"] is True
    V["A2_ok_last4_slice"] = A["ok_branch_last4_slice"] is True
    V["A3_ok_cap400_slice"] = A["ok_branch_cap400_slice"] is True
    V["A4_two_branches_distinct"] = A["two_branches_distinct"] is True
    # B 跨状态对拍层
    V["B1_ten_products"] = B["n_products"] == 10
    V["B2_all_target_unique"] = B["all_target_unique"] is True
    V["B3_nine_pass_one_fail"] = B["n_pass"] == 9 and B["n_fail"] == 1
    V["B4_pass_tails_uniform"] = B["pass_tails_uniform"] is True
    V["B5_fail_tail_selftest"] = B["fail_tail_selftest"] is True
    V["B6_status_surface_matters"] = B["status_surface_matters"] is True
    # C 归因层
    V["C1_selftest_lines_nonzero"] = C["n_selftest_lines"] > 0
    V["C2_all_selfreport"] = C["all_selftest_lines_selfreport"] is True
    V["C3_pass_last4_absent"] = C["pass_selftest_absent_in_last4"] is True
    V["C4_hit_not_frame_field"] = C["hit_not_frame_field"] is True
    V["C5_pass_fail_not_merged"] = (
        C["pass_fail_not_merged"] is True and C["merged_into_bool"] is False)
    V["C6_n1_no_generalization"] = (
        C["n_fail_is_one"] is True and C["no_generalization_claim"] is True)
    V["C7_not_overturn"] = C["not_overturn_199_201_202_203_204_125"] is True
    # D 边界层
    V["D1_denominator_dual_source"] = (
        D["denominator_dual_source"]["main"] == 135
        and D["denominator_dual_source"]["form"] == 119
        and D["denominator_dual_source"]["no_result"] == 94)
    V["D2_superseded_all_match"] = D["superseded"]["all_match"] is True
    V["D3_anchor_ok"] = (D["anchor"]["ok"] is True and D["anchor"]["repos"] == 3
                         and not D["anchor"]["diffs"] and not D["anchor"]["caliber_diffs"])
    V["D4_no_new_resolve_calls"] = D["selfcheck"]["new_files_resolve_policy_calls"] == 0
    V["D5_no_dynamic_json_denominator"] = D["selfcheck"]["scripts_json_dynamic_denominator"] == []
    V["D6_section183_zero"] = (
        D["section_183_touched"] is False and D["real_expr_run"] == 0
        and D["real_commit_run"] == 0 and D["main_repo_run"] == 0
        and D["gain_rows_reported"] == 0 and D["product_files_modified"] == 0)
    V["D7_scope_narrowing_only"] = (
        D["scope_narrowing_only"] is True and D["direction_199_203_unchanged"] is True)
    assert len(V) == CONDITIONS, (len(V), CONDITIONS)
    return V


def verdict_from(V: Dict[str, bool]) -> List[str]:
    withdraw = os.environ.get("R126_WITHDRAW", "")
    return [k for k, ok in V.items()
            if not ok or (withdraw and k.startswith(withdraw))]


def run_c() -> List[str]:
    """F 组：同源 live 重算 / 跨状态逐条对拍 / 注入反例 / 验锚 / A1 扫描面。"""
    d = load()
    A = d["accounts"]["A_status_def"]
    B = d["accounts"]["B_cross_status"]
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        withdraw = os.environ.get("R126_WITHDRAW", "")
        if withdraw and name.startswith(withdraw):
            cond = False
        if not cond:
            fails.append(name)

    # F1 状态面定义 AST live 重算，不读存档结论字段
    liveA = prss.account_a()
    ck("F1 状态面定义 AST live 重算==存档", liveA == A)
    ck("F1 同源 FRAME_KEYS 十词", prss.FRAME_KEYS == prss.pfrl.FRAME_KEYS
       and len(prss.FRAME_KEYS) == 10)

    # F2 跨状态 live 重算：从现成产物重扫 r118(fail) / r124(pass)，不读结论字段
    fail_live = prss._scan_target(prss._read_json_abs("/tmp/r118_full.json"))
    pass_live = prss._scan_target(prss._read_json_abs("/tmp/r124_full.json"))
    ck("F2 r118 fail 态 live 一致",
       fail_live["status"] == "fail"
       and fail_live["hits"] == ["selftest"]
       and fail_live["tail_len"] == 801
       and fail_live == B["per_run"]["r118"])
    ck("F2 r124 pass 态 live 一致",
       pass_live["status"] == "pass"
       and pass_live["hits"] == []
       and pass_live["tail_len"] == 200
       and pass_live == B["per_run"]["r124"])

    # F3 注入反例：pass 与 fail 命中签名不同（状态面有牙）；fail 命中非零（正例有牙）
    ck("F3 pass/fail 命中签名不同", set(fail_live["hits"]) != set(pass_live["hits"]))
    ck("F3 fail 态确有命中（反例有牙）", len(fail_live["hits"]) > 0)

    # F4 live 归因：fail tail 的 selftest 行是 self-report；pass 末4行不含
    fail_text = fail_live["tail_text"]
    slines = [ln for ln in fail_text.splitlines() if "selftest" in ln.lower()]
    mk = ("PASS", "绿", "⛔", "FAIL", "OK")
    ck("F4 fail selftest 行非零", len(slines) > 0)
    ck("F4 selftest 行全是 self-report",
       all(any(m in ln for m in mk) for ln in slines))
    ck("F4 pass 末4行不含 selftest",
       "selftest" not in pass_live["tail_last4"].lower())

    # F5 caliber hash 逐键核对（不读结论字段）
    cal = prss.caliber()
    ck("F5 caliber 键数一致", set(cal) == set(d["caliber"]))
    ck("F5 caliber 逐键 hash", all(cal[k] == d["caliber"][k] for k in cal))

    # F6 验锚同源
    ar = prss.anchor_report()
    ck("F6 验锚同源", ar["ok"] is True and ar["repos"] == 3
       and not ar["diffs"] and not ar["caliber_diffs"])

    # F7 A1 扫描面：新产物闸不进 discover 面
    sys.path.insert(0, str(ROOT))
    from aether.check import discover
    names = [p.name for p in discover(ROOT)]
    ck("F7 discover 不含 run_status_surface", not any("run_status_surface" in n for n in names))
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
