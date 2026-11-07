#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""179：119 条 form 账的「判据形态需求」结构化归因（纯静态描述性分类）。

═══ ⛔ 本脚本做什么、不做什么 ═══

✅ 以产品 `aether/extract.py:_iter_all_probes` 的 **`mapped=False`** 为分母
   （⛔ 第 149 条：这是「要不要扩张判据形态」的唯一依据），对**同源**原始
   `expr` 做描述性 AST 分类，每条落到封闭集合里**恰好一格**，并与
   `reach` / `v175` / `attr` / `repo` 正交交叉记账。

⛔ **不跑沙箱、不产出判据、不报收益、不改 `aether/` 一个字节**。
   177 已把 form 账一律判 `unmeasurable`（产品没有探针表达式 ⇒ 不同源、
   不跑沙箱）；本轮**不推翻**它，只回答「被拒收的断言需要哪种形态」。
   `reach` 是**事后 oracle**，不能当 predictor（⛔ 只交叉、不推断收益）。

⛔ **不许另写第二把尺子**（175 教训）：分母与原始 `expr` 全部取自
   `probe_extract_downstream.probe_index`（内部调产品 `_iter_all_probes`），
   并与 177 存档 `expr` 逐字对拍；分类只是**描述性标签**，不回写 `mapped`、
   不改变任何一条判据能不能进来。两本账正交，绝不合并成布尔。

⛔ `SHAPES` 是**封闭集合**：出现集合外的格 ⇒ fail-closed 报错，
   不许为了迁就读数临时新增格（166 纪律）。量具格单列、不参与归因推断。
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

from probe_extract_downstream import probe_index          # noqa: E402
from probe_alias_demote import select_commits              # noqa: E402
from realrepo_baseline import require_baseline            # noqa: E402

REPOS: Tuple[Tuple[str, str], ...] = (
    ("boltons", "boltons/"),
    ("more-itertools", "more_itertools/"),
    ("attrs", "src/attr/"),
)
SOURCE = ROOT / "scripts" / "extract_downstream.json"

#: ⛔⛔ 封闭集合（166 纪律）。前 14 个是实格，后 5 个是**量具格**
#:    （正常读数应全为 0；非 0 ⇒ 量具坏了，不许拿它做归因推断）。
SHAPES: Tuple[str, ...] = (
    "truthy",             #: 裸布尔 / assertTrue / assertFalse
    "in",                 #: a in b / assertIn
    "not_in",             #: a not in b / assertNotIn
    "ne",                 #: a != b
    "is",                 #: a is b / assertIs
    "is_not",             #: a is not b / assertIsNot
    "order",              #: < <= > >= / assertLessEqual
    "chained_compare",    #: a == b == c（产品只认单 op Compare）
    "boolop",             #: a and b / a or b
    "not_expr",           #: not X
    "multiset",           #: assertCountEqual
    "isinstance",         #: assertIsInstance
    "raises_match",       #: pytest.raises(..., match=...)
    "raises_other",       #: raises 但无 match=（含块内是语句的探针）
    #: ─── 量具格 ───
    "eq_unexpected",      #: 单 op Eq 竟落在 form（产品本该收）⇒ 口径漂移
    "other_compare",      #: 未知比较 op
    "unparseable",        #: expr 解析不了
    "other",              #: 未知 unittest attr
    "unmatched",          #: probe_index 没命中 / expr 不同源 / mapped 不是 False
)
MEASUREMENT_GATES = frozenset(SHAPES[14:])

#: 裸 assert 的比较运算 → 形态（⛔ 产品 `_bare_assert_parts` 只认单 op Eq）
OPS: Dict[str, str] = {
    "Eq": "eq_unexpected", "NotEq": "ne", "In": "in", "NotIn": "not_in",
    "Is": "is", "IsNot": "is_not", "Lt": "order", "LtE": "order",
    "Gt": "order", "GtE": "order",
}
#: unittest 断言方法名 → 形态（产品 `_UNITTEST_SAME` 之外的全被拒收）
UNITTEST: Dict[str, str] = {
    "assertTrue": "truthy", "assertFalse": "truthy",
    "assertIn": "in", "assertNotIn": "not_in",
    "assertIs": "is", "assertIsNot": "is_not",
    "assertCountEqual": "multiset", "assertIsInstance": "isinstance",
    "assertLessEqual": "order", "assertLess": "order",
    "assertGreater": "order", "assertGreaterEqual": "order",
}

