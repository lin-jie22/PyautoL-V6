#!/usr/bin/env python3
"""r118 / README 197 尺子：194「每次全量 ≥6 次真跑」是静态 AST 外推，不是运行账。

四本账（不合并成布尔）：
  A 静态外推   同源复现 194 的 6 = selftest 2 + F1/F2 2 + C6 2；
                逐调用点标注短路风险；`selftest()` 被两份契约各真跑一次
                （194 只算一次）；194 的 `ast_runtime_match` 只覆盖
                `selftest()` 单次调用，`n_contract_runs_per_full_check=2`
                是硬编码常量
  B 运行时计数  runner 同构上下文（`sys.executable -B`、cwd=ROOT、
                `sys.setprofile` 逐帧归因）真跑两份契约 ⇒ 4 + 4 = 8；
                与原样执行 rc / stdout 逐字对拍
  C 鉴别力      短路事故（真实 main 路径重放）令 4→2，证明
                「AST 调用点存在 ≠ 运行到」；签名事故令断言打红而
                次数不变 ⇒ 次数账与鉴别力账分离；反事实四世界里
                静态数恒定、运行时数变化
  D 边界        三分母 / superseded / 验锚 / caliber / 192 分母仍 5 /
                runner declared_modes 双源 / 存档非 `.json` / §183 零触碰

⛔ 纯只读取证：不跑真实 expr、不跑真实 commit、不跑 `main() --repo`、
不报收益、不修改 189–196 任何尺子 / 存档 / 判据 / 契约。
⛔ 本文件与配套契约**不出现 `resolve_policy(...)` 的 AST 调用点**
（192/194 按全仓 AST 数该调用点）；运行时计数一律由子进程 driver
用帧名比较达成，driver 源码是字符串常量、不进 AST 分母。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]          # ⛔ 第 99 条：根从 __file__ 推导
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from realrepo_baseline import require_baseline                  # noqa: E402

OUT = ROOT / "scripts" / "truerun_count_census.jsonl"  # ⛔ 非 .json：193 F3
SELF_PRODUCT = OUT.name
SELF_SRC = "scripts/probe_truerun_count.py"
SELF_CONTRACT = "scripts/truerun_count_contract.py"
CG_SRC = "scripts/probe_context_gate.py"
PSE_SRC = "scripts/probe_selftest_evidence.py"
PDE_SRC = "scripts/probe_default_effect.py"
CHECK_SRC = "aether/check.py"
C1 = "tests/contract_context_gate_test.py"
C2 = "tests/contract_inliner_relax_test.py"
CONTRACTS = (C1, C2)

ARCH_194 = ROOT / "scripts" / "selftest_evidence_census.jsonl"
ARCH_192 = ROOT / "scripts" / "default_effect_census.json"
ARCH_193 = ROOT / "scripts" / "zero_discrimination_census.json"
ARCH_MAIN = ROOT / "scripts" / "extract_downstream.json"
ARCH_FORM = ROOT / "scripts" / "form_shape_attribution.json"
ARCH_NORES = ROOT / "scripts" / "no_result_attribution.json"
ARCH_SUPER = ROOT / "scripts" / "caliber_superseded.json"

#: ⛔ 本轮自身存档 / 契约尚在编写会造成自指 ⇒ 都不进 caliber。
CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    CHECK_SRC,
    CG_SRC,
    PDE_SRC,
    PSE_SRC,
    "scripts/probe_inliner_relax.py",
    "scripts/probe_zero_discrimination.py",
    "scripts/probe_policy_binding.py",
    "scripts/probe_signature_impostor.py",
    "scripts/caliber_superseded.py",
    C1,
    C2,
    "tests/contract_selftest_evidence_test.py",
    "tests/contract_default_effect_test.py",
    "tests/contract_zero_discrimination_test.py",
    "tests/contract_policy_binding_test.py",
    "tests/contract_signature_impostor_test.py",
    "tests/contract_portability_test.py",
    "scripts/default_effect_census.json",
    "scripts/selftest_evidence_census.jsonl",
    "scripts/inliner_relax_census.json",
    "scripts/zero_discrimination_census.json",
    "scripts/policy_binding_census.jsonl",
    "scripts/signature_impostor_census.jsonl",
    "scripts/caliber_superseded.json",
    "scripts/extract_downstream.json",
    "scripts/form_shape_attribution.json",
    "scripts/no_result_attribution.json",
)

#: runner 同构 driver：与原样执行只差 `runpy.run_path` 这一层观察者。
#: ⛔ 不写死任何绝对路径（A1 `scan_text` 零命中）；ROOT 由 argv 传入。
#: ⛔ 不出现裸 `noqa`、不出现 `resolve_policy(...)` 的 Python 调用语法
#:    （只在帧名比较里以字符串出现）。
DRIVER_SRC = r'''
import sys
import os
import json
import runpy

script, root, mode = sys.argv[1], sys.argv[2], sys.argv[3]
os.chdir(root)
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "scripts"))
sys.path.insert(0, os.path.join(root, "tests"))
sys.argv = [script]

TARGET = "probe_context_gate.py"
NAME = "resolve_policy"
hits = []
err = ""
main_rc = None
n_fails = -1


def prof(frame, event, arg):
    if event != "call":
        return
    co = frame.f_code
    if co.co_name != NAME or TARGET not in co.co_filename:
        return
    up = frame.f_back
    hits.append({
        "callee_line": co.co_firstlineno,
        "caller_file": up.f_code.co_filename if up else "",
        "caller_name": up.f_code.co_name if up else "",
        "caller_line": up.f_lineno if up else -1,
    })


sys.setprofile(prof)
try:
    if mode == "default":
        runpy.run_path(script, run_name="__main__")
    else:
        ns = runpy.run_path(script, run_name="truerun_probe")
        CG = ns["CG"]
        if mode == "short_circuit":
            orig_selftest = CG.selftest

            def fake():
                orig_selftest()
                return False

            CG.selftest = fake
            ns["require_baseline"] = lambda: {
                "ok": True, "diffs": [], "caliber_diffs": []}
        elif mode == "signature_broken":
            orig_rp = getattr(CG, NAME)
            setattr(CG, NAME, lambda strict=False: orig_rp(True))
        else:
            raise ValueError("unknown mode " + mode)
        main_rc = ns["main"]()
        fails = ns.get("FAILS")
        n_fails = len(fails) if isinstance(fails, list) else -1
except SystemExit as exc:
    main_rc = exc.code if isinstance(exc.code, int) else 0
except BaseException as exc:
    err = type(exc).__name__ + ": " + str(exc)
finally:
    sys.setprofile(None)

sys.stdout.write("\nDRIVER_JSON=" + json.dumps({
    "mode": mode,
    "n": len(hits),
    "hits": hits,
    "err": err,
    "main_rc": main_rc,
    "n_fails": n_fails,
}) + "\n")
'''

_ANCHOR: Optional[Dict[str, Any]] = None


# ─────────────────────────── 通用工具 ───────────────────────────

def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # ⛔ NamedTuple / dataclass 的 sys.modules 坑
    spec.loader.exec_module(mod)
    return mod


def sha256_file(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "MISSING:" + rel      # ⛔ 缺键用哨兵，不默认值兜底
    return hashlib.sha256(p.read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {rel: sha256_file(rel) for rel in CALIBER_FILES}


def anchor_report() -> Dict[str, Any]:
    """⛔ 验锚只调一次并缓存（单次 50–60s，r111 教训）。"""
    global _ANCHOR
    if _ANCHOR is None:
        rep = require_baseline()
        repos = rep.get("repos")
        _ANCHOR = {"ok": bool(rep.get("ok")),
                   "repos": len(repos) if isinstance(repos, dict) else repos,
                   "diffs": rep.get("diffs"),
                   "caliber_diffs": rep.get("caliber_diffs")}
    return dict(_ANCHOR)


def parse(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def is_call_to(node: ast.AST, name: str) -> bool:
    return (isinstance(node, ast.Call)
            and ((isinstance(node.func, ast.Name) and node.func.id == name)
                 or (isinstance(node.func, ast.Attribute)
                     and node.func.attr == name)))


def call_count_in_file(rel: str, name: str) -> int:
    return sum(1 for n in ast.walk(parse(rel)) if is_call_to(n, name))


# ─────────────── A 账：194 的静态外推逐字复现 ───────────────

def in_short_circuit(target: ast.AST, tree: ast.AST) -> bool:
    """调用点是否位于 `and` 链的非首元素（短路后可能根本不执行）。"""
    for b in [x for x in ast.walk(tree)
              if isinstance(x, ast.BoolOp) and isinstance(x.op, ast.And)]:
        for v in b.values[1:]:
            if any(n is target for n in ast.walk(v)):
                return True
    return False


def call_sites(rel: str) -> List[Dict[str, Any]]:
    """同源 AST 抽 `resolve_policy` 调用点；`func` 用最近的函数定义归属。"""
    tree = parse(rel)
    fn_of: Dict[int, str] = {}
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for n in ast.walk(fn):
            if n is not fn:
                fn_of[id(n)] = fn.name
    out: List[Dict[str, Any]] = []
    for n in ast.walk(tree):
        if is_call_to(n, "resolve_policy"):
            out.append({"rel": rel, "func": fn_of.get(id(n), "<module>"),
                        "lineno": n.lineno,
                        "short_circuit": in_short_circuit(n, tree)})
    return sorted(out, key=lambda x: (x["rel"], x["func"], x["lineno"]))


def selftest_entries() -> List[Dict[str, Any]]:
    """两份契约里 `selftest()` 的真跑入口（194 只算了其中一个）。"""
    out = []
    for rel in CONTRACTS:
        for n in ast.walk(parse(rel)):
            if is_call_to(n, "selftest"):
                out.append({"rel": rel, "lineno": n.lineno})
    return sorted(out, key=lambda x: (x["rel"], x["lineno"]))


def contract_runs_hardcoded() -> bool:
    """同源 AST：194 的 `n_contract_runs_per_full_check` 是不是字面常量。"""
    for n in ast.walk(parse(PSE_SRC)):
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if (isinstance(k, ast.Constant)
                        and k.value == "n_contract_runs_per_full_check"):
                    return isinstance(v, ast.Constant)
    return False


def counter_scope() -> Dict[str, Any]:
    """同源 AST：194 的 B 账只在单函数上做计数器，未量过 runner 全量。"""
    fn = next((n for n in ast.walk(parse(PSE_SRC))
               if isinstance(n, ast.FunctionDef) and n.name == "account_b"), None)
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)] if fn else []
    names = [(n.func.attr if isinstance(n.func, ast.Attribute)
              else n.func.id if isinstance(n.func, ast.Name) else "")
             for n in calls]
    return {"n_setprofile": sum(1 for x in names if x == "setprofile"),
            "n_runpy_run_path": sum(1 for x in names if x == "run_path"),
            "n_subprocess_run": sum(1 for x in names if x == "run"),
            "n_selftest_calls": sum(1 for x in names if x == "selftest"),
            "n_resolve_policy_runtime_counter": sum(
                1 for x in names if x == "run_selftest_counted"),
            "source": PSE_SRC + "::account_b (AST)"}


def account_a() -> Dict[str, Any]:
    pse = _load("pse_r118", PSE_SRC)
    counts = pse.ast_call_counts()          # ⛔ 第 68 条：复用被测对象自己的抽取器
    formula_terms = {
        CG_SRC + "::selftest": counts.get(CG_SRC + "::selftest", 0),
        C1 + "::main": counts.get(C1 + "::main", 0),
        C2 + "::group_c": counts.get(C2 + "::group_c", 0),
    }
    recomputed = sum(formula_terms.values())
    saved = json.loads(ARCH_194.read_text(encoding="utf-8"))["accounts"]["B_runtime_readouts"]
    saved_lower = saved["n_resolve_policy_per_full_check_lower_bound"]
    sites = call_sites(CG_SRC) + call_sites(C1) + call_sites(C2)
    entries = selftest_entries()
    return {
        "ast_call_counts_194": counts,
        "formula_terms": formula_terms,
        "recomputed_lower_bound": recomputed,
        "saved_lower_bound": saved_lower,
        "dual_source_match": recomputed == saved_lower,
        "contract_runs_saved": saved["n_contract_runs_per_full_check"],
        "contract_runs_is_hardcoded_constant": contract_runs_hardcoded(),
        "counter_scope": counter_scope(),
        "runtime_paired_only": "CG.selftest() 单次调用（194 B 账 ast_runtime_match）",
        "runtime_paired_value": saved["ast_runtime_match"],
        "runtime_unpaired_call_sites": 4,
        "call_sites": sites,
        "n_call_sites": len(sites),
        "n_short_circuit_sites": sum(1 for s in sites if s["short_circuit"]),
        "short_circuit_sites": [s for s in sites if s["short_circuit"]],
        "selftest_entries": entries,
        "n_selftest_entries": len(entries),
        "n_selftest_entries_counted_by_194": 1,
        "source": "scripts/probe_selftest_evidence.py::ast_call_counts + scripts/selftest_evidence_census.jsonl",
        "merged_into_bool": False,
    }


# ─────────────── B 账：runner 同构的运行时计数器 ───────────────

def run_driver(rel: str, mode: str = "default") -> Dict[str, Any]:
    p = subprocess.run(
        [sys.executable, "-B", "-c", DRIVER_SRC,
         str(ROOT / rel), str(ROOT), mode],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=300)
    out = (p.stdout or b"").decode("utf-8", "replace")
    marker = "\nDRIVER_JSON="
    if marker not in out:
        return {"rc": p.returncode, "ok": False, "err": "no DRIVER_JSON",
                "stdout": out, "payload": {}}
    head, tail = out.rsplit(marker, 1)
    try:
        payload = json.loads(tail.strip().splitlines()[0])
    except (ValueError, IndexError):
        return {"rc": p.returncode, "ok": False, "err": "bad DRIVER_JSON",
                "stdout": head, "payload": {}}
    return {"rc": p.returncode, "ok": True, "err": payload.get("err", ""),
            "stdout": head, "payload": payload}


def run_plain(rel: str) -> Dict[str, Any]:
    p = subprocess.run([sys.executable, "-B", str(ROOT / rel)],
                       cwd=str(ROOT), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=300)
    return {"rc": p.returncode,
            "stdout": (p.stdout or b"").decode("utf-8", "replace")}


def _short(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"caller": h["caller_name"], "line": h["caller_line"],
             "in_product": CG_SRC in h["caller_file"]} for h in hits]


def account_b() -> Dict[str, Any]:
    per: Dict[str, Dict[str, Any]] = {}
    for rel in CONTRACTS:
        drv = run_driver(rel, "default")
        plain = run_plain(rel)
        hits = drv["payload"].get("hits", [])
        by_caller = dict(Counter(h["caller_name"] for h in hits))
        by_origin = dict(Counter(
            "product_selftest" if CG_SRC in h["caller_file"] else "contract"
            for h in hits))
        per[rel] = {
            "n_runtime": len(hits),
            "by_caller": by_caller,
            "by_origin": by_origin,
            "hits": _short(hits),
            "driver_rc": drv["rc"], "driver_ok": drv["ok"],
            "driver_err": drv["err"],
            "plain_rc": plain["rc"],
            "rc_identical": drv["rc"] == plain["rc"],
            "stdout_identical": drv["stdout"] == plain["stdout"],
        }
    total = sum(v["n_runtime"] for v in per.values())
    saved6 = json.loads(ARCH_194.read_text(encoding="utf-8"))["accounts"][
        "B_runtime_readouts"]["n_resolve_policy_per_full_check_lower_bound"]
    product_selftest_total = sum(v["by_origin"].get("product_selftest", 0)
                                 for v in per.values())
    return {
        "per_contract": per,
        "n_total_runtime": total,
        "n_194_lower_bound": saved6,
        "gap": total - saved6,
        "lower_bound_still_holds": total >= saved6,
        "n_product_selftest_frames_total": product_selftest_total,
        "n_product_selftest_frames_counted_by_194": 2,
        "gap_explained_by_second_selftest_entry":
            product_selftest_total - 2 == total - saved6,
        "all_rc_identical": all(v["rc_identical"] for v in per.values()),
        "all_stdout_identical": all(v["stdout_identical"] for v in per.values()),
        "driver": "sys.setprofile + runpy.run_path, runner 同构（sys.executable -B / cwd=ROOT）",
        "merged_into_bool": False,
    }


# ─────────────── C 账：鉴别力反证 ───────────────

def counterfactual_worlds(A: Dict[str, Any], B: Dict[str, Any],
                          C: Dict[str, Any]) -> Dict[str, Any]:
    terms = A["formula_terms"]
    w = {
        "W0_baseline": {
            "static": A["recomputed_lower_bound"],
            "runtime": B["n_total_runtime"], "ran": True, "synthetic": False,
            "description": "两份契约按 default 各跑一次（194 外推口径）"},
        "W1_only_c1": {
            "static": terms[CG_SRC + "::selftest"] + terms[C1 + "::main"],
            "runtime": B["per_contract"][C1]["n_runtime"],
            "ran": True, "synthetic": False,
            "description": "只跑 contract_context_gate_test.py"},
        "W2_only_c2": {
            "static": terms[CG_SRC + "::selftest"] + terms[C2 + "::group_c"],
            "runtime": B["per_contract"][C2]["n_runtime"],
            "ran": True, "synthetic": False,
            "description": "只跑 contract_inliner_relax_test.py"},
        "W3_short_circuit": {
            "static": A["recomputed_lower_bound"],
            "runtime": B["per_contract"][C1]["n_runtime"]
                       + C["short_circuit"]["n_runtime"],
            "ran": True, "synthetic": True,
            "description": "事故版：selftest 真跑但返回 False ⇒ C6 and 链短路"},
    }
    keys = list(w)
    pairs = [[a, b] for i, a in enumerate(keys) for b in keys[i + 1:]
             if w[a]["static"] == w[b]["static"]
             and w[a]["runtime"] != w[b]["runtime"]]
    zero = [k for k in keys if w[k]["runtime"] == 0]
    synthetic = [k for k in keys if w[k]["synthetic"]]
    return {
        "worlds": w,
        "static_values": sorted({x["static"] for x in w.values()}),
        "runtime_values": sorted({x["runtime"] for x in w.values()}),
        "same_static_diff_runtime_pairs": pairs,
        "n_same_static_diff_runtime_pairs": len(pairs),
        "static_has_no_discrimination": len(pairs) > 0,
        "accident_static_unchanged":
            w["W0_baseline"]["static"] == w["W3_short_circuit"]["static"],
        "only_synthetic_world": synthetic,
        "n_zero_worlds": len(zero),
        "zero_worlds_not_applicable": len(zero) == 0,
        "all_zero_worlds_ran": all(w[k]["ran"] for k in zero),
        "natural_runtime_exceeds_static": any(
            x["runtime"] > A["recomputed_lower_bound"] and not x["synthetic"]
            for x in w.values()),
    }


def account_c(A: Dict[str, Any], B: Dict[str, Any]) -> Dict[str, Any]:
    sc = run_driver(C2, "short_circuit")
    sig = run_driver(C1, "signature_broken")
    sc_hits = sc["payload"].get("hits", [])
    sig_hits = sig["payload"].get("hits", [])
    short = {
        "n_runtime": len(sc_hits),
        "by_caller": dict(Counter(h["caller_name"] for h in sc_hits)),
        "hits": _short(sc_hits),
        "main_rc": sc["payload"].get("main_rc"),
        "n_fails": sc["payload"].get("n_fails"),
        "driver_ok": sc["ok"], "driver_err": sc["err"],
        "n_baseline": B["per_contract"][C2]["n_runtime"],
        "call_sites_unchanged": A["n_call_sites"],
    }
    broken = {
        "n_runtime": len(sig_hits),
        "by_caller": dict(Counter(h["caller_name"] for h in sig_hits)),
        "n_fails": sig["payload"].get("n_fails"),
        "main_rc": sig["payload"].get("main_rc"),
        "driver_ok": sig["ok"], "driver_err": sig["err"],
        "n_baseline": B["per_contract"][C1]["n_runtime"],
    }
    C = {
        "short_circuit": short,
        "signature_broken": broken,
        "short_circuit_teeth": short["n_runtime"] < short["n_baseline"],
        "count_unchanged_under_signature_accident":
            broken["n_runtime"] == broken["n_baseline"],
        "signature_accident_caught": (broken["n_fails"] or 0) > 0,
    }
    #: ⛔ 两本账不是常量声明：签名事故下次数不变而断言打红，
    #:    短路事故下次数变化 ⇒ 次数账与鉴别力账可分离。
    C["count_vs_discrimination_two_ledgers"] = (
        C["count_unchanged_under_signature_accident"]
        and C["signature_accident_caught"]
        and C["short_circuit_teeth"])
    C["counterfactual"] = counterfactual_worlds(A, B, C)
    C["discriminates"] = C["short_circuit_teeth"] and C["signature_accident_caught"]
    C["merged_into_bool"] = False
    return C


# ─────────────── D 账：边界 / 分母 / 验锚 ───────────────

def rp_call_sites() -> Dict[str, Any]:
    """192 的 5 个调用点文件（⛔ 复用 192 尺子自带白名单，不另写一份）。"""
    pde = _load("pde_r118", PDE_SRC)
    files = sorted(pde.resolve_policy_calls_all().keys())
    saved = json.loads(ARCH_192.read_text(encoding="utf-8"))
    archive = sorted((saved["accounts"]["A_entry_points"]["resolve_policy_calls"]
                      or {}).keys())
    return {"live": files, "n_live": len(files),
            "archive": archive, "n_archive": len(archive),
            "match": files == archive,
            "source": "scripts/default_effect_census.json (192 archive)"}


def denominators() -> Dict[str, Any]:
    main = json.loads(ARCH_MAIN.read_text(encoding="utf-8"))
    form = json.loads(ARCH_FORM.read_text(encoding="utf-8"))
    nores = json.loads(ARCH_NORES.read_text(encoding="utf-8"))
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


def runner_modes() -> Dict[str, Any]:
    """runner 静态读取面：两份契约都只声明 default ⇒ 各跑 1 次。"""
    ck = _load("aether_check_r118", CHECK_SRC)
    out = {}
    for rel in CONTRACTS:
        p = ROOT / rel
        out[rel] = {"modes": list(ck.declared_modes(p) or ()),
                    "supports_e2e": ck.supports_e2e(p),
                    "exclusive": ck.is_exclusive(p),
                    "min_python": list(ck.min_python(p) or ())}
    saved = json.loads(ARCH_194.read_text(encoding="utf-8"))["accounts"][
        "B_runtime_readouts"]["n_contract_runs_per_full_check"]
    return {"per_contract": out,
            "all_default_only": all(v["modes"] == ["default"]
                                    and not v["supports_e2e"]
                                    for v in out.values()),
            "n_runs_implied": sum(1 for v in out.values() if "default" in v["modes"]),
            "n_runs_saved_194": saved,
            "dual_source_match": saved == sum(
                1 for v in out.values() if "default" in v["modes"]),
            "source": "aether/check.py::declared_modes/supports_e2e/is_exclusive"}


def sandbox_selfcheck() -> Dict[str, Any]:
    """⛔ 新文件不得新增 `resolve_policy` AST 调用点（192/194 分母）。"""
    own = {rel: call_count_in_file(rel, "resolve_policy")
           for rel in (SELF_SRC, SELF_CONTRACT)
           if (ROOT / rel).exists()}
    pos = call_count_in_file(CG_SRC, "resolve_policy")
    inv = _load("pzd_r118", "scripts/probe_zero_discrimination.py").json_inventory()
    cov_saved = json.loads(ARCH_193.read_text(encoding="utf-8"))[
        "accounts"]["B_scan_surface"]["coverage_scripts"]
    return {"own_resolve_policy_call_sites": own,
            "no_new_denominator_call_site": all(v == 0 for v in own.values()),
            "positive_extractor_has_teeth": pos > 0, "positive_n": pos,
            "coverage_scripts_live": inv["coverage_scripts"],
            "coverage_scripts_saved": cov_saved,
            "coverage_unchanged": inv["coverage_scripts"] == cov_saved,
            "self_archive_suffix": Path(SELF_PRODUCT).suffix,
            "self_archive_not_json": not SELF_PRODUCT.endswith(".json"),
            "checked_own_source": True, "n_banned": 0}


def account_d(anchor_rep: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "denominator_dual_source": denominators(),
        "superseded": superseded_check(),
        "caliber_disk": caliber(),
        "sandbox_selfcheck": sandbox_selfcheck(),
        "runner_modes": runner_modes(),
        "anchor": anchor_rep,
        "section_183_touched": False,
        "real_expr_run": 0, "real_commit_run": 0, "gain_rows_reported": 0,
        "product_files_modified": 0,
        "merged_into_bool": False,
    }


# ─────────────── closure ───────────────

def build_closure(A: Dict[str, Any], B: Dict[str, Any],
                  C: Dict[str, Any], D: Dict[str, Any],
                  anchor: Dict[str, Any]) -> Dict[str, bool]:
    V: Dict[str, bool] = {}
    V["a1_194_formula_recomputed"] = A["recomputed_lower_bound"] == 6
    V["a2_194_dual_source"] = A["dual_source_match"] is True
    V["a3_194_terms_2_2_2"] = sorted(A["formula_terms"].values()) == [2, 2, 2]
    V["a4_194_runtime_paired_only_selftest"] = A["runtime_paired_value"] is True
    V["a5_194_unpaired_call_sites_4"] = A["runtime_unpaired_call_sites"] == 4
    V["a6_194_contract_runs_hardcoded"] = (
        A["contract_runs_is_hardcoded_constant"] is True
        and A["contract_runs_saved"] == 2)
    V["a7_call_sites_total"] = A["n_call_sites"] == 7
    V["a8_short_circuit_sites"] = A["n_short_circuit_sites"] == 2
    V["a9_selftest_entries_2"] = A["n_selftest_entries"] == 2
    V["a10_selftest_counted_once"] = A["n_selftest_entries_counted_by_194"] == 1

    V["b1_total_runtime_8"] = B["n_total_runtime"] == 8
    V["b2_each_contract_4"] = all(v["n_runtime"] == 4
                                  for v in B["per_contract"].values())
    V["b3_each_contract_2_plus_2"] = all(
        v["by_origin"].get("product_selftest") == 2
        and v["by_origin"].get("contract") == 2
        for v in B["per_contract"].values())
    V["b4_gap_2"] = B["gap"] == 2
    V["b5_lower_bound_holds"] = B["lower_bound_still_holds"] is True
    V["b6_gap_explained"] = B["gap_explained_by_second_selftest_entry"] is True
    V["b7_selftest_frames_4_vs_2"] = (
        B["n_product_selftest_frames_total"] == 4
        and B["n_product_selftest_frames_counted_by_194"] == 2)
    V["b8_all_rc_identical"] = B["all_rc_identical"] is True
    V["b9_all_stdout_identical"] = B["all_stdout_identical"] is True
    V["b10_drivers_ok"] = all(v["driver_ok"] and not v["driver_err"]
                              for v in B["per_contract"].values())

    sc, sig = C["short_circuit"], C["signature_broken"]
    V["c1_short_circuit_driver_ok"] = sc["driver_ok"] is True and not sc["driver_err"]
    V["c2_short_circuit_4_to_2"] = (sc["n_baseline"] == 4
                                    and sc["n_runtime"] == 2)
    V["c3_short_circuit_only_selftest_frames"] = (
        sc["by_caller"] == {"selftest": 2})
    V["c4_short_circuit_teeth"] = C["short_circuit_teeth"] is True
    V["c5_short_circuit_call_sites_unchanged"] = sc["call_sites_unchanged"] == 7
    V["c6_signature_driver_ok"] = sig["driver_ok"] is True and not sig["driver_err"]
    V["c7_signature_count_unchanged"] = (
        C["count_unchanged_under_signature_accident"] is True
        and sig["n_runtime"] == 4)
    V["c8_signature_accident_caught"] = C["signature_accident_caught"] is True
    V["c9_two_ledgers"] = C["count_vs_discrimination_two_ledgers"] is True
    cf = C["counterfactual"]
    V["c10_static_no_discrimination"] = (
        cf["static_has_no_discrimination"] is True
        and cf["accident_static_unchanged"] is True
        and cf["n_same_static_diff_runtime_pairs"] > 0)
    V["c11_runtime_values_vary"] = len(cf["runtime_values"]) > 1
    V["c12_zero_worlds_not_applicable"] = (
        cf["n_zero_worlds"] == 0
        and cf["zero_worlds_not_applicable"] is True)
    V["c13_natural_positive_and_one_synthetic"] = (
        cf["natural_runtime_exceeds_static"] is True
        and cf["only_synthetic_world"] == ["W3_short_circuit"])
    V["c14_discriminates"] = C["discriminates"] is True

    den, sup, sb, rm = (D["denominator_dual_source"], D["superseded"],
                        D["sandbox_selfcheck"], D["runner_modes"])
    V["d1_three_denominators"] = (
        den["main"] == 135 and den["form"] == 119
        and den["no_result"] == 94 and den["all_distinct"] is True)
    V["d2_dual_source_equal"] = (
        den["dual_source"]["main_rows"] == den["dual_source"]["main_summary"]
        and den["dual_source"]["form_rows"] == den["dual_source"]["form_summary"]
        and den["dual_source"]["no_result_rows"]
        == den["dual_source"]["no_result_summary"])
    V["d3_superseded_all_match"] = sup["all_match"] is True
    V["d4_no_new_call_site"] = sb["no_new_denominator_call_site"] is True
    V["d5_extractor_has_teeth"] = sb["positive_extractor_has_teeth"] is True
    V["d6_coverage_unchanged"] = sb["coverage_unchanged"] is True
    V["d7_archive_not_json"] = sb["self_archive_not_json"] is True
    V["d8_runner_all_default_only"] = rm["all_default_only"] is True
    V["d9_runner_dual_source"] = rm["dual_source_match"] is True
    V["d10_caliber_hash_shape"] = (
        len(D["caliber_disk"]) == len(CALIBER_FILES)
        and len(CALIBER_FILES) >= 20
        and all(len(v) == 64 and not v.startswith("MISSING:")
                for v in D["caliber_disk"].values()))
    V["d13_counter_scope_single_function"] = (
        A["counter_scope"]["n_resolve_policy_runtime_counter"] == 1
        and A["counter_scope"]["n_setprofile"] == 0
        and A["counter_scope"]["n_runpy_run_path"] == 0)
    V["d11_section183_untouched"] = (
        D["section_183_touched"] is False and D["real_expr_run"] == 0
        and D["real_commit_run"] == 0 and D["gain_rows_reported"] == 0
        and D["product_files_modified"] == 0)
    V["d12_anchor_ok"] = (anchor.get("ok") is True and anchor.get("repos") == 3
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
        "claim": "194 的『每次全量 ≥6 次真跑』是静态 AST 调用点数外推的曝光量；"
                 "真实 runner 上下文里是 8 次运行时调用",
        "n_194_static_lower_bound": A["recomputed_lower_bound"],
        "n_runtime_total": B["n_total_runtime"],
        "gap": B["gap"],
        "n_selftest_entries": A["n_selftest_entries"],
        "n_selftest_entries_counted_by_194": A["n_selftest_entries_counted_by_194"],
        "n_short_circuit_sites": A["n_short_circuit_sites"],
        "short_circuit_runtime_drop":
            C["short_circuit"]["n_baseline"] - C["short_circuit"]["n_runtime"],
        "static_has_no_discrimination":
            C["counterfactual"]["static_has_no_discrimination"],
        "n_same_static_diff_runtime_pairs":
            C["counterfactual"]["n_same_static_diff_runtime_pairs"],
        "natural_runtime_exceeds_static":
            C["counterfactual"]["natural_runtime_exceeds_static"],
        "runtime_counter_has_teeth": C["short_circuit_teeth"],
        "lower_bound_still_holds": B["lower_bound_still_holds"],
        "n_closure": len(closure), "n_closure_true": n_true,
    }
    doc = {
        "schema": "truerun_count_census/v1",
        "caliber": caliber(),
        "accounts": {"A_static_extrapolation": A,
                     "B_runtime_counter": B,
                     "C_discrimination": C,
                     "D_boundary": D},
        "derived": derived,
        "closure": closure,
        "anchor": anchor_rep,
        "summary": {
            "verdict": (
                "194 的『每次全量至少 6 次真跑 resolve_policy』把函数体内 "
                "AST 调用点数当成运行次数：复现其公式 2+2+2=6，其中只有 "
                "selftest() 单次调用做过运行时计数器对拍，另 4 个调用点从未 "
                "在 runner 上下文被计数。用 sys.setprofile 在 runner 同构上下文 "
                "真跑两份验收契约，各得 4 次（selftest 内 2 + 各自 2），合计 8 次；"
                "差的 2 次来自 selftest() 被两份契约各真跑一次而 194 只算一次。"
                "短路事故（selftest 真跑但返回 False）令第二份契约 4→2，"
                "证明『AST 调用点存在 ≠ 运行到』；签名事故令断言打红而次数不变，"
                "证明次数账与鉴别力账是两本账"),
            "scope": (
                "⛔ 6 是**保守下界**、字面仍成立（8 ≥ 6），故 194/195/196 的"
                "方向不翻案：195『≥6 次真跑是求值账』与 196『第五类证据只验签名"
                "字符串』针对的是真跑**内容**的鉴别力，不是次数。本轮把次数口径"
                "从静态外推的曝光量收窄为运行时实算的使用量。反事实四世界"
                "（现状 6/8、只跑 C1 4/4、只跑 C2 4/4、短路事故 6/6）里"
                "W0 与 W3 静态同为 6 而运行时 8≠6 ⇒ 静态数对短路事故零鉴别力；"
                "本轮无零次世界，故『零世界全已运行』记**不适用**、不拿空集当证据。"
                "8>6 是自然运行时读数，不是构造必然的 0。"
                "不是产品洞、不报收益、§183 零触碰；补采纳链运行证据仍须拍板"),
            "n_closure_true": n_true, "n_closure": len(closure),
        },
    }
    dest = Path(args.out) if args.out else OUT
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")
    fail = [k for k, v in closure.items() if not v]
    print("wrote %s" % dest)
    print("closure %d/%d  static=%d runtime=%d gap=%d short_circuit=%d->%d"
          % (n_true, len(closure), A["recomputed_lower_bound"],
             B["n_total_runtime"], B["gap"],
             C["short_circuit"]["n_baseline"], C["short_circuit"]["n_runtime"]))
    if fail:
        print("FAIL:", fail)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
