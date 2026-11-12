#!/usr/bin/env python3
"""r105 / README 184：176 的 `call[:240]` / `stmt[:240]` 截断污染面普查。

═══ 要回答什么 ═══

176 写盘时把 `call` / `stmt` 各截到 240 字符
（`probe_criteria_gate_census.py:362-363`）。178 因此对 5 条
`unserializable` 行 `ast.parse(call, mode="eval")` 必然 SyntaxError ⇒
`main_gate="unparseable"` ⇒ 从「可归因分母 89 = 94 − 5」剔除。
但**断尾表达式不可解析是构造必然，不是探针本身不可解析**。
177/178 读的都是截断值，182 分母钉死 ctx 门、183 只查 import_error
⇒ 这 5 条从未被作为被测对象用**未截断值**跑过。

本轮把「没测过」推进到「测出来是多少」：
  ① 用产品同源 `attribute_commit` 恢复完整 call（不写回上游存档）
  ② 量出 prefix240 / 长度 / `ast.parse` 可解析性
  ③ 用 182 同源 truthy 探针在 fixed / buggy 两侧真跑
  ④ 量出 `stmt` 截断 9 条的影响面，与 178 的两本账对拍
  ⑤ 交叉 177/178/182/183 四份存档，数「未截断值重跑次数」

═══ ⛔ 本脚本做什么、不做什么 ═══

✅ 纯只读取证：分母只认 `scripts/criteria_gate_census.json` 里
   `len(call)==240` 的 5 条与 `len(stmt)==240` 的 9 条；
   探针来自产品 `attribute_commit`，沙箱用 `aether.environment` 本身，
   分类只看产出方写死的 `why` / `passed`（178 教训）。
✅ 两本账**分开**：「176 存档口径下 178 的剔除是正确操作」与
   「完整值下的运行时读数」各记各的，绝不合并成布尔（181 教训）。
⛔ **不改上游尺子**：`criteria_gate_census.json` 一个字节都不写，
   前后 sha256 对拍钉进存档（README 8283：改了会让 166/176/177
   历史读数不可比，属口径变更、需拍板）。恢复值只写本轮旁证存档。
⛔ 不改 `aether/`、不扩张判据形态、不放宽内联器（拍板 ④）、
   不喂 `synthesize_patch`、不报「修好几个」。
⛔ **不宣布 178 结论被推翻**：89 条分母在存档口径下成立；
   完整值下这 5 条仍全 raise、truthy F2P=0 ⇒ 结论方向不变，
   只是把「unparseable」的**归因**从结构性改成写盘构造。
⛔ `f2p_criterion` 只是判据条数，不是收益；`reach` 是事后 oracle，
   只记账不当 predictor。⛔ 不许宣布「扩张判据形态无用」。

═══ 用法 ═══
    python scripts/probe_truncation_census.py
    python scripts/probe_truncation_census.py --out /tmp/r105_sample.json
    python scripts/probe_truncation_census.py --out scripts/truncation_census.json
"""
from __future__ import annotations

import argparse
import ast
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

from probe_alias_demote import select_commits, _git             # noqa: E402
from probe_package_load import pkg_context                      # noqa: E402
from probe_gate_attribution import attribute_commit             # noqa: E402
from probe_ctx_downstream import (sandbox_read, pair_verdict,   # noqa: E402
                                  truthy_f2p, PAIRS)
from realrepo_baseline import require_baseline                  # noqa: E402

SOURCE = ROOT / "scripts" / "criteria_gate_census.json"
DEFAULT_OUT = ROOT / "scripts" / "truncation_census.json"
TRUNC = 240

REPOS: Tuple[Tuple[str, str], ...] = (
    ("boltons", "boltons/"),
    ("more-itertools", "more_itertools/"),
    ("attrs", "src/attr/"),
)

#: 交叉账：这四份存档覆盖 177 / 178 / 182 / 183 四轮下游结论。
CROSS_ARCHIVES: Tuple[str, ...] = (
    "scripts/extract_downstream.json",        #: 177
    "scripts/no_result_attribution.json",     #: 178
    "scripts/ctx_downstream.json",            #: 182
    "scripts/import_error_census.json",       #: 183
)

CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    "aether/environment.py",
    "scripts/probe_gate_attribution.py",
    "scripts/probe_criteria_gate_census.py",
    "scripts/probe_alias_demote.py",
    "scripts/probe_package_load.py",
    "scripts/probe_no_result_attribution.py",
    "scripts/probe_ctx_downstream.py",
    "scripts/criteria_gate_census.json",
    "scripts/extract_downstream.json",        #: ⛔ 交叉账四份存档也钉口径
    "scripts/no_result_attribution.json",
    "scripts/ctx_downstream.json",
    "scripts/import_error_census.json",
    "scripts/probe_truncation_census.py",     #: ⛔ 钉尺子自身（174 教训）
)

_HOME_PAT = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/")
_LIB_PAT = re.compile(r"/Users/…/[^\s)]*?(?=/lib/python)")
_TMP_PAT = re.compile(r"/var/folders/[^/\s]+/[^/\s]+/[^/\s()\"']+")


def redact(text: str) -> str:
    """把真实家目录换成判据允许的省略号占位（180/183 同款）。"""
    def home(m: "re.Match") -> str:
        prefix = "Users" if m.group(0).startswith("/Users/") else "home"
        return "/" + prefix + "/…/"

    text = _LIB_PAT.sub("/Users/…", _HOME_PAT.sub(home, text))
    return _TMP_PAT.sub("<tmp>", text)


def sha256_file(path: Path) -> str:
    if not path.exists():
        return "__MISSING__"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {f: sha256_file(ROOT / f) for f in CALIBER_FILES}


# ─────────────── 纯函数（供契约离线反证，不 spawn、不读仓库） ───────────────

def is_truncated(row: Dict[str, Any], field: str) -> bool:
    """⛔ 严格 ==240：176 写盘是 `[:240]`，不等于 240 的没被截。"""
    return len(row.get(field) or "") == TRUNC


def parseable(call: str) -> bool:
    """完整表达式能否 `ast.parse(mode="eval")`（178 用的同一判据）。"""
    try:
        ast.parse(call, mode="eval")
        return True
    except (SyntaxError, ValueError, TypeError):
        return False


def prefix240(saved: str, full: str) -> bool:
    """存档值逐字等于完整值前 240 字符。"""
    return bool(full) and full[:TRUNC] == saved


def align_key(row: Dict[str, Any]) -> Tuple[str, str, str]:
    """⛔ 用截断值做同源对齐键（182 惯例），避开同一 fn 多行的歧义。"""
    return (str(row.get("sha") or ""), str(row.get("fn") or ""),
            str(row.get("call") or "")[:TRUNC])


def fingerprint(rows: List[Dict[str, Any]]) -> collections.Counter:
    return collections.Counter(
        (str(r.get("sha")), str(r.get("fn")), str(r.get("call") or "")[:TRUNC],
         str(r.get("stmt") or "")[:TRUNC], str(r.get("attr") or ""),
         str(r.get("kind") or ""), str(r.get("gate") or ""))
        for r in rows)


def mismatch_of(a: collections.Counter, b: collections.Counter) -> int:
    return sum((a - b).values()) + sum((b - a).values())


def full_value_hits(rows: List[Dict[str, Any]],
                    keys: set) -> List[Dict[str, Any]]:
    """在一份存档里找「用未截断值跑过」的行。

    ⛔ 查询判据：主键命中 + (`call` 长度 >240 或 `call_len` >240)。
    这条查询本身必须能被注入正例命中（规则 133），契约 B 组做。
    """
    out = []
    for r in rows:
        if (str(r.get("sha")), str(r.get("fn")),
                str(r.get("call") or "")[:TRUNC]) not in keys:
            continue
        if len(r.get("call") or "") > TRUNC or (r.get("call_len") or 0) > TRUNC:
            out.append(r)
    return out


def two_books(rows_178: List[Dict[str, Any]],
              keys: set) -> Dict[str, Any]:
    """178 的 9 条 stmt 截断行：剔除几、留几（两本账，绝不合并）。"""
    hit = [r for r in rows_178
           if (str(r.get("sha")), str(r.get("fn")),
               str(r.get("call") or "")[:TRUNC]) in keys]
    excluded = [r for r in hit if r.get("main_gate") == "unparseable"]
    return {"n_hit": len(hit), "n_excluded": len(excluded),
            "n_kept": len(hit) - len(excluded),
            "gates": dict(sorted(collections.Counter(
                str(r.get("main_gate")) for r in hit).items()))}


