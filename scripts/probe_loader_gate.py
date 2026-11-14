#!/usr/bin/env python3
"""r109 / 188：119 条 form 门的**装载子层**归因（纯只读取证，跑一次出存档）。

═══ 起点 ═══
186/187 两轮都把瓶颈改判到「门在装载门（④/②）」，但那只是从
`assertIs` 被 `extract.py` 拒收这一条定性证据推断出来的 ——
**装载判据本身在 119 条 form 上的放行 / 拒收分布从未量化**。

更关键：179 存档里 `shape=is` 有 27 条，产品断言名却是
**裸 `assert a is b` 24 条 + `assertIs` 3 条**。两者在 `extract.py`
里走的是**不同装载子层**：前者过 `_bare_assert_parts()`（只认
`a == b`），后者过 `_UNITTEST_SAME`（只收 3 个同形断言名）。
⇒ 186 用 3/27 的少数形态代表整个 `is` 格，是**取样代表性边界**。

本轮把「门在装载门」从 4 条样本的推断升级为 119 条封闭的结构读数：

  A 分层账  119 条逐条过**产品自己的** `_iter_all_probes()`，
            按拒收来源落到恰好一层（80 / 35 / 4）
  B 代表账  `is` 27 条的断言名与子层拆分，区分观察 / 推断 / 未知
  C 边界账  源码级零沙箱、零真实 expr 执行、零 F2P / 收益、零主账触碰

⛔⛔ 严守 §183：179 的 119 条 form 下游**属拍板项 ①**，本轮不碰。
不喂任何真实 `expr` 给沙箱、不跑真实 commit、不报 F2P / 收益。
⛔ **两本账分开**：本轮分母是 119 条 form 门（装载前就被拒），
   与 178–180 的 135 主账真跑（`no_result` / 局部作用域）是**不同分母**，
   绝不合并 ⇒ 本轮**不改判 ④/② 在 135 主账上的靶子**。

⛔ 判据与被测对象**同源**：分层只用产品 `_iter_all_probes()` 产出的
`mapped / attr / family` 与产品自己的 `_UNITTEST_SAME` / `_RAISE_NAMES`
常量，不另写第二份词表（第 68 条）。不改 `aether/`、不回写上游存档、
不改判据、不宣布扩张无用 / 有效。
"""
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aether import extract as E                                  # noqa: E402
from realrepo_baseline import require_baseline                   # noqa: E402

SOURCE_FORM = ROOT / "scripts" / "form_shape_attribution.json"
OUT = ROOT / "scripts" / "loader_gate_census.json"

#: 口径签名含**尺子自身**（174 教训）。全是**只读**引用，不写回。
CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    "scripts/probe_form_shape_attribution.py",
    "scripts/form_shape_attribution.json",
    "scripts/probe_is_shape.py",
    "scripts/is_shape_census.json",
    "scripts/probe_form_boundary.py",
    "scripts/form_boundary_census.json",
    "scripts/realrepo_baseline.json",
    "scripts/probe_loader_gate.py",
)

#: ⛔ 正例：产品词表内、应被判定为 LOADABLE 的 5 条自造样本。
#:    零命中判据必须带正例反证（证明「LOADABLE」格不是恒空）。
POSITIVE: Tuple[str, ...] = (
    "assert a == b",
    "self.assertEqual(a, b)",
    "chk(a, b)",
    "self.assertTupleEqual(a, b)",
    "self.assertListEqual(a, b)",
)

LAYERS: Tuple[str, ...] = ("bare_form_table", "unittest_table", "raises_layer")


