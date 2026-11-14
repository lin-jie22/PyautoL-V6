#!/usr/bin/env python3
"""r108 / README 187 契约：186 结论的适用边界与两本账分离。

A 组（钉读数）    —— 四本账、closure、caliber 含尺子自身、验锚
B 组（纯内存反证）—— ⛔ 直接喂构造数据给**尺子自己的** derive / classify_static，
                      不另写第二份判据（第 68 条：判据必须同源）
C 组（本机实战）  —— 同源产品沙箱重跑、常量 True 两账分离、AST 复现、
                      真实验锚、契约自身在 A1 扫描面

⛔ 首次全绿必须注入反证（/tmp/r108_falsify.py）：逐条把 ck 条件改成 False、
保留 body 与缩进，逐条恰好命中预期项。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_form_boundary as pfb                                # noqa: E402
from probe_form_shape_attribution import (                       # noqa: E402
    MEASUREMENT_GATES, SHAPES, UNITTEST,
)
from realrepo_baseline import require_baseline                   # noqa: E402

CENSUS = ROOT / "scripts" / "form_boundary_census.json"
SOURCE_FORM = ROOT / "scripts" / "form_shape_attribution.json"
FAILS: List[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))


def main() -> None:
    d = json.loads(CENSUS.read_text(encoding="utf-8"))
    form = json.loads(SOURCE_FORM.read_text(encoding="utf-8"))
    A = d["accounts"]["A_static_form"]
    B = d["accounts"]["B_synth_eval"]
    C = d["accounts"]["C_synth_discriminate"]
    D = d["accounts"]["D_183_boundary"]
    F, der, cl, s = d["falsification"], d["derived"], d["closure"], d["summary"]
    ar, br, cr = A["rows"], B["rows"], C["rows"]

    print("== A 组：钉死全量读数 ==")
    ck("A1 存档 schema 正确", d["schema"] == "form_boundary_census_v1")
    ck("A2 四本账各自存在且长度 119/20/10/D",
       len(ar) == 119 and len(br) == 20 and len(cr) == 10
       and D["n_real_expr_sandbox_runs"] == 0)
    ck("A3 13 实格、119 分母（同源取自 179）",
       A["n_shapes"] == 13 and A["n_rows"] == 119
       and set(A["by_shape"]) == set(r["shape"] for r in form["rows"]))
    ck("A4 四格封闭 80 裸 / 29 改写 / 6 multiset / 4 语句",
       A["by_class"] == {"bare_bool_expr": 80, "needs_rewrite": 29,
                         "multiset": 6, "stmt_raises": 4})
    ck("A5 AST 115 表达式 / 4 语句 / 0 不可解析",
       A["ast_kind"] == {"expr": 115, "stmt": 4, "unparseable": 0})
    ck("A6 4 条语句全属 raises 两格",
       sorted(x["shape"] for x in ar if x["class"] == "stmt_raises")
       == ["raises_match", "raises_match", "raises_match", "raises_other"])
    ck("A7 B 合成求值账 20/20", der["n_eval_ok"] == der["n_eval_rows"] == 20)
    ck("A8 C 合成鉴别账 f2p 10/10", der["n_f2p"] == der["n_disc_rows"] == 10)
    ck("A9 R1/R2/R3 三组反证全中",
       F["r1_ok"] is True and F["r2_ok"] is True and F["r3_ok"] is True)
    ck("A10 ⛔ D 账：119 条真实 expr 沙箱运行 0 次、不报收益",
       all(x["sandbox_run"] is False for x in ar)
       and D["reports_f2p_on_real_expr"] is False
       and D["reports_downstream_attribution"] is False)
    ck("A11 closure 12/12 全绿", all(cl.values()),
       [k for k, v in cl.items() if not v])
    ck("A12 caliber 含尺子自身且长度 10",
       "scripts/probe_form_boundary.py" in d["caliber"]
       and len(pfb.CALIBER_FILES) == 10
       and cl["eq_caliber_self"] is True)
    ck("A13 验锚 ok / diffs 空", d["baseline_health"]["ok"] is True
       and d["baseline_health"]["diffs"] == [])
    ck("A14 上游 9 个文件 sha256 与磁盘一致（没回写）",
       all(d["caliber"][f] == pfb.sha256_file(f)
           for f in pfb.CALIBER_FILES
           if not f.endswith("probe_form_boundary.py")))
    ck("A15 summary 明确不宣布扩张无用 / 有效", "不宣布" in s["not_said"])
    ck("A16 summary 明确「求值判对 ≠ 鉴别力」", "求值判对" in s["conclusion"]
       and "≠" in s["conclusion"])
    ck("A17 A 账每行都标 sandbox_run=False",
       all(x["sandbox_run"] is False for x in ar))
    ck("A18 全部 shape ∈ 179 封闭集合、量具格 0",
       all(x["shape"] in SHAPES for x in ar)
       and not any(x["shape"] in MEASUREMENT_GATES for x in ar))
    ck("A19 分类器用 179 尺子的 UNITTEST 映射（不另写词表）",
       all(x["attr_product"] in
           ({"assert", "raises", "assertCountEqual"} | set(UNITTEST))
           for x in ar))
    ck("A20 R2 证两本账可分离：求值 10/10 但鉴别 0",
       F["r2_const_eval_ok"] == 10 and F["r2_const_f2p"] == 0)
    ck("A21 结论明确不外推到 119 条真实 expr",
       "不外推" in s["conclusion"] and "其余 4 项拍板未开工" in s["not_said"])

    print("== B 组：纯内存反证（喂尺子自己的纯函数） ==")
    ck("B1 derive 对真实存档复现 A 账读数",
       pfb.derive(A, B, C)["n_static"] == 119
       and pfb.derive(A, B, C)["bare"] == 80
       and pfb.derive(A, B, C)["n_shapes"] == 13)
    ck("B2 derive 复现 B/C/D 账读数",
       pfb.derive(A, B, C)["n_eval_ok"] == 20
       and pfb.derive(A, B, C)["n_f2p"] == 10
       and pfb.derive(A, B, C)["n_sandbox_on_real_expr"] == 0)
    inj = [dict(ar[0], sandbox_run=True)] + ar[1:]
    ck("B3 注入一条 sandbox_run=True ⇒ D 账立刻变 1（不是恒真）",
       pfb.derive(dict(A, rows=inj), B, C)["n_sandbox_on_real_expr"] == 1)
    ck("B4 抹掉 80 条裸 assert ⇒ bare 归零",
       pfb.derive(dict(A, rows=[x for x in ar if x["class"] != "bare_bool_expr"]),
                  B, C)["bare"] == 0)
    ck("B5 把 B 账一条 eval_ok 改 False ⇒ 20 变 19",
       sum(1 for x in [{**br[0], "eval_ok": False}] + br[1:] if x["eval_ok"]) == 19)
    ck("B6 把 C 账一条 f2p 改 False ⇒ 10 变 9",
       sum(1 for x in [{**cr[0], "f2p_synth": False}] + cr[1:] if x["f2p_synth"]) == 9)
    ck("B7 classify_static(assert) = bare_bool_expr",
       pfb.classify_static({"attr_product": "assert"}) == "bare_bool_expr")
    ck("B8 classify_static(assertTrue) = needs_rewrite（来自 UNITTEST）",
       pfb.classify_static({"attr_product": "assertTrue"}) == "needs_rewrite"
       and "assertTrue" in UNITTEST)
    ck("B9 classify_static(assertCountEqual) = multiset",
       pfb.classify_static({"attr_product": "assertCountEqual"}) == "multiset")
    ck("B10 classify_static(raises) = stmt_raises",
       pfb.classify_static({"attr_product": "raises"}) == "stmt_raises")
    ck("B11 classify_static(未知 attr) = unknown（fail-closed）",
       pfb.classify_static({"attr_product": "__nope__"}) == "unknown")
    ck("B12 四本账字段互斥（A 没有 f2p_synth、C 没有 sandbox_run）",
       all("f2p_synth" not in x for x in ar)
       and all("sandbox_run" not in x for x in cr))
    ck("B13 119 条 expr 与 179 存档逐字同源（不另造 expr）",
       [x["expr"] for x in ar] == [r["expr"] for r in form["rows"]])
    ck("B14 13 格集合与 179 存档完全一致（封闭）",
       set(A["by_shape"]) == set(r["shape"] for r in form["rows"])
       and len(A["by_shape"]) == 13)
    ck("B15 量具格 0（179 的 5 个 measurement gate 没出现）",
       not (set(A["by_shape"]) & set(MEASUREMENT_GATES)))
    ck("B16 summary 四个数字与 derive 一致",
       s["n_bare_bool_expr"] == der["bare"]
       and s["n_needs_rewrite"] == der["rewrite"]
       and s["n_multiset"] == der["multiset"]
       and s["n_stmt_raises"] == der["stmt"])
    ck("B17 closure 含 eq_eval_not_discriminate（两账分离被钉死）",
       cl["eq_eval_not_discriminate"] is True)
    ck("B18 closure 含 eq_no_real_sandbox（§183 禁令被钉死）",
       cl["eq_no_real_sandbox"] is True)

    print("== C 组：本机同源实战 ==")
    # 用尺子里同源的 (src, call) 逐条真跑 C 账的 fixed/buggy 两侧
    ok_live = 0
    for x in cr:
        spec = next((t for t in pfb.SHAPES_SYNTH if t[0] == x["shape"]), None)
        if spec is None:
            continue
        pf, _ = pfb.sandbox_bool(spec[1], spec[3], 8.0)
        pb, _ = pfb.sandbox_bool(spec[2], spec[3], 8.0)
        ok_live += int((pf == x["passed_fixed"] and pb == x["passed_buggy"]
                        and (bool(pf) and not bool(pb)) == x["f2p_synth"]))
    ck("C1 真实产品沙箱重跑 C 账 10/10 一致", ok_live == 10, ok_live)
    const_eval = sum(1 for t in pfb.SHAPES_SYNTH
                     if pfb.sandbox_bool(t[1], "True", 8.0)[0] is True)
    const_f2p = sum(1 for t in pfb.SHAPES_SYNTH
                    if pfb.sandbox_bool(t[1], "True", 8.0)[0]
                    and not pfb.sandbox_bool(t[2], "True", 8.0)[0])
    ck("C2 常量 True 实战：求值 10/10 但鉴别 0（两账真的可分离）",
       const_eval == 10 and const_f2p == 0, (const_eval, const_f2p))
    kinds = {"expr": 0, "stmt": 0, "unparseable": 0}
    for r in form["rows"]:
        try:
            ast.parse(r["expr"], mode="eval")
            kinds["expr"] += 1
        except SyntaxError:
            try:
                ast.parse(r["expr"], mode="exec")
                kinds["stmt"] += 1
            except SyntaxError:
                kinds["unparseable"] += 1
    ck("C3 静态 AST 同源复现 115/4/0", kinds == A["ast_kind"], kinds)
    ck("C4 真实验锚 fail-closed 通过", require_baseline()["ok"] is True)
    ck("C5 本契约文件自身在 A1 扫描面里（untracked 也扫）",
       "tests/contract_form_boundary_test.py" in
       [str(p.relative_to(ROOT)) for p in (ROOT / "tests").glob("contract_form_boundary_test.py")])
    ck("C6 尺子 CALIBER_FILES 含自身且长度 10",
       "scripts/probe_form_boundary.py" in pfb.CALIBER_FILES
       and len(pfb.CALIBER_FILES) == 10)

    print("\n%s  共 %d 项，FAILS=%s" % ("✅ 全部通过" if not FAILS else "❌ 有失败",
                                       21 + 18 + 6, FAILS))
    if FAILS:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
