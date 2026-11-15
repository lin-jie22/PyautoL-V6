#!/usr/bin/env python3
"""r118 / README 197 契约：194「≥6 次真跑」是静态 AST 外推，运行账是 8。

钉死四本账（不合并成布尔）：
  A 静态外推   194 公式 2+2+2=6 同源复现、只对 selftest 单函数做过
               运行时对拍、n_contract_runs=2 是字面常量、7 个调用点
               / 2 个短路点 / 2 个 selftest 入口
  B 运行时计数 runner 同构上下文真跑两份契约各 4 次 ⇒ 8；
               与原样执行 rc / stdout 逐字一致；差 2 来自 selftest 第二入口
  C 鉴别力     短路事故 4→2（AST 点存在但运行不到）、签名事故打红而
               次数不变、反事实四世界静态数对事故零鉴别力
  D 边界       三分母 / superseded / 验锚 / caliber / 192 分母仍 5 /
               runner declared_modes 双源 / 存档非 .json / §183 零触碰
  E derived / closure / summary 完整性

⛔⛔ 本契约**不出现 `resolve_policy(...)` 的 AST 调用点**：
192/194 尺子按全仓 AST 数该函数调用点，且只把历轮自身量具/契约
列入白名单；本文件新增调用点会令分母漂移、F3/F6 真红（r114 已踩）。
一律用 `getattr` / `setattr` / 子进程 driver 动态达成。

⛔ 合规形态（`contract_assert_wiring_test.py` 静态闸）：`run_c()` 内嵌
   `def ck(name, cond, detail)` + `fails.append(...)` + 返回失败列表，
   `main()` 用 `allbad = bad + fbad` 决定退出码（r116 首跑真红的教训）。

⛔ 纯只读取证契约：不跑真实 expr、不跑真实 commit、不跑
`main() --repo`、不碰 §183；不修改 189–196 尺子 / 存档 / 判据。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]          # ⛔ 第 99 条：根从 __file__ 推导
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import probe_truerun_count as ptc                            # noqa: E402
import contract_portability_test as cpt                      # noqa: E402

ARCH = ROOT / "scripts" / "truerun_count_census.jsonl"
SELF_ARCHIVE_NAME = ARCH.name
PENDING = ["README.md", "scripts/probe_truerun_count.py",
           "scripts/truerun_count_census.jsonl",
           "tests/contract_truerun_count_test.py"]
C1 = ptc.C1
C2 = ptc.C2
CG_SRC = ptc.CG_SRC

_ANCHOR: Dict[str, Any] | None = None


def load() -> Dict[str, Any]:
    return json.loads(ARCH.read_text(encoding="utf-8"))


def verdict_from(V: Dict[str, bool]) -> List[str]:
    """⛔ 阶段 1 逐条撤回用同一个函数（撤回只换条件、保留 body）。"""
    return [k for k, v in V.items() if not v]


def anchor() -> Dict[str, Any]:
    """⛔ 验锚只调一次并缓存（单次 50–60s，r111 教训）。"""
    global _ANCHOR
    if _ANCHOR is None:
        _ANCHOR = ptc.anchor_report()
    return dict(_ANCHOR)


def sha256_file(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "MISSING:" + rel
    return hashlib.sha256(p.read_bytes()).hexdigest()


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    A = d["accounts"]["A_static_extrapolation"]
    B = d["accounts"]["B_runtime_counter"]
    C = d["accounts"]["C_discrimination"]
    D = d["accounts"]["D_boundary"]
    der, cl, summ = d["derived"], d["closure"], d["summary"]
    V: Dict[str, bool] = {}

    # ── A 静态外推 ──
    t = A["formula_terms"]
    V["A1"] = A["recomputed_lower_bound"] == 6
    V["A2"] = A["saved_lower_bound"] == 6
    V["A3"] = A["dual_source_match"] is True
    V["A4"] = t[CG_SRC + "::selftest"] == 2
    V["A5"] = t[C1 + "::main"] == 2
    V["A6"] = t[C2 + "::group_c"] == 2
    V["A7"] = len(t) == 3 and sorted(t.values()) == [2, 2, 2]
    V["A8"] = A["contract_runs_saved"] == 2
    V["A9"] = A["contract_runs_is_hardcoded_constant"] is True
    V["A10"] = A["runtime_paired_value"] is True
    V["A11"] = A["runtime_unpaired_call_sites"] == 4
    cs = A["counter_scope"]
    V["A12"] = cs["n_resolve_policy_runtime_counter"] == 1
    V["A13"] = cs["n_setprofile"] == 0
    V["A14"] = cs["n_runpy_run_path"] == 0
    V["A15"] = cs["n_subprocess_run"] == 0
    V["A16"] = cs["n_selftest_calls"] == 0
    V["A17"] = A["n_call_sites"] == 7
    V["A18"] = A["n_short_circuit_sites"] == 2
    V["A19"] = all(s["rel"] == C2 and s["func"] == "group_c"
                   for s in A["short_circuit_sites"])
    V["A20"] = sorted(s["lineno"] for s in A["short_circuit_sites"]) == [298, 299]
    V["A21"] = A["n_selftest_entries"] == 2
    V["A22"] = sorted(e["lineno"] for e in A["selftest_entries"]) == [195, 297]
    V["A23"] = A["n_selftest_entries_counted_by_194"] == 1
    V["A24"] = A["merged_into_bool"] is False
    V["A25"] = "probe_selftest_evidence.py" in A["source"]
    V["A26"] = "selftest" in A["runtime_paired_only"]
    V["A27"] = all(s["short_circuit"] is False
                   for s in A["call_sites"] if s["rel"] != C2)
    V["A28"] = len(A["ast_call_counts_194"]) == 6
    V["A29"] = sorted(s["rel"] for s in A["call_sites"]) == sorted(
        [CG_SRC] * 3 + [C1] * 2 + [C2] * 2)
    V["A30"] = sorted((e["rel"], e["lineno"]) for e in A["selftest_entries"]) == [
        (C1, 195), (C2, 297)]

    # ── B 运行时计数 ──
    V["B1"] = B["n_total_runtime"] == 8
    V["B2"] = B["n_194_lower_bound"] == 6
    V["B3"] = B["gap"] == 2
    V["B4"] = B["lower_bound_still_holds"] is True
    V["B5"] = B["gap_explained_by_second_selftest_entry"] is True
    V["B6"] = B["n_product_selftest_frames_total"] == 4
    V["B7"] = B["n_product_selftest_frames_counted_by_194"] == 2
    V["B8"] = B["all_rc_identical"] is True
    V["B9"] = B["all_stdout_identical"] is True
    V["B10"] = "setprofile" in B["driver"]
    p1, p2 = B["per_contract"][C1], B["per_contract"][C2]
    V["B11"] = p1["n_runtime"] == 4 and p2["n_runtime"] == 4
    V["B12"] = p1["by_origin"] == {"product_selftest": 2, "contract": 2}
    V["B13"] = p2["by_origin"] == {"product_selftest": 2, "contract": 2}
    V["B14"] = p1["by_caller"] == {"selftest": 2, "main": 2}
    V["B15"] = p2["by_caller"] == {"selftest": 2, "group_c": 2}
    V["B16"] = len(p1["hits"]) == 4 and len(p2["hits"]) == 4
    V["B17"] = sorted(h["line"] for h in p1["hits"]) == [199, 201, 311, 312]
    V["B18"] = sorted(h["line"] for h in p2["hits"]) == [298, 299, 311, 312]
    V["B19"] = sum(1 for h in p1["hits"] if h["in_product"]) == 2
    V["B20"] = sum(1 for h in p2["hits"] if h["in_product"]) == 2
    V["B21"] = all(v["driver_ok"] is True and v["driver_err"] == ""
                   and v["driver_rc"] == 0 and v["plain_rc"] == 0
                   for v in (p1, p2))
    V["B22"] = all(v["rc_identical"] is True and v["stdout_identical"] is True
                   for v in (p1, p2))
    V["B23"] = B["merged_into_bool"] is False
    V["B24"] = [h["caller"] for h in p2["hits"]] == [
        "selftest", "selftest", "group_c", "group_c"]
    V["B25"] = [h["in_product"] for h in p2["hits"]] == [
        True, True, False, False]
    V["B26"] = [h["caller"] for h in p1["hits"]] == [
        "selftest", "selftest", "main", "main"]

    # ── C 鉴别力 ──
    sc, sig = C["short_circuit"], C["signature_broken"]
    V["C1"] = sc["driver_ok"] is True and sc["driver_err"] == ""
    V["C2"] = sc["n_baseline"] == 4
    V["C3"] = sc["n_runtime"] == 2
    V["C4"] = sc["by_caller"] == {"selftest": 2}
    V["C5"] = len(sc["hits"]) == 2 and all(h["in_product"] for h in sc["hits"])
    V["C6"] = sc["n_fails"] == 1 and sc["main_rc"] == 1
    V["C7"] = sc["call_sites_unchanged"] == 7
    V["C8"] = C["short_circuit_teeth"] is True
    V["C9"] = sig["driver_ok"] is True and sig["driver_err"] == ""
    V["C10"] = sig["n_baseline"] == 4 and sig["n_runtime"] == 4
    V["C11"] = sig["n_fails"] == 2 and sig["main_rc"] == 1
    V["C12"] = C["count_unchanged_under_signature_accident"] is True
    V["C13"] = C["signature_accident_caught"] is True
    V["C14"] = C["count_vs_discrimination_two_ledgers"] is True
    V["C15"] = C["discriminates"] is True
    V["C16"] = C["merged_into_bool"] is False
    cf = C["counterfactual"]
    w = cf["worlds"]
    V["C17"] = len(w) == 4
    V["C18"] = w["W0_baseline"]["static"] == 6 and w["W0_baseline"]["runtime"] == 8
    V["C19"] = w["W1_only_c1"]["static"] == 4 and w["W1_only_c1"]["runtime"] == 4
    V["C20"] = w["W2_only_c2"]["static"] == 4 and w["W2_only_c2"]["runtime"] == 4
    V["C21"] = w["W3_short_circuit"]["static"] == 6 and w["W3_short_circuit"]["runtime"] == 6
    V["C22"] = w["W3_short_circuit"]["synthetic"] is True
    V["C23"] = all(x["ran"] is True for x in w.values())
    V["C24"] = cf["static_values"] == [4, 6]
    V["C25"] = cf["runtime_values"] == [4, 6, 8]
    V["C26"] = cf["static_has_no_discrimination"] is True
    V["C27"] = cf["accident_static_unchanged"] is True
    V["C28"] = cf["n_same_static_diff_runtime_pairs"] > 0
    V["C29"] = cf["n_zero_worlds"] == 0
    V["C30"] = cf["zero_worlds_not_applicable"] is True
    V["C31"] = cf["only_synthetic_world"] == ["W3_short_circuit"]
    V["C32"] = cf["natural_runtime_exceeds_static"] is True
    V["C33"] = cf["all_zero_worlds_ran"] is True
    V["C34"] = cf["n_same_static_diff_runtime_pairs"] == 1
    V["C35"] = cf["same_static_diff_runtime_pairs"] == [
        ["W0_baseline", "W3_short_circuit"]]
    V["C36"] = all(w[k]["synthetic"] is False
                   for k in ("W0_baseline", "W1_only_c1", "W2_only_c2"))
    V["C37"] = [h["caller"] for h in sc["hits"]] == ["selftest", "selftest"]
    V["C38"] = sorted(h["line"] for h in sc["hits"]) == [311, 312]
    V["C39"] = sig["by_caller"] == {"<lambda>": 4}

    # ── D 边界 ──
    den, sup, sb, rm = (D["denominator_dual_source"], D["superseded"],
                        D["sandbox_selfcheck"], D["runner_modes"])
    V["D1"] = den["main"] == 135
    V["D2"] = den["form"] == 119
    V["D3"] = den["no_result"] == 94
    V["D4"] = den["all_distinct"] is True
    ds = den["dual_source"]
    V["D5"] = ds["main_rows"] == ds["main_summary"] == 135
    V["D6"] = ds["form_rows"] == ds["form_summary"] == 119
    V["D7"] = ds["no_result_rows"] == ds["no_result_summary"] == 94
    V["D8"] = sup["all_match"] is True and sup["n_registered"] > 0
    V["D9"] = not sup["diffs"]
    V["D10"] = len(D["caliber_disk"]) == len(ptc.CALIBER_FILES) >= 20
    V["D11"] = all(len(v) == 64 and not v.startswith("MISSING:")
                   for v in D["caliber_disk"].values())
    V["D12"] = sb["checked_own_source"] is True
    V["D13"] = sb["own_resolve_policy_call_sites"].get(ptc.SELF_SRC) == 0
    V["D14"] = sb["own_resolve_policy_call_sites"].get(ptc.SELF_CONTRACT) == 0
    V["D15"] = sb["no_new_denominator_call_site"] is True
    V["D16"] = sb["positive_extractor_has_teeth"] is True
    V["D17"] = sb["positive_n"] == 3
    V["D18"] = sb["coverage_unchanged"] is True
    V["D19"] = sb["coverage_scripts_live"] == sb["coverage_scripts_saved"] == 0.0227
    V["D20"] = sb["self_archive_not_json"] is True
    V["D21"] = sb["self_archive_suffix"] == ".jsonl"
    V["D22"] = rm["all_default_only"] is True
    V["D23"] = rm["dual_source_match"] is True
    V["D24"] = rm["n_runs_implied"] == 2
    V["D25"] = rm["n_runs_saved_194"] == 2
    V["D26"] = all(v["modes"] == ["default"] and v["supports_e2e"] is False
                   and v["exclusive"] is False and v["min_python"] == [3, 9]
                   for v in rm["per_contract"].values())
    V["D27"] = D["section_183_touched"] is False
    V["D28"] = D["real_expr_run"] == 0 and D["real_commit_run"] == 0
    V["D29"] = D["gain_rows_reported"] == 0 and D["product_files_modified"] == 0
    V["D30"] = D["merged_into_bool"] is False
    a = D["anchor"]
    V["D31"] = a["ok"] is True and a["repos"] == 3
    V["D32"] = not a["diffs"] and not a["caliber_diffs"]
    V["D33"] = sb["n_banned"] == 0
    V["D34"] = sup["n_registered"] == ptc.superseded_check()["n_registered"]

    # ── E derived / closure / summary ──
    V["E1"] = d["schema"] == "truerun_count_census/v1"
    V["E2"] = der["claim"].startswith("194 的")
    V["E3"] = der["n_194_static_lower_bound"] == 6
    V["E4"] = der["n_runtime_total"] == 8
    V["E5"] = der["gap"] == 2
    V["E6"] = der["n_selftest_entries"] == 2
    V["E7"] = der["n_selftest_entries_counted_by_194"] == 1
    V["E8"] = der["n_short_circuit_sites"] == 2
    V["E9"] = der["short_circuit_runtime_drop"] == 2
    V["E10"] = der["static_has_no_discrimination"] is True
    V["E11"] = der["natural_runtime_exceeds_static"] is True
    V["E12"] = der["runtime_counter_has_teeth"] is True
    V["E13"] = der["lower_bound_still_holds"] is True
    V["E14"] = der["n_closure_true"] == der["n_closure"]
    V["E15"] = len(cl) == der["n_closure"] and len(cl) >= 45
    V["E16"] = all(v is True for v in cl.values())
    V["E17"] = summ["n_closure_true"] == summ["n_closure"] == len(cl)
    V["E18"] = ("2+2+2=6" in summ["verdict"]
                and "8 次" in summ["verdict"]
                and "setprofile" in summ["verdict"])
    V["E19"] = "不翻案" in summ["scope"]
    V["E20"] = "§183 零触碰" in summ["scope"]
    V["E21"] = "不是产品洞" in summ["scope"]
    V["E22"] = "不适用" in summ["scope"]
    V["E23"] = d["anchor"]["ok"] is True
    V["E24"] = d["caliber"] == D["caliber_disk"]
    V["E25"] = der["n_same_static_diff_runtime_pairs"] == 1
    return V


def live_recompute() -> Dict[str, Any]:
    """F 组同源实算：不读存档结论字段，重抽 AST + 真跑便宜契约 + 事故版。"""
    A = ptc.account_a()
    drv = ptc.run_driver(C1, "default")
    sc = ptc.run_driver(C2, "short_circuit")
    sig = ptc.run_driver(C1, "signature_broken")
    return {
        "formula_terms": A["formula_terms"],
        "recomputed_lower_bound": A["recomputed_lower_bound"],
        "n_call_sites": A["n_call_sites"],
        "n_short_circuit_sites": A["n_short_circuit_sites"],
        "n_selftest_entries": A["n_selftest_entries"],
        "hardcoded": A["contract_runs_is_hardcoded_constant"],
        "counter_scope": A["counter_scope"],
        "ast_call_counts_194": A["ast_call_counts_194"],
        "call_sites": A["call_sites"],
        "short_circuit_sites": A["short_circuit_sites"],
        "selftest_entries": A["selftest_entries"],
        "sup_n_registered": ptc.superseded_check()["n_registered"],
        "c1_n": len(drv["payload"].get("hits", [])),
        "c1_hits": ptc._short(drv["payload"].get("hits", [])),
        "c1_lines": sorted(h["line"] for h in
                           ptc._short(drv["payload"].get("hits", []))),
        "c1_driver_ok": drv["ok"],
        "sc_n": len(sc["payload"].get("hits", [])),
        "sc_fails": sc["payload"].get("n_fails"),
        "sig_n": len(sig["payload"].get("hits", [])),
        "sig_fails": sig["payload"].get("n_fails"),
        "sig_driver_ok": sig["ok"],
    }


def run_c() -> List[str]:
    """F 组（⛔ `ck` 合规形态：内嵌 def ck + fails.append + return fails）。"""
    withdraw = os.environ.get("R118_WITHDRAW", "")
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        if withdraw and name.startswith(withdraw):
            cond = False
        if not cond:
            fails.append(name)

    d = load()
    A = d["accounts"]["A_static_extrapolation"]
    B = d["accounts"]["B_runtime_counter"]
    C = d["accounts"]["C_discrimination"]
    D = d["accounts"]["D_boundary"]
    live = live_recompute()

    pairs = {
        "lower_bound": (live["recomputed_lower_bound"],
                        d["derived"]["n_194_static_lower_bound"]),
        "formula_terms": (live["formula_terms"], A["formula_terms"]),
        "n_call_sites": (live["n_call_sites"], A["n_call_sites"]),
        "n_short_circuit_sites": (live["n_short_circuit_sites"],
                                  A["n_short_circuit_sites"]),
        "n_selftest_entries": (live["n_selftest_entries"],
                               A["n_selftest_entries"]),
        "hardcoded": (live["hardcoded"],
                      A["contract_runs_is_hardcoded_constant"]),
        "counter_scope": (live["counter_scope"], A["counter_scope"]),
        "ast_call_counts_194": (live["ast_call_counts_194"],
                                A["ast_call_counts_194"]),
        "call_sites": (live["call_sites"], A["call_sites"]),
        "short_circuit_sites": (live["short_circuit_sites"],
                                A["short_circuit_sites"]),
        "selftest_entries": (live["selftest_entries"], A["selftest_entries"]),
        "sup_n_registered": (live["sup_n_registered"],
                             D["superseded"]["n_registered"]),
        "c1_n": (live["c1_n"], B["per_contract"][C1]["n_runtime"]),
        "c1_hits": (live["c1_hits"], B["per_contract"][C1]["hits"]),
        "c1_lines": (live["c1_lines"], [199, 201, 311, 312]),
        "sc_n": (live["sc_n"], C["short_circuit"]["n_runtime"]),
        "sc_fails": (live["sc_fails"], C["short_circuit"]["n_fails"]),
        "sig_n": (live["sig_n"], C["signature_broken"]["n_runtime"]),
        "sig_fails": (live["sig_fails"], C["signature_broken"]["n_fails"]),
        "caliber": ({k: sha256_file(k) for k in ptc.CALIBER_FILES},
                    d["caliber"]),
    }
    diffs = [k for k, (lv, sv) in pairs.items() if lv != sv]
    cal_diffs = [k for k, want in d["caliber"].items()
                 if sha256_file(k) != want]

    ck("F1 ⛔ 同源实算 A 账逐字段对拍 + caliber 逐键磁盘 hash 零差异",
       not diffs and not cal_diffs and len(pairs) == 20
       and len(d["caliber"]) == len(ptc.CALIBER_FILES),
       ("diffs=%s cal_diffs=%s" % (diffs, cal_diffs)) if diffs or cal_diffs else "")

    ck("F2 ⛔ 本契约进程内真跑 C1 driver：运行时计数 4 且逐帧归因与存档一致",
       live["c1_driver_ok"] is True and live["c1_n"] == 4
       and live["c1_hits"] == B["per_contract"][C1]["hits"]
       and live["c1_lines"] == [199, 201, 311, 312]
       and not any(k == "c1_n" or k == "c1_hits" for k in diffs))

    ck("F3 ⛔ 事故版同源真跑：短路 4→2 且打红 1 条 / 签名事故次数仍 4 打红 2 条",
       live["sc_n"] == 2 and live["sc_fails"] == 1
       and live["sig_n"] == 4 and live["sig_fails"] == 2
       and live["sig_driver_ok"] is True
       and live["sc_n"] < live["c1_n"]
       and live["sig_n"] == live["c1_n"])

    w = C["counterfactual"]["worlds"]
    ck("F4 ⛔ 反事实四世界同源重算：静态 6/4/4/6、运行时 8/4/4/6 逐值对拍",
       w["W0_baseline"]["static"] == live["recomputed_lower_bound"] == 6
       and w["W0_baseline"]["runtime"] == (
           live["c1_n"] + B["per_contract"][C2]["n_runtime"]) == 8
       and w["W3_short_circuit"]["runtime"] == live["c1_n"] + live["sc_n"] == 6
       and w["W3_short_circuit"]["static"] == w["W0_baseline"]["static"]
       and C["counterfactual"]["static_has_no_discrimination"] is True
       and C["counterfactual"]["natural_runtime_exceeds_static"] is True)

    files = set(cpt.candidate_files() or [])
    in_surface = [p for p in PENDING if p in files]
    hits = sum(len(cpt.scan_text(cpt.read_text(ROOT / p) or ""))
               for p in PENDING)
    teeth = bool(cpt.scan_text("/Users/" + "someone" + "/V6"))
    ck("F5 A1 四文件纳入候选面 + scan_text 零命中 + 正例有牙",
       len(in_surface) == 4 and hits == 0 and teeth)

    a = anchor()
    rp = ptc.rp_call_sites()
    ck("F6 验锚 OK 3/3 + closure 全绿 + 192 分母仍 5 + 存档非 .json",
       a["ok"] is True and a["repos"] == 3 and not a["diffs"]
       and not a["caliber_diffs"]
       and len(d["closure"]) == d["derived"]["n_closure"]
       and all(v is True for v in d["closure"].values())
       and rp["match"] is True and rp["n_live"] == 5
       and SELF_ARCHIVE_NAME.endswith(".jsonl")
       and D["section_183_touched"] is False)
    return fails


def main() -> int:
    d = load()
    V = conditions(d)
    bad = verdict_from(V)
    print("=" * 74)
    print("r118 / 197  194 静态外推 6 vs runner 运行时 8")
    print("A–E %d 条：%d 通过 / %d 未通过" % (len(V), len(V) - len(bad), len(bad)))
    if bad:
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
    else:
        print("✅ 静态外推 / 运行时计数 / 短路反证 / 边界四本账全绿")
    return 1 if allbad else 0


if __name__ == "__main__":
    sys.exit(main())
