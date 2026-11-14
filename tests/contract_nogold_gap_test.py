#!/usr/bin/env python3
"""r111 / README 190：189 两本账的 `k` 值覆盖缺口 —— 离线可回归契约。

  A 组  钉覆盖矩阵读数（classify_probe 4 个 k / 189 覆盖 3/4 / 正例反证）
  B 组  钉 135 主账逐条同源重放读数（零沙箱、两口径 ledger 对拍）
  C 组  本机实战（真跑）：磁盘 hash 对拍、源码 AST、量具自检、
        A1 扫描面、主流程路由反证、验锚与 closure

⛔ 首次全绿必须注入反证（`/tmp/r111_falsify.py`）：A/B 组逐条把条件
   置 False ⇒ 恰好该项转红；C 组用 `R111_WITHDRAW=Cn` 完整真跑逐条撤回。
⛔ 判据与被测对象同源：k 值从产品 `classify_probe` 的 AST 抽出，
   ledger 全部走产品 `adopt_probe`，本契约不另写第二份规则。
⛔ 本契约纯只读取证：零真实 expr、零真实 commit、零 F2P / 收益读数，
   不改 189 已拍板的判据语义。
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
import probe_nogold_gap as pir                                   # noqa: E402

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)
CENSUS = ROOT / "scripts" / "nogold_gap_census.json"
FAILS: List[str] = []
CG = pir.CG
D = json.loads(CENSUS.read_text(encoding="utf-8"))


def ck(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (" | " + str(detail) if detail and not cond else ""))


def live_replay(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    """⛔ 同源实算：不读 census 的结论字段，只用**产品自己的函数** +
    177 原存档重算 3 行。供反证脚本打补丁验证判据有牙（B4 型反证）。"""
    arch = json.loads(
        (ROOT / "scripts" / "extract_downstream.json")
        .read_text(encoding="utf-8"))["rows"]
    out: List[Dict[str, Any]] = []
    for i in (0, 100, 130):
        r = arch[i]
        try:
            a = CG.scoped_assertions(r.get("stmt") or "")
        except SyntaxError:
            a = []
        act, exp, _fn = a[0] if a else ("", "", "")
        k = (CG.classify_probe(act, exp, r.get("got_fixed")) if a
             else "UNSPLITTABLE")
        unstable = (r.get("gate") == "unstable")
        disc = bool(r.get("truthy_f2p"))
        out.append({"i": i, "k": k,
                    "strict": CG.adopt_probe(k, unstable, CG.STRICT, disc),
                    "relaxed": CG.adopt_probe(k, unstable, CG.RELAXED, disc)})
    return out


def conditions(d: Dict[str, Any]) -> Dict[str, bool]:
    """A+B 组：**纯函数**，零沙箱零子进程，供反证脚本逐条篡改。"""
    V: Dict[str, bool] = {}
    acc = d.get("accounts", {})
    A = acc.get("A_k_coverage", {})
    B = acc.get("B_main_replay", {})
    C = acc.get("C_main_flow", {})
    E = acc.get("D_boundary", {})
    der = d.get("derived", {})
    rows = B.get("rows", [])
    kvals = A.get("k_values", [])
    covered = A.get("covered_by_189_A", [])
    pc = A.get("positive_control", {})
    mat = A.get("adopt_matrix", [])
    kd = B.get("k_distribution", {})
    sl = B.get("strict_ledgers", {})
    rl = B.get("relaxed_ledgers", {})

    # ── A 组：覆盖矩阵 ───────────────────────────────────────────
    V["A1"] = d.get("schema") == "nogold_gap_census/v1"
    V["A2"] = (kvals == ["mismatch", "none", "ok", "ok_nogold"]
               and len(kvals) == 4)
    V["A3"] = covered == ["mismatch", "none", "ok"]
    V["A4"] = A.get("missing_k") == ["ok_nogold"]
    V["A5"] = A.get("n_189_rows") == 36
    V["A6"] = "ok_nogold" not in A.get("k_counts_189", {})
    V["A7"] = A.get("n_adopt_rows") == len(mat) == 16
    V["A8"] = ({(r["k"], r["policy"], r["disc"]) for r in mat}
               == {(k, p, dc) for k in kvals
                   for p in ("0000", "1111") for dc in (False, True)})
    V["A9"] = all(r["semantic_verified"] for r in A.get("nogold_0000", []))
    V["A10"] = all(r["semantic_verified"] for r in A.get("nogold_1111", []))
    V["A11"] = ({r["ledger"] for r in A.get("nogold_1111", [])}
                == {"relaxed_rescue"})
    V["A12"] = (sorted(r["counts_disc"] for r in A.get("nogold_1111", []))
                == [False, True])
    V["A13"] = pc.get("all_hit") is True and pc.get("n_hit") == 4
    V["A14"] = (set(pc.get("observed", {}).values()) == set(kvals)
                and len(pc.get("observed", {})) == 4)
    V["A15"] = (A.get("reading_154", {}).get("nogold", {}).get("rescued") == 293
                and A.get("reading_154", {}).get("nogold", {}).get("semantic")
                == "语义未证")
    V["A16"] = (A.get("nogold_verified_true_in_both") is True
                and A.get("reading_154", {}).get("nogold", {}).get("semantic")
                == "语义未证")
    V["A17"] = der.get("coverage_ratio") == "3/4"

    # ── B 组：135 主账逐条重放 ───────────────────────────────────
    V["B1"] = B.get("n_rows") == 135
    V["B2"] = B.get("n_splittable") == 94
    V["B3"] = (B.get("n_unsplittable") == 41
               and B.get("n_splittable") + B.get("n_unsplittable") == 135)
    V["B4"] = B.get("n_syntax_error") == 9
    V["B5"] = B.get("n_got_recorded") == 32
    V["B6"] = kd == {"UNSPLITTABLE": 41, "none": 72, "ok_nogold": 22}
    V["B7"] = B.get("n_ok") == 0 and B.get("n_mismatch") == 0
    V["B8"] = B.get("n_nogold") == 22
    V["B9"] = B.get("nogold_causes") == {"gold_not_literal": 22}
    V["B10"] = sl == {"rejected": 75, "strict_rescue": 60}
    V["B11"] = rl == {"relaxed_no_value": 72, "relaxed_rescue": 60,
                      "relaxed_unstable_runs": 3}
    V["B12"] = (B.get("n_strict_verified_rows") == 60
                and B.get("n_relaxed_verified_rows") == 60)
    V["B13"] = B.get("n_newly_verified_by_relax") == 0
    V["B14"] = B.get("n_row_counts_disc_changed") == 0
    V["B15"] = B.get("n_row_ledger_changed") == 135
    V["B16"] = B.get("nogold_counts_disc_same_in_both") is True
    V["B17"] = (B.get("n_unstable_rows") == 3
                and B.get("n_nogold_unstable") == 1
                and B.get("n_unsplittable_unstable") == 2)
    V["B18"] = B.get("all_unstable_unverified_in_both") is True
    V["B19"] = B.get("nogold_stable_verified_true_in_both") is True
    V["B20"] = B.get("unique_intersection_with_189") == ["none"]
    V["B21"] = (len(rows) == 135
                and all(r.get("strict_ledger") and r.get("relaxed_ledger")
                        for r in rows))
    V["B22"] = sum(B.get("cross", {}).values()) == 135
    V["B23"] = (len({r["k"] for r in rows}) == 3
                and {r["k"] for r in rows} == set(kd))
    V["B24"] = (der.get("real_k_distribution") == kd
                and der.get("real_nogold") == 22)

    # ── C/D/边界：读数字段 ───────────────────────────────────────
    V["B25"] = C.get("routes_nogold_to_nogold") is True
    V["B26"] = C.get("falsified_variant_has_no_nogold_branch") is True
    V["B27"] = C.get("zero_sandbox") is True
    V["B28"] = E.get("extract_unchanged") is True
    V["B29"] = E.get("archive_189_not_rewritten") is True
    V["B30"] = (E.get("n_main_rows") == 135
                and E.get("n_form_rows") == 119
                and E.get("n_no_result_rows") == 94
                and E.get("three_denominators_separate") is True)
    V["B31"] = E.get("form_all_unmeasurable") is True
    V["B32"] = (E.get("n_real_expr_sandbox") == 0
                and E.get("n_real_commits") == 0
                and E.get("f2p_reported") is False
                and E.get("gain_reported") is False)
    V["B33"] = E.get("policy_semantics_changed") is False
    V["B34"] = (d.get("summary", {}).get("needs_approval_to_fix") is True
                and d.get("summary", {}).get("not_product_bug") is True)
    V["B35"] = (len(d.get("closure", {})) == 23
                and all(d["closure"].values()))
    V["B36"] = (d.get("anchor", {}).get("ok") is True
                and not d["anchor"].get("diffs")
                and not d["anchor"].get("caliber_diffs"))
    V["B37"] = "scripts/probe_nogold_gap.py" in d.get("caliber", {})
    V["B38"] = (len(d.get("caliber", {})) == len(pir.CALIBER_FILES)
                and all(len(v) == 64 for v in d["caliber"].values()))

    # ── 同源实算（⛔ 会调产品函数 ⇒ 事故版补丁能打到这里）────────
    L = live_replay(d)
    crows = {r["i"]: r for r in rows}
    V["B39"] = all(
        (L[j]["k"] == crows[L[j]["i"]]["k"]
         and L[j]["strict"][0] == crows[L[j]["i"]]["strict_ledger"]
         and L[j]["relaxed"][0] == crows[L[j]["i"]]["relaxed_ledger"]
         and L[j]["relaxed"][1] == crows[L[j]["i"]]["relaxed_verified"])
        for j in range(len(L)))
    V["B40"] = (CG.adopt_probe("ok_nogold", False, CG.RELAXED, True)
                == ("relaxed_rescue", True, True))
    probes = (("none", ("x", "1", None)), ("ok_nogold", ("x", "f()", "1")),
              ("ok", ("x", "1", "1")), ("mismatch", ("x", "1", "2")))
    V["B41"] = ({w: CG.classify_probe(*a) for w, a in probes}
                == pc.get("observed"))
    return V


def emit(V: Dict[str, bool]) -> List[str]:
    fails: List[str] = []
    for name in sorted(V):
        ok = V[name]
        print(("  PASS " if ok else "  FAIL ") + name)
        if not ok:
            fails.append(name)
    return fails


def run_c() -> None:
    """C 组本机实战：真读磁盘、真跑自检、真源码 AST、真反证。"""
    withdraw = os.environ.get("R111_WITHDRAW", "")
    print("== C 组：本机实战（真跑，不改仓库）==")

    # C1 磁盘 hash 对拍（含尺子自身）
    live = pir.caliber()
    c1 = live == D["caliber"]
    ck("C1 caliber 9 项与磁盘逐字对拍（含尺子自身）",
       c1 and not withdraw == "C1", "" if c1 else str(
           [k for k in live if live[k] != D["caliber"].get(k)]))

    # C2 源码 AST 同源 + 事故版反证（只收 Constant 会漏 IfExp 的 2 个 k）
    src = (ROOT / "scripts" / "probe_context_gate.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "classify_probe")
    naive = {n.value.value for n in ast.walk(fn)
             if isinstance(n, ast.Return)
             and isinstance(n.value, ast.Constant)
             and isinstance(n.value.value, str)}
    c2 = (len(naive) == 2 and len(pir.k_values_from_source()) == 4
          and naive < set(pir.k_values_from_source()))
    ck("C2 ⛔ k 值同源抽出 4 个；事故版只收 Constant 只有 2 个（IfExp 有牙）",
       c2 and not withdraw == "C2", f"naive={sorted(naive)}")

    # C3 量具自检（15 条 A–O）
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = CG.selftest()
    ck("C3 量具自检 15 条 A–O 真跑通过",
       ok is True and not withdraw == "C3", buf.getvalue()[-120:])

    # C4 4 个 k 值正例反证（产品函数，非恒真）
    pc = pir.positive_control()
    c4 = pc["all_hit"] and pc["n_hit"] == 4
    ck("C4 4/4 正例反证：产品 classify_probe 真能返回 4 个 k",
       c4 and not withdraw == "C4", str(pc["observed"]))

    # C5 A1 扫描面：新文件在候选面 + 零命中 + 正例反证
    cfiles = set(candidate_files() or [])
    new_files = ("scripts/probe_nogold_gap.py",
                 "scripts/nogold_gap_census.json",
                 "tests/contract_nogold_gap_test.py")
    sample = "ROOT = \"" + str(Path.home()) + "/V6\""
    c5 = (all(f in cfiles for f in new_files)
          and not scan_text((ROOT / "scripts" / "probe_nogold_gap.py")
                            .read_text(encoding="utf-8"))
          and bool(scan_text(sample)))
    ck("C5 A1 扫描面：3 个新文件在候选面 / 零命中 / 正例反证有牙",
       c5 and not withdraw == "C5", str(sorted(f for f in new_files
                                             if f not in cfiles)))

    # C6 验锚 + 主流程路由反证
    anchor = require_baseline()
    c6 = (bool(anchor.get("ok")) and not anchor.get("diffs")
          and not anchor.get("caliber_diffs")
          and D["accounts"]["C_main_flow"]["routes_nogold_to_nogold"]
          and D["accounts"]["C_main_flow"][
              "falsified_variant_has_no_nogold_branch"])
    ck("C6 验锚 OK 3/3 + 主流程 nogold 路由有牙（事故版撤回即红）",
       c6 and not withdraw == "C6", str(anchor))


def main() -> int:
    print("=" * 74)
    print("r111 / README 190：189 两本账的 k 值覆盖缺口（ok_nogold）")
    print("=" * 74)
    print("== A 组：覆盖矩阵 ==")
    print("== B 组：135 主账逐条同源重放 / 边界 ==")
    V = conditions(D)
    FAILS.extend(emit(V))
    run_c()
    print("=" * 74)
    if FAILS:
        print("❌ FAILS=" + ",".join(FAILS))
        return 1
    print(f"✅ 全部通过（A/B {len(V)} 项 + C 组 6 项）")
    print("189 反证组覆盖 3/4 个 k；缺的 ok_nogold 恰是 135 主账"
          "唯一非 none 的 k；主流程按 k 分到 nogold 档 ⇒ 不是产品洞")
    return 0


if __name__ == "__main__":
    sys.exit(main())
