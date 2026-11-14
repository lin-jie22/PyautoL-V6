#!/usr/bin/env python3
"""r110 / README 189 契约：四条自限「全放宽且默认生效」的口径 v2。

═══ 钉死什么 ═══

用户 2026-11-14 显式拍板「四条全放宽且默认生效」，覆盖 154 的原始约束
「每条都带反证，不许为救回率放宽」。⛔ 这是**判据语义变更**，不是修 bug：

  · 默认策略 `1111`，`--strict` 回到 `0000`（旧读数可复算）；
  · ③④ 是判据正确性锚 ⇒ 放宽 ≡ 接受不可复现判据 / 假鉴别力进默认路径；
  · **两本账仍分开**：放宽态多采纳的全在 `relaxed_*` 档且语义未证，
    绝不进 `rescued_gold` / `disc_gold`（契约④）；
  · 旧三份存档的 caliber 走 superseded 登记，旧值另存、不回写（契约①）。

═══ 组 ═══

  A 组（钉读数）    36 行策略矩阵 / 四条开关逐条有牙 / 两本账 /
                    94·90·4·119 静态靶子 / 合成沙箱 / superseded / closure
  B 组（纯内存反证）⛔ 直接喂**尺子自己的** `derive` / `evaluate_case` /
                    `caliber_matches_superseded`，不另写第二份判据
  C 组（本机实战）  真跑沙箱、真实验锚、磁盘 hash 对拍、A1 扫描面、
                    量具自检与默认策略

⛔ 首次全绿必须注入反证（`/tmp/r110_falsify.py`）：A/B 组逐条篡改存档
字段、C 组逐条把条件撤回为 False，确认**恰好**命中预期项（契约⑥）。
⛔ 本契约纯只读取证：零真实 expr、零真实 commit、零 F2P / 收益读数。
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
#: ⛔ 契约根从 __file__ 推导：事故版被反证脚本放到别处跑时 cwd 不再是
#:    tests/ ⇒ 显式把 tests/ 加进 sys.path（第 99 条）。
sys.path.insert(0, str(ROOT / "tests"))

import caliber_superseded as cs                                  # noqa: E402
import probe_inliner_relax as pir                                # noqa: E402
from contract_portability_test import (                            # noqa: E402
    candidate_files, scan_text,
)
from realrepo_baseline import require_baseline                   # noqa: E402

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

CENSUS = ROOT / "scripts" / "inliner_relax_census.json"
NEW_FILES = (
    "README.md",
    "scripts/probe_context_gate.py",
    "scripts/caliber_superseded.py",
    "scripts/caliber_superseded.json",
    "scripts/probe_inliner_relax.py",
    "scripts/inliner_relax_census.json",
    "tests/contract_inliner_relax_test.py",
)
FAILS: List[str] = []
CG = pir.CG


def ck(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (" | " + str(detail) if detail and not cond else ""))


def row(rows: List[Dict[str, Any]], sig: str, case: str) -> Dict[str, Any]:
    for r in rows:
        if r["policy"] == sig and r["case"] == case:
            return r
    return {}


def conditions(d: Dict[str, Any], reg: Dict[str, Any]) -> Dict[str, bool]:
    """A+B 组：**纯函数**，零沙箱零子进程，供反证脚本逐条篡改。"""
    V: Dict[str, bool] = {}
    acc = d.get("accounts", {})
    A = acc.get("A_policy_matrix", {})
    B = acc.get("B_178_static_target", {})
    C = acc.get("C_synthetic_sandbox", {})
    D = acc.get("D_superseded_boundary", {})
    rows = A.get("rows", [])
    der = d.get("derived", {})
    bp = der.get("by_policy", {})
    tl = der.get("two_ledgers", {})
    pols = A.get("policies", {})
    gate = B.get("by_main_gate", {})

    V["A1"] = d.get("schema") == "inliner_relax_census/v2"
    V["A2"] = set(acc) == {"A_policy_matrix", "B_178_static_target",
                           "C_synthetic_sandbox", "D_superseded_boundary"}
    V["A3"] = (A.get("n_rows") == 36 and A.get("n_policies") == 6
               and A.get("n_cases") == 6 and len(rows) == 36)
    V["A4"] = (set(pols) == {"0000", "1000", "0100", "0010", "0001", "1111"}
               and all(k == v for k, v in pols.items()))
    V["A5"] = all(sum(1 for r in rows if r.get("policy") == s) == 6
                  for s in pols)
    V["A6"] = (set(der.get("switch_teeth", {})) == {
        "1_cross_scope", "2_first_rhs", "3_allow_unstable", "4_no_gold_gate"}
        and all(der["switch_teeth"].values()))
    V["A7"] = bp.get("0000") == {
        "n_rows": 6, "adopted": 1, "verified": 1, "counts_disc": 1,
        "relaxed_ledgers": 0, "disc_blocked": 2, "rejected": 5}
    V["A8"] = bp.get("1111") == {
        "n_rows": 6, "adopted": 6, "verified": 0, "counts_disc": 0,
        "relaxed_ledgers": 6, "disc_blocked": 5, "rejected": 0}
    V["A9"] = (bp.get("0001", {}).get("adopted") == 4
               and bp["0001"]["relaxed_ledgers"] == 4
               and bp["0001"]["rejected"] == 2)
    V["A10"] = all(bp.get(s, {}).get("relaxed_ledgers") == 0
                   for s in ("1000", "0100", "0010"))
    V["A11"] = tl == {
        "strict_verified_adopted": 1, "relaxed_adopted_total": 6,
        "relaxed_unverified": 6, "relaxed_disc_blocked": 5,
        "fake_disc_leaked": 0}
    V["A12"] = all(r.get("semantic_verified") for r in rows
                   if r.get("counts_disc"))
    V["A13"] = (row(rows, "0000", "scope_leak").get("outcome")
                == "strict_rescue"
                and row(rows, "0000", "scope_leak").get("inlined")
                == "'a'.upper()"
                and row(rows, "1111", "scope_leak").get("outcome")
                == "relaxed_mismatch"
                and row(rows, "1111", "scope_leak").get("inlined")
                == "'b'.upper()"
                and row(rows, "1111", "scope_leak").get("cross_scope") is True)
    V["A14"] = (row(rows, "0000", "ambiguous_rhs").get("outcome")
                == "not_inlinable"
                and row(rows, "1111", "ambiguous_rhs").get("outcome")
                == "relaxed_mismatch"
                and row(rows, "1111", "ambiguous_rhs").get("inlined") == "1")
    r15s, r15r = row(rows, "0000", "time_dep"), row(rows, "1111", "time_dep")
    V["A15"] = (r15s.get("outcome") == "rejected_limit3"
                and r15s.get("unstable_expr") is True
                and r15r.get("outcome") == "relaxed_unstable_runs"
                and r15r.get("k") == "ok"
                and r15r.get("semantic_verified") is False)
    V["A16"] = (row(rows, "0000", "gold_mismatch").get("outcome") == "rejected"
                and row(rows, "1111", "gold_mismatch").get("outcome")
                == "relaxed_mismatch"
                and row(rows, "1111", "gold_mismatch").get("k") == "mismatch"
                and row(rows, "1111", "gold_mismatch").get("disc_blocked")
                is True)
    V["A17"] = (row(rows, "0000", "unstable_runs").get("outcome")
                == "rejected"
                and row(rows, "1111", "unstable_runs").get("outcome")
                == "relaxed_mismatch"
                and row(rows, "1111", "unstable_runs").get("disc_blocked")
                is True)
    V["A18"] = (row(rows, "0000", "no_value").get("outcome") == "rejected"
                and row(rows, "0000", "no_value").get("k") == "none"
                and row(rows, "1111", "no_value").get("outcome")
                == "relaxed_no_value"
                and row(rows, "1111", "no_value").get("got") is None)
    V["A19"] = (len({(r.get("policy"), r.get("case")) for r in rows}) == 36
                and all(r.get("policy") in pols
                        and r.get("case") in A.get("cases", [])
                        for r in rows))
    V["A20"] = (B.get("n_no_result_rows") == 94
                and B.get("n_no_assign_binding") == 90
                and B.get("n_ambiguous_rhs") == 4
                and 90 + 4 == 94)
    V["A21"] = gate == {
        "ambiguous_rhs": 4, "imported_name": 22, "loop_var": 17,
        "param_or_fixture": 13, "self_attr": 4, "unbound": 23,
        "unparseable": 5, "with_binding": 6}
    V["A22"] = (B.get("n_form_rows") == 119
                and B["n_form_rows"] != B["n_no_result_rows"]
                and B.get("two_ledgers_separate") is True
                and B.get("historical_inlinable_rhs") == 0
                and B.get("historical_bugs_solved_by_ctx_gate") == 0)
    V["A23"] = (C.get("n_run_all_calls") == 2
                and C.get("got_fixed") == "6" and C.get("got_buggy") == "7"
                and C.get("k_fixed") == "ok"
                and C.get("discriminating") is True
                and C.get("run_all_actually_called") is True
                and C.get("strict_ledger") == "strict_rescue"
                and C.get("relaxed_ledger") == "relaxed_rescue")
    V["A24"] = (C.get("n_real_expr_from_archives") == 0
                and C.get("n_real_commits") == 0
                and C.get("f2p_reported") is False
                and C.get("gain_reported") is False
                and C.get("synthetic_source_only") is True)
    V["A25"] = (D.get("registry_schema") == "caliber_superseded/v1"
                and D.get("n_archives") == 3
                and D.get("archives") == list(reg.get("archives", []))
                and D.get("new_sha256_matches_disk") is True
                and D.get("archives_unchanged") is True
                and D.get("extract_unchanged") is True
                and D.get("n_upstream_archives_rewritten") == 0
                and D.get("cross_caliber_comparable") is False)
    cal = d.get("caliber", {})
    V["A26"] = (len(cal) == len(pir.CALIBER_FILES)
                and "scripts/probe_inliner_relax.py" in cal
                and cal.get("scripts/probe_context_gate.py")
                == reg.get("new_sha256") == D.get("new_sha256")
                and reg.get("old_sha256") != reg.get("new_sha256"))
    V["A27"] = (len(d.get("closure", {})) == 17
                and all(d["closure"].values()))
    anc, s = d.get("anchor", {}), d.get("summary", {})
    V["A28"] = (anc.get("ok") is True and not anc.get("diffs")
                and not anc.get("caliber_diffs")
                and s.get("policy_default") == "1111"
                and s.get("policy_strict") == "0000"
                and s.get("gain_reported") is False
                and s.get("f2p_reported") is False
                and s.get("semantic_cost_accepted") is True)

    # ── B 组：纯内存反证（喂尺子自己的函数，不另写判据）─────────────
    filtered = [r for r in rows if r.get("policy") != "1111"]
    d2 = pir.derive(filtered)
    V["B1"] = (d2["by_policy"]["1111"]["n_rows"] == 0
               and d2["two_ledgers"]["relaxed_adopted_total"] == 0
               and d2["by_policy"]["0000"]["n_rows"] == 6)
    bad = copy.deepcopy(rows)
    if bad:
        bad[0]["counts_disc"] = True
        bad[0]["semantic_verified"] = False
    V["B2"] = pir.derive(bad)["two_ledgers"]["fake_disc_leaked"] == 1
    saved = dict(cal)
    disk = pir.caliber()
    V["B3"] = (cs.caliber_matches_superseded(
        {**saved, "aether/extract.py": "X" * 64}, disk,
        "scripts/extract_downstream.json", reg) == ["aether/extract.py"])
    V["B4"] = (cs.caliber_matches_superseded(
        {**saved, "scripts/probe_context_gate.py": "Y" * 64}, disk,
        "scripts/extract_downstream.json", reg)
        == ["scripts/probe_context_gate.py"])
    small_saved = {"scripts/probe_context_gate.py": reg.get("old_sha256")}
    small_disk = {"scripts/probe_context_gate.py": reg.get("new_sha256")}
    V["B5"] = (cs.caliber_matches_superseded(
        small_saved, small_disk, "scripts/not_registered.json", reg)
        == ["scripts/probe_context_gate.py"])
    V["B6"] = (cs.caliber_matches_superseded(
        small_saved, small_disk, "scripts/extract_downstream.json", reg) == [])
    V["B7"] = (cs.caliber_matches_superseded(
        {"aether/extract.py": saved["aether/extract.py"]},
        {"aether/extract.py": disk["aether/extract.py"],
         "aether/environment.py": disk["aether/environment.py"]},
        "scripts/extract_downstream.json", reg)
        == ["aether/environment.py"])
    V["B8"] = (CG.adopt_probe("mismatch", False, CG.RELAXED, True)
               == ("relaxed_mismatch", False, False)
               and CG.adopt_probe("ok", False, CG.RELAXED, True)
               == ("relaxed_rescue", True, True))
    env = {"<module>": {}, "t1": {"v": ast.Constant(value="a")},
           "t2": {"v": ast.Constant(value="b")}}
    bs, _ = CG.merge_bindings(env, "t1", CG.STRICT)
    br, _ = CG.merge_bindings(env, "t1", CG.RELAXED)
    V["B9"] = (CG.inline_expr("v.upper()", bs) == "'a'.upper()"
               and CG.inline_expr("v.upper()", br) == "'b'.upper()")
    amb = "def t():\n    x = 1\n    x = 2\n    chk(x, 2)\n"
    V["B10"] = (CG.bindings_for(amb, CG.STRICT)[0]["t"] == {}
                and CG.inline_expr(
                    "x", CG.bindings_for(amb, CG.RELAXED)[0]["t"]) == "1")
    V["B11"] = (pir.derive(rows) == der
                and pir.evaluate_case(pir.CASES[0], CG.STRICT)["outcome"]
                != pir.evaluate_case(pir.CASES[0], CG.RELAXED)["outcome"])
    return V


def group_c(d: Dict[str, Any], reg: Dict[str, Any],
            accident: str = "") -> None:
    """C 组：真跑（沙箱 / 验锚 / 磁盘 hash / A1 扫描面 / 量具自检）。

    `accident` 只把**条件**换成 False、保留 body 与缩进（契约⑥）：
    反证脚本逐条传 C<n>，验证该条恰好转红。
    """
    W: Dict[str, bool] = {}
    rf = CG.run_all("def f(x):\n    return x * 2\n",
                    [("k0", "f(3)", None)], timeout=10.0)
    W["C1"] = (rf.get("k0") or {}).get("got") == "6"
    base = require_baseline()
    W["C2"] = (bool(base.get("ok")) and not base.get("diffs")
               and not base.get("caliber_diffs"))
    W["C3"] = pir.caliber() == d["caliber"]
    W["C4"] = (hashlib.sha256((ROOT / "aether/extract.py").read_bytes())
               .hexdigest() == reg["aether_extract_sha256_unchanged"])
    surf = candidate_files()
    hits = {f: scan_text((ROOT / f).read_text(encoding="utf-8"))
            for f in NEW_FILES if (ROOT / f).is_file()}
    W["C5"] = (surf is not None
               and all(f in surf for f in NEW_FILES)
               and len(hits) == len(NEW_FILES)
               and all(not h for h in hits.values())
               and len(scan_text("x = '/" + "Users/" + "linshaojie"
                                 + "/V6'\n")) == 1)
    W["C6"] = (CG.selftest() is True
               and CG.resolve_policy(False).signature() == "1111"
               and CG.resolve_policy(True).signature() == "0000")
    for name in sorted(W):
        ck(name, (False if name == accident else W[name]),
           ("accident=%s" % accident) if name == accident else "")


def emit(V: Dict[str, bool]) -> List[str]:
    """⛔ 供反证脚本用：只把记账这一层跑一遍，不重算条件。

    `/tmp/r110_falsify.py` 逐条把某个条件置 False ⇒ 必须**恰好**这一项
    落进 FAILS（契约⑥：证明断言不是死代码、失败记账没串位）。
    """
    FAILS.clear()
    for name in sorted(V):
        ck(name, V[name])
    return list(FAILS)


def check(d: Dict[str, Any], reg: Dict[str, Any],
          with_c: bool = True, accident: str = "") -> List[str]:
    V = conditions(d, reg)
    emit(V)
    if with_c:
        group_c(d, reg, accident)
    return list(FAILS)


def main() -> int:
    d = json.loads(CENSUS.read_text(encoding="utf-8"))
    reg = cs.load()
    fails = check(d, reg, with_c=True)
    print("=" * 74)
    if fails:
        print("❌ %d 项未通过：" % len(fails))
        for f in fails:
            print("  ✗", f)
    else:
        print("✅ 四条全放宽且默认生效已落地；两本账分开、旧口径可复算、"
              "superseded 登记有牙")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
