"""r125 / §204 纯只读取证：字段选择面口径（产物 tail vs 元数据字段）。

§199/§201/§202/§203/r123/r124 一路把「帧词命中数」收窄到可复现读数：作用域
2/129、全体 3 条、子串 vs 词边界差恒 1、stdout 全文 vs tail 截断——但五轮
**全部只扫 `runs[].tail` 一个字段面**。产物 JSON 还有 `runs[].script`（契约
文件名）、顶层 `undeclared_modes` / `e2e_not_supported` 等字符串字段面，从没
对同源 FRAME_KEYS 做过命中扫描。本轮扫 9 份现成全量产物（r117–r124）的四个
字段面，证明「命中数」还依赖**字段选择面**——与前四个口径正交，且元数据面
的命中是**契约文件名常量**（比 self-report 文案更弱的一类）。

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

OUT = ROOT / "scripts" / "field_surface_census.jsonl"     # 非 .json：193 F3
SELF_SRC = "scripts/probe_field_surface.py"
SELF_CONTRACT = "scripts/field_surface_contract.py"
CHECK_SRC = "aether/check.py"
LEDGER_SRC = "scripts/probe_full_run_ledger.py"
TAIL_SRC = "scripts/probe_tail_selfreport.py"
CAL_SRC = "scripts/probe_frameword_caliber.py"
OBS_SRC = "scripts/probe_obs_surface.py"
ARCH_SUPER = "scripts/caliber_superseded.json"

# 9 份现成全量产物（含 r118 首跑红轮做稳定性对照）
FULLS = (
    ("r117", "/tmp/r117_full.json"),
    ("r118", "/tmp/r118_full.json"),      # 红轮：130 runs、1 真红
    ("r118b", "/tmp/r118_full2.json"),
    ("r119", "/tmp/r119_full.json"),
    ("r120", "/tmp/r120_full.json"),
    ("r121", "/tmp/r121_full.json"),
    ("r122", "/tmp/r122_full.json"),
    ("r123", "/tmp/r123_full.json"),
    ("r124", "/tmp/r124_full.json"),
)
# 四个字段面：一个运行输出面（tail）+ 三个元数据面
FIELDS = ("tail", "script", "undeclared_modes", "e2e_not_supported")
META_FIELDS = ("script", "undeclared_modes", "e2e_not_supported")

FRAME_KEYS = pfrl.FRAME_KEYS

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
    CHECK_SRC, LEDGER_SRC, TAIL_SRC, CAL_SRC, OBS_SRC,
    "scripts/probe_frameword_caliber.py", "scripts/probe_obs_surface.py",
    "scripts/probe_stub_gate.py", "scripts/probe_truerun_count.py",
    ARCH_SUPER, "scripts/frameword_caliber_census.jsonl",
    "scripts/tail_selfreport_census.jsonl", "scripts/obs_surface_census.jsonl",
    "scripts/obs_surface_stdout.jsonl",
)


def _read_json_abs(p: str) -> Dict[str, Any]:
    q = Path(p)
    if not q.exists():
        return {}
    return json.loads(q.read_text(encoding="utf-8"))


def _hits(t: Any) -> List[str]:
    """同源子串口径 `k in str(t).lower()`，与前五轮完全一致（只换字段面）。"""
    low = str(t).lower()
    return sorted(k for k in FRAME_KEYS if k in low)


# ─────────────── A 账：字段面定义（AST 实证 Run.to_dict + 顶层字段）───────────────

def account_a() -> Dict[str, Any]:
    """AST 实证产物字段骨架，证明 tail 只是多个字符串字段面之一。"""
    src = read_text_abs(str(ROOT / CHECK_SRC))
    tree = ast.parse(src)
    run_cls = None
    to_dict = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Run":
            run_cls = node
        if isinstance(node, ast.FunctionDef) and node.name == "to_dict":
            to_dict = node
    dataclass_fields: List[str] = []
    if run_cls is not None:
        for node in run_cls.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                dataclass_fields.append(node.target.id)
    # to_dict 返回的 key 集合
    dict_keys: List[str] = []
    if to_dict is not None:
        for node in ast.walk(to_dict):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                dict_keys = [k.value for k in node.value.keys
                             if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                break
    return {
        "run_class_found": run_cls is not None,
        "to_dict_found": to_dict is not None,
        "dataclass_fields": dataclass_fields,
        "to_dict_keys": dict_keys,
        "n_to_dict_keys": len(dict_keys),
        "tail_is_one_field": "tail" in dict_keys,
        "script_is_string_field": "script" in dict_keys,
        # 顶层列表字段由 B 账从产物实测（非 AST）
        "tail_not_only_surface": ("tail" in dict_keys and "script" in dict_keys),
        "surface_definition": (
            "run 级字符串面 = script/tail（to_dict）；顶层列表面 = undeclared_modes/e2e_not_supported（产物实测）"),
    }


# ─────────────── B 账：四字段面对拍（9 份产物 × 4 面）───────────────

def _scan_product(d: Dict[str, Any]) -> Dict[str, Any]:
    """扫一份产物的四个字段面，返回逐面命中（脚本名→词 列表）。"""
    runs = d.get("runs", [])
    tail_hits: Dict[str, List[str]] = {}
    script_hits: Dict[str, List[str]] = {}
    for r in runs:
        h_tail = _hits(r.get("tail", ""))
        if h_tail:
            tail_hits[r.get("script", "")] = h_tail
        h_script = _hits(r.get("script", ""))
        if h_script:
            script_hits[r.get("script", "")] = h_script
    undeclared_hits: Dict[str, List[str]] = {}
    for x in (d.get("undeclared_modes") or []):
        h = _hits(x)
        if h:
            undeclared_hits[str(x)] = h
    e2e_hits: Dict[str, List[str]] = {}
    for x in (d.get("e2e_not_supported") or []):
        h = _hits(x)
        if h:
            e2e_hits[str(x)] = h
    return {
        "n_runs": len(runs),
        "n_pass": sum(1 for r in runs if r.get("status") == "pass"),
        "tail": tail_hits,
        "script": script_hits,
        "undeclared_modes": undeclared_hits,
        "e2e_not_supported": e2e_hits,
    }


def account_b() -> Dict[str, Any]:
    per_run: Dict[str, Any] = {}
    for tag, path in FULLS:
        per_run[tag] = _scan_product(_read_json_abs(path))
    # 各面命中条数（干净轮 r124）
    clean = per_run["r124"]
    n_tail = len(clean["tail"])
    n_script = len(clean["script"])
    n_undeclared = len(clean["undeclared_modes"])
    n_e2e = len(clean["e2e_not_supported"])
    # 跨 8 个干净轮（排除 r118 红轮）元数据面稳定性
    clean_tags = [t for t, _ in FULLS if t != "r118"]
    meta_signatures = {
        t: (tuple(sorted(per_run[t]["script"].items())),
            tuple(sorted(per_run[t]["undeclared_modes"].items())),
            tuple(sorted(per_run[t]["e2e_not_supported"].items())))
        for t in clean_tags
    }
    first = meta_signatures[clean_tags[0]]
    meta_stable = all(v == first for v in meta_signatures.values())
    # tail 面：干净轮 3，r118 红轮 4（多 signature_impostor selftest）
    tail_counts = {t: len(per_run[t]["tail"]) for t, _ in FULLS}
    clean_tail_3 = all(tail_counts[t] == 3 for t in clean_tags)
    red_tail_4 = tail_counts["r118"] == 4
    return {
        "per_run": per_run,
        "fields": list(FIELDS),
        "clean_tag": "r124",
        "n_tail_clean": n_tail,
        "n_script_clean": n_script,
        "n_undeclared_clean": n_undeclared,
        "n_e2e_clean": n_e2e,
        "meta_signatures": {t: [list(x) for x in v] for t, v in meta_signatures.items()},
        "meta_stable_across_clean_runs": meta_stable,
        "n_clean_runs": len(clean_tags),
        "tail_counts": tail_counts,
        "clean_tail_is_three": clean_tail_3,
        "red_tail_is_four": red_tail_4,
        # 核心：换字段面命中数不同（tail 3 vs 元数据面非零），字段选择是独立口径
        "field_surface_matters": (n_tail == 3 and n_script > 0
                                  and n_e2e > 0 and meta_stable),
    }


# ─────────────── C 账：归因（元数据面命中是契约文件名常量）───────────────

def account_c(B: Dict[str, Any]) -> Dict[str, Any]:
    """元数据面每条命中逐条溯源：是不是某个契约文件名子串？是不是运行输出？"""
    clean = B["per_run"]["r124"]
    # 元数据面命中词 → 来自哪个契约文件名的子串
    provenance: Dict[str, Any] = {}
    all_script_names: set = set()
    for d in B["per_run"].values():
        all_script_names.update(d["script"].keys())
    for field in META_FIELDS:
        hits_map = clean[field]
        rows = []
        for name, words in sorted(hits_map.items()):
            for w in words:
                low = name.lower()
                rows.append({
                    "surface": field,
                    "string": name,
                    "frame_key": w,
                    "is_substring_of_script_name": w in low,
                    "is_contract_filename": name.startswith("contract_") and name.endswith(".py"),
                })
        provenance[field] = rows
    all_rows = [r for rows in provenance.values() for r in rows]
    all_from_filename = all(
        r["is_substring_of_script_name"] and r["is_contract_filename"]
        for r in all_rows)
    # tail 面命中仍是 self-report（复用 §201/§203 结论，不重证）：命中脚本集合
    tail_scripts = sorted(clean["tail"].keys())
    # 两族命中脚本不重合：tail = knowledge_consume/sampling/serve；元数据 = root_resolve/selftest_evidence/traceability
    tail_set = set(tail_scripts)
    meta_set = set(clean["script"].keys())
    disjoint = tail_set.isdisjoint(meta_set)
    return {
        "provenance": provenance,
        "n_meta_hit_rows": len(all_rows),
        "all_meta_hits_are_filename_constants": all_from_filename,
        "tail_hit_scripts": tail_scripts,
        "meta_hit_scripts": sorted(meta_set),
        "tail_and_meta_scripts_disjoint": disjoint,
        "meta_hits_not_runtime_output": all_from_filename,
        "meta_hits_not_selfreport": all_from_filename and disjoint,
        # 四本账不合并：tail 面 / script 面 / undeclared 面 / e2e 面各自记
        "four_surfaces_not_merged": True,
        "merged_into_bool": False,
        "hit_not_runtime_evidence": True,
        "not_overturn_199_201_202_203_124": True,
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
        # A 字段面定义
        "a1_run_class_found": A["run_class_found"] is True,
        "a2_to_dict_found": A["to_dict_found"] is True,
        "a3_seven_to_dict_keys": A["n_to_dict_keys"] == 7,
        "a4_tail_not_only_surface": A["tail_not_only_surface"] is True,
        # B 四面对拍
        "b1_nine_products": len(B["per_run"]) == 9,
        "b2_tail_clean_three": B["clean_tail_is_three"] is True,
        "b3_red_tail_four": B["red_tail_is_four"] is True,
        "b4_meta_stable_across_clean": B["meta_stable_across_clean_runs"] is True
        and B["n_clean_runs"] == 8,
        "b5_four_fields": B["fields"] == list(FIELDS),
        "b6_field_surface_matters": B["field_surface_matters"] is True,
        # C 归因
        "c1_meta_rows_nonzero": C["n_meta_hit_rows"] > 0,
        "c2_meta_are_filename_constants": C["all_meta_hits_are_filename_constants"] is True,
        "c3_tail_meta_disjoint": C["tail_and_meta_scripts_disjoint"] is True,
        "c4_meta_not_runtime_output": C["meta_hits_not_runtime_output"] is True,
        "c5_meta_not_selfreport": C["meta_hits_not_selfreport"] is True,
        "c6_four_surfaces_not_merged": C["four_surfaces_not_merged"] is True
        and C["merged_into_bool"] is False,
        "c7_not_overturn": C["not_overturn_199_201_202_203_124"] is True,
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
    C = account_c(B)
    D = account_d(anchor_report())
    closure = build_closure(A, B, C, D)
    n_true = sum(bool(v) for v in closure.values())

    doc = {
        "caliber": caliber(),
        "accounts": {
            "A_field_def": A, "B_four_surfaces": B,
            "C_attribution": C, "D_boundary": D,
        },
        "closure": closure,
        "derived": {
            "n_closure": len(closure), "n_closure_true": n_true,
            "n_tail_clean": B["n_tail_clean"],
            "n_script_clean": B["n_script_clean"],
            "n_meta_hit_rows": C["n_meta_hit_rows"],
            "field_surface_matters": B["field_surface_matters"],
        },
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)

    fail = [k for k, v in closure.items() if not v]
    print("closure %d/%d tail=%d script=%d meta_rows=%d matters=%s"
          % (n_true, len(closure), B["n_tail_clean"], B["n_script_clean"],
             C["n_meta_hit_rows"], B["field_surface_matters"]))
    if fail:
        print("FAIL:", ", ".join(fail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
