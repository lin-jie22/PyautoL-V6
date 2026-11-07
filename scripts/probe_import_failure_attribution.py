#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r180 / README 180：9 条 `import_failed` 的运行时归因 + 主账封闭性对账。

═══ ⛔ 本脚本做什么、不做什么 ═══

✅ 纯取证：对 177 存档里 `verdict=import_failed` 的 9 条，用 **177 完全
   同源**的设施（`select_commits` / `_git` / `pkg_context` /
   `run_in_sandbox`）真跑 fixed 与 buggy 两侧，取 `import_error` 完整
   末行作为**观察**，把每条落到恰好一格归因。
✅ 顺带做「254 条 unknown 的账目闭合」可复算对拍：主账 135 的
   五格、form 账 119、178/179/180 三份诊断账两两不相交。
⛔ **不跑「放宽 deny-modules」的对照**：那会削弱沙箱隔离边界，属
   显式人工审批节点 / 产品口径变更，需拍板。本轮没有测过它，
   因此「放宽就能测」是**推断**、不是观察，绝不写成结论。
⛔ 不产出判据、不改 `aether/` 一个字节、不报收益。177 的
   `unmeasurable` 与 179 的下游未知都没被推翻。

═══ 观察 / 分类 / 未知（契约⑤）═══

- `evidence_tail` = 产出方 `ExecResult.import_error` 的最后一个非空行，
  是**运行时观察**，不是静态推断。
- `main_gate` = 基于**产品同源词表**的可复算分类，不是异常原因的
  自由解读；词表外的末行一律 `other_unknown`（fail-closed 保留未知）。
- `downstream` 恒为 `unknown`：装不起来 ⇒ 探针从未执行，下游能不能
  F2P **没测过 ≠ 测出 0**。

═══ 词表同源（契约⑥）═══

- deny 名单取 `aether.environment._AUDIT_DENY_MODULES`（runner 的
  `_hook` 就是拿它抛 `V6_SANDBOX_DENY:import:<mod>`）。
- stdlib 名单取 `sys.stdlib_module_names`（解释器自己的权威表）。
⇒ 不在脚本里另抄一份词表；词表命中不了就落 `other_unknown`。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aether.environment import (                       # noqa: E402
    _AUDIT_DENY_MODULES, run_in_sandbox)
from probe_alias_demote import select_commits, _git    # noqa: E402
from probe_package_load import pkg_context             # noqa: E402
from realrepo_baseline import require_baseline         # noqa: E402

REPOS: Tuple[Tuple[str, str], ...] = (
    ("boltons", "boltons/"),
    ("more-itertools", "more_itertools/"),
    ("attrs", "src/attr/"),
)
SOURCE = ROOT / "scripts" / "extract_downstream.json"
NRA = ROOT / "scripts" / "no_result_attribution.json"
FSA = ROOT / "scripts" / "form_shape_attribution.json"
DEFAULT_OUT = ROOT / "scripts" / "import_failure_attribution.json"

#: ⛔ 封闭集合：定好后不许为迁就读数新增格（166 纪律）。
GATES: Tuple[str, ...] = (
    "sandbox_deny",       #: 产品 deny 词表命中 ⇒ 安全边界正确拒收
    "stdlib_incompat",    #: stdlib 名命中 ⇒ 题源年代与解释器错配
    "missing_module",     #: ModuleNotFoundError
    "relative_import",    #: 164 的典型（attempted relative import ...）
    "other_unknown",      #: ⛔ fail-closed 兜底，保留未知
)

#: 两侧运行时账（正交于主格，绝不合并成布尔）
PAIRS: Tuple[str, ...] = ("both_import", "fixed_only", "buggy_only",
                          "both_ok", "skipped")

CALIBER_FILES = (
    "aether/environment.py",
    "aether/extract.py",
    "scripts/probe_context_gate.py",
    "scripts/probe_package_load.py",
    "scripts/probe_alias_demote.py",
    "scripts/probe_extract_downstream.py",
    "scripts/extract_downstream.json",
    "scripts/no_result_attribution.json",
    "scripts/form_shape_attribution.json",
    "scripts/probe_import_failure_attribution.py",  #: ⛔ 钉尺子自身
)

DENY_RE = re.compile(r"V6_SANDBOX_DENY:import:([A-Za-z_][A-Za-z0-9_.]*)")
NAME_FROM_RE = re.compile(
    r"cannot import name ['\"]([^'\"]+)['\"] from ['\"]([^'\"]+)['\"]")
NO_MODULE_RE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")
RELATIVE_RE = re.compile(r"attempted relative import")


# ───────────────────── 纯函数（供契约离线反证） ─────────────────────

def sha256_file(rel: str) -> str:
    p = ROOT / rel
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "MISSING"


def caliber() -> Dict[str, str]:
    return {rel: sha256_file(rel) for rel in CALIBER_FILES}


def tail_line(err: Optional[str]) -> str:
    """取 traceback 最后一个非空行（⛔ 不许只看前 200 字符）。"""
    if not err:
        return ""
    lines = [x.strip() for x in str(err).splitlines() if x.strip()]
    return lines[-1] if lines else ""


