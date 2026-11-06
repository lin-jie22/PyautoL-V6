#!/usr/bin/env python3
"""r99 / README 178 —— 给 177 的 94 条 `no_result_both` 做**结构化归因**。

═══ 要填的洞 ═══

177 把 254 条 `downstream=unknown` 中 135 条测出必要条件上界，但主账里
**94 条 `no_result_both` 连「为什么没结果」都没记账**：存档里
`sandbox_verdict / got_buggy / got_fixed` 全是 `None`，`downstream=unknown`。
README 177 里那句 `NameError: stream is not defined` 只是抽查时的观察，
不是结构化读数 ⇒ 「上下文门」至今仍是一个大格。

═══ ⛔ 本脚本做什么、不做什么 ═══

✅ **两本账分开、绝不合并成一个布尔**：
   ① **静态归因账**：用产品同源 `_iter_all_probes` / `collect_bindings`
      取原始断言与绑定表，对产品输出的 `call` 做 AST 自由变量分析，
      每条落到**恰好一格**（自由变量出身），并**正交**记「产品是否内联过」。
   ② **运行时硬证据账**：重跑 177 同源 truthy 沙箱，但改用**产出方写死
      的 `why`**（`environment.py:302` 明令不许解析 evidence 文案猜分类），
      记 `why_fixed / why_buggy` 和 evidence 原文，验证「94 条是否真的
      两侧都抛异常」——177 的 `run_all` 异常时丢掉 evidence 只留
      `got=None`，正是存档里 `sandbox_verdict` 全 `None` 的原因。
⛔ **不产出判据、不报收益、不改 `aether/` 一个字节**。178 的读数只回答
   「被什么挡住」，不回答「解开能修几个」（后者要过三验 / 判据形态 /
   定位门 / 形态门，177 已证 10 条 diff 全 `reach=X`）。
⛔ **不许另写第二把尺子**（175 教训）：原始断言一律取产品
   `_iter_all_probes`，绑定一律取产品 `collect_bindings`，沙箱一律用
   `aether.environment.run_in_sandbox` 本身。

═══ 便宜对照已证尺子有牙（5 条分出 5 种形态）═══

    stream.getvalue()                  → with-as 绑定，产品完全没内联
    Stats(data)...                     → 产品内联过，但残留循环变量 data
    typing.List[int]                   → import 名没带进沙箱（补局部变量无效）
    ...slice(start, stop, step)        → 产品内联过，残留 3 个循环变量
    i                                  → 裸循环变量

⇒ 「补上下文」根本不是一件事：至少混着「内联器自限」「模块级 import
未装载」「resolve_probe 动过即返回导致内联不彻底」三类不同修法。

用法：
    python scripts/probe_no_result_attribution.py
    python scripts/probe_no_result_attribution.py --only 4e5faa3,1cdc0e9
    python scripts/probe_no_result_attribution.py --out scripts/no_result_attribution.json
"""
from __future__ import annotations

import argparse
import ast
import builtins
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
from aether.extract import _iter_all_probes, collect_bindings  # noqa: E402
from probe_alias_demote import select_commits, _git            # noqa: E402
from probe_package_load import pkg_context                      # noqa: E402
from realrepo_baseline import require_baseline                 # noqa: E402

REPOS: Tuple[Tuple[str, str], ...] = (
    ("boltons", "boltons/"),
    ("more-itertools", "more_itertools/"),
    ("attrs", "src/attr/"),
)
SOURCE = ROOT / "scripts" / "extract_downstream.json"

#: ⛔ 封闭集合：不许为了迁就读数新增格（166 纪律）。
GATES: Tuple[str, ...] = (
    "self_attr",          #: unittest 实例 / cls
    "param_or_fixture",   #: 测试函数参数（含 pytest fixture）
    "loop_var",           #: for / comprehension target（内联器自限 2 明确禁止）
    "with_binding",       #: with ... as X（collect_bindings 不收）
    "imported_name",      #: 模块级 import 没带进沙箱
    "ambiguous_rhs",      #: 有赋值出身但被 collect_bindings 剔除（同名多 rhs）
    "inlinable_rhs",      #: 在绑定表里却仍残留（产品没内联干净）
    "unbound",            #: 以上都不是
    "builtin_only",       #: 只剩内建名 ⇒ 不构成障碍，单列
    "no_free_var",        #: 没有自由变量 ⇒ 单列（本该能求值）
    "unparseable",        #: call 语法不可解析（量具格，不参与归因）
)

#: 主格优先级：取所有自由变量里「最拦路」的那一类。
PRIORITY: Tuple[str, ...] = (
    "self_attr", "param_or_fixture", "loop_var", "with_binding",
    "imported_name", "ambiguous_rhs", "inlinable_rhs", "unbound",
    "builtin_only", "no_free_var",
)

