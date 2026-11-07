#!/usr/bin/env python3
"""契约：**9 条 `import_failed` 的运行时归因**（README 180）必须同源、有牙、封闭。

═══ 钉死什么 ═══

177 把 6 个 sha / 9 条探针判成 `import_failed`（`run_all` 见到
`__import_error__` ⇒ 整个 commit 连坐），但**没记为什么**。180 用 177
完全同源的设施真跑 fixed/buggy，取 `ExecResult.import_error` 的完整
末行作为**观察**，按产品同源词表分类，并把 254 条 unknown 的账目闭合
成可复算等式。

三条硬约束：

  A. **观察 / 分类 / 未知分开**（契约⑤）：`evidence_tail` 是运行时观察；
     `main_gate` 是同源词表的可复算分类；`downstream` 恒 `unknown`
     —— 装不起来 ⇒ 探针从未执行，**没测过 ≠ 测出 0**。
  B. **不报收益、不放宽边界**：⛔ 本轮没有跑「去掉 deny-modules」的对照
     （会削弱隔离边界，属审批节点）。⇒ 不许宣称「放宽就能测几条」，
     也不许把 8 条 deny 命中写成「产品缺陷」。
  C. **反证组**（契约⑥）：B 组离线注入（词表外 fail-closed / 只认末行 /
     恒真分类 / 篡改封闭性 / 去掉验锚），C 组才碰本机基线与真实沙箱
     正例（第 133 条：零命中先证查询能命中，且必须能命中非 import 路径）。

⛔ `GATES` / `PAIRS` 是封闭集合，集合外的格一律 fail-closed。

用法：python tests/contract_import_failure_attribution_test.py
"""
from __future__ import annotations

import ast
import collections
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_import_failure_attribution as ifa      # noqa: E402
from aether.environment import (                    # noqa: E402
    _AUDIT_DENY_MODULES, run_in_sandbox)
from realrepo_baseline import require_baseline       # noqa: E402

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

READING = ROOT / "scripts" / "import_failure_attribution.json"
SCRIPT = ROOT / "scripts" / "probe_import_failure_attribution.py"
SOURCE = ROOT / "scripts" / "extract_downstream.json"

PASSES: list = []
FAILS: list = []
SKIPS: list = []

EXPECT_GATES = {"sandbox_deny": 8, "stdlib_incompat": 1}
EXPECT_DETAILS = {"ctypes": 7, "fcntl": 1,
                  "collections.MutableSet": 1}
EXPECT_PAIRS = {"both_import": 9}
EXPECT_REPO = {"attrs": 7, "boltons": 2}
EXPECT_GATE_176 = {"unserializable": 5, "unmatched": 4}
CLOSURE_EQS = (
    "eq_main_135", "eq_main_five_books", "eq_254",
    "eq_178_matches_no_result", "eq_179_matches_form",
    "eq_180_matches_import_failed",
)


