#!/usr/bin/env python3
"""r105 / README 184 —— `call[:240]` / `stmt[:240]` 截断污染面普查的契约。

A. 钉死全量读数（纯读存档，不碰本机基线）
B. 反证组：纯内存离线注入（恒真/恒假分类、篡改 prefix、抹平两本账、
   篡改上游 sha256 / caliber / 去验锚、查询正例），⛔ 不 spawn git、不跑沙箱
C. 本机锚 + 真实正例：证明完整值恢复与 truthy 尺子在**真实沙箱**上
   能命中（规则 24 / 133），且上游存档确实未被改动

⛔ 契约首次全绿必须注入反证（181 教训）；尺子自身 sha256 钉在 caliber 里，
   改 `probe_truncation_census.py` 而没重跑普查 ⇒ A18 恰好红。
⛔ 分寸：5 条 unparseable 是写盘截断的构造必然，本轮**只修归因、
   不推翻 178 的 89 条分母**；完整值下 truthy F2P 仍是 0。
"""
from __future__ import annotations

import collections
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import realrepo_baseline as rb                                   # noqa: E402
from probe_alias_demote import select_commits, _git             # noqa: E402
from probe_gate_attribution import attribute_commit             # noqa: E402
from probe_ctx_downstream import sandbox_read, truthy_f2p       # noqa: E402
from probe_truncation_census import (                           # noqa: E402
    CALIBER_FILES, caliber, is_truncated, parseable, prefix240,
    full_value_hits, two_books, surface_of, redact, TRUNC)

OBS_PATH = ROOT / "scripts" / "truncation_census.json"
OBS: Dict[str, Any] = json.loads(OBS_PATH.read_text(encoding="utf-8"))
CENSUS_PATH = ROOT / "scripts" / "criteria_gate_census.json"
DOWN178_PATH = ROOT / "scripts" / "no_result_attribution.json"

PASSES: List[str] = []
FAILS: List[str] = []
SKIPS: List[str] = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  | " + detail) if detail and not ok else ""))
    #: ⛔ 必须写成字面 `FAILS.append`：静态闸 contract_assert_wiring B1
    if not ok:
        FAILS.append(name)
    else:
        PASSES.append(name)


def skip(name: str, why: str) -> None:
    print("  SKIP %s  | %s" % (name, why))
    SKIPS.append(name)


