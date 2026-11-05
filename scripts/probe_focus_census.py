#!/usr/bin/env python3
"""**定位门量级普查**：在全自动抽取路径上，逐 commit 判「改动单元 ∈ focus？」。

═══ 为什么不是把 173 的 6 例再数一遍 ═══

173 用**手写 oracle 判据**发现一道独立的「定位门」（D / F：focus 非空但
改动单元在射程外）。但那是上界实验，不是产品路径。本脚本回答：

    全自动路径（`auto_extract` 判据 + `synthesize_patch` 定位）下，
    这道门到底有多宽？以及「修 locate_focus」值不值？

⛔⛔ 同源纪律（本轮实测踩中）：**不许自己拼 `texts` 再调 `locate_focus`。**
   产品 `synthesize_patch` 的 texts 是 `f"{name} {call}"` 且**只取 F2P 判据**
   （`synthesize.py:359`）。第一版尺子用「全部判据 + 无空格拼接」⇒
   给 `93185b2` 算出 `focus=['daterange']`、`d992be0` 算出
   `['Stats','running_statistics']` —— 全是假的；产品实际两者都是 **`[]`
   ⇒ `S1_no_focus`**。若照那版出读数，结论会完全相反。

⇒ 做法：把 `synth_candidates` 临时替身成空，直接调**产品自己的**
   `synthesize_patch` 取 `res.focus`（跳过昂贵候选跑分，其余路径一字不改）。
   替身版与正常版的 focus 已在 3 例上逐字对拍（见契约 B0）。

═══ 判定顺序（互斥、覆盖）═══

    no_src           buggy/fixed 读不到
    no_criteria      自动抽取一条判据都没有 ⇒ focus 无从谈起
    blew             基线在沙箱里跑挂（S0）
    no_f2p           判据全过 ⇒ texts 为空 ⇒ focus 必空（判据鉴别力门）
    f2p_but_no_focus 有 F2P 但标识符映射不到顶层 def/class（**定位没开工**）
    no_units         `changed_units` 返回空（纯 INSERT）⇒ 射程无从判定
    out_of_reach     focus 非空、units 非空、但不相交（**173 定义的定位门**）
    in_reach         至少一个改动单元 ∈ focus

⛔ 把 `no_f2p` 与 `f2p_but_no_focus` 分开是这轮的关键：前者该修判据，
   后者才该修定位。合并成一个「focus 空」会得出「扩射程没用」或
   「扩射程能救一大片」两种相反但都错的结论。

用法：
    python scripts/probe_focus_census.py --only 93185b2,c1c25da,d992be0
    python scripts/probe_focus_census.py --out scripts/focus_census.json
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aether import synthesize as S                       # noqa: E402
from probe_alias_demote import select_commits            # noqa: E402
from probe_auto_unlock import (auto_extract, pkg_context,   # noqa: E402
                               changed_lines)
from probe_focus_reach import changed_units              # noqa: E402
from realrepo_baseline import require_baseline           # noqa: E402

REPOS = (
    ("boltons", "boltons/", "census_boltons_40.json"),
    ("more-itertools", "more_itertools/", "census_mi_40.json"),
    ("attrs", "src/attr/", "census_attrs_40.json"),
)

VERDICTS = ("no_src", "no_criteria", "blew", "no_f2p",
            "f2p_but_no_focus", "no_units", "out_of_reach", "in_reach")


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(repo),
                       capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


@contextmanager
def no_candidates():
    """只替身「候选枚举」，让产品的 `synthesize_patch` 自己算 focus。

    ⛔ 不替身 `locate_focus` / `score_source`：那两处正是被测对象，
      替了就等于另写一份判据（skill 契约⑥）。
    """
    orig = S.synth_candidates
    S.synth_candidates = lambda *a, **k: []
    try:
        yield
    finally:
        S.synth_candidates = orig


def classify(res, units: list, k: int) -> tuple:
    """判定顺序（互斥、覆盖）。**抽成纯函数**是为了契约能离线反证。

    ⛔ `no_f2p` 必须排在 `f2p_but_no_focus` 前面：F2P=0 时产品的 texts
       本来就是空的，focus 空是**判据鉴别力**的结果，不是定位失败。
       反过来说，把这两格合并会同时得出「扩射程没用」和「扩射程能救
       一大片」两种相反但都错的结论。
    """
    n_f2p = k - res.base_ok if res.base_ok >= 0 else -1
    if res.n_blew:
        v = "blew"
    elif n_f2p == 0:
        v = "no_f2p"
    elif not res.focus:
        v = "f2p_but_no_focus"
    elif not units:
        v = "no_units"
    elif set(units) & set(res.focus):
        v = "in_reach"
    else:
        v = "out_of_reach"
    return v, n_f2p


def run_one(name: str, subdir: str, cand: tuple, census: dict,
            timeout: float) -> dict:
    repo = Path("/tmp/realrepo") / name
    sha, src, testfs, _subj = cand
    cref = census.get(sha, {})
    row = {"repo": name, "sha": sha, "src": src,
           "in_census": sha in census,
           "reach": cref.get("reach"),
           "census_stage": cref.get("stage")}
    buggy = git(repo, "show", sha + "^:" + src)
    fixed = git(repo, "show", sha + ":" + src)
    if not buggy or not fixed or buggy == fixed:
        row["verdict"] = "no_src"
        return row

    #: ⛔ 包内模块必须整包装载，否则 import-failed ⇒ 读数全 0（README 164）
    pkg = pkg_context(repo, sha, src, fixed) if subdir != "boltons/" else None
    tests, _stx = auto_extract(repo, sha, src, testfs, pkg=pkg)
    row["k"] = len(tests)
    if not tests:
        row["verdict"] = "no_criteria"
        return row

    with no_candidates():
        res = S.synthesize_patch(buggy, tests, timeout=timeout, pkg=pkg)
    focus = list(res.focus)
    units = changed_units(buggy, fixed)
    v, n_f2p = classify(res, units, len(tests))
    row.update({"gate": res.gate, "base_ok": res.base_ok,
                "n_f2p": n_f2p, "focus": focus, "units": units,
                "in_reach": bool(set(units) & set(focus)),
                "verdict": v})
    return row


def forced_gain(name: str, subdir: str, cand: tuple,
                timeout: float) -> dict:
    """**诊断读数**：把 focus 强制设成改动单元，看合成层能不能修好。

    ⛔ 这不是产品路径、不算修复（173 第六节）：等于告诉合成层去哪儿找。
       用途只有一个 —— 回答「定位门开到位后，收益会不会被下一道门吞掉」。
    ⛔ 替身的是 `locate_focus`（定位），候选枚举与跑分仍走产品原样。
    """
    repo = Path("/tmp/realrepo") / name
    sha, src, testfs, _subj = cand
    buggy = git(repo, "show", sha + "^:" + src)
    fixed = git(repo, "show", sha + ":" + src)
    if not buggy or not fixed or buggy == fixed:
        return {"repo": name, "sha": sha, "stage": "no_src"}
    pkg = pkg_context(repo, sha, src, fixed) if subdir != "boltons/" else None
    tests, _stx = auto_extract(repo, sha, src, testfs, pkg=pkg)
    if not tests:
        return {"repo": name, "sha": sha, "stage": "no_criteria"}
    units = changed_units(buggy, fixed)
    if not units:
        return {"repo": name, "sha": sha, "stage": "no_units"}

    auto = S.synthesize_patch(buggy, tests, timeout=timeout, pkg=pkg)
    orig = S.locate_focus
    S.locate_focus = lambda _src, _texts: list(units)
    try:
        forced = S.synthesize_patch(buggy, tests, timeout=timeout, pkg=pkg)
    finally:
        S.locate_focus = orig

    #: ⛔ 与 census 同源（161/170 口径）：full_pass 只是**必要条件**，
    #:   还得判机器改动是不是人类改动的子集，否则是 elsewhere（绕开）。
    verdict = "no_fix"
    patch = forced.patch
    h_lines = changed_lines(buggy, fixed)
    m_lines = changed_lines(buggy, patch) if patch else []
    if forced.found and patch:
        if patch == fixed:
            verdict = "exact_file"
        else:
            h = {(t, l) for t, l in h_lines}
            m = {(t, l) for t, l in m_lines}
            verdict = "same_core_fix" if m and m <= h else "elsewhere"
    return {"repo": name, "sha": sha, "stage": "measured",
            "auto_focus": list(auto.focus), "units": units,
            "auto_gate": auto.gate, "auto_found": auto.found,
            "auto_full_pass": auto.full_pass,
            "forced_gate": forced.gate, "forced_found": forced.found,
            "forced_full_pass": forced.full_pass,
            "forced_n_cands": forced.n_cands, "forced_n_ran": forced.n_ran,
            "exact_file": verdict == "exact_file",
            "forced_verdict": verdict,
            "n_human_changes": len(h_lines),
            "n_machine_changes": len(m_lines),
            "diagnostic_only": True}


def summarize(rows: list) -> dict:
    """两本账分开报：全部 120 个候选 vs 与 census 对得上的 119 个读数。"""
    def dist(rs):
        c = collections.Counter(r["verdict"] for r in rs)
        return {v: c.get(v, 0) for v in VERDICTS}

    out = {"all": dist(rows),
           "in_census_only": dist([r for r in rows if r["in_census"]]),
           "by_repo": {}, "by_reach": {}}
    for name in [x[0] for x in REPOS]:
        out["by_repo"][name] = dist([r for r in rows if r["repo"] == name])
    for reach in ("X", "L2", "L3", None):
        key = str(reach)
        out["by_reach"][key] = dist(
            [r for r in rows if r.get("reach") == reach])

    determ = [r for r in rows if r["verdict"] in ("in_reach", "out_of_reach")]
    out["determinable"] = len(determ)
    out["out_of_reach_ratio"] = (
        round(sum(1 for r in determ if r["verdict"] == "out_of_reach")
              / len(determ), 4) if determ else None)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default="", help="只跑这些 sha（逗号分隔）")
    ap.add_argument("--repo", default="", help="只跑某个仓库")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--out", default="", help="读数落盘路径")
    ap.add_argument("--no-baseline-gate", action="store_true",
                    help="⛔ 仅供离线反证；正式普查必须验锚")
    ap.add_argument("--forced-focus", action="store_true",
                    help="诊断模式：强制 focus=改动单元（不算修复）")
    ap.add_argument("--forced-out", default="",
                    help="强制 focus 对照读数落盘路径")
    args = ap.parse_args()

    #: ⛔ 174 规则：每次普查先验锚，不健康 ⇒ 拒绝出读数（fail-closed）。
    if not args.no_baseline_gate:
        require_baseline()

    want = set(x for x in args.only.split(",") if x)
    rows = []
    forced_rows = []
    for name, subdir, cf in REPOS:
        if args.repo and args.repo != name:
            continue
        repo = Path("/tmp/realrepo") / name
        census = {r["sha"]: r for r in
                  json.loads((ROOT / "scripts" / cf).read_text(
                      encoding="utf-8"))}
        for cand in select_commits(repo, args.limit, subdir):
            if want and cand[0] not in want:
                continue
            if args.forced_focus:
                row = forced_gain(name, subdir, cand, args.timeout)
                forced_rows.append(row)
                print("  %-16s %s  %-10s auto=%s/%s forced=%s/%s "
                      "full_pass=%s verdict=%s human=%s machine=%s"
                      % (name, row["sha"], row["stage"],
                         row.get("auto_gate"), row.get("auto_found"),
                         row.get("forced_gate"), row.get("forced_found"),
                         row.get("forced_full_pass"),
                         row.get("forced_verdict"),
                         row.get("n_human_changes"),
                         row.get("n_machine_changes")))
                continue
            row = run_one(name, subdir, cand, census, args.timeout)
            rows.append(row)
            print("  %-16s %s  k=%-4s %-18s focus=%-24s units=%s"
                  % (name, row["sha"], row.get("k", "-"),
                     row["verdict"], str(row.get("focus", ""))[:24],
                     str(row.get("units", ""))[:32]))

    if args.forced_focus:
        out = {"schema": "focus-forced-gain/v1",
               "note": "⛔ 强制 focus 是诊断读数，绕过 locate_focus，不算修复",
               "rows": forced_rows}
        p = Path(args.forced_out or "scripts/focus_forced_gain.json")
        p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
        print("\n对照读数落盘: %s" % p)
        return 0

    rep = {"schema": "focus-census/v1", "rows": rows,
           "summary": summarize(rows)}
    print("\n════ 门分布（全部候选）════")
    for v, n in rep["summary"]["all"].items():
        print("  %-18s %d" % (v, n))
    print("可判定 in/out of reach = %d，其中射程外占比 = %s"
          % (rep["summary"]["determinable"],
             rep["summary"]["out_of_reach_ratio"]))
    print("\n════ 按 reach 分层 ════")
    for reach, d in rep["summary"]["by_reach"].items():
        print("  %-5s %s" % (reach, {k: v for k, v in d.items() if v}))
    if args.out:
        p = Path(args.out)
        p.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
        print("\n读数落盘: %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
