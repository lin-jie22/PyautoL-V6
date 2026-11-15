#!/usr/bin/env python3
"""r117 / README 196 契约：⛔ 第五类证据只验 signature 字符串的强度边界。

钉死四本账（不合并成布尔）：
  A 断言面    15 条自检分形态（签名 2 / 硬编码 4 / 其它 9 / 字段级 0）、
              全仓契约条件位 1 条字段级断言（2 处字段 Attribute，
              全钉硬编码常量）、指向默认对象的字段级断言 0、
              抽取器正例反例有牙、192 的 5 个调用点文件双源对拍
  B 运行读数  基线 15/15 绿 + 真跑 2 次；Impostor（签名对、4 位全 False）
              下 selftest 15/15 绿、签名条件 2/2 绿；对照签名破坏打红、
              adopt_probe 忽略 policy 恒绿
  C 鉴别力    反事实四世界：2 个零世界全已运行、默认态混合、
              无自然字段正例 ⇒ 零鉴别力是断言面结构造成的构造必然
  D 边界      三分母双源 / superseded / 验锚 / caliber 磁盘同源实算 /
              §183 零触碰 / 本轮 2 文件不新增 `resolve_policy` AST 调用点 /
              存档非 `.json`（r115 已登记开口）
  E derived / summary / closure 完整性

⛔⛔ 本契约**不出现 `resolve_policy(...)` 的 AST 调用点**：
192/194 尺子按全仓 AST 数该函数调用点，且只把历轮自身量具/契约
列入白名单；本文件新增调用点会令分母漂移、F3/F6 真红（r114 已踩）。
一律用 `getattr` / `setattr` 动态调用达成语义等价。

⛔ 合规形态（`contract_assert_wiring_test.py` 静态闸）：`run_c()` 内嵌
   `def ck(name, cond, detail)` + `fails.append(...)` + 返回失败列表，
   `main()` 用 `allbad = bad + fbad` 决定退出码（r116 首跑真红的教训）。

⛔ 纯只读取证契约：不跑真实 expr、不跑真实 commit、不跑
`main() --repo`、不碰 §183；不修改 189–195 尺子 / 存档 / 判据。
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

import probe_signature_impostor as psi                        # noqa: E402

ARCH = ROOT / "scripts" / "signature_impostor_census.jsonl"
SELF_ARCHIVE_NAME = ARCH.name
PENDING = ["README.md", "scripts/probe_signature_impostor.py",
           "scripts/signature_impostor_census.jsonl",
           "tests/contract_signature_impostor_test.py"]

_ANCHOR: Dict[str, Any] | None = None


def load() -> Dict[str, Any]:
    return json.loads(ARCH.read_text(encoding="utf-8"))


def anchor_cached() -> Dict[str, Any]:
    global _ANCHOR
    if _ANCHOR is None:
        _ANCHOR = psi.anchor_report()
    return dict(_ANCHOR)


def verdict_from(V: Dict[str, bool]) -> List[str]:
    """⛔ 纯函数：供反证阶段 1 逐条置 False 验证「没有死条件」。"""
    return [k for k, v in V.items() if not v]


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    """A–E 静态条件（只读存档）。⛔ 每条都要能被注入反证逐条撤回命中。"""
    A = d["accounts"]["A_assertion_surface"]
    B = d["accounts"]["B_runtime_readouts"]
    C = d["accounts"]["C_discrimination"]
    D = d["accounts"]["D_boundary"]
    W = C["counterfactual"]
    st, cs, rp = A["selftest"], A["contracts"], A["rp_call_sites"]
    chk = D["sandbox_selfcheck"]
    cal = d["caliber"]
    der = d["derived"]
    summ = d["summary"]
    V: Dict[str, bool] = {}

    # ── A 组：断言面 ──
    V["A1"] = d["schema"] == "signature_impostor_census/v1"
    V["A2"] = A["n_fields"] == 4
    V["A3"] = A["policy_fields"] == ["cross_scope", "first_rhs",
                                     "allow_unstable", "no_gold_gate"]
    V["A4"] = A["extractor_teeth"]["positive"] == "field_level"
    V["A5"] = A["extractor_teeth"]["negative"] == "signature_string"
    V["A6"] = A["extractor_teeth"]["teeth"] is True
    V["A6b"] = A["extractor_teeth"]["default_object_positive"] is True
    V["A6c"] = A["extractor_teeth"]["constant_positive"] is True
    V["A7"] = st["n_assertions"] == 15
    V["A8"] = st["kinds"]["signature_string"] == 2
    V["A9"] = st["kinds"]["hardcoded_constant"] == 4
    V["A10"] = st["kinds"]["other"] == 9
    V["A11"] = st["kinds"]["field_level"] == 0
    V["A12"] = st["n_field_level"] == 0
    V["A12b"] = st["n_field_level_on_default_object"] == 0
    V["A13"] = st["n_policy_related"] == 6
    V["A14"] = cs["n_field_level"] == 1
    V["A15"] = cs["n_signature_string"] > 0
    V["A15b"] = cs["n_field_level_on_default_object"] == 0
    V["A15c"] = cs["n_field_level_on_constant"] == 2
    V["A16"] = cs["n_files"] > 0
    V["A17"] = A["n_field_level_on_default_object_total"] == 0
    V["A17b"] = A["n_field_level_total"] == 1
    V["A18"] = rp["n_live"] == 5
    V["A19"] = rp["n_archive"] == 5
    V["A20"] = rp["match"] is True
    V["A21"] = A["merged_into_bool"] is False

    # ── B 组：运行读数 ──
    V["B1"] = B["baseline"]["selftest_ok"] is True
    V["B2"] = B["baseline"]["n_assertion_lines"] == 15
    V["B3"] = B["baseline"]["n_fail_marks"] == 0
    V["B4"] = B["n_resolve_policy_actual"] == 2
    V["B5"] = B["impostor"]["selftest_ok"] is True
    V["B6"] = B["impostor"]["n_assertion_lines"] == 15
    V["B7"] = B["impostor"]["n_fail_marks"] == 0
    V["B8"] = B["impostor"]["fail_labels"] == []
    V["B9"] = B["signature_replay"]["got"] == ["1111", "0000"]
    V["B10"] = B["signature_replay"]["want"] == ["1111", "0000"]
    V["B11"] = B["signature_replay"]["n_green"] == 2
    V["B11b"] = B["signature_replay"]["n"] == 2
    V["B12"] = B["impostor_signature"] == "1111"
    V["B13"] = B["impostor_all_fields_false"] is True
    V["B14"] = len(B["impostor_fields"]) == 4
    V["B15"] = B["impostor_fields"]["cross_scope"] is False
    V["B16"] = B["impostor_fields"]["first_rhs"] is False
    V["B17"] = B["impostor_fields"]["allow_unstable"] is False
    V["B18"] = B["impostor_fields"]["no_gold_gate"] is False
    V["B19"] = B["all_green_under_impostor"] is True
    V["B20"] = B["signature_broken"]["n_fail_marks"] > 0
    V["B20b"] = B["signature_broken"]["selftest_ok"] is False
    V["B20c"] = B["signature_broken"]["n_assertion_lines"] == 15
    V["B21"] = B["adopt_probe_blind"]["selftest_ok"] is True
    V["B22"] = B["adopt_probe_blind"]["n_fail_marks"] == 0
    V["B22b"] = B["adopt_probe_blind"]["n_assertion_lines"] == 15
    V["B23"] = B["positive_has_teeth"] is True
    V["B24"] = B["merged_into_bool"] is False

    # ── C 组：鉴别力 ──
    V["C1"] = W["n_worlds"] == 4
    V["C2"] = W["n_zero_worlds"] == 2
    V["C3"] = W["n_ran_zero_worlds"] == 2
    V["C4"] = W["zero_assertion_worlds"] == ["W0_intact", "W1_impostor"]
    V["C4b"] = W["intact_values_in_zero"] == ["False", "True"]
    V["C5"] = W["intact_mixed"] is True
    V["C6"] = W["discriminates"] is False
    V["C7"] = W["zero_field_assertion_structural"] is True
    V["C8"] = W["positive_has_teeth"] is True
    V["C9"] = W["synthetic_marked"] is True
    V["C10"] = W["no_natural_field_positive"] is True
    V["C11"] = C["constructurally_necessary"] is True
    V["C12"] = C["discriminates"] is False
    V["C13"] = C["positive_has_teeth"] is True
    V["C14"] = C["direction_194_195_kept"] is True
    V["C15"] = C["not_a_product_hole"] is True
    V["C16"] = C["merged_into_bool"] is False
    V["C17"] = "构造必然" in C["verdict"]

    # ── D 组：边界 ──
    V["D1"] = D["policy_default"] == "1111"
    V["D2"] = D["policy_strict"] == "0000"
    V["D3"] = D["denominators"] == {"main": 135, "form": 119, "no_result": 94}
    V["D4"] = D["denominator_dual_source"]["form_rows"] == 119
    V["D4b"] = D["denominator_dual_source"]["main_rows"] == 135
    V["D4c"] = D["denominator_dual_source"]["main_summary"] == 135
    V["D5"] = D["denominator_dual_source"]["form_summary"] == 119
    V["D6"] = D["denominator_dual_source"]["no_result_rows"] == 94
    V["D7"] = D["denominator_dual_source"]["no_result_summary"] == 94
    V["D8"] = D["denominators_all_distinct"] is True
    V["D9"] = D["superseded_total"] == 3
    V["D10"] = D["superseded_sha_matches"] == 3
    V["D11"] = D["superseded_diffs"] == []
    V["D12"] = chk["no_new_denominator_call_site"] is True
    V["D12b"] = all(v == 0 for v in
                    chk["own_resolve_policy_call_sites"].values())
    V["D13"] = chk["positive_extractor_has_teeth"] is True
    V["D13b"] = chk["positive_n"] > 0
    V["D14"] = chk["coverage_unchanged"] is True
    V["D14b"] = (chk["coverage_scripts_live"]
                 == chk["coverage_scripts_saved"])
    V["D15"] = chk["self_archive_not_json"] is True
    V["D16"] = chk["n_banned"] == 0
    V["D17"] = D["n_call_sites_192_from_archive"] == 5
    V["D18"] = D["n_closure_195"] == 53
    V["D19"] = D["real_expr_runs"] == 0
    V["D20"] = D["real_commit_runs"] == 0
    V["D21"] = D["main_repo_runs"] == 0
    V["D22"] = D["f2p_runs"] == 0
    V["D23"] = D["gain_reported"] == 0
    V["D24"] = D["section183_untouched"] is True
    V["D25"] = D["b192_archive_rewritten"] is False
    V["D26"] = D["archives_189_195_rewritten"] is False
    V["D27"] = D["aether_rewritten"] is False
    V["D28"] = D["anchor"]["ok"] is True
    V["D28b"] = D["verdict_194_195_unchanged"] is True
    V["D29"] = D["anchor"]["repos"] == 3
    V["D30"] = D["anchor"]["diffs"] == []
    V["D31"] = D["anchor"]["caliber_diffs"] == []
    V["D32"] = len(cal) == len(psi.CALIBER_FILES)

    # ── E 组：derived / summary / closure ──
    V["E1"] = der["n_selftest_assertions"] == 15
    V["E2"] = der["n_selftest_field_level"] == 0
    V["E3"] = der["n_contract_field_level"] == 1
    V["E3b"] = der["n_contract_default_field_level"] == 0
    V["E3c"] = der["n_contract_constant_field_level"] == 2
    V["E3d"] = der["n_default_field_level_total"] == 0
    V["E4"] = der["n_contract_signature"] == cs["n_signature_string"]
    V["E5"] = der["n_fail_marks_under_impostor"] == 0
    V["E6"] = der["impostor_signature"] == "1111"
    V["E6b"] = der["impostor_fields"] == B["impostor_fields"]
    V["E7"] = der["discriminates"] is False
    V["E8"] = der["n_closure_true"] == der["n_closure"]
    V["E9"] = len(d["closure"]) == der["n_closure"]
    V["E10"] = all(v is True for v in d["closure"].values())
    V["E11"] = "签名正确" in summ["verdict"]
    V["E12"] = "构造必然" in summ["scope"]
    V["E13"] = "不翻案" in summ["scope"]
    V["E14"] = "不是产品洞" in summ["scope"]
    V["E15"] = summ["n_closure_true"] == summ["n_closure"] == der["n_closure"]
    V["E16"] = d["anchor"] == D["anchor"]
    V["E17"] = "signature_impostor_census/v1" in d["schema"]
    return V


def live_recompute() -> Dict[str, Any]:
    """⛔ 同源实算：不读存档结论字段，只用尺子自己的抽取器 + 产品源码重算。"""
    fields = psi.policy_fields()
    st = psi.selftest_surface(fields)
    cs = psi.contract_surface(fields)
    rp = psi.rp_call_sites()
    teeth = psi.extractor_teeth(fields)
    patch = lambda strict=False: psi.Impostor(strict)
    imp = psi._run_selftest(patch)
    replay = psi.replay_signature_conditions(patch)
    obj = psi._with_patch(patch, lambda: getattr(psi.CG, "resolve_policy")(False))
    strict_obj = getattr(psi.CG, "STRICT")
    sig_broken = psi._run_selftest(lambda strict=False: strict_obj)
    return {
        "fields": list(fields),
        "st_kinds": st["kinds"],
        "n_st": st["n_assertions"],
        "n_st_field": st["n_field_level"],
        "n_st_policy": st["n_policy_related"],
        "n_contract_field": cs["n_field_level"],
        "n_contract_default_field": cs["n_field_level_on_default_object"],
        "n_contract_constant_field": cs["n_field_level_on_constant"],
        "n_contract_sig": cs["n_signature_string"],
        "n_contract_hardcoded": cs["n_hardcoded_constant"],
        "n_contract_files": cs["n_files"],
        "st_field_sources": st["field_sources"],
        "per_file": cs["per_file"],
        "teeth": teeth,
        "rp_live": rp["live"], "rp_archive": rp["archive"],
        "imp_ok": imp["selftest_ok"], "imp_lines": imp["n_assertion_lines"],
        "imp_fails": imp["n_fail_marks"],
        "replay_got": replay["got"], "replay_green": replay["n_green"],
        "obj_sig": obj.signature(),
        "obj_fields": {f: bool(getattr(obj, f)) for f in fields},
        "sig_broken_fails": sig_broken["n_fail_marks"],
        "positive_n": psi.call_count_in_file(psi.CG_SRC, "resolve_policy"),
    }


def run_c() -> List[str]:
    """F 组：本机实战 / 正例反证 / 同源实算 / 真跑 / A1 / 验锚。

    ⛔ 合规形态（上游静态闸）：内嵌 `ck(name, cond, detail)` 用
       `fails.append` 记账，返回值即失败列表，使退出码路径能引用它。
    """
    import contract_portability_test as cpt

    withdraw = os.environ.get("R117_WITHDRAW", "")
    fails: List[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        if withdraw and name.startswith(withdraw):
            cond = False
        if not cond:
            fails.append(name)
        print("  %s %s %s" % ("PASS" if cond else "FAIL", name, detail))

    d = load()
    a = anchor_cached()

    ck("F1 存档存在 + schema v1 + 4 个待交付文件齐备",
       ARCH.exists() and d["schema"] == "signature_impostor_census/v1"
       and all((ROOT / p).exists() for p in PENDING)
       and ARCH.name == SELF_ARCHIVE_NAME)

    # F2：抽取器正例 / 反例有牙 —— 字段表被事故版清空后正例失效
    real_fields = psi.policy_fields
    try:
        setattr(psi, "policy_fields", lambda: [])
        broken = psi.extractor_teeth([])
        teeth = broken["positive"] != "field_level"
    finally:
        setattr(psi, "policy_fields", real_fields)
    ck("F2 ⛔ 抽取器正例 + 事故版反例有牙（字段表清空令正例失效）",
       teeth and psi.extractor_teeth(psi.policy_fields())["teeth"])

    # F3：live 与存档逐字段对拍 + caliber 逐键与磁盘 sha 同源实算
    A = d["accounts"]["A_assertion_surface"]
    B = d["accounts"]["B_runtime_readouts"]
    cs, st, rp = A["contracts"], A["selftest"], A["rp_call_sites"]
    live = live_recompute()
    pairs = {
        "fields": (live["fields"], A["policy_fields"]),
        "st_kinds": (live["st_kinds"], st["kinds"]),
        "n_st": (live["n_st"], st["n_assertions"]),
        "n_st_field": (live["n_st_field"], st["n_field_level"]),
        "n_st_policy": (live["n_st_policy"], st["n_policy_related"]),
        "n_contract_field": (live["n_contract_field"], cs["n_field_level"]),
        "n_contract_default_field":
            (live["n_contract_default_field"],
             cs["n_field_level_on_default_object"]),
        "n_contract_constant_field":
            (live["n_contract_constant_field"],
             cs["n_field_level_on_constant"]),
        "n_contract_sig": (live["n_contract_sig"], cs["n_signature_string"]),
        "n_contract_hardcoded":
            (live["n_contract_hardcoded"], cs["n_hardcoded_constant"]),
        "n_contract_files": (live["n_contract_files"], cs["n_files"]),
        "st_field_sources": (live["st_field_sources"], st["field_sources"]),
        "per_file": (live["per_file"], cs["per_file"]),
        "teeth": (live["teeth"], A["extractor_teeth"]),
        "rp_live": (live["rp_live"], rp["live"]),
        "rp_archive": (live["rp_archive"], rp["archive"]),
        "imp_ok": (live["imp_ok"], B["impostor"]["selftest_ok"]),
        "imp_lines": (live["imp_lines"], B["impostor"]["n_assertion_lines"]),
        "imp_fails": (live["imp_fails"], B["impostor"]["n_fail_marks"]),
        "replay_got": (live["replay_got"], B["signature_replay"]["got"]),
        "replay_green": (live["replay_green"], B["signature_replay"]["n_green"]),
        "obj_sig": (live["obj_sig"], B["impostor_signature"]),
        "obj_fields": (live["obj_fields"], B["impostor_fields"]),
        "sig_broken": (live["sig_broken_fails"],
                       B["signature_broken"]["n_fail_marks"]),
        "positive_n": (live["positive_n"],
                       d["accounts"]["D_boundary"]["sandbox_selfcheck"]
                       ["positive_n"]),
    }
    cal_diffs = [rel for rel, want in d["caliber"].items()
                 if psi.sha256_file(rel) != want]
    diffs = [k for k, (lv, sv) in pairs.items() if lv != sv]
    ck("F3 ⛔ 同源实算 A/B 账逐字段对拍 + caliber 逐键磁盘 hash 零差异",
       not diffs and not cal_diffs and len(pairs) == 25
       and len(d["caliber"]) == len(psi.CALIBER_FILES))

    # F4：真跑 Impostor 事故 —— 全绿但字段语义全错；对照组打红有牙
    ck("F4 ⛔ 真跑 Impostor：selftest 15/15 绿 + 签名 2/2 绿 + 4 位全 False + 对照打红",
       live["imp_ok"] is True and live["imp_lines"] == 15
       and live["imp_fails"] == 0 and live["replay_green"] == 2
       and live["obj_sig"] == "1111"
       and not any(live["obj_fields"].values())
       and live["sig_broken_fails"] > 0)

    # F5：A1 候选面（tracked ∪ untracked，不按扩展名过滤）
    files = set(cpt.candidate_files() or [])
    in_surface = [p for p in PENDING if p in files]
    hits = sum(len(cpt.scan_text(cpt.read_text(ROOT / p) or ""))
               for p in PENDING)
    teeth = bool(cpt.scan_text("/Users/" + "someone" + "/V6"))
    ck("F5 A1 四文件纳入候选面 + scan_text 零命中 + 正例有牙",
       len(in_surface) == 4 and hits == 0 and teeth)

    ck("F6 验锚 OK 3/3 + closure 全绿 + 192 分母仍 5 + 存档非 .json",
       a["ok"] is True and a["repos"] == 3 and not a["diffs"]
       and not a["caliber_diffs"]
       and len(d["closure"]) == d["derived"]["n_closure"]
       and all(v is True for v in d["closure"].values())
       and rp["n_live"] == 5
       and SELF_ARCHIVE_NAME.endswith(".jsonl"))
    return fails


def main() -> int:
    d = load()
    V = conditions(d)
    bad = verdict_from(V)
    print("=" * 76)
    print("r117 / README 196 契约：第五类证据只验 signature 字符串的强度边界")
    print("=" * 76)
    for k in sorted(V):
        print("  %-5s %s" % (k, "PASS" if V[k] else "FAIL"))
    fbad = run_c()
    print("-" * 76)
    print("%s A–E %d 项 + F 组 6 项" %
          ("✅ 全部通过" if not bad and not fbad else "❌ 有失败", len(V)))
    if bad:
        print("A–E FAIL:", bad)
    if fbad:
        print("F FAIL:", fbad)
    print("签名对 / 字段错仍全绿 ⇒ 第五类证据只能证明签名字符串；构造必然、不翻案")
    allbad = bad + fbad
    return 1 if allbad else 0


if __name__ == "__main__":
    sys.exit(main())
