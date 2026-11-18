#!/usr/bin/env python3
"""r121 / README 200 契约：产物零帧是结构必然 + 空壳过闸是实测事实。

⛔ 本契约**不出现 `resolve_policy(...)` 的 AST 调用点**：192 尺子按全仓 AST
数该函数调用点，白名单只排除自身；本文件若新增调用点会令 192 契约分母漂移。

A–E 组是**纯函数**（只读存档，供反证逐条置 False / 篡改字段）；
F 组是本机实战（同源实算、逐字段、注入反例、验锚、A1 扫描面）。
合规形态与 r116–r120 同构：`run_c()` 内嵌 `def ck(name, cond, detail)`
+ `fails.append` + `return fails`；`main()` 用 `allbad = bad + fbad`。
⛔ 条件按名撤回，保留 body 与缩进。
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

import probe_stub_gate as psg                            # noqa: E402

CENSUS = ROOT / "scripts" / "stub_gate_census.jsonl"
PENDING = ["README.md", "scripts/probe_stub_gate.py",
           "scripts/stub_gate_census.jsonl",
           "scripts/stub_gate_contract.py"]
CONDITIONS = 18  # A5 + B5 + C7 + SELF1


def load() -> Dict[str, Any]:
    return json.loads(CENSUS.read_text(encoding="utf-8"))


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    A = d["accounts"]["A_schema_structural"]
    B = d["accounts"]["B_measured_stubs"]
    C = d["accounts"]["C_discrimination"]
    V: Dict[str, bool] = {}
    V["A1_ast_live_identical"] = A["ast_live_identical"] is True
    V["A2_field_set_fixed_seven"] = A["field_set_fixed"] is True and A["n_fields"] == 7
    V["A3_zero_frame_structural"] = A["zero_frame_is_structural"] is True
    V["A4_pass_solely_rc_zero"] = A["pass_solely_rc_zero"] is True
    V["A5_no_profile_in_one"] = A["one_pass_branches"]["profile_calls_in_one"] == 0
    V["B1_all_stubs_pass"] = B["all_stubs_pass"] is True
    V["B2_all_stubs_zero_frame"] = B["all_stubs_zero_frame"] is True
    V["B3_stub_field_set_fixed"] = all(
        x["fields"] == ["mode", "rc", "reason", "script", "seconds", "status", "tail"]
        for x in (B["stub_exit0"], B["stub_tail_keep"], B["stub_tail_flood"]))
    V["B4_sentinel_visible_kept"] = (B["summarize_sentinel_visible_in_tail"] is True
                                     and B["measured_sentinel_visible_in_tail"] is True)
    V["B5_sentinel_buried_flooded"] = (B["summarize_sentinel_buried_in_tail"] is True
                                       and B["measured_sentinel_buried_in_tail"] is True)
    V["C1_w1_from_archive_pass"] = C["r120_w1_status_from_archive"] == "pass"
    V["C2_w1_measured_pass"] = C["measured_w1_status"] == "pass"
    V["C3_assumption_confirmed"] = C["w1_assumption_confirmed_by_measurement"] is True
    V["C4_tail_text_varies"] = C["tail_text_varies_visible_vs_buried"] is True
    V["C5_artifact_blind_tail"] = C["artifact_blind_to_tail_text"] is True
    V["C6_two_ledgers"] = C["count_vs_discrimination_two_ledgers"] is True
    V["C7_not_merged"] = C["merged_into_bool"] is False
    V["SELF_closure_all_true"] = (
        sum(d["closure"].values()) == len(d["closure"])
        and len(d["closure"]) == d["derived"]["n_closure"])
    assert len(V) == CONDITIONS, (len(V), CONDITIONS)
    return V


def verdict_from(V: Dict[str, bool]) -> List[str]:
    withdraw = os.environ.get("R121_WITHDRAW", "")
    return [k for k, ok in V.items()
            if not ok or (withdraw and k.startswith(withdraw))]


def run_c() -> List[str]:
    """F 组：同源实算 / 逐字段 / 注入反例 / 静态边界。"""
    d = load()
    A = d["accounts"]["A_schema_structural"]
    B = d["accounts"]["B_measured_stubs"]
    C = d["accounts"]["C_discrimination"]
    D = d["accounts"]["D_boundary"]
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        withdraw = os.environ.get("R121_WITHDRAW", "")
        if withdraw and name.startswith(withdraw):
            cond = False
        if not cond:
            fails.append(name)

    # F1 同源实算：Run.to_dict 的 AST 键与 live 键各自重算，不读存档结论字段
    ck("F1 to_dict AST 键 live 重算==存档",
       psg.to_dict_keys_ast() == A["to_dict_keys_ast"])
    ck("F1 to_dict live 键 live 重算==存档",
       psg.to_dict_keys_live() == A["to_dict_keys_live"])
    ck("F1 _one pass 分支 live 重算==存档",
       psg.one_pass_branches_ast() == A["one_pass_branches"])

    # F2 逐字段：七字段固定、零帧、唯一 rc==0
    expect = ["mode", "rc", "reason", "script", "seconds", "status", "tail"]
    ck("F2 七字段固定零帧", A["to_dict_keys_live"] == expect
       and A["frame_fields_in_schema"] == [])
    ck("F2 pass 唯一 rc==0 且无 profile", A["pass_solely_rc_zero"] is True
       and A["one_pass_branches"]["profile_calls_in_one"] == 0)

    # F3 实测空壳：live 重跑三个 stub，判据层与存档读数一致（不读结论字段）
    for tag, path in (("exit0", psg.STUB_EXIT0),
                      ("tail_keep", psg.STUB_TAILKEEP),
                      ("tail_flood", psg.STUB_TAILFLOOD)):
        live = psg.probe_one(path)
        saved = B["stub_" + tag]
        ck("F3 %s live 重跑==存档读数" % tag,
           live["status"] == saved["status"] and live["rc"] == saved["rc"]
           and live["fields"] == saved["fields"]
           and live["tail_has_sentinel"] == saved["tail_has_sentinel"])
    ck("F3 三空壳全 pass 零帧字段", B["all_stubs_pass"] is True
       and B["all_stubs_zero_frame"] is True)

    # F4 注入反例：W1 手填假设被实测确认；tail 可见 vs 淹没；产物零鉴别力
    ck("F4 W1 存档 pass == 实测 pass", C["r120_w1_status_from_archive"] == "pass"
       and C["measured_w1_status"] == "pass")
    ck("F4 tail 文本可见/淹没有别", C["tail_text_varies_visible_vs_buried"] is True)
    ck("F4 产物对 tail 文本零鉴别力", C["artifact_blind_to_tail_text"] is True)
    ck("F4 次数账/鉴别账分开未合并", C["count_vs_discrimination_two_ledgers"] is True
       and C["merged_into_bool"] is False)

    # F5 caliber 逐键磁盘 hash + 存档形态 + A1 扫描面
    ck("F5 caliber 逐键磁盘 hash 对齐",
       len(d["caliber"]) == len(psg.CALIBER_FILES)
       and all(d["caliber"][k] == psg.sha256_file(k) for k in psg.CALIBER_FILES))
    ck("F5 存档后缀非 .json", CENSUS.suffix == ".jsonl")
    from contract_portability_test import candidate_files, scan_text, read_text
    files = set(candidate_files() or [])
    in_surface = [p for p in PENDING if p in files]
    hits = sum(len(scan_text(read_text(ROOT / p) or "")) for p in PENDING)
    teeth = bool(scan_text("/Users/" + "someone" + "/V6"))
    ck("F5 A1 四文件在候选面 + 零命中 + 正例有牙",
       len(in_surface) == 4 and hits == 0 and teeth)

    # F6 验锚 / closure / 192 分母 / 193 未漂移 / §183 零
    a = psg.anchor_report()
    ck("F6 验锚 OK 3/3 + closure 全绿 + §183 零",
       a["ok"] is True and a["repos"] == 3
       and not a["diffs"] and not a["caliber_diffs"]
       and D["section_183_touched"] is False
       and D["real_expr_run"] == 0 and D["real_commit_run"] == 0
       and D["gain_rows_reported"] == 0 and D["product_files_modified"] == 0)
    ck("F6 三分母双源 135/119/94",
       D["denominator_dual_source"]["main"] == 135
       and D["denominator_dual_source"]["form"] == 119
       and D["denominator_dual_source"]["no_result"] == 94
       and d["closure"]["d1b_dual_source"] is True)
    ck("F6 superseded 全匹配", D["superseded"]["all_match"] is True)
    ck("F6 r120 存档 W1 读数 live 对齐",
       D["r120_archive_available"] is True
       and D["r120_w1_status_from_archive"] == C["r120_w1_status_from_archive"])
    return fails


def main() -> int:
    d = load()
    V = conditions(d)
    bad = verdict_from(V)
    print("=" * 74)
    print("r121 / 200  产物零帧是结构必然 + 空壳过闸是实测事实")
    print("A–E %d 条：%d 通过 / %d 未通过" % (len(V), len(V) - len(bad), len(bad)))
    for k in bad:
        print("  ✗", k)
    fbad = run_c()
    print("F 组 %d 条未通过" % len(fbad))
    for k in fbad:
        print("  ✗", k)
    allbad = bad + fbad
    print("=" * 74)
    if allbad:
        print("❌ 共 %d 项未通过" % len(allbad))
        return 1
    print("✅ 全部通过（A–E %d + F 组）" % len(V))
    return 0


if __name__ == "__main__":
    sys.exit(main())
