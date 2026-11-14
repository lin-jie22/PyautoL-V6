#!/usr/bin/env python3
"""r112 / README 191：190 的「两口径」只切了 1/4 位 policy —— 离线可回归契约。

  A 组  钉作用点读数（RelaxPolicy 4 位 / 190 调用链 / 触达 1/4 / 抽取器正反例）
  B 组  钉 135 行输入可得性（0/135）+ 189 合成 case 输入完备性
  C 组  钉两口径逐行 outcome 对拍（补 190 聚合粒度）+ 翻转探测器 4/4 有牙
  D 组  钉边界（superseded 登记 / 三分母双源 / 零沙箱 / 验锚读数）
  E 组  钉 closure 36/36 + derived / summary
  F 组  本机实战（真跑）：磁盘 hash、AST 抽取器反证、同源实算 4 行、
        翻转探测器事故版、A1 扫描面、验锚 + 尺子同源重算

⛔ 首次全绿必须注入反证（`/tmp/r112_falsify.py`）：A–E 组逐条把条件
   置 False ⇒ 恰好该项转红；F 组用 `R112_WITHDRAW=Fn` 同进程逐条撤回。
⛔ 判据与被测对象同源：四位字段从 `class RelaxPolicy` AST 抽、所需输入键
   从产品函数签名 AST 派生、逐行 outcome 全走产品 `adopt_probe`。
⛔ 本契约纯只读取证：零真实 expr、零真实 commit、零 F2P / 收益读数，
   不改 189 已拍板的判据语义，也不改 190 的结论方向。
"""
from __future__ import annotations

import ast
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
#: ⛔ 契约根从 __file__ 推导（第 99 条）：事故版被反证脚本放到 /tmp 跑时
#:    cwd 不再是 tests/ ⇒ 显式把 tests/ 加进 sys.path。
sys.path.insert(0, str(ROOT / "tests"))

from contract_portability_test import (                            # noqa: E402
    candidate_files, scan_text,
)
from realrepo_baseline import require_baseline                    # noqa: E402
import probe_policy_reach as pir                                  # noqa: E402

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)
CENSUS = ROOT / "scripts" / "policy_reach_census.json"
FAILS: List[str] = []
CG = pir.CG
E = pir.E
D = json.loads(CENSUS.read_text(encoding="utf-8"))


def ck(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (" | " + str(detail) if detail and not cond else ""))


