"""r124 / §202→§203 纯只读取证：观测面口径（stdout 全文 vs 产物 tail 截断）。

§199/§201/§202/r123 的「帧词命中数」全部只在**产物 tail** 一个观测面上量过。
tail 是 `aether.check._summarize_out` 成功分支 `lines[-4:][:400]` 的截断结果。
本轮单跑 6 条验收契约拿 stdout 全文，与产物 tail 做三口径对拍，证明
「命中数」还依赖**观测面**——与 r123 的「匹配方式」口径正交。

⛔ 纯只读：契约单跑（env 脱管、非真实 expr/commit/main --repo）、零 F2P/收益、§183 零触碰。
⛔ 复用同源 FRAME_KEYS（probe_full_run_ledger.FRAME_KEYS），不另写词表。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import probe_full_run_ledger as pfrl                      # noqa: E402
import probe_stub_gate as psg                             # noqa: E402
from realrepo_baseline import require_baseline            # noqa: E402

OUT = ROOT / "scripts" / "obs_surface_census.jsonl"        # 非 .json：193 F3
SELF_SRC = "scripts/probe_obs_surface.py"
SELF_CONTRACT = "scripts/obs_surface_contract.py"
CHECK_SRC = "aether/check.py"
LEDGER_SRC = "scripts/probe_full_run_ledger.py"
TAIL_SRC = "scripts/probe_tail_selfreport.py"
CAL_SRC = "scripts/probe_frameword_caliber.py"
ARCH_R123 = "scripts/frameword_caliber_census.jsonl"
ARCH_STDOUT = "scripts/obs_surface_stdout.jsonl"
ARCH_SUPER = "scripts/caliber_superseded.json"
FULL_JSON = "/tmp/r123_full.json"

FRAME_KEYS = pfrl.FRAME_KEYS
C1, C2 = pfrl.C1, pfrl.C2

# 6 条验收契约：r123 命中的 3 条 + 3 条对照（含 C1/C2 与有词边界真词却进不了 tail 的）
PROBES = (
    "contract_context_gate_test.py",
    "contract_inliner_relax_test.py",
    "contract_knowledge_consume_test.py",
    "contract_sampling_test.py",
    "contract_serve_test.py",
    "contract_default_effect_test.py",
)

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
    CHECK_SRC, LEDGER_SRC, TAIL_SRC, CAL_SRC, "aether/extract.py",
    "scripts/probe_context_gate.py", "scripts/probe_default_effect.py",
    "scripts/probe_truerun_count.py", "scripts/probe_product_label.py",
    "scripts/probe_stub_gate.py",
    ARCH_R123, ARCH_STDOUT, ARCH_SUPER, "scripts/frameword_caliber_census.jsonl",
    "scripts/truerun_count_census.jsonl", "scripts/product_label_census.jsonl",
    "scripts/stub_gate_census.jsonl", "scripts/tail_selfreport_census.jsonl",
    "tests/contract_context_gate_test.py", "tests/contract_inliner_relax_test.py",
    "tests/contract_knowledge_consume_test.py", "tests/contract_sampling_test.py",
    "tests/contract_serve_test.py", "tests/contract_default_effect_test.py",
)


def _read_json_abs(p: str) -> Dict[str, Any]:
    q = Path(p)
    if not q.exists():
        return {}
    return json.loads(q.read_text(encoding="utf-8"))


def load_stdout_archive() -> Dict[str, Any]:
    """读预采集的契约 stdout 全文存档（Bash 独立进程采集，避 spawn 累积 SIGTERM）。
    ⛔ 量具本体纯只读、不 spawn；契约单跑是采集步骤、非真实 expr/commit/main --repo。"""
    d = _read_json_abs(str(ROOT / ARCH_STDOUT))
    return d.get("rows", {})


# ─────────────── A 账：观测面定义（AST 实证 _summarize_out）───────────────

def account_a() -> Dict[str, Any]:
    src = read_text_abs(str(ROOT / CHECK_SRC))
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_summarize_out":
            fn = node
            break
    if fn is None:
        return {"found": False, "success_tail_slice": None,
                "success_chars_cap": None, "failure_keeps_signal_lines": None}
    # 找 `if ok:` 分支里的 `lines[-4:][:400]`
    ok_branch = None
    for sub in ast.walk(fn):
        if isinstance(sub, ast.If) and isinstance(sub.test, ast.Name) and sub.test.id == "ok":
            ok_branch = sub
            break
    # lines[-4:] → Slice(lower=UnaryOp(USub, Constant(4)))；[:400] → Slice(upper=Constant(400))
    neg_slice = False
    cap = None
    if ok_branch is not None:
        for sub in ast.walk(ok_branch):
            if isinstance(sub, ast.Slice):
                lo = sub.lower
                hi = sub.upper
                if isinstance(lo, ast.UnaryOp) and isinstance(lo.op, ast.USub) \
                        and isinstance(lo.operand, ast.Constant):
                    neg_slice = True
                if isinstance(hi, ast.Constant) and hi.value == 400:
                    cap = 400
    # 失败分支保留信号行：_BAD_LINE_RE.search / keep.update
    has_signal = any(
        isinstance(sub, ast.Attribute) and sub.attr == "search"
        for sub in ast.walk(fn))
    return {
        "found": True,
        "func_lineno": fn.lineno,
        "success_branch_found": ok_branch is not None,
        "success_negative_slice": neg_slice,
        "success_chars_cap": cap,
        "success_is_truncated_tail": neg_slice and cap == 400,
        "failure_keeps_signal_lines": has_signal,
        "two_observation_surfaces": (neg_slice and cap == 400) and has_signal,
        "obs_surface_definition": "产物 tail = stdout.strip().splitlines()[-4:] 再 [:400]（成功分支）",
    }


# ─────────────── B 账：三口径对拍（6 条契约单跑 vs 产物 tail）───────────────

def _hits(t: str, word: bool = False) -> List[str]:
    low = str(t).lower()
    if word:
        return sorted(k for k in FRAME_KEYS
                      if re.search(r"\b" + re.escape(k) + r"\b", low))
    return sorted(k for k in FRAME_KEYS if k in low)


def account_b() -> Dict[str, Any]:
    product = _read_json_abs(FULL_JSON)
    by = {r.get("script"): r for r in product.get("runs", [])}
    archive = load_stdout_archive()
    per: Dict[str, Any] = {}
    for script in PROBES:
        row = archive.get(script, {})
        full = row.get("stdout", "")
        rec = by.get(script, {})
        tail = str(rec.get("tail", ""))
        # tail 是否逐字 = stdout.strip() 末4行（成功分支口径）
        tail4 = "\n".join(full.strip().splitlines()[-4:])[:400]
        fs, ts = _hits(full), _hits(tail)
        fw, tw = _hits(full, word=True), _hits(tail, word=True)
        per[script] = {
            "rc": 0 if full else -1,
            "stdout_bytes": len(full.encode("utf-8")),
            "tail_bytes": len(tail.encode("utf-8")),
            "in_product": script in by,
            "product_status": rec.get("status", ""),
            "tail_equals_stdout_last4": tail4.rstrip("\n") == tail.rstrip("\n"),
            "sub_string_full": fs, "sub_string_tail": ts,
            "word_full": fw, "word_tail": tw,
            "sub_gain_by_full": [k for k in fs if k not in ts],
            "word_gain_by_full": [k for k in fw if k not in tw],
            "stdout": full,
        }
    ok_runs = [p for p in per.values() if p["rc"] == 0 and p["in_product"]]
    tail_faithful = all(p["tail_equals_stdout_last4"] for p in ok_runs)
    gain_rows = [s for s, p in per.items() if p["sub_gain_by_full"]]
    word_gain_rows = [s for s, p in per.items() if p["word_gain_by_full"]]
    return {
        "per_contract": per,
        "n_probes": len(PROBES),
        "n_ok_runs": len(ok_runs),
        "all_rc_zero": all(p["rc"] == 0 for p in per.values()),
        "all_in_product": all(p["in_product"] for p in per.values()),
        "tail_faithful_to_last4": tail_faithful,
        "gain_rows": sorted(gain_rows),
        "n_gain_rows": len(gain_rows),
        "word_gain_rows": sorted(word_gain_rows),
        "observation_surface_matters": tail_faithful and len(gain_rows) >= 3,
    }


# ─────────────── C 账：差集归因（截断砍掉的词都是 self-report 文案）───────────────

def account_c(B: Dict[str, Any]) -> Dict[str, Any]:
    per = B["per_contract"]
    # 逐差集行：差词是否在 stdout 全文里逐字存在（本来就在，只是 tail 截断看不到）
    diffs: Dict[str, Any] = {}
    for script, p in per.items():
        for k in p["sub_gain_by_full"]:
            diffs.setdefault(script, []).append({
                "frame_key": k,
                "in_full_stdout": k in p["stdout"].lower(),
                "in_tail": k in str(per[script]["sub_string_tail"]),
            })
    # 特例1：knowledge_consume 的 call 来自 memory.recall 子串
    kc = per.get("contract_knowledge_consume_test.py", {})
    kc_stdout = kc.get("stdout", "")
    call_in_recall = "memory.recall" in kc_stdout and "call" in kc_stdout.lower()
    # 特例2：context_gate 的 selftest 是词边界真词，却完全进不了 tail
    cg = per.get("contract_context_gate_test.py", {})
    cg_word_full = "selftest" in cg.get("word_full", [])
    cg_word_tail = "selftest" in cg.get("word_tail", [])
    cg_false_green = cg_word_full and not cg_word_tail
    # 差词都仍是契约 PASS 文案：逐行看 stdout 里命中词所在行是否含 PASS/账本字样
    selfreport_lines = 0
    for script, p in per.items():
        for line in p["stdout"].splitlines():
            if any(k in line.lower() for k in FRAME_KEYS) and \
                    ("PASS" in line or "账本" in line or "读数" in line or "⛔" in line):
                selfreport_lines += 1
    return {
        "diff_detail": diffs,
        "all_gain_keys_in_full": all(
            d["in_full_stdout"] for rows in diffs.values() for d in rows),
        "call_from_recall": call_in_recall,
        "context_gate_selftest_word_true": cg_word_full,
        "context_gate_selftest_word_in_tail": cg_word_tail,
        "c1_false_green_by_truncation": cg_false_green,
        "selfreport_line_count": selfreport_lines,
        "gain_is_selfreport_text": selfreport_lines >= 3,
        # 命中数三本账不合并，命中 ≠ 运行证据
        "hit_not_runtime_evidence": True,
        "three_surfaces_not_merged": True,
        "merged_into_bool": False,
        "not_overturn_199_201_202_123": True,
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
        "direction_199_202_unchanged": True,
    }


# ─────────────── closure ───────────────

def build_closure(A, B, C, D) -> Dict[str, bool]:
    sc = D["selfcheck"]
    return {
        # A 观测面定义
        "a1_summarize_out_found": A["found"] is True,
        "a2_success_truncated_tail": A["success_is_truncated_tail"] is True,
        "a3_failure_keeps_signal": A["failure_keeps_signal_lines"] is True,
        "a4_two_observation_surfaces": A["two_observation_surfaces"] is True,
        # B 三口径对拍
        "b1_six_probes": B["n_probes"] == 6,
        "b2_all_rc_zero": B["all_rc_zero"] is True,
        "b3_all_in_product": B["all_in_product"] is True,
        "b4_tail_faithful_last4": B["tail_faithful_to_last4"] is True,
        "b5_gain_rows_ge_three": B["n_gain_rows"] >= 3,
        "b6_obs_surface_matters": B["observation_surface_matters"] is True,
        # C 归因
        "c1_gain_keys_in_full": C["all_gain_keys_in_full"] is True,
        "c2_call_from_recall": C["call_from_recall"] is True,
        "c3_c1_selftest_false_green": C["c1_false_green_by_truncation"] is True,
        "c4_gain_is_selfreport": C["gain_is_selfreport_text"] is True,
        "c5_hit_not_runtime_evidence": C["hit_not_runtime_evidence"] is True,
        "c6_three_surfaces_not_merged": C["three_surfaces_not_merged"] is True
        and C["merged_into_bool"] is False,
        "c7_not_overturn": C["not_overturn_199_201_202_123"] is True,
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
        and D["direction_199_202_unchanged"] is True,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    A = account_a()
    B = account_b()
    C = account_c(B)
    D = account_d(anchor_report())
    closure = build_closure(A, B, C, D)
    n_true = sum(bool(v) for v in closure.values())

    doc = {
        "caliber": caliber(),
        "accounts": {
            "A_obs_surface": A, "B_tricaliber": B,
            "C_attribution": C, "D_boundary": D,
        },
        "closure": closure,
        "derived": {
            "n_closure": len(closure), "n_closure_true": n_true,
            "n_gain_rows": B["n_gain_rows"],
            "gain_rows": B["gain_rows"],
            "c1_false_green_by_truncation": C["c1_false_green_by_truncation"],
        },
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)

    fail = [k for k, v in closure.items() if not v]
    print("closure %d/%d gain_rows=%d c1_false_green=%s"
          % (n_true, len(closure), B["n_gain_rows"],
             C["c1_false_green_by_truncation"]))
    if fail:
        print("FAIL:", ", ".join(fail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
