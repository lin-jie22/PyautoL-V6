#!/usr/bin/env python3
"""r119 / README 198 产物闸（⛔ 有意放 scripts/，不进 discover 面见 §197 第六段）。

钉死 r119 四本账：第五类证据「真跑产品模块」的标签实为量具自证。
同构 r116/r117/r118 的 `ck` 合规形态（`contract_assert_wiring_test.py`）：
`run_c()` 内嵌 `def ck(name, cond, detail)` + `fails.append` + `return fails`，
`main()` 用 `allbad = bad + fbad`。⛔ 条件按名撤回，保留 body 与缩进。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import probe_default_effect as pde                         # noqa: E402
import probe_product_label as ppl                          # noqa: E402

CENSUS = ROOT / "scripts" / "product_label_census.jsonl"
PENDING = ["README.md", "scripts/probe_product_label.py",
           "scripts/product_label_census.jsonl",
           "scripts/product_label_contract.py"]
CONDITIONS = 24   # A 7 + B 10 + C 6 + self-consistency 1


def load() -> Dict[str, Any]:
    return json.loads(CENSUS.read_text(encoding="utf-8"))


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    A = d["accounts"]["A_definition_layer"]
    B = d["accounts"]["B_frame_attribution"]
    C = d["accounts"]["C_discrimination"]
    V: Dict[str, bool] = {}
    V["A1_definition_unique"] = A["n_definition_sites"] == 1
    V["A2_definition_in_gauge"] = (
        len(A["definition_in_gauge"]) == 1
        and A["definition_in_gauge"][0]["file"] == ppl.CG_SRC)
    V["A3_no_definition_in_aether"] = A["n_definition_in_aether"] == 0
    V["A4_no_call_in_aether"] = A["n_call_files_in_aether"] == 0
    V["A5_denominator_stable_5"] = (
        A["n_call_files_live"] == A["n_call_files_saved"] == 5
        and A["dual_source_match"] is True)
    V["A6_product_sha_unchanged"] = A["product_sha_matches_189"] is True
    V["A7_product_anchor_present"] = A["product_unchanged_anchor_present"] is True
    V["B1_total_8"] = B["n_total_runtime"] == 8
    V["B2_saved_total_matches"] = B["saved_total_8"] is True
    V["B3_gauge_frames_4"] = B["n_gauge_frames"] == 4
    V["B4_product_frames_0"] = B["n_product_aether_frames"] == 0
    V["B5_contract_frames_4"] = B["n_contract_frames"] == 4
    V["B6_old_label_4"] = B["n_live_old_label_true"] == 4
    V["B7_old_label_matches_saved"] = B["label_matches_saved"] is True
    V["B8_old_true_are_gauge"] = B["old_true_are_all_gauge"] is True
    V["B9_product_never_labeled"] = B["product_frame_never_labeled"] is True
    V["B10_runner_identical"] = B["all_rc_stdout_identical"] is True
    V["C1_gauge_positive"] = C["gauge_positive_has_teeth"] is True
    V["C2_label_is_gauge"] = C["saved_label_is_gauge_not_product"] is True
    V["C3_label_ignores_domain"] = C["old_label_ignores_definition_domain"] is True
    V["C4_two_ledgers"] = C["two_ledgers_separate"] is True
    V["C5_zero_worlds_not_vacuous"] = C["product_world_is_positive_not_vacuous"] is True
    V["C6_discriminates"] = C["discriminates"] is True
    V["SELF_closure_all_true"] = (
        sum(d["closure"].values()) == len(d["closure"])
        and len(d["closure"]) == d["derived"]["n_closure"])
    assert len(V) == CONDITIONS, (len(V), CONDITIONS)
    return V


def verdict_from(V: Dict[str, bool]) -> List[str]:
    withdraw = os.environ.get("R119_WITHDRAW", "")
    return [k for k, ok in V.items()
            if not ok or (withdraw and k.startswith(withdraw))]


def run_c() -> List[str]:
    """F 组：同源实算 / 逐键字段 / 注入反例 / 静态边界。"""
    d = load()
    A = d["accounts"]["A_definition_layer"]
    B = d["accounts"]["B_frame_attribution"]
    C = d["accounts"]["C_discrimination"]
    D = d["accounts"]["D_boundary"]
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        withdraw = os.environ.get("R119_WITHDRAW", "")
        if withdraw and name.startswith(withdraw):
            cond = False
        if not cond:
            fails.append(name)

    # F1 同源实算：判据 / 定义点 / 帧归因都从live重算，不读存档结论字段
    live_defs = ppl.find_definitions("resolve_policy")
    live_calls = pde.resolve_policy_calls_all()
    ck("F1 定义点 live==存档", len(live_defs) == A["n_definition_sites"]
       and [x["file"] for x in live_defs] == [x["file"] for x in A["definition_sites"]])
    ck("F1 调用点 live==存档 且 aether 零命中",
       sorted(live_calls) == A["call_files_live"]
       and not [k for k in live_calls if k.startswith("aether/")])
    ck("F1 in_product 判据 live 复现：量具 true / 契约 false / 产品 false",
       ppl.old_label(ppl.CG_SRC) is True
       and ppl.old_label(ppl.C1) is False
       and ppl.old_label(ppl.EXTRACT_SRC) is False)
    ck("F1 classify live 三层归位",
       ppl.classify_caller("aether/x.py") == "product_aether"
       and ppl.classify_caller(ppl.CG_SRC) == "gauge_scripts"
       and ppl.classify_caller(ppl.C1) == "contract_tests")
    ck("F1 产品 sha live 对拍 §189 前缀",
       ppl.sha256_file(ppl.EXTRACT_SRC).startswith(ppl.EXTRACT_SHA189_PREFIX)
       == A["product_sha_matches_189"])

    # F2 逐帧字段：存档每一帧的 rel/layer/old_in_product 自洽
    frames = [f for v in B["per_contract"].values() for f in v["frames"]]
    ck("F2 每帧三层字段自洽", all(
        f["layer"] == ppl.classify_caller(f["rel"])
        and f["old_in_product"] == ppl.old_label(f["rel"])
        and f["rel"] for f in frames) and len(frames) == 8)
    ck("F2 每帧非未分类", all(f["layer"] != "unclassified" for f in frames))
    ck("F2 量具帧 caller 是 selftest 且行号 311/312",
       sorted((f["caller"], f["line"]) for f in frames
              if f["layer"] == "gauge_scripts")
       == [("selftest", 311), ("selftest", 311),
           ("selftest", 312), ("selftest", 312)])
    ck("F2 产品帧零（按真实路径）",
       [f for f in frames if f["layer"] == "product_aether"] == [])
    ck("F2 存档 4 个 true 逐帧=量具帧", sum(
        1 for f in frames if f["old_in_product"]) == 4
       and all(f["layer"] == "gauge_scripts"
               for f in frames if f["old_in_product"]))

    # F3 注入反例：把路径换掉，判据翻转 / 定义域判据不动
    w = C["counterfactual"]["worlds"]
    ck("F3 改后缀即翻转（只认文件名）",
       w["W0_gauge"]["old_in_product"] is True
       and w["W3_gauge_suffix"]["old_in_product"] is True
       and w["W3_gauge_suffix"]["rel"] != w["W0_gauge"]["rel"])
    ck("F3 换产品路径仍 false（零识别力）",
       w["W2_product"]["layer"] == "product_aether"
       and w["W2_product"]["old_in_product"] is False)
    ck("F3 契约路径 false", w["W1_contract"]["old_in_product"] is False)
    ck("F3 四世界三层都有", C["counterfactual"]["distinct_layers"] == 3)
    ck("F3 产品世界零识别是有牙正例非空集",
       C["n_product_worlds"] == 1 and C["n_product_unrecognized"] == 1
       and C["product_unrecognized_has_teeth"] is True)

    # F4 caliber 逐键磁盘 hash + 存档 sha 幂等形态
    ck("F4 caliber 逐键磁盘 hash 对齐",
       len(d["caliber"]) == len(ppl.CALIBER_FILES)
       and all(d["caliber"][k] == ppl.sha256_file(k)
               for k in ppl.CALIBER_FILES))
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
    a = ppl.anchor_report()
    ck("F6 验锚 OK 3/3 + closure 全绿 + §183 零",
       a["ok"] is True and a["repos"] == 3
       and not a["diffs"] and not a["caliber_diffs"]
       and D["section_183_touched"] is False
       and D["real_expr_run"] == 0 and D["real_commit_run"] == 0
       and D["gain_rows_reported"] == 0
       and D["product_files_modified"] == 0)
    ck("F6 三分母双源 135/119/94",
       D["denominator_dual_source"]["main"] == 135
       and D["denominator_dual_source"]["form"] == 119
       and D["denominator_dual_source"]["no_result"] == 94
       and d["closure"]["d1b_denominator_dual_source"] is True)
    ck("F6 superseded 全匹配", D["superseded"]["all_match"] is True)
    return fails


ARCH_118 = ROOT / "scripts" / "truerun_count_census.jsonl"


def main() -> int:
    d = load()
    V = conditions(d)
    bad = verdict_from(V)
    print("=" * 74)
    print("r119 / 198  第五类证据『产品模块』= 量具自证，产品包帧 0")
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
    else:
        print("✅ 定义层 / 帧归因 / 鉴别力 / 边界四本账全绿")
    return 1 if allbad else 0


if __name__ == "__main__":
    sys.exit(main())