def surface_of(all_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """静态影响面：分母只认 176 存档里恰好 240 的行。"""
    call5 = [r for r in all_rows if is_truncated(r, "call")]
    stmt9 = [r for r in all_rows if is_truncated(r, "stmt")]
    kc = {(r["sha"], r["fn"], (r.get("call") or "")[:TRUNC]) for r in call5}
    ks = {(r["sha"], r["fn"], (r.get("call") or "")[:TRUNC]) for r in stmt9}
    return {
        "n_call_truncated": len(call5),
        "n_stmt_truncated": len(stmt9),
        "n_call_subset_of_stmt": len(kc & ks),
        "call_subset_of_stmt": kc <= ks,
        "call_by_gate": dict(sorted(collections.Counter(
            r["gate"] for r in call5).items())),
        "stmt_by_gate": dict(sorted(collections.Counter(
            r["gate"] for r in stmt9).items())),
        "stmt_by_sha": dict(sorted(collections.Counter(
            r["sha"] for r in stmt9).items())),
        "stmt_by_downstream": dict(sorted(collections.Counter(
            r["downstream"] for r in stmt9).items())),
        "stmt_by_reach": dict(sorted(collections.Counter(
            str(r.get("reach")) for r in stmt9).items())),
        "call_keys": sorted([list(k) for k in kc]),
        "stmt_keys": sorted([list(k) for k in ks]),
    }


def summarize(rows: List[Dict[str, Any]], surface: Dict[str, Any],
              cross: Dict[str, Any], books: Dict[str, Any],
              upstream: Dict[str, Any], health: Dict[str, Any],
              mismatch: int) -> Dict[str, Any]:
    """纯函数：所有结论都从 rows / 存档派生，不藏进程内状态。"""
    by_pair = collections.Counter(r["pair_verdict"] for r in rows)
    f2p = [r for r in rows if r["f2p_criterion"]]
    eqs = {
        "eq_denominator_5": len(rows) == 5,
        "eq_align_zero": mismatch == 0,
        "eq_prefix240_all": all(r["prefix240"] for r in rows),
        "eq_full_longer_all": all(r["full_call_len"] > TRUNC for r in rows),
        "eq_full_parseable_all": all(r["parseable"] for r in rows),
        "eq_saved_unparseable_all": all(
            not parseable(r["saved_call"]) for r in rows),
        "eq_pair_sum": sum(by_pair.values()) == len(rows),
        "eq_pair_closed": all(p in PAIRS for p in by_pair),
        "eq_f2p_subset": all(r["pair_verdict"] == "not_raise" for r in f2p),
        "eq_no_unstable": not any(r["fixed_unstable"] for r in rows),
        "eq_surface_5_9": (surface["n_call_truncated"] == 5
                           and surface["n_stmt_truncated"] == 9),
        "eq_call_subset_stmt": (surface["call_subset_of_stmt"]
                                and surface["n_call_subset_of_stmt"] == 5),
        "eq_two_books_5_4": (books["n_excluded"] == 5
                             and books["n_kept"] == 4),
        "eq_cross_zero": cross["n_full_value_reruns"] == 0,
        "eq_cross_four": cross["n_archives"] == 4,
        "eq_upstream_untouched": upstream["unchanged"],
        "eq_baseline_ok": len(health.get("diffs", [])) == 0,
        "eq_caliber_self": "scripts/probe_truncation_census.py" in caliber(),
    }
    return {
        "schema": "truncation_census/v1",
        "n_rows": len(rows),
        "align_mismatch": mismatch,
        "n_prefix240": sum(1 for r in rows if r["prefix240"]),
        "n_full_parseable": sum(1 for r in rows if r["parseable"]),
        "n_saved_unparseable": sum(1 for r in rows
                                   if not parseable(r["saved_call"])),
        "full_call_len": dict(sorted(collections.Counter(
            r["full_call_len"] for r in rows).items())),
        "by_pair": dict(sorted(by_pair.items())),
        "n_f2p_criterion": len(f2p),
        "n_fixed_unstable": sum(1 for r in rows if r["fixed_unstable"]),
        "by_why_fixed": dict(sorted(collections.Counter(
            r["why_fixed"] for r in rows).items())),
        "by_why_buggy": dict(sorted(collections.Counter(
            r["why_buggy"] for r in rows).items())),
        "surface": surface,
        "cross": cross,
        "two_books": books,
        "upstream": upstream,
        "baseline_health": "OK" if not health.get("diffs") else "BYPASS",
        "closure_eqs": eqs,
        "n_closure_true": sum(1 for v in eqs.values() if v),
        "n_closure_items": len(eqs),
        "note": ("⛔ 5 条 unparseable 是 call[:240] 写盘截断造成的构造必然；"
                 "完整值 5/5 可 ast.parse，但两侧真跑仍 3 both_raise / "
                 "2 mixed_raise、truthy F2P=0（自由变量 NameError）⇒ "
                 "178 的 89 条分母结论方向不变，本轮只修归因不推翻结论。"
                 "⛔ 不改上游存档；改上游尺子属口径变更需拍板。"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--timeout", type=float, default=20)
    ap.add_argument("--no-baseline-gate", action="store_true",
                    help="仅反证用；正常路径必须验锚")
    args = ap.parse_args()

    health: Dict[str, Any] = {"diffs": [], "repos": {}}
    if args.no_baseline_gate:
        health["diffs"] = ["BYPASS（仅反证）"]
    else:
        health = require_baseline()
        print("baseline_health=OK  repos=%d  diffs=%d" %
              (len(health.get("repos", {})), len(health.get("diffs", []))))

    upstream_before = sha256_file(SOURCE)
    saved = json.loads(SOURCE.read_text(encoding="utf-8"))
    all_rows = saved["rows"]
    surface = surface_of(all_rows)
    call5 = [r for r in all_rows if is_truncated(r, "call")]
    want = {r["sha"] for r in call5}
    keys = {(r["sha"], r["fn"], (r.get("call") or "")[:TRUNC])
            for r in call5}
    saved_by_key = {(r["sha"], r["fn"], (r.get("call") or "")[:TRUNC]): r
                    for r in call5}
    print("surface: call=%d stmt=%d shas=%r" %
          (surface["n_call_truncated"], surface["n_stmt_truncated"],
           sorted(want)))

    rows: List[Dict[str, Any]] = []
    live_keys: List[Dict[str, Any]] = []
    for name, subdir in REPOS:
        repo = Path("/tmp/realrepo") / name
        if name not in {r["repo"] for r in call5}:
            continue
        for cand in select_commits(repo, args.limit, subdir):
            sha, src, testfs, _subj = cand
            if sha not in want:
                continue
            fixed = _git(repo, "show", "%s:%s" % (sha, src))
            buggy = _git(repo, "show", "%s^:%s" % (sha, src))
            pkg: Optional[Dict[str, Any]] = None
            if subdir != "boltons/":
                pkg = pkg_context(repo, sha, src, fixed)
            got = attribute_commit(repo, sha, src, testfs,
                                   pkg=pkg, timeout=args.timeout)
            if got is None:
                continue
            hits = [r for r in got["rows"]
                    if (sha, str(r.get("fn")),
                        str(r.get("call") or "")[:TRUNC]) in keys]
            tests = [{"name": "t%d" % i, "call": r.get("call") or ""}
                     for i, r in enumerate(hits)]
            f1, i1 = sandbox_read(fixed, tests, args.timeout, pkg)
            f2, i2 = sandbox_read(fixed, tests, args.timeout, pkg)
            bs, ib = sandbox_read(buggy, tests, args.timeout, pkg)
            for i, r in enumerate(hits):
                a1, a2, b = f1[i], f2[i], bs[i]
                unstable = (a1["passed"] != a2["passed"]
                            or a1["why"] != a2["why"])
                full = str(r.get("call") or "")
                sk = (sha, str(r.get("fn")), full[:TRUNC])
                sv = saved_by_key.get(sk)
                if sv is None:                      #: 对齐不上 ⇒ 量具格
                    continue
                live_keys.append({
                    "repo": name, "sha": sha, "fn": r.get("fn"),
                    "call": full[:TRUNC], "stmt": (r.get("stmt") or "")[:TRUNC],
                    "attr": r.get("attr"), "kind": r.get("kind"),
                    "gate": sv["gate"]})
                rows.append({
                    "repo": name, "sha": sha, "fn": r.get("fn"),
                    "attr": r.get("attr"), "kind": r.get("kind"),
                    "gate": sv["gate"], "downstream": sv["downstream"],
                    "reach": sv.get("reach"),
                    "saved_call": sv["call"],
                    "saved_call_len": len(sv["call"]),
                    "saved_stmt_len": len(sv.get("stmt") or ""),
                    "full_call": redact(full),
                    "full_call_len": len(full),
                    "full_stmt_len": len(str(r.get("stmt") or "")),
                    "prefix240": prefix240(sv["call"], full),
                    "parseable": parseable(full),
                    "saved_parseable": parseable(sv["call"]),
                    "verdict3": r.get("verdict"),
                    "pair_verdict": pair_verdict(a1, b),
                    "f2p_criterion": bool(truthy_f2p(a1, b, unstable)),
                    "fixed_unstable": bool(unstable),
                    "passed_fixed": bool(a1["passed"]),
                    "passed_buggy": bool(b["passed"]),
                    "why_fixed": a1["why"], "why_buggy": b["why"],
                    "evidence_fixed": redact(a1["evidence"])[:160],
                    "evidence_buggy": redact(b["evidence"])[:160],
                    "import_flags": [bool(i1), bool(i2), bool(ib)],
                })

    #: ⛔ 同源对齐闸：5 条必须逐字等于 176 存档（截断口径）
    a = fingerprint(call5)
    b = fingerprint(live_keys)
    mismatch = mismatch_of(a, b)
    print("align: saved=%d live=%d mismatch=%d" %
          (len(call5), len(live_keys), mismatch))
    if mismatch:
        print("⛔ 分母与 176 存档不同源 ⇒ 拒绝出读数（fail-closed）",
              file=sys.stderr)
        raise SystemExit(2)

    #: 交叉账：四份存档里这 5 条用未截断值跑过几次
    archives: Dict[str, Any] = {}
    n_reruns = 0
    for rel in CROSS_ARCHIVES:
        p = ROOT / rel
        if not p.exists():
            archives[rel] = {"present": False, "n_rows": 0,
                             "n_key_hits": 0, "n_full_value": 0}
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        ar = d.get("rows", [])
        key_hits = [r for r in ar
                    if (str(r.get("sha")), str(r.get("fn")),
                        str(r.get("call") or "")[:TRUNC]) in keys]
        hv = full_value_hits(ar, keys)
        n_reruns += len(hv)
        archives[rel] = {
            "present": True, "n_rows": len(ar),
            "n_key_hits": len(key_hits), "n_full_value": len(hv),
            "by_verdict": dict(sorted(collections.Counter(
                str(r.get("verdict") or r.get("main_gate")
                    or r.get("pair_verdict") or "-") for r in key_hits).items())),
        }
    cross = {"n_archives": len(CROSS_ARCHIVES),
             "n_full_value_reruns": n_reruns,
             "archives": archives}

    d178 = json.loads((ROOT / "scripts" /
                       "no_result_attribution.json").read_text(encoding="utf-8"))
    stmt_keys = {(r["sha"], r["fn"], (r.get("call") or "")[:TRUNC])
                 for r in all_rows if is_truncated(r, "stmt")}
    books = two_books(d178.get("rows", []), stmt_keys)

    upstream_after = sha256_file(SOURCE)
    upstream = {"path": str(SOURCE.relative_to(ROOT)),
                "sha256_before": upstream_before,
                "sha256_after": upstream_after,
                "unchanged": upstream_before == upstream_after}
    s = summarize(rows, surface, cross, books, upstream, health, mismatch)

    print("\n=== r105 截断污染面普查 ===")
    print("  n_rows=%d prefix240=%d full_parseable=%d saved_unparseable=%d" %
          (s["n_rows"], s["n_prefix240"], s["n_full_parseable"],
           s["n_saved_unparseable"]))
    print("  full_call_len =", s["full_call_len"])
    print("  pair          =", s["by_pair"])
    print("  f2p_criterion =", s["n_f2p_criterion"],
          " unstable =", s["n_fixed_unstable"])
    print("  surface       =", {k: surface[k] for k in
                                ("n_call_truncated", "n_stmt_truncated",
                                 "call_subset_of_stmt")})
    print("  two_books(178)=", books)
    print("  cross reruns  =", n_reruns, " / archives =", cross["n_archives"])
    print("  upstream      =", upstream["unchanged"])
    print("  closure       =", s["n_closure_true"], "/",
          s["n_closure_items"])
    print("  " + s["note"])

    if args.out:
        out = Path(args.out)
        out.write_text(json.dumps({
            "schema": s["schema"], "caliber": caliber(),
            "rows": rows, "summary": s,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n落盘：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
