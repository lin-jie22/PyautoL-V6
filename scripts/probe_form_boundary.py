#!/usr/bin/env python3
"""r108 / 187：186 结论的**适用边界**普查（纯只读取证，跑一次出存档）。

═══ 起点 ═══
186 用 4 条 `is` 合成样本得出「判据层本就能判 `is`，门在装载门」。
⛔ 但 4 条样本只证明产品沙箱 truthy 分支 `bool(got)` 能**求值**整条
bool 表达式；「判对」的期望值就是 Python 里同一表达式的真值 ⇒
同义反复，**不等于判据层有鉴别力**（第 122 条：构造必然≠证据）。

本轮把这件事做成可回归的两本账 + 一条适用边界：

  A 静态形态账  119 条真实 `expr`，**纯 AST、一律不喂沙箱**（§183 禁令）
  B 合成求值账  10 形态 × 2 条件 = 20 条自造样本，`passed == bool(eval)`
  C 合成鉴别账  10 形态 × 同一表达式喂两段自造源码 ⇒ 鉴别力
  D 禁令边界账  真实 expr 沙箱运行次数恒 0、不报 F2P / 下游 / 收益

⛔⛔ 严守 §183：179 的 119 条 form 下游**属拍板项 ①**，本轮不碰。
原始 `expr` 依赖测试局部作用域，183 已证直接跑是构造必然。
B/C 两账全部是**自造合成样本**，与 119 条真实 expr 无交集。

⛔ 分母与形态集合全部**同源**取自 179 存档与 179 尺子的 `UNITTEST`
映射，不另写第二份词表（第 68 条）。不改 `aether/`、不回写上游存档、
不改判据、不改安全边界、不宣布扩张无用 / 有效。
"""
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aether.environment import run_in_sandbox                    # noqa: E402
from probe_form_shape_attribution import SHAPES, UNITTEST        # noqa: E402
from realrepo_baseline import require_baseline                   # noqa: E402

SOURCE_FORM = ROOT / "scripts" / "form_shape_attribution.json"
OUT = ROOT / "scripts" / "form_boundary_census.json"

#: 口径签名含**尺子自身**（174 教训）。全是**只读**引用，不写回。
CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    "aether/environment.py",
    "scripts/probe_form_shape_attribution.py",
    "scripts/probe_extract_downstream.py",
    "scripts/form_shape_attribution.json",
    "scripts/extract_downstream.json",
    "scripts/realrepo_baseline.json",
    "scripts/probe_is_shape.py",
    "scripts/is_shape_census.json",
    "scripts/probe_form_boundary.py",
)

#: ⛔ 自造合成样本：同一条表达式喂两段源码。
#:    全部是**整条 bool 表达式**，不依赖 self / 测试局部状态；
#:    与 179 的 119 条真实 `expr` 无交集。
SHAPES_SYNTH: Tuple[Tuple[str, str, str, str], ...] = (
    ("is", "V = None\n", "V = 1\n", "V is None"),
    ("is_not", "W = object()\nV = None\n", "W = object()\nV = W\n", "V is not W"),
    ("truthy", "V = [1]\n", "V = []\n", "V"),
    ("in", "V = [1, 2]\n", "V = [9]\n", "2 in V"),
    ("not_in", "V = [9]\n", "V = [2]\n", "2 not in V"),
    ("chained_compare", "V = 2\n", "V = 0\n", "1 < V < 3"),
    ("order", "V = 1\n", "V = 3\n", "V <= 2"),
    ("ne", "V = 1\n", "V = 2\n", "V != 2"),
    ("not_expr", "V = []\n", "V = [1]\n", "not V"),
    ("isinstance", "V = 1\n", "V = 'x'\n", "isinstance(V, int)"),
)

#: 求值账：每条形态再配一个「反向条件」，证明不是恒真也不是恒假。
EVAL_EXTRA: Tuple[Tuple[str, str, str], ...] = (
    ("is", "V = 1\n", "V is None"),
    ("truthy", "V = []\n", "V"),
    ("in", "V = [9]\n", "2 in V"),
    ("chained_compare", "V = 0\n", "1 < V < 3"),
    ("order", "V = 3\n", "V <= 2"),
    ("ne", "V = 2\n", "V != 2"),
    ("not_expr", "V = [1]\n", "not V"),
    ("isinstance", "V = 'x'\n", "isinstance(V, int)"),
    ("is_not", "W = object()\nV = W\n", "V is not W"),
    ("not_in", "V = [2]\n", "2 not in V"),
)