def ck(name: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  | " + detail) if detail and not ok else ""))
    #: ⛔ 必须写成字面 `FAILS.append`：静态闸 contract_assert_wiring B1
    #: 要能定位失败记账（写成 `(PASSES if ok else FAILS).append` 会判
    #: 「失败不落地」⇒ 全量验收真红）。
    if not ok:
        FAILS.append(name)
    else:
        PASSES.append(name)


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print("  SKIP %s  | %s" % (name, why))


# ─────────────────────────── A 组：钉死全量读数 ───────────────────────────
def group_a() -> None:
    d = json.loads(READING.read_text(encoding="utf-8"))
    s, rows, by_sha = d["summary"], d["rows"], d["by_sha"]
    print("A 组：全量读数（⛔ 改尺子必须重跑普查）")
    ck("A1 schema 版本",
       d["schema"] == "import_failure_attribution/v1", str(d.get("schema")))
    ck("A2 9 条 / 6 sha", s["n_rows"] == 9 and len(rows) == 9
       and s["n_sha"] == 6 and len(by_sha) == 6,
       "%s / %s / %s" % (s["n_rows"], len(rows), s["n_sha"]))
    ck("A3 主格分布 8 + 1", s["gates"] == EXPECT_GATES, str(s["gates"]))
    ck("A4 detail 分布", s["details"] == EXPECT_DETAILS, str(s["details"]))
    ck("A5 两侧账 9/9 both_import", s["pairs"] == EXPECT_PAIRS,
       str(s["pairs"]))
    ck("A6 两侧分类一致 / 无 skip / 无 other_unknown",
       s["n_sides_agree"] == 9 and s["n_skipped"] == 0
       and s["n_other_unknown"] == 0,
       "%s/%s/%s" % (s["n_sides_agree"], s["n_skipped"],
                     s["n_other_unknown"]))
    ck("A7 by_repo", s["by_repo"] == EXPECT_REPO, str(s["by_repo"]))
    ck("A8 176 门交叉", s["by_gate_176"] == EXPECT_GATE_176,
       str(s["by_gate_176"]))
    cl = s["closure"]
    for i, key in enumerate(CLOSURE_EQS, 1):
        ck("A9.%d 封闭性 %s" % (i, key), cl.get(key) is True, str(cl.get(key)))
    ck("A10 账目条数",
       (cl.get("n_main") == 135 and cl.get("n_form") == 119
        and cl.get("n_178") == 94 and cl.get("n_179") == 119
        and cl.get("n_180") == 9 and cl.get("n_178_179_overlap") == 0),
       str({k: cl.get(k) for k in ("n_main", "n_form", "n_178",
                                   "n_179", "n_180", "n_178_179_overlap")}))
    ck("A11 基线健康当读数报出",
       s["baseline_health"] == {"ok": True, "diffs": 0, "repos": 3},
       str(s["baseline_health"]))
    ck("A12 下游恒 unknown / 诊断账",
       all(r.get("downstream") == "unknown" and r.get("diagnostic_only") is True
           for r in rows))
    ck("A13 不报收益（无 F2P / gain 字段）",
       all(not any(k in r for k in ("f2p", "gain", "passed_fixed",
                                    "fixed_ok", "verdict_repair"))
           for r in rows))

    #: 第 133 条：零命中先证查询能命中 —— 9 条 key 与 177 的 import_failed
    #: 子集逐字同源，且该子集非空。
    src = json.loads(SOURCE.read_text(encoding="utf-8"))
    want = [r for r in src["rows"] if r.get("verdict") == "import_failed"]
    k_src = {ifa.row_key(r) for r in want}
    k_this = {ifa.row_key(r) for r in rows}
    ck("A14 与 177 存档逐字同源（查询能命中）",
       bool(k_src) and len(k_src) == 9 and k_this == k_src,
       "%d vs %d" % (len(k_src), len(k_this)))

    tails = collections.Counter(
        "deny" if "V6_SANDBOX_DENY:import:" in r["evidence_tail_fixed"]
        else ("stdlib" if "MutableSet" in r["evidence_tail_fixed"]
              else "other") for r in rows)
    ck("A15 观察文本 8 deny + 1 stdlib",
       tails == collections.Counter({"deny": 8, "stdlib": 1}), str(tails))
    ck("A16 观察末行与 by_sha 逐条一致",
       all(any(b["sha"] == r["sha"] and
               b["evidence_tail_fixed"] == r["evidence_tail_fixed"] and
               b["evidence_tail_buggy"] == r["evidence_tail_buggy"]
               for b in by_sha) for r in rows))
    ck("A17 口径签名每个文件与实际一致",
       all(hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == h
           for rel, h in d["caliber"].items())
       and "scripts/probe_import_failure_attribution.py" in d["caliber"],
       str(len(d["caliber"])))
    ck("A18 没有放宽 deny 的读数（同源词表大小已钉）",
       s["caliber_sizes"]["deny_modules"] == len(set(_AUDIT_DENY_MODULES))
       and s["caliber_sizes"]["stdlib_module_names"] > 0,
       str(s["caliber_sizes"]))


# ───────────────────── B 组：离线注入反证 ─────────────────────
def group_b() -> None:
    print("B 组：离线注入反证（分类有牙 / fail-closed / 封闭性可复算）")
    deny = frozenset(_AUDIT_DENY_MODULES)
    std = frozenset(getattr(sys, "stdlib_module_names", ()) or ())

    ck("B01 tail_line 取最后非空行",
       ifa.tail_line("a\n\nb\n   \n") == "b", ifa.tail_line("a\n\nb\n   \n"))
    ck("B02 tail_line 空串 fail-closed", ifa.tail_line("") == ""
       and ifa.tail_line(None) == "")

    cases = [
        ("B03", "PermissionError: V6_SANDBOX_DENY:import:ctypes",
         ("sandbox_deny", "ctypes")),
        ("B04", "PermissionError: V6_SANDBOX_DENY:import:fcntl",
         ("sandbox_deny", "fcntl")),
        ("B05", "PermissionError: V6_SANDBOX_DENY:import:evil_module",
         ("other_unknown", "deny-not-in-caliber:evil_module")),
        ("B06",
         "ImportError: cannot import name 'MutableSet' from 'collections' (x)",
         ("stdlib_incompat", "collections.MutableSet")),
        ("B07", "ImportError: cannot import name 'X' from 'vendor' (x)",
         ("other_unknown", "non-stdlib:vendor")),
        ("B08", "ImportError: attempted relative import with no known parent",
         ("relative_import", "")),
        ("B09", "ModuleNotFoundError: No module named 'attr._compat'",
         ("missing_module", "attr._compat")),
        ("B10", "", ("other_unknown", "")),
        ("B11", "RuntimeError: something else entirely",
         ("other_unknown", "")),
    ]
    for tag, tail, expect in cases:
        got = ifa.classify_import_error(tail, deny, std)
        ck("%s 分类 %s" % (tag, tail[:45] or "<empty>"), got == expect,
           str(got))

    prefixed = ("Traceback (most recent call last):\n"
                "  PermissionError: V6_SANDBOX_DENY:import:ctypes\n"
                "RuntimeError: final line wins")
    ck("B12 只认末行，前缀命中不许冒充",
       ifa.classify_import_error(prefixed, deny, std)
       == ("other_unknown", ""))

    for tag, a, b, expect in (
            ("B13", "e", "e", "both_import"),
            ("B14", "e", "", "fixed_only"),
            ("B15", "", "e", "buggy_only"),
            ("B16", "", "", "both_ok")):
        ck("%s 两侧账 %s" % (tag, expect),
           ifa.pair_verdict(a, b) == expect)

    src = json.loads(SOURCE.read_text(encoding="utf-8"))
    nra = json.loads((ROOT / "scripts" / "no_result_attribution.json")
                     .read_text(encoding="utf-8"))
    fsa = json.loads((ROOT / "scripts" / "form_shape_attribution.json")
                     .read_text(encoding="utf-8"))
    base = ifa.closure_of(src, nra, fsa, 9)
    ck("B17 基准封闭性 6 等式全绿",
       all(base[k] is True for k in CLOSURE_EQS))
    bad1 = copy.deepcopy(src)
    bad1["rows"] = bad1["rows"][:-1]
    ck("B18 篡改主账条数 ⇒ eq_main_135 转红",
       ifa.closure_of(bad1, nra, fsa, 9)["eq_main_135"] is False)
    ck("B19 篡改 n_this ⇒ eq_180 转红",
       ifa.closure_of(src, nra, fsa, 8)["eq_180_matches_import_failed"]
       is False)
    bad2 = copy.deepcopy(src)
    bad2["form_diagnostic_rows"] = bad2["form_diagnostic_rows"][:-1]
    ck("B20 篡改 form 账 ⇒ eq_254 转红",
       ifa.closure_of(bad2, nra, fsa, 9)["eq_254"] is False)
    ck("B21 两本账分开（main_verdicts 是 dict 不是布尔）",
       isinstance(base["main_verdicts"], dict)
       and sum(base["main_verdicts"].values()) == 135)
    ck("B22 GATES 封闭集合恰 5 格", ifa.GATES == (
        "sandbox_deny", "stdlib_incompat", "missing_module",
        "relative_import", "other_unknown"))
    ck("B23 PAIRS 封闭集合恰 5 格", ifa.PAIRS == (
        "both_import", "fixed_only", "buggy_only", "both_ok", "skipped"))
    ck("B24 词表同源：ctypes/fcntl 在产品 deny 名单内",
       {"ctypes", "fcntl"} <= deny and "collections" in std)
    ck("B25 空词表 ⇒ 分类 fail-closed",
       ifa.classify_import_error(
           "PermissionError: V6_SANDBOX_DENY:import:ctypes",
           frozenset(), std)[0] == "other_unknown")

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
    calls = {n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    ck("B26 正式脚本真的调用 require_baseline（行为级，非文案）",
       "require_baseline" in calls)
    ck("B27 正式脚本真的调用 run_in_sandbox（运行时证据来源）",
       "run_in_sandbox" in calls)
    ck("B28 口径签名钉了尺子自身",
       "scripts/probe_import_failure_attribution.py" in ifa.CALIBER_FILES)


# ───────────────────── C 组：本机 + 真实沙箱正例 ─────────────────────
def group_c() -> None:
    print("C 组：本机健康与真实沙箱正例（⛔ 零命中先证查询能命中）")
    denial = frozenset(_AUDIT_DENY_MODULES)
    std = frozenset(getattr(sys, "stdlib_module_names", ()) or ())
    tests = [{"name": "q0", "call": "1"}]

    try:
        h = require_baseline()
        ck("C1 174 基线锚本机可验",
           h.get("ok") is True and not h.get("diffs"))
    except Exception as exc:                       # noqa: BLE001
        ck("C1 174 基线锚本机可验", False, str(exc))

    try:
        ex = run_in_sandbox("import ctypes\n", tests, timeout=10.0)
        tail = ifa.tail_line(ex.import_error)
        got = ifa.classify_import_error(ex.import_error, denial, std)
        ck("C2 真实 deny 正例能命中 sandbox_deny",
           bool(ex.import_error) and
           "V6_SANDBOX_DENY:import:ctypes" in tail
           and got == ("sandbox_deny", "ctypes"), str(got))
    except Exception as exc:                       # noqa: BLE001
        ck("C2 真实 deny 正例能命中 sandbox_deny", False, str(exc))

    try:
        ex = run_in_sandbox("x = 1\n", tests, timeout=10.0)
        ck("C3 真实正常源码能命中非 import 路径（反恒真）",
           not ex.import_error and ifa.tail_line(ex.import_error) == ""
           and ifa.pair_verdict("", "") == "both_ok"
           and ex.results and ex.results[0].get("why") == "behavior",
           str([(r.get("why"), r.get("evidence", "")[:40])
                for r in ex.results[:1]]))
    except Exception as exc:                       # noqa: BLE001
        ck("C3 真实正常源码能命中非 import 路径（反恒真）", False, str(exc))

    try:
        ex = run_in_sandbox("from collections import MutableSet\n",
                            tests, timeout=10.0)
        got = ifa.classify_import_error(ex.import_error, denial, std)
        ck("C4 真实 stdlib 错配正例能命中",
           bool(ex.import_error) and got
           == ("stdlib_incompat", "collections.MutableSet"), str(got))
    except Exception as exc:                       # noqa: BLE001
        ck("C4 真实 stdlib 错配正例能命中", False, str(exc))

    fake = "PermissionError: V6_SANDBOX_DENY:import:evil_module"
    ck("C5 词表外 deny ⇒ other_unknown（fail-closed 有牙）",
       ifa.classify_import_error(fake, denial, std)[0] == "other_unknown")

    try:
        ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
        ck("C6 尺子脚本语法可解析", True)
    except SyntaxError as exc:
        ck("C6 尺子脚本语法可解析", False, str(exc))

    d = json.loads(READING.read_text(encoding="utf-8"))
    ck("C7 9 条 reach 事后 oracle 只交叉、未当 predictor",
       all(r.get("reach") == "X" for r in d["rows"]))


def main() -> int:
    print("契约：9 条 import_failed 的运行时归因（README 180）")
    group_a()
    group_b()
    group_c()
    print("=" * 60)
    print("pass=%d  fail=%d  skip=%d" % (len(PASSES), len(FAILS), len(SKIPS)))
    if FAILS:
        print("FAILED:")
        for n in FAILS:
            print("  - %s" % n)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
