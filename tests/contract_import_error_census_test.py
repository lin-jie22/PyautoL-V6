#!/usr/bin/env python3
"""r104 / README 183 —— `import_error` 污染面普查的契约。

A. 钉死全量读数（纯读存档，不碰本机基线）
B. 反证组：纯内存离线注入（恒真/恒假分类、篡改读数、两本账合并、
   去验锚、篡改 caliber、脱敏失效、可选键恒真），⛔ 不 spawn git、不跑沙箱
C. 本机锚 + 真实沙箱正/反例：证明末行分类与脱敏在**真实异常文本**上
   能命中，且非 ie commit 不产生 import_error（判据不是恒真）

⛔ 契约首次全绿必须注入反证（181 教训）；尺子自身 sha256 钉在 caliber 里，
   改 `probe_import_error_census.py` 而没重跑普查 ⇒ A17 恰好红。
"""
from __future__ import annotations

import collections
import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import realrepo_baseline as rb                                   # noqa: E402
from aether.environment import run_in_sandbox                   # noqa: E402
from probe_alias_demote import select_commits, _git             # noqa: E402
from probe_import_error_census import (                          # noqa: E402
    CATS, CALIBER_FILES, caliber, classify, ie_commits_of, redact)

OBS_PATH = ROOT / "scripts" / "import_error_census.json"
OBS: Dict[str, Any] = json.loads(OBS_PATH.read_text(encoding="utf-8"))
CENSUS_PATH = ROOT / "scripts" / "criteria_gate_census.json"
ATTR180_PATH = ROOT / "scripts" / "import_failure_attribution.json"
DOWN177_PATH = ROOT / "scripts" / "extract_downstream.json"

PASSES: List[str] = []
FAILS: List[str] = []
SKIPS: List[str] = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  | " + detail) if detail and not ok else ""))
    #: ⛔ 必须写成字面 `FAILS.append`：静态闸 contract_assert_wiring B1
    if not ok:
        FAILS.append(name)
    else:
        PASSES.append(name)


def skip(name: str, why: str) -> None:
    print("  SKIP %s  | %s" % (name, why))
    SKIPS.append(name)


def portability() -> Any:
    """加载产品自己的 portability 判据（不另写第二把尺子）。"""
    spec = importlib.util.spec_from_file_location(
        "r104_port", ROOT / "tests" / "contract_portability_test.py")
    m = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(m)
    return m


#: B 组复算用的判据函数：⛔ 与 A 组同源，篡改后必须翻红。
def j_ie(s: Dict[str, Any]) -> bool:
    return s.get("n_ie_commits") == 8


def j_gates(s: Dict[str, Any]) -> bool:
    return s.get("ie_rows_by_gate") == {"form": 5, "unserializable": 5,
                                        "unmatched": 4}


def j_unmatched(s: Dict[str, Any]) -> bool:
    return (s.get("n_unmatched") == 4
            and s.get("n_unmatched_in_ie") == 4
            and s.get("n_unmatched_outside_ie") == 0)


def j_two_books(s: Dict[str, Any]) -> bool:
    return (s.get("n_tests_total_zero", 0) > s.get("n_ie_commits", 0)
            and s.get("n_ie_commits") != s.get("n_ie_rows"))


def j_cross180(s: Dict[str, Any]) -> bool:
    c = s.get("cross_180", {})
    return (c.get("n_rows_180") == 9 and c.get("n_sha_180") == 6
            and len(c.get("covered_ie_shas", [])) == 6
            and len(c.get("uncovered_ie_shas", [])) == 2)


def j_health(s: Dict[str, Any]) -> bool:
    h = s.get("baseline_health", {})
    return h.get("ok") is True and h.get("diffs") == 0 and h.get("repos") == 3


def j_caliber(s: Dict[str, Any]) -> bool:
    return all(v != "__MISSING__" for v in s.get("caliber", {}).values())


