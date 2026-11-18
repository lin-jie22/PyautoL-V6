#!/usr/bin/env python3
"""r120 / README 199 契约：四份全量产物零帧级字段，「每次全量真跑」全称靠静态存档外推。

⛔ 本契约**不出现 `resolve_policy(...)` 的 AST 调用点**：192 尺子
`resolve_policy_calls_all()` 按全仓 AST 数该函数调用点，白名单只
排除自身；本文件若新增调用点会令 192 契约 F3/F6 分母漂移。

A–E 组是**纯函数**（只读存档，供反证逐条置 False / 篡改字段）；
F 组是本机实战（同源实算、逐字段、注入反例、验锚、A1 扫描面）。
合规形态与 r116–r119 同构：`run_c()` 内嵌 `def ck(name, cond, detail)`
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

import probe_full_run_ledger as pfl                       # noqa: E402
import probe_default_effect as pde                        # noqa: E402

CENSUS = ROOT / "scripts" / "full_run_ledger_census.jsonl"
PENDING = ["README.md", "scripts/probe_full_run_ledger.py",
           "scripts/full_run_ledger_census.jsonl",
           "scripts/full_run_ledger_contract.py"]
CONDITIONS = 19  # A6 + B6 + C6 + SELF1


def load() -> Dict[str, Any]:
    return json.loads(CENSUS.read_text(encoding="utf-8"))


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    A = d["accounts"]["A_artifact_layer"]
    B = d["accounts"]["B_archive_layer"]
    C = d["accounts"]["C_discrimination"]
    V: Dict[str, bool] = {}
    V["A1_four_available"] = A["n_available"] == 4
    V["A2_field_sets_identical"] = A["all_field_sets_identical"] is True
    V["A3_frame_fields_zero"] = A["all_frame_fields_zero"] is True
    V["A4_two_contracts_pass"] = A["two_contracts_all_pass"] is True
    V["A5_two_present_everywhere"] = A["two_contracts_present_everywhere"] is True
    V["A6_tail_frame_word_zero"] = A["tail_frame_word_zero"] is True
    V["B1_frame_in_archives"] = B["archive_has_frame_evidence"] is True
    V["B2_archive_mtime_earlier"] = all(B["archive_mtime_earlier_than_full"].values())
    V["B3_verify_absent_discover"] = B["verify_contracts_absent_from_discover"] is True
    V["B4_discover_contracts_present"] = len(B["discover_contracts_present"]) == 3
    V["B5_cross_run_universal"] = B["claim_is_cross_run_universal"] is True
    V["B6_four_runs_sampled"] = B["n_full_runs_sampled"] == 4
    V["C1_drop_two"] = C["accident_frames_drop_2"] is True
    V["C2_same_status_diff_frame"] = C["same_status_diff_frame"] is True
    V["C3_zero_discrimination"] = C["artifact_frame_zero_discrimination"] is True
    V["C4_static_sees_frame"] = C["static_archive_sees_frame"] is True
    V["C5_two_ledgers"] = C["count_vs_discrimination_two_ledgers"] is True
    V["C6_not_merged"] = C["merged_into_bool"] is False
    V["SELF_closure_all_true"] = (
        sum(d["closure"].values()) == len(d["closure"])
        and len(d["closure"]) == d["derived"]["n_closure"])
    assert len(V) == CONDITIONS, (len(V), CONDITIONS)
    return V


def verdict_from(V: Dict[str, bool]) -> List[str]:
    withdraw = os.environ.get("R120_WITHDRAW", "")
    return [k for k, ok in V.items()
            if not ok or (withdraw and k.startswith(withdraw))]


def run_c() -> List[str]:
    """F 组：同源实算 / 逐键字段 / 注入反例 / 静态边界。"""
    d = load()
    A = d["accounts"]["A_artifact_layer"]
    B = d["accounts"]["B_archive_layer"]
    C = d["accounts"]["C_discrimination"]
    D = d["accounts"]["D_boundary"]
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        withdraw = os.environ.get("R120_WITHDRAW", "")
        if withdraw and name.startswith(withdraw):
            cond = False
        if not cond:
            fails.append(name)

    # F1 同源实算：字段普查从四份产物 live 重算，不读存档结论字段
    per = A["per_run"]
    for tag, path in pfl.FULL_RUNS:
        got = per.get(tag, {})
        if not got.get("available"):
            continue
        raw = pfl._read_json_abs(path)
        runs = raw.get("runs") or []
        union = sorted({k for r in runs for k in r.keys()})
        ck("F1 %s 字段并集 live==存档" % tag,
           union == got["union_fields"]
           and pfl._frame_keys(union) == got["frame_fields"])
        ck("F1 %s counts live==存档" % tag, raw.get("counts") == got["counts"])

    # F2 逐字段：union 恰是七字段，帧词零命中
    expect = ["mode", "rc", "reason", "script", "seconds", "status", "tail"]
    ck("F2 产物字段并集==七字段（零帧字段）", A["field_union"] == expect)
    ck("F2 七字段无帧词", pfl._frame_keys(expect) == [])
    ck("F2 两契约 tail 无帧词", A["tail_frame_word_zero"] is True)
    ck("F2 status 非帧账（取值 pass/fail）", A["status_not_frame_ledger"] is True)

    # F3 注入反例：同 status 不同帧数 ⇒ 产物层零鉴别力
    w = C["worlds"]
    ck("F3 W0/W1 同 status", w["W0_real_full"]["status"] == w["W1_no_policy_stub_pass"]["status"])
    ck("F3 W0/W1 静态帧不同且 W1=0",
       w["W0_real_full"]["frame_from_static_archive"] == 8
       and w["W1_no_policy_stub_pass"]["frame_from_static_archive"] == 0)
    ck("F3 三世界产物帧读数皆 None（不可读）",
       all(v["frame_from_artifact"] is None for v in w.values()))
    ck("F3 短路事故帧 4→2 有牙", C["short_circuit_accident"]["n_baseline"] == 4
       and C["short_circuit_accident"]["n_runtime"] == 2
       and C["short_circuit_accident"]["teeth"] is True)
    ck("F3 次数账/鉴别账分开", C["count_vs_discrimination_two_ledgers"] is True)

    # F4 caliber 逐键磁盘 hash + 存档形态
    ck("F4 caliber 逐键磁盘 hash 对齐",
       len(d["caliber"]) == len(pfl.CALIBER_FILES)
       and all(d["caliber"][k] == pfl.sha256_file(k)
               for k in pfl.CALIBER_FILES))
    ck("F4 存档后缀非 .json", CENSUS.suffix == ".jsonl")

    # F5 A1 扫描面（四文件）+ 正例有牙
    from contract_portability_test import candidate_files, scan_text, read_text
    files = set(candidate_files() or [])
    in_surface = [p for p in PENDING if p in files]
    hits = sum(len(scan_text(read_text(ROOT / p) or "")) for p in PENDING)
    teeth = bool(scan_text("/Users/" + "someone" + "/V6"))
    ck("F5 A1 四文件在候选面 + 零命中 + 正例有牙",
       len(in_surface) == 4 and hits == 0 and teeth)

    # F6 验锚 / closure / 192 分母 / §183 零
    a = pfl.anchor_report()
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
    # F6 同源：验帧契约确实不在 discover 面（live 重算）
    names = {p.name if hasattr(p, "name") else str(p) for p in pfl.discover(str(ROOT))}
    ck("F6 验帧契约 live 不在 discover 面",
       not [n for n in names if n in pfl.VERIFY_CONTRACTS])
    return fails


def main() -> int:
    d = load()
    V = conditions(d)
    bad = verdict_from(V)
    print("=" * 74)
    print("r120 / 199  「每次全量真跑」全称：产物零帧字段、帧证据只在静态存档")
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
