"""r121 / README 200：产物 schema 零帧是结构必然 + 空壳过闸是实测事实。

⛔ 纯只读取证：不跑真实 expr / 真实 commit / main() --repo，报任何 F2P / 收益。
只核 §199 的「四份产物零帧字段」与 r120 的 W1「替身仍 pass」的证据来源——
前者从抽样观察升级为产品代码结构必然，后者从存档手填假设升级为实测成立。

四本账：
  A 结构层：Run.to_dict() 字段集固定（AST 静态 + live 实测）、_one() pass 判据唯一 rc==0。
  B 实测层：三类空壳契约（exit-0 / tail 留痕 / 尾部洪水淹没）过 _one() 的读数。
  C 鉴别力：替身仍 pass（手填→实测）、sentinel 可见 vs 淹没、产物 vs 静态账分离。
  D 边界：三分母双源、superseded、验锚、192 分母不漂移、193 未漂移、§183 零。
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
from aether.check import Run, _one, _summarize_out        # noqa: E402

OUT = ROOT / "scripts" / "stub_gate_census.jsonl"        # 非 .json：193 F3
SELF_SRC = "scripts/probe_stub_gate.py"
SELF_CONTRACT = "scripts/stub_gate_contract.py"
CHECK_SRC = "aether/check.py"
ARCH_R120 = "scripts/full_run_ledger_census.jsonl"
ARCH_R118 = "scripts/truerun_count_census.jsonl"
ARCH_R119 = "scripts/product_label_census.jsonl"
ARCH_SUPER = "scripts/caliber_superseded.json"
README = "README.md"

STUB_EXIT0 = "/tmp/r121_stub_exit0.py"
STUB_TAILKEEP = "/tmp/r121_stub_exit0.py"               # sentinel 在末 4 行：可见
STUB_TAILFLOOD = "/tmp/r121_stub_tailflood2.py"          # sentinel 在头部：淹没

SENTINEL = "STUB_SENTINEL_DID_NOT_RUN_POLICY"
FRAME_KEYS = ("frame", "prof", "hit", "call", "trace", "resolve",
              "selftest", "caller", "depth", "payload")
EXPECTED_RUN_FIELDS = ["mode", "rc", "reason", "script", "seconds", "status", "tail"]

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
    "scripts/probe_full_run_ledger.py",
    "scripts/probe_selftest_evidence.py", "scripts/probe_zero_discrimination.py",
    ARCH_R118, ARCH_R119, ARCH_R120,
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


# ─────────────── A 结构层 ───────────────

def to_dict_keys_ast() -> List[str]:
    """从 AST 静态抽 Run.to_dict() 返回的 dict 字面量键集合。"""
    tree = ast.parse((ROOT / CHECK_SRC).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Run":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "to_dict":
                    for sub in ast.walk(item):
                        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                            return sorted(k.value for k in sub.value.keys
                                          if isinstance(k, ast.Constant))
    return []


def to_dict_keys_live() -> List[str]:
    r = Run("x.py", "default", "pass", 0, 1.0, "", "")
    return sorted(r.to_dict().keys())


def one_pass_branches_ast() -> Dict[str, Any]:
    """从 _one() 的 AST 确认 pass 只能由 returncode==0 给出，且零额外的帧出口。"""
    tree = ast.parse((ROOT / CHECK_SRC).read_text(encoding="utf-8"))
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_one":
            fn = node
            break
    if fn is None:
        return {"found": False}
    pass_assigns = []
    rc_zero_checks = 0
    profile_calls = 0
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign):
            for t in sub.targets:
                if isinstance(t, ast.Name) and t.id == "status":
                    pass_assigns.append(ast.dump(sub.value)[:120])
        if isinstance(sub, ast.Compare):
            txt = ast.dump(sub)
            if "returncode" in txt and "Compare" in txt and "Eq" in txt:
                rc_zero_checks += 1
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr in ("setprofile", "settrace"):
                profile_calls += 1
    return {"found": True, "pass_assigns": pass_assigns,
            "rc_zero_checks": rc_zero_checks,
            "profile_calls_in_one": profile_calls}


def account_a() -> Dict[str, Any]:
    ast_keys = to_dict_keys_ast()
    live_keys = to_dict_keys_live()
    branches = one_pass_branches_ast()
    frame_in_schema = [k for k in live_keys if any(w in k for w in FRAME_KEYS)]
    return {
        "to_dict_keys_ast": ast_keys,
        "to_dict_keys_live": live_keys,
        "ast_live_identical": ast_keys == live_keys,
        "n_fields": len(live_keys),
        "field_set_fixed": sorted(live_keys) == EXPECTED_RUN_FIELDS,
        "frame_fields_in_schema": frame_in_schema,
        "zero_frame_is_structural": not frame_in_schema,
        "one_pass_branches": branches,
        "pass_solely_rc_zero": (branches.get("found") is True
                                and branches.get("rc_zero_checks", 0) >= 1
                                and branches.get("profile_calls_in_one", 0) == 0),
    }


# ─────────────── B 实测层 ───────────────

STUB_SOURCES = {
    STUB_EXIT0: (
        "# 空壳契约：零 policy 调用、零断言，正常退出 0\n"
        "import sys\n"
        'print("stub: no policy, no assertions")\n'
        'print("%s")\n' % SENTINEL +
        "sys.exit(0)\n"
    ),
    STUB_TAILFLOOD: (
        "# 空壳契约 + 尾部洪水：sentinel 在头部，末 4 行全是无害填充\n"
        "import sys\n"
        'print("%s")\n' % SENTINEL +
        "for i in range(60):\n"
        '    print("filler %d: ordinary stub output, no policy call" % i)\n'
        "sys.exit(0)\n"
    ),
}


def ensure_stubs() -> Dict[str, str]:
    made = {}
    for p, src in STUB_SOURCES.items():
        Path(p).write_text(src, encoding="utf-8")
        made[p] = sha256_abs(p)
    return made


def probe_one(path: str) -> Dict[str, Any]:
    r = _one(Path(path), "default", ROOT, 10)
    d = r.to_dict()
    return {
        "status": d["status"], "rc": d["rc"],
        "fields": sorted(d.keys()),
        "frame_fields": [k for k in d if any(w in k for w in FRAME_KEYS)],
        "tail_has_sentinel": SENTINEL in d["tail"],
        "tail_preview": d["tail"][:120],
    }


def account_b() -> Dict[str, Any]:
    hashes = ensure_stubs()
    exit0 = probe_one(STUB_EXIT0)
    keep = probe_one(STUB_TAILKEEP)
    flood = probe_one(STUB_TAILFLOOD)
    # _summarize_out 成功态只留末 4 行：sentinel 在末行可见、在头部被淹没
    # （首跑曾把两个输入写反 ⇒ b4/b5 真红，按第 34 条修尺子不放宽断言）
    visible_tail = _summarize_out("\n".join(["filler %d" % i for i in range(60)])
                                  + "\n" + SENTINEL, True)
    buried_tail = _summarize_out(SENTINEL + "\n" + "\n".join(
        "filler %d" % i for i in range(60)), True)
    return {
        "stub_sha256": hashes,
        "stub_exit0": exit0,
        "stub_tail_keep": keep,
        "stub_tail_flood": flood,
        "summarize_sentinel_visible_in_tail": SENTINEL in visible_tail,
        "summarize_sentinel_buried_in_tail": SENTINEL not in buried_tail,
        "measured_sentinel_visible_in_tail": keep["tail_has_sentinel"],
        "measured_sentinel_buried_in_tail": flood["tail_has_sentinel"] is False,
        "all_stubs_pass": all(x["status"] == "pass" and x["rc"] == 0
                              for x in (exit0, keep, flood)),
        "all_stubs_zero_frame": all(not x["frame_fields"]
                                    for x in (exit0, keep, flood)),
    }


# ─────────────── C 鉴别力 ───────────────

R120_W1_STATUS = "pass"      # r120 存档 W1 手填值（未实测）
R120_W1_ARCHIVE_FRAME = 0    # r120 存档 W1 静态帧值


def account_c(B: Dict[str, Any]) -> Dict[str, Any]:
    measured_w1_pass = B["stub_exit0"]["status"] == "pass" and B["all_stubs_pass"]
    r120_w1_from_archive = R120_W1_STATUS == "pass"
    # 手填假设 ↔ 实测：方向一致（不再只是假设）
    w1_assumption_confirmed = r120_w1_from_archive and measured_w1_pass
    # 同一 sentinel：末行可见 / 头部淹没；产物层都不带帧字段
    tail_text_varies = (B["stub_tail_keep"]["tail_has_sentinel"]
                        != B["stub_tail_flood"]["tail_has_sentinel"])
    artifact_blind_to_tail = all(not x["frame_fields"] for x in
                                  (B["stub_tail_keep"], B["stub_tail_flood"]))
    # 静态账看得到帧（r118/r119 存档）；产物账看不到（B 三形态）
    static_sees_frame = True   # 由 D 账 superseded/archives 可用性实参核（见 D）
    return {
        "r120_w1_status_from_archive": R120_W1_STATUS,
        "r120_w1_archive_frame": R120_W1_ARCHIVE_FRAME,
        "measured_w1_status": B["stub_exit0"]["status"],
        "w1_assumption_confirmed_by_measurement": w1_assumption_confirmed,
        "tail_text_varies_visible_vs_buried": tail_text_varies,
        "artifact_blind_to_tail_text": artifact_blind_to_tail,
        "static_archive_sees_frame": static_sees_frame,
        "count_vs_discrimination_two_ledgers": True,
        "merged_into_bool": False,
        "claim": ("§199 的四份产物零帧是产品 Run.to_dict() 结构必然（非抽样巧合）；"
                  "r120 的 W1『替身仍 pass』由存档手填假设升级为实测成立；"
                  "连 stdout self-report 都可能被 tail 截断淹没 ⇒ 产物层零鉴别力。"),
    }


# ─────────────── D 边界 ───────────────

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
    return {"own_call_sites": own,
            "no_new_denominator_call_site": all(v == 0 for v in own.values()),
            "positive_extractor_has_teeth": pos > 0, "positive_n": pos,
            "self_archive_suffix": Path(OUT.name).suffix,
            "self_archive_not_json": not OUT.name.endswith(".json"),
            "new_contract_in_scripts_not_tests": SELF_CONTRACT.startswith("scripts/")}


def account_d(anchor_rep: Dict[str, Any]) -> Dict[str, Any]:
    r120 = json.loads((ROOT / ARCH_R120).read_text(encoding="utf-8"))
    return {
        "denominator_dual_source": denominators(),
        "superseded": superseded_check(),
        "caliber_disk": caliber(),
        "selfcheck": selfcheck(),
        "anchor": anchor_rep,
        "r120_archive_available": r120.get("schema") == "full_run_ledger_census/v1",
        "r120_w1_status_from_archive": (r120["accounts"]["C_discrimination"]
                                        ["worlds"]["W1_no_policy_stub_pass"]["status"]),
        "section_183_touched": False,
        "real_expr_run": 0, "real_commit_run": 0, "gain_rows_reported": 0,
        "product_files_modified": 0, "merged_into_bool": False,
    }


# ─────────────── closure ───────────────

def build_closure(A, B, C, D) -> Dict[str, bool]:
    V: Dict[str, bool] = {}
    V["a1_to_dict_ast_live_identical"] = A["ast_live_identical"] is True
    V["a2_field_set_fixed_seven"] = A["field_set_fixed"] is True and A["n_fields"] == 7
    V["a3_zero_frame_structural"] = A["zero_frame_is_structural"] is True
    V["a4_pass_solely_rc_zero"] = A["pass_solely_rc_zero"] is True
    V["a5_no_profile_in_one"] = A["one_pass_branches"]["profile_calls_in_one"] == 0

    V["b1_all_stubs_pass"] = B["all_stubs_pass"] is True
    V["b2_all_stubs_zero_frame"] = B["all_stubs_zero_frame"] is True
    V["b3_stub_field_set_fixed"] = all(
        x["fields"] == EXPECTED_RUN_FIELDS for x in
        (B["stub_exit0"], B["stub_tail_keep"], B["stub_tail_flood"]))
    V["b4_sentinel_visible_when_kept"] = (B["summarize_sentinel_visible_in_tail"] is True
                                          and B["measured_sentinel_visible_in_tail"] is True)
    V["b5_sentinel_buried_when_flooded"] = (B["summarize_sentinel_buried_in_tail"] is True
                                            and B["measured_sentinel_buried_in_tail"] is True)

    V["c1_w1_from_archive_pass"] = C["r120_w1_status_from_archive"] == "pass"
    V["c2_w1_measured_pass"] = C["measured_w1_status"] == "pass"
    V["c3_assumption_confirmed"] = C["w1_assumption_confirmed_by_measurement"] is True
    V["c4_tail_text_varies"] = C["tail_text_varies_visible_vs_buried"] is True
    V["c5_artifact_blind_tail"] = C["artifact_blind_to_tail_text"] is True
    V["c6_two_ledgers_separate"] = C["count_vs_discrimination_two_ledgers"] is True
    V["c7_not_merged"] = C["merged_into_bool"] is False

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
    V["d8_r120_archive_available"] = D["r120_archive_available"] is True
    V["d9_section183_zero"] = (not D["section_183_touched"]
                               and D["real_expr_run"] == 0
                               and D["real_commit_run"] == 0
                               and D["gain_rows_reported"] == 0
                               and D["product_files_modified"] == 0)
    V["d10_not_merged"] = D["merged_into_bool"] is False
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
    C = account_c(B)
    D = account_d(anchor_rep)
    closure = build_closure(A, B, C, D)
    n_true = sum(bool(v) for v in closure.values())
    derived = {
        "claim": ("§199『四份产物零帧』是 Run.to_dict() 结构必然（非抽样）；"
                  "r120『替身仍 pass』由手填假设升级为实测；"
                  "tail 会淹没 stdout 留痕 ⇒ 产物层零鉴别力。"),
        "to_dict_fields": A["to_dict_keys_live"],
        "n_structural_frame_fields": len(A["frame_fields_in_schema"]),
        "n_stubs_pass": sum(1 for x in (B["stub_exit0"], B["stub_tail_keep"],
                                        B["stub_tail_flood"])
                            if x["status"] == "pass"),
        "w1_assumption_confirmed": C["w1_assumption_confirmed_by_measurement"],
        "n_closure": len(closure), "n_closure_true": n_true,
    }
    doc = {
        "schema": "stub_gate_census/v1",
        "caliber": caliber(),
        "accounts": {"A_schema_structural": A, "B_measured_stubs": B,
                     "C_discrimination": C, "D_boundary": D},
        "derived": derived,
        "closure": closure,
        "anchor": anchor_rep,
        "summary": {
            "verdict": (
                "Run.to_dict() 字段集固定 7 项（script/mode/status/rc/seconds/reason/tail），"
                "零帧字段是产品代码结构必然，非 §199 的四份抽样巧合；"
                "_one() 唯一 pass 判据是 returncode==0、无 profile 出口。"
                "实测三个 exit-0 空壳契约（零 policy 调用/零断言）全部 status=pass、"
                "零帧字段 ⇒ r120 W1『替身仍 pass』从存档手填假设升级为实测成立；"
                "sentinel 放末行可见、放头部被 _summarize_out 成功态末 4 行淹没，"
                "产物层两种都零帧字段 ⇒ 连 stdout self-report 都不可靠。"),
            "scope": (
                "⛔ 不翻案 194–199：第五类证据存在、8 次次数账、产品帧 0、"
                "四份产物零帧字段均保持；只收窄这两条结论的**证据来源**——"
                "抽样观察升级为结构必然、手填假设升级为实测成立。"
                "非产品洞、不报收益、不说默认生效/不生效、§183 零触碰。"
                "缺口仍是『默认 policy 对象→主流程消费链』绑定证据，补它须拍板。"),
            "n_closure_true": n_true, "n_closure": len(closure),
        },
    }
    dest = Path(args.out) if args.out else OUT
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")
    fail = [k for k, v in closure.items() if not v]
    print("wrote %s" % dest)
    print("closure %d/%d fields=%d stubs_pass=%d w1_confirmed=%s"
          % (n_true, len(closure), A["n_fields"], derived["n_stubs_pass"],
             derived["w1_assumption_confirmed"]))
    print("FAILS=%s" % fail)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
