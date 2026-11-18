#!/usr/bin/env python3
"""r125 / README 204 契约：帧词命中数依赖字段选择面（产物 tail vs 元数据字段）。

§199–§203/r124 的命中数只在 `runs[].tail` 一个字段面上量过。产物还有
`runs[].script`（契约文件名）、顶层 `undeclared_modes` / `e2e_not_supported`
等元数据字段面，从没对同源 FRAME_KEYS 做过命中扫描。本契约核 9 份产物的
四字段面对拍，元数据面命中逐条溯源到契约文件名常量（比 self-report 更弱）。

⛔ 本契约不出现 `resolve_policy(...)` 调用点（192 分母闸）。
A–D 纯函数（读存档、供反证逐条置 False / 篡改）；F 组本机实战。
合规形态与 r116–r124 同构：run_c() 内嵌 ck + fails.append + return fails。
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

import probe_field_surface as pfs                         # noqa: E402

CENSUS = ROOT / "scripts" / "field_surface_census.jsonl"
PENDING = ["README.md", "scripts/probe_field_surface.py",
           "scripts/field_surface_contract.py", "scripts/field_surface_census.jsonl"]
CONDITIONS = 24  # A4 + B6 + C7 + D7


def load() -> Dict[str, Any]:
    return json.loads(CENSUS.read_text(encoding="utf-8"))


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    A = d["accounts"]["A_field_def"]
    B = d["accounts"]["B_four_surfaces"]
    C = d["accounts"]["C_attribution"]
    D = d["accounts"]["D_boundary"]
    V: Dict[str, bool] = {}
    # A 字段面定义层
    V["A1_run_class_found"] = A["run_class_found"] is True
    V["A2_to_dict_found"] = A["to_dict_found"] is True
    V["A3_seven_to_dict_keys"] = A["n_to_dict_keys"] == 7
    V["A4_tail_not_only_surface"] = A["tail_not_only_surface"] is True
    # B 四面对拍层
    V["B1_nine_products"] = len(B["per_run"]) == 9
    V["B2_tail_clean_three"] = B["clean_tail_is_three"] is True
    V["B3_red_tail_four"] = B["red_tail_is_four"] is True
    V["B4_meta_stable_across_clean"] = (
        B["meta_stable_across_clean_runs"] is True and B["n_clean_runs"] == 8)
    V["B5_four_fields"] = B["fields"] == list(pfs.FIELDS)
    V["B6_field_surface_matters"] = B["field_surface_matters"] is True
    # C 归因层
    V["C1_meta_rows_nonzero"] = C["n_meta_hit_rows"] > 0
    V["C2_meta_are_filename_constants"] = C["all_meta_hits_are_filename_constants"] is True
    V["C3_tail_meta_disjoint"] = C["tail_and_meta_scripts_disjoint"] is True
    V["C4_meta_not_runtime_output"] = C["meta_hits_not_runtime_output"] is True
    V["C5_meta_not_selfreport"] = C["meta_hits_not_selfreport"] is True
    V["C6_four_surfaces_not_merged"] = (
        C["four_surfaces_not_merged"] is True and C["merged_into_bool"] is False)
    V["C7_not_overturn"] = C["not_overturn_199_201_202_203_124"] is True
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
    withdraw = os.environ.get("R125_WITHDRAW", "")
    return [k for k, ok in V.items()
            if not ok or (withdraw and k.startswith(withdraw))]


def run_c() -> List[str]:
    """F 组：同源 live 重算 / 逐字段溯源 / 注入反例 / 验锚 / A1 扫描面。"""
    d = load()
    A = d["accounts"]["A_field_def"]
    B = d["accounts"]["B_four_surfaces"]
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        withdraw = os.environ.get("R125_WITHDRAW", "")
        if withdraw and name.startswith(withdraw):
            cond = False
        if not cond:
            fails.append(name)

    # F1 字段骨架 AST live 重算，不读存档结论字段
    liveA = pfs.account_a()
    ck("F1 字段骨架 AST live 重算==存档", liveA == A)
    ck("F1 同源 FRAME_KEYS 十词", pfs.FRAME_KEYS == pfs.pfrl.FRAME_KEYS
       and len(pfs.FRAME_KEYS) == 10)

    # F2 逐字段面 live 重算：从现成产物重扫 r124，不读结论字段
    prod = pfs._read_json_abs("/tmp/r124_full.json")
    liveB = pfs._scan_product(prod)
    saved = B["per_run"]["r124"]
    for field in pfs.FIELDS:
        ck("F2 r124 %s 面 live 一致" % field,
           liveB[field] == saved[field])
    ck("F2 r124 n_runs live 一致", liveB["n_runs"] == saved["n_runs"])

    # F3 注入反例：tail 面与 script 面命中脚本集合不重合（正例有牙）
    tail_scripts = set(liveB["tail"].keys())
    script_scripts = set(liveB["script"].keys())
    ck("F3 tail 面与 script 面脚本不重合", tail_scripts.isdisjoint(script_scripts))
    ck("F3 script 面确有命中（反例有牙）", len(script_scripts) > 0)

    # F4 元数据面命中逐条 live 溯源文件名常量
    for field in pfs.META_FIELDS:
        for name, words in liveB[field].items():
            for w in words:
                ck("F4 %s/%s 文件名子串" % (field, w),
                   w in name.lower() and name.startswith("contract_"))

    # F5 caliber hash 逐键核对（不读结论字段）
    cal = pfs.caliber()
    ck("F5 caliber 键数一致", set(cal) == set(d["caliber"]))
    ck("F5 caliber 逐键 hash", all(cal[k] == d["caliber"][k] for k in cal))

    # F6 验锚同源
    ar = pfs.anchor_report()
    ck("F6 验锚同源", ar["ok"] is True and ar["repos"] == 3
       and not ar["diffs"] and not ar["caliber_diffs"])

    # F7 A1 扫描面：新产物闸不进 discover 面
    sys.path.insert(0, str(ROOT))
    from aether.check import discover
    names = [p.name for p in discover(ROOT)]
    ck("F7 discover 不含 field_surface", not any("field_surface" in n for n in names))
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
