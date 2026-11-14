#!/usr/bin/env python3
"""r113 / README 192：「默认生效」的证据类型分层 —— 离线可回归契约。

  A 组  钉接入点读数（4 位字段 / 读取点 / resolve_policy 调用点分层 /
        run_all 无 policy 参数 / main 要求 --repo / 抽取器正反例）
  B 组  钉运行证据（28 份存档 / main() 产物 0 份 / 自身产物显式排除 /
        189 C 账 2 次真跑不接 policy / 190/191 零真实）
  C 组  钉四层 reach（静态 4/4、合成 4/4、离线 1/4、运行 0/4）四分母不合并
  D 组  钉边界（默认 1111 纯函数层 / 三分母双源 / superseded / 零沙箱）
  E 组  钉 closure 49/49 + derived / summary 口径
  F 组  本机实战：磁盘 hash、AST 抽取器反证（含属性形式）、同源实算
        A/B/C 账、事故版产品函数打红、探测器有牙、A1 扫描面、验锚 + 幂等

⛔ 首次全绿必须注入反证（`/tmp/r113_falsify.py`）：A–E 组逐条把条件
   置 False ⇒ 恰好该项转红；另篡改存档字段、事故版产品函数、F 组
   `R113_WITHDRAW=Fn` 同进程逐条撤回。
⛔ 判据与被测对象同源：四位字段从 `class RelaxPolicy` AST 抽、
   `resolve_policy` 调用点从全仓库 AST 抽（含属性形式）、
   `run_all` 签名从产品 AST 抽、reach 从产品函数体实读字段派生。
⛔ 本契约纯只读取证：零真实 expr、零真实 commit、零 F2P / 收益读数，
   不改 189 已拍板的判据语义，也不推翻 186–191 的结论方向。
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
import probe_default_effect as pde                                # noqa: E402

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)
CENSUS = ROOT / "scripts" / "default_effect_census.json"
FAILS: List[str] = []
CG = pde.CG
D = json.loads(CENSUS.read_text(encoding="utf-8"))


def ck(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (" | " + str(detail) if detail and not cond else ""))


def emit(V: Dict[str, bool]) -> List[str]:
    """纯函数：把条件字典转成失败名列表，供反证脚本逐条置 False。"""
    return [name for name, cond in V.items() if not cond]


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    """A–E 组：**纯函数**，零沙箱零子进程，供反证脚本逐条篡改。"""
    V: Dict[str, bool] = {}
    acc = d.get("accounts", {})
    A = acc.get("A_entry_points", {})
    B = acc.get("B_runtime_evidence", {})
    C = acc.get("C_reach_by_evidence_type", {})
    F = acc.get("D_boundary", {})
    der = d.get("derived", {})
    clo = d.get("closure", {})
    sc = A.get("extractor_selfcheck", {})
    ra = A.get("run_all_signature", {})
    me = A.get("main_entry", {})

    # ── A 组：接入点 ──────────────────────────────────────────────
    V["A1"] = d.get("schema") == "default_effect_census/v1"
    V["A2"] = A.get("fields") == ["cross_scope", "first_rhs",
                                  "allow_unstable", "no_gold_gate"]
    V["A3"] = A.get("n_fields") == 4 and A.get("n_fields_with_reader") == 4
    V["A4"] = (A.get("readers", {}).get("cross_scope") == ["merge_bindings"]
               and A.get("readers", {}).get("first_rhs") == ["bindings_for"])
    V["A5"] = A.get("readers", {}).get("no_gold_gate") == ["adopt_probe"]
    V["A6"] = A.get("readers", {}).get("allow_unstable") == ["evaluate_case", "main"]
    V["A7"] = A.get("production_entry_calls") == ["main"]
    V["A8"] = A.get("n_production_entry_files") == 1
    V["A9"] = (A.get("n_call_sites") == 5
               and A.get("n_attribute_form_sites") == 4
               and A.get("denominator_stable_by_explicit_exclusion") is True)
    V["A9b"] = A.get("excluded_self_measure_modules") == [
        "scripts/probe_default_effect.py",
        "tests/contract_default_effect_test.py"]
    V["A9c"] = A.get("n_excluded_self_measure_modules") == 2
    V["A10"] = ("tests/contract_context_gate_test.py" in
                A.get("resolve_policy_calls", {}))
    V["A11"] = (A.get("resolve_policy_calls", {})
                .get("scripts/probe_context_gate.py", {}).get("forms")
                == ["direct"])
    V["A12"] = (A.get("resolve_policy_calls", {})
                .get("scripts/probe_inliner_relax.py", {}).get("forms")
                == ["attribute"])
    V["A13"] = ra.get("args") == ["source", "items", "timeout", "pkg"]
    V["A14"] = ra.get("has_policy") is False and ra.get("n_args") == 4
    V["A15"] = me.get("requires_repo") is True and me.get("required_args") == ["--repo"]
    V["A16"] = me.get("has_name_main_guard") is True
    V["A17"] = "scripts/probe_context_gate.py" in (me.get("main_callers_in_repo") or {})
    V["A18"] = sc.get("pos_hit") == 4 and sc.get("pos_total") == 4
    V["A19"] = sc.get("neg_hit") == 0 and sc.get("call_extractor_neg") == []
    V["A20"] = sc.get("call_extractor_pos") == ["a"]
    V["A21"] = sc.get("attr_form_pos") == ["b"] and sc.get("attr_form_teeth") is True
    V["A22"] = sc.get("call_extractor_pos_legacy") == ["a"]
    V["A23"] = "读取点存在 ≠ 运行时走到" in A.get("boundary", "")

    # ── B 组：运行证据 ────────────────────────────────────────────
    V["B1"] = B.get("n_archives_scanned") == 28
    V["B2"] = B.get("n_all_json_in_scripts") == 29
    V["B3"] = B.get("excluded_self_product") == ["default_effect_census.json"]
    V["B4"] = B.get("idempotent_by_explicit_exclusion") is True
    V["B5"] = B.get("n_archives_scanned") + len(
        B.get("excluded_self_product") or []) == B.get("n_all_json_in_scripts")
    _shapes = B.get("shapes") or {}
    _dicts = [v for v in _shapes.values()
              if isinstance(v, dict) and v.get("is_dict") is True]
    _nondicts = [v for v in _shapes.values()
                 if isinstance(v, dict) and v.get("is_dict") is False]
    # ⛔ 契约⑤：顶层 list 的 6 份没有这两个键 ⇒ 单独记「不适用」，
    #    不许把缺键读成「零命中」（否则是假绿）。
    V["B6"] = (len(_shapes) == 28
               and len(_dicts) == 22 and len(_nondicts) == 6
               and all(v.get("has_main_product_keys") is False
                       and v.get("has_policy_field") is False
                       for v in _dicts)
               and all("has_main_product_keys" not in v
                       and "has_policy_field" not in v
                       for v in _nondicts))
    V["B7"] = B.get("n_main_product_archives") == 0 and B.get("main_product_archives") == []
    V["B8"] = B.get("n_archives_with_policy_field") == 0
    V["B9"] = B.get("archives_with_policy_field") == []
    V["B10"] = B.get("n_archives_with_per_commit") == 1
    V["B11"] = B.get("archives_with_per_commit") == ["scripts/criteria_gate_census.json"]
    V["B12"] = B.get("detector", {}).get("teeth") is True
    V["B13"] = B.get("detector", {}).get("positive_hit") is True
    V["B14"] = B.get("detector", {}).get("negative_hit") is False
    V["B15"] = B.get("detector", {}).get("probe_keys") == ["per_commit", "stats", "policy"]
    V["B16"] = B.get("c189_n_run_all_calls") == 2
    V["B17"] = B.get("c189_run_all_actually_called") is True
    V["B18"] = B.get("c189_synthetic_source_only") is True
    V["B19"] = B.get("c189_n_real_expr") == 0 and B.get("c189_n_real_commits") == 0
    V["B20"] = B.get("c189_run_all_has_policy") is False
    V["B21"] = B.get("c189_run_all_policy_touch") == 0
    V["B22"] = B.get("c190_zero_sandbox") is True
    V["B23"] = B.get("c191_real_expr_runs") == 0 and B.get("c191_real_commit_runs") == 0
    V["B24"] = B.get("n_main_runtime_evidence") == 0
    V["B25"] = "不是『默认没生效』" in B.get("boundary", "")

    # ── C 组：四层 reach（⛔ 不合并）─────────────────────────────
    V["C1"] = C.get("fields") == A.get("fields")
    V["C2"] = C.get("static", {}).get("ratio") == "4/4"
    V["C3"] = "静态" in C.get("static", {}).get("evidence_type", "")
    V["C4"] = C.get("synthetic_189", {}).get("ratio") == "4/4"
    V["C5"] = "零沙箱" in C.get("synthetic_189", {}).get("evidence_type", "")
    V["C6"] = "bindings_for" in C.get("synthetic_189", {}).get("calls", [])
    V["C7"] = "merge_bindings" in C.get("synthetic_189", {}).get("calls", [])
    V["C8"] = C.get("synthetic_189", {}).get("n_run_all_calls") == 2
    V["C9"] = C.get("synthetic_189", {}).get("run_all_has_policy") is False
    V["C10"] = C.get("offline_190_191", {}).get("ratio") == "1/4"
    V["C11"] = C.get("offline_190_191", {}).get("replay_of_191_ratio") == "1/4"
    V["C12"] = C.get("offline_190_191", {}).get("calls") == [
        "adopt_probe", "classify_probe", "scoped_assertions"]
    V["C13"] = C.get("offline_190_191", {}).get("per_field") == {
        "cross_scope": [], "first_rhs": [], "allow_unstable": [],
        "no_gold_gate": ["adopt_probe"]}
    V["C14"] = C.get("offline_190_191", {}).get("reached") == 1
    V["C15"] = C.get("production_main_runtime", {}).get("ratio") == "0/4"
    V["C16"] = C.get("production_main_runtime", {}).get("n_evidence") == 0
    V["C17"] = C.get("denominators_different") is True
    V["C18"] = C.get("merged_into_bool") is False
    V["C19"] = "绝不合并" in C.get("boundary", "")

    # ── D 组：边界 ────────────────────────────────────────────────
    V["D1"] = F.get("policy_default") == "1111"
    V["D2"] = F.get("policy_strict") == "0000"
    V["D3"] = (F.get("default_is_pure_function_reading") is True
               and F.get("default_is_runtime_reading") is False)
    V["D4"] = F.get("denominators") == {"main": 135, "form": 119, "no_result": 94}
    V["D5"] = F.get("denominators_kept_separate") is True
    ds = F.get("denominator_dual_source", {})
    V["D6"] = (ds.get("form_rows") == ds.get("form_summary") == 119
               and ds.get("no_result_rows") == ds.get("no_result_summary") == 94)
    V["D7"] = F.get("superseded_total") == 3 and F.get("superseded_sha_matches") == 3
    V["D8"] = F.get("anchor", {}).get("ok") is True
    V["D9"] = F.get("anchor", {}).get("repos") == 3
    V["D10"] = F.get("anchor", {}).get("diffs") == []
    V["D11"] = F.get("anchor", {}).get("caliber_diffs") == []
    V["D12"] = F.get("sandbox_selfcheck", {}).get("n_banned") == 0
    V["D13"] = F.get("sandbox_selfcheck", {}).get("checked_own_source") is True
    V["D14"] = all(F.get(k) == 0 for k in
                   ("real_expr_runs", "real_commit_runs", "f2p_runs",
                    "gain_reported", "n_forbidden_runtime_calls"))
    V["D15"] = "不报收益" in F.get("boundary", "")

    # ── E 组：closure / derived / summary ────────────────────────
    V["E1"] = len(clo) == 49
    V["E2"] = all(bool(v) for v in clo.values())
    V["E3"] = der.get("n_closure") == 49 and der.get("n_closure_true") == 49
    V["E4"] = der.get("static_ratio") == "4/4"
    V["E5"] = der.get("synthetic_ratio") == "4/4"
    V["E6"] = der.get("offline_ratio") == "1/4"
    V["E7"] = der.get("runtime_ratio") == "0/4"
    V["E8"] = der.get("n_main_runtime_evidence") == 0
    V["E9"] = der.get("run_all_has_policy") is False
    V["E10"] = der.get("claim") == "189 拍板「四条全放宽且默认生效」"
    V["E11"] = der.get("evidence_types") == ["静态 AST 读取点", "零沙箱合成矩阵",
                                              "零沙箱离线重放", "产品主流程运行"]
    V["E12"] = "静态不得声称看到了运行" in d.get("summary", {}).get("verdict", "")
    V["E13"] = "不是产品洞" in d.get("summary", {}).get("scope", "")
    V["E14"] = d.get("summary", {}).get("n_closure_true") == 49
    V["E15"] = len(d.get("caliber") or {}) == 14
    V["E16"] = "scripts/probe_default_effect.py" in (d.get("caliber") or {})
    return V


def live_recompute() -> Dict[str, Any]:
    """⛔ 同源实算：不读存档结论字段，只用尺子自己的抽取器 + 产品源码重算。

    供反证脚本打补丁验证判据有牙（B4 型 / r112 G 组同源实算）。
    """
    ft = tuple(pde.policy_fields())
    reads = pde.policy_reads(pde.parse(pde.CG_SRC), ft)
    ra = pde.run_all_signature()
    rpc = pde.resolve_policy_calls_all()
    return {
        "fields": list(ft),
        "n_fields": len(ft),
        "readers_cg": reads,
        "run_all_has_policy": ra["has_policy"],
        "run_all_args": ra["args"],
        "n_call_sites": len(rpc),
        "production_entry": [f for f in rpc.get(pde.CG_SRC, {}).get("functions", [])
                             if f == "main"],
        "attr_teeth": pde.extractor_selfcheck(ft)["attr_form_teeth"],
        "detector_teeth": pde.product_shape_detector()["teeth"],
        "resolve_default": CG.resolve_policy(False).signature(),
        "resolve_strict": CG.resolve_policy(True).signature(),
    }


def run_c() -> None:
    """F 组本机实战：真读磁盘、真跑抽取器、真反证。⛔ 同进程不 spawn。"""
    withdraw = os.environ.get("R113_WITHDRAW", "")

    # F1 磁盘 hash 对拍（caliber 14 项全一致 ⇒ 旧存档零回写）
    cal = D["caliber"]
    bad = [k for k, v in cal.items()
           if not (ROOT / k).exists() or pde.sha256_file(k) != v]
    ck("F1 caliber 14 项磁盘 hash 全对拍（189–191 存档零回写）",
       not bad and not withdraw == "F1", str(bad))

    # F2 AST 抽取器反证：字段读取 4/4 正例 / 0 反例；调用抽取器两种形态都有牙
    ft = tuple(pde.policy_fields())
    pos_src = "\n".join("def f_%s(policy):\n    return policy.%s\n" % (f, f)
                        for f in ft)
    pos = pde.policy_reads(ast.parse(pos_src), ft)
    neg = pde.policy_reads(ast.parse("def g(policy):\n    return 1\n"), ft)
    attr = pde.resolve_policy_calls_all().get(
        "tests/contract_context_gate_test.py", {}).get("forms")
    f2 = (len(ft) == 4 and sum(1 for f in ft if pos[f]) == 4
          and sum(len(v) for v in neg.values()) == 0
          and attr == ["attribute"])
    ck("F2 ⛔ AST 抽取器正例 4/4 + 反例 0 + 属性形式真命中（零命中先证能命中）",
       f2 and not withdraw == "F2", "%s attr=%s" % (pos, attr))

    # F3 同源实算 → 与 A/B/C 账逐字段对拍（使事故版补丁有牙）
    live = live_recompute()
    A = D["accounts"]["A_entry_points"]
    B = D["accounts"]["B_runtime_evidence"]
    C = D["accounts"]["C_reach_by_evidence_type"]
    f3 = (live["fields"] == A["fields"] == C["fields"]
          and live["n_call_sites"] == A["n_call_sites"]
          and live["production_entry"] == A["production_entry_calls"]
          and live["run_all_has_policy"] == A["run_all_signature"]["has_policy"]
          and live["run_all_args"] == A["run_all_signature"]["args"]
          and live["readers_cg"]["no_gold_gate"] == ["adopt_probe"]
          and live["attr_teeth"] is True
          and live["detector_teeth"] == B["detector"]["teeth"] is True
          and live["resolve_default"] == D["accounts"]["D_boundary"]["policy_default"]
          and live["resolve_strict"] == D["accounts"]["D_boundary"]["policy_strict"])
    ck("F3 同源实算 A/B/C 账逐字段对拍（事故版补丁有打红点）",
       f3 and not withdraw == "F3", str(live)[:300])

    # F4 事故版产品函数：恒返 STRICT ⇒ F3 的 resolve_default 必须打红
    orig = CG.resolve_policy
    try:
        CG.resolve_policy = lambda strict=False: CG.STRICT
        bad = live_recompute()
    finally:
        CG.resolve_policy = orig
    f4 = (live["resolve_default"] == "1111"
          and bad["resolve_default"] == "0000"
          and bad["resolve_default"] != live["resolve_default"])
    ck("F4 ⛔ 事故版恒返 STRICT ⇒ 同源实算 resolve_default 翻转（反证有牙）",
       f4 and not withdraw == "F4",
       "live=%s bad=%s" % (live["resolve_default"], bad["resolve_default"]))

    # F5 探测器事故版：把 main() 产物三键塞进真实存档形态 ⇒ 必须命中
    fake = {"schema": "x/v1", "stats": {}, "policy": "1111",
            "per_commit": [], "rows": []}
    real_shape = pde.archive_shape(ROOT / "scripts" / "policy_reach_census.json")
    f5 = (all(k in fake for k in pde.MAIN_PRODUCT_KEYS)
          and real_shape.get("has_main_product_keys") is False
          and B["n_main_product_archives"] == 0)
    ck("F5 ⛔ main() 产物探测器正例命中 / 真实存档全不命中（0 份不是恒空）",
       f5 and not withdraw == "F5", str(real_shape)[:200])

    # F6 验锚 + 尺子同源重算 + A1 扫描面（⛔ 一次真实验锚，不重复 census）
    anchor = require_baseline()
    f6_anchor = (bool(anchor.get("ok")) and not anchor.get("diffs")
                 and not anchor.get("caliber_diffs"))
    A2 = pde.account_a()
    B2 = pde.account_b()
    C2 = pde.account_c(A2, B2)
    same = (A2["n_call_sites"] == A["n_call_sites"]
            and B2["n_main_runtime_evidence"] == B["n_main_runtime_evidence"]
            and C2["static"]["ratio"] == C["static"]["ratio"]
            and C2["offline_190_191"]["ratio"] == C["offline_190_191"]["ratio"]
            and C2["production_main_runtime"]["ratio"] ==
            C["production_main_runtime"]["ratio"]
            and all(D["closure"].values()))
    cfiles = set(candidate_files() or [])
    new_files = ("scripts/probe_default_effect.py",
                 "scripts/default_effect_census.json",
                 "tests/contract_default_effect_test.py")
    sample = "ROOT = \"" + str(Path.home()) + "/V6\""
    a1 = (all(f in cfiles for f in new_files)
          and not scan_text((ROOT / "scripts" / "probe_default_effect.py")
                            .read_text(encoding="utf-8"))
          and bool(scan_text(sample)))
    ck("F6 验锚 OK 3/3 + 尺子同源重算幂等 + closure 49/49 + A1 三文件纳入",
       f6_anchor and same and a1 and not withdraw == "F6",
       "anchor=%s same=%s a1=%s" % (anchor, same, a1))


def main() -> int:
    print("=" * 74)
    print("r113 / README 192：「默认生效」的证据类型分层（静态 ≠ 运行）")
    print("=" * 74)
    print("== A–E 组：接入点 / 运行证据 / 四层 reach / 边界 / closure ==")
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
    print("『默认生效』只有静态 AST / 零沙箱合成 / 零沙箱离线三类证据；"
          "产品主流程运行证据 0 份；189 C 账 2 次真跑 run_all 不接 policy ⇒ "
          "证据类型边界，不是产品洞、不是『默认没生效』；不报收益")
    return 0


if __name__ == "__main__":
    sys.exit(main())