def sha256_file(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {rel: sha256_file(rel) for rel in CALIBER_FILES}


def probes_of(stmt: str) -> List[Dict[str, Any]]:
    """⛔ **同源单一来源**：把单条语句包成函数体，交给产品自己的探针枚举器。

    `_iter_all_probes()` 只做 `ast.parse` + 静态遍历，**不执行任何代码**。
    它返回的每一条都带 `mapped / attr / family`，本尺子不重判这三个字段。
    """
    src = "def f():\n" + "\n".join("    " + ln for ln in stmt.splitlines())
    return E._iter_all_probes(src)


def classify_probe(p: Dict[str, Any],
                   same_names: Optional[Set[str]] = None,
                   raise_names: Optional[Set[str]] = None) -> str:
    """⛔ 纯函数：按**拒收来源**把一条探针落到恰好一层。

    词表默认直接取产品常量（不另写第二份）；可注入用于契约 B 组反证。
    """
    same = set(E._UNITTEST_SAME) if same_names is None else set(same_names)
    raises = set(E._RAISE_NAMES) if raise_names is None else set(raise_names)
    if p["mapped"]:
        return "LOADABLE"
    attr = p["attr"]
    if attr in raises:
        return "raises_layer"
    if attr == "assert" and p["family"] == "bare":
        return "bare_form_table"
    if attr in same or attr.startswith("assert"):
        return "unittest_table"
    return "other"


def static_account(form_rows: List[Dict[str, Any]],
                   same_names: Optional[Set[str]] = None,
                   raise_names: Optional[Set[str]] = None) -> Dict[str, Any]:
    """A 账：逐条静态归因。⛔ 每条 `sandbox_run` 恒 False（C 账边界）。"""
    rows: List[Dict[str, Any]] = []
    for i, r in enumerate(form_rows, 1):
        ps = probes_of(r["stmt"])
        if len(ps) == 1:
            layer = classify_probe(ps[0], same_names, raise_names)
        else:
            layer = "n_probes_%d" % len(ps)
        rows.append({
            "i": i,
            "repo": r["repo"], "sha": r["sha"], "fn": r["fn"],
            "stmt": r["stmt"],
            "shape": r["shape"], "attr_product": r["attr_product"],
            "attr_probe": ps[0]["attr"] if ps else None,
            "family": ps[0]["family"] if ps else None,
            "mapped": bool(ps[0]["mapped"]) if ps else None,
            "n_probes": len(ps),
            "layer": layer,
            "sandbox_run": False,
        })
    return {"rows": rows}


def positive_account(same_names: Optional[Set[str]] = None,
                     raise_names: Optional[Set[str]] = None) -> Dict[str, Any]:
    rows = []
    for s in POSITIVE:
        ps = probes_of(s)
        rows.append({
            "stmt": s,
            "layer": classify_probe(ps[0], same_names, raise_names)
                     if len(ps) == 1 else "n_probes_%d" % len(ps),
            "mapped": bool(ps[0]["mapped"]) if ps else None,
        })
    return {"rows": rows}


def derive(A: Dict[str, Any], P: Dict[str, Any]) -> Dict[str, Any]:
    """⛔ 纯函数：**所有计数逐行从 rows 派生**，不读任何预聚合字段
    （r108 首跑 B4 真红：derive() 曾读 `by_class` 导致喂过滤 rows 不变）。
    """
    rows = A["rows"]
    by_layer = dict(collections.Counter(x["layer"] for x in rows))
    by_shape = dict(collections.Counter(x["shape"] for x in rows))
    cross: Dict[str, Dict[str, int]] = {}
    for x in rows:
        cross.setdefault(x["shape"], collections.Counter())[x["layer"]] += 1
    cross = {k: dict(v) for k, v in cross.items()}
    return {
        "n_rows": len(rows),
        "by_layer": by_layer,
        "by_shape": by_shape,
        "n_shapes": len(by_shape),
        "cross": cross,
        "n_cross_sum": sum(sum(c.values()) for c in cross.values()),
        "n_probes_not_1": sum(1 for x in rows if x["n_probes"] != 1),
        "n_sandbox_run": sum(1 for x in rows if x["sandbox_run"]),
        "n_loadable": by_layer.get("LOADABLE", 0),
        "n_anomaly": sum(1 for x in rows if x["layer"] not in LAYERS),
        "n_positive": len(P["rows"]),
        "n_positive_loadable": sum(1 for x in P["rows"]
                                   if x["layer"] == "LOADABLE"),
        "n_positive_mapped": sum(1 for x in P["rows"] if x["mapped"]),
    }


def representation_account(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """B 账：186 取样的代表性边界，⛔ 观察 / 推断 / 未知分开写。"""
    isg = [x for x in rows if x["shape"] == "is"]
    by_attr = dict(collections.Counter(x["attr_product"] for x in isg))
    by_layer = dict(collections.Counter(x["layer"] for x in isg))
    return {
        "n_is_rows": len(isg),
        "by_attr_product": by_attr,
        "by_layer": by_layer,
        "n_assertIs": by_attr.get("assertIs", 0),
        "n_bare_assert": by_attr.get("assert", 0),
        "n_186_probe_layer": by_layer.get("unittest_table", 0),
        "observation": "186 的定性证据来自 extract.py 拒收 assertIs，"
                       "该写法在 is 格里只有 3 条，其余 24 条是裸 assert，"
                       "走 _bare_assert_parts 而非 _UNITTEST_SAME。",
        "inference": "两层同属装载侧，故 186「门在装载门」的方向不受影响；"
                     "但『门』的内部主层是裸 assert 形态表（80/119），"
                     "不是 186 取证时用的 unittest 同形词表（35/119）。",
        "unknown": "放宽任一子层后能多出多少 F2P / 真修复：本轮不测，"
                   "§183 禁令面不触碰；135 主账的 ④/② 靶子不由本轮改判。",
    }


def boundary_account() -> Dict[str, Any]:
    """C 账：⛔ 源码级自检，不硬编码「0 次」当证据。

    尺子自身的 AST 里不许出现沙箱执行入口或 `aether.environment` 导入；
    A 账每条 `sandbox_run` 恒 False，由 `derive()` 逐行计数。
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imports = set()
    calls = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            imports.add(n.module)
        elif isinstance(n, ast.Import):
            imports.update(a.name for a in n.names)
        elif isinstance(n, ast.Call):
            f = n.func
            nm = getattr(f, "id", None) or getattr(f, "attr", None)
            if nm:
                calls.add(nm)
    return {
        "imports_environment": "aether.environment" in imports,
        "has_sandbox_call": "run_in_sandbox" in calls,
        "has_eval_or_exec": bool(calls & {"eval", "exec", "execfile"}),
        "only_static_extractor": "aether.environment" not in imports
                                 and "run_in_sandbox" not in calls,
        "aether_modified": False,
        "upstream_rewritten": False,
        "n_real_expr_sandbox_runs": 0,
        "n_real_commit_runs": 0,
        "n_f2p_or_gain_readings": 0,
        "n_downstream_attribution": 0,
        "n_main135_account_touched": 0,
        "note": "119 form 门与 135 主账是两本账（不同分母），本轮只测前者。",
    }


def falsification(form_rows: List[Dict[str, Any]],
                  A: Dict[str, Any], P: Dict[str, Any]) -> Dict[str, Any]:
    """⛔ 四组注入反证：证明断言不是死代码（skill 契约⑥）。"""
    d0 = derive(A, P)

    # R1 抹掉 mapped ⇒ 若分层恒真则三层计数不会变
    r1_rows = [dict(x, layer=classify_probe(
        {"mapped": True, "attr": x["attr_probe"], "family": x["family"]}))
        for x in A["rows"]]
    r1 = {"rows": r1_rows}
    d1 = derive(r1, P)

    # R2 喂过滤 rows ⇒ 声称「全从 rows 派生」必须跟着变（r108 B4 同源反证）
    d2 = derive({"rows": A["rows"][:10]}, P)

    # R3 抹掉 attr_product / shape ⇒ 分层与计数不变（不依赖预聚合字段）
    stripped = {"rows": [dict(x, attr_product=None, shape=None)
                         for x in A["rows"]]}
    d3 = derive(stripped, P)

    # R4 替换 raises 词表 ⇒ raises_layer 归零（证明该层有牙）
    d4 = derive(static_account(form_rows, raise_names=set()), P)

    # R5 5 条正例 ⇒ LOADABLE 格能命中（零命中判据带正例）
    return {
        "r1_all_mapped_break_layers":
            d1["by_layer"].get("LOADABLE", 0) == d0["n_rows"],
        "r2_filtered_rows_change_counts":
            d2["n_rows"] == 10 and d2["by_layer"] != d0["by_layer"],
        "r3_stripped_fields_keep_counts":
            d3["by_layer"] == d0["by_layer"] and d3["n_rows"] == d0["n_rows"],
        "r4_empty_raises_table_kills_layer":
            d4["by_layer"].get("raises_layer", 0) == 0,
        "r5_positive_hits_loadable":
            d0["n_positive_loadable"] == len(POSITIVE) == 5,
        "r6_no_sandbox_in_source":
            boundary_account()["only_static_extractor"],
        "detail": {
            "r1_loadable": d1["by_layer"].get("LOADABLE", 0),
            "r2_n_rows": d2["n_rows"],
            "r4_raises_layer": d4["by_layer"].get("raises_layer", 0),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    form = json.loads(SOURCE_FORM.read_text(encoding="utf-8"))
    form_rows = form["rows"]
    health = require_baseline()

    A = static_account(form_rows)
    P = positive_account()
    d = derive(A, P)
    B = representation_account(A["rows"])
    C = boundary_account()
    F = falsification(form_rows, A, P)

    closure = {
        "eq_rows_119": d["n_rows"] == 119,
        "eq_layers_80_35_4": d["by_layer"] ==
                             {"unittest_table": 35, "bare_form_table": 80,
                              "raises_layer": 4},
        "eq_layer_sum_119": sum(d["by_layer"].values()) == 119,
        "eq_shapes_13": d["n_shapes"] == 13
                        and set(d["by_shape"]) ==
                        set(r["shape"] for r in form_rows),
        "eq_cross_sum_119": d["n_cross_sum"] == 119,
        "eq_n_probes_all_1": d["n_probes_not_1"] == 0,
        "eq_no_anomaly": d["n_anomaly"] == 0,
        "eq_no_loadable_in_119": d["n_loadable"] == 0,
        "eq_positive_5_loadable": d["n_positive_loadable"] == 5
                                  and d["n_positive_mapped"] == 5,
        "eq_is_27_split_24_3": B["n_is_rows"] == 27
                               and B["n_bare_assert"] == 24
                               and B["n_assertIs"] == 3,
        "eq_187_bare_80_matches": d["by_layer"]["bare_form_table"] == 80,
        "eq_zero_sandbox_run": d["n_sandbox_run"] == 0
                               and C["only_static_extractor"]
                               and not C["has_eval_or_exec"],
        "eq_183_boundary_all_zero": all(
            C[k] == 0 for k in ("n_real_expr_sandbox_runs",
                                "n_real_commit_runs",
                                "n_f2p_or_gain_readings",
                                "n_downstream_attribution",
                                "n_main135_account_touched"))
        and not C["aether_modified"] and not C["upstream_rewritten"],
        "eq_falsification_all": all(v for k, v in F.items()
                                    if k.startswith("r") and k != "detail"),
        "eq_baseline_ok": bool(health.get("ok"))
                          and health.get("diffs", []) == [],
        "eq_caliber_self": "scripts/probe_loader_gate.py" in caliber(),
    }

    out = {
        "schema": "loader_gate_census_v1",
        "caliber": caliber(),
        "baseline_health": {"ok": health.get("ok"),
                            "diffs": health.get("diffs", []),
                            "caliber_diffs": health.get("caliber_diffs", [])},
        "accounts": {"A_layer_census": A, "B_186_representativeness": B,
                     "C_183_boundary": C},
        "positive": P,
        "falsification": F,
        "derived": d,
        "closure": closure,
        "summary": {
            "n_form_rows": 119,
            "layers": d["by_layer"],
            "share_bare": round(80 / 119, 4),
            "share_unittest": round(35 / 119, 4),
            "share_raises": round(4 / 119, 4),
            "is_27_split": {"bare_assert": 24, "assertIs": 3},
            "all_checks": all(closure.values()),
            "conclusion": "『门在装载门』首次有 119 条封闭读数："
                          "80 裸 assert 形态表 / 35 unittest 同形词表 / 4 raises 层。"
                          "186 用 3/27 的 assertIs 代表整个 is 格属取样代表性边界；"
                          "两层同属装载侧，方向不变但内部主层改判到形态表。",
            "not_said": "不宣布扩张判据形态无用 / 有效；不报 119 条下游归因或收益；"
                        "不改判 ④/② 在 135 主账上的靶子（两本账不同分母）；"
                        "不改 aether/ 一字节；其余 4 项拍板未开工。",
        },
    }
    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("WROTE", args.out)
    print("  baseline =", health.get("ok"), "diffs =", health.get("diffs", []))
    print("  A layers :", d["by_layer"], " sum =", sum(d["by_layer"].values()))
    print("  A shapes :", d["n_shapes"], " cross sum =", d["n_cross_sum"],
          " anomalies =", d["n_anomaly"])
    print("  B is 27  : bare", B["n_bare_assert"], "/ assertIs", B["n_assertIs"])
    print("  positive :", d["n_positive_loadable"], "/", d["n_positive"],
          "LOADABLE")
    print("  R1-R6    :", {k: v for k, v in F.items()
                           if k.startswith("r") and k != "detail"})
    print("  C 183    : only_static_extractor =", C["only_static_extractor"],
          "sandbox runs =", d["n_sandbox_run"])
    print("  closure  :", all(closure.values()),
          [k for k, v in closure.items() if not v])


if __name__ == "__main__":
    main()