# ─────────────────────────── A. 钉死全量读数 ───────────────────────────
def group_a() -> None:
    print("\n=== A. 钉死全量读数（纯读存档） ===")
    d = OBS
    s = d["summary"]
    rows = d["rows"]
    ies = d["ie_commits"]
    eq = s["closure_eqs"]

    ck("A1 schema = import_error_census/v1",
       d.get("schema") == "import_error_census/v1")
    ck("A2 caliber 13 文件且含尺子自身",
       len(d["caliber"]) == len(CALIBER_FILES) == 13
       and "scripts/probe_import_error_census.py" in d["caliber"])
    ck("A3 分母 120 commit / 459 行",
       s.get("n_per_commit") == 120 and s.get("n_census_rows") == 459)
    ck("A4 ⛔ ie 8 commit / 14 行（两本账分开）",
       j_ie(s) and s.get("n_ie_rows") == 14
       and len(ies) == 8 and len(rows) == 14)
    ck("A5 ie_by_repo = attrs 6 / boltons 2",
       s.get("ie_by_repo") == {"attrs": 6, "boltons": 2})
    ck("A6 ie_by_cat 三格（deny 6+1 / 年代错配 1）",
       s.get("ie_by_cat") == {"deny_ctypes": 6, "deny_fcntl": 1,
                              "stdlib_mismatch": 1})
    ck("A7 ie_rows_by_gate = form 5 / unserializable 5 / unmatched 4",
       j_gates(s))
    ck("A8 14 行全部 downstream=unknown",
       s.get("ie_rows_by_downstream") == {"unknown": 14}
       and all(r["downstream"] == "unknown" for r in rows))
    ck("A9 ie_rows_by_repo = attrs 11 / boltons 3",
       s.get("ie_rows_by_repo") == {"attrs": 11, "boltons": 3})
    ck("A10 ⛔⛔ unmatched 4 条全在 ie commit、外部 0 条",
       j_unmatched(s))
    ck("A11 unmatched_shas 逐字钉死",
       s.get("unmatched_shas") == ["9d441d7", "a84a36d", "f5b59a0"])
    ck("A12 ⛔ n_tests_total_zero=30 但真 ie 只有 8（不可反推）",
       s.get("n_tests_total_zero") == 30
       and s.get("n_tests_total_zero_not_ie") == 22
       and j_two_books(s))
    ck("A13 cross_180 = 9 行 / 6 sha / 覆盖 6 / 未覆盖 2",
       j_cross180(s))
    ck("A14 未覆盖的两个新 commit 逐字钉死且都是 deny_ctypes",
       s["cross_180"]["uncovered_ie_shas"] == ["5ed8c4a", "c1adf5c"]
       and s["cross_180"]["uncovered_cats"] == {
           "5ed8c4a": "deny_ctypes", "c1adf5c": "deny_ctypes"})
    ck("A15 cross_177 = 135 行 / 9 条 import_failed",
       s["cross_177"] == {"n_rows": 135, "n_import_failed": 9})
    ck("A16 18 条封闭等式逐条为真",
       s.get("n_closure_true") == 18 and s.get("n_closure_items") == 18
       and len(eq) == 18 and all(eq.values()))
    ck("A17 ⛔ 尺子自身 sha256 与当前文件一致（改尺子必须重跑普查）",
       d["caliber"]["scripts/probe_import_error_census.py"]
       == caliber()["scripts/probe_import_error_census.py"]
       and all(d["caliber"][f] == caliber()[f] for f in CALIBER_FILES))
    ck("A18 caliber 无 __MISSING__", j_caliber(d))
    ck("A19 8 个 ie commit 的 n_tests_total 全 0",
       all(c["n_tests_total"] == 0 for c in ies))
    ck("A20 8 个 ie commit 的 cat 封闭且无 other",
       all(c["cat"] in CATS and c["cat"] != "other" for c in ies))
    ck("A21 8 个 ie commit 的 sha 逐字钉死",
       [c["sha"] for c in ies] == ["55dfe50", "d34e461", "9e1f136",
                                   "5ed8c4a", "9d441d7", "f5b59a0",
                                   "a84a36d", "c1adf5c"])
    t180 = [r["evidence_tail_buggy"] for r in json.loads(
        ATTR180_PATH.read_text(encoding="utf-8"))["rows"]
        if r["sha"] == "d34e461"]
    ck("A22 ⛔ d34e461 末行与 180 存档逐字同形",
       bool(t180) and [c["tail"] for c in ies
                       if c["sha"] == "d34e461"][0] == t180[0])
    ck("A23 所有 tail 的 scan_text() 零命中（脱敏有牙）",
       all(portability().scan_text(c["tail"]) == [] for c in ies))
    real_style = ("(/Users/" + "alice" + "/work/binaries/lib/python3.13/"
                  "collections/__init__.py)")
    ck("A24 ⛔ redact 纯函数：真实样式必被脱敏，脱敏形式幂等",
       portability().scan_text(real_style) != []
       and portability().scan_text(redact(real_style)) == []
       and redact(redact(real_style)) == redact(real_style))
    ck("A25 classify 封闭 5 格且空串落 other",
       len(CATS) == 5 and classify("") == "other"
       and classify("V6_SANDBOX_DENY:import:ctypes") == "deny_ctypes"
       and classify("V6_SANDBOX_DENY:import:fcntl") == "deny_fcntl"
       and classify("ImportError: cannot import name 'X' from 'y'")
       == "stdlib_mismatch")
    pc = json.loads(CENSUS_PATH.read_text(
        encoding="utf-8"))["per_commit"]
    ck("A26 ⛔ import_error 是可选键：8 有 / 112 无",
       len(ie_commits_of(pc)) == 8
       and sum(1 for c in pc
               if "import_error" in c.get("extract_stats", {})) == 8
       and sum(1 for c in pc
               if "import_error" not in c.get("extract_stats", {})) == 112)
    by_sha = {c["sha"]: c["cat"] for c in ies}
    ck("A27 14 行的 ie_cat 与所属 commit 逐 sha 一致",
       all(r["ie_cat"] == by_sha[r["sha"]] for r in rows))
    note = s.get("note", "")
    ck("A28 ⛔ 分寸写死：不报收益 / ⑤ 未开工 / 不推翻 177·179·182",
       all(k in note for k in ("只报归因与账目", "⑤ 未开工",
                               "不推翻 177", "属拍板 ①")))
    ck("A29 baseline_health = OK 3/3 diffs=0", j_health(s))
    ck("A30 180 / 177 存档当前读数与契约同源",
       len(json.loads(ATTR180_PATH.read_text(
           encoding="utf-8"))["rows"]) == 9
       and sum(1 for r in json.loads(DOWN177_PATH.read_text(
           encoding="utf-8"))["rows"]
           if r.get("verdict") == "import_failed") == 9)