INLINE_STATES = ("none", "partial", "complete", "unknown", "unparseable")

CALIBER_FILES = (
    "aether/extract.py",
    "aether/environment.py",
    "scripts/probe_alias_demote.py",
    "scripts/probe_extract_downstream.py",
    "scripts/probe_criteria_gate_census.py",
    "scripts/probe_no_result_attribution.py",   #: ⛔ 钉尺子自身（174 教训）
    "scripts/criteria_gate_census.json",
    "scripts/extract_downstream.json",
)

BUILTINS = frozenset(dir(builtins))


# ─────────────────────────── 纯函数（供契约离线反证） ───────────────────────────
def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {f: sha256_file(ROOT / f) for f in CALIBER_FILES}


def free_names(call: str) -> Optional[List[str]]:
    """`call` 的 Load 名字集合；解析不了返回 None（⛔ 不是空集）。"""
    try:
        t = ast.parse(call, mode="eval")
    except (SyntaxError, ValueError):
        return None
    return sorted({n.id for n in ast.walk(t)
                   if isinstance(n, ast.Name)
                   and isinstance(n.ctx, ast.Load)})


def scope_info(tsrc: str, fn: str):
    """返回 (origins, params, scope_found)。

    origins: {name: {出身…}}；出身 ∈ assign / with / loop / import。
    ⛔ 只从**指定 fn 的函数体**取 with / loop / 参数，import 取全树
       （import 通常在模块级）。找不到 fn ⇒ 退回全树并标 scope_found=False。
    """
    tree = ast.parse(tsrc)
    node = next((n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == fn), None)
    body = list(node.body) if node is not None else list(tree.body)
    found = node is not None

    origins: Dict[str, set] = collections.defaultdict(set)

    def add(nm: str, kind: str):
        if nm:
            origins[nm].add(kind)

    for n in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    add(t.id, "assign")
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            add(n.target.id, "assign")
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for it in n.items:
                if isinstance(it.optional_vars, ast.Name):
                    add(it.optional_vars.id, "with")
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            for x in ast.walk(n.target):
                if isinstance(x, ast.Name):
                    add(x.id, "loop")
        elif isinstance(n, ast.comprehension):
            for x in ast.walk(n.target):
                if isinstance(x, ast.Name):
                    add(x.id, "loop")
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                add((a.asname or a.name).split(".")[0], "import")

    params = set()
    if node is not None:
        for a in list(node.args.args) + list(node.args.kwonlyargs):
            params.add(a.arg)
        if node.args.vararg:
            params.add(node.args.vararg.arg)
        if node.args.kwarg:
            params.add(node.args.kwarg.arg)
    return dict(origins), params, found


def classify_name(nm: str, origins: Dict[str, Any],
                  fn_binds: Dict[str, Any], mod_binds: Dict[str, Any],
                  params: set) -> str:
    """单个自由变量的归因（⛔ 返回值必须 ∈ GATES）。"""
    if nm in ("self", "cls"):
        return "self_attr"
    if nm in params:
        return "param_or_fixture"
    o = origins.get(nm, set())
    if "loop" in o:
        return "loop_var"
    if "with" in o:
        return "with_binding"
    if "import" in o:
        return "imported_name"
    if "assign" in o:
        if nm in fn_binds or nm in mod_binds:
            return "inlinable_rhs"
        return "ambiguous_rhs"
    if nm in BUILTINS:
        return "builtin_only"
    return "unbound"


def main_gate(names: Optional[List[str]], classes: Dict[str, str]) -> str:
    """⛔ 每条恰好一格；None（解析不了）⇒ unparseable。"""
    if names is None:
        return "unparseable"
    if not names:
        return "no_free_var"
    got = {classes[n] for n in names}
    for g in PRIORITY:
        if g in got:
            return g
    #: 走到这里说明 classify_name 产出了封闭集合外的值 ⇒ fail-closed
    raise ValueError("unmapped class: %s" % sorted(got))


def inline_state_of(call: str, orig: Optional[str],
                    names: Optional[List[str]]) -> str:
    """正交账：产品到底内联过没有（⛔ 不并入主格）。"""
    if names is None:
        return "unparseable"
    if orig is None:
        return "unknown"
    if call.strip() == orig.strip():
        return "none"
    return "complete" if not names else "partial"


#: 运行时账封闭集合（⛔ 只看产出方写死的 why，不解析 evidence 文案）
PAIRS: Tuple[str, ...] = (
    "both_raise", "mixed", "not_raise", "import", "missing", "skipped")