def emit(V: Dict[str, bool]) -> List[str]:
    """纯函数：把条件字典转成失败名列表，供反证脚本逐条置 False。"""
    fails: List[str] = []
    for name, cond in V.items():
        if not cond:
            fails.append(name)
    return fails


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    """A–E 组：**纯函数**，零沙箱零子进程，供反证脚本逐条篡改。"""
    V: Dict[str, bool] = {}
    acc = d.get("accounts", {})
    A = acc.get("A_reach", {})
    B = acc.get("B_input_availability", {})
    C = acc.get("C_rowwise_outcome", {})
    F = acc.get("D_boundary", {})
    der = d.get("derived", {})
    clo = d.get("closure", {})
    cal = d.get("caliber", {})
    rows = B.get("rows", [])
    crows = C.get("rows", [])

    # ── A 组：四位作用点 ────────────────────────────────────────
    V["A1"] = d.get("schema") == "policy_reach_census/v1"
    V["A2"] = (A.get("fields") ==
               ["cross_scope", "first_rhs", "allow_unstable", "no_gold_gate"]
               and A.get("n_fields") == 4)
    V["A3"] = A.get("n_fields_with_reader") == 4 and len(A.get("readers", {})) == 4
    V["A4"] = A.get("readers", {}).get("cross_scope") == ["merge_bindings"]
    V["A5"] = A.get("readers", {}).get("first_rhs") == ["bindings_for"]
    V["A6"] = sorted(A.get("readers", {}).get("allow_unstable", [])) == \
        ["evaluate_case", "main"]
    V["A7"] = A.get("readers", {}).get("no_gold_gate") == ["adopt_probe"]
    V["A8"] = A.get("b190_calls") == \
        ["adopt_probe", "classify_probe", "scoped_assertions"]
    V["A9"] = A.get("b190_calls_taking_policy") == ["adopt_probe"]
    V["A10"] = A.get("fields_read_by_b190") == ["no_gold_gate"]
    V["A11"] = A.get("reach_ratio") == "1/4" and A.get("n_reachable") == 1
    V["A12"] = sorted(A.get("unreachable_fields", [])) == \
        ["allow_unstable", "cross_scope", "first_rhs"]
    V["A13"] = A.get("extractor_positive_hit") == 4 \
        and A.get("extractor_positive_total") == 4
    V["A14"] = A.get("extractor_negative_hit") == 0

    # ── B 组：135 行输入可得性 + 189 合成 case ────────────────────
    V["B1"] = B.get("n_rows") == 135 and len(rows) == 135
    V["B2"] = B.get("required_input_keys") == \
        ["envs", "test_src", "unstable_runs"] and B.get("n_required_keys") == 3
    V["B3"] = B.get("per_key_present") == \
        {"envs": 0, "test_src": 0, "unstable_runs": 0}
    V["B4"] = B.get("n_rows_with_any_input") == 0
    V["B5"] = B.get("n_rows_with_all_inputs") == 0
    V["B6"] = B.get("by_k") == \
        {"UNSPLITTABLE": 41, "none": 72, "ok_nogold": 22}
    V["B7"] = B.get("by_k_sum") == 135
    V["B8"] = B.get("by_verdict_sum") == 135
    V["B9"] = all(r.get("n_present") == 0 for r in rows)
    V["B10"] = B.get("cases_189", {}).get("n_cases") == 6
    V["B11"] = B.get("cases_189", {}).get("n_with_src") == 6
    V["B12"] = B.get("cases_189", {}).get("n_with_unstable_runs") == 6
    V["B13"] = (B.get("cases_189", {}).get("n_archived_ids") == 6
                and B.get("cases_189", {}).get("archive_has_src") == 0)
    V["B14"] = ("src" in B.get("cases_189", {}).get("union_keys", [])
                and "unstable_runs" in B.get("cases_189", {}).get("union_keys", []))

    # ── C 组：两口径逐行 outcome 对拍 ────────────────────────────
    V["C1"] = C.get("n_rows") == 135 and len(crows) == 135
    V["C2"] = C.get("n_ledger_changed") == 135
    V["C3"] = C.get("n_verified_flip") == 0
    V["C4"] = C.get("n_counts_disc_flip") == 0
    V["C5"] = C.get("group_sum") == 135 and C.get("n_groups") == 6
    V["C6"] = C.get("same_row_with_190") == 135 and C.get("n_190_rows") == 135
    V["C7"] = (C.get("flip_detector_teeth") == 4
               and C.get("flip_detector_total") == 4)
    V["C8"] = all(r.get("ledger_changed") is True
                  and r.get("verified_flip") is False
                  and r.get("counts_disc_flip") is False for r in crows)
    V["C9"] = all(g.get("ledger_changed") == g.get("n")
                  for g in C.get("by_group", {}).values())
    V["C10"] = len(C.get("flip_detector_cases", [])) == 4 \
        and all(t.get("n_flips", 0) > 0
                for t in C.get("flip_detector_cases", []))

    # ── D 组：边界 ───────────────────────────────────────────────
    V["D1"] = F.get("policy_default") == "1111"
    V["D2"] = F.get("policy_strict") == "0000"
    V["D3"] = (F.get("superseded_sha_matches") == 3
               and F.get("superseded_total") == 3)
    V["D4"] = (F.get("superseded_new_sha_on_disk")
               == F.get("superseded_new_sha_registered"))
    V["D5"] = F.get("denominators") == \
        {"main_135": 135, "form_119": 119, "no_result_94": 94}
    dual = F.get("denominator_dual_source", {})
    V["D6"] = (dual.get("form_rows") == dual.get("form_summary") == 119
               and dual.get("no_result_rows")
               == dual.get("no_result_summary") == 94)
    V["D7"] = F.get("denominators_kept_separate") is True
    V["D8"] = (F.get("sandbox_selfcheck", {}).get("n_banned") == 0
               and F.get("n_forbidden_runtime_calls") == 0
               and F.get("sandbox_selfcheck", {}).get("checked_own_source")
               is True)
    V["D9"] = all(F.get(k) == 0 for k in
                  ("real_expr_runs", "real_commit_runs",
                   "f2p_runs", "gain_reported"))
    V["D10"] = (d.get("anchor", {}).get("ok") is True
                and d.get("anchor", {}).get("repos") == 3
                and not d.get("anchor", {}).get("diffs")
                and not d.get("anchor", {}).get("caliber_diffs"))

    # ── E 组：closure / derived / summary ────────────────────────
    V["E1"] = len(clo) == 36 and all(bool(v) for v in clo.values())
    V["E2"] = (d.get("summary", {}).get("n_closure") == 36
               and d.get("summary", {}).get("n_closure_true") == 36)
    V["E3"] = der.get("reach_ratio") == "1/4"
    V["E4"] = (der.get("n_rows_input_available") == 0
               and der.get("n_rows_input_unavailable") == 135)
    V["E5"] = len(cal) == 12 \
        and "scripts/probe_policy_reach.py" in cal
    V["E6"] = len(set(cal.values())) == len(cal)

    # ── G 组：同源实算（⛔ 不读存档结论字段，专供事故版补丁打红）──
    V["G1"] = pir.policy_fields_from_source() == A.get("fields")
    V["G2"] = sum(t["n_flips"] > 0
                  for t in pir.flip_detector_cases()) == 4
    live = live_replay(d)
    by_i = {r["i"]: r for r in crows}
    V["G3"] = (len(live) == 4
               and all(x == by_i.get(x["i"]) for x in live))
    V["G4"] = len(pir.cases_from_source()) == 6
    V["G5"] = len(pir.caliber()) == 12
    return V


