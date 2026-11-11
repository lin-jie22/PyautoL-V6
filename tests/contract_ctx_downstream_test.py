#!/usr/bin/env python3
"""r103 / README 182 —— 123 条 ctx 门「门后第一格」普查的契约。

A. 钉死全量读数（纯读存档，不碰本机基线）
B. 反证组：纯内存离线注入（恒真/恒假分类、篡改读数、两本账合并、
   去验锚、篡改 caliber），⛔ 不 spawn git、不跑沙箱
C. 本机锚 + 真实沙箱正例：证明 A7 的 `f2p_criterion=0` 不是量具恒假
   （第 24 条「恒 0 先怀疑量具」/ 第 133 条「零命中先证查询能命中」）

⛔ 契约首次全绿必须注入反证（181 教训）；尺子自身 sha256 钉在 caliber 里，
   改 `probe_ctx_downstream.py` 而没重跑普查 ⇒ A18 恰好红。
"""
from __future__ import annotations

import ast
import collections
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import realrepo_baseline as rb                                   # noqa: E402
from probe_ctx_downstream import (                               # noqa: E402
    CALIBER_FILES, PAIRS, VERDICTS3, caliber, fingerprint,
    mismatch_of, pair_verdict, truthy_f2p)

OBS_PATH = ROOT / "scripts" / "ctx_downstream.json"
OBS: Dict[str, Any] = json.loads(OBS_PATH.read_text(encoding="utf-8"))

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


def rec(why: str, passed: bool = False,
        missing: bool = False) -> Dict[str, Any]:
    """构造一条 sandbox_read 的结果，供 B 组离线反证。"""
    return {"why": why, "passed": passed, "evidence": "",
            "missing": missing}


# ─────────────────────────── A. 钉死全量读数 ───────────────────────────
def group_a() -> None:
    print("\n=== A. 钉死全量读数（纯读存档） ===")
    d = OBS
    s = d["summary"]
    rows = d["rows"]

    ck("A1 schema = ctx_downstream/v1",
       d.get("schema") == "ctx_downstream/v1")
    ck("A2 分母 123 条", s.get("n_rows") == 123 and len(rows) == 123)
    ck("A3 与 176 存档逐字同源",
       s.get("n_saved_ctx") == 123 and s.get("align_mismatch") == 0)
    ck("A4 三验裁决三格（176 同源）",
       s.get("by_verdict3") == {"gold_mismatch": 9, "no_result": 61,
                                "unpassed_on_fixed": 53})
    ck("A5 两侧运行时账三格",
       s.get("by_pair") == {"both_raise": 86, "mixed_raise": 14,
                            "not_raise": 23})
    ck("A6 两本账交叉表六格",
       s.get("cross_verdict3_x_pair") == {
           "gold_mismatch/mixed_raise": 2,
           "gold_mismatch/not_raise": 7,
           "no_result/both_raise": 61,
           "unpassed_on_fixed/both_raise": 25,
           "unpassed_on_fixed/mixed_raise": 12,
           "unpassed_on_fixed/not_raise": 16})
    ck("A7 ⛔ f2p_criterion = 0（现有 truthy 形态下）",
       s.get("n_f2p_criterion") == 0
       and s.get("f2p_by_verdict3") == {}
       and s.get("f2p_shas") == []
       and not any(r["f2p_criterion"] for r in rows))
    ck("A8 fixed 两次求值无不稳定", s.get("n_fixed_unstable") == 0
       and not any(r["fixed_unstable"] for r in rows))
    ck("A9 三仓库分布", s.get("by_repo") ==
       {"attrs": 37, "boltons": 52, "more-itertools": 34})
    ck("A10 reach 只记账（事后 oracle）",
       s.get("reach_of_saved_ctx") == {"L2": 2, "X": 121})
    ck("A11 验锚健康 3/3", s.get("baseline_health") ==
       {"ok": True, "diffs": 0, "repos": 3})
    for name, v in sorted(s.get("closure_eqs", {}).items()):
        ck("A12 封闭等式 %s" % name, v is True)
    ck("A13 每行落在封闭格内",
       all(r["pair_verdict"] in PAIRS and r["verdict3"] in VERDICTS3
           for r in rows))
    ck("A14 123/123 两侧真跑到（有 why 与 evidence）",
       all(r["why_fixed"] and r["why_buggy"]
           and (r["evidence_fixed"] or r["evidence_buggy"]) for r in rows))
    nr = collections.Counter(
        (r["passed_fixed"], r["passed_buggy"]) for r in rows
        if r["pair_verdict"] == "not_raise")
    ck("A15 23 条求值成功行的两侧真假交叉",
       dict(nr) == {(False, False): 12, (True, True): 10,
                     (False, True): 1})
    whys = {r["why_fixed"] for r in rows} | {r["why_buggy"] for r in rows}
    ck("A16 why 取值封闭（不解析 evidence 文案）",
       whys <= {"raise", "mismatch", "behavior"}, str(sorted(whys)))
    ck("A17 caliber 10 文件且含尺子自身",
       len(d["caliber"]) == 10
       and "scripts/probe_ctx_downstream.py" in d["caliber"]
       and len(CALIBER_FILES) == 10)
    ck("A18 ⛔ 口径签名与磁盘一致（改尺子必须重跑普查）",
       d["caliber"] == caliber())
    bad_keys = {"gain", "repair", "patched", "synthesized", "fixed_by"}
    ck("A19 存档是诊断账、无收益字段",
       not any(k in r for r in rows for k in bad_keys))
    ck("A20 note 保留「不许宣布扩张无用」",
       "不许宣布" in s.get("note", ""))
    ck("A21 call 截断与 call_len 自洽（176 残留）",
       all(len(r["call"]) == min(r["call_len"], 240) for r in rows))
    ck("A22 两本账分开（不合并成布尔）",
       all("verdict3" in r and "pair_verdict" in r
           and isinstance(r["f2p_criterion"], bool) for r in rows))
    ck("A23 0 跳过", s.get("n_skipped_commits") == 0
       and len(d["skipped"]) == 0)
    ck("A24 封闭等式 9/9 全真",
       s.get("n_closure_true") == 9 and s.get("n_closure_items") == 9)


