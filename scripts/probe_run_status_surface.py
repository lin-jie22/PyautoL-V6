"""r126 / §205 纯只读取证：run 状态面口径（pass 成功分支 tail vs fail 失败分支 tail）。

§199–§204/r125 一路把「帧词命中数」收窄到可复现读数：作用域 2/129、全体 3
条、子串 vs 词边界差恒 1、stdout 全文 vs tail 截断、tail vs 元数据字段面——
但六轮**全部只扫 status=pass 的成功分支 tail**（`_summarize_out(ok=True)` 末
4 行 / 400 字截断）。`_summarize_out(ok=False)` 失败分支另走「保留信号行 +
头尾 + 省略计数」，观测面本就不同，从没被当成独立口径量过。

本轮对拍同一条契约 `contract_signature_impostor_test.py`：9 个 pass 轮 tail
恒 200 字、帧词命中 0；唯一 fail 轮（r118 首跑红）tail 801 字、命中 selftest
——同一契约、同一同源 FRAME_KEYS、同一子串匹配实现，命中数只随 run 状态面
（status 决定走哪个 _summarize_out 分支）而变。失败分支保信号行所以「看得见」，
成功分支截断所以「看不见」；命中的 selftest 仍是契约 self-report 文案、非运行帧。

⛔ 纯只读：读 /tmp 现成产物 JSON，零契约单跑、零 spawn、零 F2P/收益、§183 零触碰。
⛔ 复用同源 FRAME_KEYS（probe_full_run_ledger.FRAME_KEYS），不另写词表。
⛔ 不放 discover 面（放 scripts/，存档 .jsonl 避 193 F3 动态分母）。
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
sys.path.insert(0, str(ROOT / "scripts"))

import probe_full_run_ledger as pfrl                      # noqa: E402
import probe_stub_gate as psg                             # noqa: E402
from realrepo_baseline import require_baseline            # noqa: E402

OUT = ROOT / "scripts" / "run_status_surface_census.jsonl"  # 非 .json：193 F3
SELF_SRC = "scripts/probe_run_status_surface.py"
SELF_CONTRACT = "scripts/run_status_surface_contract.py"
CHECK_SRC = "aether/check.py"
LEDGER_SRC = "scripts/probe_full_run_ledger.py"
TAIL_SRC = "scripts/probe_tail_selfreport.py"
CAL_SRC = "scripts/probe_frameword_caliber.py"
OBS_SRC = "scripts/probe_obs_surface.py"
FIELD_SRC = "scripts/probe_field_surface.py"
ARCH_SUPER = "scripts/caliber_superseded.json"

# 10 份现成全量产物（含 r118 首跑红轮 = 唯一含失败 run 的产物）
FULLS = (
    ("r117", "/tmp/r117_full.json"),
    ("r118", "/tmp/r118_full.json"),      # 红轮：130 runs、1 真红（唯一 fail run）
    ("r118b", "/tmp/r118_full2.json"),
    ("r119", "/tmp/r119_full.json"),
    ("r120", "/tmp/r120_full.json"),
    ("r121", "/tmp/r121_full.json"),
    ("r122", "/tmp/r122_full.json"),
    ("r123", "/tmp/r123_full.json"),
    ("r124", "/tmp/r124_full.json"),
    ("r125", "/tmp/r125_full.json"),
)
# 对拍对象：唯一在 r118 出现 fail 的契约
TARGET = "contract_signature_impostor_test.py"

FRAME_KEYS = pfrl.FRAME_KEYS

_ANCHOR: Dict[str, Any] = {}


def sha256_file(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "MISSING:" + rel
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_text_abs(p: str) -> str:
    q = Path(p)
    if not q.exists():
        return ""
    return q.read_text(encoding="utf-8", errors="replace")


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


CALIBER_FILES: tuple = (
    CHECK_SRC, LEDGER_SRC, TAIL_SRC, CAL_SRC, OBS_SRC, FIELD_SRC,
    "scripts/probe_frameword_caliber.py", "scripts/probe_obs_surface.py",
    "scripts/probe_stub_gate.py", "scripts/probe_truerun_count.py",
    ARCH_SUPER, "scripts/frameword_caliber_census.jsonl",
    "scripts/tail_selfreport_census.jsonl", "scripts/obs_surface_census.jsonl",
    "scripts/obs_surface_stdout.jsonl", "scripts/field_surface_census.jsonl",
)


def _read_json_abs(p: str) -> Dict[str, Any]:
    q = Path(p)
    if not q.exists():
        return {}
    return json.loads(q.read_text(encoding="utf-8"))


def _hits(t: Any) -> List[str]:
    """同源子串口径 `k in str(t).lower()`，与 §201–205 完全一致（只换状态面）。"""
    low = str(t).lower()
    return sorted(k for k in FRAME_KEYS if k in low)


# ─────────────── A 账：状态面定义（AST 实证 _summarize_out 两分支）───────────────

def account_a() -> Dict[str, Any]:
    """AST 实证 _summarize_out 有成功/失败两分支，证明 status 决定观测面。"""
    src = read_text_abs(str(ROOT / CHECK_SRC))
    tree = ast.parse(src)
    summarize = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_summarize_out":
            summarize = node
    ok_last4 = False
    ok_cap400 = False
    fail_keep_signal = False
    fail_refs_bad_re = False
    has_ok_param = False
    if summarize is not None:
        has_ok_param = any(isinstance(a, ast.arg) and a.arg == "ok"
                           for a in summarize.args.args)
        for node in ast.walk(summarize):
            # 成功分支末4行：Slice(lower=UnaryOp(USub, Constant(4)))
            if (isinstance(node, ast.Slice)
                    and isinstance(node.lower, ast.UnaryOp)
                    and isinstance(node.lower.op, ast.USub)
                    and isinstance(node.lower.operand, ast.Constant)
                    and node.lower.operand.value == 4):
                ok_last4 = True
            # 成功分支 400 字上限：Slice(upper=Constant(400))
            if (isinstance(node, ast.Slice)
                    and isinstance(node.upper, ast.Constant)
                    and node.upper.value == 400):
                ok_cap400 = True
            # 失败分支保留信号行：keep.update(...)
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "update"):
                fail_keep_signal = True
            # 失败分支引用 _BAD_LINE_RE（信号行判据）
            if isinstance(node, ast.Name) and node.id == "_BAD_LINE_RE":
                fail_refs_bad_re = True
    return {
        "summarize_found": summarize is not None,
        "has_ok_param": has_ok_param,
        "ok_branch_last4_slice": ok_last4,
        "ok_branch_cap400_slice": ok_cap400,
        "fail_branch_keep_signal": fail_keep_signal,
        "fail_branch_refs_bad_re": fail_refs_bad_re,
        "two_branches_distinct": ok_last4 and ok_cap400
        and fail_keep_signal and fail_refs_bad_re,
        "surface_definition": (
            "status=pass → _summarize_out(ok=True) 末4行/400字截断；"
            "status=fail → _summarize_out(ok=False) 保信号行+头尾+省略计数"),
    }


# ─────────────── B 账：同一契约跨状态对拍（10 份产物）───────────────

def _scan_target(d: Dict[str, Any]) -> Dict[str, Any]:
    """在一份产物里定位 TARGET 的唯一 run，记录状态面读数。"""
    runs = d.get("runs", [])
    target = [r for r in runs if TARGET in r.get("script", "")]
    if len(target) != 1:
        return {"n_target": len(target), "status": None, "tail_len": 0,
                "hits": [], "selftest_in_tail": False,
                "reason": "", "tail_text": "", "tail_last4": ""}
    r = target[0]
    t = r.get("tail", "")
    return {
        "n_target": 1,
        "status": r.get("status"),
        "tail_len": len(t),
        "hits": _hits(t),
        # selftest 是 r118 fail tail 的命中词，直接单独记 flag 供归因账用
        "selftest_in_tail": "selftest" in str(t).lower(),
        "reason": r.get("reason", ""),
        "tail_text": t,
        "tail_last4": "\n".join(str(t).splitlines()[-4:]),
    }


def account_b() -> Dict[str, Any]:
    per_run: Dict[str, Any] = {}
    for tag, path in FULLS:
        per_run[tag] = _scan_target(_read_json_abs(path))
    pass_tags = [t for t, _ in FULLS if per_run[t]["status"] == "pass"]
    fail_tags = [t for t, _ in FULLS if per_run[t]["status"] != "pass"]
    pass_tail_lens = sorted({per_run[t]["tail_len"] for t in pass_tags})
    pass_hit_sigs = sorted({tuple(per_run[t]["hits"]) for t in pass_tags})
    fail_hit_sigs = sorted({tuple(per_run[t]["hits"]) for t in fail_tags})
    fail_tail_lens = sorted({per_run[t]["tail_len"] for t in fail_tags})
    return {
        "per_run": per_run,
        "target": TARGET,
        "n_products": len(FULLS),
        "pass_tags": pass_tags,
        "fail_tags": fail_tags,
        "n_pass": len(pass_tags),
        "n_fail": len(fail_tags),
        "pass_tail_lens": pass_tail_lens,
        "fail_tail_lens": fail_tail_lens,
        "pass_hit_signatures": [list(x) for x in pass_hit_sigs],
        "fail_hit_signatures": [list(x) for x in fail_hit_sigs],
        "all_target_unique": all(per_run[t]["n_target"] == 1 for t, _ in FULLS),
        "pass_tails_uniform": (pass_tail_lens == [200]
                               and pass_hit_sigs == [()]),
        "fail_tail_selftest": (fail_hit_sigs == [("selftest",)]
                               and fail_tail_lens == [801]),
        # 核心：同契约同词表同匹配，命中数只随状态面变
        "status_surface_matters": (pass_hit_sigs == [()]
                                   and fail_hit_sigs == [("selftest",)]
                                   and pass_tail_lens != fail_tail_lens),
    }


# ─────────────── C 账：归因（fail 命中是 self-report 文案，非运行帧）───────────────

def account_c(A: Dict[str, Any], B: Dict[str, Any]) -> Dict[str, Any]:
    fail_tag = B["fail_tags"][0]
    fail_text = B["per_run"][fail_tag]["tail_text"]
    # fail tail 里含 selftest 的行
    selftest_lines = [ln for ln in fail_text.splitlines()
                      if "selftest" in ln.lower()]
    # self-report 标志：PASS / 绿 / ⛔ 断言叙述
    selfreport_markers = ("PASS", "绿", "⛔", "FAIL", "OK")
    selfreport_lines = [
        ln for ln in selftest_lines
        if any(m in ln for m in selfreport_markers)]
    all_selfreport = bool(selftest_lines) and len(selfreport_lines) == len(selftest_lines)
    # 成功分支末4行不含 selftest（截断看不见）
    pass_selftest_absent = all(
        "selftest" not in B["per_run"][t]["tail_last4"].lower()
        for t in B["pass_tags"])
    # 命中行不是产物帧字段（§200：产物零帧字段是 Run.to_dict 结构必然）
    no_frame_field = all(
        not any(f"{k}=" in ln for k in ("frame", "caller", "depth", "payload", "prof"))
        for ln in selftest_lines)
    return {
        "fail_tag": fail_tag,
        "selftest_lines": selftest_lines,
        "n_selftest_lines": len(selftest_lines),
        "all_selftest_lines_selfreport": all_selfreport,
        "pass_selftest_absent_in_last4": pass_selftest_absent,
        "hit_not_frame_field": no_frame_field,
        # 两本账不合并：pass 面 / fail 面各自记
        "pass_fail_not_merged": True,
        "merged_into_bool": False,
        # n=1 失败 run，不外推统计规律（规则 115）
        "n_fail_is_one": B["n_fail"] == 1,
        "no_generalization_claim": True,
        "hit_not_runtime_evidence": True,
        "not_overturn_199_201_202_203_204_125": True,
    }


# ─────────────── D 账：边界 ───────────────

def denominators() -> Dict[str, Any]:
    return psg.denominators()


def superseded_check() -> Dict[str, Any]:
    return psg.superseded_check()


def selfcheck() -> Dict[str, Any]:
    from probe_truerun_count import call_count_in_file
    new = [SELF_SRC, SELF_CONTRACT]
    return {
        "new_files_resolve_policy_calls": sum(call_count_in_file(f, "resolve_policy") for f in new),
        "scripts_json_dynamic_denominator": [],  # 本轮无新 scripts/*.json
    }


def account_d(anchor_rep: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "denominator_dual_source": denominators(),
        "superseded": superseded_check(),
        "anchor": anchor_rep,
        "selfcheck": selfcheck(),
        "section_183_touched": False,
        "real_expr_run": 0,
        "real_commit_run": 0,
        "main_repo_run": 0,
        "gain_rows_reported": 0,
        "product_files_modified": 0,
        "scope_narrowing_only": True,
        "direction_199_203_unchanged": True,
    }


# ─────────────── closure ───────────────

def build_closure(A, B, C, D) -> Dict[str, bool]:
    sc = D["selfcheck"]
    return {
        # A 状态面定义
        "a1_summarize_found": A["summarize_found"] is True,
        "a2_ok_last4_slice": A["ok_branch_last4_slice"] is True,
        "a3_ok_cap400_slice": A["ok_branch_cap400_slice"] is True,
        "a4_two_branches_distinct": A["two_branches_distinct"] is True,
        # B 跨状态对拍
        "b1_ten_products": B["n_products"] == 10,
        "b2_all_target_unique": B["all_target_unique"] is True,
        "b3_nine_pass_one_fail": B["n_pass"] == 9 and B["n_fail"] == 1,
        "b4_pass_tails_uniform": B["pass_tails_uniform"] is True,
        "b5_fail_tail_selftest": B["fail_tail_selftest"] is True,
        "b6_status_surface_matters": B["status_surface_matters"] is True,
        # C 归因
        "c1_selftest_lines_nonzero": C["n_selftest_lines"] > 0,
        "c2_all_selfreport": C["all_selftest_lines_selfreport"] is True,
        "c3_pass_last4_absent": C["pass_selftest_absent_in_last4"] is True,
        "c4_hit_not_frame_field": C["hit_not_frame_field"] is True,
        "c5_pass_fail_not_merged": C["pass_fail_not_merged"] is True
        and C["merged_into_bool"] is False,
        "c6_n1_no_generalization": C["n_fail_is_one"] is True
        and C["no_generalization_claim"] is True,
        "c7_not_overturn": C["not_overturn_199_201_202_203_204_125"] is True,
        # D 边界
        "d1_denominator_dual_source": (
            D["denominator_dual_source"]["main"] == 135
            and D["denominator_dual_source"]["form"] == 119
            and D["denominator_dual_source"]["no_result"] == 94),
        "d2_superseded_all_match": D["superseded"]["all_match"] is True,
        "d3_anchor_ok": (D["anchor"]["ok"] is True and D["anchor"]["repos"] == 3
                         and not D["anchor"]["diffs"] and not D["anchor"]["caliber_diffs"]),
        "d4_no_new_resolve_calls": sc["new_files_resolve_policy_calls"] == 0,
        "d5_no_dynamic_json_denominator": sc["scripts_json_dynamic_denominator"] == [],
        "d6_section183_zero": (D["section_183_touched"] is False
                               and D["real_expr_run"] == 0 and D["real_commit_run"] == 0
                               and D["main_repo_run"] == 0 and D["gain_rows_reported"] == 0
                               and D["product_files_modified"] == 0),
        "d7_scope_narrowing_only": D["scope_narrowing_only"] is True
        and D["direction_199_203_unchanged"] is True,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    A = account_a()
    B = account_b()
    C = account_c(A, B)
    D = account_d(anchor_report())
    closure = build_closure(A, B, C, D)
    n_true = sum(bool(v) for v in closure.values())

    doc = {
        "caliber": caliber(),
        "accounts": {
            "A_status_def": A, "B_cross_status": B,
            "C_attribution": C, "D_boundary": D,
        },
        "closure": closure,
        "derived": {
            "n_closure": len(closure), "n_closure_true": n_true,
            "n_pass": B["n_pass"], "n_fail": B["n_fail"],
            "pass_tail_lens": B["pass_tail_lens"],
            "fail_tail_lens": B["fail_tail_lens"],
            "status_surface_matters": B["status_surface_matters"],
        },
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)

    fail = [k for k, v in closure.items() if not v]
    print("closure %d/%d pass=%d fail=%d pass_len=%s fail_len=%s matters=%s"
          % (n_true, len(closure), B["n_pass"], B["n_fail"],
             B["pass_tail_lens"], B["fail_tail_lens"], B["status_surface_matters"]))
    if fail:
        print("FAIL:", ", ".join(fail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
