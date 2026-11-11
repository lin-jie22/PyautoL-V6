#!/usr/bin/env python3
"""r104 / README 183 —— `import_error` 污染面全量普查：176 的 `unmatched` 是伪格。

═══ 要填的洞 ═══

176（README 7949）把 459 条 PR 新增断言逐条归到门，其中 `unmatched 4` 的
注释是「判据在新增集合里，但三验没给它裁决 ⇒ 说明两边**对不上**」
（`scripts/probe_gate_attribution.py:162`）。177 把这 4 条算进主账 135 条，
180 归因了其中 9 条 `import_failed` 的运行时成因，但**没人查过 176 的
`per_commit.extract_stats` 里本来就存着 `import_error`**。

实测：120 个 commit 里 **8 个 `import_error=True`**，`auto_extract` 直接
返回空 `tests` / 空 `detail` ⇒ 三验**整体没跑**，不是「键对不上」。
⛔ 这 4 条 `unmatched` 100% 落在这 8 个 commit 里，其余 112 个 commit
**0 条 unmatched** ⇒ `unmatched` 是装载失败的派生格，不是独立门。

═══ ⛔ 本脚本做什么、不做什么 ═══

✅ **只做一件事**：以 176 存档自身为主账（同源、纯离线），把
   `import_error` 的 commit 级读数与行级污染面量出来，并用产品
   `run_in_sandbox` 给 8 个 commit 取**异常末行**做封闭归因。
✅ **两本账分开**（181 教训）：commit 级 `import_error`（8）与 180 的
   行级 `import_failed`（9 条 / 6 sha）只做交叉表，绝不合并成布尔。
   `n_tests_total == 0` 是**独立第三本账**：30 个 commit 为 0，只有 8 个
   是 `import_error` ⇒ 不许拿它反推。
✅ **控制探针**：`{"name":"t0","call":"1"}` 只为触发装载、取
   `ex.import_error` 末行；⛔ 不产出判据、不参与 F2P、不手写 oracle。
⛔ **不改 `aether/` 一个字节**、不改 176 的归源代码（本轮只报不修）、
   不扩张判据形态、不放宽内联器（④）、**不放宽/降级 deny-modules（⑤）**。
⛔ **只报归因与账目，不报收益**：8/8 是安全边界正确拒收或题源年代错配；
   ⛔ 没跑「放宽 deny 对照」⇒ 不许宣称放宽后能测几条（180 纪律）。
⛔ **不推翻** 177 `unmeasurable`、179 的 119 条 form 下游、182 的 truthy 0。
⛔ **不另写第二把尺子**（175 教训）：分母、行数、门全部取自 176 存档；
   分类只看末行的固定前缀，不解析 evidence 文案猜（178/180 教训）。

═══ 上一轮候选方向的纠偏（写进 README 183）═══

182 收口时曾把「补 179 那 119 条 form 的下游」列为不需拍板的候选。
⛔ **这是判断偏差**：`probe_extract_downstream.py` 开头明确写着，第一版
曾用产品 `_iter_all_probes` 的原始 `expr` 跑沙箱，7/7 全 `no_result_both`
是**构造必然**（原始 expr 依赖测试函数里的 `self` / 局部变量，没经
`resolve_probe` 加工），不是证据（第 122 条），且会重演 175「不同源尺子
⇒ 结论反转」；硬结论是「要测这 119 条下游必须先扩张判据形态 ⇒ 拍板 ①」。
⇒ 179 的 119 条 form 下游**属拍板项 ①，不是纯取证空白**。

═══ 用法 ═══
    python scripts/probe_import_error_census.py --out /tmp/r104_sample.json
    python scripts/probe_import_error_census.py --out scripts/import_error_census.json
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

from aether.environment import run_in_sandbox                    # noqa: E402
from probe_alias_demote import select_commits, _git             # noqa: E402
from probe_package_load import pkg_context                      # noqa: E402
from realrepo_baseline import require_baseline                  # noqa: E402

CENSUS = ROOT / "scripts" / "criteria_gate_census.json"        # 176
ATTR180 = ROOT / "scripts" / "import_failure_attribution.json"  # 180
DOWN177 = ROOT / "scripts" / "extract_downstream.json"          # 177

REPOS: Tuple[Tuple[str, str], ...] = (
    ("boltons", "boltons/"),
    ("more-itertools", "more_itertools/"),
    ("attrs", "src/attr/"),
)

#: ⛔ 封闭集合：不许为了迁就读数新增格（166 纪律）。
CATS: Tuple[str, ...] = (
    "deny_ctypes", "deny_fcntl", "deny_other", "stdlib_mismatch", "other")
IE_ROW_GATES: Tuple[str, ...] = ("form", "unserializable", "unmatched")

CALIBER_FILES = (
    "aether/extract.py",
    "aether/environment.py",
    "scripts/probe_gate_attribution.py",
    "scripts/probe_criteria_gate_census.py",
    "scripts/probe_alias_demote.py",
    "scripts/probe_package_load.py",
    "scripts/probe_extract_downstream.py",
    "scripts/probe_import_failure_attribution.py",
    "scripts/criteria_gate_census.json",
    "scripts/import_failure_attribution.json",
    "scripts/extract_downstream.json",
    "scripts/realrepo_baseline.json",
    "scripts/probe_import_error_census.py",   #: ⛔ 钉尺子自身（174 教训）
)

#: 只认像真用户名的那一段；⛔ 脱敏必须让 portability A1 零命中（181 假绿教训）。
_HOME_PAT = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/")
_TMP_PAT = re.compile(r"/var/folders/[^/\s]+/[^/\s]+/[^/\s()\"']+")
#: 与 180 存档 `evidence_tail_*` 逐字同形：压掉 /Users/…/ 到 /lib/python 之间的中间段。
_LIB_PAT = re.compile(r"/Users/…/[^\s)]*?(?=/lib/python)")


# ─────────────────────────── 纯函数（供契约离线反证） ───────────────────────────
def sha256_file(path: Path) -> str:
    if not path.exists():
        return "__MISSING__"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {f: sha256_file(ROOT / f) for f in CALIBER_FILES}


def redact(text: str) -> str:
    """把真实家目录/临时路径换成判据允许的省略号占位（180 同款）。"""
    def home(m: "re.Match") -> str:
        prefix = "Users" if m.group(0).startswith("/Users/") else "home"
        return "/" + prefix + "/…/"
    text = _LIB_PAT.sub("/Users/…", _HOME_PAT.sub(home, text))
    return _TMP_PAT.sub("<tmp>", text)


def classify(tail: str) -> str:
    """⛔ 纯函数：只看末行固定前缀，恰好一格，不解析文案猜（178 教训）。"""
    if "V6_SANDBOX_DENY:import:ctypes" in tail:
        return "deny_ctypes"
    if "V6_SANDBOX_DENY:import:fcntl" in tail:
        return "deny_fcntl"
    if "V6_SANDBOX_DENY:import:" in tail:
        return "deny_other"
    if "ImportError: cannot import name" in tail:
        return "stdlib_mismatch"
    return "other"


def ie_commits_of(per_commit: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """⛔ `import_error` 是**可选键**：只在装载失败时才写入（8 有 / 112 无）。
    不许用 `per_commit[0]` 的键集合推断整份存档有没有这个字段。
    """
    return [c for c in per_commit
            if c.get("extract_stats", {}).get("import_error") is True]


def summarize(rows: List[Dict[str, Any]],
              per_commit: List[Dict[str, Any]],
              census_rows: List[Dict[str, Any]],
              probes: Dict[str, Dict[str, Any]],
              attr180_rows: List[Dict[str, Any]],
              down177_rows: List[Dict[str, Any]],
              health: Dict[str, Any]) -> Dict[str, Any]:
    """纯函数：所有结论都从输入派生，不藏进程内状态。"""
    ie = ie_commits_of(per_commit)
    ie_shas = {c["sha"] for c in ie}
    sub = [r for r in census_rows if r["sha"] in ie_shas]
    um = [r for r in census_rows if r["gate"] == "unmatched"]

    cats = collections.Counter(probes[s]["cat"] for s in sorted(probes))
    sha180 = {r["sha"] for r in attr180_rows}
    covered = sorted(ie_shas & sha180)
    uncovered = sorted(ie_shas - sha180)
    n_zero = sum(1 for c in per_commit if c.get("n_tests_total") == 0)

    eqs = {
        "eq_denominator": (len(census_rows) == 459
                           and len(per_commit) == 120
                           and len(rows) == len(sub)),
        "eq_ie8": len(ie) == 8,
        "eq_ie_key_8_112": (
            sum(1 for c in per_commit
                if "import_error" in c.get("extract_stats", {})) == 8
            and sum(1 for c in per_commit
                    if "import_error" not in c.get("extract_stats", {})) == 112),
        "eq_ie_tests_zero": all(c.get("n_tests_total") == 0 for c in ie),
        "eq_two_books_not_iff": n_zero > len(ie),
        "eq_ie_rows14": len(sub) == 14,
        "eq_ie_rows_gates": dict(collections.Counter(
            r["gate"] for r in sub)) == {"form": 5, "unserializable": 5,
                                         "unmatched": 4},
        "eq_ie_rows_gates_closed": all(r["gate"] in IE_ROW_GATES for r in sub),
        "eq_ie_rows_unknown": all(r["downstream"] == "unknown" for r in sub),
        "eq_unmatched_all_ie": (len(um) == 4
                                and all(r["sha"] in ie_shas for r in um)),
        "eq_no_unmatched_outside_ie": sum(
            1 for r in census_rows if r["gate"] == "unmatched"
            and r["sha"] not in ie_shas) == 0,
        "eq_probe_coverage": set(probes) == ie_shas,
        "eq_cats_closed": all(p["cat"] in CATS for p in probes.values()),
        "eq_cats_no_other": all(p["cat"] != "other" for p in probes.values()),
        "eq_all_why_import": all(p["why"] == "import" for p in probes.values()),
        "eq_180_cross": (len(attr180_rows) == 9 and len(sha180) == 6
                         and len(covered) == 6 and len(uncovered) == 2),
        "eq_177_import_failed9": sum(
            1 for r in down177_rows
            if r.get("verdict") == "import_failed") == 9,
        "eq_caliber_self": "scripts/probe_import_error_census.py" in caliber(),
    }
    return {
        "schema": "import_error_census/v1",
        "n_per_commit": len(per_commit),
        "n_census_rows": len(census_rows),
        "n_ie_commits": len(ie),
        "n_ie_rows": len(sub),
        "n_tests_total_zero": n_zero,
        "n_tests_total_zero_not_ie": n_zero - len(ie),
        "ie_by_repo": dict(sorted(collections.Counter(
            c["repo"] for c in ie).items())),
        "ie_by_cat": dict(sorted(cats.items())),
        "ie_rows_by_gate": dict(sorted(collections.Counter(
            r["gate"] for r in sub).items())),
        "ie_rows_by_downstream": dict(sorted(collections.Counter(
            r["downstream"] for r in sub).items())),
        "ie_rows_by_repo": dict(sorted(collections.Counter(
            r["repo"] for r in sub).items())),
        "n_unmatched": len(um),
        "n_unmatched_in_ie": sum(1 for r in um if r["sha"] in ie_shas),
        "n_unmatched_outside_ie": sum(1 for r in um
                                      if r["sha"] not in ie_shas),
        "unmatched_shas": sorted({r["sha"] for r in um}),
        "cross_180": {
            "n_rows_180": len(attr180_rows),
            "n_sha_180": len(sha180),
            "covered_ie_shas": covered,
            "uncovered_ie_shas": uncovered,
            "uncovered_cats": {s: probes[s]["cat"] for s in uncovered
                               if s in probes},
            "note": ("⛔ 两本账分开：180 是行级 import_failed（9 条 / 6 sha），"
                     "本轮是 commit 级 import_error（8 个）；只交叉，不合并。"),
        },
        "cross_177": {
            "n_rows": len(down177_rows),
            "n_import_failed": sum(1 for r in down177_rows
                                   if r.get("verdict") == "import_failed"),
        },
        "baseline_health": {"ok": health.get("ok"),
                            "diffs": len(health.get("diffs", [])),
                            "repos": len(health.get("repos", {}))},
        "closure_eqs": eqs,
        "n_closure_true": sum(1 for v in eqs.values() if v),
        "n_closure_items": len(eqs),
        "note": ("⛔ 176 的 unmatched 4 条是 auto_extract 装载失败的派生格，"
                 "不是「两边对不上」；8/8 是安全边界正确拒收或题源年代错配。"
                 "⛔ 只报归因与账目、不报收益；没跑放宽 deny 对照 ⇒ 不许宣称"
                 "放宽后能测几条（⑤ 未开工）。⛔ 不推翻 177 unmeasurable / "
                 "179 的 119 条 form 下游 / 182 的 truthy 0；179 的 form 下游"
                 "属拍板 ①，177 已 cancel 拿原始 expr 跑沙箱（构造必然）。"),
    }


# ─────────────────────────── 主流程 ───────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--out", default="",
                    help="⛔ 默认不落盘；全量普查须显式给 --out")
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

    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    attr180 = json.loads(ATTR180.read_text(encoding="utf-8"))
    down177 = json.loads(DOWN177.read_text(encoding="utf-8"))
    per_commit, census_rows = census["per_commit"], census["rows"]
    ie = ie_commits_of(per_commit)
    want = {c["sha"] for c in ie}
    print("ie_commits=%d / %d  census_rows=%d" %
          (len(ie), len(per_commit), len(census_rows)))

    #: 8 次极简控制探针：只为触发装载，取异常末行
    probes: Dict[str, Dict[str, Any]] = {}
    for name, subdir in REPOS:
        repo = Path("/tmp/realrepo") / name
        for cand in select_commits(repo, args.limit, subdir):
            sha, src, _testfs, _subj = cand
            if sha not in want:
                continue
            fixed = _git(repo, "show", "%s:%s" % (sha, src))
            pkg: Optional[Dict[str, Any]] = None
            if subdir != "boltons/":
                pkg = pkg_context(repo, sha, src, fixed)
            ex = run_in_sandbox(fixed, [{"name": "t0", "call": "1"}],
                                timeout=args.timeout, pkg=pkg)
            lines = (str(ex.import_error) or "").rstrip().splitlines()
            tail = redact(lines[-1] if lines else "")
            why = (ex.results[0].get("why")
                   if ex.results else None)
            probes[sha] = {"repo": name, "cat": classify(tail),
                           "tail": tail[:240], "why": why,
                           "n_results": len(ex.results)}
            print("  %-16s %s  %-16s why=%s" %
                  (name, sha, probes[sha]["cat"], why))

    if set(probes) != want:
        print("⛔ 探针覆盖不全 ⇒ 拒绝出读数（fail-closed）", file=sys.stderr)
        raise SystemExit(2)

    ie_shas = want
    rows = []
    for r in census_rows:
        if r["sha"] not in ie_shas:
            continue
        p = probes[r["sha"]]
        rows.append({
            "repo": r["repo"], "sha": r["sha"], "gate": r["gate"],
            "fn": r.get("fn"), "stmt": (r.get("stmt") or "")[:240],
            "call": (r.get("call") or "")[:240],
            "downstream": r.get("downstream"), "reach": r.get("reach"),
            "v175": r.get("v175"), "in_census": r.get("in_census"),
            "ie_cat": p["cat"], "ie_tail": p["tail"],
        })

    s = summarize(rows, per_commit, census_rows, probes,
                  attr180["rows"], down177["rows"], health)
    print("\n=== r104 import_error 污染面普查 ===")
    print("  ie_commits =", s["n_ie_commits"], " ie_rows =", s["n_ie_rows"])
    print("  ie_by_cat  =", s["ie_by_cat"])
    print("  ie_rows_by_gate =", s["ie_rows_by_gate"])
    print("  unmatched  =", s["n_unmatched"],
          " in_ie =", s["n_unmatched_in_ie"],
          " outside_ie =", s["n_unmatched_outside_ie"])
    print("  n_tests_total_zero =", s["n_tests_total_zero"],
          " (真 ie 只有 %d ⇒ 两本账分开)" % s["n_ie_commits"])
    print("  cross_180  =", s["cross_180"])
    print("  closure    =", s["n_closure_true"], "/", s["n_closure_items"])
    print("  " + s["note"])

    if not all(s["closure_eqs"].values()):
        print("⛔ 封闭等式有假 ⇒ 拒绝出读数（fail-closed）", file=sys.stderr)
        raise SystemExit(2)

    if args.out:
        out = Path(args.out)
        out.write_text(json.dumps({
            "schema": "import_error_census/v1",
            "caliber": caliber(),
            "ie_commits": [
                {"repo": c["repo"], "sha": c["sha"],
                 "n_new": c.get("n_new"),
                 "n_tests_total": c.get("n_tests_total"),
                 "gates": c.get("gates"),
                 **{k: probes[c["sha"]][k] for k in ("cat", "tail", "why")}}
                for c in ie],
            "rows": rows, "summary": s,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n落盘：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