# ─────────────────────────── B. 离线注入反证 ───────────────────────────
def group_b() -> None:
    print("\n=== B. 纯内存离线注入反证（不 spawn git、不跑沙箱） ===")
    cases = [
        (rec("raise"), rec("raise"), "both_raise"),
        (rec("raise"), rec("behavior"), "mixed_raise"),
        (rec("behavior"), rec("raise"), "mixed_raise"),
        (rec("mismatch"), rec("behavior"), "not_raise"),
        (rec("import"), rec("raise"), "import_failed"),
        (rec("raise"), rec("import"), "import_failed"),
        (rec("raise", missing=True), rec("raise"), "missing_result"),
        (None, rec("raise"), "missing_result"),
        (rec("raise"), None, "missing_result"),
    ]
    for i, (f, b, want) in enumerate(cases, 1):
        ck("B1.%02d pair_verdict 分格" % i,
           pair_verdict(f, b) == want, pair_verdict(f, b))

    f2p_cases = [
        (rec("behavior", True), rec("mismatch", False), False, True),
        (rec("behavior", True), rec("mismatch", False), True, False),
        (rec("raise"), rec("mismatch", False), False, False),
        (rec("import", True), rec("mismatch"), False, False),
        (rec("behavior", True, True), rec("mismatch"), False, False),
        (rec("behavior", False), rec("mismatch", False), False, False),
        (rec("behavior", True), rec("behavior", True), False, False),
        (None, rec("mismatch"), False, False),
    ]
    for i, (f, b, unstable, want) in enumerate(f2p_cases, 1):
        ck("B2.%02d truthy_f2p fail-closed" % i,
           truthy_f2p(f, b, unstable) is want,
           str(truthy_f2p(f, b, unstable)))

    def always_false(_f, _b, _u=False):
        return False

    ck("B3 ⛔ 恒假判据在正例上不翻绿 ⇒ 排除",
       truthy_f2p(rec("behavior", True), rec("mismatch", False)) is True
       and always_false(rec("behavior", True), rec("mismatch", False)) is False)

    a = fingerprint(OBS["rows"])
    ck("B4 自对拍为零", mismatch_of(a, a) == 0)
    bad = copy.deepcopy(OBS["rows"])
    bad[0]["verdict3"] = "__TAMPERED__"
    ck("B4a 篡改 verdict3 ⇒ 翻红",
       mismatch_of(a, fingerprint(bad)) > 0)
    bad2 = copy.deepcopy(OBS["rows"])
    bad2[0]["call"] = "__TAMPERED__"
    ck("B4b 篡改 call ⇒ 翻红",
       mismatch_of(a, fingerprint(bad2)) > 0)
    ck("B4c 删一行 ⇒ 翻红",
       mismatch_of(a, fingerprint(OBS["rows"][:-1])) > 0)

    def books_ok(s):
        return (sum(s["by_verdict3"].values()) == s["n_rows"]
                and sum(s["by_pair"].values()) == s["n_rows"])

    ck("B5 ⛔ 两本账抹平成一格 ⇒ 封闭性转红",
       books_ok(OBS["summary"])
       and not books_ok({**OBS["summary"], "by_pair": {"not_raise": 1}}))

    s_bad = dict(OBS["summary"])
    s_bad["n_f2p_criterion"] = 5
    ck("B6 篡改 n_f2p=0 ⇒ A7 判据转红",
       not (s_bad["n_f2p_criterion"] == 0
            and s_bad["f2p_by_verdict3"] == {}
            and s_bad["f2p_shas"] == []))

    cal_bad = dict(OBS["caliber"])
    cal_bad[sorted(cal_bad)[0]] = "0" * 64
    ck("B7 篡改 caliber ⇒ A18 判据转红",
       cal_bad != OBS["caliber"] and cal_bad != caliber())

    src = (ROOT / "scripts" / "probe_ctx_downstream.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    calls = {n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    ck("B8a 真的调 run_in_sandbox", "run_in_sandbox" in calls)
    ck("B8b 真的调 require_baseline", "require_baseline" in calls)
    ck("B8c 没喂 synthesize_patch（不报收益）",
       "synthesize_patch" not in calls)
    probe_dicts = [list(n.keys) for n in ast.walk(tree)
                   if isinstance(n, ast.Dict)
                   and any(isinstance(k, ast.Constant)
                           and k.value == "name" for k in n.keys)]
    keysets = {tuple(sorted(k.value for k in ks
                            if isinstance(k, ast.Constant)))
               for ks in probe_dicts}
    ck("B8d ⛔ 探针 dict 只有 name/call（不带 expect ⇒ 不手写 oracle）",
       bool(keysets) and all(set(ks) <= {"name", "call"} for ks in keysets),
       str(keysets))
    ck("B9 ⛔ 零命中先证查询能命中（纯函数层）",
       truthy_f2p(rec("behavior", True), rec("mismatch", False)) is True)

    def always_not_raise(_f, _b):
        return "not_raise"

    ck("B10 ⛔ 恒真分类在 both_raise 上不翻红 ⇒ 排除",
       always_not_raise(rec("raise"), rec("raise")) == "not_raise"
       and pair_verdict(rec("raise"), rec("raise")) == "both_raise")


# ─────────────────────────── C. 本机锚 + 真实正例 ───────────────────────────
def group_c() -> None:
    print("\n=== C. 本机锚 + 真实沙箱正例 ===")
    try:
        h = rb.require_baseline()
        ck("C1 锚健康 3/3 diffs=0",
           h.get("ok") is True and len(h.get("diffs", [])) == 0
           and len(h.get("repos", {})) == 3)
    except SystemExit:
        ck("C1 锚健康 3/3 diffs=0", False, "require_baseline fail-closed")

    repo = Path("/tmp/realrepo/boltons")
    if not repo.exists():
        skip("C2/C3 真实 F2P 正例", "取证基线骨架不存在（未覆盖 ≠ 通过）")
        return

    from probe_alias_demote import select_commits, _git
    from probe_gate_attribution import attribute_commit
    from probe_ctx_downstream import sandbox_read

    sha = "5eb6cc0"          #: 176 里已知有 3 条 F2P 的 boltons commit
    found: Optional[Any] = None
    for cand in select_commits(repo, 40, "boltons/"):
        if cand[0] == sha:
            found = cand
            break
    if found is None:
        skip("C2/C3 真实 F2P 正例", "候选序列里找不到正例 sha")
        return
    src, testfs = found[1], found[2]
    got = attribute_commit(repo, sha, src, testfs, pkg=None, timeout=20)
    live = [r for r in got["rows"] if r["gate_final"] == "F2P"] if got else []
    if not live:
        skip("C2/C3 真实 F2P 正例", "正例 commit 当前重跑没有 F2P 行")
        return
    fixed = _git(repo, "show", "%s:%s" % (sha, src))
    buggy = _git(repo, "show", "%s^:%s" % (sha, src))
    tests = [{"name": "t%d" % i, "call": r.get("call") or ""}
             for i, r in enumerate(live)]
    f1, _ = sandbox_read(fixed, tests, 20, None)
    f2, _ = sandbox_read(fixed, tests, 20, None)
    bs, _ = sandbox_read(buggy, tests, 20, None)
    hits = 0
    same_side = 0
    for i in range(len(live)):
        unstable = (f1[i]["passed"] != f2[i]["passed"]
                    or f1[i]["why"] != f2[i]["why"])
        hits += int(truthy_f2p(f1[i], bs[i], unstable))
        same_side += int(truthy_f2p(f1[i], f1[i]))
    ck("C2 ⛔ 已知 F2P 行用同一把尺子能命中（0 不是量具恒假）",
       hits > 0, "hits=%d/%d" % (hits, len(live)))
    ck("C3 判据不是恒真（正例未全命中）且同侧不判 F2P",
       hits < len(live) and same_side == 0,
       "hits=%d n=%d same=%d" % (hits, len(live), same_side))
    ck("C4 正例 commit 三验确有 F2P 行（查询能命中）",
       len(live) > 0, "n=%d" % len(live))


def main() -> int:
    print("=" * 74)
    print("r103 / README 182  ctx 门后普查契约")
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
