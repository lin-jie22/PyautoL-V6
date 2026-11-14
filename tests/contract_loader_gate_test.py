#!/usr/bin/env python3
"""r109 / README 188 契约：119 条 form 门的装载子层归因。

A 组（钉读数）    —— 三本账、closure、caliber 含尺子自身、验锚
B 组（纯内存反证）—— ⛔ 直接喂构造数据给**尺子自己的** derive /
                      classify_probe / static_account，不另写第二份判据
C 组（本机实战）  —— 真跑产品 `_iter_all_probes()` 复现分层、AST 复现、
                      真实验锚、A1 扫描面、产品文件 sha256 对拍

⛔ 首次全绿必须注入反证（/tmp/r109_falsify.py）：逐条把 ck 条件改成 False、
保留 body 与缩进，逐条恰好命中预期项。本轮契约纯静态、单次耗时短 ⇒
**45 起全部完整真跑**，不分层（比 r108 更硬）。
"""
from __future__ import annotations

import ast
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
#: ⛔ 契约根从 __file__ 推导：事故版被反证脚本放到 /tmp 跑时，
#:    cwd 不再是 tests/ ⇒ 必须显式把 tests/ 加进 sys.path（第 99 条）。
sys.path.insert(0, str(ROOT / "tests"))

import probe_loader_gate as plg                                  # noqa: E402
from contract_portability_test import (                            # noqa: E402
    candidate_files, scan_text,
)
from realrepo_baseline import require_baseline                   # noqa: E402

CENSUS = ROOT / "scripts" / "loader_gate_census.json"
SOURCE_FORM = ROOT / "scripts" / "form_shape_attribution.json"
FAILS: List[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (" | " + str(extra) if extra and not cond else ""))


