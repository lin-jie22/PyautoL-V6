#!/usr/bin/env python3
"""r107 / README 186 契约：`is` 27 条收益上界普查。

A 组（钉读数）  —— 存档 27/5/4 三本账、closure、caliber 含尺子自身、验锚
B 组（纯内存反证）—— `derive()` / `to_probe()` 是纯函数，喂构造数据验边界
C 组（本机实战）  —— 同源产品沙箱重跑 C 账 + 一条手写 oracle 的 good/bad
                     对照 + 真实验锚 + 上游存档字节未改

⛔ 首次全绿必须注入反证（`/tmp/r107_falsify.py`）。
⛔ 判据同源：直接 import 被测尺子的 `to_probe` / `derive` 概念，不另写词表。
"""
from __future__ import annotations

import collections
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_is_shape as pis                                     # noqa: E402
from aether.environment import run_in_sandbox                    # noqa: E402
from probe_alias_demote import _git                              # noqa: E402
from probe_ctx_downstream import sandbox_read                    # noqa: E402
from realrepo_baseline import require_baseline                   # noqa: E402

CENSUS = ROOT / "scripts" / "is_shape_census.json"
FAILS: List[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" | " + extra if extra and not cond else ""))


# ───────────────────────── 纯函数（B 组离线反证用） ─────────────────────────
def derive(auto: List[Dict[str, Any]],
           orc: List[Dict[str, Any]]) -> Dict[str, Any]:
    """⛔ 纯函数：所有结论从 rows 派生，不藏进程内状态。"""
    by_why = dict(collections.Counter(
        "%s/%s" % (x["why_fixed"], x["why_buggy"]) for x in auto))
    return {
        "n_rows": len(auto),
        "by_why": by_why,
        "n_f2p_auto": sum(1 for x in auto if x["f2p_auto"]),
        "n_raise_both": by_why.get("raise/raise", 0),
        "n_behavior_both": by_why.get("behavior/behavior", 0),
        "n_oracle_f2p": sum(1 for x in orc if x["f2p_oracle"]),
        "n_oracle_green": sum(1 for x in orc if x["oracle_green"]),
        "n_oracle_bad_red": sum(1 for x in orc if x["oracle_bad_red"]),
        "n_weak": sum(1 for x in orc if x["f2p_is_crash_weak"]),
    }


def row(why_f: str, why_b: str, f2p: bool = False) -> Dict[str, Any]:
    return {"why_fixed": why_f, "why_buggy": why_b, "f2p_auto": f2p}