def sha256_file(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "__MISSING__"
    return hashlib.sha256(p.read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {f: sha256_file(f) for f in CALIBER_FILES}


def local_bool(src: str, call: str) -> Any:
    """同源真值：`exec` 同一段自造源码，再 `eval` 同一条表达式。"""
    ns: Dict[str, Any] = {"__builtins__": __builtins__}
    try:
        exec(src, ns)
        return bool(eval(call, ns))
    except Exception as e:                                    # noqa: BLE001
        return "ERR:%s" % e


def sandbox_bool(src: str, call: str, timeout: float) -> Tuple[bool, str]:
    """⛔ 判据同源：直接调产品 `aether.environment.run_in_sandbox`，不另写沙箱。"""
    ex = run_in_sandbox(src, [{"name": "t0", "call": call}], timeout=timeout)
    r = ex.results[0]
    return bool(r.get("passed")), str(ex.import_error or "")


# ─────────────────── A 账：静态形态（不喂沙箱） ───────────────────
def classify_static(row: Dict[str, Any]) -> str:
    """四格分类。⛔ `UNITTEST` 用 179 尺子自己的映射，不另写词表。"""
    attr = row.get("attr_product") or ""
    if attr == "raises":
        return "stmt_raises"
    if attr == "assertCountEqual":
        return "multiset"
    if attr == "assert":
        return "bare_bool_expr"
    if attr in UNITTEST:
        return "needs_rewrite"
    return "unknown"


def ast_kind_of(expr: str) -> str:
    """纯静态：`eval` 模式能解析 = 表达式；否则 `exec` 模式 = 语句。"""
    try:
        ast.parse(expr, mode="eval")
        return "expr"
    except SyntaxError:
        try:
            ast.parse(expr, mode="exec")
            return "stmt"
        except SyntaxError:
            return "unparseable"


def static_account(form_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    # ⛔ 先逐行落原始事实，所有聚合再从 rows 派生（derive 不许读预聚合）。
    rows = [{
        "i": i, "shape": r["shape"], "attr_product": r.get("attr_product"),
        "expr": r["expr"], "class": classify_static(r),
        "ast_kind": ast_kind_of(r["expr"]),
        "sandbox_run": False,                       # ⛔ D 账：恒不喂沙箱
    } for i, r in enumerate(form_rows, 1)]
    by_class = dict(collections.Counter(x["class"] for x in rows))
    by_shape = dict(collections.Counter(x["shape"] for x in rows))
    counts = collections.Counter(x["ast_kind"] for x in rows)
    ast_kind = {k: counts.get(k, 0) for k in ("expr", "stmt", "unparseable")}
    return {
        "rows": rows,
        "n_rows": len(rows),
        "by_class": by_class,
        "by_shape": by_shape,
        "n_shapes": len(by_shape),
        "ast_kind": ast_kind,
        "note": "纯静态 AST；119 条真实 expr 一律不喂沙箱（§183 禁令）。"
                "bare_bool_expr 只是形态上已是整条 bool 表达式，"
                "仍依赖测试局部作用域，绝不等于可跑出 F2P。",
    }


# ─────────────────── B/C 账：自造合成样本 ───────────────────
def eval_account(timeout: float) -> Dict[str, Any]:
    cases = [(s, f, c) for s, f, _b, c in SHAPES_SYNTH] + list(EVAL_EXTRA)
    rows = []
    for shape, src, call in cases:
        got, ie = sandbox_bool(src, call, timeout)
        want = local_bool(src, call)
        rows.append({"shape": shape, "src": src, "call": call,
                     "want": want, "got": got, "import_error": ie,
                     "eval_ok": (got == want and not ie)})
    return {"rows": rows, "n_rows": len(rows),
            "n_ok": sum(1 for x in rows if x["eval_ok"]),
            "note": "求值账：`passed == bool(eval)` 是同义反复，只证沙箱忠实求值。"}


def discriminate_account(timeout: float) -> Dict[str, Any]:
    rows = []
    for shape, sf, sb, call in SHAPES_SYNTH:
        pf, ie_f = sandbox_bool(sf, call, timeout)
        pb, ie_b = sandbox_bool(sb, call, timeout)
        lf, lb = local_bool(sf, call), local_bool(sb, call)
        rows.append({"shape": shape, "call": call,
                     "passed_fixed": pf, "passed_buggy": pb,
                     "local_fixed": lf, "local_buggy": lb,
                     "import_error_fixed": ie_f, "import_error_buggy": ie_b,
                     "eval_ok": (pf == lf and pb == lb and not ie_f and not ie_b),
                     "f2p_synth": bool(pf) and not bool(pb)})
    return {"rows": rows, "n_rows": len(rows),
            "n_eval_ok": sum(1 for x in rows if x["eval_ok"]),
            "n_f2p": sum(1 for x in rows if x["f2p_synth"]),
            "note": "鉴别账：同一表达式、两段自造源码。⛔ 源码差异是构造的，"
                    "不是真实 commit 收益；只用来证「形态不构成结构性障碍」。"}


def falsification(timeout: float) -> Dict[str, Any]:
    """三组反证：证明两本账可分离、尺子不是恒真。"""
    # R1 同源码 ⇒ 鉴别账必须全 0
    r1 = 0
    for _s, sf, _sb, call in SHAPES_SYNTH:
        pf, _ = sandbox_bool(sf, call, timeout)
        pb, _ = sandbox_bool(sf, call, timeout)
        r1 += int(bool(pf) and not bool(pb))
    # R2 常量 True ⇒ 求值账仍全对、鉴别账全 0（两本账可分离）
    r2_eval, r2_f2p = 0, 0
    for _s, sf, sb, _call in SHAPES_SYNTH:
        pf, _ = sandbox_bool(sf, "True", timeout)
        pb, _ = sandbox_bool(sb, "True", timeout)
        r2_eval += int(pf == local_bool(sf, "True") and pb == local_bool(sb, "True"))
        r2_f2p += int(bool(pf) and not bool(pb))
    # R3 期望反转 ⇒ 「判对」归零
    r3 = 0
    for _s, sf, sb, call in SHAPES_SYNTH:
        pf, _ = sandbox_bool(sf, call, timeout)
        pb, _ = sandbox_bool(sb, call, timeout)
        r3 += int(pf == (not local_bool(sf, call))
                  and pb == (not local_bool(sb, call)))
    return {"r1_same_src_f2p": r1, "r2_const_eval_ok": r2_eval,
            "r2_const_f2p": r2_f2p, "r3_reversed_correct": r3,
            "n_shapes": len(SHAPES_SYNTH),
            "r1_ok": r1 == 0,
            "r2_ok": r2_eval == len(SHAPES_SYNTH) and r2_f2p == 0,
            "r3_ok": r3 == 0}


def derive(a: Dict[str, Any], b: Dict[str, Any],
           c: Dict[str, Any]) -> Dict[str, Any]:
    """⛔ 纯函数：**每一项都从 rows 逐行派生**，绝不读预聚合字段。

    B4 曾抓到过「读 by_class ⇒ 喂过滤后的 rows 却不变」的不纯粹；
    按第 34 条修尺子本身，不放宽契约断言。
    """
    ar = a["rows"]
    by_class = collections.Counter(x["class"] for x in ar)
    kinds = collections.Counter(x["ast_kind"] for x in ar)
    return {
        "n_static": len(ar),
        "n_shapes": len({x["shape"] for x in ar}),
        "bare": by_class.get("bare_bool_expr", 0),
        "rewrite": by_class.get("needs_rewrite", 0),
        "multiset": by_class.get("multiset", 0),
        "stmt": by_class.get("stmt_raises", 0),
        "n_expr": kinds.get("expr", 0),
        "n_stmt": kinds.get("stmt", 0),
        "n_eval_ok": sum(1 for x in b["rows"] if x["eval_ok"]),
        "n_eval_rows": len(b["rows"]),
        "n_f2p": sum(1 for x in c["rows"] if x["f2p_synth"]),
        "n_disc_rows": len(c["rows"]),
        "n_sandbox_on_real_expr": sum(1 for x in ar if x["sandbox_run"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-baseline-gate", action="store_true",
                    help="仅供离线反证；正式跑必须验锚")
    args = ap.parse_args()

    if args.no_baseline_gate:
        health: Dict[str, Any] = {"ok": None, "skipped": True}
    else:
        health = require_baseline()
        if not health.get("ok"):
            print("BASELINE_GATE_FAIL", health.get("diffs"))
            raise SystemExit(2)

    form = json.loads(SOURCE_FORM.read_text(encoding="utf-8"))
    rows = form["rows"]
    A = static_account(rows)
    B = eval_account(args.timeout)
    C = discriminate_account(args.timeout)
    F = falsification(args.timeout)
    D = {"n_real_expr_sandbox_runs": sum(1 for x in A["rows"] if x["sandbox_run"]),
         "reports_f2p_on_real_expr": False,
         "reports_downstream_attribution": False,
         "note": "§183：179 的 119 条 form 下游属拍板项 ①；本轮零沙箱、零收益读数。"}
    d = derive(A, B, C)

    closure = {
        "eq_denominator": d["n_static"] == 119,
        "eq_shapes": d["n_shapes"] == 13,
        "eq_class_split": (d["bare"] + d["rewrite"] + d["multiset"] + d["stmt"]
                           == d["n_static"]),
        "eq_ast_split": d["n_expr"] + d["n_stmt"] == d["n_static"],
        "eq_stmt_is_raises": d["n_stmt"] == d["stmt"] == 4,
        "eq_eval": d["n_eval_ok"] == d["n_eval_rows"] == 20,
        "eq_disc": d["n_f2p"] == d["n_disc_rows"] == 10,
        "eq_eval_not_discriminate": (F["r2_ok"] and F["r1_ok"] and F["r3_ok"]),
        "eq_no_real_sandbox": d["n_sandbox_on_real_expr"] == 0
                              and D["reports_f2p_on_real_expr"] is False,
        "eq_caliber_self": "scripts/probe_form_boundary.py" in caliber(),
        "eq_baseline": health.get("ok") is True,
        "eq_four_accounts": d["n_static"] == 119 and d["n_eval_rows"] == 20
                            and d["n_disc_rows"] == 10
                            and D["n_real_expr_sandbox_runs"] == 0,
    }

    out = {
        "schema": "form_boundary_census_v1",
        "caliber": caliber(),
        "baseline_health": {"ok": health.get("ok"),
                            "diffs": health.get("diffs", []),
                            "caliber_diffs": health.get("caliber_diffs", [])},
        "accounts": {"A_static_form": A, "B_synth_eval": B,
                     "C_synth_discriminate": C, "D_183_boundary": D},
        "falsification": F,
        "derived": d,
        "closure": closure,
        "summary": {
            "n_form_rows": 119, "n_shapes": 13,
            "n_bare_bool_expr": d["bare"], "n_needs_rewrite": d["rewrite"],
            "n_multiset": d["multiset"], "n_stmt_raises": d["stmt"],
            "n_eval_ok": d["n_eval_ok"], "n_f2p_synth": d["n_f2p"],
            "all_checks": all(closure.values()),
            "conclusion": "判据层对 10 种 bool 表达式形态无结构性障碍；"
                          "但「求值判对」≠「鉴别力」，且 4 条 raises 连表达式都不是。"
                          "186 的结论适用于已展开的整条 bool 表达式，不外推到 119 条真实 expr。",
            "not_said": "不宣布扩张判据形态无用 / 有效；不报 119 条下游归因；"
                        "不改 aether/ 一字节；其余 4 项拍板未开工。",
        },
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")
    print("WROTE", args.out)
    print("  baseline =", health.get("ok"), "diffs =", health.get("diffs", []))
    print("  A static : n=%d shapes=%d class=%s ast=%s"
          % (d["n_static"], d["n_shapes"], A["by_class"], A["ast_kind"]))
    print("  B eval   : %d/%d correct" % (d["n_eval_ok"], d["n_eval_rows"]))
    print("  C disc   : f2p %d/%d" % (d["n_f2p"], d["n_disc_rows"]))
    print("  R1/R2/R3 :", F["r1_ok"], F["r2_ok"], F["r3_ok"])
    print("  D 183    : real expr sandbox runs =", D["n_real_expr_sandbox_runs"])
    print("  closure  :", all(closure.values()),
          [k for k, v in closure.items() if not v])


if __name__ == "__main__":
    main()
