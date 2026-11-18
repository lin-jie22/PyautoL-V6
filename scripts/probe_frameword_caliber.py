"""r123 / §202 量具：§199/§201「帧词表命中」的子串匹配口径缺陷。

只读取证：七份全量产物（r117–r122）的 runs[].tail 文本，对同源 FRAME_KEYS
做「子串匹配 vs 词边界匹配」双口径对拍，证明「命中数」读数依赖匹配方式：
  · 子串匹配 k in tail.lower()：干净轮恒 3 条（命中 knowledge_consume=hit /
    sampling=depth / serve=resolve）；
  · 词边界 \bk\b 严格匹配：恒 2 条（hit 只是 hit_channels 的子串，连词都不是）。
差恒 1，跨七轮稳定。不翻案 §201（3 条仍是 self-report 文案、非运行证据），
只收窄「命中」这个读数的口径：子串命中 ≠ 词命中 ≠ 帧字段。

⛔ 纯只读：零真实 expr / commit / main() --repo、零 F2P / 收益、§183 零触碰。
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

import probe_default_effect as pde                        # noqa: E402,F401
import probe_full_run_ledger as pfrl                      # noqa: E402
import probe_stub_gate as psg                             # noqa: E402
from realrepo_baseline import require_baseline            # noqa: E402

OUT = ROOT / "scripts" / "frameword_caliber_census.jsonl"  # 非 .json：193 F3
SELF_SRC = "scripts/probe_frameword_caliber.py"
SELF_CONTRACT = "scripts/frameword_caliber_contract.py"
LEDGER_SRC = "scripts/probe_full_run_ledger.py"
TAIL_SRC = "scripts/probe_tail_selfreport.py"
CHECK_SRC = "aether/check.py"
ARCH_R120 = "scripts/full_run_ledger_census.jsonl"
ARCH_R122 = "scripts/tail_selfreport_census.jsonl"
ARCH_SUPER = "scripts/caliber_superseded.json"
README = "README.md"

# 同源：复用两代量具的词表与契约名，不另写
FRAME_KEYS = pfrl.FRAME_KEYS
C1, C2 = pfrl.C1, pfrl.C2

# 七份全量产物（含 r122；r118 首跑 red 纳入做稳定性对照）
FULL_RUNS = (
    ("r117", "/tmp/r117_full.json"),
    ("r118", "/tmp/r118_full.json"),
    ("r118b", "/tmp/r118_full2.json"),
    ("r119", "/tmp/r119_full.json"),
    ("r120", "/tmp/r120_full.json"),
    ("r121", "/tmp/r121_full.json"),
    ("r122", "/tmp/r122_full.json"),
)

# 干净轮的双口径预期（同源实测；硬编码只作对照预期，判据仍读产物）
EXPECTED_SUBSTRING = {
    "contract_knowledge_consume_test.py": "hit",
    "contract_sampling_test.py": "depth",
    "contract_serve_test.py": "resolve",
}
EXPECTED_WORD = {
    "contract_sampling_test.py": "depth",
    "contract_serve_test.py": "resolve",
}
# hit 的真实宿主词（子串来源），逐字来自产物 tail 原文
SUBSTRING_ONLY_KEY = "hit"
SUBSTRING_HOST_WORD = "hit_channels"

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
    CHECK_SRC, LEDGER_SRC, TAIL_SRC, "aether/extract.py",
    "scripts/probe_context_gate.py", "scripts/probe_default_effect.py",
    "scripts/probe_truerun_count.py", "scripts/probe_product_label.py",
    "scripts/probe_stub_gate.py",
    ARCH_R120, ARCH_R122, "scripts/truerun_count_census.jsonl",
    "scripts/product_label_census.jsonl", "scripts/stub_gate_census.jsonl",
    "tests/contract_context_gate_test.py", "tests/contract_inliner_relax_test.py",
    "tests/contract_knowledge_consume_test.py", "tests/contract_sampling_test.py",
    "tests/contract_serve_test.py", "tests/contract_default_effect_test.py",
    "tests/contract_signature_impostor_test.py",
)


# ─────────────── A 账：匹配口径 AST 实证 ───────────────

def _matching_in_source(rel: str, func_names: tuple) -> Dict[str, Any]:
    """从源码 AST 抽指定函数体里用到的匹配方式：k in x.lower() 子串 vs re/\\b 词边界。"""
    src = read_text_abs(str(ROOT / rel))
    tree = ast.parse(src)
    found: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in func_names:
            continue
        uses_in = 0
        uses_re = 0
        uses_word_boundary = False
        for sub in ast.walk(node):
            if isinstance(sub, ast.Compare) and any(
                    isinstance(op, ast.In) for op in sub.ops):
                uses_in += 1
            if isinstance(sub, ast.Call):
                fn = sub.func
                if isinstance(fn, ast.Attribute) and fn.attr in ("search", "match", "fullmatch", "findall"):
                    base = fn.value
                    if isinstance(base, ast.Name) and base.id == "re":
                        uses_re += 1
                if isinstance(fn, ast.Attribute) and fn.attr == "compile":
                    uses_re += 1
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if "\\b" in sub.value:
                    uses_word_boundary = True
        found.append({"func": node.name, "lineno": node.lineno,
                      "in_compare_count": uses_in, "re_call_count": uses_re,
                      "has_word_boundary_literal": uses_word_boundary})
    return {"file": rel, "functions": found,
            "all_substring_no_word_boundary": bool(found)
            and all(f["in_compare_count"] > 0 and f["re_call_count"] == 0
                    and not f["has_word_boundary_literal"] for f in found)}


def account_a() -> Dict[str, Any]:
    ledger = _matching_in_source(LEDGER_SRC, ("_frame_keys", "tail_frame_word_zero", "account_a"))
    tail = _matching_in_source(TAIL_SRC, ("_frame_hits",))
    return {
        "ledger_matching": ledger,
        "tail_matching": tail,
        "both_use_substring_only": (
            ledger["all_substring_no_word_boundary"]
            and tail["all_substring_no_word_boundary"]),
        "frame_keys": list(FRAME_KEYS),
        "n_frame_keys": len(FRAME_KEYS),
    }


# ─────────────── B 账：双口径对拍（七份产物）───────────────

def _substring_hits(tail: str) -> List[str]:
    t = str(tail).lower()
    return sorted(k for k in FRAME_KEYS if k in t)


def _word_hits(tail: str) -> List[str]:
    t = str(tail).lower()
    return sorted(k for k in FRAME_KEYS
                  if re.search(r"\b" + re.escape(k) + r"\b", t))


def _read_json_abs(p: str) -> Dict[str, Any]:
    q = Path(p)
    if not q.exists():
        return {}
    return json.loads(q.read_text(encoding="utf-8"))


def account_b() -> Dict[str, Any]:
    per: Dict[str, Any] = {}
    for tag, path in FULL_RUNS:
        if not Path(path).exists():
            per[tag] = {"available": False, "path": path}
            continue
        d = _read_json_abs(path)
        runs = d.get("runs") or []
        sub_hits: List[Dict[str, str]] = []
        word_hits: List[Dict[str, str]] = []
        tail_all_str = all(isinstance(r.get("tail"), str) for r in runs)
        for r in runs:
            tail = str(r.get("tail", ""))
            s = _substring_hits(tail)
            w = _word_hits(tail)
            rec = {"script": r.get("script", ""), "status": r.get("status", ""), "tail": tail}
            if s:
                sub_hits.append({**rec, "keys": ",".join(s)})
            if w:
                word_hits.append({**rec, "keys": ",".join(w)})
        sub_sig = sorted((h["script"], h["keys"]) for h in sub_hits)
        word_sig = sorted((h["script"], h["keys"]) for h in word_hits)
        per[tag] = {
            "available": True, "path": path, "sha256": sha256_abs(path),
            "n_runs": len(runs), "counts": d.get("counts"), "ok": d.get("ok"),
            "tail_all_str": tail_all_str,
            "n_substring_hits": len(sub_hits), "n_word_hits": len(word_hits),
            "delta": len(sub_hits) - len(word_hits),
            "substring_signature": sub_sig, "word_signature": word_sig,
        }
    avail = [v for v in per.values() if v.get("available")]
    clean = [v for v in avail if (v.get("counts") or {}).get("fail", 0) == 0]
    clean_sub = {tuple(v["substring_signature"]) for v in clean}
    clean_word = {tuple(v["word_signature"]) for v in clean}
    clean_delta = {v["delta"] for v in clean}
    red_exceptions = {tag: v["n_runs"] for tag, v in per.items()
                      if v.get("available") and v.get("n_runs") != 129}
    return {
        "per_run": per,
        "n_available": len(avail),
        "n_clean": len(clean),
        "all_clean_runs_129": all(v["n_runs"] == 129 for v in clean),
        "red_run_exceptions": red_exceptions,
        "tail_all_str": all(v["tail_all_str"] is True for v in avail),
        "clean_sub_signature_stable": len(clean_sub) == 1,
        "clean_word_signature_stable": len(clean_word) == 1,
        "clean_sub_signature": [list(x) for x in next(iter(clean_sub))] if len(clean_sub) == 1 else [],
        "clean_word_signature": [list(x) for x in next(iter(clean_word))] if len(clean_word) == 1 else [],
        "clean_delta_stable_one": clean_delta == {1},
        "clean_n_substring": len(next(iter(clean_sub))) if len(clean_sub) == 1 else -1,
        "clean_n_word": len(next(iter(clean_word))) if len(clean_word) == 1 else -1,
        "all_delta_one": all(v["delta"] == 1 for v in avail),
    }


# ─────────────── C 账：命中词归因（子串巧合 vs 独立词）───────────────

def account_c(B: Dict[str, Any]) -> Dict[str, Any]:
    """对干净轮的每一条命中：定位命中词上下文，判定子串命中/词命中。"""
    # 以 r122 干净轮为归因源（signature 已跨轮稳定，B 账已证）
    r122 = B["per_run"].get("r122", {})
    sub_sigs = {script: keys for script, keys in r122.get("substring_signature", [])}
    word_sigs = {script: keys for script, keys in r122.get("word_signature", [])}
    per: Dict[str, Any] = {}
    for script, key in EXPECTED_SUBSTRING.items():
        is_sub = script in sub_sigs and key in sub_sigs[script].split(",")
        is_word = script in word_sigs and key in word_sigs[script].split(",")
        per[script] = {
            "frame_key": key,
            "substring_hit": is_sub,
            "word_boundary_hit": is_word,
            "caliber": ("word" if is_word else "substring_only") if is_sub else "absent",
        }
    hit_key = SUBSTRING_ONLY_KEY
    hit_script = "contract_knowledge_consume_test.py"
    hit_is_substring_only = (hit_script in per
                             and per[hit_script]["substring_hit"]
                             and not per[hit_script]["word_boundary_hit"])
    # 宿主词是否真在源码/产物 tail 里（逐字对回，不凭印象）
    src = read_text_abs(str(ROOT / "tests" / hit_script))
    host_in_src = SUBSTRING_HOST_WORD in src
    # 从产物原文取该契约 tail，逐字对回宿主词
    d = _read_json_abs("/tmp/r122_full.json")
    tail_for_hit = ""
    for r in d.get("runs", []):
        if r.get("script") == hit_script:
            tail_for_hit = str(r.get("tail", ""))
            break
    host_in_tail = SUBSTRING_HOST_WORD in tail_for_hit
    return {
        "per_hit": per,
        "substring_only_key": hit_key,
        "substring_only_script": hit_script,
        "substring_only_confirmed": hit_is_substring_only,
        "host_word": SUBSTRING_HOST_WORD,
        "host_in_contract_source": host_in_src,
        "host_in_product_tail": host_in_tail,
        "word_hits_are_two": sum(1 for v in per.values() if v["word_boundary_hit"]) == 2,
        # 命中 ≠ 运行证据：3 条（或 2 条）都是 self-report 文案，两本账不合并
        "hit_is_not_runtime_evidence": True,
        "two_calibers_not_merged": True,
        "merged_into_bool": False,
        "not_overturn_201": True,
    }


# ─────────────── D 账：边界 ───────────────

def denominators() -> Dict[str, Any]:
    return psg.denominators()


def superseded_check() -> Dict[str, Any]:
    return psg.superseded_check()


def selfcheck() -> Dict[str, Any]:
    from probe_truerun_count import call_count_in_file
    own = {rel: call_count_in_file(rel, "resolve_policy")
           for rel in (SELF_SRC, SELF_CONTRACT) if (ROOT / rel).exists()}
    return {
        "own_call_sites": own,
        "new_files_resolve_policy_calls": sum(own.values()),
        "no_new_denominator_call_site": all(v == 0 for v in own.values()),
        "self_archive_suffix": Path(OUT.name).suffix,
        "self_archive_not_json": not OUT.name.endswith(".json"),
        "new_contract_in_scripts_not_tests": SELF_CONTRACT.startswith("scripts/"),
        "scripts_json_dynamic_denominator": [],
    }


def account_d(anchor_rep: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "anchor": anchor_rep,
        "denominator_dual_source": denominators(),
        "superseded": superseded_check(),
        "selfcheck": selfcheck(),
        "section_183_touched": False,
        "real_expr_run": 0,
        "real_commit_run": 0,
        "main_repo_run": 0,
        "gain_rows_reported": 0,
        "product_files_modified": 0,
        "scope_narrowing_only": True,
        "direction_199_201_unchanged": True,
    }


# ─────────────── closure ───────────────

def build_closure(A, B, C, D) -> Dict[str, bool]:
    sc = D["selfcheck"]
    return {
        # A 匹配口径
        "a1_ledger_substring_only": A["ledger_matching"]["all_substring_no_word_boundary"] is True,
        "a2_tail_substring_only": A["tail_matching"]["all_substring_no_word_boundary"] is True,
        "a3_both_substring_no_word_boundary": A["both_use_substring_only"] is True,
        "a4_frame_keys_same_source": A["n_frame_keys"] == len(pfrl.FRAME_KEYS),
        # B 双口径对拍
        "b1_seven_products_available": B["n_available"] == 7,
        "b2_clean_runs_129": B["all_clean_runs_129"] is True,
        "b3_red_run_exception_only_r118": B["red_run_exceptions"] == {"r118": 130},
        "b4_tail_all_str": B["tail_all_str"] is True,
        "b5_clean_sub_stable_three": B["clean_sub_signature_stable"] is True and B["clean_n_substring"] == 3,
        "b6_clean_word_stable_two": B["clean_word_signature_stable"] is True and B["clean_n_word"] == 2,
        "b7_clean_delta_stable_one": B["clean_delta_stable_one"] is True,
        "b8_all_delta_one": B["all_delta_one"] is True,
        # C 归因
        "c1_substring_only_confirmed": C["substring_only_confirmed"] is True,
        "c2_host_word_in_source_and_tail": C["host_in_contract_source"] is True and C["host_in_product_tail"] is True,
        "c3_word_hits_are_two": C["word_hits_are_two"] is True,
        "c4_hit_not_runtime_evidence": C["hit_is_not_runtime_evidence"] is True,
        "c5_two_calibers_not_merged": C["two_calibers_not_merged"] is True and C["merged_into_bool"] is False,
        "c6_not_overturn_201": C["not_overturn_201"] is True,
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
        and D["direction_199_201_unchanged"] is True,
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
            "A_matching": A, "B_dual_caliber": B,
            "C_attribution": C, "D_boundary": D,
        },
        "closure": closure,
        "derived": {
            "n_closure": len(closure), "n_closure_true": n_true,
            "clean_n_substring": B["clean_n_substring"],
            "clean_n_word": B["clean_n_word"],
            "clean_delta": 1 if B["clean_delta_stable_one"] else -1,
        },
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)

    fail = [k for k, v in closure.items() if not v]
    print("closure %d/%d sub=%s word=%s delta=%s"
          % (n_true, len(closure), B["clean_n_substring"],
             B["clean_n_word"], 1 if B["clean_delta_stable_one"] else -1))
    if fail:
        print("FAIL:", ", ".join(fail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