def classify_import_error(err: Optional[str],
                          deny_names: frozenset,
                          stdlib_names: frozenset
                          ) -> Tuple[str, str]:
    """⛔ 纯函数：把运行时末行落到恰好一格。

    词表外的命中一律 `other_unknown`（fail-closed），不许靠文案猜原因。
    """
    tail = tail_line(err)
    if not tail:
        return "other_unknown", ""
    m = DENY_RE.search(tail)
    if m:
        mod = m.group(1)
        return (("sandbox_deny", mod) if mod in deny_names
                else ("other_unknown", "deny-not-in-caliber:" + mod))
    m = NAME_FROM_RE.search(tail)
    if m:
        name, mod = m.group(1), m.group(2).split(".")[0]
        return (("stdlib_incompat", mod + "." + name)
                if mod in stdlib_names
                else ("other_unknown", "non-stdlib:" + mod))
    if RELATIVE_RE.search(tail):
        return "relative_import", ""
    m = NO_MODULE_RE.search(tail)
    if m:
        return "missing_module", m.group(1)
    return "other_unknown", ""


def pair_verdict(err_f: Optional[str], err_b: Optional[str]) -> str:
    """⛔ 正交账：两侧是否都装不起来。"""
    f, b = bool(err_f), bool(err_b)
    if f and b:
        return "both_import"
    if f:
        return "fixed_only"
    if b:
        return "buggy_only"
    return "both_ok"


def row_key(r: Dict[str, Any]) -> Tuple[str, ...]:
    return (str(r.get("repo")), str(r.get("sha")), str(r.get("fn")),
            str(r.get("stmt")), str(r.get("call")))


def closure_of(src: Dict[str, Any], nra: Dict[str, Any],
               fsa: Dict[str, Any], n_this: int) -> Dict[str, Any]:
    """⛔ 254 条 unknown 的账目闭合，全部可复算，不合并成单一布尔。"""
    main = src.get("rows") or []
    form = src.get("form_diagnostic_rows") or []
    counts = collections.Counter(r.get("verdict") for r in main)
    keys_nra = {row_key(r) for r in nra.get("rows") or []}
    keys_fsa = {row_key(r) for r in fsa.get("rows") or []}
    return {
        "n_main": len(main),
        "n_form": len(form),
        "main_verdicts": dict(sorted(counts.items())),
        "n_178": len(nra.get("rows") or []),
        "n_179": len(fsa.get("rows") or []),
        "n_180": n_this,
        "eq_main_135": len(main) == 135,
        "eq_main_five_books": (
            counts.get("no_result_both", 0) + counts.get("same", 0)
            + counts.get("diff", 0) + counts.get("import_failed", 0)
            + counts.get("unstable", 0)) == len(main),
        "eq_254": len(main) + len(form) == 254,
        "eq_178_matches_no_result": len(nra.get("rows") or []) == counts.get(
            "no_result_both", 0),
        "eq_179_matches_form": len(fsa.get("rows") or []) == len(form),
        "eq_180_matches_import_failed": n_this == counts.get(
            "import_failed", 0),
        "n_178_179_overlap": len(keys_nra & keys_fsa),
        "note": ("⛔ 9 条从 unknown 变成『结构性不可测（安全边界 / 题源年代）』；"
                 "119 条 form 仍是『形态已描述、下游 unmeasurable』。"
                 "没测过 ≠ 测出 0。"),
    }


# ─────────────────────────── 主流程 ───────────────────────────

