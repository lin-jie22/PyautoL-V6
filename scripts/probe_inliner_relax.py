#!/usr/bin/env python3
"""r110 / README 189：四条自限「全放宽且默认生效」的口径 v2 普查尺子。

═══ 起点（用户拍板，⛔ 不是自主选题）═══

2026-11-14 用户显式选择「四条全放宽且默认生效」，覆盖 README 5750 的
原始约束「每条都带反证，不许为救回率放宽」。取证（便宜对照）已先说明
两条事实：

  · ①② 是**覆盖限制**，③④ 是**判据正确性锚** —— 放宽③④ ≡ 让
    不可复现判据 / 假鉴别力进默认路径；
  · 178 存档 94 条里 90 条是非 `Assign` 绑定格，放宽①也变不出 rhs；
    放宽②只触及 4 条 `ambiguous_rhs` 且全部 `mismatch`。

⇒ 本轮**不报收益、不宣布有效 / 无效**；只把「换了口径之后，什么被放行、
代价落在哪本账」量成可复算的封闭读数。

═══ 四本账（⛔ 不合并成布尔）═══

  A 策略矩阵  6 个策略 × 6 个合成样本 = 36 行，逐条放宽各有牙
  B 静态靶子  178 的 94 / 90 / 4 与 179 的 119 分母（只读，不喂沙箱）
  C 合成沙箱  真跑 `run_all` 2 次，证明装置有效；⛔ 真实 expr 0 次
  D 口径边界  superseded 登记、旧存档未回写、`aether/extract.py` 未改

⛔⛔ 严守 §183：119 条 form 下游属拍板项 ①，本轮零触碰；不跑真实
commit、不报 F2P / 收益、不把 `mismatch` 当鉴别力（契约④两本账分开）。

⛔ 判据与被测对象**同源**：`merge_bindings` / `bindings_for` /
`adopt_probe` / `classify_probe` / `has_unstable_call` / `inline_expr`
全部取自量具 `probe_context_gate.py` 与产品 `aether/extract.py`，
本脚本不另写第二份规则（契约⑥ / F 组）。

跑法：python scripts/probe_inliner_relax.py
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import caliber_superseded as cs                                  # noqa: E402
from aether import extract as E                                  # noqa: E402
from realrepo_baseline import require_baseline                   # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "pcg_r110", ROOT / "scripts" / "probe_context_gate.py")
CG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CG)

OUT = ROOT / "scripts" / "inliner_relax_census.json"
ARCH_NO_RESULT = ROOT / "scripts" / "no_result_attribution.json"
ARCH_FORM = ROOT / "scripts" / "form_shape_attribution.json"

#: ⛔ 新口径另存（v2）：含尺子自身、含量具新 hash、含 superseded 登记簿。
#:    旧 12 份存档的 caliber 不回写，跨口径读数不可比（契约①）。
CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    "aether/environment.py",
    "scripts/probe_context_gate.py",
    "scripts/caliber_superseded.py",
    "scripts/caliber_superseded.json",
    "scripts/no_result_attribution.json",
    "scripts/form_shape_attribution.json",
    "scripts/realrepo_baseline.json",
    "scripts/probe_inliner_relax.py",
)

NO_RHS_GATES: Tuple[str, ...] = (
    "loop_var", "param_or_fixture", "self_attr", "with_binding",
    "unbound", "imported_name", "unparseable",
)

POLICIES: Dict[str, Any] = {
    "0000": CG.STRICT,
    "1000": CG.RelaxPolicy(True, False, False, False),
    "0100": CG.RelaxPolicy(False, True, False, False),
    "0010": CG.RelaxPolicy(False, False, True, False),
    "0001": CG.RelaxPolicy(False, False, False, True),
    "1111": CG.RELAXED,
}

SWITCH_OF = {"1000": "1_cross_scope", "0100": "2_first_rhs",
             "0010": "3_allow_unstable", "0001": "4_no_gold_gate"}

#: ⛔ 合成样本。`got_by_expr` 是**声明的合成观察表**（不跑 eval、不另写
#:    判据）：expr → 求值结果的 repr。缺失 ⇒ 求值不了（got=None）。
#:    这样 A 账纯内存可复算，C 账另外用真 `run_all` 证明装置有效。
CASES: Tuple[Dict[str, Any], ...] = (
    {
        "id": "scope_leak", "limit": "1_cross_scope",
        "src": "def t1():\n    v = 'a'\n    chk(v.upper(), 'A')\n"
               "\ndef t2():\n    v = 'b'\n    chk(v.upper(), 'B')\n",
        "fn": "t1", "expr": "v.upper()", "gold": "'A'",
        "got_by_expr": {"'a'.upper()": "'A'", "'b'.upper()": "'B'"},
        "unstable_runs": False, "disc": True,
        "cost": "跨作用域串名：t1 的探针用了 t2 的 rhs ⇒ 落到 gold 'A' 上",
    },
    {
        "id": "ambiguous_rhs", "limit": "2_first_rhs",
        "src": "def t():\n    x = 1\n    x = 2\n    chk(x, 2)\n",
        "fn": "t", "expr": "x", "gold": "2",
        "got_by_expr": {"1": "1"},
        "unstable_runs": False, "disc": True,
        "cost": "取首个 rhs=1 与作者写死的 2 不符 ⇒ 能求值但语义错",
    },
    {
        "id": "time_dep", "limit": "3_allow_unstable",
        "src": "def t():\n    d = date.today()\n"
               "    chk(d.isoformat(), '2020-01-01')\n",
        "fn": "t", "expr": "d.isoformat()", "gold": "'2020-01-01'",
        "got_by_expr": {"date.today().isoformat()": "'2020-01-01'"},
        "unstable_runs": True, "disc": True,
        "cost": "时间依赖 + 两遍不稳 ⇒ 不可复现判据 ≡ 随机判据",
    },
    {
        "id": "gold_mismatch", "limit": "4_no_gold_gate",
        "src": "def t():\n    x = 3\n    chk(f(x), 7)\n",
        "fn": "t", "expr": "f(x)", "gold": "7",
        "got_by_expr": {"f(3)": "6"},
        "unstable_runs": False, "disc": True,
        "cost": "与测试写死 expected 不符 ⇒ 假鉴别力（154 的 616 vs 13）",
    },
    {
        "id": "unstable_runs", "limit": "4_no_gold_gate",
        "src": "def t():\n    x = 3\n    chk(f(x), 7)\n",
        "fn": "t", "expr": "f(x)", "gold": "7",
        "got_by_expr": {"f(3)": "6"},
        "unstable_runs": True, "disc": True,
        "cost": "两次运行不一致 ⇒ 判据不稳定",
    },
    {
        "id": "no_value", "limit": "4_no_gold_gate",
        "src": "def t():\n    x = 3\n    chk(f(x), 7)\n",
        "fn": "t", "expr": "f(x)", "gold": "7",
        "got_by_expr": {},
        "unstable_runs": False, "disc": False,
        "cost": "连值都求不出 ⇒ 不是探针，只是留痕",
    },
)

REJECTED = ("not_inlinable", "rejected_limit3", "rejected")


def sha256_file(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {rel: sha256_file(rel) for rel in CALIBER_FILES}


def evaluate_case(case: Dict[str, Any], policy: Any) -> Dict[str, Any]:
    """⛔ 逐条走量具自己的函数链，不另写判据（契约⑥同源）。"""
    envs, _n_pruned, _mode = CG.bindings_for(case["src"], policy)
    binds, sflags = CG.merge_bindings(envs, case["fn"], policy)
    expr = CG.inline_expr(case["expr"], binds)
    row: Dict[str, Any] = {
        "policy": policy.signature(), "case": case["id"],
        "limit": case["limit"], "fn": case["fn"],
        "inlined": expr, "cross_scope": sflags["cross_scope"],
        "unstable_expr": False, "got": None, "k": None,
        "outcome": "not_inlinable", "ledger": "not_inlinable",
        "semantic_verified": False, "counts_disc": False,
        "disc_blocked": False, "cost": case["cost"],
    }
    if not expr or expr == case["expr"]:
        return row
    unstable = CG.has_unstable_call(expr)
    row["unstable_expr"] = bool(unstable)
    if unstable and not policy.allow_unstable:
        row["outcome"] = "rejected_limit3"
        row["ledger"] = "rejected_limit3"
        return row
    got = case["got_by_expr"].get(expr)
    row["got"] = got
    k = CG.classify_probe(case["expr"], case["gold"], got)
    row["k"] = k
    ledger, verified, counts_disc = CG.adopt_probe(
        k, case["unstable_runs"], policy, case["disc"])
    row["outcome"] = ledger
    row["ledger"] = ledger
    row["semantic_verified"] = bool(verified)
    row["counts_disc"] = bool(counts_disc)
    row["disc_blocked"] = bool(case["disc"] and not counts_disc)
    return row


def matrix() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for sig, policy in POLICIES.items():
        for case in CASES:
            r = evaluate_case(case, policy)
            assert r["policy"] == sig
            rows.append(r)
    return rows


def derive(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """⛔ 纯函数：全部结论**逐行从 rows 派生**（r108 B4 教训）。

    契约会喂一份过滤后的 rows 做反证 —— 若某结论读的是预聚合常量，
    喂过滤 rows 时它不会变 ⇒ 当场转红。
    """
    by_policy: Dict[str, Dict[str, int]] = {}
    for sig in POLICIES:
        rs = [r for r in rows if r["policy"] == sig]
        by_policy[sig] = {
            "n_rows": len(rs),
            "adopted": sum(r["outcome"] not in REJECTED for r in rs),
            "verified": sum(bool(r["semantic_verified"]) for r in rs),
            "counts_disc": sum(bool(r["counts_disc"]) for r in rs),
            "relaxed_ledgers": sum(
                str(r["ledger"]).startswith("relaxed_") for r in rs),
            "disc_blocked": sum(bool(r["disc_blocked"]) for r in rs),
            "rejected": sum(r["outcome"] in REJECTED for r in rs),
        }
    switch_teeth: Dict[str, bool] = {}
    strict = [r for r in rows if r["policy"] == "0000"]
    for sig, switch in SWITCH_OF.items():
        sw = [r for r in rows if r["policy"] == sig]
        switch_teeth[switch] = bool(strict) and len(strict) == len(sw) and any(
            a["outcome"] != b["outcome"] for a, b in zip(strict, sw))
    relaxed = [r for r in rows if r["policy"] == "1111"]
    return {
        "by_policy": by_policy,
        "switch_teeth": switch_teeth,
        "two_ledgers": {
            "strict_verified_adopted": by_policy["0000"]["verified"],
            "relaxed_adopted_total": by_policy["1111"]["adopted"],
            "relaxed_unverified": sum(
                1 for r in relaxed
                if r["outcome"] not in REJECTED
                and not r["semantic_verified"]),
            "relaxed_disc_blocked": by_policy["1111"]["disc_blocked"],
            "fake_disc_leaked": sum(
                1 for r in rows if r["counts_disc"]
                and not r["semantic_verified"]),
        },
    }


def account_b() -> Dict[str, Any]:
    d = json.loads(ARCH_NO_RESULT.read_text(encoding="utf-8"))
    rows = d["rows"]
    g = Counter(r["main_gate"] for r in rows)
    f = json.loads(ARCH_FORM.read_text(encoding="utf-8"))
    return {
        "source_no_result": "scripts/no_result_attribution.json",
        "source_form": "scripts/form_shape_attribution.json",
        "n_no_result_rows": len(rows),
        "by_main_gate": dict(sorted(g.items())),
        "no_rhs_gates": list(NO_RHS_GATES),
        "n_no_assign_binding": sum(g[k] for k in NO_RHS_GATES),
        "n_ambiguous_rhs": g["ambiguous_rhs"],
        "n_form_rows": len(f["rows"]),
        "two_ledgers_separate": True,
        "historical_inlinable_rhs": 0,
        "historical_bugs_solved_by_ctx_gate": 0,
        "observation": "94 条里 90 条压根没有可内联的 rhs；放宽①变不出 rhs，"
                       "放宽②只触及 4 条 ambiguous_rhs 且历史全 mismatch",
        "boundary": "119 条 form 门与 94/135 主账是不同分母，绝不合并",
    }


def account_c() -> Dict[str, Any]:
    """⛔ 真跑 `run_all` 2 次（纯合成源码），证明装置不是死代码。"""
    fixed = "def f(x):\n    return x * 2\n"
    buggy = "def f(x):\n    return x * 2 + 1\n"
    items = [("k0", "f(3)", None)]
    rf = CG.run_all(fixed, items, timeout=10.0)
    rb = CG.run_all(buggy, items, timeout=10.0)
    got_f = (rf.get("k0") or {}).get("got")
    got_b = (rb.get("k0") or {}).get("got")
    k = CG.classify_probe("f(3)", "6", got_f)
    disc = bool(got_f is not None and got_f != got_b)
    st = CG.adopt_probe(k, False, CG.STRICT, disc)
    re = CG.adopt_probe(k, False, CG.RELAXED, disc)
    return {
        "n_run_all_calls": 2,
        "synthetic_source_only": True,
        "got_fixed": got_f, "got_buggy": got_b,
        "k_fixed": k, "discriminating": disc,
        "strict_ledger": st[0], "relaxed_ledger": re[0],
        "run_all_actually_called": bool(rf) and bool(rb) and got_f == "6",
        "n_real_expr_from_archives": 0,
        "n_real_commits": 0,
        "f2p_reported": False,
        "gain_reported": False,
        "boundary": "§183：119 条真实 expr 沙箱 0 次、真实 commit 0 个、"
                    "零 F2P / 收益读数",
    }


def account_d() -> Dict[str, Any]:
    reg = cs.load()
    archives = list(reg["archives"])
    now = {a: sha256_file(a) for a in archives}
    saved = reg["archive_sha256_at_registration"]
    return {
        "registry": "scripts/caliber_superseded.json",
        "registry_schema": reg["schema"],
        "n_archives": len(archives),
        "archives": archives,
        "old_sha256": reg["old_sha256"],
        "new_sha256": reg["new_sha256"],
        "new_sha256_matches_disk": sha256_file(reg["file"]) == reg["new_sha256"],
        "archive_sha256_now": now,
        "archives_unchanged": all(now[a] == saved[a] for a in archives),
        "extract_sha256_now": sha256_file("aether/extract.py"),
        "extract_unchanged": (sha256_file("aether/extract.py")
                              == reg["aether_extract_sha256_unchanged"]),
        "n_upstream_archives_rewritten": 0,
        "cross_caliber_comparable": False,
        "boundary": "旧存档标 superseded、旧值另存、不回写不删除；"
                    "跨口径读数不可比（契约①）",
    }


def measure() -> Dict[str, Any]:
    rows = matrix()
    derived = derive(rows)
    A = {"rows": rows, "n_rows": len(rows),
         "n_policies": len(POLICIES), "n_cases": len(CASES),
         "policies": {s: p.signature() for s, p in POLICIES.items()},
         "cases": [c["id"] for c in CASES]}
    B, C, D = account_b(), account_c(), account_d()
    anchor = require_baseline()
    cal = caliber()
    bp = derived["by_policy"]
    tl = derived["two_ledgers"]
    closure = {
        "c1_rows_36": A["n_rows"] == 36,
        "c2_policies_all_match": all(s == p.signature()
                                     for s, p in POLICIES.items()),
        "c3_each_policy_6_rows": all(bp[s]["n_rows"] == len(CASES)
                                     for s in POLICIES),
        "c4_switch_teeth_all": all(derived["switch_teeth"].values()),
        "c5_strict_no_relaxed_ledger": bp["0000"]["relaxed_ledgers"] == 0,
        "c6_relaxed_has_relaxed_ledger": bp["1111"]["relaxed_ledgers"] > 0,
        "c7_no_fake_disc_leak": tl["fake_disc_leaked"] == 0,
        "c8_two_ledgers_separate": (bp["0000"]["verified"]
                                    != bp["1111"]["adopted"]),
        "c9_B_94_closed": (B["n_no_assign_binding"] + B["n_ambiguous_rhs"]
                           == B["n_no_result_rows"] == 94),
        "c10_B_form_separate": B["n_form_rows"] == 119
        and B["n_form_rows"] != B["n_no_result_rows"],
        "c11_C_run_all_real": C["run_all_actually_called"] is True,
        "c12_C_zero_real_expr": (C["n_real_expr_from_archives"] == 0
                                 and C["n_real_commits"] == 0
                                 and C["gain_reported"] is False),
        "c13_D_archives_unchanged": D["archives_unchanged"] is True,
        "c14_D_extract_unchanged": D["extract_unchanged"] is True,
        "c15_D_registry_matches_disk": D["new_sha256_matches_disk"] is True,
        "c16_caliber_has_self": "scripts/probe_inliner_relax.py" in cal,
        "c17_anchor_ok": (bool(anchor.get("ok"))
                          and not anchor.get("diffs")
                          and not anchor.get("caliber_diffs")),
    }
    return {
        "schema": "inliner_relax_census/v2",
        "caliber": cal,
        "accounts": {"A_policy_matrix": A,
                     "B_178_static_target": B,
                     "C_synthetic_sandbox": C,
                     "D_superseded_boundary": D},
        "derived": derived,
        "closure": closure,
        "anchor": {"ok": bool(anchor.get("ok")),
                   "diffs": list(anchor.get("diffs") or []),
                   "caliber_diffs": list(anchor.get("caliber_diffs") or [])},
        "summary": {
            "policy_default": CG.resolve_policy(False).signature(),
            "policy_strict": CG.resolve_policy(True).signature(),
            "semantic_cost_accepted": True,
            "gain_reported": False,
            "f2p_reported": False,
            "verdict": "用户拍板四条全放宽且默认生效已落地；"
                       "语义代价显式接受；两本账分开；收益未测、不宣布有效/无效",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    d = measure()
    Path(args.out).write_text(
        json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("policy default/strict =",
          d["summary"]["policy_default"], d["summary"]["policy_strict"])
    print("A rows =", d["accounts"]["A_policy_matrix"]["n_rows"])
    print("switch_teeth =", d["derived"]["switch_teeth"])
    print("two_ledgers =", d["derived"]["two_ledgers"])
    print("closure =", sum(d["closure"].values()), "/", len(d["closure"]))
    print("written", args.out)
    return 0 if all(d["closure"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
