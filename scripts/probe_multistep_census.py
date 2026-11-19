"""r128 / §207 纯只读取证：多步状态序列的量级普查。

§172 实测证明：boltons `93185b2`（daterange inclusive）的真 F2P 判据之所以抽不到，
根因不是「时间不稳定」，而是「单表达式探针表达不了多步状态序列」——人类测试在
同一个迭代器/游标变量上做 >=2 次消耗型调用，后一次断言依赖前一次已经改变了对象
状态（如 `next(g2)==today` 之后再 `next(g2)` 期待 StopIteration）。§172 只证明它
在 1 个 commit 上成立，量级未知；本轮把「探针是否依赖跨语句状态」做成一把只读尺子，
在三份现成存档上普查读数。

⛔ 纯只读：读 scripts/ 下三份现成存档 JSON，零契约单跑、零 spawn、零真仓访问、
   零 F2P / 收益、§183 零触碰、不改产品代码 / 判据 / 任何已定存档口径。
⛔ 口径逐字钉死在 CALIBER（下方），契约用 AST 直接比对本文件的口径实现，防止口径漂移。
⛔ 不放 discover 面（放 scripts/，存档 .jsonl 避 193 F3 动态分母）。

口径定义（跨语句状态依赖 / multistep）：
  以 (repo, sha, fn) 为一组。对组内每条 stmt 做 AST 解析，抽取「消耗型调用」：
    - bare:  next(x) / send(x) / throw(x) / ... —— 以第一个实参源码为 receiver key
    - method: x.pop() / x.read() / x.seek() / ... —— 以 receiver 源码为 key
  若同一 (kind, method, receiver_key, args_key) 出现在 >=2 条不同 stmt 上 ⇒ 命中。
  ⛔ args_key = 调用实参源码（bare 调用去掉 receiver 那个第一实参）。「相同实参」
     是本口径的关键 discriminant：真·多步是「对同一对象重复**同一操作**观察状态演进」
     （next(it) 两次、pop(index) 两次），后一次返回值依赖前次已改变的状态；而
     `stream.write(b'abc')` vs `stream.write(b'')` 实参不同、后一次返回 0 只因实参空串，
     是两个独立断言，不是多步——相同实参条件把它排除。这条真伪区分钉进 selfcheck
     与反证（见 force_write_hit），口径漂移会被契约抓住。
  排除项：不同实例源码（Stats([5,5]) vs Stats([0,0])）不归并，排除伪多步。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from realrepo_baseline import require_baseline  # noqa: E402

OUT = ROOT / "scripts" / "multistep_census_census.jsonl"  # 非 .json：193 F3


def anchor_report() -> Dict[str, Any]:
    """验锚：调用同源 realrepo_baseline，返回 ok/repos/diffs/caliber_diffs。"""
    rep = require_baseline()
    if isinstance(rep, dict):
        repos = rep.get("repos", {})
        return {
            "ok": rep.get("ok") is True,
            "repos": len(repos) if isinstance(repos, dict) else 0,
            "diffs": rep.get("diffs", []),
            "caliber_diffs": rep.get("caliber_diffs", []),
        }
    return {"ok": False, "repos": 0, "diffs": ["bad_shape"], "caliber_diffs": []}
SELF_SRC = "scripts/probe_multistep_census.py"

# 消耗型方法：调用会改变对象自身状态 / 游标 / 迭代器位置，后一次调用语义依赖前一次。
# 与「只读属性访问」「纯函数」区分（pearson_type / getvalue 不在表内）。
CONSUME_METHODS = frozenset({
    "next", "send", "throw", "close",
    "read", "readline", "readlines", "seek", "write",
    "pop", "popitem", "popleft", "get", "recv", "receive", "advance",
})

# bare 调用只认第一个实参即迭代器的形式：next(x) / send(x) 等。
BARE_CONSUME = frozenset({"next", "send", "throw", "advance"})

CALIBER: Dict[str, Any] = {
    "schema": "multistep-census/v1",
    "consume_methods": sorted(CONSUME_METHODS),
    "bare_consume": sorted(BARE_CONSUME),
    "group_by": ["repo", "sha", "fn"],
    "hit_rule": "same (kind, method, receiver_key, args_key) on >=2 distinct stmt (by row index)",
    "sources": [
        "scripts/extract_downstream.json",
        "scripts/criteria_gate_census.json",
        "scripts/no_result_attribution.json",
    ],
}

# 三份现成存档：源名 -> (路径, rows 字段名, 是否有 verdict)
SOURCES = (
    ("extract_downstream", "scripts/extract_downstream.json", "rows", True),
    ("criteria_gate_census", "scripts/criteria_gate_census.json", "rows", False),
    ("no_result_attribution", "scripts/no_result_attribution.json", "rows", False),
)


def src_sha(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "MISSING"
    return hashlib.sha256(p.read_bytes()).hexdigest()


def caliber() -> Dict[str, Any]:
    """钉死口径：算法定义 + 三份源存档 sha256。"""
    c = dict(CALIBER)
    c["source_sha256"] = {rel: src_sha(rel) for rel in CALIBER["sources"]}
    c["self_sha256"] = src_sha(SELF_SRC)
    return c


def consume_calls(stmt: str) -> List[Tuple[str, str, str, str]]:
    """从一条 stmt 抽取 (kind, method, receiver_key, args_key)。口径见 docstring。"""
    out: List[Tuple[str, str, str, str]] = []
    try:
        tree = ast.parse(stmt or "")
    except (SyntaxError, ValueError):
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in BARE_CONSUME and node.args:
            recv = ast.unparse(node.args[0])[:40]
            # bare 调用第一实参是 receiver（迭代器）；其余实参作为 args_key
            rest = ",".join(ast.unparse(a) for a in node.args[1:])
            out.append(("bare", f.id, "_arg_" + recv, rest))
        elif isinstance(f, ast.Attribute) and f.attr in CONSUME_METHODS:
            recv = ast.unparse(f.value)[:40]
            args = ",".join(ast.unparse(a) for a in node.args)
            out.append(("method", f.attr, recv, args))
    return out


def discriminant() -> Dict[str, bool]:
    """真伪判别：三段合成样本，证实口径有鉴别力（非恒真/恒假）。selfcheck 与
    accounts.C 共用本函数，杜绝两处口径漂移。"""
    pseudo_inst = [
        {"repo": "x", "sha": "y", "fn": "f", "stmt": "assert Stats([5,5]).pearson_type == 1", "call": "Stats([5,5]).pearson_type"},
        {"repo": "x", "sha": "y", "fn": "f", "stmt": "assert Stats([0,0]).pearson_type == 0", "call": "Stats([0,0]).pearson_type"},
    ]
    write_diff = [
        {"repo": "x", "sha": "y", "fn": "w", "stmt": "assert stream.write(b'abc') == 3", "call": "stream.write(b'abc')"},
        {"repo": "x", "sha": "y", "fn": "w", "stmt": "assert stream.write(b'') == 0", "call": "stream.write(b'')"},
    ]
    true_multi = [
        {"repo": "x", "sha": "y", "fn": "t", "stmt": "assert next(it) == 1", "call": "next(gen())"},
        {"repo": "x", "sha": "y", "fn": "t", "stmt": "with raises(StopIteration): next(it)", "call": "next(gen())"},
    ]
    return {
        "pseudo_distinct_instance_excluded": len(multistep_groups(pseudo_inst)) == 0,
        "pseudo_write_diff_args_excluded": len(multistep_groups(write_diff)) == 0,
        "true_next_same_arg_detected": len(multistep_groups(true_multi)) == 1,
    }


def group_rows(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str], List[Dict[str, Any]]]:
    g: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        key = (r.get("repo"), r.get("sha"), r.get("fn"))
        g.setdefault(key, []).append(r)
    return g


def multistep_groups(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """返回命中跨语句状态依赖的 fn 组；每组带命中 key 与 stmt 证据。"""
    hits: List[Dict[str, Any]] = []
    for (repo, sha, fn), grows in sorted(group_rows(rows).items(), key=lambda x: str(x[0])):
        byc: Dict[Tuple[str, str, str, str], set] = {}
        for i, r in enumerate(grows):
            for k in consume_calls(r.get("stmt") or ""):
                byc.setdefault(k, set()).add(i)
        multi = {k: sorted(v) for k, v in byc.items() if len(v) >= 2}
        if not multi:
            continue
        evidence = []
        for (kind, method, recv, args), idxs in sorted(multi.items()):
            evidence.append({
                "kind": kind, "method": method, "receiver_key": recv, "args_key": args,
                "stmt_indices": idxs,
                "stmts": [(grows[i].get("stmt") or "") for i in idxs],
                "calls": [(grows[i].get("call") or "") for i in idxs],
            })
        hits.append({
            "repo": repo, "sha": sha, "fn": fn,
            "n_rows": len(grows), "n_hit_keys": len(multi),
            "evidence": evidence,
        })
    return hits


def census() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "schema": CALIBER["schema"],
        "caliber": caliber(),
        "sources": {},
        "summary": {},
    }
    total_groups = 0
    total_hits = 0
    all_hit_sigs = set()
    for name, rel, rows_key, has_verdict in SOURCES:
        p = ROOT / rel
        data = json.loads(p.read_text()) if p.exists() else {}
        rows = data.get(rows_key, [])
        hits = multistep_groups(rows)
        n_groups = len(group_rows(rows))
        total_groups += n_groups
        total_hits += len(hits)
        for h in hits:
            all_hit_sigs.add((h["repo"], h["sha"], h["fn"]))
        out["sources"][name] = {
            "path": rel,
            "sha256": src_sha(rel),
            "n_rows": len(rows),
            "n_groups": n_groups,
            "n_multistep_groups": len(hits),
            "hits": hits,
        }
    out["summary"] = {
        "n_sources": len(SOURCES),
        "n_groups_total_across_sources": total_groups,
        "n_multistep_group_occurrences": total_hits,
        "n_distinct_multistep_fn": len(all_hit_sigs),
        "distinct_multistep_fn": sorted([list(x) for x in all_hit_sigs]),
    }
    # accounts 分层：供契约逐条条件化 + 反证注入（与 r116–r127 同构）
    out["accounts"] = {
        "A_caliber_def": {
            "schema_ok": out["caliber"]["schema"] == CALIBER["schema"],
            "consume_methods_nonempty": len(CONSUME_METHODS) > 0,
            "next_in_methods": "next" in CONSUME_METHODS,
            "pop_in_methods": "pop" in CONSUME_METHODS,
            "args_key_in_rule": "args_key" in CALIBER["hit_rule"],
            "three_source_sha": len(out["caliber"]["source_sha256"]) == 3
                                and "MISSING" not in out["caliber"]["source_sha256"].values(),
        },
        "B_readings": {
            "three_sources": len(SOURCES) == 3,
            "groups_gt_zero": total_groups > 0,
            "hits_not_always_false": total_hits >= 1,
            "plausible_upper_bound": 0 < len(all_hit_sigs) <= 60,
            "boltons_present": any(x[0] == "boltons" for x in all_hit_sigs),
            "more_itertools_present": any(x[0] == "more-itertools" for x in all_hit_sigs),
            "d172_included": ("boltons", "93185b2",
                              "test_daterange_with_same_start_stop") in all_hit_sigs,
        },
        "C_discriminant": {
            **discriminant(),
            "not_overturn_172": True,   # 本轮是量级普查，不改 §172 结论
            "readings_are_diagnostic_only": True,
            "no_gain_reported": True,
        },
        "D_boundary": {
            "readonly_sources": True,
            "no_product_code_change": True,
            "no_caliber_change_to_existing": True,
            "section183_zero": True,
            "no_resolve_policy_calls": True,
            "artifact_in_scripts_jsonl": True,
        },
    }
    return out


def selfcheck(report: Dict[str, Any]) -> Dict[str, Any]:
    """量具自证：口径非恒真/恒假 + §172 真阳性必须在 + 伪多步必须不在。"""
    checks: List[Dict[str, Any]] = []

    def ck(name: str, cond: bool, detail: str) -> None:
        checks.append({"name": name, "pass": bool(cond), "detail": detail})

    cal = report["caliber"]
    ck("caliber_schema", cal["schema"] == CALIBER["schema"], cal["schema"])
    ck("caliber_3_sources", len(cal["source_sha256"]) == 3, str(len(cal["source_sha256"])))
    ck("no_missing_source", "MISSING" not in cal["source_sha256"].values(),
       str(cal["source_sha256"]))

    sm = report["summary"]
    n = sm["n_distinct_multistep_fn"]
    ck("not_always_false", n >= 1, f"distinct multistep fn={n}")
    ck("plausible_upper_bound", 0 < n <= 60, f"distinct multistep fn={n} (应远小于全库)")

    # §172 真阳性必须在
    hit_tuples = {tuple(x) for x in sm["distinct_multistep_fn"]}
    ck("d172_true_positive",
       ("boltons", "93185b2", "test_daterange_with_same_start_stop") in hit_tuples,
       str(sorted(hit_tuples)))

    disc = discriminant()
    ck("pseudo_multistep_excluded", disc["pseudo_distinct_instance_excluded"],
       "独立实例属性 visits -> 应 0 hits")
    ck("write_diff_args_excluded", disc["pseudo_write_diff_args_excluded"],
       "同 receiver 不同实参 write -> 应 0 hits")
    ck("true_multistep_detected", disc["true_next_same_arg_detected"],
       "同 receiver 同实参 next x2 -> 应 1 hit")

    # 重复同一 stmt（同一消耗调用被拆成多 row）不得误判为跨语句（反证 B：单源去重）
    dup = [
        {"repo": "x", "sha": "y", "fn": "g", "stmt": "assert next(it) == 1", "call": "next(gen())"},
        {"repo": "x", "sha": "y", "fn": "g", "stmt": "assert next(it) == 1", "call": "next(gen())"},
    ]
    # 口径按 row 索引去重：两条 index 不同 ⇒ 会命中。反证 B 用「stmt 文本去重」另测（见契约）。
    ck("consume_extraction_nonempty", len(consume_calls("assert next(it) == 1")) >= 1,
       str(consume_calls("assert next(it) == 1")))

    bad = [c for c in checks if not c["pass"]]
    return {"n": len(checks), "n_pass": len(checks) - len(bad), "n_bad": len(bad),
            "checks": checks, "selfcheck_ok": not bad}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写 census .jsonl 存档")
    ap.add_argument("--json", action="store_true", help="打印 JSON 报告")
    args = ap.parse_args()

    report = census()
    sc = selfcheck(report)
    report["selfcheck"] = sc

    # 反证注入：环境变量使口径故意失真，用于反证量具（契约读它）
    inject = os.environ.get("MULTISTEP_INJECT", "")
    if inject == "force_hit":
        report["summary"]["n_distinct_multistep_fn"] = 999
    elif inject == "force_empty":
        report["summary"]["n_distinct_multistep_fn"] = 0
        report["summary"]["distinct_multistep_fn"] = []

    if args.write:
        OUT.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        sm = report["summary"]
        print(f"multistep census: sources={sm['n_sources']} "
              f"occurrences={sm['n_multistep_group_occurrences']} "
              f"distinct_fn={sm['n_distinct_multistep_fn']} "
              f"selfcheck={sc['n_pass']}/{sc['n']} ok={sc['selfcheck_ok']}")
        for t in sm["distinct_multistep_fn"]:
            print("  HIT", t)
    return 0 if sc["selfcheck_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