def main() -> None:
    d = json.loads(CENSUS.read_text(encoding="utf-8"))
    form = json.loads(SOURCE_FORM.read_text(encoding="utf-8"))
    A, B, C = (d["accounts"]["A_layer_census"],
               d["accounts"]["B_186_representativeness"],
               d["accounts"]["C_183_boundary"])
    P, F, der, cl, s = (d["positive"], d["falsification"],
                        d["derived"], d["closure"], d["summary"])
    ar = A["rows"]

    print("== A 组：钉死全量读数 ==")
    ck("A1 存档 schema 正确", d["schema"] == "loader_gate_census_v1")
    ck("A2 三本账各自存在且 A 账 119 条",
       len(ar) == 119 and isinstance(B["observation"], str)
       and C["only_static_extractor"] is True)
    ck("A3 13 实格、119 分母（同源取自 179）",
       der["n_shapes"] == 13 and der["n_rows"] == 119
       and set(der["by_shape"]) == set(r["shape"] for r in form["rows"]))
    ck("A4 三层封闭 80 裸 assert / 35 unittest / 4 raises",
       der["by_layer"] == {"unittest_table": 35, "bare_form_table": 80,
                           "raises_layer": 4})
    ck("A5 三层和 = 119", sum(der["by_layer"].values()) == 119)
    ck("A6 cross 和 119、每条探针数恰为 1",
       der["n_cross_sum"] == 119 and der["n_probes_not_1"] == 0)
    ck("A7 零异常、119 条里零 LOADABLE",
       der["n_anomaly"] == 0 and der["n_loadable"] == 0)
    ck("A8 5 条同源正例全部命中 LOADABLE 且 mapped=True",
       der["n_positive_loadable"] == 5 and der["n_positive_mapped"] == 5
       and all(x["layer"] == "LOADABLE" for x in P["rows"]))
    ck("A9 B 账 is 27 条 = 裸 24 + assertIs 3",
       B["n_is_rows"] == 27 and B["n_bare_assert"] == 24
       and B["n_assertIs"] == 3)
    ck("A10 B 账 is 格按子层 = bare 24 / unittest 3",
       B["by_layer"] == {"bare_form_table": 24, "unittest_table": 3})
    ck("A11 B 账观察 / 推断 / 未知三段分开写",
       all(isinstance(B[k], str) and len(B[k]) > 20
           for k in ("observation", "inference", "unknown"))
       and len({B["observation"], B["inference"], B["unknown"]}) == 3)
    ck("A12 C 账源码级零沙箱（未 import aether.environment）",
       C["only_static_extractor"] is True
       and C["imports_environment"] is False
       and C["has_sandbox_call"] is False)
    ck("A13 C 账无 eval / exec / execfile 调用",
       C["has_eval_or_exec"] is False)
    ck("A14 C 账五项 0 + aether 未改 + 上游零回写",
       all(C[k] == 0 for k in
           ("n_real_expr_sandbox_runs", "n_real_commit_runs",
            "n_f2p_or_gain_readings", "n_downstream_attribution",
            "n_main135_account_touched"))
       and C["aether_modified"] is False
       and C["upstream_rewritten"] is False)
    ck("A15 C 账明确写出两本账不同分母",
       "两本账" in C["note"] and "119" in C["note"])
    ck("A16 closure 16 项全 true", len(cl) == 16 and all(cl.values()))
    ck("A17 caliber 含尺子自身且长度 9",
       "scripts/probe_loader_gate.py" in plg.CALIBER_FILES
       and len(plg.CALIBER_FILES) == 9)
    ck("A18 caliber 含产品与 179 存档",
       "aether/extract.py" in d["caliber"]
       and "scripts/form_shape_attribution.json" in d["caliber"])
    ck("A19 验锚 ok / diffs / caliber_diffs 全干净",
       d["baseline_health"]["ok"] is True
       and d["baseline_health"]["diffs"] == []
       and d["baseline_health"]["caliber_diffs"] == [])
    ck("A20 derived 与 summary 三层读数一致",
       s["layers"] == der["by_layer"] and s["all_checks"] is True)
    ck("A21 summary 写明不改判 ④/② 与不宣布扩张",
       "不改判" in s["not_said"] and "不宣布扩张" in s["not_said"]
       and "80" in s["conclusion"])

    print("== B 组：纯内存反证（喂尺子自己的纯函数） ==")
    ck("B1 derive 复现 119 条", plg.derive(A, P)["n_rows"] == 119)
    ck("B2 derive 复现 80/35/4",
       plg.derive(A, P)["by_layer"] == der["by_layer"])
    inj = [dict(ar[0], layer="LOADABLE")] + ar[1:]
    ck("B3 注入一条 LOADABLE ⇒ n_loadable 立刻变 1（不是恒真）",
       plg.derive(dict(A, rows=inj), P)["n_loadable"] == 1)
    ck("B4 抹掉 80 条裸 assert ⇒ bare 层归零（r108 B4 同源反证）",
       plg.derive(dict(A, rows=[x for x in ar
                                if x["layer"] != "bare_form_table"]), P)
       ["by_layer"].get("bare_form_table", 0) == 0)
    ck("B5 喂过滤 10 条 ⇒ 计数跟着变（声称全从 rows 派生）",
       plg.derive(dict(A, rows=ar[:10]), P)["n_rows"] == 10
       and plg.derive(dict(A, rows=ar[:10]), P)["by_layer"]
       != der["by_layer"])
    ck("B6 抹掉 attr_product / shape ⇒ 分层计数不变",
       plg.derive(dict(A, rows=[dict(x, attr_product=None, shape=None)
                                for x in ar]), P)["by_layer"]
       == der["by_layer"])
    ck("B7 classify_probe(mapped=True) = LOADABLE",
       plg.classify_probe({"mapped": True, "attr": "assertIs",
                           "family": "unittest"}) == "LOADABLE")
    ck("B8 classify_probe(裸 assert 未映射) = bare_form_table",
       plg.classify_probe({"mapped": False, "attr": "assert",
                           "family": "bare"}) == "bare_form_table")
    ck("B9 classify_probe(assertIs 未映射) = unittest_table",
       plg.classify_probe({"mapped": False, "attr": "assertIs",
                           "family": "unittest"}) == "unittest_table")
    ck("B10 classify_probe(raises 未映射) = raises_layer",
       plg.classify_probe({"mapped": False, "attr": "raises",
                           "family": "pytest"}) == "raises_layer")
    ck("B11 classify_probe(未知 attr) = other（不静默吞）",
       plg.classify_probe({"mapped": False, "attr": "__nope__",
                           "family": "x"}) == "other")
    ck("B12 空 raises 词表 ⇒ raises 归 other（该层有牙）",
       plg.classify_probe({"mapped": False, "attr": "raises",
                           "family": "pytest"},
                          raise_names=set()) == "other")
    ck("B13 static_account(空 raises 词表) ⇒ raises_layer=0",
       dict(collections.Counter(
           x["layer"] for x in plg.static_account(
               form["rows"], raise_names=set())["rows"]))
       .get("raises_layer", 0) == 0)
    ck("B14 119 条 stmt 与 179 存档逐字同源（不另造语句）",
       [x["stmt"] for x in ar] == [r["stmt"] for r in form["rows"]])
    ck("B15 13 格集合与 179 存档完全一致（封闭）",
       set(der["by_shape"]) == set(r["shape"] for r in form["rows"])
       and len(der["by_shape"]) == 13)
    ck("B16 5 条正例过产品枚举器全部 mapped=True",
       all(len(plg.probes_of(x)) == 1 and plg.probes_of(x)[0]["mapped"]
           for x in plg.POSITIVE))
    ck("B17 falsification R1–R6 全 true",
       all(v for k, v in F.items()
           if k.startswith("r") and k != "detail") and len(F) == 7)
    ck("B18 summary 三层占比与 80/35/4 一致",
       round(s["share_bare"], 4) == round(80 / 119, 4)
       and round(s["share_unittest"], 4) == round(35 / 119, 4)
       and round(s["share_raises"], 4) == round(4 / 119, 4))

    print("== C 组：本机同源实战 ==")
    live = collections.Counter(
        plg.classify_probe(plg.probes_of(r["stmt"])[0])
        if len(plg.probes_of(r["stmt"])) == 1 else "n_probes_bad"
        for r in form["rows"])
    ck("C1 真跑产品 _iter_all_probes 复现 80/35/4",
       dict(live) == der["by_layer"], dict(live))
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
    ck("C2 静态 AST 同源复现 115/4/0（187 的四条语句）",
       kinds == {"expr": 115, "stmt": 4, "unparseable": 0}, kinds)
    ck("C3 真实验锚 fail-closed 通过",
       require_baseline()["ok"] is True)
    files = candidate_files()
    rel = "tests/contract_loader_gate_test.py"
    ck("C4 本契约文件在 A1 扫描面且 scan_text 零命中",
       files is not None and rel in files
       and scan_text((ROOT / rel).read_text(encoding="utf-8")) == [])
    now = hashlib.sha256((ROOT / "aether" / "extract.py").read_bytes()).hexdigest()
    ck("C5 aether/extract.py 当前 sha256 与存档 caliber 一致（产品未改）",
       now == d["caliber"]["aether/extract.py"])
    ck("C6 尺子自身 caliber 与当前文件一致（含自身 hash）",
       hashlib.sha256((ROOT / "scripts" / "probe_loader_gate.py")
                      .read_bytes()).hexdigest()
       == d["caliber"]["scripts/probe_loader_gate.py"])

    print("\n%s  共 %d 项，FAILS=%s"
          % ("✅ 全部通过" if not FAILS else "❌ 有失败", 21 + 18 + 6, FAILS))
    if FAILS:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
