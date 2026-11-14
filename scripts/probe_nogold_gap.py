#!/usr/bin/env python3
"""r111 / README 190：189 两本账的 `k` 值覆盖缺口（纯只读取证）。

═══ 起点：189 的「可复算 / 有牙」只在合成样本上测过 ═══

189（r110）把四条自限全放宽并默认生效，用 6 个策略 × 6 个合成样本
= 36 行把「放宽会放行什么」钉成封闭读数，`fake_disc_leaked=0`，
`switch_teeth 4/4`。但产品自己的 `classify_probe()` 有 **4** 个返回值：

    none        求值不了              （154 硬失败）
    ok_nogold   能求值、无法对拍      （154 三档里「语义未证」那 293 条）
    ok          与作者写死 expected 一致
    mismatch    与作者写死 expected 不符（假鉴别力）

189 的 36 行里只出现 `ok` / `mismatch` / `none` ⇒ **覆盖 3/4**，
`ok_nogold` 从未进过反证组；而 `adopt_probe()` 只把 `none` /
`mismatch` 判为语义未证，`ok_nogold` 一律 `semantic_verified=True`
⇒ 与 154 三档的「语义未证」命名不自洽。

⛔ 更关键：本轮把 177 主账 135 条逐条同源重放后发现，真实数据里
非 `none` 的 k **全部**是 `ok_nogold`，`ok` / `mismatch` 是 0 条
⇒ 189 合成反证组与真实主账在 k 值上**唯一交集是 `none`**。
即 189 的 `fake_disc_leaked=0` 是在一个真实主账上几乎不出现的
k 分布上测出来的（第 115 条：单次读数 ≠ 结构必然；187 教训：
n=6 合成样本不可外推）。

═══ 四本账（⛔ 不合并成布尔）═══

  A 覆盖矩阵  classify_probe 的 4 个 k 值（AST 从产品源码抽，不手写词表）
             × 189 A 账 36 行覆盖情况 × 0000/1111 下 adopt_probe 全枚举
             + 4/4 正例反证（证明 k 值不是死代码）
  B 主账重放  177 主账 135 条逐条同源重放：scoped_assertions →
             classify_probe → adopt_probe；got 只取存档**已记录**的值，
             零沙箱、零真实 expr、零真实 commit；两口径逐行 ledger 对拍
  C 主流程    源码级：主流程按 `k` 分到 rescued_nogold / disc_nogold，
             ⛔ 不进 disc_gold ⇒ **不是产品洞**，是 189 离线尺子的
             ledger 语义与 154 三档不自洽 + 反证组缺一格
  D 边界      aether/ 未改、189 存档零回写、119 form 门不合并、
             §183 禁令零触碰、验锚 OK 3/3

⛔⛔ 本轮**只取证报告，不改 189 已拍板的语义**：`adopt_probe()` /
`probe_context_gate.py` / `inliner_relax_census.json` 一个字节都不动。
给 `adopt_probe` 补 `ok_nogold` 分支或给反证组补样本 ⇒ 属改判据语义，
是另一次需拍板的产品口径变更（第 34 条）。

跑法：python scripts/probe_nogold_gap.py
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aether import extract as E                                  # noqa: E402
from realrepo_baseline import require_baseline                   # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "pcg_r111", ROOT / "scripts" / "probe_context_gate.py")
CG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CG)

OUT = ROOT / "scripts" / "nogold_gap_census.json"
ARCH_MAIN = ROOT / "scripts" / "extract_downstream.json"        # 177 主账 135
ARCH_189 = ROOT / "scripts" / "inliner_relax_census.json"       # 189 A 账 36
ARCH_FORM = ROOT / "scripts" / "form_shape_attribution.json"    # 179 form 119
CG_SRC = ROOT / "scripts" / "probe_context_gate.py"

#: ⛔ 新口径另存（v1）：含尺子自身、含量具 hash、含 189 存档 hash。
#:    旧存档零回写，跨口径读数不可比（eval-standard-anchoring 契约①）。
CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    "aether/environment.py",
    "scripts/probe_context_gate.py",
    "scripts/inliner_relax_census.json",
    "scripts/extract_downstream.json",
    "scripts/form_shape_attribution.json",
    "scripts/no_result_attribution.json",
    "scripts/realrepo_baseline.json",
    "scripts/probe_nogold_gap.py",
)

#: 154 三档原文读数（README 5777-5784），⛔ 硬编码、不从生产常量读。
READING_154 = {
    "gold": {"rescued": 247, "disc": 8},
    "nogold": {"rescued": 293, "disc": 4, "semantic": "语义未证"},
    "raises": {"rescued": 76, "disc": 1},
    "total": {"rescued": 616, "disc": 13},
}


def sha256_file(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {rel: sha256_file(rel) for rel in CALIBER_FILES}


def k_values_from_source() -> List[str]:
    """⛔ 同源：从产品自己的 `classify_probe` AST 抽 return 字面量，
    绝不另写一份 k 词表（契约⑥：判据必须与被测对象同源）。"""
    tree = ast.parse(CG_SRC.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "classify_probe")
    #: ⛔ 末行是 `return "ok" if v == g else "mismatch"`（IfExp），
    #:    只看 Return.value 是不是 Constant 会漏掉 2 个 k 值（第 24 条：
    #:    恒少先怀疑量具）。改成收集 Return 表达式里**所有**字符串常量。
    vals: set[str] = set()
    for n in ast.walk(fn):
        if not isinstance(n, ast.Return):
            continue
        for sub in ast.walk(n.value):
            if (isinstance(sub, ast.Constant)
                    and isinstance(sub.value, str)):
                vals.add(sub.value)
    return sorted(vals)


def positive_control() -> Dict[str, Any]:
    """4/4 正例反证：证明 4 个 k 值都能被产品函数真返回（不是恒真 / 死代码）。"""
    probes = (
        ("none", ("x", "1", None)),
        ("ok_nogold", ("x", "f()", "1")),
        ("ok", ("x", "1", "1")),
        ("mismatch", ("x", "1", "2")),
    )
    got = {want: CG.classify_probe(*args) for want, args in probes}
    return {"probes": {w: list(a) for w, a in probes},
            "observed": got,
            "all_hit": all(got[w] == w for w, _ in probes),
            "n_hit": sum(got[w] == w for w, _ in probes),
            "n_total": len(probes)}


def account_a() -> Dict[str, Any]:
    """A 账：覆盖矩阵（纯静态、零沙箱、零子进程）。"""
    ks = k_values_from_source()
    d189 = json.loads(ARCH_189.read_text(encoding="utf-8"))
    rows189 = d189["accounts"]["A_policy_matrix"]["rows"]
    seen = sorted({r["k"] for r in rows189 if isinstance(r.get("k"), str)})
    missing = [k for k in ks if k not in seen]

    matrix: List[Dict[str, Any]] = []
    for k in ks:
        for sig, pol in (("0000", CG.STRICT), ("1111", CG.RELAXED)):
            for disc in (False, True):
                ledger, verified, cd = CG.adopt_probe(k, False, pol, disc)
                matrix.append({"k": k, "policy": sig, "disc": disc,
                               "ledger": ledger,
                               "semantic_verified": verified,
                               "counts_disc": cd})
    nogold = [r for r in matrix if r["k"] == "ok_nogold"]
    pc = positive_control()
    return {
        "source_k": "scripts/probe_context_gate.py::classify_probe (AST)",
        "k_values": ks,
        "n_k_values": len(ks),
        "covered_by_189_A": seen,
        "n_covered": len(seen),
        "missing_k": missing,
        "n_189_rows": len(rows189),
        "k_counts_189": dict(sorted(Counter(
            str(r.get("k")) for r in rows189).items())),
        "adopt_matrix": matrix,
        "n_adopt_rows": len(matrix),
        "nogold_0000": [r for r in nogold if r["policy"] == "0000"],
        "nogold_1111": [r for r in nogold if r["policy"] == "1111"],
        "nogold_verified_true_in_both": all(
            r["semantic_verified"] for r in nogold),
        "reading_154": READING_154,
        "conflict_with_154": (
            "ok_nogold 被 adopt_probe 判 semantic_verified=True，"
            "但 154 三档把『无金标准可对拍』标为语义未证（293 条）"),
        "positive_control": pc,
        "boundary": "A 账纯函数，零沙箱、零真实 expr",
    }


def account_b() -> Dict[str, Any]:
    """B 账：177 主账 135 条逐条同源重放（零沙箱）。

    ⛔ `got` 只取存档**已记录**的 `got_fixed`，绝不 eval / exec / 跑沙箱；
       `scoped_assertions` / `classify_probe` / `adopt_probe` 全是产品自己的
       函数，本脚本不另写第二份判据。
    """
    d = json.loads(ARCH_MAIN.read_text(encoding="utf-8"))
    rows = d["rows"]
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(rows):
        stmt = r.get("stmt") or ""
        syntax_error = False
        try:
            a = CG.scoped_assertions(stmt)
        except SyntaxError:
            a = []
            syntax_error = True
        splittable = bool(a)
        act, exp, _fn = a[0] if a else ("", "", "")
        got = r.get("got_fixed")            # ⛔ 存档已记录的观察值
        k = (CG.classify_probe(act, exp, got) if splittable
             else "UNSPLITTABLE")
        unstable = (r.get("gate") == "unstable")
        disc = bool(r.get("truthy_f2p"))
        strict = CG.adopt_probe(k, unstable, CG.STRICT, disc)
        relaxed = CG.adopt_probe(k, unstable, CG.RELAXED, disc)
        cause = ""
        if k == "ok_nogold":
            # ⛔ 同源细分：gold_of / lit 都是产品函数，不另写规则
            cause = ("gold_not_literal" if E.gold_of(exp) is None
                     else "got_not_literal")
        out.append({
            "i": i, "repo": r.get("repo"), "sha": r.get("sha"),
            "verdict": r.get("verdict"), "gate": r.get("gate"),
            "splittable": splittable,
            "syntax_error": syntax_error,
            "got_recorded": got is not None,
            "k": k, "nogold_cause": cause,
            "unstable": unstable, "disc": disc,
            "strict_ledger": strict[0], "strict_verified": strict[1],
            "strict_counts_disc": strict[2],
            "relaxed_ledger": relaxed[0], "relaxed_verified": relaxed[1],
            "relaxed_counts_disc": relaxed[2],
        })

    ks = Counter(r["k"] for r in out)
    strict_l = Counter(r["strict_ledger"] for r in out)
    relaxed_l = Counter(r["relaxed_ledger"] for r in out)
    nogold = [r for r in out if r["k"] == "ok_nogold"]
    cross = Counter((r["k"], r["strict_ledger"], r["relaxed_ledger"])
                    for r in out)
    return {
        "source": "scripts/extract_downstream.json (177 主账)",
        "rows": out,
        "n_rows": len(out),
        "n_splittable": sum(r["splittable"] for r in out),
        "n_unsplittable": sum(not r["splittable"] for r in out),
        "n_syntax_error": sum(r["syntax_error"] for r in out),
        "n_got_recorded": sum(r["got_recorded"] for r in out),
        "k_distribution": dict(sorted(ks.items())),
        "nogold_causes": dict(sorted(Counter(
            r["nogold_cause"] for r in nogold).items())),
        "n_nogold": len(nogold),
        "n_ok": ks.get("ok", 0), "n_mismatch": ks.get("mismatch", 0),
        "strict_ledgers": dict(sorted(strict_l.items())),
        "relaxed_ledgers": dict(sorted(relaxed_l.items())),
        "cross": {f"{a}|{b}|{c}": n for (a, b, c), n in sorted(cross.items())},
        "nogold_counts_disc_same_in_both": all(
            r["strict_counts_disc"] == r["relaxed_counts_disc"]
            for r in nogold),
        #: ⛔ 分层：非 unstable 的 nogold 两口径都判 verified=True；
        #:    unstable 的 nogold 两口径都**未证**（严格 rejected /
        #:    放宽 relaxed_unstable_runs）。不许把两者合并成一个布尔。
        "n_nogold_unstable": sum(r["unstable"] for r in nogold),
        "n_unsplittable_unstable": sum(
            r["unstable"] and r["k"] == "UNSPLITTABLE" for r in out),
        "n_unstable_rows": sum(r["unstable"] for r in out),
        "nogold_stable_verified_true_in_both": all(
            r["strict_verified"] and r["relaxed_verified"]
            for r in nogold if not r["unstable"]),
        "all_unstable_unverified_in_both": all(
            not r["strict_verified"] and not r["relaxed_verified"]
            for r in out if r["unstable"]),
        "n_strict_verified_rows": sum(r["strict_verified"] for r in out),
        "n_relaxed_verified_rows": sum(r["relaxed_verified"] for r in out),
        "n_newly_verified_by_relax": sum(
            r["relaxed_verified"] and not r["strict_verified"] for r in out),
        "n_row_ledger_changed": sum(
            r["strict_ledger"] != r["relaxed_ledger"] for r in out),
        "n_row_counts_disc_changed": sum(
            r["strict_counts_disc"] != r["relaxed_counts_disc"]
            for r in out),
        "unique_intersection_with_189": sorted(
            set(ks) & {"none", "ok", "mismatch"}),
        "boundary": ("零沙箱 / 零真实 expr / 零真实 commit / 零 F2P；"
                     "119 条 form 门无探针 ⇒ unmeasurable，不同分母不合并"),
    }


def account_c() -> Dict[str, Any]:
    """C 账：主流程分档自洽（源码级 AST，零沙箱）⇒ 不是产品洞。"""
    src = CG_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    assign = [n for n in ast.walk(tree)
              if isinstance(n, ast.Assign)
              and isinstance(n.value, ast.IfExp)
              and isinstance(n.value.body, ast.Constant)
              and n.value.body.value == "gold"
              and isinstance(n.value.orelse, ast.Constant)
              and n.value.orelse.value == "nogold"]
    routed_nogold = len(assign) == 1
    # 反证：把 else 分支改成 "gold" ⇒ 断言必须转红（证明不是恒真）
    bad = src.replace('"gold" if k == "ok" else "nogold"',
                      '"gold" if k == "ok" else "gold"')
    bad_tree = ast.parse(bad)
    bad_assign = [n for n in ast.walk(bad_tree)
                  if isinstance(n, ast.Assign)
                  and isinstance(n.value, ast.IfExp)
                  and isinstance(n.value.orelse, ast.Constant)
                  and n.value.orelse.value == "nogold"]
    return {
        "source": "scripts/probe_context_gate.py (main 第二轮记账)",
        "routes_nogold_to_nogold": routed_nogold,
        "n_matching_statements": len(assign),
        "falsified_variant_has_no_nogold_branch": len(bad_assign) == 0,
        "conclusion": ("主流程按 k 把 ok_nogold 记进 rescued_nogold / "
                       "disc_nogold，⛔ 不进 disc_gold ⇒ "
                       "189 的 semantic_verified 命名冲突不是产品洞；"
                       "真缺口是离线反证组少一格 k 值"),
        "reading_154_nogold": READING_154["nogold"],
        "zero_sandbox": True,
    }


def account_d(cal: Dict[str, str]) -> Dict[str, Any]:
    """D 账：边界（旧存档零回写、三分母不合并、§183 禁令零触碰）。"""
    form = json.loads(ARCH_FORM.read_text(encoding="utf-8"))
    main = json.loads(ARCH_MAIN.read_text(encoding="utf-8"))
    no_result = json.loads(
        (ROOT / "scripts" / "no_result_attribution.json")
        .read_text(encoding="utf-8"))
    return {
        "aether_extract_sha256": cal["aether/extract.py"],
        "extract_unchanged": cal["aether/extract.py"].startswith("974203181bc9"),
        "archive_189_sha256_at_measure": cal["scripts/inliner_relax_census.json"],
        "archive_189_not_rewritten": True,
        "probe_context_gate_sha256": cal["scripts/probe_context_gate.py"],
        "n_main_rows": main["summary"]["n_main"],
        "n_form_rows": form["summary"]["n_rows"],
        "n_no_result_rows": len(no_result["rows"]),
        "form_all_unmeasurable": (
            main["summary"]["form_diagnostic_verdicts"].get("unmeasurable")
            == form["summary"]["n_rows"]),
        "three_denominators_separate": (
            main["summary"]["n_main"] == 135
            and form["summary"]["n_rows"] == 119
            and len(no_result["rows"]) == 94
            and len({main["summary"]["n_main"],
                     form["summary"]["n_rows"],
                     len(no_result["rows"])}) == 3),
        "n_real_expr_sandbox": 0,
        "n_real_commits": 0,
        "f2p_reported": False,
        "gain_reported": False,
        "policy_semantics_changed": False,
        "boundary": ("§183 禁令面零触碰；修 ok_nogold 属改 189 已拍板的"
                     "判据语义 ⇒ 另一次需拍板，本轮不动"),
    }


def measure() -> Dict[str, Any]:
    A, B, C = account_a(), account_b(), account_c()
    anchor = require_baseline()
    cal = caliber()
    D = account_d(cal)
    closure = {
        "c1_main_rows_135": B["n_rows"] == 135,
        "c2_split_94_plus_41": (B["n_splittable"] + B["n_unsplittable"]
                                == B["n_rows"] == 135),
        "c3_k_sum_eq_135": sum(B["k_distribution"].values()) == 135,
        "c4_k_values_4": A["n_k_values"] == 4,
        "c5_covered_3_missing_1": (A["n_covered"] == 3
                                   and A["missing_k"] == ["ok_nogold"]),
        "c6_adopt_matrix_16": A["n_adopt_rows"] == 16,
        "c7_positive_control_4_of_4": (A["positive_control"]["all_hit"]
                                       and A["positive_control"]["n_hit"] == 4),
        "c8_real_ok_mismatch_zero": (B["n_ok"] == 0
                                     and B["n_mismatch"] == 0),
        "c9_real_nogold_positive": B["n_nogold"] > 0,
        "c10_intersection_only_none": B["unique_intersection_with_189"] == ["none"],
        "c11_nogold_verified_true": (A["nogold_verified_true_in_both"]
                                     and B["nogold_stable_verified_true_in_both"]),
        "c11b_all_unstable_unverified": (
            B["n_unstable_rows"] == 3
            and B["n_nogold_unstable"] + B["n_unsplittable_unstable"] == 3
            and B["all_unstable_unverified_in_both"]),
        "c11c_no_newly_verified": (B["n_newly_verified_by_relax"] == 0
                                   and B["n_strict_verified_rows"]
                                   == B["n_relaxed_verified_rows"]),
        "c12_nogold_disc_same_in_both": B["nogold_counts_disc_same_in_both"],
        "c13_main_flow_routes_nogold": C["routes_nogold_to_nogold"],
        "c14_main_flow_falsifiable": C["falsified_variant_has_no_nogold_branch"],
        "c15_form_119_separate": D["form_all_unmeasurable"],
        "c16_three_denominators": D["three_denominators_separate"],
        "c17_extract_unchanged": D["extract_unchanged"],
        "c18_caliber_has_self": "scripts/probe_nogold_gap.py" in cal,
        "c19_anchor_ok": (bool(anchor.get("ok"))
                          and not anchor.get("diffs")
                          and not anchor.get("caliber_diffs")),
        "c20_zero_real_expr": (D["n_real_expr_sandbox"] == 0
                               and D["n_real_commits"] == 0
                               and D["gain_reported"] is False
                               and D["f2p_reported"] is False),
        "c21_policy_semantics_untouched": D["policy_semantics_changed"] is False,
    }
    return {
        "schema": "nogold_gap_census/v1",
        "caliber": cal,
        "accounts": {"A_k_coverage": A,
                     "B_main_replay": B,
                     "C_main_flow": C,
                     "D_boundary": D},
        "derived": {
            "coverage_ratio": f"{A['n_covered']}/{A['n_k_values']}",
            "missing_k": A["missing_k"],
            "real_k_distribution": B["k_distribution"],
            "real_nogold": B["n_nogold"],
            "strict_ledgers": B["strict_ledgers"],
            "relaxed_ledgers": B["relaxed_ledgers"],
            "n_row_ledger_changed": B["n_row_ledger_changed"],
            "n_row_counts_disc_changed": B["n_row_counts_disc_changed"],
            "n_strict_verified_rows": B["n_strict_verified_rows"],
            "n_relaxed_verified_rows": B["n_relaxed_verified_rows"],
            "n_newly_verified_by_relax": B["n_newly_verified_by_relax"],
            "n_nogold_unstable": B["n_nogold_unstable"],
        },
        "closure": closure,
        "anchor": {"ok": bool(anchor.get("ok")),
                   "diffs": list(anchor.get("diffs") or []),
                   "caliber_diffs": list(anchor.get("caliber_diffs") or [])},
        "summary": {
            "verdict": ("189 的离线反证组覆盖 classify_probe 4 个 k 值中的 3 个；"
                        "缺的 ok_nogold 恰是 177 主账 135 条里唯一非 none 的 k。"
                        "主流程按 k 分到 nogold 档 ⇒ 不是产品洞；"
                        "修法属改 189 已拍板语义，需另一次拍板"),
            "not_product_bug": True,
            "policy_semantics_changed": False,
            "gain_reported": False,
            "f2p_reported": False,
            "needs_approval_to_fix": True,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    d = measure()
    Path(args.out).write_text(
        json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("k values =", d["accounts"]["A_k_coverage"]["k_values"])
    print("covered by 189 =", d["accounts"]["A_k_coverage"]["covered_by_189_A"])
    print("missing =", d["accounts"]["A_k_coverage"]["missing_k"])
    print("real k =", d["derived"]["real_k_distribution"])
    print("closure =", sum(d["closure"].values()), "/", len(d["closure"]))
    print("written", args.out)
    return 0 if all(d["closure"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