CALIBER_FILES = (
    "aether/extract.py",                       #: 形态表单一来源
    "scripts/probe_alias_demote.py",           #: select_commits
    "scripts/probe_extract_downstream.py",     #: probe_index + form 存档
    "scripts/probe_criteria_gate_census.py",   #: 176 尺子
    "scripts/probe_form_shape_attribution.py", #: ⛔ 钉尺子自身（174 教训）
    "scripts/criteria_gate_census.json",
    "scripts/extract_downstream.json",
    "scripts/realrepo_baseline.json",
)


# ─────────────────────────── 纯函数（供契约离线反证） ───────────────────────────
def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {f: sha256_file(ROOT / f) for f in CALIBER_FILES}


def has_match_kw(stmt: str) -> bool:
    """`with pytest.raises(..., match=...)` 的 match= 在**整条 stmt** 里，
    不在块内 `expr` 里（块内可能是赋值 / del / raise 语句）。
    """
    try:
        t = ast.parse(stmt)
    except (SyntaxError, ValueError):
        return False
    return any(isinstance(n, ast.Call)
               and any(k.arg == "match" for k in n.keywords)
               for n in ast.walk(t))


def shape_of(probe: Dict[str, Any]) -> str:
    """⛔ **描述性**分类：这条被拒收的断言「需要哪种判据形态」。

    只读 `attr / expr / stmt`，不看 buggy/fixed 源码、不跑沙箱、不产出判据。
    返回值恒 ∈ `SHAPES`；量具格单列，绝不参与归因推断。
    """
    attr = str(probe.get("attr") or "")
    expr = str(probe.get("expr") or "")
    stmt = str(probe.get("stmt") or "")

    if attr in ("raises", "assertRaises"):
        if has_match_kw(stmt):
            return "raises_match"
        try:
            ast.parse(expr)          #: exec 模式：块内可能是语句
        except (SyntaxError, ValueError):
            return "unparseable"
        return "raises_other"

    if attr == "assert":
        try:
            t = ast.parse(expr, mode="eval").body
        except (SyntaxError, ValueError):
            return "unparseable"
        if isinstance(t, ast.Compare):
            if len(t.ops) > 1:
                return "chained_compare"
            return OPS.get(type(t.ops[0]).__name__, "other_compare")
        if isinstance(t, ast.BoolOp):
            return "boolop"
        if isinstance(t, ast.UnaryOp):
            return "not_expr"
        return "truthy"

    if attr.startswith("assert"):
        return UNITTEST.get(attr, "other")
    return "other"


def summarize(rows: List[Dict[str, Any]], health: Dict[str, Any],
              unmatched: List[Dict[str, Any]]) -> Dict[str, Any]:
    shapes = collections.Counter(r["shape"] for r in rows)
    unknown = sorted(set(shapes) - set(SHAPES))
    measurement = {g: shapes.get(g, 0) for g in sorted(MEASUREMENT_GATES)}
    return {
        "n_rows": len(rows),
        "shapes": dict(sorted(shapes.items())),
        "n_distinct_shapes": len(shapes),
        "measurement_gates": measurement,
        "n_measurement": sum(measurement.values()),
        "unknown_shapes": unknown,
        "by_attr": dict(sorted(collections.Counter(
            r["attr_product"] for r in rows).items())),
        "by_repo": dict(sorted(collections.Counter(
            r["repo"] for r in rows).items())),
        "cross_reach": dict(sorted(collections.Counter(
            "%s/%s" % (r["shape"], r["reach"]) for r in rows).items())),
        "cross_v175": dict(sorted(collections.Counter(
            "%s/%s" % (r["shape"], r["v175"]) for r in rows).items())),
        "attr_x_shape": dict(sorted(collections.Counter(
            "%s/%s" % (r["attr_product"], r["shape"]) for r in rows).items())),
        "n_expr_mismatch": sum(1 for r in rows if r["expr_mismatch"]),
        "n_not_mapped": sum(1 for r in rows if not r["mapped_false"]),
        "n_unmatched": len(unmatched),
        "baseline_health": {"ok": health.get("ok"),
                            "diffs": len(health.get("diffs", [])),
                            "repos": len(health.get("repos", {}))},
        "note": ("⛔ 纯静态描述性归因：不跑沙箱、不产出判据、不报收益、不改 aether/。"
                 "分母是产品 _iter_all_probes 的 mapped=False；reach 是事后 oracle，"
                 "只交叉、不能当 predictor。量具格与实格分开，不许合并成布尔。"),
    }


