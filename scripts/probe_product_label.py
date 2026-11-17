#!/usr/bin/env python3
"""r119 / README 198 正式尺子：第五类证据的「产品模块」标签是量具自证。

═══ 命题（纯只读取证，不翻案 194–197）═══

194 写「验收契约真跑**产品模块**」，r118（§197）把 8 次真跑里 4 帧
标 `in_product=true` / `product_selftest`。本尺子量一件从未量过的事：
这 4 帧的 caller_file 到底落在哪一层？被真跑的 resolve_policy 定义在
`aether/` 产品包还是 `scripts/` 量具？

**四本账分持，不合并成布尔**：
  A 定义/调用点层：resolve_policy 定义点唯一落在量具；aether/ 零定义零调用；
    192 分母仍 5；产品 extract.py sha 仍是 §189 的 97420318…
  B 帧真实归因层：runner 同构 driver 真跑两契约，按 caller_file 逐帧判
    （aether/ / scripts 量具 / tests 契约三层），产品包内帧数 = 0；
    与 r118 存档的 in_product 标签逐帧对拍，证明标签=量具帧而非产品帧
  C 鉴别力层：注入反例——把一份契约帧的 caller_file 换成量具路径，
    in_product 判据翻转 ⇒ 判据只认文件名、不认定义域；再换成 aether 路径
    判据仍 false ⇒ 对「真正产品帧」零识别（因为产品包根本无此函数）
  D 边界层：三分母双源、superseded、验锚、§183 五项零、新文件分母不漂移

⛔ 纯只读取证：零真实 expr、零真实 commit、零 main() --repo、零 F2P / 收益。
⛔ 不写死绝对路径（A1 scan_text 零命中）；根从 __file__ 推导。
⛔ 新文件不出现 `resolve_policy(...)` 的 Python 调用语法（192/194 分母不漂移）。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import probe_default_effect as pde                       # noqa: E402
import probe_truerun_count as ptc                        # noqa: E402
from realrepo_baseline import require_baseline            # noqa: E402

OUT = ROOT / "scripts" / "product_label_census.jsonl"   # 非 .json：193 F3
SELF_PRODUCT = OUT.name
SELF_SRC = "scripts/probe_product_label.py"
SELF_CONTRACT = "scripts/product_label_contract.py"
CG_SRC = "scripts/probe_context_gate.py"
EXTRACT_SRC = "aether/extract.py"
CHECK_SRC = "aether/check.py"
C1 = "tests/contract_context_gate_test.py"
C2 = "tests/contract_inliner_relax_test.py"
CONTRACTS = (C1, C2)

ARCH_R118 = ROOT / "scripts" / "truerun_count_census.jsonl"
ARCH_192 = ROOT / "scripts" / "default_effect_census.json"
ARCH_189 = ROOT / "scripts" / "inliner_relax_census.json"
ARCH_SUPER = ROOT / "scripts" / "caliber_superseded.json"
README = ROOT / "README.md"

#: §189 钉死的产品 sha 前缀（原文 9667 行）。
EXTRACT_SHA189_PREFIX = "97420318"
#: §189 原文锚（9666 行）。
PRODUCT_UNCHANGED_ANCHOR = "定义与执行主体是量具"

CALIBER_FILES: tuple = (
    EXTRACT_SRC, CHECK_SRC, CG_SRC,
    "scripts/probe_inliner_relax.py", "scripts/probe_policy_reach.py",
    "scripts/probe_default_effect.py", "scripts/probe_truerun_count.py",
    "scripts/probe_selftest_evidence.py", "scripts/probe_zero_discrimination.py",
    C1, C2, "tests/contract_default_effect_test.py",
    "tests/contract_selftest_evidence_test.py",
    "scripts/default_effect_census.json",
    "scripts/inliner_relax_census.json",
    "scripts/selftest_evidence_census.jsonl",
    "scripts/truerun_count_census.jsonl",
)

_ANCHOR: Dict[str, Any] = {}


def sha256_file(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "MISSING:" + rel
    return hashlib.sha256(p.read_bytes()).hexdigest()


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


def parse(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


# ─────────────── A 账：定义/调用点层 ───────────────

def find_definitions(name: str) -> List[Dict[str, Any]]:
    """全仓 AST 找某个函数的定义点（只认 def，不认调用）。"""
    out: List[Dict[str, Any]] = []
    for p in sorted(ROOT.glob("**/*.py")):
        rel = str(p.relative_to(ROOT))
        if "/.venv/" in rel or rel.startswith("."):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in ast.walk(tree):
            if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == name):
                out.append({"file": rel, "line": n.lineno})
    return out


def rel_of(path: str) -> str:
    """把 driver 的绝对路径归一为仓库相对路径；非本仓返回空串。"""
    try:
        p = Path(path).resolve().relative_to(ROOT)
    except (ValueError, OSError):
        return ""
    return str(p)


def account_a() -> Dict[str, Any]:
    defs = find_definitions("resolve_policy")
    calls = pde.resolve_policy_calls_all()      # 同源抽取器，192 分母
    saved = json.loads(ARCH_192.read_text(encoding="utf-8"))[
        "accounts"]["A_entry_points"]
    saved_calls = saved["resolve_policy_calls"]
    aether_defs = [d for d in defs if d["file"].startswith("aether/")]
    aether_calls = {k: v for k, v in calls.items() if k.startswith("aether/")}
    readme_text = README.read_text(encoding="utf-8")
    anchor_line = [i for i, ln in enumerate(readme_text.splitlines(), 1)
                   if PRODUCT_UNCHANGED_ANCHOR in ln]
    extract_sha = sha256_file(EXTRACT_SRC)
    return {
        "definition_sites": defs,
        "n_definition_sites": len(defs),
        "definition_in_aether": aether_defs,
        "n_definition_in_aether": len(aether_defs),
        "definition_in_gauge": [d for d in defs if d["file"] == CG_SRC],
        "call_files_live": sorted(calls),
        "n_call_files_live": len(calls),
        "call_files_saved": sorted(saved_calls),
        "n_call_files_saved": saved["n_call_sites"],
        "dual_source_match": sorted(calls) == sorted(saved_calls)
                             and len(calls) == saved["n_call_sites"],
        "call_files_in_aether": sorted(aether_calls),
        "n_call_files_in_aether": len(aether_calls),
        "product_extract_sha256": extract_sha,
        "product_sha_matches_189": extract_sha.startswith(EXTRACT_SHA189_PREFIX),
        "product_unchanged_anchor_lines": anchor_line,
        "product_unchanged_anchor_present": bool(anchor_line),
        "merged_into_bool": False,
    }


# ─────────────── B 账：帧真实归因 ───────────────

def classify_caller(rel: str) -> str:
    """按 caller 文件的真实路径分层（不是按文件名子串）。"""
    if rel.startswith("aether/"):
        return "product_aether"
    if rel.startswith("scripts/"):
        return "gauge_scripts"
    if rel.startswith("tests/"):
        return "contract_tests"
    return "unclassified"


def old_label(rel: str) -> bool:
    """r118 的 in_product 判据逐字复现：CG_SRC in caller_file。"""
    return CG_SRC in rel


def account_b() -> Dict[str, Any]:
    saved = json.loads(ARCH_R118.read_text(encoding="utf-8"))[
        "accounts"]["B_runtime_counter"]
    per: Dict[str, Dict[str, Any]] = {}
    for rel in CONTRACTS:
        drv = ptc.run_driver(rel, "default")
        plain = ptc.run_plain(rel)
        hits = drv["payload"].get("hits", [])
        frames = []
        for h in hits:
            r = rel_of(h["caller_file"])
            frames.append({"caller": h["caller_name"],
                           "line": h["caller_line"],
                           "rel": r,
                           "layer": classify_caller(r),
                           "old_in_product": old_label(r)})
        per[rel] = {
            "n_runtime": len(hits),
            "frames": frames,
            "by_layer": dict(Counter(f["layer"] for f in frames)),
            "by_old_label": {
                "true": sum(1 for f in frames if f["old_in_product"]),
                "false": sum(1 for f in frames if not f["old_in_product"]),
            },
            "driver_ok": drv["ok"], "driver_rc": drv["rc"],
            "plain_rc": plain["rc"],
            "rc_identical": drv["rc"] == plain["rc"],
            "stdout_identical": drv["stdout"] == plain["stdout"],
        }
    all_frames = [f for v in per.values() for f in v["frames"]]
    by_layer = dict(Counter(f["layer"] for f in all_frames))
    saved_frames = [h for v in saved["per_contract"].values()
                    for h in v["hits"]]
    saved_in_product = sum(1 for h in saved_frames if h["in_product"])
    live_old_true = sum(1 for f in all_frames if f["old_in_product"])
    return {
        "per_contract": per,
        "n_total_runtime": len(all_frames),
        "by_layer_total": by_layer,
        "n_product_aether_frames": by_layer.get("product_aether", 0),
        "n_gauge_frames": by_layer.get("gauge_scripts", 0),
        "n_contract_frames": by_layer.get("contract_tests", 0),
        "n_saved_in_product_true": saved_in_product,
        "n_live_old_label_true": live_old_true,
        "label_matches_saved": live_old_true == saved_in_product,
        "old_true_are_all_gauge": all(
            f["layer"] == "gauge_scripts"
            for f in all_frames if f["old_in_product"]),
        "product_frame_never_labeled": all(
            not f["old_in_product"]
            for f in all_frames if f["layer"] == "product_aether"),
        "saved_total_8": saved["n_total_runtime"] == len(all_frames) == 8,
        "all_rc_stdout_identical": all(
            v["rc_identical"] and v["stdout_identical"]
            for v in per.values()),
        "merged_into_bool": False,
    }


# ─────────────── C 账：鉴别力（注入反例） ───────────────

def counterfactual_labels() -> Dict[str, Any]:
    """把同一帧的 caller_file 换成三层路径，看 in_product 判据怎么变。"""
    base = {"caller_name": "selftest", "caller_line": 311}
    worlds: Dict[str, Any] = {}
    for tag, rel in (("W0_gauge", CG_SRC),
                     ("W1_contract", C1),
                     ("W2_product", EXTRACT_SRC),
                     ("W3_gauge_suffix", CG_SRC + ".fake")):
        worlds[tag] = {
            "rel": rel,
            "layer": classify_caller(rel),
            "old_in_product": old_label(rel),
        }
    old_values = [w["old_in_product"] for w in worlds.values()]
    layers = [w["layer"] for w in worlds.values()]
    return {
        "worlds": worlds,
        "old_label_values": old_values,
        "distinct_layers": len(set(layers)),
        "old_label_flips_on_filename": (
            worlds["W0_gauge"]["old_in_product"] is True
            and worlds["W2_product"]["old_in_product"] is False
            and worlds["W3_gauge_suffix"]["old_in_product"] is True),
        "product_world_not_recognized": (
            worlds["W2_product"]["layer"] == "product_aether"
            and worlds["W2_product"]["old_in_product"] is False),
        "n_product_worlds": sum(
            1 for w in worlds.values() if w["layer"] == "product_aether"),
        "n_product_unrecognized": sum(
            1 for w in worlds.values()
            if w["layer"] == "product_aether" and not w["old_in_product"]),
        "product_unrecognized_has_teeth": (
            worlds["W2_product"]["layer"] == "product_aether"
            and worlds["W2_product"]["old_in_product"] is False),
    }


def account_c(A: Dict[str, Any], B: Dict[str, Any]) -> Dict[str, Any]:
    cf = counterfactual_labels()
    gauge_positive = B["n_gauge_frames"] == 4 and B["n_product_aether_frames"] == 0
    false_label = B["n_saved_in_product_true"] == 4
    return {
        "counterfactual": cf,
        "gauge_positive_has_teeth": gauge_positive,
        "saved_label_is_gauge_not_product": (
            false_label and B["old_true_are_all_gauge"] is True),
        "old_label_ignores_definition_domain": (
            cf["old_label_flips_on_filename"] is True
            and cf["product_world_not_recognized"] is True),
        "two_ledgers_separate": B["merged_into_bool"] is False
                                and A["merged_into_bool"] is False,
        "n_product_worlds": cf["n_product_worlds"],
        "n_product_unrecognized": cf["n_product_unrecognized"],
        "product_unrecognized_has_teeth":
            cf["product_unrecognized_has_teeth"] is True,
        "product_world_is_positive_not_vacuous": (
            cf["n_product_worlds"] == 1
            and cf["n_product_unrecognized"] == 1
            and cf["product_unrecognized_has_teeth"] is True),
        "discriminates": (gauge_positive and false_label
                          and cf["old_label_flips_on_filename"]),
    }


# ─────────────── D 账：边界 ───────────────

def denominators() -> Dict[str, Any]:
    """与 r118::denominators 同源：rows 长度 vs summary 双源对拍。"""
    main = json.loads((ROOT / "scripts" / "extract_downstream.json")
                      .read_text(encoding="utf-8"))
    form = json.loads((ROOT / "scripts" / "form_shape_attribution.json")
                      .read_text(encoding="utf-8"))
    nores = json.loads((ROOT / "scripts" / "no_result_attribution.json")
                       .read_text(encoding="utf-8"))
    mr = len(main.get("rows") or [])
    ms = (main.get("summary") or {}).get("n_main", -1)
    fr = len(form.get("rows") or [])
    fs = (form.get("summary") or {}).get("n_rows", -1)
    nr = len(nores.get("rows") or [])
    ns = (nores.get("summary") or {}).get("n_rows", -1)
    return {"main": mr, "form": fr, "no_result": nr,
            "dual_source": {"main_rows": mr, "main_summary": ms,
                            "form_rows": fr, "form_summary": fs,
                            "no_result_rows": nr, "no_result_summary": ns},
            "all_distinct": len({mr, fr, nr}) == 3}


def superseded_check() -> Dict[str, Any]:
    reg = json.loads(ARCH_SUPER.read_text(encoding="utf-8"))
    registered = reg.get("archive_sha256_at_registration") or {}
    diffs = [rel for rel, want in registered.items()
             if not (ROOT / rel).exists() or sha256_file(rel) != want]
    return {"n_registered": len(registered), "diffs": sorted(diffs),
            "all_match": not diffs}


def selfcheck() -> Dict[str, Any]:
    own = {rel: ptc.call_count_in_file(rel, "resolve_policy")
           for rel in (SELF_SRC, SELF_CONTRACT)
           if (ROOT / rel).exists()}
    pos = ptc.call_count_in_file(CG_SRC, "resolve_policy")
    inv = ptc._load("pzd_r119", "scripts/probe_zero_discrimination.py") \
              .json_inventory()
    saved = json.loads((ROOT / "scripts" / "zero_discrimination_census.json")
                       .read_text(encoding="utf-8"))[
                           "accounts"]["B_scan_surface"]["coverage_scripts"]
    return {
        "own_call_sites": own,
        "no_new_denominator_call_site": all(v == 0 for v in own.values()),
        "positive_extractor_has_teeth": pos > 0, "positive_n": pos,
        "coverage_live": inv["coverage_scripts"],
        "coverage_saved": saved,
        "coverage_unchanged": inv["coverage_scripts"] == saved,
        "self_archive_suffix": Path(SELF_PRODUCT).suffix,
        "self_archive_not_json": not SELF_PRODUCT.endswith(".json"),
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
        "product_files_modified": 0,
        "merged_into_bool": False,
    }


# ─────────────── closure ───────────────

def build_closure(A, B, C, D, anchor) -> Dict[str, bool]:
    V: Dict[str, bool] = {}
    V["a1_definition_unique"] = A["n_definition_sites"] == 1
    V["a2_definition_in_gauge"] = any(
        d["file"] == CG_SRC for d in A["definition_in_gauge"])
    V["a3_no_definition_in_aether"] = A["n_definition_in_aether"] == 0
    V["a4_no_call_in_aether"] = A["n_call_files_in_aether"] == 0
    V["a5_denominator_stable_5"] = (
        A["n_call_files_live"] == 5 and A["n_call_files_saved"] == 5
        and A["dual_source_match"] is True)
    V["a6_product_sha_unchanged"] = A["product_sha_matches_189"] is True
    V["a7_product_anchor_present"] = A["product_unchanged_anchor_present"] is True

    V["b1_total_8"] = B["n_total_runtime"] == 8
    V["b2_saved_total_matches"] = B["saved_total_8"] is True
    V["b3_gauge_frames_4"] = B["n_gauge_frames"] == 4
    V["b4_product_frames_0"] = B["n_product_aether_frames"] == 0
    V["b5_contract_frames_4"] = B["n_contract_frames"] == 4
    V["b6_old_label_4"] = B["n_live_old_label_true"] == 4
    V["b7_old_label_matches_saved"] = B["label_matches_saved"] is True
    V["b8_old_true_are_gauge"] = B["old_true_are_all_gauge"] is True
    V["b9_product_never_labeled"] = B["product_frame_never_labeled"] is True
    V["b10_runner_identical"] = B["all_rc_stdout_identical"] is True

    V["c1_gauge_positive"] = C["gauge_positive_has_teeth"] is True
    V["c2_label_is_gauge"] = C["saved_label_is_gauge_not_product"] is True
    V["c3_label_ignores_domain"] = C["old_label_ignores_definition_domain"] is True
    V["c4_two_ledgers"] = C["two_ledgers_separate"] is True
    V["c5_zero_worlds_not_vacuous"] = (
        C["product_world_is_positive_not_vacuous"] is True)
    V["c6_discriminates"] = C["discriminates"] is True

    den, sup, sc = (D["denominator_dual_source"], D["superseded"],
                    D["selfcheck"])
    ds = den["dual_source"]
    V["d1_three_denominators"] = (
        den["main"] == 135 and den["form"] == 119
        and den["no_result"] == 94 and den["all_distinct"] is True)
    V["d1b_denominator_dual_source"] = (
        ds["main_rows"] == ds["main_summary"]
        and ds["form_rows"] == ds["form_summary"]
        and ds["no_result_rows"] == ds["no_result_summary"])
    V["d2_superseded_all_match"] = sup["all_match"] is True
    V["d3_no_new_call_site"] = sc["no_new_denominator_call_site"] is True
    V["d4_extractor_has_teeth"] = sc["positive_extractor_has_teeth"] is True
    V["d5_coverage_unchanged"] = sc["coverage_unchanged"] is True
    V["d6_archive_not_json"] = sc["self_archive_not_json"] is True
    V["d7_caliber_hash_shape"] = (
        len(D["caliber_disk"]) == len(CALIBER_FILES)
        and all(len(v) == 64 and not v.startswith("MISSING:")
                for v in D["caliber_disk"].values()))
    V["d8_section183_untouched"] = (
        D["section_183_touched"] is False and D["real_expr_run"] == 0
        and D["real_commit_run"] == 0 and D["gain_rows_reported"] == 0
        and D["product_files_modified"] == 0)
    V["d9_anchor_ok"] = (anchor.get("ok") is True and anchor.get("repos") == 3
                         and not anchor.get("diffs")
                         and not anchor.get("caliber_diffs"))
    return V


# ─────────────── main ───────────────

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
    C = account_c(A, B)
    D = account_d(anchor_rep)
    closure = build_closure(A, B, C, D, anchor_rep)
    n_true = sum(bool(v) for v in closure.values())
    derived = {
        "claim": "第五类证据的『验收契约真跑产品模块』标签是量具自证："
                 "resolve_policy 定义唯一落在 scripts/ 量具，aether/ 产品包"
                 "零定义零调用；8 帧里 4 帧被 r118 标 in_product=true，"
                 "真实 caller 全是量具 selftest，产品包内帧数 = 0",
        "n_definition_sites": A["n_definition_sites"],
        "n_definition_in_product": A["n_definition_in_aether"],
        "n_call_files_product": A["n_call_files_in_aether"],
        "n_runtime_total": B["n_total_runtime"],
        "n_gauge_frames": B["n_gauge_frames"],
        "n_product_frames": B["n_product_aether_frames"],
        "n_label_true": B["n_live_old_label_true"],
        "label_is_gauge": C["saved_label_is_gauge_not_product"],
        "n_closure": len(closure), "n_closure_true": n_true,
    }
    doc = {
        "schema": "product_label_census/v1",
        "caliber": caliber(),
        "accounts": {"A_definition_layer": A,
                     "B_frame_attribution": B,
                     "C_discrimination": C,
                     "D_boundary": D},
        "derived": derived,
        "closure": closure,
        "anchor": anchor_rep,
        "summary": {
            "verdict": (
                "§194/195/197 说的『验收契约每次全量真跑产品模块』里的"
                "『产品模块』措辞，实测指向的是量具 scripts/probe_context_gate.py："
                "resolve_policy 定义点全仓唯一且落在量具，aether/ 产品包零定义、"
                "零调用、extract.py sha 仍是 §189 的 97420318…。runner 同构 driver "
                "真跑两契约各 4 帧，按 caller_file 真实归因：量具 selftest 4 帧、"
                "契约自身 4 帧、产品包 0 帧；r118 存档的 4 帧 in_product=true "
                "逐帧就是量具帧。注入反例证明该判据只认文件名子串 "
                "（改后缀即翻转、换成 aether 路径仍 false），对『真正产品帧』"
                "零识别力。"),
            "scope": (
                "⛔ 不翻案 194–197：第五类证据确实存在、8 次次数账成立、"
                "签名鉴别力有限——本轮只收窄『产品模块』这个标签：它是量具"
                "自证，不是产品包代码执行。不是产品洞（量具自检跑自己的 policy "
                "是设计使然，§189 已明示四条自限主体是量具），不报收益、"
                "不说默认生效/不生效，§183 零触碰。缺口仍是『默认 policy 对象→"
                "主流程消费链』的绑定证据，补它须拍板。"),
            "n_closure_true": n_true, "n_closure": len(closure),
        },
    }
    dest = Path(args.out) if args.out else OUT
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")
    fail = [k for k, v in closure.items() if not v]
    print("wrote %s" % dest)
    print("closure %d/%d  defs=%d in_aether=%d frames=%d gauge=%d product=%d label=%d"
          % (n_true, len(closure), A["n_definition_sites"],
             A["n_definition_in_aether"], B["n_total_runtime"],
             B["n_gauge_frames"], B["n_product_aether_frames"],
             B["n_live_old_label_true"]))
    print("FAILS=%s" % fail)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
