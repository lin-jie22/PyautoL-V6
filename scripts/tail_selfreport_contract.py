#!/usr/bin/env python3
"""r122 / README 201 契约：§199「tail 帧词命中 0」作用域只覆盖 C1/C2，
首次普查全体 129 份 tail，命中 3 条都是 self-report 文案、非运行证据。

⛔ 本契约**不出现 `resolve_policy(...)` 的 AST 调用点**：192 尺子按全仓 AST
数该函数调用点，白名单只排除自身；本文件若新增调用点会令 192 契约分母漂移。

A–E 组是**纯函数**（只读存档，供反证逐条置 False / 篡改字段）；
F 组是本机实战（同源实算、逐字段、注入反例、验锚、A1 扫描面）。
合规形态与 r116–r121 同构：`run_c()` 内嵌 `def ck(name, cond, detail)`
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

import probe_tail_selfreport as pts                      # noqa: E402

CENSUS = ROOT / "scripts" / "tail_selfreport_census.jsonl"
PENDING = ["README.md", "scripts/probe_tail_selfreport.py",
           "scripts/tail_selfreport_census.jsonl",
           "scripts/tail_selfreport_contract.py"]
CONDITIONS = 17  # A4 + B5 + C5 + D2 + SELF1


def load() -> Dict[str, Any]:
    return json.loads(CENSUS.read_text(encoding="utf-8"))


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    A = d["accounts"]["A_scope"]
    B = d["accounts"]["B_tail_hits"]
    C = d["accounts"]["C_attribution"]
    V: Dict[str, bool] = {}
    # A 作用域层
    V["A1_scope_two_contracts"] = A["scope_is_two_contracts"] is True
    V["A2_scope_two_of_129"] = A["scope_two_of_129"] is True
    V["A3_scope_from_ast_not_text"] = (
        A["ledger_tail_assertion_scope"]["tail_scope_two_contracts"] is True
        and len(A["ledger_tail_assertion_scope"]["scope_predicates"]) == 1)
    V["A4_c1c2_are_the_two"] = sorted(A["c1c2"]) == sorted(
        ["contract_context_gate_test.py", "contract_inliner_relax_test.py"])
    # B 全体命中层
    V["B1_six_products_available"] = B["n_available"] == 6
    V["B2_clean_129_red_130"] = (
        B["all_runs_129"] is True and B["red_run_exceptions"] == {"r118": 130})
    V["B3_tail_all_str"] = B["tail_all_str"] is True
    V["B4_stable_three_hits"] = B["n_stable_hits"] == 3
    V["B5_c1c2_zero_non_c1c2_present"] = (
        B["c1c2_hit_count"] == 0 and B["non_c1c2_hit_count"] > 0)
    # C 归因层
    V["C1_all_expected_contracts_hit"] = C["all_expected_contracts_hit"] is True
    V["C2_unexpected_only_red_run"] = C["unexpected_only_red_run"] is True
    V["C3_c1c2_zero_in_full"] = C["c1c2_tail_frame_word_zero"] is True
    V["C4_all_hits_selfreport_text"] = C["all_hits_are_selfreport_text"] is True
    V["C5_two_ledgers_not_merged"] = (
        C["hit_is_not_runtime_evidence"] is True
        and C["two_ledgers_separate"] is True and C["merged_into_bool"] is False)
    # D 边界层
    D = d["accounts"]["D_boundary"]
    V["D1_section183_zero"] = (
        D["section_183_touched"] is False and D["real_expr_run"] == 0
        and D["real_commit_run"] == 0 and D["main_repo_run"] == 0
        and D["gain_rows_reported"] == 0 and D["product_files_modified"] == 0)
    V["D2_direction_unchanged"] = (
        D["scope_narrowing_only"] is True and D["direction_199_200_unchanged"] is True)
    V["SELF_closure_all_true"] = (
        sum(d["closure"].values()) == len(d["closure"])
        and len(d["closure"]) == d["derived"]["n_closure"])
    assert len(V) == CONDITIONS, (len(V), CONDITIONS)
    return V


def verdict_from(V: Dict[str, bool]) -> List[str]:
    withdraw = os.environ.get("R122_WITHDRAW", "")
    return [k for k, ok in V.items()
            if not ok or (withdraw and k.startswith(withdraw))]


def run_c() -> List[str]:
    """F 组：同源实算 / 逐字段 / 注入反例 / 静态边界。"""
    d = load()
    A = d["accounts"]["A_scope"]
    B = d["accounts"]["B_tail_hits"]
    C = d["accounts"]["C_attribution"]
    D = d["accounts"]["D_boundary"]
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        withdraw = os.environ.get("R122_WITHDRAW", "")
        if withdraw and name.startswith(withdraw):
            cond = False
        if not cond:
            fails.append(name)

    # F1 同源实算：作用域 AST 重算，不读存档结论字段
    scope = pts.ledger_scope_ast_v2()
    ck("F1 作用域 AST live 重算==存档",
       scope["tail_scope_two_contracts"] is True
       and scope == A["ledger_tail_assertion_scope"])
    ck("F1 C1/C2 常量 live 重算==存档",
       scope["constants"].get("C1") == "contract_context_gate_test.py"
       and scope["constants"].get("C2") == "contract_inliner_relax_test.py")

    # F2 逐字段：全体命中逐条 live 重扫六份产物，核对 signature（不读结论字段）
    for tag, path in pts.FULL_RUNS:
        saved = B["per_run"].get(tag, {})
        if not saved.get("available"):
            ck("F2 %s 产物可用" % tag, False)
            continue
        dd = pts._read_json_abs(path)
        hits = sorted((h["script"], h["keys"])
                      for r in (dd.get("runs") or [])
                      for h in [{"script": r.get("script", ""),
                                 "keys": ",".join(sorted(
                                     k for k in pts.FRAME_KEYS
                                     if k in str(r.get("tail", "")).lower()))}]
                      if h["keys"])
        ck("F2 %s 命中 live 重扫==存档" % tag,
           hits == [tuple(x) for x in saved["hit_signature"]]
           and len(dd.get("runs") or []) == saved["n_runs"])
    ck("F2 稳定命中恒 3 条",
       B["n_stable_hits"] == 3
       and len(B["stable_hit_signature"]) == 3)

    # F3 归因：命中词对回契约源码 PASS 文案（同源，不读结论字段）
    for script, key in pts.EXPECTED_SELFREPORT.items():
        src = pts._contract_src(script)
        in_pass = any(key in ln.lower()
                      and ("pass" in ln.lower() or "✓" in ln or "⛔" in ln)
                      for ln in src.splitlines())
        ck("F3 %s 命中词在源码 PASS 文案" % script, in_pass and bool(src))
    ck("F3 c1c2 在产物 tail 命中为 0（§199 方向不变）",
       B["c1c2_hit_count"] == 0)

    # F4 注入反例：命中 ≠ 运行证据；两账不合并
    ck("F4 命中是自报文案非运行证据", C["hit_is_not_runtime_evidence"] is True
       and C["all_hits_are_selfreport_text"] is True)
    ck("F4 tail 文本账/帧运行账分开未合并",
       C["two_ledgers_separate"] is True and C["merged_into_bool"] is False)

    # F5 caliber 逐键磁盘 hash + 存档形态 + A1 扫描面
    ck("F5 caliber 逐键磁盘 hash 对齐",
       len(d["caliber"]) == len(pts.CALIBER_FILES)
       and all(d["caliber"][k] == pts.sha256_file(k) for k in pts.CALIBER_FILES))
    ck("F5 存档后缀非 .json", CENSUS.suffix == ".jsonl")
    from contract_portability_test import candidate_files, scan_text, read_text
    files = set(candidate_files() or [])
    in_surface = [p for p in PENDING if p in files]
    hits_scan = sum(len(scan_text(read_text(ROOT / p) or "")) for p in PENDING)
    teeth = bool(scan_text("/Users/" + "someone" + "/V6"))
    ck("F5 A1 四文件在候选面 + 零命中 + 正例有牙",
       len(in_surface) == 4 and hits_scan == 0 and teeth)

    # F6 验锚 / closure / 192 分母 / 193 未漂移 / §183 零
    a = pts.anchor_report()
    sc = D["selfcheck"]
    ck("F6 验锚 OK 3/3 + closure 全绿 + §183 零",
       a["ok"] is True and a["repos"] == 3
       and not a["diffs"] and not a["caliber_diffs"]
       and D["section_183_touched"] is False
       and D["real_expr_run"] == 0 and D["real_commit_run"] == 0
       and D["gain_rows_reported"] == 0 and D["product_files_modified"] == 0)
    ck("F6 三分母双源 135/119/94",
       D["denominator_dual_source"]["main"] == 135
       and D["denominator_dual_source"]["form"] == 119
       and D["denominator_dual_source"]["no_result"] == 94)
    ck("F6 superseded 全匹配", D["superseded"]["all_match"] is True)
    ck("F6 新文件零 resolve_policy 调用点 + 存档非 json + 契约在 scripts/",
       sc["new_files_resolve_policy_calls"] == 0
       and sc["self_archive_not_json"] is True
       and sc["new_contract_in_scripts_not_tests"] is True)
    return fails


def main() -> int:
    d = load()
    V = conditions(d)
    bad = verdict_from(V)
    print("=" * 74)
    print("r122 / 201  tail 帧词命中 0 作用域 2/129 + 全体 3 条 self-report")
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