# ─────────────────────────── 主流程 ───────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑指定 sha（逗号分隔）")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--out", default="")
    ap.add_argument("--no-baseline-gate", action="store_true",
                    help="⛔ 仅供离线反证；正式普查必须验锚")
    args = ap.parse_args()

    #: 174 规则：先验锚，并把健康度当读数报出来
    health: Dict[str, Any] = {"ok": None, "diffs": [], "repos": {}}
    if args.no_baseline_gate:
        health["diffs"] = ["BYPASS（仅反证）"]
    else:
        health = require_baseline()
        print("baseline_health=OK  repos=%d  diffs=%d" %
              (len(health.get("repos", {})), len(health.get("diffs", []))))

    src = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows_in = list(src.get("form_diagnostic_rows") or [])
    want = {x for x in args.only.split(",") if x}
    if want:
        rows_in = [r for r in rows_in if r["sha"] in want]

    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = \
        collections.OrderedDict()
    for r in rows_in:
        groups.setdefault((r["repo"], r["sha"]), []).append(r)

    cands = {name: select_commits(Path("/tmp/realrepo") / name,
                                  args.limit, subdir)
             for name, subdir in REPOS}

    rows: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []
    for (name, sha), grp in groups.items():
        cand = next((c for c in cands[name] if c[0] == sha), None)
        if cand is None:
            for r in grp:
                unmatched.append({"repo": name, "sha": sha, "fn": r["fn"],
                                  "reason": "not in select_commits"})
            continue
        _, _, testfs, _ = cand
        idx = probe_index(Path("/tmp/realrepo") / name, sha, testfs)

        for r in grp:
            q = idx.get((r["fn"], r["stmt"]))
            if q is None:
                unmatched.append({"repo": name, "sha": sha, "fn": r["fn"],
                                  "stmt": r["stmt"],
                                  "reason": "probe_index miss"})
                shape = "unmatched"
                expr_mismatch = False
                mapped_false = False
            else:
                expr_mismatch = ((q.get("expr") or "") != (r.get("expr") or ""))
                mapped_false = (q.get("mapped") is False)
                shape = shape_of(q)
                if shape not in SHAPES or expr_mismatch or not mapped_false:
                    #: ⛔ fail-closed：不悄悄新增格、不拿不同源数据做归因
                    unmatched.append({"repo": name, "sha": sha, "fn": r["fn"],
                                      "reason": "caliber violation",
                                      "shape": shape,
                                      "expr_mismatch": expr_mismatch,
                                      "mapped": q.get("mapped")})
                    shape = "unmatched"

            rows.append({
                "repo": name, "sha": sha, "fn": r["fn"],
                "stmt": r["stmt"], "expr": (q or {}).get("expr") if q else None,
                "attr_176": r.get("attr"), "attr_product": (q or {}).get("attr"),
                "shape": shape, "reach": r.get("reach"), "v175": r.get("v175"),
                "gate": r.get("gate"), "verdict_177": r.get("verdict"),
                "expr_mismatch": expr_mismatch, "mapped_false": mapped_false,
                "diagnostic_only": True,
            })

    rep = {
        "schema": "form_shape_attribution/v1",
        "caliber": caliber(),
        "rows": rows,
        "unmatched": unmatched,
        "summary": summarize(rows, health, unmatched),
    }
    s = rep["summary"]
    if s["unknown_shapes"]:
        raise RuntimeError("封闭集合被突破: %s" % s["unknown_shapes"])

    out = args.out or str(ROOT / "scripts" / "form_shape_attribution.json")
    Path(out).write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print("n_rows=%d  distinct_shapes=%d  n_measurement=%d" %
          (s["n_rows"], s["n_distinct_shapes"], s["n_measurement"]))
    print("shapes=%s" % s["shapes"])
    print("n_expr_mismatch=%d  n_not_mapped=%d  n_unmatched=%d  baseline=%s" %
          (s["n_expr_mismatch"], s["n_not_mapped"], s["n_unmatched"],
           s["baseline_health"]))
    print("written: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