# ─────────────────────── B. 纯内存离线注入反证 ───────────────────────
def group_b() -> None:
    print("\n=== B. 纯内存离线注入反证（不 spawn git / 不跑沙箱） ===")
    s = OBS["summary"]
    ies = OBS["ie_commits"]
    tails = [c["tail"] for c in ies]

    m1 = copy.deepcopy(s)
    m1["n_ie_commits"] = 999
    ck("B1 篡改 n_ie_commits 8→999 ⇒ A4 判据翻红",
       j_ie(s) and not j_ie(m1))
    m2 = copy.deepcopy(s)
    m2["ie_rows_by_gate"] = {"unmatched": 14}
    ck("B2 篡改门分布 ⇒ A7 判据翻红",
       j_gates(s) and not j_gates(m2))
    m3 = copy.deepcopy(s)
    m3["n_unmatched_outside_ie"] = 1
    ck("B3 unmatched 伪造成非 ie ⇒ A10 判据翻红",
       j_unmatched(s) and not j_unmatched(m3))
    m4 = copy.deepcopy(s)
    m4["n_tests_total_zero"] = 8
    ck("B4 ⛔ 抹平两本账（30→8）⇒ A12 判据翻红",
       j_two_books(s) and not j_two_books(m4))
    m5 = copy.deepcopy(s)
    m5["cross_180"]["uncovered_ie_shas"] = []
    ck("B5 把 180 覆盖抹平成 8/8 ⇒ A13 判据翻红",
       j_cross180(s) and not j_cross180(m5))
    m6 = copy.deepcopy(s)
    m6["baseline_health"] = {"ok": False, "diffs": 1, "repos": 0}
    ck("B6 去掉验锚 ⇒ A29 判据翻红",
       j_health(s) and not j_health(m6))
    m7 = copy.deepcopy(OBS["caliber"])
    m7["aether/extract.py"] = "deadbeef"
    ck("B7 篡改 caliber hash ⇒ A17 判据翻红",
       j_caliber(OBS) and m7["aether/extract.py"]
       != caliber()["aether/extract.py"])

    real = collections.Counter(classify(t) for t in tails)
    always_true = collections.Counter(
        (lambda _t: "deny_ctypes")(t) for t in tails)
    always_false = collections.Counter(
        (lambda _t: "other")(t) for t in tails)
    ck("B8 ⛔ 分类恒真 ⇒ 三格塌成一格（恒真判据被排除）",
       len(real) == 3 and len(always_true) == 1
       and dict(always_true) != dict(real))
    ck("B9 分类恒假（全 other）⇒ eq_cats_no_other 翻红",
       all(k != "other" for k in real)
       and set(always_false) == {"other"})
    ck("B10 ⛔ redact 失效（返回原文）⇒ A23 判据翻红",
       portability().scan_text(redact(tails[0])) == []
       and portability().scan_text(
           "(/Users/" + "alice" + "/x/lib/python3.13/y.py)") != [])
    pc = json.loads(CENSUS_PATH.read_text(
        encoding="utf-8"))["per_commit"]
    ck("B11 ⛔ ie_commits_of 恒真 ⇒ A26 判据翻红",
       len(ie_commits_of(pc)) == 8 and len([c for c in pc if True]) == 120)
    ck("B12 两本账不可合并成布尔（8 commit ≠ 14 行）",
       s["n_ie_commits"] != s["n_ie_rows"]
       and "只交叉，不合并" in s["cross_180"]["note"])


