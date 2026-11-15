#!/usr/bin/env python3
"""契约：r114 / README 193 —— 192 B 账零命中的鉴别力。

⛔ 结构（沿用 r109/r111/r112/r113 分层反证）：
  · `conditions(d)` 是**纯函数**：只读传入的存档 dict，零磁盘、零沙箱、
    零子进程，供反证脚本逐条置 False / 篡改字段。
  · `run_c()` 是 F 组本机实战：同源实算（不读存档结论字段）、事故版
    补丁打红点、一次真实验锚读数缓存、A1 四文件纳入。

⛔ 契约⑤：缺键记「不适用」，不读成零命中；顶层非 dict 存档同理。
⛔ 第 133 条：零命中判据必须带正例 + 反例证明查询能命中。
⛔ 第 34 条：尺子自身真红一律修尺子、不放宽断言。
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
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
                                       read_text)


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pzd = _load("pzd_r114", "scripts/probe_zero_discrimination.py")
CG = _load("pcg_r114c", "scripts/probe_context_gate.py")
# ⛔ 复用尺子内部的 PDE 实例，不许再 _load 一份同源码模块：
#    否则事故版 `archive_shape` 补丁打在另一实例上 ⇒ F3 恒绿（r114 反证真红）。
PDE = pzd.PDE

ARCH = ROOT / "scripts" / "zero_discrimination_census.json"
_ANCHOR: Dict[str, Any] | None = None

PRODUCT_KEYS = ["policy", "tally", "stats", "per_commit", "rows"]
DETECTOR_KEYS = ["per_commit", "stats", "policy"]
WORLD_HITS = {"W0_no_run": 0, "W1_ran_out_tmp": 0,
              "W2_ran_out_scripts": 1, "W3_ran_no_out": 0}
WORLD_IN_SCRIPTS = {"W0_no_run": False, "W1_ran_out_tmp": False,
                    "W2_ran_out_scripts": True, "W3_ran_no_out": False}


def load() -> Dict[str, Any]:
    return json.loads(ARCH.read_text(encoding="utf-8"))


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    """A–E 组：**纯函数**，零沙箱零子进程，供反证脚本逐条篡改。"""
    V: Dict[str, bool] = {}
    A = d["accounts"]["A_product_shape"]
    B = d["accounts"]["B_scan_surface"]
    C = d["accounts"]["C_counterfactual"]
    D = d["accounts"]["D_boundary"]
    W = C["worlds"]
    sc = A["extractor_selfcheck"]
    gs = B["scan_contract"]["glob_extractor_selfcheck"]

    # ── A：产品真实写盘形态 ──
    V["A1"] = sorted(A["product_out_keys"]) == sorted(PRODUCT_KEYS)
    V["A2"] = A["n_product_out_keys"] == 5
    V["A3"] = A["out_default"] == ""
    V["A4"] = A["writes_only_if_out"] is True
    V["A5"] = A["out_target"] == "args.out"
    V["A6"] = A["out_path_unconstrained"] is True
    V["A7"] = A["detector_keys"] == DETECTOR_KEYS
    V["A8"] = A["n_detector_keys"] == 3
    V["A9"] = A["detector_is_subset"] is True
    V["A10"] = A["key_coverage"] == "3/5"
    V["A11"] = sorted(A["detector_missing_keys"]) == ["rows", "tally"]
    V["A12"] = (sorted(A["detector_positive_keys"])
                == ["per_commit", "policy", "rows", "stats"])
    V["A13"] = A["detector_positive_is_literal"] is True
    V["A14"] = A["detector_positive_from_product_ast"] is False
    V["A15"] = sc["pos_hit"] == 4
    V["A16"] = sc["neg_hit"] == 0
    V["A17"] = sc["attr_form_teeth"] is True
    V["A18"] = len(sc["pos_cases"]) == 4 and all(
        isinstance(c, list) and c for c in sc["pos_cases"])
    V["A19"] = "构造必然" not in A["boundary"] and "子集" in A["boundary"]

    # ── B：扫描面代表性（⛔ 逐件断言内容，不只断言长度） ──
    V["B1"] = B["n_all_json_in_repo"] >= 1000
    V["B2"] = B["excluded_self_product"] == ["zero_discrimination_census.json"]
    V["B3"] = B["n_scripts_json"] == 29
    V["B4"] = B["n_rea_evidence"] == 1197
    V["B5"] = B["n_other_json"] == 54
    V["B6"] = (B["n_scripts_json"] + B["n_rea_evidence"]
               + B["n_other_json"] == B["n_all_json_in_repo"])
    V["B7"] = B["coverage_scripts"] == 0.0227
    _top = B["other_dirs_top"]
    V["B8"] = (isinstance(_top, list) and len(_top) > 0
               and all(isinstance(x, list) and len(x) == 2
                       and isinstance(x[0], str) and isinstance(x[1], int)
                       and x[1] > 0 for x in _top)
               and sum(x[1] for x in _top) <= B["n_other_json"])
    V["B9"] = B["b192_glob_root"] == "scripts"
    V["B10"] = B["b192_glob_pattern"] == "*.json"
    V["B11"] = B["b192_n_scanned"] == 28
    V["B12"] = B["b192_excluded"] == ["default_effect_census.json"]
    V["B13"] = B["b192_scanned_from_archive"] is True
    _scan = B["scan_contract"]
    V["B14"] = _scan["pattern"] == "*.json"
    V["B15"] = _scan["glob_source_function"] == "account_b"
    V["B16"] = _scan["keys"] == DETECTOR_KEYS
    V["B17"] = _scan["excluded"] == ["default_effect_census.json"]
    V["B18"] = gs["pos"] == "*.json"
    V["B19"] = gs["neg"] == ""
    V["B20"] = gs["teeth"] is True
    V["B21"] = B["idempotent_by_explicit_exclusion"] is True
    V["B22"] = "scripts/*.json" in B["boundary"]

    # ── C：反事实四世界 ──
    V["C1"] = C["product_shape_recognised"] is True
    V["C2"] = sorted(C["product_shape_keys"] or []) == sorted(PRODUCT_KEYS)
    V["C3"] = sorted(W.keys()) == sorted(WORLD_HITS.keys())
    for i, (k, want) in enumerate(WORLD_HITS.items(), 1):
        V["C%d" % (3 + i)] = W[k]["b_hits"] == want
    for i, (k, want) in enumerate(WORLD_IN_SCRIPTS.items(), 1):
        V["C%d" % (7 + i)] = W[k]["in_scripts"] is want
    V["C12"] = W["W0_no_run"]["shape_available"] is False
    V["C13"] = W["W3_ran_no_out"]["shape_available"] is False
    V["C14"] = (W["W0_no_run"]["shape"].get("applicable") is False
                and W["W3_ran_no_out"]["shape"].get("applicable") is False)
    V["C15"] = (W["W1_ran_out_tmp"]["shape_available"] is True
                and W["W2_ran_out_scripts"]["shape_available"] is True)
    V["C16"] = (sorted(W["W1_ran_out_tmp"]["shape"].get("keys") or [])
                == sorted(PRODUCT_KEYS)
                and sorted(W["W2_ran_out_scripts"]["shape"].get("keys") or [])
                == sorted(PRODUCT_KEYS))
    V["C17"] = (W["W1_ran_out_tmp"]["rel"] == "tmp/main_out.json"
                and W["W2_ran_out_scripts"]["rel"] == "scripts/main_out.json")
    V["C18"] = sorted(C["zero_worlds"]) == [
        "W0_no_run", "W1_ran_out_tmp", "W3_ran_no_out"]
    V["C19"] = C["n_zero_worlds"] == 3
    V["C20"] = C["n_zero_worlds_with_run"] == 2
    V["C21"] = sorted(C["ran_zero_worlds"]) == [
        "W1_ran_out_tmp", "W3_ran_no_out"]
    V["C22"] = C["discriminates"] is False
    V["C23"] = C["detector_teeth_on_w2"] is True
    V["C24"] = C["b192_original_hits"] == 0
    V["C25"] = C["zero_is_constructive_necessity"] is True
    V["C26"] = C["merged_into_bool"] is False
    V["C27"] = "构造必然" in C["boundary"]
    for i, (k, want) in enumerate(WORLD_HITS.items(), 1):
        V["C%d" % (27 + i)] = (W[k]["truth"] != ""
                               and isinstance(W[k]["truth"], str)
                               and W[k]["b_hits"] == want)
    # ⛔ 无产物世界的 rel 必须为 None（反证首跑发现该字段无人断言 ⇒ 补上）
    V["C32"] = (W["W0_no_run"]["rel"] is None
                and W["W3_ran_no_out"]["rel"] is None)

    # ── D：边界 ──
    V["D1"] = D["policy_default"] == "1111"
    V["D2"] = D["policy_strict"] == "0000"
    V["D3"] = D["verdict_192_unchanged"] is True
    V["D4"] = (len(D["support_moved_to"]) == 2
               and "A " in D["support_moved_to"][0]
               and "C " in D["support_moved_to"][1])
    V["D5"] = D["not_a_product_hole"] is True
    V["D6"] = D["not_default_disabled"] is True
    V["D7"] = D["b192_archive_rewritten"] is False
    V["D8"] = D["archives_189_192_rewritten"] is False
    V["D9"] = D["aether_rewritten"] is False
    V["D10"] = D["denominators"] == {"main": 135, "form": 119,
                                    "no_result": 94}
    _ds = D["denominator_dual_source"]
    V["D11"] = _ds["form_rows"] == _ds["form_summary"] == 119
    V["D12"] = (_ds["no_result_rows"] == _ds["no_result_summary"] == 94)
    V["D13"] = D["denominators_kept_separate"] is True
    V["D14"] = D["superseded_total"] == D["superseded_sha_matches"] == 3
    V["D15"] = D["sandbox_selfcheck"]["n_banned"] == 0
    V["D16"] = D["sandbox_selfcheck"]["checked_own_source"] is True
    V["D17"] = all(D[k] == 0 for k in
                   ("real_expr_runs", "real_commit_runs", "f2p_runs",
                    "gain_reported", "n_forbidden_runtime_calls"))
    V["D18"] = "§183" in D["boundary"]

    # ── E：顶层 ──
    V["E1"] = d["schema"] == "zero_discrimination_census/v1"
    _cal = d["caliber"]
    V["E2"] = len(_cal) == 16
    V["E3"] = "aether/extract.py" in _cal
    V["E4"] = "scripts/default_effect_census.json" in _cal
    V["E5"] = all(isinstance(v, str) and len(v) == 64 for v in _cal.values())
    _clo = d["closure"]
    V["E6"] = len(_clo) == 47
    V["E7"] = all(v is True for v in _clo.values())
    _der = d["derived"]
    V["E8"] = _der["discriminates"] is False
    V["E9"] = _der["key_coverage"] == "3/5"
    V["E10"] = _der["scan_coverage"] == 0.0227
    V["E11"] = _der["n_zero_worlds_with_run"] == 2
    V["E12"] = _der["n_closure"] == _der["n_closure_true"] == 47
    _anc = d["anchor"]
    V["E13"] = _anc.get("ok") is True
    V["E14"] = _anc.get("repos") == 3
    V["E15"] = _anc.get("diffs") == []
    V["E16"] = _anc.get("caliber_diffs") == []
    V["E17"] = "构造必然" in d["summary"]["verdict"]
    V["E18"] = "不推翻 192 结论方向" in d["summary"]["scope"]
    V["E19"] = sorted(d["accounts"].keys()) == [
        "A_product_shape", "B_scan_surface", "C_counterfactual",
        "D_boundary"]
    V["E20"] = (d["summary"]["n_closure"]
                == d["summary"]["n_closure_true"] == 47)
    return V


# ─────────────────── F 组：本机实战 ───────────────────
def live_recompute() -> Dict[str, Any]:
    """⛔ 同源实算：不读存档结论字段，只用尺子抽取器 + 产品源码重算。"""
    tree = pzd.parse(pzd.CG_SRC)
    A = pzd.account_a()
    B = pzd.account_b()
    C = pzd.account_c(A, B)
    return {
        "out_keys": pzd.extract_out_keys(tree),
        "out_default": pzd.extract_out_default(tree),
        "guard": pzd.extract_out_guard(tree),
        "target": pzd.extract_out_target(tree),
        "detector_keys": pzd.detector_keys_from_source(),
        "glob_pattern": B["scan_contract"]["pattern"],
        "glob_teeth": B["scan_contract"]["glob_extractor_selfcheck"]["teeth"],
        "key_coverage": A["key_coverage"],
        "scan_coverage": B["coverage_scripts"],
        "w_hits": {k: v["b_hits"] for k, v in C["worlds"].items()},
        "discriminates": C["discriminates"],
        "shape_recognised": C["product_shape_recognised"],
        "ran_zero": sorted(C["ran_zero_worlds"]),
    }


def run_c() -> List[str]:
    """返回 F 组失败项名（空 = 全过）。"""
    global _ANCHOR
    fails: List[str] = []
    d = load()

    def ck(name: str, cond: bool) -> None:
        print(("  PASS " if cond else "  FAIL ") + name)
        if not cond:
            fails.append(name)

    # F1 caliber 16 项磁盘 hash 全对拍（189–192 存档零回写）
    ok = True
    for rel, want in d["caliber"].items():
        p = ROOT / rel
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != want:
            ok = False
    ck("F1 caliber 16 项磁盘 hash 全对拍（189–192 存档零回写）",
       ok and len(d["caliber"]) == 16)

    # F2 ⛔ 抽取器正例 / 反例 / glob 牙齿（零命中先证查询能命中）
    es = pzd.extractor_selfcheck()
    gs = pzd.glob_extractor_selfcheck()
    ck("F2 ⛔ 抽取器正例 4/4 + 反例 0 + 属性形式 / glob 抽取器有牙",
       es["pos_hit"] == 4 and es["neg_hit"] == 0
       and es["attr_form_teeth"] is True and gs["teeth"] is True)

    # F3 同源实算 A/B/C 账逐字段对拍（事故版补丁有打红点）
    live = live_recompute()
    A = d["accounts"]["A_product_shape"]
    B = d["accounts"]["B_scan_surface"]
    C = d["accounts"]["C_counterfactual"]
    ck("F3 同源实算 A/B/C 账逐字段对拍（事故版补丁有打红点）",
       live["out_keys"] == A["product_out_keys"]
       and live["out_default"] == A["out_default"]
       and live["guard"] is A["writes_only_if_out"]
       and live["target"] == A["out_target"]
       and live["detector_keys"] == A["detector_keys"]
       and live["glob_pattern"] == B["scan_contract"]["pattern"]
       and live["glob_teeth"] is True
       and live["key_coverage"] == A["key_coverage"]
       and live["scan_coverage"] == B["coverage_scripts"]
       and live["w_hits"] == {k: v["b_hits"] for k, v in C["worlds"].items()}
       and live["discriminates"] is C["discriminates"]
       and live["shape_recognised"] is C["product_shape_recognised"]
       and live["ran_zero"] == sorted(C["ran_zero_worlds"]))

    # F4 ⛔ 事故版恒 False 谓词 ⇒ W2 归零、牙齿翻转（反证有牙）
    A2 = pzd.account_a()
    B2 = pzd.account_b()
    orig = pzd.in_b192_scope
    try:
        pzd.in_b192_scope = lambda rel, scan: False
        broken = pzd.account_c(A2, B2)
    finally:
        pzd.in_b192_scope = orig
    ck("F4 ⛔ 事故版恒 False 谓词 ⇒ W2 归零、探测器牙齿翻转",
       broken["worlds"]["W2_ran_out_scripts"]["b_hits"] == 0
       and broken["detector_teeth_on_w2"] is False
       and broken["n_zero_worlds"] == 4)

    # F5 ⛔ 产品真实 5 键产物：形态能识别、路径不在 glob（0 不是恒空）
    import tempfile
    keys = A["product_out_keys"]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "main_out.json"
        p.write_text(json.dumps({k: None for k in keys}), encoding="utf-8")
        shape = PDE.archive_shape(p)
    scan = B["scan_contract"]
    ck("F5 ⛔ main() 真实产物形态能识别 / 路径不在 scripts/（0 不是恒空）",
       bool(shape.get("has_main_product_keys"))
       and pzd.in_b192_scope("tmp/main_out.json", scan) is False
       and pzd.in_b192_scope("scripts/main_out.json", scan) is True
       and bool(scan_text("/Users/" + "someone" + "/V6")))

    # F6 验锚 + 尺子同源重算 + closure + A1 四文件纳入
    if _ANCHOR is None:
        _ANCHOR = require_baseline()
    anchor_ok = (bool(_ANCHOR.get("ok"))
                 and len(_ANCHOR.get("repos") or {}) == 3
                 and not (_ANCHOR.get("diffs") or [])
                 and not (_ANCHOR.get("caliber_diffs") or []))
    cfiles = set(candidate_files() or [])
    pending = ["scripts/probe_zero_discrimination.py",
               "scripts/zero_discrimination_census.json",
               "tests/contract_zero_discrimination_test.py",
               "README.md"]
    a1_ok = all(f in cfiles for f in pending) and all(
        not scan_text(read_text(ROOT / f) or "") for f in pending)
    ck("F6 验锚 OK 3/3 + 尺子同源重算 closure 47/47 + A1 四文件纳入",
       anchor_ok and a1_ok
       and live["w_hits"] == WORLD_HITS
       and len(d["closure"]) == 47
       and all(v is True for v in d["closure"].values()))
    return fails


def main() -> int:
    d = load()
    V = conditions(d)
    print("=" * 74)
    print("r114 / README 193：192 B 账零命中的鉴别力（构造必然 ≠ 证据）")
    print("=" * 74)
    print("== A–E 组：产品形态 / 扫描面 / 反事实 / 边界 / 顶层 ==")
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
    print("192 B 账 0/28 是判据自锁下的构造必然；不推翻 192 结论方向、不报收益")
    return 0


if __name__ == "__main__":
    sys.exit(main())