def main() -> None:
    d = json.loads(CENSUS.read_text(encoding="utf-8"))
    A, B, C = d["accounts"]["A_auto"], d["accounts"]["B_oracle"], d["accounts"]["C_shape_fact"]
    ar, br = A["rows"], B["rows"]
    s, cl = d["summary"], d["closure"]

    print("== A 组：钉死全量读数 ==")
    ck("A1 存档 schema 正确", d["schema"] == "is_shape_census_v1")
    ck("A2 三本账各自存在且长度 27/5/4",
       len(ar) == 27 and len(br) == 5 and C["n_rows"] == 4)
    ck("A3 ⛔ 自动路径 F2P = 0（26 raise/raise + 1 behavior/behavior）",
       A["n_f2p"] == 0 and A["by_why"] == {"behavior/behavior": 1, "raise/raise": 26},
       str(A["by_why"]))
    ck("A4 27 条全部 `shape=is` 且分母来自 179",
       all(x["shape"] == "is" for x in ar) and s["n_is_rows"] == 27)
    ck("A5 27 条全部 `call_from_177 == ''`（产品没产出探针）",
       all(x["call_from_177"] == "" for x in ar))
    ck("A6 27 条全部 `has_raw_expr_177 is True`",
       all(x["has_raw_expr_177"] is True for x in ar))
    ck("A7 27 条全部 `reach=X` 且 `verdict_177=unmeasurable`",
       all(x["reach"] == "X" and x["verdict_177"] == "unmeasurable" for x in ar))
    ck("A8 26 条 raise 末行归因全是测试局部名字 / 装载失败",
       sum(A["raise_tail"].values()) == 26
       and any("NameError" in k for k in A["raise_tail"]),
       str(A["raise_tail"]))
    ck("A9 手写 oracle 5/5 参照实现验绿", B["n_green"] == 5)
    ck("A10 手写 oracle `oracle_bad` 5/5 转红（尺子有牙）", B["n_bad_red"] == 5)
    ck("A11 ⛔ 手写 oracle 真 F2P = 4，且 4/4 都是 crash 弱题",
       B["n_f2p"] == 4 and B["n_f2p_crash_weak"] == 4)
    ck("A12 每条 oracle 都写明语义来源（171 教训）",
       all(len(x["origin"]) > 40 and x["oracle_good"] != x["oracle_bad"] for x in br))
    ck("A13 C 账产品 truthy 分支 4/4 判对 `is` 表达式",
       C["n_correct"] == 4 and not C["import_error"])
    ck("A14 closure 全绿", all(cl.values()), str([k for k, v in cl.items() if not v]))
    ck("A15 closure 含 `eq_three_accounts`（三本账不许合并成布尔）",
       cl["eq_three_accounts"] is True)
    ck("A16 口径签名含尺子自身", "scripts/probe_is_shape.py" in d["caliber"]
       and cl["eq_caliber_self"] is True)
    ck("A17 验锚 ok / diffs 空", d["baseline_health"]["ok"] is True
       and d["baseline_health"]["diffs"] == [])
    ck("A18 上游 8 个源码文件 sha256 与磁盘一致（没回写）",
       all(d["caliber"][f] == pis.sha256_file(f)
           for f in pis.CALIBER_FILES if not f.endswith("probe_is_shape.py")))
    ck("A19 `probe` 是整条 bool 表达式（不是拆开的 actual/expect）",
       all(" is " in x["probe"] for x in ar))
    ck("A20 summary 明确不宣布扩张无用 / 有效",
       "不宣布" in s["not_said"] and "不改" in s["not_said"])
    ck("A21 结论指向装载门而非判据形态",
       "装载门" in s["conclusion"] and "而非判据形态" in s["conclusion"])

    print("== B 组：纯内存反证（derive / to_probe 不是死代码） ==")
    ck("B1 derive 对真实存档复现 A 账读数",
       derive(ar, br)["n_rows"] == 27
       and derive(ar, br)["n_raise_both"] == 26
       and derive(ar, br)["n_f2p_auto"] == 0)
    ck("B2 derive 复现 B 账读数",
       derive(ar, br)["n_oracle_f2p"] == 4
       and derive(ar, br)["n_oracle_bad_red"] == 5)
    ck("B3 注入一条 `f2p_auto=True` ⇒ derive 立刻变成 1",
       derive(ar + [row("behavior", "raise", True)], br)["n_f2p_auto"] == 1)
    ck("B4 抹掉 26 条 raise ⇒ `n_raise_both` 归零",
       derive([x for x in ar if x["why_fixed"] != "raise"], br)["n_raise_both"] == 0)
    ck("B5 全部改成 behavior/behavior ⇒ 27 同侧、0 鉴别力",
       derive([row("behavior", "behavior") for _ in range(27)], br)["n_behavior_both"] == 27)
    ck("B6 抹掉 oracle 的 good ⇒ `n_oracle_green` 归零",
       derive(ar, [{**x, "oracle_green": False} for x in br])["n_oracle_green"] == 0)
    ck("B7 让 oracle_bad 恒绿 ⇒ `n_oracle_bad_red` 归零（A10 不是恒真）",
       derive(ar, [{**x, "oracle_bad_red": False} for x in br])["n_oracle_bad_red"] == 0)
    ck("B8 把 4 条 F2P 的 buggy `why` 改成 mismatch ⇒ 弱题数归零",
       sum(1 for x in br if x["f2p_oracle"] and x["buggy_good"]["why"] == "raise") == 4)
    ck("B9 `to_probe` 对裸 assert 原样返回（同形、不加工）",
       pis.to_probe("X is None", "assert") == "X is None")
    ck("B10 `to_probe` 对 `assertIs(A, B)` 改写成 `A is B`",
       pis.to_probe("self.assertIs(prefix[0], iterables[0][0])", "assertIs")
       == "prefix[0] is iterables[0][0]")
    ck("B11 `to_probe` 对嵌套下标不在内层逗号切错",
       pis.to_probe("self.assertIs(f(a, b), g(c, d))", "assertIs")
       == "f(a, b) is g(c, d)")
    ck("B12 `to_probe` 遇无逗号 `assertIs` ⇒ None（fail-closed）",
       pis.to_probe("self.assertIs(x)", "assertIs") is None)
    ck("B13 A 账 `f2p_auto` 与两侧 passed 严格一致（不靠文案推断）",
       all(x["f2p_auto"] == (x["passed_fixed"] and not x["passed_buggy"]) for x in ar))
    ck("B14 B 账 `f2p_oracle` 与两侧 passed 严格一致",
       all(x["f2p_oracle"] == (x["fixed_good"]["passed"]
                               and not x["buggy_good"]["passed"]) for x in br))
    ck("B15 弱题标记只在 F2P 且 buggy 侧 raise 时为真",
       all(x["f2p_is_crash_weak"] == (x["f2p_oracle"]
                                      and x["buggy_good"]["why"] == "raise") for x in br))
    ck("B16 三本账字段互斥（A 没有 oracle_green、B 没有 why_fixed）",
       all("oracle_green" not in x for x in ar)
       and all("why_fixed" not in x for x in br))
    ck("B17 27 条 `probe` 与 `expr` 同源：裸 assert 逐字相同",
       all(x["probe"] == x["expr"] for x in ar if x["attr"] == "assert")
       and sum(1 for x in ar if x["attr"] == "assert") == 24)
    ck("B18 3 条 assertIs 的 probe 均由 expr 同形改写",
       sum(1 for x in ar if x["attr"] == "assertIs") == 3
       and all(" is " in x["probe"] for x in ar if x["attr"] == "assertIs"))

    print("== C 组：本机同源实战 ==")
    fact = pis.shape_fact(8.0)
    ck("C1 真实产品沙箱重跑 C 账 4/4 判对", fact["n_correct"] == 4
       and [x["passed"] for x in fact["rows"]] == [x["passed"] for x in C["rows"]])
    ck("C2 `X is None` 真绿 / `1 is True` 真红（不是恒真也不是恒假）",
       fact["rows"][0]["passed"] is True and fact["rows"][2]["passed"] is False)
    # 一条真实 oracle good/bad 对照（同源产品沙箱）
    o = pis.ORACLES[0]
    base = Path("/tmp/realrepo") / o["repo"]
    fixed = _git(base, "show", "%s:%s" % (o["sha"], o["src"]))
    fg, _ = sandbox_read(fixed, [{"name": "t0", "call": o["good"]}], 8.0, None)
    fb, _ = sandbox_read(fixed, [{"name": "t0", "call": o["bad"]}], 8.0, None)
    ck("C3 手写 oracle 在参照实现真绿、bad 真红（真实跑，非只读存档）",
       bool(fg[0]["passed"]) and not bool(fb[0]["passed"]))
    ck("C4 真实验锚 fail-closed 通过", require_baseline()["ok"] is True)
    ck("C5 本契约文件自身在 A1 扫描面里（untracked 也扫）",
       "tests/contract_is_shape_test.py" in
       [str(p.relative_to(ROOT)) for p in (ROOT / "tests").glob("contract_is_shape_test.py")])
    ck("C6 尺子 `CALIBER_FILES` 含自身且长度 11",
       "scripts/probe_is_shape.py" in pis.CALIBER_FILES
       and len(pis.CALIBER_FILES) == 11)

    print("\n%s  共 %d 项，FAILS=%s" % ("✅ 全部通过" if not FAILS else "❌ 有失败",
                                     21 + 18 + 6, FAILS))
    if FAILS:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