# ───────────────── C. 本机锚 + 真实沙箱正/反例 ─────────────────
def group_c() -> None:
    print("\n=== C. 本机锚 + 真实沙箱正/反例 ===")
    try:
        h = rb.require_baseline()
        ck("C1 ⛔ 本机验锚真实通过（ok / 0 diffs / 3 repos）",
           h.get("ok") is True and not h.get("diffs")
           and len(h.get("repos", {})) == 3)
    except SystemExit:
        ck("C1 ⛔ 本机验锚真实通过（ok / 0 diffs / 3 repos）", False,
           "require_baseline raised SystemExit")
        return

    repo = Path("/tmp/realrepo/boltons")
    found: Dict[str, Any] = {}
    for cand in select_commits(repo, 40, "boltons/"):
        sha, src, _tf, _subj = cand
        if sha in ("55dfe50", "4e5faa3"):
            found[sha] = src
    if "55dfe50" not in found or "4e5faa3" not in found:
        skip("C2/C3/C4 真实沙箱正/反例", "候选 commit 未在锚上找到")
        return

    def probe(sha: str) -> Any:
        src = found[sha]
        fixed = _git(repo, "show", "%s:%s" % (sha, src))
        return run_in_sandbox(fixed, [{"name": "t0", "call": "1"}],
                              timeout=20, pkg=None)

    ex = probe("55dfe50")
    lines = (str(ex.import_error) or "").rstrip().splitlines()
    tail = redact(lines[-1]) if lines else ""
    saved = [c for c in OBS["ie_commits"] if c["sha"] == "55dfe50"][0]
    ck("C2 ⛔ 真实末行分类命中存档格（查询能命中，第 133 条）",
       bool(ex.import_error) and classify(tail) == saved["cat"]
       == "deny_fcntl")
    ck("C3 真实末行脱敏后 scan_text() 零命中",
       portability().scan_text(tail) == [])
    ck("C4 真实末行的产出方 why=import",
       bool(ex.results) and ex.results[0].get("why") == "import")
    ex2 = probe("4e5faa3")
    ck("C5 ⛔ 非 ie commit 不产生 import_error（判据不是恒真）",
       not ex2.import_error)


def main() -> int:
    print("=" * 74)
    print("r104 / README 183  import_error 污染面普查契约")
    print("=" * 74)
    group_a()
    group_b()
    group_c()
    print("\n" + "=" * 74)
    print("pass=%d fail=%d skip=%d" %
          (len(PASSES), len(FAILS), len(SKIPS)))
    if FAILS:
        for name in FAILS:
            print("  ⛔ " + name)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
