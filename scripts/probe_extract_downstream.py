#!/usr/bin/env python3
"""**抽取层门下游上界普查** —— README 177。

═══ 它回答什么 ═══

176 把判据侧 F2P 恒 0 收窄成「**当前抽取口径下**为 0」，并留下
**254 条 `downstream=unknown`**：倒在抽取层门（form / unserializable /
tuple / unstable / unmatched）的 PR 新增断言，下游**从未测过**。
⛔ 176 明确写了「没测过 ≠ 测出 0」，并把它列为「扩张判据形态」拍板
所需的最后一块证据。本脚本就是把这块 unknown 变成 measured。

═══ ⛔⛔ 测的是「必要条件上界」，不是收益 ═══

F2P 的定义是「探针在 fixed 上通过、在 buggy 上失败」。无论判据形态
怎么扩张（truthy / ∈ / ≠ / match= / raises），判据总是**对某个表达式
求值，再拿结果做比较**。所以有一条与形态无关的严格必要条件：

    探针表达式在 buggy 与 fixed 两侧求值结果相同
      ⇒ 任何形态的判据都不可能区分两版 ⇒ 永远不可能 F2P

本脚本只测这一条。产出两类读数：

  · `same`  两侧同值 ⇒ **结构性排除**（扩张判据形态也救不回来）
  · `diff`  两侧异值 ⇒ 必要条件成立，是**上界候选**，⛔ 不是收益
            —— 后面还压着三验、判据形态、定位门、形态门（reach=X）、
            合成层。167 已证形态门会把开门收益吞掉。

⛔ 因此**不许**把 `diff` 的条数写成「解开这道门能修好 N 个」。

═══ 分寸：两本账分开，不许合并 ═══

  · **主账 135 条**：176 存档里 `call` 非空（unserializable 122 /
    tuple 6 / unstable 3 / unmatched 4）——产品**自己**抽出的探针表达式，
    只是期望侧没过闸。用同源 `run_all` 在两侧求值，口径与产品一致。
  · **form 门 119 条**：产品压根**没有**为它定义探针表达式
    （`mapped=False`，`call=None`）⇒ 记 `unmeasurable`，**不出沙箱读数**。
    ⛔⛔ 第一版曾拿产品 `_iter_all_probes` 的原始 `expr`
    （`self.assertTrue(stream._rolled)`）跑沙箱，7/7 全
    `no_result_both` —— 那是**构造必然**（原始 expr 依赖测试函数里的
    `self` / 局部变量，没经 `resolve_probe` 加工），不是证据（第 122 条），
    且会重演 175 第一版「不同源尺子 ⇒ 结论反转」。
    ⛔ 这 119 条的硬结论是：**要测下游，必须先扩张判据形态**（⇒ 需拍板）；
    本轮不擅自替产品造探针。

═══ 为什么不会重演 165 的 tuple / JSON 假 F2P ═══

`run_all` 不带 `expect` ⇒ 沙箱走 truthy 分支，返回的是 `got` 的
**repr 字符串**，比的是两侧 repr 是否相同，**不做 JSON 往返、不与
expect 比较** ⇒ 165 的「tuple 降级成 list 恒失败」不适用。
⛔ `repr` 含内存地址会造**假 diff** ⇒ `addr_noise` 单独剔除。
⛔ fixed 侧跑两次做稳定性自检（172 的时间参数化教训）。

═══ 已知局限（保留未知，不许读成否定）═══

`got=None` 有两种含义：探针求不出值（上下文门 / 装载失败）**或**
抛了异常。两侧都 `None` 一律记 `no_result_both` 并标 `unknown`，
⛔ 不许读成「结构性排除」——`raises` 形态的判据靠异常类型区分，
本尺子看不见。这正是「缺失不得被读作否定」。

用法：
    python scripts/probe_extract_downstream.py --only 4e5faa3,ec1ff9f
    python scripts/probe_extract_downstream.py --out scripts/extract_downstream.json
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

import aether.synthesize as S                                # noqa: E402
from aether.extract import _iter_all_probes                    # noqa: E402
from probe_alias_demote import (                               # noqa: E402
    select_commits, run_all, _git)
from probe_auto_unlock import changed_lines                   # noqa: E402
from probe_package_load import pkg_context                    # noqa: E402
from realrepo_baseline import require_baseline               # noqa: E402

REPOS: Tuple[Tuple[str, str], ...] = (
    ("boltons", "boltons/"),
    ("more-itertools", "more_itertools/"),
    ("attrs", "src/attr/"),
)
SOURCE_CENSUS = ROOT / "scripts" / "criteria_gate_census.json"
FOCUS_CENSUS = ROOT / "scripts" / "focus_census.json"

#: ⓪ 尺子本身：改了其中任何一个，存档读数就过期（174 的锚只钉了
#: select_commits，这里补钉探针求值/抽取链路）
CALIBER_FILES = (
    "aether/extract.py",
    "aether/environment.py",
    "scripts/probe_alias_demote.py",
    "scripts/probe_context_gate.py",
    "scripts/probe_package_load.py",
    "scripts/probe_criteria_gate_census.py",
)

#: 主账判据封闭集合（⛔ 不许新增格来迁就读数）；form 账专用
#: `unmeasurable`，⛔ 不与主账合并成一张分布表。
VERDICTS = ("same", "diff", "no_result_both", "unstable", "import_failed")
MAIN_GATES = ("unserializable", "tuple", "unstable", "unmatched")

#: `repr` 里的内存地址 ⇒ 假 diff（同一对象两次 repr 地址可能不同）
ADDR_RE = re.compile(r"0x[0-9a-fA-F]{6,}|<[^>]*\bat\s+0x")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {f: sha256_file(ROOT / f) for f in CALIBER_FILES}


def caliber_matches(saved: Dict[str, str],
                    disk: Dict[str, str]) -> List[str]:
    """⛔ 纯函数（供契约离线反证）：⛔ 缺键也算差异，不许默认值兜底。"""
    return sorted(f for f in disk if saved.get(f) != disk[f])


def addr_noise(*vals: Optional[str]) -> bool:
    """⛔ 纯函数：repr 含内存地址 ⇒ 该行不可用于两侧比较。"""
    return any(bool(v) and bool(ADDR_RE.search(v)) for v in vals)


def truthy_f2p(a1: Optional[Dict[str, Any]],
               b: Optional[Dict[str, Any]]) -> bool:
    """⛔ 纯函数：现有 **truthy 判据形态**能不能直接把这条探针变成 F2P。

    `run_all` 不带 `expect` ⇒ 沙箱走 truthy 分支，`passed = bool(got)`。
    所以 `passed_fixed and not passed_buggy` 就是「解开 truthy 门」
    的**真 F2P**（不是必要条件、不是收益推断）。
    ⛔ 但 truthy 只看真假，看不出值本身 ⇒ 它只是 `diff` 的一个子集。
    ⛔ 不许拿 `got` 的 repr 字符串去推 bool（`"None"` 恒真）。
    """
    #: ⛔ 缺结果一律 fail-closed：契约 B9f 抓到过 `a1=None` 时
    #: `not bool(b.passed)` 恰好成立 ⇒ 把「没跑到」写成了 F2P。
    if not a1 or not b:
        return False
    return bool(a1.get("passed")) and not bool(b.get("passed"))


def classify(a1: Optional[Dict[str, Any]],
             a2: Optional[Dict[str, Any]],
             b: Optional[Dict[str, Any]]) -> str:
    """⛔⛔ **核心判定，纯函数**，供契约 B 组离线反证。

    `a1/a2` = fixed 侧两次求值（稳定性自检），`b` = buggy 侧一次。
    每项 `{"got": str|None, "passed": bool}` 或 None（缺结果）。

    判定顺序**不许改**：先剔地址噪声/不稳定，再判双空保留未知，
    再判同值排除，最后才轮到 diff。改顺序会让 `no_result_both`
    被吞进 `same`，把「没测到」写成「不可能」（契约 ③/⑤）。
    """
    g1 = (a1 or {}).get("got")
    g2 = (a2 or {}).get("got")
    gb = (b or {}).get("got")
    if addr_noise(g1, g2, gb):
        return "unstable"
    if g1 != g2:                     #: fixed 自身两次不一致 ⇒ 不可复现
        return "unstable"
    if g1 is None and gb is None:    #: ⛔ 双空 = 未知，不是排除
        return "no_result_both"
    if g1 == gb:                     #: 严格必要条件不成立
        return "same"
    return "diff"


def probe_index(repo: Path, sha: str, testfs: List[str]) -> Dict[
        Tuple[str, str], Dict[str, Any]]:
    """用产品自己的 `_iter_all_probes` 建 `(fn, stmt) -> probe` 索引。

    ⛔ 只为 form 门的**诊断账**服务；主账一律用 176 存档的产品 `call`。
    """
    idx: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for tf in testfs:
        tsrc = _git(repo, "show", "%s:%s" % (sha, tf))
        if not tsrc:
            continue
        try:
            probes = _iter_all_probes(tsrc)
        except SyntaxError:
            continue
        for q in probes:
            idx[(q["fn"], q["stmt"])] = q
    return idx


def truthy_synth_one(repo: Path, sha: str, src: str,
                     calls: List[str], timeout: float,
                     pkg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """⛔ **诊断读数，不算修复**（与 175 的 --forced-focus 同类）。

    把「解开 truthy 门」得到的判据（`{"name","call"}`，⛔ 不带 expect）
    真喂进产品 `synthesize_patch`，判 161 口径的四格。
    ⛔ 判据来自产品自己抽出的 `call` + 现有 truthy 形态，**不是手写 oracle**
    ⇒ 不触发 171 的 `oracle_bad` 闸（那条只管人工转写的判据）。
    ⛔ `reach` 是事后 oracle，不能当 predictor；本函数只看实测。
    """
    buggy = _git(repo, "show", "%s^:%s" % (sha, src))
    fixed = _git(repo, "show", "%s:%s" % (sha, src))
    if not buggy or not fixed:
        return {"sha": sha, "verdict": "no_src", "diagnostic_only": True}
    tests = [{"name": "t%d" % i, "call": c}
             for i, c in enumerate(sorted(set(calls)))]
    res = S.synthesize_patch(buggy, tests, timeout=timeout, pkg=pkg)
    v = "no_fix"
    patch = res.patch
    h = {(t, l) for t, l in changed_lines(buggy, fixed)}
    m = {(t, l) for t, l in changed_lines(buggy, patch)} if patch else set()
    if res.found and patch:
        if patch == fixed:
            v = "exact_file"
        else:
            v = "same_core_fix" if m and m <= h else "elsewhere"
    return {"sha": sha, "n_tests": len(tests), "calls": calls,
            "gate": res.gate, "found": res.found,
            "full_pass": res.full_pass, "base_ok": res.base_ok,
            "new_ok": res.new_ok, "n_cands": res.n_cands,
            "n_ran": res.n_ran, "focus": list(res.focus),
            "verdict": v, "n_human_changes": len(h),
            "n_machine_changes": len(m),
            "diagnostic_only": True}


def summarize(rows: List[Dict[str, Any]], form_rows: List[Dict[str, Any]],
              health: Dict[str, Any],
              skipped: List[Dict[str, Any]]) -> Dict[str, Any]:
    """纯函数：所有结论都从 rows 派生，不藏进程内状态。"""
    by_gate = collections.Counter(r["gate"] for r in rows)
    verdicts = collections.Counter(r["verdict"] for r in rows)
    cross = collections.Counter((r["gate"], r["verdict"]) for r in rows)
    #: diff 落在 175 的哪格 / reach 是什么（⛔ 形态门还在下游等着）
    diff_v175 = collections.Counter(
        r.get("v175") for r in rows if r["verdict"] == "diff")
    diff_reach = collections.Counter(
        r.get("reach") for r in rows if r["verdict"] == "diff")
    #: 「解开 truthy 门」对照：现有形态能直接变 F2P 的条数 + 落点
    tf = [r for r in rows if r.get("truthy_f2p")]
    return {
        "n_main": len(rows),
        "n_form_diagnostic": len(form_rows),
        "n_commit_groups": len({(r["repo"], r["sha"]) for r in rows}),
        "by_gate": dict(by_gate),
        "verdicts": dict(verdicts),
        "cross": {"%s/%s" % k: v for k, v in sorted(cross.items())},
        "diff_by_v175": dict(diff_v175),
        "diff_by_reach": dict(diff_reach),
        "n_truthy_f2p": len(tf),
        "truthy_f2p_shas": sorted({r["sha"] for r in tf}),
        "truthy_f2p_by_reach": dict(collections.Counter(
            r.get("reach") for r in tf)),
        "form_diagnostic_verdicts": dict(collections.Counter(
            r["verdict"] for r in form_rows)),
        "form_has_raw_expr": sum(1 for r in form_rows
                                 if r.get("has_raw_expr")),
        "n_skipped": len(skipped),
        "skipped": skipped,
        "baseline_health": {
            "ok": bool(health.get("ok")),
            "diffs": len(health.get("diffs", [])),
            "repos": len(health.get("repos", {})),
        },
        "two_books_note": (
            "主账 = 产品已抽出 call 的行（真跑沙箱）；form 账 = 产品未定义"
            "探针表达式 ⇒ 一律 unmeasurable、不跑沙箱、不参与收益推断。"
            "两本账不许合并成一张分布表。"),
        "unknown_note": (
            "no_result_both 含『两侧都抛异常』，raises 形态看不见 ⇒ 保留未知，"
            "不参与排除/收益；diff 只是必要条件上界，不是收益。"
            "form 的 119 条下游仍需先扩张判据形态才能测（需拍板）。"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--out", default="")
    ap.add_argument("--truthy-synth", action="store_true",
                    help="⛔ 诊断：把 truthy F2P 判据真喂进合成层")
    ap.add_argument("--synth-out", default="")
    ap.add_argument("--no-baseline-gate", action="store_true",
                    help="⛔ 仅供离线反证；正式普查必须验锚")
    args = ap.parse_args()

    #: 174 规则：先验锚，并把健康度当读数报出来（第三次实战）
    health: Dict[str, Any] = {"ok": None, "diffs": [], "repos": {}}
    if args.no_baseline_gate:
        health["diffs"] = ["BYPASS（仅反证）"]
    else:
        health = require_baseline()
        print("baseline_health=OK  repos=%d  diffs=%d" %
              (len(health.get("repos", {})), len(health.get("diffs", []))))

    src = json.loads(SOURCE_CENSUS.read_text(encoding="utf-8"))
    unknown = [r for r in src["rows"] if r["downstream"] == "unknown"]
    focus = {(r["repo"], r["sha"]): r for r in json.loads(
        FOCUS_CENSUS.read_text(encoding="utf-8"))["rows"]}
    want = {x for x in args.only.split(",") if x}

    #: 按 (repo, sha) 分组 ⇒ 一个 commit 只装载/求值一次
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = collections.OrderedDict()
    for r in unknown:
        if want and r["sha"] not in want:
            continue
        groups.setdefault((r["repo"], r["sha"]), []).append(r)

    cands: Dict[str, List[Tuple[Any, ...]]] = {}
    subdirs = dict(REPOS)
    for name, subdir in REPOS:
        cands[name] = select_commits(Path("/tmp/realrepo") / name,
                                     args.limit, subdir)

    rows: List[Dict[str, Any]] = []
    form_rows: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for (name, sha), grp in groups.items():
        cand = next((c for c in cands[name] if c[0] == sha), None)
        if cand is None:
            skipped.append({"repo": name, "sha": sha,
                            "reason": "not in select_commits"})
            continue
        _, src_path, testfs, _ = cand
        repo = Path("/tmp/realrepo") / name
        fixed = _git(repo, "show", "%s:%s" % (sha, src_path))
        buggy = _git(repo, "show", "%s^:%s" % (sha, src_path))
        if not fixed or not buggy:
            skipped.append({"repo": name, "sha": sha,
                            "reason": "no fixed/buggy pair"})
            continue
        pkg = None
        if subdirs[name] != "boltons/":
            pkg = pkg_context(repo, sha, src_path, fixed)

        idx = probe_index(repo, sha, testfs)
        f = focus.get((name, sha), {})

        #: ⛔ 只有「产品已抽出 call」的行才进沙箱（主账）。
        #: form 门无产品探针 ⇒ 只记 unmeasurable，⛔ 绝不拿原始 expr
        #: 出读数（构造必然，见 docstring）。
        items: List[Tuple[str, str, None]] = []
        meta: List[Tuple[str, Dict[str, Any], str]] = []
        for i, r in enumerate(grp):
            call = (r.get("call") or "").strip()
            if call:
                items.append(("m%d" % i, call, None))
                meta.append(("main", r, call))
                continue
            q = idx.get((r["fn"], r["stmt"]))
            form_rows.append({
                **r, "verdict": "unmeasurable",
                "expr": (q or {}).get("expr") or "",
                "has_raw_expr": bool(q),
                "v175": f.get("verdict"), "reach": f.get("reach"),
                "different_caliber": True, "diagnostic_only": True,
                "reason": "product defines no probe expression "
                          "(mapped=False, call=None)",
            })

        if items:
            try:
                r1 = run_all(fixed, items, timeout=args.timeout, pkg=pkg)
                r2 = run_all(fixed, items, timeout=args.timeout, pkg=pkg)
                rb = run_all(buggy, items, timeout=args.timeout, pkg=pkg)
            except Exception as exc:               # 单 commit 炸不连坐
                skipped.append({"repo": name, "sha": sha,
                                "reason": "%s: %s" % (type(exc).__name__, exc)})
                continue
            broke = any("__import_error__" in (x or {})
                        for x in (r1, r2, rb))
        else:
            r1 = r2 = rb = {}
            broke = False

        for (key, _, _), (_book, r, expr) in zip(items, meta):
            v = "import_failed" if broke else classify(
                r1.get(key), r2.get(key), rb.get(key))
            rows.append({**r, "verdict": v, "expr": expr,
                         "v175": f.get("verdict"), "reach": f.get("reach"),
                         "different_caliber": False,
                         "diagnostic_only": False,
                         "got_fixed": (r1.get(key) or {}).get("got"),
                         "got_buggy": (rb.get(key) or {}).get("got"),
                         "passed_fixed": (r1.get(key) or {}).get("passed"),
                         "passed_buggy": (rb.get(key) or {}).get("passed"),
                         "truthy_f2p": (False if broke else truthy_f2p(
                             r1.get(key), rb.get(key)))})

        vc = collections.Counter(
            x["verdict"] for x in rows
            if x["repo"] == name and x["sha"] == sha)
        print("  %-16s %s  n=%-3d main=%-3d form=%-3d %s" %
              (name, sha, len(grp),
               sum(1 for x in rows if x["repo"] == name and x["sha"] == sha),
               sum(1 for x in form_rows
                   if x["repo"] == name and x["sha"] == sha),
               dict(vc)))

    #: ⛔ 诊断对照：把 truthy F2P 判据真喂进合成层（175 同类，不算修复）
    synth_rows: List[Dict[str, Any]] = []
    if args.truthy_synth:
        by_commit: Dict[Tuple[str, str], List[str]] = collections.OrderedDict()
        for r in rows:
            if r.get("truthy_f2p"):
                by_commit.setdefault((r["repo"], r["sha"]), []).append(r["call"])
        print("\n═══ truthy 合成层诊断（%d commit）═══" % len(by_commit))
        for (name, sha), calls in by_commit.items():
            cand = next((c for c in cands[name] if c[0] == sha), None)
            if cand is None:
                continue
            _, src_path, _, _ = cand
            repo = Path("/tmp/realrepo") / name
            fx = _git(repo, "show", "%s:%s" % (sha, src_path))
            pk = (pkg_context(repo, sha, src_path, fx)
                  if subdirs[name] != "boltons/" else None)
            out = truthy_synth_one(repo, sha, src_path, calls,
                                   args.timeout, pk)
            out["repo"] = name
            synth_rows.append(out)
            print("  %-16s %s  n=%-2d %-14s gate=%-12s found=%s "
                  "full_pass=%s cands=%d ran=%d" %
                  (name, sha, out.get("n_tests", 0), out["verdict"],
                   out.get("gate"), out.get("found"),
                   out.get("full_pass"), out.get("n_cands", 0),
                   out.get("n_ran", 0)))
        print("  四格 =", dict(collections.Counter(
            r["verdict"] for r in synth_rows)))
        if args.synth_out:
            Path(args.synth_out).write_text(json.dumps({
                "schema": "truthy_synth/v1", "caliber": caliber(),
                "rows": synth_rows,
                "summary": {"verdicts": dict(collections.Counter(
                    r["verdict"] for r in synth_rows)),
                    "diagnostic_only": True,
                    "note": "解开 truthy 门的诊断读数，不是自动端到端修复"},
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            print("  落盘 →", args.synth_out)

    summary = summarize(rows, form_rows, health, skipped)
    print("\n═══ 主账（产品 call，n=%d）═══" % summary["n_main"])
    print("  by_gate   =", summary["by_gate"])
    print("  verdicts  =", summary["verdicts"])
    print("  cross     =", summary["cross"])
    print("  diff@v175 =", summary["diff_by_v175"])
    print("  diff@reach=", summary["diff_by_reach"])
    print("  ⛔ 解开 truthy 门后真 F2P = %d 条，落点 %s，sha=%s" %
          (summary["n_truthy_f2p"], summary["truthy_f2p_by_reach"],
           summary["truthy_f2p_shas"]))
    print("═══ form 账（产品无探针，n=%d；只记不可测，不跑沙箱）═══" %
          summary["n_form_diagnostic"])
    print(" ", summary["form_diagnostic_verdicts"],
          " 有原始 expr 的=", summary["form_has_raw_expr"])
    print("  skipped=%d  baseline=%s" %
          (summary["n_skipped"], summary["baseline_health"]))

    if args.out:
        payload = {
            "schema": "extract_downstream/v1",
            "caliber": caliber(),
            "rows": rows,
            "form_diagnostic_rows": form_rows,
            "summary": summary,
        }
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("  落盘 →", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