def live_replay(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    """⛔ 同源实算：不读 census 的结论字段，只用**产品自己的函数** +
    177 原存档重算 4 行。供反证脚本打补丁验证判据有牙（B4 型反证）。"""
    arch = json.loads(
        (ROOT / "scripts" / "extract_downstream.json")
        .read_text(encoding="utf-8"))["rows"]
    out: List[Dict[str, Any]] = []
    #: ⛔ 取样必须含「可拆 + got 有记录」的一行（i=3，22 条 ok_nogold 之一）：
    #:    否则 classify_probe 恒返 "none" 的事故版在构造上改不动任何一行 ⇒
    #:    反证组会假绿（r112 首跑真红留痕）。
    for i in (0, 3, 100, 130):
        r = arch[i]
        try:
            a = E.scoped_assertions(r.get("stmt") or "")
        except SyntaxError:
            a = []
        act, exp, _fn = a[0] if a else ("", "", "")
        k = (CG.classify_probe(act, exp, r.get("got_fixed")) if a
             else "UNSPLITTABLE")
        unstable = (r.get("gate") == "unstable")
        disc = bool(r.get("truthy_f2p"))
        s = CG.adopt_probe(k, unstable, CG.STRICT, disc)
        x = CG.adopt_probe(k, unstable, CG.RELAXED, disc)
        out.append({
            "i": i, "k": k, "unstable": unstable, "disc": disc,
            "strict": {"ledger": s[0], "verified": s[1], "counts_disc": s[2]},
            "relaxed": {"ledger": x[0], "verified": x[1], "counts_disc": x[2]},
            "ledger_changed": s[0] != x[0],
            "verified_flip": s[1] != x[1],
            "counts_disc_flip": s[2] != x[2],
        })
    return out


def run_c() -> None:
    """F 组：本机实战（真跑）。⛔ 同进程、不 spawn 子进程。"""
    withdraw = os.environ.get("R112_WITHDRAW", "")

    # F1 磁盘 hash 对拍（caliber 12 项全部与磁盘一致）
    cal = D["caliber"]
    bad = [k for k, v in cal.items()
           if not (ROOT / k).exists()
           or pir.sha256_file(k) != v]
    ck("F1 caliber 12 项磁盘 hash 全对拍（旧存档零回写）",
       not bad and not withdraw == "F1", str(bad))

    # F2 AST 抽取器同源反证：正例 4/4 命中、反例 0（查询既非恒真也非恒空）
    fields = tuple(pir.policy_fields_from_source())
    pos_src = "\n".join("def f_%s(policy):\n    return policy.%s\n" % (f, f)
                        for f in fields)
    pos = pir.policy_reads(ast.parse(pos_src), fields)
    neg = pir.policy_reads(ast.parse("def g(policy):\n    return 1\n"), fields)
    f2 = (len(fields) == 4
          and sum(1 for f in fields if pos[f]) == 4
          and sum(len(v) for v in neg.values()) == 0)
    ck("F2 ⛔ AST 抽取器正例 4/4 + 反例 0（零命中先证查询能命中）",
       f2 and not withdraw == "F2", str(pos))

    # F3 同源实算 4 行 → 与 C 账逐字段对拍（使事故版补丁有牙）
    live = live_replay(D)
    crow = {r["i"]: r for r in D["accounts"]["C_rowwise_outcome"]["rows"]}
    f3 = len(live) == 4 and all(
        live[j] == crow[live[j]["i"]] for j in range(len(live)))
    ck("F3 同源实算 4 行与 C 账逐字段对拍（B4 型反证有牙）",
       f3 and not withdraw == "F3", str([r["i"] for r in live]))

    # F4 翻转探测器事故版：把 adopt_probe 换成恒同函数 ⇒ teeth 必须归 0
    teeth_live = pir.flip_detector_cases()
    n_live = sum(t["n_flips"] > 0 for t in teeth_live)
    orig = CG.adopt_probe
    try:
        CG.adopt_probe = lambda k, u, p, dc: ("same", True, False)
        n_bad = sum(t["n_flips"] > 0 for t in pir.flip_detector_cases())
    finally:
        CG.adopt_probe = orig
    ck("F4 ⛔ 翻转探测器 4/4 有牙；事故版恒同 adopt_probe ⇒ teeth=0",
       n_live == 4 and n_bad == 0 and not withdraw == "F4",
       "live=%s bad=%s" % (n_live, n_bad))

    # F5 A1 扫描面：3 个新文件在候选面 + 零命中 + 正例反证
    cfiles = set(candidate_files() or [])
    new_files = ("scripts/probe_policy_reach.py",
                 "scripts/policy_reach_census.json",
                 "tests/contract_policy_reach_test.py")
    sample = "ROOT = \"" + str(Path.home()) + "/V6\""
    f5 = (all(f in cfiles for f in new_files)
          and not scan_text((ROOT / "scripts" / "probe_policy_reach.py")
                            .read_text(encoding="utf-8"))
          and bool(scan_text(sample)))
    ck("F5 A1 扫描面：3 个新文件在候选面 / 零命中 / 正例反证有牙",
       f5 and not withdraw == "F5", str(sorted(f for f in new_files
                                             if f not in cfiles)))

    # F6 验锚 + 尺子同源重算（⛔ 一次真实验锚读数缓存，不重复 census）
    anchor = require_baseline()
    f6 = bool(anchor.get("ok")) and not anchor.get("diffs") \
        and not anchor.get("caliber_diffs")
    A2 = pir.account_a()
    C2 = pir.account_c()
    B2 = pir.account_b(C2["rows"])
    F2 = pir.account_d({"ok": f6, "repos": len(anchor.get("repos") or {}),
                        "diffs": [], "caliber_diffs": []})
    same = (A2["reach_ratio"] == D["accounts"]["A_reach"]["reach_ratio"]
            and B2["n_rows_with_all_inputs"] ==
            D["accounts"]["B_input_availability"]["n_rows_with_all_inputs"]
            and C2["n_verified_flip"] ==
            D["accounts"]["C_rowwise_outcome"]["n_verified_flip"]
            and F2["denominators"] ==
            D["accounts"]["D_boundary"]["denominators"]
            and all(D["closure"].values()))
    ck("F6 验锚 OK 3/3 + 尺子同源重算幂等 + closure 36/36",
       f6 and same and not withdraw == "F6", str(anchor))


def main() -> int:
    print("=" * 74)
    print("r112 / README 191：190 的「两口径」只切了 4 位 policy 中的 1 位")
    print("=" * 74)
    print("== A–E 组：作用点 / 输入可得性 / 逐行对拍 / 边界 / closure ==")
    V = conditions(D)
    FAILS.extend(emit(V))
    for name, cond in V.items():
        print(("  PASS " if cond else "  FAIL ") + name)
    print("== F 组：本机实战 ==")
    run_c()
    print("=" * 74)
    if FAILS:
        print("❌ FAILS=" + ",".join(FAILS))
        return 1
    print("✅ 全部通过（A–E %d 项 + F 组 6 项）" % len(V))
    print("190 两口径只触达 no_gold_gate（1/4）；另 3 位所需输入在 135 主账"
          "0/135 可得 ⇒ 尺子代表性边界，不是产品洞；不报收益")
    return 0


if __name__ == "__main__":
    sys.exit(main())
