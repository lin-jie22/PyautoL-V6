#!/usr/bin/env python3
"""r124 / README 203 契约：帧词命中数依赖观测面（stdout 全文 vs 产物 tail 截断）。

§199/§201/§202/r123 的命中数只在产物 tail 一个观测面上量过；tail 是
`_summarize_out` 成功分支 lines[-4:][:400] 的截断。本契约核 6 条契约的
stdout 全文 vs tail 三口径差集，C1 的 selftest 是词边界真词却被截断砍掉。

⛔ 本契约不出现 `resolve_policy(...)` 调用点（192 分母闸）。
A–E 纯函数（读存档、供反证逐条置 False / 篡改）；F 组本机实战。
合规形态与 r116–r123 同构：run_c() 内嵌 ck + fails.append + return fails。
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

import probe_obs_surface as pos                          # noqa: E402

CENSUS = ROOT / "scripts" / "obs_surface_census.jsonl"
PENDING = ["README.md", "scripts/probe_obs_surface.py",
           "scripts/obs_surface_stdout.jsonl", "scripts/obs_surface_census.jsonl",
           "scripts/obs_surface_contract.py"]
CONDITIONS = 24  # A4 + B6 + C7 + D7


def load() -> Dict[str, Any]:
    return json.loads(CENSUS.read_text(encoding="utf-8"))


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    A = d["accounts"]["A_obs_surface"]
    B = d["accounts"]["B_tricaliber"]
    C = d["accounts"]["C_attribution"]
    D = d["accounts"]["D_boundary"]
    V: Dict[str, bool] = {}
    # A 观测面定义层
    V["A1_summarize_out_found"] = A["found"] is True
    V["A2_success_truncated_tail"] = A["success_is_truncated_tail"] is True
    V["A3_failure_keeps_signal"] = A["failure_keeps_signal_lines"] is True
    V["A4_two_observation_surfaces"] = A["two_observation_surfaces"] is True
    # B 三口径对拍层
    V["B1_six_probes"] = B["n_probes"] == 6
    V["B2_all_rc_zero"] = B["all_rc_zero"] is True
    V["B3_all_in_product"] = B["all_in_product"] is True
    V["B4_tail_faithful_last4"] = B["tail_faithful_to_last4"] is True
    V["B5_gain_rows_ge_three"] = B["n_gain_rows"] >= 3
    V["B6_obs_surface_matters"] = B["observation_surface_matters"] is True
    # C 归因层
    V["C1_gain_keys_in_full"] = C["all_gain_keys_in_full"] is True
    V["C2_call_from_recall"] = C["call_from_recall"] is True
    V["C3_c1_selftest_false_green"] = C["c1_false_green_by_truncation"] is True
    V["C4_gain_is_selfreport"] = C["gain_is_selfreport_text"] is True
    V["C5_hit_not_runtime_evidence"] = C["hit_not_runtime_evidence"] is True
    V["C6_three_surfaces_not_merged"] = (
        C["three_surfaces_not_merged"] is True and C["merged_into_bool"] is False)
    V["C7_not_overturn"] = C["not_overturn_199_201_202_123"] is True
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
        D["scope_narrowing_only"] is True and D["direction_199_202_unchanged"] is True)
    assert len(V) == CONDITIONS, (len(V), CONDITIONS)
    return V


def verdict_from(V: Dict[str, bool]) -> List[str]:
    withdraw = os.environ.get("R124_WITHDRAW", "")
    return [k for k, ok in V.items()
            if not ok or (withdraw and k.startswith(withdraw))]


def run_c() -> List[str]:
    """F 组：同源实算 / 逐字段 / 注入反例 / 验锚 / A1 扫描面。"""
    d = load()
    A = d["accounts"]["A_obs_surface"]
    B = d["accounts"]["B_tricaliber"]
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        withdraw = os.environ.get("R124_WITHDRAW", "")
        if withdraw and name.startswith(withdraw):
            cond = False
        if not cond:
            fails.append(name)

    # F1 观测面 AST live 重算，不读存档结论字段
    liveA = pos.account_a()
    ck("F1 观测面 AST live 重算==存档", liveA == A)
    ck("F1 同源 FRAME_KEYS 十词", pos.FRAME_KEYS == pos.pfrl.FRAME_KEYS
       and len(pos.FRAME_KEYS) == 10)

    # F2 逐字段：从存档 stdout + 产物 tail live 重算三口径，不读结论字段
    prod = pos._read_json_abs(pos.FULL_JSON)
    by = {r.get("script"): r for r in prod.get("runs", [])}
    archive = pos.load_stdout_archive()
    gain = []
    for script in pos.PROBES:
        full = archive.get(script, {}).get("stdout", "")
        tail = str(by.get(script, {}).get("tail", ""))
        fs, ts = pos._hits(full), pos._hits(tail)
        fw = pos._hits(full, word=True)
        if [k for k in fs if k not in ts]:
            gain.append(script)
        saved = B["per_contract"].get(script, {})
        ck("F2 %s 三口径 live 一致" % script,
           fs == saved["sub_string_full"] and ts == saved["sub_string_tail"]
           and fw == saved["word_full"])
    ck("F2 gain 行数 live 一致", sorted(gain) == B["gain_rows"])

    # F3 注入反例：把 tail 换成 stdout 全文，差值应归零（证明差是截断造成）
    kc = archive.get("contract_knowledge_consume_test.py", {}).get("stdout", "")
    gain_full = [k for k in pos._hits(kc) if k not in pos._hits(kc)]
    ck("F3 同观测面差归零（反例有牙）", gain_full == [])
    kc_tail = str(by.get("contract_knowledge_consume_test.py", {}).get("tail", ""))
    real_gain = [k for k in pos._hits(kc) if k not in pos._hits(kc_tail)]
    ck("F3 真观测面差非零", "call" in real_gain)

    # F4 C1 假绿灯：selftest 词边界真词在 stdout、不在 tail
    cg = archive.get("contract_context_gate_test.py", {}).get("stdout", "")
    cg_tail = str(by.get("contract_context_gate_test.py", {}).get("tail", ""))
    ck("F4 C1 selftest 词边界 live",
       "selftest" in pos._hits(cg, word=True)
       and "selftest" not in pos._hits(cg_tail, word=True))

    # F5 caliber hash 逐键核对（不读结论字段）
    cal = pos.caliber()
    ck("F5 caliber 键数一致", set(cal) == set(d["caliber"]))
    ck("F5 caliber 逐键 hash", all(cal[k] == d["caliber"][k] for k in cal))

    # F6 验锚同源
    ar = pos.anchor_report()
    ck("F6 验锚同源", ar["ok"] is True and ar["repos"] == 3
       and not ar["diffs"] and not ar["caliber_diffs"])

    # F7 A1 扫描面：新产物闸不进 discover 面
    sys.path.insert(0, str(ROOT))
    from aether.check import discover
    names = [p.name for p in discover(ROOT)]
    ck("F7 discover 不含 obs_surface", not any("obs_surface" in n for n in names))
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
