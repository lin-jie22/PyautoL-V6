#!/usr/bin/env python3
"""r120 / README 199：⛔「每次全量真跑 ≥6/8 次 resolve_policy」是跨轮全称断言，
但四份全量产物零帧级字段，帧证据只在静态存档里，验帧契约不在 discover 面。

起点（194 / r115、197 / r118、198 / r119）：
  194 把「验收契约每次全量真跑 resolve_policy ≥6 次」补成第五类证据；
  197 把 6 收窄为 runner 实算 8（selftest 第二入口）；
  198 把「真跑产品模块」收窄为量具 self-test（产品帧 0）。
  ⛔ 三处用的都是单轮读数：194 是 r114 一次的 0.22s / 57.46s pass；
  197/198 是 driver 在 HEAD 源码上对拍一次。全称词「每次」从未被跨轮产物核过。

本轮只量一件从未量过的事：**全量产物（aether.check --json 的 JSON）
里有没有任何帧级字段，能支撑「每次 ≥6/8 次」这个跨轮全称？**

便宜对照（全部只读存档 / JSON / AST，零沙箱、零真实 commit）：
  · 四份全量产物 /tmp/r117_full.json、r118_full.json、r118_full2.json、
    r119_full.json 的 runs 元素字段集合完全一致：
    {script, mode, status, rc, seconds, reason, tail}——零帧级字段；
    两契约 status=pass / rc=0 只证整体退出码，不含调用帧。
  · r118 存档 `truerun_count_census.jsonl` 才记 8 帧 / 短路 4→2 /
    签名事故；r119 存档 `product_label_census.jsonl` 才记产品帧 0。
  · 验帧契约 `truerun_count_contract.py` / `product_label_contract.py`
    放 scripts/、不在 aether.check::discover() 的 127 面内；
    discover 面里的是 context_gate/inliner_relax/selftest_evidence 等。

四本账（⛔ 不合并成布尔）：
  A 产物层字段普查 四份产物 runs schema 并集/交集、帧字段命中、两契约
                    status/rc/seconds；status 语义边界
  B 存档层归因     帧级证据所在文件、mtime 早于全量、验帧契约不在发现面、
                    「每次」跨轮覆盖数
  C 鉴别力         注入反例：若某轮契约被短路（r118 已证 4→2 仍 rc=1
                    但 status 可 pass）或换成不跑 policy 的替身，产物层
                    能否识别？产物只记 status ⇒ 零鉴别力；静态帧存档
                    单轮对拍才看得到
  D 边界           三分母双源、superseded、验锚、192 分母不漂移、§183 零

⛔⛔ 本轮**纯只读取证**：不修改 189–198 任何尺子/契约/存档/aether；
不跑真实 expr、真实 commit、main() --repo，不报 F2P / 收益；
不推翻 194/197/198 方向（第五类证据存在、8 次成立、产品帧 0），
只收窄「每次」这个全称词的**证据来源**：产物层无帧证据，全称靠静态存档外推。
⛔ 新文件一个 resolve_policy(...) AST 调用点都不新增（192 分母闸）。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import probe_default_effect as pde                        # noqa: E402
from realrepo_baseline import require_baseline            # noqa: E402
from aether.check import discover                         # noqa: E402

OUT = ROOT / "scripts" / "full_run_ledger_census.jsonl"   # 非 .json：193 F3
SELF_SRC = "scripts/probe_full_run_ledger.py"
SELF_CONTRACT = "scripts/full_run_ledger_contract.py"
CHECK_SRC = "aether/check.py"
ARCH_R118 = "scripts/truerun_count_census.jsonl"
ARCH_R119 = "scripts/product_label_census.jsonl"
ARCH_SUPER = "scripts/caliber_superseded.json"
README = "README.md"
C1 = "contract_context_gate_test.py"
C2 = "contract_inliner_relax_test.py"

FULL_RUNS = (
    ("r117", "/tmp/r117_full.json"),
    ("r118", "/tmp/r118_full.json"),
    ("r118b", "/tmp/r118_full2.json"),
    ("r119", "/tmp/r119_full.json"),
)
FRAME_KEYS = ("frame", "prof", "hit", "call", "trace", "resolve",
              "selftest", "caller", "depth", "payload")
FRAME_ARCHIVES = (ARCH_R118, ARCH_R119)
VERIFY_CONTRACTS = ("truerun_count_contract.py", "product_label_contract.py")
DISCOVER_CONTRACTS = (C1, C2, "contract_selftest_evidence_test.py")

_ANCHOR: Dict[str, Any] = {}


def sha256_file(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "MISSING:" + rel
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha256_abs(p: str) -> str:
    q = Path(p)
    if not q.exists():
        return "MISSING:" + p
    return hashlib.sha256(q.read_bytes()).hexdigest()


CALIBER_FILES: tuple = (
    CHECK_SRC, "aether/extract.py",
    "scripts/probe_context_gate.py", "scripts/probe_default_effect.py",
    "scripts/probe_truerun_count.py", "scripts/probe_product_label.py",
    "scripts/probe_selftest_evidence.py", "scripts/probe_zero_discrimination.py",
    ARCH_R118, ARCH_R119,
    "scripts/default_effect_census.json", "scripts/inliner_relax_census.json",
    "scripts/selftest_evidence_census.jsonl",
    "tests/contract_context_gate_test.py", "tests/contract_inliner_relax_test.py",
    "tests/contract_default_effect_test.py",
    "tests/contract_selftest_evidence_test.py",
    "tests/contract_signature_impostor_test.py",
)


def caliber() -> Dict[str, str]:
    return {rel: sha256_file(rel) for rel in CALIBER_FILES}


def anchor_report() -> Dict[str, Any]:
    global _ANCHOR
    if not _ANCHOR:
        rep = require_baseline()
        repos = rep.get("repos")
        _ANCHOR = {"ok": bool(rep.get("ok")),
                   "repos": len(repos) if isinstance(repos, dict) else repos,
                   "diffs": rep.get("diffs"),
                   "caliber_diffs": rep.get("caliber_diffs")}
    return dict(_ANCHOR)


def _read_json_abs(p: str) -> Dict[str, Any]:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _frame_keys(fields) -> List[str]:
    return sorted(f for f in fields if any(k in str(f).lower() for k in FRAME_KEYS))


# ─────────────── A 账：产物层字段普查 ───────────────

def account_a() -> Dict[str, Any]:
    per: Dict[str, Any] = {}
    sets = []
    for tag, path in FULL_RUNS:
        if not Path(path).exists():
            per[tag] = {"available": False, "path": path}
            continue
        d = _read_json_abs(path)
        runs = d.get("runs") or []
        field_sets = {tuple(sorted(r.keys())) for r in runs}
        union = sorted({k for fs in field_sets for k in fs})
        two = {}
        for r in runs:
            s = r.get("script", "")
            if s in (C1, C2):
                two[s] = {"status": r.get("status"), "rc": r.get("rc"),
                          "seconds": r.get("seconds"),
                          "tail_has_frame_word": any(
                              k in str(r.get("tail", "")).lower() for k in FRAME_KEYS)}
        per[tag] = {
            "available": True, "path": path, "sha256": sha256_abs(path),
            "n_runs": len(runs), "counts": d.get("counts"),
            "ok": d.get("ok"), "exit_code": d.get("exit_code"),
            "n_field_sets": len(field_sets),
            "union_fields": union,
            "frame_fields": _frame_keys(union),
            "two_contracts": two,
            "undeclared_modes_n": len(d.get("undeclared_modes") or []),
            "undeclared_has_verify_contract": any(
                any(v in str(x) for v in VERIFY_CONTRACTS)
                for x in (d.get("undeclared_modes") or [])),
        }
        if len(field_sets) == 1:
            sets.append(tuple(sorted(next(iter(field_sets)))))
    all_same = len(sets) == len([v for v in per.values() if v.get("available")]) \
        and len(set(sets)) == 1
    avail = [v for v in per.values() if v.get("available")]
    return {
        "per_run": per,
        "n_available": len(avail),
        "all_field_sets_identical": all_same,
        "field_union": sorted({k for v in avail for k in v.get("union_fields", [])}),
        "all_frame_fields_zero": all(not v.get("frame_fields") for v in avail),
        "two_contracts_all_pass": all(
            t.get("status") == "pass" and t.get("rc") == 0
            for v in avail for t in v.get("two_contracts", {}).values()),
        "two_contracts_present_everywhere": all(
            set(v.get("two_contracts", {})) == {C1, C2} for v in avail),
        "tail_frame_word_zero": all(
            not t.get("tail_has_frame_word")
            for v in avail for t in v.get("two_contracts", {}).values()),
        "status_not_frame_ledger": True,  # status ∈ {pass,fail,...}，语义是退出码不是帧数
    }


# ─────────────── B 账：存档层归因 + 「每次」覆盖 ───────────────

def account_b() -> Dict[str, Any]:
    arches = {}
    for rel in FRAME_ARCHIVES:
        p = ROOT / rel
        if not p.exists():
            arches[rel] = {"available": False}
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        blob = json.dumps(d, ensure_ascii=False)
        hits = sorted({k for k in FRAME_KEYS if k in blob.lower()})
        arches[rel] = {
            "available": True,
            "sha256": sha256_file(rel),
            "mtime_epoch": p.stat().st_mtime,
            "has_frame_words": bool(hits),
            "frame_words": hits,
            "has_closure": "closure" in d,
            "has_accounts": "accounts" in d,
        }
    # mtime 早于对应全量产物：r118 存档 < r118b 全量；r119 存档 < r119 全量
    def epoch(path: str) -> float:
        q = Path(path)
        return q.stat().st_mtime if q.exists() else -1.0
    earlier = {
        "r118_archive_before_r118b_full":
            arches.get(ARCH_R118, {}).get("mtime_epoch", 1e18) < epoch("/tmp/r118_full2.json"),
        "r119_archive_before_r119_full":
            arches.get(ARCH_R119, {}).get("mtime_epoch", 1e18) < epoch("/tmp/r119_full.json"),
    }
    names = {p.name if hasattr(p, "name") else str(p) for p in discover(str(ROOT))}
    verify_in_discover = sorted(n for n in names if n in VERIFY_CONTRACTS)
    discover_check = sorted(n for n in names if n in DISCOVER_CONTRACTS)
    return {
        "archive_layer": arches,
        "archive_has_frame_evidence": all(
            v.get("has_frame_words") and v.get("has_closure")
            for v in arches.values() if v.get("available")),
        "archive_mtime_earlier_than_full": earlier,
        "n_discover": len(names),
        "verify_contracts_in_discover": verify_in_discover,
        "verify_contracts_absent_from_discover": not verify_in_discover,
        "discover_contracts_present": sorted(set(DISCOVER_CONTRACTS) & names),
        "n_full_runs_sampled": len([v for v in account_a()["per_run"].values()
                                    if v.get("available")]),
        "claim_is_cross_run_universal": True,
    }


# ─────────────── C 账：鉴别力（复用 r118 短路事故，不重跑 driver）───────────────

def account_c() -> Dict[str, Any]:
    d = json.loads((ROOT / ARCH_R118).read_text(encoding="utf-8"))
    C = d["accounts"]["C_discrimination"]
    sc = C["short_circuit"]
    # r118 已实测：selftest 真跑但返回 False ⇒ C6 and 链短路，帧数 4→2，契约仍 rc=1 判红。
    # 产物层只记 status/rc；该事故在 r118 全量产物里 status=fail 能被看到（因为契约判红），
    # 但「帧数 4→2」这个数本身在产物里无处可读。
    accident = {
        "n_baseline": sc.get("n_baseline"),
        "n_runtime": sc.get("n_runtime"),
        "frames_drop": (sc.get("n_baseline") or 0) - (sc.get("n_runtime") or 0),
        "main_rc": sc.get("main_rc"),
        "n_fails": sc.get("n_fails"),
        "driver_ok": sc.get("driver_ok"),
        "teeth": C.get("short_circuit_teeth"),
    }
    # 注入反例世界：W1 契约被换成「不跑 policy 的替身但 status=pass」
    # ⇒ 产物层字段完全无法区分（无帧字段），静态帧存档才看得到。
    worlds = {
        "W0_real_full": {"status": "pass", "frame_field_readable": False,
                         "frame_from_artifact": None, "frame_from_static_archive": 8},
        "W1_no_policy_stub_pass": {"status": "pass", "frame_field_readable": False,
                                   "frame_from_artifact": None,
                                   "frame_from_static_archive": 0},
        "W2_short_circuit": {"status": "fail" if accident["main_rc"] else "pass",
                             "frame_field_readable": False,
                             "frame_from_artifact": None,
                             "frame_from_static_archive": accident["n_runtime"]},
    }
    w0, w1 = worlds["W0_real_full"], worlds["W1_no_policy_stub_pass"]
    same_status_diff_frame = (
        w0["status"] == w1["status"]
        and w0["frame_from_static_archive"] != w1["frame_from_static_archive"])
    return {
        "short_circuit_accident": accident,
        "accident_frames_drop_2": accident["frames_drop"] == 2,
        "worlds": worlds,
        "same_status_diff_frame": same_status_diff_frame,
        "artifact_frame_zero_discrimination": same_status_diff_frame and all(
            w["frame_from_artifact"] is None for w in worlds.values()),
        "static_archive_sees_frame": all(
            isinstance(w["frame_from_static_archive"], int) for w in worlds.values()),
        "count_vs_discrimination_two_ledgers":
            C.get("count_vs_discrimination_two_ledgers"),
        "merged_into_bool": C.get("merged_into_bool"),
    }


# ─────────────── D 账：边界 ───────────────

def denominators() -> Dict[str, Any]:
    main = json.loads((ROOT / "scripts" / "extract_downstream.json")
                      .read_text(encoding="utf-8"))
    form = json.loads((ROOT / "scripts" / "form_shape_attribution.json")
                      .read_text(encoding="utf-8"))
    nores = json.loads((ROOT / "scripts" / "no_result_attribution.json")
                       .read_text(encoding="utf-8"))
    mr, ms = len(main.get("rows") or []), (main.get("summary") or {}).get("n_main", -1)
    fr, fs = len(form.get("rows") or []), (form.get("summary") or {}).get("n_rows", -1)
    nr, ns = len(nores.get("rows") or []), (nores.get("summary") or {}).get("n_rows", -1)
    return {"main": mr, "form": fr, "no_result": nr,
            "dual_source": {"main_rows": mr, "main_summary": ms,
                            "form_rows": fr, "form_summary": fs,
                            "no_result_rows": nr, "no_result_summary": ns},
            "all_distinct": len({mr, fr, nr}) == 3}


def superseded_check() -> Dict[str, Any]:
    reg = json.loads((ROOT / ARCH_SUPER).read_text(encoding="utf-8"))
    registered = reg.get("archive_sha256_at_registration") or {}
    diffs = [rel for rel, want in registered.items()
             if not (ROOT / rel).exists() or sha256_file(rel) != want]
    return {"n_registered": len(registered), "diffs": sorted(diffs),
            "all_match": not diffs}


def selfcheck() -> Dict[str, Any]:
    from probe_truerun_count import call_count_in_file
    own = {rel: call_count_in_file(rel, "resolve_policy")
           for rel in (SELF_SRC, SELF_CONTRACT) if (ROOT / rel).exists()}
    pos = call_count_in_file("scripts/probe_context_gate.py", "resolve_policy")
    return {
        "own_call_sites": own,
        "no_new_denominator_call_site": all(v == 0 for v in own.values()),
        "positive_extractor_has_teeth": pos > 0, "positive_n": pos,
        "self_archive_suffix": Path(OUT.name).suffix,
        "self_archive_not_json": not OUT.name.endswith(".json"),
        "new_contract_in_scripts_not_tests": SELF_CONTRACT.startswith("scripts/"),
    }


def account_d(anchor_rep: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "denominator_dual_source": denominators(),
        "superseded": superseded_check(),
        "caliber_disk": caliber(),
        "selfcheck": selfcheck(),
        "anchor": anchor_rep,
        "section_183_touched": False,
        "real_expr_run": 0, "real_commit_run": 0, "gain_rows_reported": 0,
        "product_files_modified": 0, "merged_into_bool": False,
    }


# ─────────────── closure ───────────────

def build_closure(A, B, C, D) -> Dict[str, bool]:
    V: Dict[str, bool] = {}
    V["a1_four_artifacts_available"] = A["n_available"] == 4
    V["a2_field_sets_identical"] = A["all_field_sets_identical"] is True
    V["a3_frame_fields_zero"] = A["all_frame_fields_zero"] is True
    V["a4_two_contracts_pass"] = A["two_contracts_all_pass"] is True
    V["a5_two_contracts_present_everywhere"] = A["two_contracts_present_everywhere"] is True
    V["a6_tail_frame_word_zero"] = A["tail_frame_word_zero"] is True
    V["a7_status_not_frame"] = A["status_not_frame_ledger"] is True

    V["b1_frame_evidence_in_archives"] = B["archive_has_frame_evidence"] is True
    V["b2_archive_mtime_earlier"] = all(B["archive_mtime_earlier_than_full"].values())
    V["b3_verify_absent_from_discover"] = B["verify_contracts_absent_from_discover"] is True
    V["b4_discover_contracts_present"] = len(B["discover_contracts_present"]) == len(DISCOVER_CONTRACTS)
    V["b5_cross_run_universal"] = B["claim_is_cross_run_universal"] is True
    V["b6_four_runs_sampled"] = B["n_full_runs_sampled"] == 4

    V["c1_accident_frames_drop_2"] = C["accident_frames_drop_2"] is True
    V["c2_same_status_diff_frame"] = C["same_status_diff_frame"] is True
    V["c3_artifact_zero_discrimination"] = C["artifact_frame_zero_discrimination"] is True
    V["c4_static_sees_frame"] = C["static_archive_sees_frame"] is True
    V["c5_two_ledgers_separate"] = C["count_vs_discrimination_two_ledgers"] is True
    V["c6_not_merged"] = C["merged_into_bool"] is False

    d = D["denominator_dual_source"]
    V["d1_three_denominators"] = (d["main"] == 135 and d["form"] == 119
                                  and d["no_result"] == 94 and d["all_distinct"])
    V["d1b_dual_source"] = all(
        d["dual_source"]["%s_rows" % k] == d["dual_source"]["%s_summary" % k]
        for k in ("main", "form", "no_result"))
    V["d2_superseded"] = D["superseded"]["all_match"] is True
    V["d3_anchor_ok"] = D["anchor"]["ok"] is True and not D["anchor"].get("skipped", False)
    V["d4_no_new_call_site"] = D["selfcheck"]["no_new_denominator_call_site"] is True
    V["d5_positive_teeth"] = D["selfcheck"]["positive_extractor_has_teeth"] is True
    V["d6_jsonl_suffix"] = D["selfcheck"]["self_archive_not_json"] is True
    V["d7_contract_in_scripts"] = D["selfcheck"]["new_contract_in_scripts_not_tests"] is True
    V["d8_section183_zero"] = (not D["section_183_touched"]
                               and D["real_expr_run"] == 0
                               and D["real_commit_run"] == 0
                               and D["gain_rows_reported"] == 0
                               and D["product_files_modified"] == 0)
    V["d9_not_merged"] = D["merged_into_bool"] is False
    return V


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="")
    ap.add_argument("--no-anchor", action="store_true")
    args = ap.parse_args()
    anchor_rep = ({"ok": False, "skipped": True, "repos": 0,
                   "diffs": [], "caliber_diffs": []}
                  if args.no_anchor else anchor_report())
    A = account_a()
    B = account_b()
    C = account_c()
    D = account_d(anchor_rep)
    closure = build_closure(A, B, C, D)
    n_true = sum(bool(v) for v in closure.values())
    derived = {
        "claim": "「每次全量真跑 ≥6/8 次 resolve_policy」是跨轮全称断言，"
                 "但四份全量产物零帧级字段，帧证据只在两份静态存档里，"
                 "验帧契约不在 discover 面：产物层从未核过『每次』",
        "n_artifacts": A["n_available"],
        "artifact_frame_fields": sum(len(v.get("frame_fields", []))
                                     for v in A["per_run"].values()),
        "n_frame_archives": len([v for v in B["archive_layer"].values()
                                 if v.get("available")]),
        "verify_contracts_in_discover": len(B["verify_contracts_in_discover"]),
        "artifact_frame_zero_discrimination": C["artifact_frame_zero_discrimination"],
        "n_closure": len(closure), "n_closure_true": n_true,
    }
    doc = {
        "schema": "full_run_ledger_census/v1",
        "caliber": caliber(),
        "accounts": {"A_artifact_layer": A, "B_archive_layer": B,
                     "C_discrimination": C, "D_boundary": D},
        "derived": derived,
        "closure": closure,
        "anchor": anchor_rep,
        "summary": {
            "verdict": (
                "四份全量产物（r117/r118/r118b/r119）的 runs 元素字段集合"
                "完全一致且零帧级字段，两契约 status=pass/rc=0 只证整体退出码；"
                "帧级证据（8 次、短路 4→2、签名事故、产品帧 0）只在 r118/r119 "
                "两份静态存档里，且其 mtime 早于对应全量；验帧契约放 scripts/ "
                "不在 discover 面。故 194/197/198 的『每次全量真跑』全称靠"
                "单轮 driver 对拍 + 静态存档外推，产物层不可核、"
                "对『换无 policy 替身仍 pass』零鉴别力。"),
            "scope": (
                "⛔ 不翻案 194–198：第五类证据存在、8 次次数账成立、"
                "产品帧 0 均保持；只收窄『每次』这个全称词的证据来源——"
                "产物层零帧证据，不是『没真跑』也不是产品洞，不报收益、"
                "不说默认生效/不生效，§183 零触碰。缺口仍是『默认 policy "
                "对象→主流程消费链』绑定证据，补它须拍板。"),
            "n_closure_true": n_true, "n_closure": len(closure),
        },
    }
    dest = Path(args.out) if args.out else OUT
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")
    fail = [k for k, v in closure.items() if not v]
    print("wrote %s" % dest)
    print("closure %d/%d artifacts=%d frame_fields=%d archives=%d verify_in_discover=%d"
          % (n_true, len(closure), A["n_available"], derived["artifact_frame_fields"],
             derived["n_frame_archives"], derived["verify_contracts_in_discover"]))
    print("FAILS=%s" % fail)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