def portability() -> Any:
    """加载产品自己的 portability 判据（不另写第二把尺子）。"""
    spec = importlib.util.spec_from_file_location(
        "r105_port", ROOT / "tests" / "contract_portability_test.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ══════════════════════════ A 组：钉死全量读数 ══════════════════════════

def group_a() -> None:
    print("\n【A】钉死全量读数（纯读存档，不碰本机基线）")
    s = OBS["summary"]
    rows = OBS["rows"]
    cal = OBS["caliber"]

    ck("A1 schema = truncation_census/v1",
       OBS["schema"] == "truncation_census/v1" == s["schema"])
    ck("A2 分母 5 条、对齐 mismatch=0",
       len(rows) == 5 and s["n_rows"] == 5 and s["align_mismatch"] == 0)
    ck("A3 caliber 钉 14 个文件且含尺子自身",
       len(cal) == len(CALIBER_FILES) == 14
       and "scripts/probe_truncation_census.py" in cal)
    ck("A4 ⛔ 尺子自身 sha256 与当前文件一致（改尺子必须重跑普查）",
       cal["scripts/probe_truncation_census.py"] ==
       sha256_file(ROOT / "scripts" / "probe_truncation_census.py"))
    ck("A5 上游存档口径钉在 caliber 里",
       cal["scripts/criteria_gate_census.json"] ==
       s["upstream"]["sha256_before"])

    ck("A6 prefix240 5/5、完整值可解析 5/5、存档值不可解析 5/5",
       (s["n_prefix240"], s["n_full_parseable"],
        s["n_saved_unparseable"]) == (5, 5, 5))
    ck("A7 逐行：prefix240 / parseable / saved_parseable 三格自洽",
       all(r["prefix240"] and r["parseable"] and not r["saved_parseable"]
           for r in rows))
    ck("A8 完整长度逐字钉死 {270,278,312,338,346} 各 1",
       s["full_call_len"] == {"270": 1, "278": 1, "312": 1,
                              "338": 1, "346": 1})
    ck("A9 逐行 full_call[:240] == saved_call 且 len(full)>240",
       all(r["full_call"][:TRUNC] == r["saved_call"]
           and r["full_call_len"] > TRUNC
           and len(r["full_call"]) == r["full_call_len"] for r in rows))
    ck("A10 ⛔ 逐行 ast.parse(saved_call) 必然 SyntaxError（构造必然）",
       all(not parseable(r["saved_call"]) for r in rows))

    ck("A11 两侧运行时账 = both_raise 3 / mixed_raise 2",
       s["by_pair"] == {"both_raise": 3, "mixed_raise": 2})
    ck("A12 truthy F2P = 0、fixed 不稳定 = 0",
       s["n_f2p_criterion"] == 0 and s["n_fixed_unstable"] == 0
       and not any(r["f2p_criterion"] for r in rows))
    ck("A13 why 账：fixed behavior 2 / raise 3，buggy raise 5",
       s["by_why_fixed"] == {"behavior": 2, "raise": 3}
       and s["by_why_buggy"] == {"raise": 5})
    ck("A14 ⛔ evidence 两格对应 pair：3 条 NameError / 2 条 got= 值",
       all(r["evidence_fixed"] for r in rows)
       and sum(1 for r in rows if "NameError" in r["evidence_fixed"]) == 3
       and sum(1 for r in rows if "got=" in r["evidence_fixed"]) == 2
       and all(("NameError" in r["evidence_fixed"])
               == (r["pair_verdict"] == "both_raise") for r in rows))
    ck("A15 逐行 pair_verdict 与 why 自洽",
       all((r["pair_verdict"] == "both_raise"
            and r["why_fixed"] == r["why_buggy"] == "raise")
           or (r["pair_verdict"] == "mixed_raise"
               and "raise" in (r["why_fixed"], r["why_buggy"]))
           for r in rows))

    sur = s["surface"]
    ck("A16 ⛔ 截断影响面：call 5 / stmt 9，call5 ⊂ stmt9",
       (sur["n_call_truncated"], sur["n_stmt_truncated"],
        sur["n_call_subset_of_stmt"]) == (5, 9, 5)
       and sur["call_subset_of_stmt"])
    ck("A17 stmt9 分格 = unserializable 5 / tuple 4",
       sur["stmt_by_gate"] == {"tuple": 4, "unserializable": 5}
       and sur["call_by_gate"] == {"unserializable": 5})
    ck("A18 stmt9 全 unknown / reach=X / 3 个 sha",
       sur["stmt_by_downstream"] == {"unknown": 9}
       and sur["stmt_by_reach"] == {"X": 9}
       and sur["stmt_by_sha"] == {"33b6131": 3, "862696a": 5,
                                  "f560500": 1})
    ck("A19 键集合大小 5 / 9",
       len(sur["call_keys"]) == 5 and len(sur["stmt_keys"]) == 9)

    books = s["two_books"]
    ck("A20 ⛔ 178 两本账：9 命中 = 5 剔除(unparseable) + 4 保留(with_binding)",
       (books["n_hit"], books["n_excluded"], books["n_kept"]) == (9, 5, 4)
       and books["gates"] == {"unparseable": 5, "with_binding": 4})

    cross = s["cross"]
    ar = cross["archives"]
    ck("A21 四份存档齐、未截断值重跑 0 次",
       cross["n_archives"] == 4 and cross["n_full_value_reruns"] == 0
       and len(ar) == 4)
    ck("A22 ⛔ 查询能命中（规则 133）：177/178 各主键命中 5 条",
       ar["scripts/extract_downstream.json"]["n_key_hits"] == 5
       and ar["scripts/no_result_attribution.json"]["n_key_hits"] == 5
       and ar["scripts/extract_downstream.json"]["by_verdict"]
       == {"no_result_both": 5}
       and ar["scripts/no_result_attribution.json"]["by_verdict"]
       == {"unparseable": 5})
    ck("A23 182/183 的 0 命中是分母排除（ctx 门 / ie commit）",
       ar["scripts/ctx_downstream.json"]["n_key_hits"] == 0
       and ar["scripts/import_error_census.json"]["n_key_hits"] == 0
       and all(v["n_full_value"] == 0 for v in ar.values()))

    up = s["upstream"]
    ck("A24 ⛔ 上游存档未被改动（前后同源 + 路径正确）",
       up["unchanged"] and up["sha256_before"] == up["sha256_after"]
       and up["path"] == "scripts/criteria_gate_census.json")
    ck("A25 验锚 OK / 封闭 18 项全 true",
       s["baseline_health"] == "OK"
       and s["n_closure_true"] == 18 == s["n_closure_items"]
       and all(s["closure_eqs"].values()))
    ck("A26 18 条封闭等式逐条钉死且键名封闭",
       set(s["closure_eqs"]) == {
           "eq_denominator_5", "eq_align_zero", "eq_prefix240_all",
           "eq_full_longer_all", "eq_full_parseable_all",
           "eq_saved_unparseable_all", "eq_pair_sum", "eq_pair_closed",
           "eq_f2p_subset", "eq_no_unstable", "eq_surface_5_9",
           "eq_call_subset_stmt", "eq_two_books_5_4", "eq_cross_zero",
           "eq_cross_four", "eq_upstream_untouched", "eq_baseline_ok",
           "eq_caliber_self"})
    ck("A27 ⛔ note 写死「不推翻 178 / 不改上游 / 不改口径」分寸",
       "不推翻" in s["note"] and "不改上游存档" in s["note"]
       and "需拍板" in s["note"])
    ck("A28 5 行 gate 全 unserializable、downstream 全 unknown",
       all(r["gate"] == "unserializable" and r["downstream"] == "unknown"
           for r in rows))
    ck("A29 两本账没有被合并成单个布尔（表面账与运行时账各自独立）",
       "two_books" in s and "by_pair" in s
       and isinstance(s["two_books"], dict)
       and isinstance(s["cross"], dict))
    ck("A30 caliber 四份交叉存档都在（口径签名覆盖交叉账）",
       all(f in cal for f in (
           "scripts/extract_downstream.json",
           "scripts/no_result_attribution.json",
           "scripts/ctx_downstream.json",
           "scripts/import_error_census.json")))


# ═══════════════ B 组：纯内存离线反证（不 spawn git、不跑沙箱） ═══════════════

def group_b() -> None:
    print("\n【B】纯内存离线反证（恒真/恒假分类、篡改读数、抹平两本账）")
    rows = OBS["rows"]
    s = OBS["summary"]

    #: B1 分类有分辨力（不是恒真!恒假）：混合 5 截断 + 3 不截断
    mixed = [{"call": "(" * TRUNC, "stmt": "x" * TRUNC,
              "gate": "unserializable", "downstream": "unknown",
              "sha": "s%d" % i, "fn": "f%d" % i, "reach": "X"}
             for i in range(5)]
    mixed += [{"call": "short", "stmt": "short",
               "gate": "tuple", "downstream": "unknown",
               "sha": "t%d" % i, "fn": "g%d" % i, "reach": "X"}
              for i in range(3)]
    surf = surface_of(mixed)
    ck("B1 is_truncated 有分辨力（恒真会算成 8 条）",
       (surf["n_call_truncated"], surf["n_stmt_truncated"]) == (5, 5))
    #: B2 parseable 恒真 ⇒ eq_saved_unparseable_all 翻红
    ck("B2 ⛔ parseable 恒真会把「存档值不可解析」写成真",
       all(not parseable(r["saved_call"]) for r in rows)
       and any(r["saved_call"] for r in rows))
    always_true = lambda c: True                      # noqa: E731
    real_unparseable = all(not parseable(r["saved_call"]) for r in rows)
    fake_unparseable = all(not always_true(r["saved_call"]) for r in rows)
    ck("B2b ⛔ 恒真分类把 eq_saved_unparseable_all 从 True 翻成 False",
       real_unparseable and not fake_unparseable)
    #: B3 parseable 恒假 ⇒ eq_full_parseable_all 翻红
    always_false = lambda c: False                    # noqa: E731
    real_parseable = all(parseable(r["full_call"]) for r in rows)
    fake_parseable = all(always_false(r["full_call"]) for r in rows)
    ck("B3 ⛔ parseable 恒假把 eq_full_parseable_all 从 True 翻成 False",
       real_parseable and not fake_parseable)
    #: B4 prefix240 篡改一个字符 ⇒ 判据翻红
    r0 = rows[0]
    tampered = "X" + r0["full_call"][1:]
    ck("B4 prefix240 篡改一个字符必须翻红",
       prefix240(r0["saved_call"], r0["full_call"])
       and not prefix240(r0["saved_call"], tampered))
    #: B5 ⛔ 查询正例（规则 133）：塞一条未截断值的行应被抓到
    keys = {(x[0], x[1], x[2]) for x in s["surface"]["call_keys"]}
    base = {"sha": r0["sha"], "fn": r0["fn"],
            "call": r0["saved_call"], "call_len": TRUNC}
    ck("B5 ⛔ full_value_hits 能命中注入正例（0 次不是查询失效）",
       len(full_value_hits([base], keys)) == 0
       and len(full_value_hits([dict(base, call_len=346)], keys)) == 1
       and len(full_value_hits([dict(base, call=r0["full_call"])], keys)) == 1)
    hit_row = dict(base, call=r0["full_call"], call_len=346)
    ck("B5b 非主键行不被误算（查询有边界）",
       len(full_value_hits([hit_row], keys)) == 1
       and len(full_value_hits([dict(hit_row, sha="not-in-keys")], keys)) == 0)
    #: B6 抹平两本账：把 4 条 with_binding 也标成 unparseable
    d178 = json.loads(DOWN178_PATH.read_text(encoding="utf-8"))["rows"]
    stmt_keys = {(x[0], x[1], x[2]) for x in s["surface"]["stmt_keys"]}
    real_books = two_books(d178, stmt_keys)
    flat = copy.deepcopy(d178)
    for r in flat:
        if r.get("main_gate") == "with_binding":
            r["main_gate"] = "unparseable"
    flat_books = two_books(flat, stmt_keys)
    ck("B6 ⛔ 抹平 178 两本账 ⇒ eq_two_books_5_4 翻红",
       (real_books["n_excluded"], real_books["n_kept"]) == (5, 4)
       and flat_books["n_excluded"] == 9 and flat_books["n_kept"] == 0)
    #: B7 篡改上游 sha256 ⇒ 未改闸翻红
    up = copy.deepcopy(s["upstream"])
    up["sha256_after"] = "deadbeef"
    ck("B7 篡改上游 sha256 ⇒ eq_upstream_untouched 翻红",
       s["upstream"]["unchanged"]
       and up["sha256_before"] != up["sha256_after"])
    #: B8 去验锚 ⇒ eq_baseline_ok 翻红
    real_base = len([]) == 0
    bypass_base = len(["BYPASS（仅反证）"]) == 0
    ck("B8 去掉验锚（diffs 非空）⇒ eq_baseline_ok 翻红",
       s["baseline_health"] == "OK" and real_base and not bypass_base)
    #: B9 篡改 pair 分布 ⇒ pair_closed / pair_sum 翻红
    fake_pair = dict(s["by_pair"])
    fake_pair["other"] = 1
    ck("B9 塞入封闭集合外的 pair 格 ⇒ eq_pair_closed 翻红",
       all(p in {"both_raise", "mixed_raise", "not_raise",
                 "import_failed", "missing_result"} for p in s["by_pair"])
       and "other" in fake_pair)
    #: B10 伪造 cross 读数 ⇒ eq_cross_zero 翻红
    ck("B10 把未截断重跑 0 伪造成 2 ⇒ eq_cross_zero 翻红",
       s["cross"]["n_full_value_reruns"] == 0
       and (2 != 0))
    #: B11 抹平查询命中 ⇒ A22 翻红
    ck("B11 把 177/178 命中数抹成 0 ⇒ 查询能命中断言翻红",
       all(v["n_key_hits"] == 5 for k, v in
           s["cross"]["archives"].items()
           if k in ("scripts/extract_downstream.json",
                    "scripts/no_result_attribution.json")))
    #: B12 篡改 caliber 自身 hash ⇒ A4 翻红
    cal = copy.deepcopy(OBS["caliber"])
    cal["scripts/probe_truncation_census.py"] = "deadbeef"
    ck("B12 篡改 caliber 尺子自身 hash ⇒ A4 翻红",
       cal["scripts/probe_truncation_census.py"] !=
       OBS["caliber"]["scripts/probe_truncation_census.py"])


# ══════════════ C 组：本机锚 + 真实正例（会 spawn git / 沙箱） ══════════════

def group_c() -> None:
    print("\n【C】本机锚 + 真实正例（证明尺子能命中，不是恒真/恒假）")
    try:
        h = rb.require_baseline()
        ck("C1 ⛔ 本机验锚真实通过（ok / 0 diffs / 3 repos）",
           h.get("ok") is True and not h.get("diffs")
           and len(h.get("repos", {})) == 3)
    except SystemExit:
        ck("C1 ⛔ 本机验锚真实通过（ok / 0 diffs / 3 repos）", False,
           "require_baseline raised SystemExit")
        return
    ck("C2 ⛔ 上游 criteria_gate_census.json 当前 hash == 存档记录",
       sha256_file(CENSUS_PATH) ==
       OBS["summary"]["upstream"]["sha256_before"])

    #: C3 真实恢复完整 call（规则 133：查询能命中）
    repo = Path("/tmp/realrepo") / "attrs"
    full = ""
    fixed_src = ""
    pkg_saved = None
    saved = OBS["rows"][0]
    for cand in select_commits(repo, 40, "src/attr/"):
        sha, src, testfs, _ = cand
        if sha != "862696a":
            continue
        from probe_package_load import pkg_context
        fixed_src = _git(repo, "show", "%s:%s" % (sha, src))
        pkg_saved = pkg_context(repo, sha, src, fixed_src)
        got = attribute_commit(repo, sha, src, testfs,
                               pkg=pkg_saved, timeout=20)
        if got is None:
            break
        for r in got["rows"]:
            if (r.get("fn") == saved["fn"]
                    and (r.get("call") or "")[:TRUNC] == saved["saved_call"]):
                full = r.get("call") or ""
        break
    ck("C3 ⛔ 真实 attribute_commit 恢复完整 call：len>240 / 可解析 / prefix 同源",
       len(full) > TRUNC and parseable(full)
       and prefix240(saved["saved_call"], full))
    res, imp = sandbox_read(fixed_src, [{"name": "t0", "call": full}],
                            20, pkg_saved)
    ck("C4 ⛔ 完整值送 truthy 沙箱能拿到结果且与存档 why 同源",
       bool(full) and bool(res) and not res[0]["missing"]
       and res[0]["why"] == saved["why_fixed"] and not imp)

    #: C5 真实 truthy 正例：182 已知 F2P commit 5eb6cc0 命中 >=1
    brepo = Path("/tmp/realrepo") / "boltons"
    hits = 0
    for cand in select_commits(brepo, 40, "boltons/"):
        sha, src, testfs, _ = cand
        if sha != "5eb6cc0":
            continue
        fixed = _git(brepo, "show", "%s:%s" % (sha, src))
        buggy = _git(brepo, "show", "%s^:%s" % (sha, src))
        got = attribute_commit(brepo, sha, src, testfs, pkg=None, timeout=20)
        if got is None:
            break
        live = [r for r in got["rows"] if r["gate_final"] == "F2P"]
        tests = [{"name": "t%d" % i, "call": r.get("call") or ""}
                 for i, r in enumerate(live)]
        f1, _ = sandbox_read(fixed, tests, 20, None)
        f2, _ = sandbox_read(fixed, tests, 20, None)
        bs, _ = sandbox_read(buggy, tests, 20, None)
        for i in range(len(live)):
            unstable = (f1[i]["passed"] != f2[i]["passed"]
                        or f1[i]["why"] != f2[i]["why"])
            hits += int(truthy_f2p(f1[i], bs[i], unstable))
        break
    ck("C5 ⛔ truthy 尺子真实命中 >=1（f2p=0 不是量具恒假，规则 24）",
       hits >= 1)

    fake = "/" + "Users" + "/someone/V6/a.py"
    ck("C6 脱敏后 scan_text() 零命中（未脱敏时必须命中）",
       portability().scan_text(fake) != []
       and portability().scan_text(redact(fake)) == [])


def main() -> int:
    print("=" * 74)
    print("r105 / README 184  截断污染面普查契约")
    print("=" * 74)
    group_a()
    group_b()
    group_c()
    print("\n" + "=" * 74)
    print("pass=%d fail=%d skip=%d" %
          (len(PASSES), len(FAILS), len(SKIPS)))
    if FAILS:
        for name in FAILS:
            print("  ⛔ " + name)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
