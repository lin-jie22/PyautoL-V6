#!/usr/bin/env python3
"""r103 / README 182 —— 123 条 `ctx` 门的「门后第一格」同源运行时普查。

═══ 要填的洞 ═══

176（README 7949）把 459 条 PR 新增断言逐条归到门，其中 **123 条落 ctx**，
裁决是 `auto_extract` 三验对**参照实现（fixed）**给出的
`no_result 61 / unpassed_on_fixed 53 / gold_mismatch 9`。
177 只处理 `downstream=unknown` 的 254 条（主账 135 + form 账 119），
**ctx 一条都没碰**；167 的「三轮开门收益 0」开的是 164/165/166 的
装载 / 判据抽取 / form 最大格，也没有一轮开过 ctx。

⛔⛔ 关键：`attribute_commit` 只在 **fixed 侧**做三验，
`run_in_sandbox(buggy, sel_tests, ...)` 只对**过了三验**的 `sel_tests`
跑 ⇒ 这 123 条 **从没在 buggy 侧跑过**；存档里也没有任何收益字段。
「没测过 ≠ 测出 0」，本轮把这块从没测过推进到测出来是多少。

═══ ⛔ 本脚本做什么、不做什么 ═══

✅ **只做一件事**：把 176 的 123 条 ctx 行，用产品自己的 truthy 探针
   （`{"name","call"}`，⛔ 不带 expect ⇒ 走 `environment.py:299` 的
   `ok = bool(got)`，不手写 oracle、不触发 171 的 oracle_bad 闸）
   在 **fixed / buggy 两侧真跑**，记产出方写死的 `why` / `passed`，判两格：
     ① `pair_verdict`（运行时账，封闭 5 格）
     ② `f2p_criterion`（`passed_fixed and not passed_buggy`，177 同源）
✅ **两本账分开**：176 的 `verdict3` 与这里的 `pair_verdict` 只做交叉表，
   绝不合并成一个布尔（181 教训）。fixed 侧跑两次做稳定性自检。
⛔ **不改 `aether/` 一个字节**、不扩张判据形态、不放宽内联器
   （后者属拍板项 ④）、不产出新判据、不报「修好几个」。
   `f2p_criterion` 只是**判据条数**，不是收益；`reach` 是事后 oracle，
   只记账、不当 predictor。⛔ 不许宣布「扩张判据形态无用」。
⛔ **不另写第二把尺子**（175 教训）：探针来自产品 `attribute_commit`
   的 `call`（未截断；176 存档 `call[:240]` 是已知残留），
   沙箱用 `aether.environment.run_in_sandbox` 本身，
   分类只看产出方写死的 `why`，不解析 evidence 文案（178 教训）。

═══ 用法 ═══
    python scripts/probe_ctx_downstream.py --only 4e5faa3,eb65901
    python scripts/probe_ctx_downstream.py --out scripts/ctx_downstream.json
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

from aether.environment import run_in_sandbox                    # noqa: E402
from probe_alias_demote import select_commits, _git             # noqa: E402
from probe_package_load import pkg_context                      # noqa: E402
from probe_gate_attribution import attribute_commit             # noqa: E402
from realrepo_baseline import require_baseline                  # noqa: E402

SOURCE = ROOT / "scripts" / "criteria_gate_census.json"

REPOS: Tuple[Tuple[str, str], ...] = (
    ("boltons", "boltons/"),
    ("more-itertools", "more_itertools/"),
    ("attrs", "src/attr/"),
)

#: ⛔ 封闭集合：不许为了迁就读数新增格（166 纪律）。
VERDICTS3: Tuple[str, ...] = (
    "no_result", "unpassed_on_fixed", "gold_mismatch")
PAIRS: Tuple[str, ...] = (
    "both_raise", "mixed_raise", "not_raise", "import_failed",
    "missing_result")

#: 对齐键：⛔ 只取 `attribute_commit` 与 176 存档共有的字段，
#: 不含 176 的交叉列（v175 / reach / in_census），避免跨口径关联歧义。
ALIGN_FIELDS = ("repo", "sha", "fn", "stmt", "call", "attr", "kind", "verdict3")

CALIBER_FILES = (
    "aether/extract.py",
    "aether/environment.py",
    "scripts/probe_gate_attribution.py",
    "scripts/probe_criteria_gate_census.py",
    "scripts/probe_alias_demote.py",
    "scripts/probe_package_load.py",
    "scripts/probe_extract_downstream.py",
    "scripts/probe_no_result_attribution.py",
    "scripts/criteria_gate_census.json",
    "scripts/probe_ctx_downstream.py",   #: ⛔ 钉尺子自身（174 教训）
)


# ─────────────────────────── 纯函数（供契约离线反证） ───────────────────────────
def sha256_file(path: Path) -> str:
    if not path.exists():
        return "__MISSING__"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {f: sha256_file(ROOT / f) for f in CALIBER_FILES}


def fingerprint(rows: List[Dict[str, Any]]) -> collections.Counter:
    """冻结成可比较的多重集；⛔ 逐字段取值，不做模糊匹配。"""
    return collections.Counter(
        tuple(json.dumps(r.get(f), sort_keys=True) for f in ALIGN_FIELDS)
        for r in rows)


def mismatch_of(a: collections.Counter,
                b: collections.Counter) -> int:
    """两本账的对称差条数。0 才叫同源。"""
    return sum((a - b).values()) + sum((b - a).values())


def pair_verdict(f: Optional[Dict[str, Any]],
                 b: Optional[Dict[str, Any]]) -> str:
    """⛔ 纯函数：两侧运行时账，恰好一格。缺结果一律量具格。"""
    if not f or not b:
        return "missing_result"
    if f.get("why") == "import" or b.get("why") == "import":
        return "import_failed"
    if f.get("missing") or b.get("missing"):
        return "missing_result"
    wf, wb = f.get("why"), b.get("why")
    if wf == "raise" and wb == "raise":
        return "both_raise"
    if "raise" in (wf, wb):
        return "mixed_raise"
    return "not_raise"


def truthy_f2p(f: Optional[Dict[str, Any]],
               b: Optional[Dict[str, Any]],
               unstable: bool = False) -> bool:
    """⛔ 纯函数：现有 truthy 判据形态能不能直接给出真 F2P。

    照抄 177 `probe_extract_downstream.truthy_f2p` 的 fail-closed：
    缺结果 / 异常 / import 失败 / fixed 两次不稳定一律 False，
    不许把「没跑到」写成 F2P（契约 B9f 抓到过的坑）。
    """
    if unstable or not f or not b:
        return False
    if f.get("why") in ("import", "raise") or b.get("why") in ("import", "raise"):
        return False
    if f.get("missing") or b.get("missing"):
        return False
    return bool(f.get("passed")) and not bool(b.get("passed"))


def sandbox_read(source: str, tests: List[Dict[str, Any]],
                 timeout: float,
                 pkg: Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
    """同源 truthy 沙箱；返回长度**恒等于** tests 的结果表 + import 标志。

    ⛔ 只看产出方写死的 `why` / `passed`，不解析 evidence 文案猜分类
    （178 教训）。results 不足 ⇒ fail-closed 补 missing_result，
    不许静默错位（第 159 条：返回类型不匹配被静默吞）。
    """
    ex = run_in_sandbox(source, tests, timeout=timeout, pkg=pkg)
    if ex.import_error:
        tail = "import-failed: " + str(ex.import_error)[:160]
        return ([{"why": "import", "passed": False, "evidence": tail,
                  "missing": False} for _ in tests], True)
    out = [{"why": str(r.get("why")), "passed": bool(r.get("passed")),
            "evidence": str(r.get("evidence") or "")[:200],
            "missing": False} for r in ex.results]
    while len(out) < len(tests):
        out.append({"why": "missing_result", "passed": False,
                    "evidence": "", "missing": True})
    del out[len(tests):]
    return out, False


def summarize(rows: List[Dict[str, Any]],
              saved_ctx: List[Dict[str, Any]],
              health: Dict[str, Any],
              skipped: List[Dict[str, Any]],
              mismatch: int) -> Dict[str, Any]:
    """纯函数：所有结论都从 rows 派生，不藏进程内状态。"""
    by_v3 = collections.Counter(r["verdict3"] for r in rows)
    by_pair = collections.Counter(r["pair_verdict"] for r in rows)
    cross = collections.Counter(
        "%s/%s" % (r["verdict3"], r["pair_verdict"]) for r in rows)
    f2p = [r for r in rows if r["f2p_criterion"]]
    eqs = {
        "eq_denominator": len(rows) == len(saved_ctx),
        "eq_align_zero": mismatch == 0,
        "eq_pair_sum": sum(by_pair.values()) == len(rows),
        "eq_v3_sum": sum(by_v3.values()) == len(rows),
        "eq_pair_closed": all(p in PAIRS for p in by_pair),
        "eq_v3_closed": all(v in VERDICTS3 for v in by_v3),
        "eq_f2p_subset": all(r["pair_verdict"] == "not_raise" for r in f2p),
        "eq_f2p_conditions": all(
            r["passed_fixed"] and not r["passed_buggy"]
            and not r["fixed_unstable"] for r in f2p),
        "eq_caliber_self": "scripts/probe_ctx_downstream.py" in caliber(),
    }
    return {
        "n_rows": len(rows),
        "n_saved_ctx": len(saved_ctx),
        "align_mismatch": mismatch,
        "by_verdict3": dict(sorted(by_v3.items())),
        "by_pair": dict(sorted(by_pair.items())),
        "cross_verdict3_x_pair": dict(sorted(cross.items())),
        "n_f2p_criterion": len(f2p),
        "f2p_by_verdict3": dict(sorted(collections.Counter(
            r["verdict3"] for r in f2p).items())),
        "f2p_shas": sorted({r["sha"] for r in f2p}),
        "n_fixed_unstable": sum(1 for r in rows if r["fixed_unstable"]),
        "by_repo": dict(sorted(collections.Counter(
            r["repo"] for r in rows).items())),
        #: ⛔ 事后 oracle，只记账、不当 predictor（176/179 纪律）
        "reach_of_saved_ctx": dict(sorted(collections.Counter(
            str(x.get("reach")) for x in saved_ctx).items())),
        "n_skipped_commits": len(skipped),
        "baseline_health": {"ok": health.get("ok"),
                            "diffs": len(health.get("diffs", [])),
                            "repos": len(health.get("repos", {}))},
        "closure_eqs": eqs,
        "n_closure_true": sum(1 for v in eqs.values() if v),
        "n_closure_items": len(eqs),
        "note": ("⛔ 两本账分开：176 的三验裁决 verdict3 与本轮两侧运行时 "
                 "pair_verdict 只做交叉表，绝不合并成布尔。f2p_criterion "
                 "只是现有 truthy 形态下的真 F2P **判据条数**，不是收益、"
                 "不是修复；没喂合成层、没改 aether/、没放宽内联器。"
                 "reach 是事后 oracle，只记账不当 predictor。"
                 "⛔ 不许宣布扩张判据形态无用。"),
    }


# ─────────────────────────── 主流程 ───────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--out", default="",
                    help="⛔ 默认不落盘；全量普查须显式给 --out")
    ap.add_argument("--no-baseline-gate", action="store_true",
                    help="⛔ 仅供离线反证；正式普查必须验锚")
    args = ap.parse_args()

    #: 174 规则：先验锚，并把健康度当读数报出来
    health: Dict[str, Any] = {"ok": None, "diffs": [], "repos": {}}
    if args.no_baseline_gate:
        health["diffs"] = ["BYPASS（仅反证）"]
    else:
        health = require_baseline()
        print("baseline_health=OK  repos=%d  diffs=%d" %
              (len(health.get("repos", {})), len(health.get("diffs", []))))

    saved = json.loads(SOURCE.read_text(encoding="utf-8"))
    want = {x for x in args.only.split(",") if x}
    saved_ctx = [r for r in saved["rows"] if r["gate"] == "ctx"
                 and (not want or r["sha"] in want)]

    rows: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for name, subdir in REPOS:
        repo = Path("/tmp/realrepo") / name
        for cand in select_commits(repo, args.limit, subdir):
            sha, src, testfs, _subj = cand
            if want and sha not in want:
                continue
            pkg: Optional[Dict[str, Any]] = None
            if subdir != "boltons/":
                pkg = pkg_context(repo, sha, src,
                                  _git(repo, "show", "%s:%s" % (sha, src)))
            try:
                got = attribute_commit(repo, sha, src, testfs, pkg=pkg,
                                       timeout=args.timeout)
            except Exception as exc:                  #: 单 commit 炸不连坐
                skipped.append({"repo": name, "sha": sha,
                                "reason": "%s: %s" % (type(exc).__name__, exc)})
                continue
            if got is None:
                skipped.append({"repo": name, "sha": sha,
                                "reason": "no fixed/buggy pair"})
                continue

            ctx = [r for r in got["rows"] if r["gate_final"] == "ctx"]
            if not ctx:
                continue
            fixed = _git(repo, "show", "%s:%s" % (sha, src))
            buggy = _git(repo, "show", "%s^:%s" % (sha, src))
            #: ⛔ 不带 expect ⇒ 产品 truthy 分支；判据不是手写 oracle
            tests = [{"name": "t%d" % i, "call": (r.get("call") or "")}
                     for i, r in enumerate(ctx)]
            f1, imp1 = sandbox_read(fixed, tests, args.timeout, pkg)
            f2, imp2 = sandbox_read(fixed, tests, args.timeout, pkg)
            bs, impb = sandbox_read(buggy, tests, args.timeout, pkg)

            for i, r in enumerate(ctx):
                a1, a2, b = f1[i], f2[i], bs[i]
                unstable = (a1["passed"] != a2["passed"]
                            or a1["why"] != a2["why"])
                pv = pair_verdict(a1, b)
                rows.append({
                    "repo": name, "sha": sha,
                    "fn": r.get("fn"),
                    "stmt": (r.get("stmt") or "")[:240],
                    "call": (r.get("call") or "")[:240],
                    "call_len": len(r.get("call") or ""),
                    "attr": r.get("attr"), "kind": r.get("kind"),
                    "verdict3": r.get("verdict"),
                    "pair_verdict": pv,
                    "fixed_unstable": bool(unstable),
                    "f2p_criterion": bool(truthy_f2p(a1, b, unstable)),
                    "passed_fixed": bool(a1["passed"]),
                    "passed_buggy": bool(b["passed"]),
                    "why_fixed": a1["why"], "why_buggy": b["why"],
                    "evidence_fixed": a1["evidence"][:160],
                    "evidence_buggy": b["evidence"][:160],
                    "import_flags": [bool(imp1), bool(imp2), bool(impb)],
                })
            print("  %-16s %s  ctx=%-3d pair=%s" %
                  (name, sha, len(ctx),
                   dict(collections.Counter(
                       x["pair_verdict"] for x in rows
                       if x["sha"] == sha))))

    #: ⛔ 分母同源对拍：产品重跑的 ctx 行必须逐字等于 176 存档
    a = fingerprint([{
        "repo": x["repo"], "sha": x["sha"], "fn": x.get("fn"),
        "stmt": x.get("stmt"), "call": x.get("call"),
        "attr": x.get("attr"), "kind": x.get("kind"),
        "verdict3": x.get("sandbox_verdict")} for x in saved_ctx])
    b = fingerprint(rows)
    diff = mismatch_of(a, b)
    print("align: saved_ctx=%d live_ctx=%d mismatch=%d" %
          (len(saved_ctx), len(rows), diff))
    if diff:
        print("⛔ 分母与 176 存档不同源 ⇒ 拒绝出读数（fail-closed）",
              file=sys.stderr)
        raise SystemExit(2)

    s = summarize(rows, saved_ctx, health, skipped, diff)
    print("\n=== r103 ctx 门后普查 ===")
    print("  verdict3 =", s["by_verdict3"])
    print("  pair     =", s["by_pair"])
    print("  f2p_criterion =", s["n_f2p_criterion"],
          " by_verdict3 =", s["f2p_by_verdict3"])
    print("  fixed_unstable =", s["n_fixed_unstable"])
    print("  closure =", s["n_closure_true"], "/", s["n_closure_items"])
    print("  " + s["note"])

    if args.out:
        out = Path(args.out)
        out.write_text(json.dumps({
            "schema": "ctx_downstream/v1",
            "caliber": caliber(),
            "rows": rows, "skipped": skipped, "summary": s,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n落盘：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