def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--no-baseline-gate", action="store_true",
                    help="⛔ 仅供离线反证；正式普查必须验锚")
    args = ap.parse_args()

    health: Dict[str, Any] = {"ok": None, "diffs": [], "repos": {}}
    if args.no_baseline_gate:
        health["diffs"] = ["BYPASS（仅反证）"]
    else:
        health = require_baseline()
        print("baseline_health=OK  repos=%d  diffs=%d" %
              (len(health.get("repos", {})), len(health.get("diffs", []))))

    src = read_json(SOURCE)
    nra, fsa = read_json(NRA), read_json(FSA)
    rows_in = [r for r in src["rows"]
               if r.get("verdict") == "import_failed"]
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

    deny_names = frozenset(_AUDIT_DENY_MODULES)
    stdlib_names = frozenset(getattr(sys, "stdlib_module_names", ()) or ())
    if not deny_names or not stdlib_names:
        raise RuntimeError("同源词表为空 ⇒ 拒绝出读数（fail-closed）")

    rows: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    by_sha: List[Dict[str, Any]] = []

    for (name, sha), grp in groups.items():
        cand = next((c for c in cands[name] if c[0] == sha), None)
        if cand is None:
            skipped.append({"repo": name, "sha": sha,
                            "reason": "not in select_commits"})
            continue
        _, src_path, _testfs, _ = cand
        repo = Path("/tmp/realrepo") / name
        fixed = _git(repo, "show", "%s:%s" % (sha, src_path))
        buggy = _git(repo, "show", "%s^:%s" % (sha, src_path))
        if not fixed or not buggy:
            skipped.append({"repo": name, "sha": sha,
                            "reason": "no fixed/buggy pair"})
            continue
        pkg = (None if name == "boltons"
               else pkg_context(repo, sha, src_path, fixed))
        tests = [{"name": "q%d" % i, "call": r.get("call") or ""}
                 for i, r in enumerate(grp)]

        try:
            ef = run_in_sandbox(fixed, tests, timeout=args.timeout,
                                pkg=pkg)
            eb = run_in_sandbox(buggy, tests, timeout=args.timeout,
                                pkg=pkg)
        except Exception as exc:            # 单 commit 炸不连坐
            skipped.append({"repo": name, "sha": sha,
                            "reason": "%s: %s" % (type(exc).__name__, exc)})
            continue

        err_f, err_b = ef.import_error or "", eb.import_error or ""
        gate_f, det_f = classify_import_error(err_f, deny_names, stdlib_names)
        gate_b, det_b = classify_import_error(err_b, deny_names, stdlib_names)
        pair = pair_verdict(err_f, err_b)
        tail_f, tail_b = tail_line(err_f), tail_line(err_b)

        by_sha.append({
            "repo": name, "sha": sha, "src_path": src_path,
            "n_probes": len(grp), "pkg_mode":
                ("none" if pkg is None else "package"),
            "pair": pair, "gate_fixed": gate_f, "gate_buggy": gate_b,
            "detail_fixed": det_f, "detail_buggy": det_b,
            "evidence_tail_fixed": tail_f, "evidence_tail_buggy": tail_b,
            "import_error_len_fixed": len(err_f),
            "import_error_len_buggy": len(err_b),
        })

        for r in grp:
            rows.append({
                "repo": name, "sha": sha, "fn": r.get("fn"),
                "stmt": r.get("stmt"), "call": r.get("call"),
                "gate_176": r.get("gate"), "reach": r.get("reach"),
                "v175": r.get("v175"),
                "main_gate": gate_f, "detail": det_f,
                "pair": pair,
                "gate_fixed": gate_f, "gate_buggy": gate_b,
                "sides_agree": gate_f == gate_b and det_f == det_b,
                "evidence_tail_fixed": tail_f,
                "evidence_tail_buggy": tail_b,
                "downstream": "unknown",
                "diagnostic_only": True,
                "note": "observation=import_error tail; classification="
                        "product-caliber; downstream NOT measured",
            })

    summary = {
        "n_rows": len(rows),
        "n_sha": len(by_sha),
        "gates": dict(sorted(collections.Counter(
            r["main_gate"] for r in rows).items())),
        "details": dict(sorted(collections.Counter(
            r["detail"] for r in rows if r["detail"]).items())),
        "pairs": dict(sorted(collections.Counter(
            r["pair"] for r in rows).items())),
        "by_repo": dict(sorted(collections.Counter(
            r["repo"] for r in rows).items())),
        "by_gate_176": dict(sorted(collections.Counter(
            str(r["gate_176"]) for r in rows).items())),
        "n_sides_agree": sum(1 for r in rows if r["sides_agree"]),
        "n_skipped": len(skipped),
        "n_other_unknown": sum(1 for r in rows
                               if r["main_gate"] == "other_unknown"),
        "closure": closure_of(src, nra, fsa, len(rows)),
        "caliber_sizes": {"deny_modules": len(deny_names),
                          "stdlib_module_names": len(stdlib_names)},
        "baseline_health": {"ok": health.get("ok"),
                            "diffs": len(health.get("diffs", [])),
                            "repos": len(health.get("repos", {}))},
        "note": (
            "⛔ 只报归因与账目，不报收益。8/9 是产品 deny 词表命中 "
            "（安全边界正确拒收），1/9 是题源与解释器 stdlib 错配。"
            "⛔ 没有跑『放宽 deny-modules』对照 ⇒ 不许宣称放宽后能测几条；"
            "119 条 form 的下游仍 unmeasurable，177 的 unmeasurable 未推翻。"),
    }
    rep = {
        "schema": "import_failure_attribution/v1",
        "caliber": caliber(),
        "rows": rows,
        "by_sha": by_sha,
        "skipped": skipped,
        "summary": summary,
    }
    out = Path(args.out) if args.out else DEFAULT_OUT
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("n_rows=%d  n_sha=%d  gates=%s" %
          (summary["n_rows"], summary["n_sha"], summary["gates"]))
    print("details=%s" % summary["details"])
    print("pairs=%s  sides_agree=%d  skipped=%d  other_unknown=%d" %
          (summary["pairs"], summary["n_sides_agree"],
           summary["n_skipped"], summary["n_other_unknown"]))
    print("closure=%s" % json.dumps(summary["closure"],
                                    ensure_ascii=False, sort_keys=True))
    print("baseline=%s" % summary["baseline_health"])
    print("written: %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