def pair_verdict(wf: Optional[str], wb: Optional[str]) -> str:
    """⛔ 纯函数：两侧 `why` 的合取结论，供契约离线反证。"""
    if wf is None or wb is None:
        return "skipped"
    if "import" in (wf, wb):
        return "import"
    if "missing_result" in (wf, wb):
        return "missing"
    if wf == "raise" and wb == "raise":
        return "both_raise"
    if "raise" in (wf, wb):
        return "mixed"
    return "not_raise"


def sandbox_read(source: str, tests: List[Dict[str, Any]],
                 timeout: float, pkg: Optional[Dict[str, Any]]):
    """同源 truthy 沙箱；返回 `[(why, evidence)]`，长度**恒等于** tests。

    ⛔ results 数量不足 ⇒ fail-closed 补 `missing_result`，不许静默错位
       （第 159 条：返回类型不匹配被静默吞，症状是「全 0 / 全 unchanged」）。
    """
    ex = run_in_sandbox(source, tests, timeout=timeout, pkg=pkg)
    if ex.import_error:
        return [("import", "import-failed: " +
                 str(ex.import_error)[:160])] * len(tests)
    out = [(str(r.get("why")), str(r.get("evidence") or "")[:200])
           for r in ex.results]
    while len(out) < len(tests):
        out.append(("missing_result", ""))
    del out[len(tests):]
    return out


