#!/usr/bin/env python3
"""r115 / README 194 契约：192 四类证据分层漏了第五类。

⛔ 本契约**不出现 `resolve_policy(...)` 的 AST 调用点**：192 尺子
`resolve_policy_calls_all()` 按全仓 AST 数该函数调用点，白名单只
排除 192 自身 2 个模块；本文件若新增调用点会令 192 契约 F3/F6
分母漂移（r114 首跑红的根因）。一律用 `getattr(CG, "RELAXED")` /
`setattr(CG, "resolve_policy", ...)` 达成语义等价。

A–E 组是**纯函数**（只读存档，供反证逐条置 False / 篡改字段）；
F 组是本机实战（同源实算、事故版补丁、一次真实验锚、A1 扫描面）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]          # ⛔ 第 99 条：根从 __file__ 推导
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from realrepo_baseline import require_baseline                  # noqa: E402
from contract_portability_test import (candidate_files, scan_text,  # noqa: E402
                                       scan_surface)
import probe_selftest_evidence as pse                            # noqa: E402

CG = pse.CG
ARCH = ROOT / "scripts" / "selftest_evidence_census.jsonl"
SELF_ARCHIVE_NAME = ARCH.name
_ANCHOR: Dict[str, Any] | None = None

PENDING = ["README.md", "scripts/probe_selftest_evidence.py",
           "scripts/selftest_evidence_census.jsonl",
           "tests/contract_selftest_evidence_test.py"]


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def load() -> Dict[str, Any]:
    return json.loads(read_text(ARCH))


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    """A–E 组：**纯函数**，零沙箱零子进程，供反证脚本逐条篡改。"""
    V: Dict[str, bool] = {}
    A = d["accounts"]["A_call_site_strata"]
    B = d["accounts"]["B_runtime_readouts"]
    C = d["accounts"]["C_discrimination"]
    D = d["accounts"]["D_boundary"]
    clo = d["closure"]
    cal = d["caliber"]
    anc = d["anchor"]
    summ = d["summary"]
    glob = A["discover_glob"]
    sinks = A["main_policy_sinks"]
    cnt = B["ast_call_counts_by_function"]
    ev = B["selftest_eval_account"]
    acc = C["accidents"]
    sc = D["sandbox_selfcheck"]

    # ── A 组：调用点分层（双源）──
    V["A1"] = d["schema"] == "selftest_evidence_census/v1"
    V["A2"] = A["n_call_sites_192"] == 5
    V["A3"] = len(A["call_site_files"]) == 5
    V["A4"] = "tests/contract_context_gate_test.py" in A["call_site_files"]
    V["A5"] = "tests/contract_inliner_relax_test.py" in A["call_site_files"]
    V["A6"] = A["source_192_archive"].endswith("resolve_policy_calls")
    V["A7"] = glob["pattern"] == "contract_*_test.py"
    V["A8"] = glob["source"] == "aether/check.py::discover (AST)"
    V["A9"] = glob["pos_hit"] is True and glob["neg_hit"] is True
    V["A10"] = glob["teeth"] is True
    V["A11"] = A["n_contract_files_true_run"] == 2
    V["A12"] = sorted(A["contract_files_true_run"]) == [
        "tests/contract_context_gate_test.py",
        "tests/contract_inliner_relax_test.py"]
    V["A13"] = A["n_offline_ruler_files"] == 2
    V["A14"] = A["n_product_files"] == 1
    V["A15"] = A["product_files"] == ["scripts/probe_context_gate.py"]
    V["A16"] = A["product_functions"] == {
        "scripts/probe_context_gate.py": ["main", "selftest"]}
    V["A17"] = A["product_selftest_run_via_E1"] is True
    V["A18"] = A["product_main_unrun"] is True
    V["A19"] = A["selftest_covers_adopt_probe"] is False
    V["A20"] = "adopt_probe" not in A["selftest_covered_calls"]
    V["A21"] = "resolve_policy" in A["selftest_covered_calls"]
    V["A22"] = "bindings_for" in A["selftest_covered_calls"]
    V["A23"] = "merge_bindings" in A["selftest_covered_calls"]
    V["A24"] = sinks["n_sinks"] == 3
    V["A25"] = sinks["sinks"] == ["adopt_probe", "bindings_for", "merge_bindings"]
    V["A26"] = sinks["run_all_has_policy"] is False
    V["A27"] = sinks["run_all_args"] == ["source", "items", "timeout", "pkg"]
    V["A28"] = A["denominator_unchanged"] is True
    V["A29"] = A["n_runtime_call_site_files_now"] == 5
    V["A30"] = A["self_modules_not_in_denominator"] is True
    V["A31"] = "读取点存在 ≠ 运行时走到" in A["a192_boundary"]
    V["A32"] = "判词不能一概而论" in A["verdict"]

    # ── B 组：运行读数（AST 数与真跑计数器对拍）──
    V["B1"] = cnt["scripts/probe_context_gate.py::selftest"] == 2
    V["B2"] = cnt["scripts/probe_context_gate.py::main"] == 1
    V["B3"] = cnt["tests/contract_context_gate_test.py::main"] == 2
    V["B4"] = cnt["tests/contract_inliner_relax_test.py::group_c"] == 2
    V["B5"] = cnt["scripts/probe_inliner_relax.py::measure"] == 2
    V["B6"] = cnt["scripts/probe_policy_reach.py::account_d"] == 2
    V["B7"] = B["ast_runtime_match"] is True
    V["B8"] = B["n_selftest_ast"] == 2
    V["B9"] = B["n_selftest_runtime_counter"] == 2
    V["B10"] = ev["ok"] is True
    V["B11"] = ev["n_fail_marks"] == 0
    V["B12"] = ev["n_assertion_lines"] == 15
    V["B13"] = B["n_resolve_policy_per_full_check_lower_bound"] == 6
    V["B14"] = B["n_contract_runs_per_full_check"] == 2
    V["B15"] = B["merged_into_bool"] is False
    V["B16"] = "第五类证据" in B["verdict"]

    # ── C 组：鉴别力（求值账 / 鉴别账分开）──
    V["C1"] = C["evaluation_account"]["selftest_ok"] is True
    V["C2"] = C["n_caught"] == 3
    V["C3"] = C["n_blind"] == 1
    V["C4"] = sorted(C["caught"]) == ["A_strict_broken", "B_default_0000",
                                      "D_bindings_for_ignores_policy"]
    V["C5"] = C["blind"] == ["C_adopt_probe_ignores_policy"]
    V["C6"] = C["positive_has_teeth"] is True
    V["C7"] = C["adoption_chain_blind"] is True
    V["C8"] = C["discriminates_adoption_chain"] is False
    V["C9"] = acc["A_strict_broken"]["selftest_ok"] is False
    V["C10"] = acc["A_strict_broken"]["n_fail_marks"] == 1
    V["C11"] = any("自检K" in l for l in acc["A_strict_broken"]["red_lines"])
    V["C12"] = acc["B_default_0000"]["selftest_ok"] is False
    V["C12b"] = acc["B_default_0000"]["n_fail_marks"] == 1
    V["C13"] = any("自检J" in l for l in acc["B_default_0000"]["red_lines"])
    V["C14"] = acc["C_adopt_probe_ignores_policy"]["selftest_ok"] is True
    V["C15"] = acc["C_adopt_probe_ignores_policy"]["n_fail_marks"] == 0
    V["C15b"] = acc["C_adopt_probe_ignores_policy"]["red_lines"] == []
    V["C16"] = acc["D_bindings_for_ignores_policy"]["selftest_ok"] is False
    V["C16b"] = acc["D_bindings_for_ignores_policy"]["n_fail_marks"] == 1
    V["C17"] = any("自检O" in l for l in acc["D_bindings_for_ignores_policy"]["red_lines"])
    V["C18"] = "采纳链" in acc["C_adopt_probe_ignores_policy"]["description"]
    V["C19"] = C["merged_into_bool"] is False
    V["C20"] = "求值判对 ≠ 鉴别力" in C["boundary"]
    V["C21"] = "缺口精确在采纳链" in C["verdict"]

    # ── D 组：边界 / 自检 ──
    V["D1"] = D["policy_default"] == "1111"
    V["D2"] = D["policy_strict"] == "0000"
    V["D3"] = D["verdict_192_unchanged"] is True
    V["D4"] = D["not_a_product_hole"] is True
    V["D5"] = (D["aether_rewritten"] is False
               and D["archives_189_193_rewritten"] is False
               and D["b192_archive_rewritten"] is False)
    V["D6"] = D["denominators"] == {"main": 135, "form": 119, "no_result": 94}
    V["D7"] = (D["denominator_dual_source"]["form_rows"]
               == D["denominator_dual_source"]["form_summary"] == 119)
    V["D8"] = (D["denominator_dual_source"]["no_result_rows"]
               == D["denominator_dual_source"]["no_result_summary"] == 94)
    V["D9"] = D["superseded_total"] == D["superseded_sha_matches"] == 3
    V["D10"] = D["superseded_diffs"] == []
    V["D11"] = sc["n_banned"] == 0 and sc["banned"] == []
    V["D12"] = sc["checked_own_source"] is True
    V["D13"] = sc["n_own_resolve_policy_ast_calls"] == 0
    V["D14"] = sc["no_new_denominator_call_site"] is True
    V["D15"] = all(D[k] == 0 for k in ("real_expr_runs", "real_commit_runs",
                                       "main_repo_runs", "f2p_runs",
                                       "gain_reported"))
    V["D16"] = D["n_forbidden_runtime_calls"] == 0
    V["D17"] = D["section183_untouched"] is True
    V["D18"] = "不推翻 192" in D["boundary"]
    V["D19"] = len(cal) == 24
    V["D20"] = SELF_ARCHIVE_NAME not in cal
    V["D21"] = "scripts/probe_selftest_evidence.py" in cal
    V["D22"] = len(clo) == 44 and all(v is True for v in clo.values())
    V["D23"] = d["derived"]["n_closure_true"] == d["derived"]["n_closure"] == 44
    V["D24"] = (anc.get("ok") is True and anc.get("repos") == 3
                and anc.get("diffs") == [] and anc.get("caliber_diffs") == [])
    V["D25"] = "不推翻 192" in summ["scope"]
    V["D26"] = ("第五类证据格" in summ["verdict"]
                and "补上这一格" in summ["scope"])
    V["D27"] = sorted(d["accounts"].keys()) == [
        "A_call_site_strata", "B_runtime_readouts",
        "C_discrimination", "D_boundary"]
    V["D28"] = d["derived"]["adoption_chain_blind"] is True
    return V


def live_recompute() -> Dict[str, Any]:
    """⛔ 不读存档结论字段：只用尺子抽取器 + 产品源码 / 真跑重算。

    供 F3 同源实算对拍；事故版函数补丁必须能把它打红。
    """
    A = pse.account_a()
    B = pse.account_b()
    C = pse.account_c()
    return {
        "glob_pattern": A["discover_glob"]["pattern"],
        "glob_teeth": A["discover_glob"]["teeth"],
        "n_contract": A["n_contract_files_true_run"],
        "n_offline": A["n_offline_ruler_files"],
        "n_product": A["n_product_files"],
        "selftest_has_adopt": A["selftest_covers_adopt_probe"],
        "main_sinks": A["main_policy_sinks"]["sinks"],
        "run_all_has_policy": A["main_policy_sinks"]["run_all_has_policy"],
        "denominator": A["n_runtime_call_site_files_now"],
        "self_out": A["self_modules_not_in_denominator"],
        "n_selftest_ast": B["n_selftest_ast"],
        "per_full": B["n_resolve_policy_per_full_check_lower_bound"],
        "selftest_ok": B["selftest_eval_account"]["ok"],
        "n_caught": C["n_caught"],
        "n_blind": C["n_blind"],
        "blind": C["blind"],
        "positive_teeth": C["positive_has_teeth"],
        "adopt_blind": C["adoption_chain_blind"],
    }


def anchor_once() -> Dict[str, Any]:
    global _ANCHOR
    if _ANCHOR is None:
        _ANCHOR = require_baseline()
    r = _ANCHOR
    return {"ok": bool(r.get("ok")), "repos": len(r.get("repos") or {}),
            "diffs": list(r.get("diffs") or []),
            "caliber_diffs": list(r.get("caliber_diffs") or [])}


def run_c() -> List[str]:
    """F 组：本机实战（同进程，不 spawn 子进程）。"""
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            fails.append(name)
        print("  %s %s %s" % ("PASS" if cond else "FAIL", name, detail))

    d = load()
    A = d["accounts"]["A_call_site_strata"]
    B = d["accounts"]["B_runtime_readouts"]
    C = d["accounts"]["C_discrimination"]

    # F1 caliber 同源实算（事故版改一个 hash ⇒ 打红）
    live_cal = pse.caliber()
    ck("F1 caliber 24 键同源实算逐键对拍",
       live_cal == d["caliber"] and len(live_cal) == 24)

    # F2 抽取器有牙 + 自身不新增分母调用点
    g = pse.check_discover_glob()
    own = pse.sandbox_selfcheck()
    ck("F2 glob 抽取器正例/反例有牙 + 尺子自身零 resolve_policy 调用点",
       g["teeth"] is True and g["pos_hit"] is True and g["neg_hit"] is True
       and own["n_own_resolve_policy_ast_calls"] == 0
       and own["n_banned"] == 0)

    # F3 同源实算（事故版函数补丁 ⇒ 打红）
    lr = live_recompute()
    same = (lr["glob_pattern"] == A["discover_glob"]["pattern"]
            and lr["glob_teeth"] == A["discover_glob"]["teeth"]
            and lr["n_contract"] == A["n_contract_files_true_run"]
            and lr["n_offline"] == A["n_offline_ruler_files"]
            and lr["n_product"] == A["n_product_files"]
            and lr["selftest_has_adopt"] == A["selftest_covers_adopt_probe"]
            and lr["main_sinks"] == A["main_policy_sinks"]["sinks"]
            and lr["run_all_has_policy"] == A["main_policy_sinks"]["run_all_has_policy"]
            and lr["denominator"] == A["n_runtime_call_site_files_now"]
            and lr["self_out"] == A["self_modules_not_in_denominator"]
            and lr["n_selftest_ast"] == B["n_selftest_ast"]
            and lr["per_full"] == B["n_resolve_policy_per_full_check_lower_bound"]
            and lr["selftest_ok"] == B["selftest_eval_account"]["ok"]
            and lr["n_caught"] == C["n_caught"]
            and lr["n_blind"] == C["n_blind"]
            and lr["blind"] == C["blind"]
            and lr["positive_teeth"] == C["positive_has_teeth"]
            and lr["adopt_blind"] == C["adoption_chain_blind"])
    ck("F3 同源实算逐项对拍四本账（事故版补丁必须打红）", same)

    # F4 真跑 selftest 计数器 + 事故版恒绿/恒红鉴别力
    counted = pse.run_selftest_counted()
    strict = getattr(CG, "STRICT")
    orig = getattr(CG, "resolve_policy")
    try:
        setattr(CG, "resolve_policy", lambda s=False: strict)
        with_patch = pse.run_selftest_counted()
    finally:
        setattr(CG, "resolve_policy", orig)
    ck("F4 真跑 selftest 计数器 2 次 + 默认态事故版打红",
       counted["n_resolve_policy_actual"] == 2
       and counted["selftest_ok"] is True
       and counted["n_fail_marks"] == 0
       and with_patch["selftest_ok"] is False
       and with_patch["n_fail_marks"] >= 1)

    # F5 A1 扫描面：4 个待交付文件纳入 + 零命中 + 正例反证
    cfiles = set(candidate_files() or [])
    hits = {f: len(scan_text(read_text(ROOT / f) or "")) for f in PENDING}
    ck("F5 A1 四文件纳入候选面且 scan_text() 零命中 + 正例有牙",
       all(f in cfiles for f in PENDING) and all(v == 0 for v in hits.values())
       and len(scan_text("x = '/Users/" + "linshaojie" + "/V6'\n")) >= 1)

    # F6 一次真实验锚 + closure 44/44 + 分母不漂移
    a = anchor_once()
    rpc = pse.resolve_call_sites_all()
    ck("F6 验锚 OK 3/3 + closure 44/44 + 分母仍 5 且自身模块不在内",
       a["ok"] is True and a["repos"] == 3 and not a["diffs"]
       and not a["caliber_diffs"]
       and len(d["closure"]) == 44
       and all(v is True for v in d["closure"].values())
       and len(rpc) == 5
       and "scripts/probe_selftest_evidence.py" not in rpc
       and "tests/contract_selftest_evidence_test.py" not in rpc)
    return fails


def main() -> int:
    d = load()
    V = conditions(d)
    print("=" * 74)
    print("r115 / README 194：192 四类证据分层漏了第五类（验收契约真跑产品模块）")
    print("=" * 74)
    print("== A–E 组：调用点分层 / 运行读数 / 鉴别力 / 边界 / 顶层 ==")
    bad = [k for k, v in V.items() if not v]
    for k in V:
        print("  %s %s" % ("PASS" if V[k] else "FAIL", k))
    print("== F 组：本机实战 ==")
    fbad = run_c()
    allbad = bad + fbad
    print("=" * 74)
    if allbad:
        print("❌ 未通过 %s" % allbad)
        return 1
    print("✅ 全部通过（A–E %d 项 + F 组 6 项）" % len(V))
    print("补第五类证据格但『默认生效』仍缺采纳链运行证据；不推翻 192 方向、不报收益")
    return 0


if __name__ == "__main__":
    sys.exit(main())
