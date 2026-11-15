#!/usr/bin/env python3
"""r116 / README 195 契约：⛔ 194 鉴别账的覆盖比与第五类真跑的鉴别力边界。

钉死四本账（不合并成布尔）：
  A 调用点分层  4 位开关 / 4 读取函数 / 3 sinks / selftest 覆盖 2 /
                194 事故 patch 目标同源抽取 / 覆盖比 2/3 → 3/3
  B 运行读数    真跑 selftest 计数器 2 次 / 返回对象 0 次流入 /
                4 处硬编码常量 / 5 类事故 4 打红 1 恒绿
  C 鉴别力      反事实四世界：3 个零世界全已运行、默认态混合、
                无自然正例 ⇒ 0 次流入是构造必然、不具鉴别力
  D 边界        三分母双源 / superseded / 验锚 / §183 零触碰 /
                本轮 2 文件不新增 `resolve_policy` AST 调用点 /
                存档非 `.json`（r115 已登记开口）
  E derived/summary/closure 完整性

⛔⛔ 本契约**不出现 `resolve_policy(...)` 的 AST 调用点**：
192/194 尺子按全仓 AST 数该函数调用点，且只把历轮自身量具/契约
列入白名单；本文件新增调用点会令分母漂移、F3/F6 真红（r114 已踩）。
一律用 `getattr(CG, "RELAXED")` / `setattr(CG, "resolve_policy", ...)`
达成语义等价。

⛔ 纯只读取证契约：不跑真实 expr、不跑真实 commit、不跑
`main() --repo`、不碰 §183；不修改 189–194 尺子 / 存档 / 判据。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]          # ⛔ 第 99 条：根从 __file__ 推导
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import probe_policy_binding as ppb                            # noqa: E402

ARCH = ROOT / "scripts" / "policy_binding_census.jsonl"
SELF_ARCHIVE_NAME = ARCH.name

PENDING = ["README.md", "scripts/probe_policy_binding.py",
           "scripts/policy_binding_census.jsonl",
           "tests/contract_policy_binding_test.py"]

_ANCHOR: Dict[str, Any] | None = None


def load() -> Dict[str, Any]:
    return json.loads(ARCH.read_text(encoding="utf-8"))


def anchor_cached() -> Dict[str, Any]:
    global _ANCHOR
    if _ANCHOR is None:
        _ANCHOR = ppb.anchor_report()
    return dict(_ANCHOR)


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    """A–E 静态条件（只读存档）。⛔ 每条都要能被注入反证逐条撤回命中。"""
    A = d["accounts"]["A_call_site_strata"]
    B = d["accounts"]["B_runtime_readouts"]
    C = d["accounts"]["C_discrimination"]
    D = d["accounts"]["D_boundary"]
    W = C["counterfactual"]
    acc = B["accidents"]
    flow = B["policy_object_flow"]
    run = B["evaluation_account"]
    chk = D["sandbox_selfcheck"]
    cal = d["caliber"]
    der = d["derived"]
    summ = d["summary"]
    V: Dict[str, bool] = {}

    # ── A 账 ──
    V["A1"] = d["schema"] == "policy_binding_census/v1"
    V["A2"] = A["n_fields"] == 4
    V["A3"] = A["policy_fields"] == ["cross_scope", "first_rhs",
                                     "allow_unstable", "no_gold_gate"]
    V["A4"] = A["n_field_reads"] == 4
    V["A5"] = A["field_read_functions"] == ["adopt_probe", "bindings_for",
                                            "main", "merge_bindings"]
    V["A6"] = A["field_reads"]["cross_scope"] == [{"fn": "merge_bindings", "line": 189}]
    V["A7"] = A["field_reads"]["first_rhs"] == [{"fn": "bindings_for", "line": 203}]
    V["A8"] = A["field_reads"]["allow_unstable"] == [{"fn": "main", "line": 464}]
    V["A9"] = A["field_reads"]["no_gold_gate"] == [{"fn": "adopt_probe", "line": 257}]
    V["A10"] = A["main_policy_sinks"]["n_sinks"] == 3
    V["A11"] = A["main_policy_sinks"]["sinks"] == ["adopt_probe", "bindings_for",
                                                   "merge_bindings"]
    V["A12"] = A["main_policy_sinks"]["run_all_has_policy"] is False
    V["A13"] = A["main_policy_sinks"]["source"].endswith("::main (AST)")
    V["A14"] = A["n_selftest_covered_fields"] == 2
    V["A15"] = A["selftest_covered_field_functions"] == ["bindings_for",
                                                         "merge_bindings"]
    V["A16"] = A["selftest_uncovered_field_functions"] == ["adopt_probe", "main"]
    V["A17"] = A["accident_194"]["n_targets"] == 4
    V["A18"] = A["accident_194"]["targets"] == ["resolve_policy", "resolve_policy",
                                                "adopt_probe", "bindings_for"]
    V["A19"] = (A["accident_194"]["teeth"] is True
                and A["accident_194"]["pos_n"] == 3
                and A["accident_194"]["neg_n"] == 0)
    V["A20"] = A["accident_194"]["source"].endswith("::account_c (AST)")
    V["A21"] = A["sinks_covered_by_194"] == ["adopt_probe", "bindings_for"]
    V["A22"] = A["coverage_ratio_194"] == "2/3"
    V["A23"] = A["sinks_covered_this_round"] == ["adopt_probe", "bindings_for",
                                                 "merge_bindings"]
    V["A24"] = A["coverage_ratio_this"] == "3/3"
    V["A25"] = A["n_call_sites_192_from_archive"] == 5
    V["A26"] = A["merged_into_bool"] is False
    V["A27"] = "读取点存在 ≠ 运行时走到" in A["boundary"]
    # ⛔ 反证阶段 2 的 MISS 补强：原始读数也要逐字段被看着，
    #    不能只靠聚合结论（r115「聚合相同 ≠ 逐行一致」）
    V["A28"] = (
        "resolve_policy" in A["selftest_called_functions"]
        and "merge_bindings" in A["selftest_called_functions"]
        and "bindings_for" in A["selftest_called_functions"]
        and "adopt_probe" not in A["selftest_called_functions"])
    V["A29"] = A["main_policy_sinks"]["run_all_args"] == [
        "source", "items", "timeout", "pkg"]
    V["A30"] = (A["accident_194"]["pos_hit"] is True
                and A["accident_194"]["neg_hit"] is True)
    V["A31"] = A["n_sinks_covered_by_194"] == 2
    V["A32"] = A["n_sinks_covered_this_round"] == 3

    # ── B 账 ──
    V["B1"] = B["ast_runtime_match"] is True
    V["B2"] = run["n_resolve_policy_actual"] == 2
    V["B3"] = run["selftest_ok"] is True
    V["B4"] = run["n_assertion_lines"] == 15
    V["B5"] = run["n_pass_marks"] == 15
    V["B6"] = run["n_fail_marks"] == 0
    V["B7"] = run["signatures_seen"] == ["1111", "0000"]
    V["B8"] = "setattr 计数器" in run["source"]
    V["B9"] = flow["n_resolve_policy_ast"] == 2
    V["B10"] = flow["rp_lines"] == [311, 312]
    V["B11"] = flow["n_flowed_into_others"] == 0
    V["B12"] = flow["flowed_lines"] == []
    V["B13"] = flow["n_attribute_only"] == 2
    V["B14"] = flow["attribute_only_lines"] == [311, 312]
    V["B15"] = flow["only_signature_attribute"] is True
    V["B16"] = flow["n_hardcoded_constants"] == 4
    V["B17"] = flow["hardcoded_lines"] == [315, 316, 320, 322]
    V["B18"] = B["n194_per_full_lower_bound"] == 6
    V["B19"] = B["n194_selftest_ast"] == 2
    V["B20"] = len(acc["accidents"]) == 5
    V["B21"] = acc["n_caught"] == 4
    V["B22"] = acc["n_blind"] == 1
    V["B23"] = acc["caught"] == ["A_strict_broken", "B_default_0000",
                                 "D_bindings_for_ignores_policy",
                                 "E_merge_bindings_ignores_policy"]
    V["B24"] = acc["blind"] == ["C_adopt_probe_ignores_policy"]
    V["B25"] = acc["positive_has_teeth"] is True
    V["B26"] = acc["new_sink_caught"] is True
    V["B27"] = acc["accidents"]["E_merge_bindings_ignores_policy"]["n_fail_marks"] == 2
    V["B28"] = len(acc["accidents"]["E_merge_bindings_ignores_policy"]["red_lines"]) == 2
    V["B29"] = acc["orig4_caught"] == ["A_strict_broken", "B_default_0000",
                                       "D_bindings_for_ignores_policy"]
    V["B30"] = acc["orig4_blind"] == ["C_adopt_probe_ignores_policy"]
    V["B31"] = acc["still_only_adopt_blind"] is True
    V["B32"] = acc["accidents"]["C_adopt_probe_ignores_policy"]["n_fail_marks"] == 0
    V["B33"] = acc["accidents"]["C_adopt_probe_ignores_policy"]["selftest_ok"] is True
    V["B34"] = "求值账" in B["verdict"]
    V["B35"] = B["merged_into_bool"] is False
    # ⛔ 事故逐行 outcome 对拍：聚合 `n_caught=4` 会被内容篡改骗过
    accs = acc["accidents"]
    V["B36"] = flow["source"].endswith("::selftest (AST)")
    V["B37"] = all(accs[k]["selftest_ok"] is False for k in acc["caught"])
    V["B38"] = all(accs[k]["n_fail_marks"] >= 1 for k in acc["caught"])
    V["B39"] = all(len(accs[k]["red_lines"]) == accs[k]["n_fail_marks"]
                   for k in acc["caught"])
    V["B40"] = all(accs[k]["selftest_ok"] is True
                   and accs[k]["n_fail_marks"] == 0
                   and accs[k]["red_lines"] == [] for k in acc["blind"])
    V["B41"] = {k: accs[k]["target"] for k in accs} == {
        "A_strict_broken": "resolve_policy",
        "B_default_0000": "resolve_policy",
        "C_adopt_probe_ignores_policy": "adopt_probe",
        "D_bindings_for_ignores_policy": "bindings_for",
        "E_merge_bindings_ignores_policy": "merge_bindings"}
    V["B42"] = all(isinstance(accs[k]["description"], str)
                   and bool(accs[k]["description"]) for k in accs)
    V["B43"] = acc["orig4"] == [
        "A_strict_broken", "B_default_0000",
        "C_adopt_probe_ignores_policy", "D_bindings_for_ignores_policy"]
    V["B44"] = acc["merged_into_bool"] is False

    # ── C 账 ──
    V["C1"] = len(W["worlds"]) == 4
    V["C2"] = W["n_zero_worlds"] == 3
    V["C3"] = W["zero_worlds"] == ["W0_present", "W1_default_broken", "W3_main_ran"]
    V["C4"] = W["n_ran_zero_worlds"] == 3
    V["C5"] = W["ran_zero_worlds"] == W["zero_worlds"]
    V["C6"] = set(W["intact_values_in_zero"]) == {True, False}
    V["C7"] = W["n_natural_positive_worlds"] == 0
    V["C8"] = W["discriminates"] is False
    V["C9"] = W["positive_has_teeth"] is True
    V["C10"] = W["worlds"]["W0_present"]["n_flowed"] == 0
    V["C11"] = W["worlds"]["W1_default_broken"]["default_intact"] is False
    V["C12"] = W["worlds"]["W1_default_broken"]["ran_selftest"] is True
    V["C13"] = W["worlds"]["W2_injected_binding"]["n_flowed"] == 1
    V["C14"] = W["worlds"]["W2_injected_binding"]["synthetic"] is True
    V["C15"] = W["worlds"]["W3_main_ran"]["n_flowed"] == 0
    V["C16"] = W["worlds"]["W3_main_ran"]["synthetic"] is True
    V["C17"] = W["worlds"]["W0_present"]["synthetic"] is False
    V["C18"] = "构造必然" in W["verdict"] and "不具鉴别力" in W["verdict"]
    V["C19"] = "合成反事实" in W["boundary"]
    V["C20"] = C["accident_coverage_194"] == "2/3"
    V["C21"] = C["accident_coverage_this"] == "3/3"
    V["C22"] = C["new_sink_caught"] is True
    V["C23"] = C["positive_has_teeth"] is True
    V["C24"] = C["still_only_adopt_blind"] is True
    V["C25"] = "不翻案" in C["verdict"]
    V["C26"] = C["merged_into_bool"] is False
    Ws = W["worlds"]
    V["C27"] = (Ws["W0_present"]["default_intact"] is True
                and Ws["W0_present"]["ran_selftest"] is True)
    V["C28"] = (Ws["W1_default_broken"]["n_flowed"] == 0
                and Ws["W1_default_broken"]["synthetic"] is False)
    V["C29"] = (Ws["W2_injected_binding"]["default_intact"] is True
                and Ws["W2_injected_binding"]["ran_selftest"] is True)
    V["C30"] = (Ws["W3_main_ran"]["default_intact"] is True
                and Ws["W3_main_ran"]["ran_selftest"] is True)
    V["C31"] = all(isinstance(v["description"], str) and bool(v["description"])
                   for v in Ws.values())

    # ── D 账 ──
    V["D1"] = D["policy_default"] == "1111"
    V["D2"] = D["policy_strict"] == "0000"
    V["D3"] = D["denominators"] == {"main": 135, "form": 119, "no_result": 94}
    V["D4"] = D["denominator_dual_source"]["main_rows"] == 135
    V["D5"] = D["denominator_dual_source"]["main_summary"] == 135
    V["D6"] = D["denominator_dual_source"]["form_rows"] == 119
    V["D7"] = D["denominator_dual_source"]["form_summary"] == 119
    V["D8"] = D["denominator_dual_source"]["no_result_rows"] == 94
    V["D9"] = D["denominator_dual_source"]["no_result_summary"] == 94
    V["D10"] = D["denominators_all_distinct"] is True
    V["D11"] = D["superseded_total"] == 3
    V["D12"] = D["superseded_sha_matches"] == 3
    V["D13"] = D["superseded_diffs"] == []
    V["D14"] = chk["own_resolve_policy_call_sites"] == {
        "scripts/probe_policy_binding.py": 0,
        "tests/contract_policy_binding_test.py": 0}
    V["D15"] = chk["no_new_denominator_call_site"] is True
    V["D16"] = chk["positive_extractor_has_teeth"] is True
    V["D17"] = chk["positive_n"] == 3
    V["D18"] = chk["coverage_unchanged"] is True
    V["D19"] = chk["coverage_scripts_live"] == chk["coverage_scripts_saved"] == 0.0227
    V["D20"] = chk["self_archive_suffix"] == ".jsonl"
    V["D21"] = chk["self_archive_not_json"] is True
    V["D22"] = chk["n_banned"] == 0
    V["D23"] = chk["checked_own_source"] is True
    V["D24"] = D["n_call_sites_192_from_archive"] == 5
    V["D25"] = D["verdict_194_unchanged"] is True
    V["D26"] = D["not_a_product_hole"] is True
    V["D27"] = (D["b192_archive_rewritten"] is False
                and D["archives_189_194_rewritten"] is False
                and D["aether_rewritten"] is False)
    V["D28"] = all(D[k] == 0 for k in ("real_expr_runs", "real_commit_runs",
                                       "main_repo_runs", "f2p_runs",
                                       "gain_reported"))
    V["D29"] = D["section183_untouched"] is True
    V["D30"] = (D["anchor"]["ok"] is True and D["anchor"]["repos"] == 3
                and D["anchor"]["diffs"] == []
                and D["anchor"]["caliber_diffs"] == [])
    V["D31"] = "不改上游尺子" in D["boundary"]
    V["D32"] = "§183" in D["boundary"]
    V["D33"] = len(cal) == 26
    V["D34"] = not any(v.startswith("MISSING:") for v in cal.values())
    V["D35"] = SELF_ARCHIVE_NAME not in " ".join(cal.keys())
    V["D36"] = "scripts/selftest_evidence_census.jsonl" in cal
    # ⛔ r113 教训：只断言数量/格式打不红内容篡改 ⇒ 与磁盘同源实算逐键对拍
    V["D37"] = cal == ppb.caliber()
    V["D38"] = D["n_own_modules_checked"] == 2

    # ── E 账：derived / summary / closure ──
    V["E1"] = der["claim"].startswith("194 鉴别账覆盖比 2/3")
    V["E2"] = der["accident_coverage_194"] == "2/3"
    V["E3"] = der["accident_coverage_this"] == "3/3"
    V["E4"] = der["n_selftest_covered_policy_fields"] == 2
    V["E5"] = der["n_resolve_policy_real_per_selftest"] == 2
    V["E6"] = der["n_policy_object_flows"] == 0
    V["E7"] = der["n_accidents_caught"] == 4 and der["n_accidents_blind"] == 1
    V["E8"] = der["zero_flow_discriminates"] is False
    V["E9"] = der["n_closure"] == der["n_closure_true"] == 53
    V["E10"] = "2/3" in summ["verdict"] and "0 次" in summ["verdict"]
    V["E11"] = "构造必然" in summ["scope"] and "不翻案" in summ["scope"]
    V["E12"] = summ["n_closure_true"] == summ["n_closure"] == 53
    V["E13"] = len(d["closure"]) == 53
    V["E14"] = all(v is True for v in d["closure"].values())
    V["E15"] = d["anchor"]["ok"] is True and d["anchor"]["repos"] == 3
    V["E16"] = (d["anchor"]["diffs"] == []
                and d["anchor"]["caliber_diffs"] == [])
    V["E17"] = d["accounts"]["D_boundary"]["anchor"] == d["anchor"]
    return V


def live_recompute() -> Dict[str, Any]:
    """⛔ 不读存档结论字段：只用尺子抽取器 + 产品源码 + 真跑重算。
    供 F3 同源对拍与反证阶段 2b 的事故版打红点。"""
    A = ppb.account_a()
    flow = ppb.policy_object_flow()
    run = ppb.run_selftest_counted()
    acc = ppb.accidents()
    return {
        "fields": A["policy_fields"],
        "read_fns": A["field_read_functions"],
        "sinks": A["main_policy_sinks"]["sinks"],
        "coverage_194": A["coverage_ratio_194"],
        "coverage_this": A["coverage_ratio_this"],
        "targets_194": A["accident_194"]["targets"],
        "selftest_covered": A["selftest_covered_field_functions"],
        "selftest_uncovered": A["selftest_uncovered_field_functions"],
        "n_reads": A["n_field_reads"],
        "flow": flow["n_flowed_into_others"],
        "hardcoded": flow["n_hardcoded_constants"],
        "rp_lines": flow["rp_lines"],
        "only_signature": flow["only_signature_attribute"],
        "real_calls": run["n_resolve_policy_actual"],
        "n_assertions": run["n_assertion_lines"],
        "caught": acc["caught"],
        "blind": acc["blind"],
        "new_sink_fail_marks":
            acc["accidents"]["E_merge_bindings_ignores_policy"]["n_fail_marks"],
        "adopt_blind": acc["still_only_adopt_blind"],
    }


def run_c() -> List[str]:
    """F 组：本机实战 / 正例反证 / 同源实算 / 真跑 / A1 / 验锚。

    ⛔ 合规形态（`contract_assert_wiring_test.py` 静态闸）：内嵌
       `ck(name, cond, detail)` 用 `fails.append` 记账，返回值即失败列表，
       使退出码路径能引用它 —— 与 r115 契约同构。不定义 ck 会被 A2/A4/B1
       判「定位不到条件位 / 失败不落地」，本轮首跑全量即因此真红 1 项。
    """
    import contract_portability_test as cpt

    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            fails.append(name)
        print("  %s %s %s" % ("PASS" if cond else "FAIL", name, detail))

    d = load()
    a = anchor_cached()

    ck("F1 存档存在 + schema v1 + 4 个待交付文件齐备",
       ARCH.exists() and d["schema"] == "policy_binding_census/v1"
       and all((ROOT / p).exists() for p in PENDING)
       and ARCH.name == SELF_ARCHIVE_NAME)

    # F2：抽取器正例 / 反例有牙 —— 事故版补丁令同源抽取结果有差异
    live = live_recompute()
    real_fields = ppb.policy_fields
    real_sinks = ppb.main_policy_sinks
    try:
        setattr(ppb, "policy_fields", lambda: [])
        setattr(ppb, "main_policy_sinks",
                lambda: {"sinks": [], "n_sinks": 0, "run_all_args": [],
                         "run_all_has_policy": False, "source": "accident"})
        broken = live_recompute()
        teeth = broken["fields"] == [] and broken["sinks"] == []
    finally:
        setattr(ppb, "policy_fields", real_fields)
        setattr(ppb, "main_policy_sinks", real_sinks)
    ck("F2 ⛔ 抽取器正例 + 事故版反例有牙（fields / sinks 双补丁）", teeth)

    # F3：live 与存档逐字段对拍
    A = d["accounts"]["A_call_site_strata"]
    B = d["accounts"]["B_runtime_readouts"]
    flow = B["policy_object_flow"]
    run = B["evaluation_account"]
    acc = B["accidents"]
    pairs = {
        "fields": (live["fields"], A["policy_fields"]),
        "read_fns": (live["read_fns"], A["field_read_functions"]),
        "sinks": (live["sinks"], A["main_policy_sinks"]["sinks"]),
        "coverage_194": (live["coverage_194"], A["coverage_ratio_194"]),
        "coverage_this": (live["coverage_this"], A["coverage_ratio_this"]),
        "targets_194": (live["targets_194"], A["accident_194"]["targets"]),
        "selftest_covered": (live["selftest_covered"],
                             A["selftest_covered_field_functions"]),
        "selftest_uncovered": (live["selftest_uncovered"],
                               A["selftest_uncovered_field_functions"]),
        "n_reads": (live["n_reads"], A["n_field_reads"]),
        "flow": (live["flow"], flow["n_flowed_into_others"]),
        "hardcoded": (live["hardcoded"], flow["n_hardcoded_constants"]),
        "rp_lines": (live["rp_lines"], flow["rp_lines"]),
        "only_signature": (live["only_signature"],
                           flow["only_signature_attribute"]),
        "real_calls": (live["real_calls"], run["n_resolve_policy_actual"]),
        "n_assertions": (live["n_assertions"], run["n_assertion_lines"]),
        "caught": (live["caught"], acc["caught"]),
        "blind": (live["blind"], acc["blind"]),
        "new_sink_fail_marks": (
            live["new_sink_fail_marks"],
            acc["accidents"]["E_merge_bindings_ignores_policy"]["n_fail_marks"]),
        "adopt_blind": (live["adopt_blind"], acc["still_only_adopt_blind"]),
    }
    diffs = [k for k, (lv, sv) in pairs.items() if lv != sv]
    # ⛔ key 必须是常量字符串：反证阶段 3 的 AST 抽取器按 Subscript 常量
    #    找 F 条件，带 `%d` 的 BinOp key 会被漏掉（首跑只找到 5/6）
    ck("F3 ⛔ 同源实算 A/B 账逐字段对拍零差异",
       not diffs and len(pairs) == 19)

    # F4：真跑 selftest 计数器 + 新补 sink 事故打红
    one = ppb._selftest_with(
        "merge_bindings",
        lambda envs, fn, p: ({}, {"cross_scope": False, "n_scopes_merged": 1}))
    ck("F4 ⛔ 真跑 selftest 2 次 resolve_policy + 新补 sink 事故打红 2 条",
       live["real_calls"] == 2 and one["selftest_ok"] is False
       and one["n_fail_marks"] == 2)

    # F5：A1 候选面（tracked ∪ untracked，不按扩展名过滤）
    # ⛔ candidate_files() 返回**相对路径字符串**，不是绝对路径 Path（r115 同款）
    files = set(cpt.candidate_files() or [])
    in_surface = [p for p in PENDING if p in files]
    hits = sum(len(cpt.scan_text(cpt.read_text(ROOT / p) or ""))
               for p in PENDING)
    teeth = bool(cpt.scan_text("/Users/" + "someone" + "/V6"))
    ck("F5 A1 四文件纳入候选面 + scan_text 零命中 + 正例有牙",
       len(in_surface) == 4 and hits == 0 and teeth)

    ck("F6 验锚 OK 3/3 + closure 53/53 + 分母仍 5 且自身模块不在内",
       a["ok"] is True and a["repos"] == 3 and not a["diffs"]
       and not a["caliber_diffs"]
       and len(d["closure"]) == 53
       and all(v is True for v in d["closure"].values())
       and A["n_call_sites_192_from_archive"] == 5
       and "scripts/probe_policy_binding.py" not in
           str(A["accident_194"]["source"])
       and SELF_ARCHIVE_NAME.endswith(".jsonl"))
    return fails


def main() -> int:
    d = load()
    V = conditions(d)
    bad = [k for k, v in V.items() if not v]
    print("=" * 76)
    print("r116 / README 195 契约：194 鉴别账覆盖比与第五类真跑鉴别力边界")
    print("=" * 76)
    for k in sorted(V):
        print("  %-5s %s %s" % (k, "PASS" if V[k] else "FAIL", ""))
    fbad = run_c()
    print("-" * 76)
    print("%s A–E %d 项 + F 组 6 项" %
          ("✅ 全部通过" if not bad and not fbad else "❌ 有失败",
           len(V)))
    if bad:
        print("A–E FAIL:", bad)
    if fbad:
        print("F FAIL:", fbad)
    print("194『唯一恒绿』只在选中 4 类事故内成立；0 次流入是构造必然、不翻案")
    allbad = bad + fbad
    return 1 if allbad else 0


if __name__ == "__main__":
    sys.exit(main())