# ─────────────────────────── 主流程 ───────────────────────────
def probe_index(repo: Path, sha: str,
                testfs: List[str]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """⛔ 与 177 同源：产品 `_iter_all_probes` 建 (fn, stmt) → probe。"""
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


def summarize(rows: List[Dict[str, Any]], health: Dict[str, Any],
              unmatched: List[Dict[str, Any]],
              skipped: List[Dict[str, Any]]) -> Dict[str, Any]:
    gates = collections.Counter(r["main_gate"] for r in rows)
    states = collections.Counter(r["inline_state"] for r in rows)
    cross = collections.Counter("%s/%s" % (r["main_gate"], r["inline_state"])
                                for r in rows)
    pairs = collections.Counter(r["sandbox_pair"] for r in rows)
    whys = collections.Counter(r["why_fixed"] for r in rows)
    pxg = collections.Counter("%s/%s" % (r["sandbox_pair"], r["main_gate"])
                              for r in rows)
    ev = collections.Counter(r["evidence_fixed"][:90] for r in rows
                             if r["evidence_fixed"])
    return {
        "n_rows": len(rows),
        "gates": dict(sorted(gates.items())),
        "inline_state": dict(sorted(states.items())),
        "cross": dict(sorted(cross.items())),
        "sandbox_pair": dict(sorted(pairs.items())),
        "why_fixed": dict(sorted(whys.items())),
        "pair_x_gate": dict(sorted(pxg.items())),
        "n_distinct_evidence": len(ev),
        "top_evidence": [[k, v] for k, v in ev.most_common(6)],
        "by_repo": dict(sorted(collections.Counter(
            r["repo"] for r in rows).items())),
        "n_scope_missing": sum(1 for r in rows if not r["scope_found"]),
        "n_unmatched": len(unmatched),
        "n_skipped_commits": len(skipped),
        "baseline_health": {"ok": health.get("ok"),
                            "diffs": len(health.get("diffs", [])),
                            "repos": len(health.get("repos", {}))},
        "note": ("⛔ 两本账分开：静态归因（main_gate + inline_state）与运行时"
                 "硬证据（sandbox_pair / why / evidence）绝不合并成布尔。"
                 "不产出判据、不报收益；unparseable / unmatched / skipped "
                 "是量具格，不参与归因推断。"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--out", default="")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--no-sandbox", action="store_true",
                    help="⛔ 只出静态账（仅供离线反证）")
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

    src = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows_in = [r for r in src["rows"]
               if r.get("verdict") == "no_result_both"]
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
    subdirs = dict(REPOS)

    rows: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for (name, sha), grp in groups.items():
        cand = next((c for c in cands[name] if c[0] == sha), None)
        if cand is None:
            for r in grp:
                unmatched.append({"repo": name, "sha": sha,
                                  "fn": r["fn"], "reason": "not in select_commits"})
            continue
        _, src_path, testfs, _ = cand
        repo = Path("/tmp/realrepo") / name
        idx = probe_index(repo, sha, testfs)

        #: ② 运行时硬证据账：177 同源 truthy 沙箱（不带 expect），
        #:    但取**产出方写死的 why** + evidence 原文，不解析文案分类。
        sb: List[Dict[str, Any]] = [
            {"why_fixed": None, "why_buggy": None,
             "evidence_fixed": "", "evidence_buggy": "",
             "sandbox_pair": "skipped"} for _ in grp]
        if not args.no_sandbox:
            fixed = _git(repo, "show", "%s:%s" % (sha, src_path))
            buggy = _git(repo, "show", "%s^:%s" % (sha, src_path))
            pkg = None
            if fixed and subdirs[name] != "boltons/":
                try:
                    pkg = pkg_context(repo, sha, src_path, fixed)
                except Exception as exc:            # noqa: BLE001
                    skipped.append({"repo": name, "sha": sha,
                                    "reason": "pkg_context: %s: %s" %
                                              (type(exc).__name__, exc)})
            tests = [{"name": "q%d" % i,
                      "call": (r.get("call") or "").strip()}
                     for i, r in enumerate(grp)]
            if fixed and buggy:
                try:
                    wf = sandbox_read(fixed, tests, args.timeout, pkg)
                    wb = sandbox_read(buggy, tests, args.timeout, pkg)
                    for i in range(len(grp)):
                        sb[i] = {
                            "why_fixed": wf[i][0], "why_buggy": wb[i][0],
                            "evidence_fixed": wf[i][1],
                            "evidence_buggy": wb[i][1],
                            "sandbox_pair": pair_verdict(wf[i][0], wb[i][0]),
                        }
                except Exception as exc:            # noqa: BLE001
                    skipped.append({"repo": name, "sha": sha,
                                    "reason": "sandbox: %s: %s" %
                                              (type(exc).__name__, exc)})
            else:
                skipped.append({"repo": name, "sha": sha,
                                "reason": "no fixed/buggy pair"})

        #: 测试源码按文件缓存；绑定表与出身表按 (tf, fn) 缓存
        tsrcs = [(tf, _git(repo, "show", "%s:%s" % (sha, tf)))
                 for tf in testfs]
        tsrcs = [(tf, t) for tf, t in tsrcs if t]
        cache: Dict[Tuple[str, str],
                    Tuple[Dict, Dict, Dict, Tuple[set, bool]]] = {}

        for i, r in enumerate(grp):
            q = idx.get((r["fn"], r["stmt"]))
            orig = (q or {}).get("expr")
            if q is None:
                unmatched.append({"repo": name, "sha": sha,
                                  "fn": r["fn"], "stmt": r["stmt"],
                                  "reason": "probe_index miss"})

            tf = next((f for f, t in tsrcs if r["fn"] in t), None)
            tsrc = next((t for f, t in tsrcs if f == tf), "") if tf else ""
            key = (tf or "", r["fn"])
            if key not in cache:
                try:
                    binds = collect_bindings(tsrc) if tsrc else {}
                    origins, params, found = (
                        scope_info(tsrc, r["fn"]) if tsrc else ({}, set(), False))
                    cache[key] = (binds.get(r["fn"], {}),
                                  binds.get("<module>", {}), origins,
                                  (params, found))
                except SyntaxError:
                    cache[key] = ({}, {}, {}, (set(), False))
            fb, mb, origins, (params, found) = cache[key]

            call = r.get("call") or ""
            names = free_names(call)
            classes = {}
            if names is not None:
                for nm in names:
                    classes[nm] = classify_name(nm, origins, fb, mb, params)
            gate = main_gate(names, classes)
            state = inline_state_of(call, orig, names)

            rows.append({
                "repo": name, "sha": sha, "fn": r["fn"],
                "stmt": r["stmt"], "call": call,
                "orig_expr": orig, "free_vars": names,
                "name_classes": classes, "main_gate": gate,
                "inline_state": state, "scope_found": found,
                "gate_176": r.get("gate"), "reach": r.get("reach"),
                "v175": r.get("v175"),
                "diagnostic_only": True,
                **sb[i],
            })

    rep = {
        "schema": "no_result_attribution/v1",
        "caliber": caliber(),
        "rows": rows,
        "unmatched": unmatched,
        "skipped": skipped,
        "summary": summarize(rows, health, unmatched, skipped),
    }
    out = args.out or str(ROOT / "scripts" / "no_result_attribution.json")
    Path(out).write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    s = rep["summary"]
    print("n_rows=%d  gates=%s" % (s["n_rows"], s["gates"]))
    print("inline_state=%s" % s["inline_state"])
    print("sandbox_pair=%s  why_fixed=%s" %
          (s["sandbox_pair"], s["why_fixed"]))
    print("pair_x_gate=%s" % s["pair_x_gate"])
    print("top_evidence=%s" % s["top_evidence"])
    print("n_scope_missing=%d  n_unmatched=%d  n_skipped_commits=%d  baseline=%s" %
          (s["n_scope_missing"], s["n_unmatched"],
           s["n_skipped_commits"], s["baseline_health"]))
    print("written: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
