#!/usr/bin/env python3
"""r123 / README 202 契约：§199/§201「帧词表命中」用子串匹配，
命中 3 条里 hit 只是 hit_channels 子串；词边界严格匹配恒 2 条，差恒 1。

⛔ 本契约**不出现 `resolve_policy(...)` 的 AST 调用点**：192 尺子按全仓 AST
数该函数调用点，白名单只排除自身；本文件若新增调用点会令 192 契约分母漂移。

A–E 组是**纯函数**（只读存档，供反证逐条置 False / 篡改字段）；
F 组是本机实战（同源实算、逐字段、注入反例、验锚、A1 扫描面）。
合规形态与 r116–r122 同构：`run_c()` 内嵌 `def ck(name, cond, detail)`
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

import probe_frameword_caliber as pfc                     # noqa: E402

CENSUS = ROOT / "scripts" / "frameword_caliber_census.jsonl"
PENDING = ["README.md", "scripts/probe_frameword_caliber.py",
           "scripts/frameword_caliber_census.jsonl",
           "scripts/frameword_caliber_contract.py"]
CONDITIONS = 21  # A4 + B8 + C6 + D2 + SELF1


def load() -> Dict[str, Any]:
    return json.loads(CENSUS.read_text(encoding="utf-8"))


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    A = d["accounts"]["A_matching"]
    B = d["accounts"]["B_dual_caliber"]
    C = d["accounts"]["C_attribution"]
    V: Dict[str, bool] = {}
    # A 匹配口径层
    V["A1_ledger_substring_only"] = A["ledger_matching"]["all_substring_no_word_boundary"] is True
    V["A2_tail_substring_only"] = A["tail_matching"]["all_substring_no_word_boundary"] is True
    V["A3_both_substring_no_word_boundary"] = A["both_use_substring_only"] is True
    V["A4_frame_keys_same_source"] = A["n_frame_keys"] == len(pfc.FRAME_KEYS)
    # B 双口径对拍层
    V["B1_seven_products_available"] = B["n_available"] == 7
    V["B2_clean_runs_129"] = B["all_clean_runs_129"] is True
    V["B3_red_only_r118"] = B["red_run_exceptions"] == {"r118": 130}
    V["B4_tail_all_str"] = B["tail_all_str"] is True
    V["B5_clean_sub_stable_three"] = (
        B["clean_sub_signature_stable"] is True and B["clean_n_substring"] == 3)
    V["B6_clean_word_stable_two"] = (
        B["clean_word_signature_stable"] is True and B["clean_n_word"] == 2)
    V["B7_clean_delta_stable_one"] = B["clean_delta_stable_one"] is True
    V["B8_all_delta_one"] = B["all_delta_one"] is True
    # C 归因层
    V["C1_substring_only_confirmed"] = C["substring_only_confirmed"] is True
    V["C2_host_word_in_source_and_tail"] = (
        C["host_in_contract_source"] is True and C["host_in_product_tail"] is True)
    V["C3_word_hits_are_two"] = C["word_hits_are_two"] is True
    V["C4_hit_not_runtime_evidence"] = C["hit_is_not_runtime_evidence"] is True
    V["C5_two_calibers_not_merged"] = (
        C["two_calibers_not_merged"] is True and C["merged_into_bool"] is False)
    V["C6_not_overturn_201"] = C["not_overturn_201"] is True
    # D 边界层
    D = d["accounts"]["D_boundary"]
    V["D1_section183_zero"] = (
        D["section_183_touched"] is False and D["real_expr_run"] == 0
        and D["real_commit_run"] == 0 and D["main_repo_run"] == 0
        and D["gain_rows_reported"] == 0 and D["product_files_modified"] == 0)
    V["D2_direction_unchanged"] = (
        D["scope_narrowing_only"] is True and D["direction_199_201_unchanged"] is True)
    V["SELF_closure_all_true"] = (
        sum(d["closure"].values()) == len(d["closure"])
        and len(d["closure"]) == d["derived"]["n_closure"])
    assert len(V) == CONDITIONS, (len(V), CONDITIONS)
    return V


def verdict_from(V: Dict[str, bool]) -> List[str]:
    withdraw = os.environ.get("R123_WITHDRAW", "")
    return [k for k, ok in V.items()
            if not ok or (withdraw and k.startswith(withdraw))]


def run_c() -> List[str]:
    """F 组：同源实算 / 逐字段 / 注入反例 / 静态边界。"""
    d = load()
    A = d["accounts"]["A_matching"]
    B = d["accounts"]["B_dual_caliber"]
    C = d["accounts"]["C_attribution"]
    D = d["accounts"]["D_boundary"]
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        withdraw = os.environ.get("R123_WITHDRAW", "")
        if withdraw and name.startswith(withdraw):
            cond = False
        if not cond:
            fails.append(name)

    # F1 匹配口径 AST live 重算，不读存档结论字段
    led = pfc._matching_in_source(pfc.LEDGER_SRC, ("_frame_keys", "tail_frame_word_zero", "account_a"))
    tal = pfc._matching_in_source(pfc.TAIL_SRC, ("_frame_hits",))
    ck("F1 匹配口径 AST live 重算==存档",
       led == A["ledger_matching"] and tal == A["tail_matching"])
    ck("F1 同源词表 live 重算", pfc.FRAME_KEYS == pfrl_keys() and len(pfc.FRAME_KEYS) == 10)

    # F2 逐字段：双口径 live 重扫七份产物，核对两套 signature（不读结论字段）
    for tag, path in pfc.FULL_RUNS:
        saved = B["per_run"].get(tag, {})
        if not saved.get("available"):
            ck("F2 %s 产物可用" % tag, False)
            continue
        dd = pfc._read_json_abs(path)
        runs = dd.get("runs") or []
        sub = sorted((r.get("script", ""), ",".join(pfc._substring_hits(r.get("tail", ""))))
                     for r in runs if pfc._substring_hits(r.get("tail", "")))
        word = sorted((r.get("script", ""), ",".join(pfc._word_hits(r.get("tail", ""))))
                      for r in runs if pfc._word_hits(r.get("tail", "")))
        ck("F2 %s 双口径 live 重扫==存档" % tag,
           sub == [tuple(x) for x in saved["substring_signature"]]
           and word == [tuple(x) for x in saved["word_signature"]]
           and len(runs) == saved["n_runs"]
           and saved["delta"] == len(sub) - len(word))

    # F3 注入反例：词边界严格匹配能剔除 hit；depth/resolve 保留（同源 re.search）
    sample = "PASS ✓ G3 `hit_channels == {}`"
    ck("F3 注入反例 hit_channels 子串命中但词边界不命中",
       pfc.SUBSTRING_ONLY_KEY in pfc._substring_hits(sample)
       and pfc.SUBSTRING_ONLY_KEY not in pfc._word_hits(sample))
    sample2 = "PASS S6.3 降级到 depth=1；先 resolve 再判"
    ck("F3 注入正例 depth/resolve 两口径都命中",
       "depth" in pfc._word_hits(sample2) and "resolve" in pfc._word_hits(sample2))
    ck("F3 命中不是运行证据 + §201 未翻案",
       C["hit_is_not_runtime_evidence"] is True and C["not_overturn_201"] is True)

    # F4 caliber 逐键磁盘 hash + 存档形态 + A1 扫描面
    ck("F4 caliber 逐键磁盘 hash 对齐",
       len(d["caliber"]) == len(pfc.CALIBER_FILES)
       and all(d["caliber"][k] == pfc.sha256_file(k) for k in pfc.CALIBER_FILES))
    ck("F4 存档后缀非 .json", CENSUS.suffix == ".jsonl")
    from contract_portability_test import candidate_files, scan_text, read_text
    files = set(candidate_files() or [])
    in_surface = [p for p in PENDING if p in files]
    hits_scan = sum(len(scan_text(read_text(ROOT / p) or "")) for p in PENDING)
    teeth = bool(scan_text("/Users/" + "someone" + "/V6"))
    ck("F4 A1 四文件在候选面 + 零命中 + 正例有牙",
       len(in_surface) == 4 and hits_scan == 0 and teeth)

    # F5 验锚 / closure / 192 分母 / 193 未漂移 / §183 零
    a = pfc.anchor_report()
    sc = D["selfcheck"]
    ck("F5 验锚 OK 3/3 + closure 全绿 + §183 零",
       a["ok"] is True and a["repos"] == 3
       and not a["diffs"] and not a["caliber_diffs"]
       and D["section_183_touched"] is False
       and D["real_expr_run"] == 0 and D["real_commit_run"] == 0
       and D["gain_rows_reported"] == 0 and D["product_files_modified"] == 0)
    ck("F5 三分母双源 135/119/94",
       D["denominator_dual_source"]["main"] == 135
       and D["denominator_dual_source"]["form"] == 119
       and D["denominator_dual_source"]["no_result"] == 94)
    ck("F5 superseded 全匹配", D["superseded"]["all_match"] is True)
    ck("F5 新文件零 resolve_policy 调用点 + 存档非 json + 契约在 scripts/",
       sc["new_files_resolve_policy_calls"] == 0
       and sc["self_archive_not_json"] is True
       and sc["new_contract_in_scripts_not_tests"] is True)
    return fails


def pfrl_keys() -> tuple:
    import probe_full_run_ledger as pfrl
    return pfrl.FRAME_KEYS


def main() -> int:
    d = load()
    V = conditions(d)
    bad = verdict_from(V)
    print("=" * 74)
    print("r123 / 202  帧词命中子串口径：干净轮 sub=3 / word=2 / delta=1")
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
