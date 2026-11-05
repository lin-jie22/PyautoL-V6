#!/usr/bin/env python3
"""**判据侧门量级普查** —— README 176。

═══ 它回答什么 ═══

175 把定位门 cancel 之后，唯一大头是判据侧 **102/120**
（`no_criteria` 30 + `no_f2p` 72）。但 175 只按 commit 判
「判据集在 buggy 版本上全过」，没回答：人类 PR 自己新增的那条断言，
到底倒在哪道门上、那道门后面还有没有门。

本脚本复用 166 的同源尺子 `probe_gate_attribution.attribute_commit`
（⛔ 不另写判据、不另写三验、不另写新增断言集合），对三仓库全部
120 个候选逐条归因，并与 175 的八格 verdict 交叉，量化三种成因：

    A 需拍板    form / tuple / 真 `n_gold_unjsonable`（判据形态表达力）
    B 不需拍板  `n_gold_unresolved`（期望值没解出 ⇒ 抽取侧工程缺口）、
                ctx（上下文 / 装载 / 探针求不出值）
    C 原则性    P2P（人类断言不覆盖该改动）、F2P 后仍被形态门吞掉

⛔⛔ **分寸（不许越界）**：倒在抽取层门的断言**下游未测** ——
166 实测「解开 form 门最大的一格（assertRaises-lambda）」后 10 条
**全部落 ctx、F2P 仍是 0**。所以「倒在这道门」≠「被拦下的 F2P」，
这些行一律记 `downstream=unknown`，不参与任何收益推断。

═══ 与 166 的差别（⛔ 跨口径不可比）═══

166 只跑 `--limit 10` × 3 仓库，且是在 `cd9e90c` 的旧口径下出的表
（boltons F2P 22/53）。之后 `5ad4553`（README 167）改了
`aether/extract.py`：拆 `n_unjsonable` + 修「探针/期望写反」。
**当前口径下复现为 10/54** ⇒ 166 那 22 条里含假 F2P。
按第 90 条纪律，⛔ 不许拿旧表给当前读数背书，也不许反过来。

═══ 口径签名（补 174 的洞）═══

174 的锚 `caliber.extractor_sha256` 钉的是 `probe_alias_demote.select_commits`
（候选序列零点），**没有钉抽取器源码** ⇒ 抽取器一改，跨轮读数就悄悄变了。
本存档额外记录 4 个相关源码的 sha256；契约钉死「存档 hash == 当前文件
hash」，以后有人改抽取器，这份读数立刻被标为过期（尺子可读不可写）。

用法：
    python scripts/probe_criteria_gate_census.py --only 4e5faa3,c1c25da
    python scripts/probe_criteria_gate_census.py --out scripts/criteria_gate_census.json
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aether.extract import extract_criteria, target_module      # noqa: E402
from probe_alias_demote import select_commits, _git           # noqa: E402
from probe_package_load import pkg_context                     # noqa: E402
from probe_gate_attribution import (                            # noqa: E402
    ALL_GATES, attribute_commit)
from realrepo_baseline import require_baseline                  # noqa: E402

REPOS: Tuple[Tuple[str, str, str], ...] = (
    ("boltons", "boltons/", "census_boltons_40.json"),
    ("more-itertools", "more_itertools/", "census_mi_40.json"),
    ("attrs", "src/attr/", "census_attrs_40.json"),
)

#: ⓪ 尺子本身：改了其中任何一个，存档读数就过期（⛔ 不许静默沿用）
CALIBER_FILES = (
    "aether/extract.py",
    "aether/environment.py",
    "scripts/probe_auto_unlock.py",
    "scripts/probe_gate_attribution.py",
    "scripts/probe_alias_demote.py",
)

#: 抽取层的门（没进沙箱 ⇒ 下游未测）vs 沙箱层的门（真跑过）
EXTRACT_GATES = ("criterion", "form", "unserializable", "tuple", "unstable")
SAND_GATES = ("ctx", "P2P", "F2P")
VERDICTS175 = ("no_src", "no_criteria", "blew", "no_f2p",
               "f2p_but_no_focus", "no_units", "out_of_reach", "in_reach")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {f: sha256_file(ROOT / f) for f in CALIBER_FILES}


def caliber_matches(saved: Dict[str, str],
                    disk: Dict[str, str]) -> List[str]:
    """⛔ 纯函数（供契约离线反证）：存档口径签名 vs 当前磁盘源码。

    返回**差异文件名列表**，空 ⇒ 匹配。⛔ 缺键也算差异（不许默认值兜底，
    否则「没记」和「记了假值」不可区分）。
    """
    return sorted(f for f in disk if saved.get(f) != disk[f])


#: ⛔ **唯一字段事实源 + 别名表**（data-artifact-contract 第 77 条）。
#: 166 时代 `auto_extract` 的 stx 用合并键名 `n_unserializable`；167 把
#: 期望侧拆成 `n_gold_unresolved` / `n_gold_unjsonable` 后，`attribute_statements`
#: 的 stats 用新键名，但 stx 的旧键名**没跟着改**。⛔ 直接 `.get(新键, 0)`
#: 会静默得 0 ⇒ 造出「两条调用链不一致」的假读数（本轮实测踩中）。
#: 修法：规范名优先，缺失才按别名表回退，且**回退次数必须可计数**。
EXPECT_KEYS = ("n_gold_unresolved", "n_gold_unjsonable",
               "n_gold_resolved", "n_gold_swapped", "n_raw", "n_out")
EXPECT_ALIASES = {"n_gold_unjsonable": ("n_unserializable",)}


def stat_of(d: Dict[str, Any], key: str,
            hits: Optional[Dict[str, int]] = None) -> int:
    """按唯一字段事实源取值。⛔ 纯函数，供契约离线反证。"""
    if key in d:
        return int(d[key])
    for alias in EXPECT_ALIASES.get(key, ()):
        if alias in d:
            if hits is not None:
                hits[alias] = hits.get(alias, 0) + 1
            return int(d[alias])
    return 0


def expect_full(repo: Path, sha: str, src: str,
                testfs: List[str]) -> Dict[str, int]:
    """**全量**期望侧计数（修 `extract_criteria` 的 stats.update 覆盖语义）。

    `attribute_commit` 把同一个 `stats` dict 传给每个测试文件，而
    `extract_criteria` 末尾是 `stats.update(st)`（**覆盖**不是累加）⇒
    多测试文件的 commit 只保留**最后一个文件**的计数。本函数改为
    每个文件独立传 `stats={}` 再累加，仍只调产品自己的 `extract_criteria`
    （⛔ 不另写判据 / 不另写抽取规则）。
    """
    out: Dict[str, int] = {"n_files": 0}
    target = target_module(src)
    for tf in testfs:
        tsrc = _git(repo, "show", "%s:%s" % (sha, tf))
        if not tsrc:
            continue
        st: Dict[str, Any] = {}
        extract_criteria(tsrc, target, stats=st)
        for k in EXPECT_KEYS:
            out[k] = out.get(k, 0) + int(st.get(k, 0))
        out["n_files"] += 1
    return out


def downstream_of(gate: str) -> str:
    """⛔ 纯函数，供契约离线反证。抽取层的门不许被读成「拦下了 F2P」。"""
    if gate in SAND_GATES:
        return "measured"
    return "unknown"


def cross_tab(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """175 八格 × 166 门 ⇒ 交叉表。纯函数。"""
    out: Dict[str, Dict[str, int]] = {v: {} for v in VERDICTS175}
    for r in rows:
        v, g = r.get("v175"), r["gate"]
        if v not in out:
            continue
        d = out[v]
        d[g] = d.get(g, 0) + 1
    return out


def summarize(rows: List[Dict[str, Any]], commits: List[Dict[str, Any]],
              health: Dict[str, Any], skipped: List[Dict[str, Any]]) -> Dict[str, Any]:
    """纯函数：所有结论都从 rows/commits 派生，不藏进程内状态。"""
    gates = collections.Counter(r["gate"] for r in rows)
    form_attrs = collections.Counter(r["attr"] for r in rows
                                     if r["gate"] == "form")
    ctx_verdicts = collections.Counter(r["sandbox_verdict"] for r in rows
                                       if r["gate"] == "ctx")
    f2p_by_v = collections.Counter(r["v175"] for r in rows
                                   if r["gate"] == "F2P")
    by_repo = {n: dict(collections.Counter(
        r["gate"] for r in rows if r["repo"] == n))
        for n, _, _ in REPOS}

    #: 166 的 stats：⛔ commit 级采样（多测试文件时只留最后一个），仅作对照
    expect: Dict[str, int] = {}
    for key in EXPECT_KEYS:
        expect[key] = sum(int(c["stats"].get(key, 0)) for c in commits)
    #: 同源恒等式：两条调用链经**别名表归一**后应一致（漂移 ⇒ 量具可疑）
    alias_hits: Dict[str, int] = {}
    expect_cross = {
        key: sum(stat_of(c["extract_stats"], key, alias_hits)
                 for c in commits)
        for key in ("n_gold_unresolved", "n_gold_unjsonable")}
    #: 全量：逐测试文件独立 stats 累加（⛔ 结论只用这份）
    full = collections.Counter()
    for c in commits:
        for k, v in c.get("expect_full", {}).items():
            full[k] += int(v)

    census_rows = 0
    for _name, _subdir, cf in REPOS:
        census_rows += len(json.loads(
            (ROOT / "scripts" / cf).read_text(encoding="utf-8")))
    not_in_census = sorted({(c["repo"], c["sha"]) for c in commits
                            if not c["in_census"]})
    return {
        "n_commits": len(commits),
        "n_rows": len(rows),
        "n_skipped": len(skipped),
        "skipped": skipped,
        "gates": dict(gates),
        "cross": cross_tab(rows),
        "by_repo": by_repo,
        "form_attrs": dict(form_attrs.most_common()),
        "ctx_verdicts": dict(ctx_verdicts.most_common()),
        "f2p_by_v175": dict(f2p_by_v),
        "commits_with_f2p": sum(1 for c in commits
                                if c["gates"].get("F2P", 0) > 0),
        "expect_side_sampled_166": expect,
        "expect_side_full": dict(full),
        "expect_side_cross_check": expect_cross,
        "expect_alias_hits": alias_hits,
        "downstream": dict(collections.Counter(
            r["downstream"] for r in rows)),
        "two_books": {"n_candidates": len(commits) + len(skipped),
                      "n_census_readings": census_rows,
                      "not_in_census": [list(x) for x in not_in_census]},
        "baseline_health": {"ok": health.get("ok"),
                            "diffs": len(health.get("diffs", [])),
                            "caliber_diffs": len(health.get(
                                "caliber_diffs", []))},
        "unknown_note": (
            "抽取层门（form/unserializable/tuple/unstable）的行下游未测，"
            "不得读作「被拦下的 F2P」；166 实测解开 assertRaises-lambda "
            "一格后 10 条全落 ctx、F2P 仍 0。"),
    }


def print_summary(s: Dict[str, Any]) -> None:
    print("\n═══ 全量门分布（120 候选 / PR 新增断言逐条）═══")
    for g in list(ALL_GATES) + ["unmatched"]:
        n = s["gates"].get(g, 0)
        if n:
            print("  %-16s %5d  %5.1f%%" % (g, n, 100.0 * n / max(s["n_rows"], 1)))

    print("\n═══ 175 八格 × 166 门（交叉表）═══")
    hdr = [g for g in list(ALL_GATES) + ["unmatched"]
           if any(g in s["cross"][v] for v in VERDICTS175)]
    print("  %-18s %s" % ("verdict175", " ".join("%6s" % g[:6] for g in hdr)))
    for v in VERDICTS175:
        d = s["cross"][v]
        if not d:
            continue
        print("  %-18s %s" % (v, " ".join(
            "%6d" % d.get(g, 0) for g in hdr)))

    print("\n═══ 判据侧 102 例的成因（⛔ 只报门，不推断下游）═══")
    for v in ("no_criteria", "no_f2p"):
        d = s["cross"][v]
        print("  %-14s %s" % (v, dict(sorted(d.items()))))

    print("\n═══ form 门写法细分（需拍板的扩张候选）═══")
    for k, v in list(s["form_attrs"].items())[:12]:
        print("  %-24s %d" % (k, v))

    print("\n═══ ctx 门三验裁决细分（不需拍板的工程残留）═══")
    for k, v in list(s["ctx_verdicts"].items())[:8]:
        print("  %-24s %d" % (k, v))

    e = s["expect_side_full"]
    print("\n═══ 167 期望侧拆分（⛔ 全量逐文件累加；非逐条对应到门里的行）═══")
    print("  n_files（实测测试文件数）= %d" % e.get("n_files", 0))
    print("  n_gold_unresolved（没解出值 ⇒ 可修）= %d" %
          e.get("n_gold_unresolved", 0))
    print("  n_gold_unjsonable（真不可 JSON ⇒ 结构性）= %d" %
          e.get("n_gold_unjsonable", 0))
    print("  n_gold_swapped（167 写反修复救回）= %d" %
          e.get("n_gold_swapped", 0))
    print("  166 stats 采样对照 =", s["expect_side_sampled_166"])
    print("  两条链交叉自检（经别名表归一）= %s  alias_hits=%s" %
          (s["expect_side_cross_check"], s["expect_alias_hits"]))

    print("\n═══ 账目 / 健康度 ═══")
    print("  commits=%d rows=%d skipped=%d  F2P commit=%d" %
          (s["n_commits"], s["n_rows"], s["n_skipped"],
           s["commits_with_f2p"]))
    print("  two_books=", s["two_books"])
    print("  baseline_health=", s["baseline_health"])
    print("  downstream=", s["downstream"])
    print("  ⛔ " + s["unknown_note"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--out", default="")
    ap.add_argument("--no-baseline-gate", action="store_true",
                    help="⛔ 仅供离线反证；正式普查必须验锚")
    args = ap.parse_args()

    #: 174 规则：先验锚，并把健康度当读数报出来（169）
    health: Dict[str, Any] = {"ok": None, "diffs": [], "caliber_diffs": []}
    if args.no_baseline_gate:
        health["diffs"] = ["BYPASS（仅反证）"]
    else:
        health = require_baseline()
        print("baseline_health=OK  repos=%d  diffs=%d" %
              (len(health.get("repos", {})), len(health.get("diffs", []))))

    focus = {(r["repo"], r["sha"]): r for r in json.loads(
        (ROOT / "scripts" / "focus_census.json").read_text(
            encoding="utf-8"))["rows"]}
    census: Dict[str, Dict[str, Any]] = {}
    for _, _, cf in REPOS:
        for row in json.loads((ROOT / "scripts" / cf).read_text(
                encoding="utf-8")):
            census[row["sha"]] = row

    want = set(x for x in args.only.split(",") if x)
    rows: List[Dict[str, Any]] = []
    commits: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for name, subdir, _cf in REPOS:
        repo = Path("/tmp/realrepo") / name
        for cand in select_commits(repo, args.limit, subdir):
            sha, src, testfs, subj = cand
            if want and sha not in want:
                continue
            pkg: Optional[Dict[str, Any]] = None
            if subdir != "boltons/":
                pkg = pkg_context(repo, sha, src,
                                  _git(repo, "show", "%s:%s" % (sha, src)))
            try:
                got = attribute_commit(repo, sha, src, testfs, pkg=pkg,
                                       timeout=args.timeout)
            except Exception as exc:                 # 单 commit 炸不连坐
                print("  %s %s 异常：%s: %s" %
                      (name, sha, type(exc).__name__, exc))
                skipped.append({"repo": name, "sha": sha,
                                "reason": "%s: %s" % (type(exc).__name__, exc)})
                continue
            if got is None:
                skipped.append({"repo": name, "sha": sha,
                                "reason": "no fixed/buggy pair"})
                continue

            f = focus.get((name, sha), {})
            c = census.get(sha, {})
            v175 = f.get("verdict", "unknown")
            for r in got["rows"]:
                gate = r["gate_final"]
                rows.append({
                    "repo": name, "sha": sha, "gate": gate,
                    "attr": r.get("attr"), "kind": r.get("kind"),
                    "sandbox_verdict": r.get("verdict"),
                    "fn": r.get("fn"),
                    "call": (r.get("call") or "")[:240],
                    "stmt": (r.get("stmt") or "")[:240],
                    "v175": v175, "reach": f.get("reach"),
                    "in_census": bool(c),
                    "downstream": downstream_of(gate),
                })
            gc = dict(collections.Counter(
                r["gate_final"] for r in got["rows"]))
            commits.append({
                "repo": name, "sha": sha, "src": src,
                "n_new": got["n_new"], "n_tests_total": got["n_tests_total"],
                "stats": got["stats"], "extract_stats": got["extract_stats"],
                "expect_full": expect_full(repo, sha, src, testfs),
                "gates": gc, "v175": v175, "reach": f.get("reach"),
                "in_census": bool(c), "census_stage": c.get("stage"),
            })
            print("  %-16s %s  %-12s new=%-3d %s" %
                  (name, sha, v175, got["n_new"], gc))

    s = summarize(rows, commits, health, skipped)
    print_summary(s)

    if args.out:
        out = Path(args.out)
        out.write_text(json.dumps({
            "schema": "criteria-gate-census/v1",
            "caliber": caliber(),
            "rows": rows, "per_commit": commits, "summary": s,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n落盘：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
