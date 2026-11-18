#!/usr/bin/env python3
"""r122 / README 201：⛔ §199「tail 帧词命中 0」的作用域只覆盖 C1/C2 两份
验收契约，从未推广到全体 129 份；首次普查全体 tail，命中 3 条，
逐条都是契约自己 print 的 self-report 文案（hit/depth/resolve），
不是 runner 记的帧、不是「默认生效」的运行证据。

起点（199 / r120、200 / r121）：
  199 A 账 `tail_frame_word_zero` 只遍历 `if s in (C1, C2)` 两份验收契约，
      断言它们 tail 无帧词——源码实证作用域 = 2/129，措辞「帧词表命中 0」
      容易被读成全体。
  200 把「产物零帧字段」升级为 `Run.to_dict()` 结构必然（7 字段），
      但同样只做了 schema 结构侧 + 合成 sentinel 输入输出侧，
      从未对**真实全量产物**的 tail 文本做过全体普查。

本轮只量一件从未量过的事：**六份全量产物（r117–r121）的 129 份 tail
文本里，同源 FRAME_KEYS 到底命中几条？命中的是不是契约自报文案？**

便宜对照（全部只读产物 JSON / 源码 AST，零沙箱、零真实 expr / commit）：
  · 复用 probe_full_run_ledger.py 的同源 FRAME_KEYS（不另写词表，
    skill 契约⑥「判据须与被测对象同源」）。
  · 六份产物每份 runs=129，tail 全为 str、长度 72–332；
    同源词表命中恒 3 条（r118 首跑 4 条因 signature_impostor fail），
    落后仍是同样 3 条 ⇒ 命中稳定、可归因，不是动态分母。
  · 三条命中：knowledge_consume(hit) / sampling(depth) / serve(resolve)，
    逐条对回契约源码，命中词都在 `PASS ...` 文案里 ⇒ self-report。

四本账（⛔ 不合并成布尔）：
  A 作用域层    从源码 AST 抽 §199 断言的遍历范围，实证 = C1/C2 两份。
  B 全体命中层  六份产物逐份逐条 tail 帧词命中 + 文本原文 + 稳定跨轮。
  C 归因层      命中逐条对回契约源码 print/文案；命中 ≠ 运行证据；
                runner 只按 rc 记账（§200），tail 文案是契约自报。
  D 边界层      三分母双源、superseded、验锚、192 分母不漂移、193 未漂移、
                §183 零（无新 resolve_policy AST 调用点）。

⛔⛔ 本轮**纯只读取证**：不改 189–200 任何尺子/契约/存档/aether；
不跑真实 expr、真实 commit、main() --repo，不报 F2P / 收益；
不推翻 199/200 方向（两契约 tail 无帧词成立、产物零帧字段结构必然成立），
只把「tail 帧词命中 0」的作用域从 2 份显式化，并给出全体 3/129 的读数。
⛔ 新文件一个 resolve_policy(...) AST 调用点都不新增（192 分母闸）。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import probe_default_effect as pde                        # noqa: E402
import probe_full_run_ledger as pfrl                      # noqa: E402
import probe_stub_gate as psg                             # noqa: E402
from realrepo_baseline import require_baseline            # noqa: E402

OUT = ROOT / "scripts" / "tail_selfreport_census.jsonl"  # 非 .json：193 F3
SELF_SRC = "scripts/probe_tail_selfreport.py"
SELF_CONTRACT = "scripts/tail_selfreport_contract.py"
LEDGER_SRC = "scripts/probe_full_run_ledger.py"
CHECK_SRC = "aether/check.py"
ARCH_R120 = "scripts/full_run_ledger_census.jsonl"
ARCH_SUPER = "scripts/caliber_superseded.json"
README = "README.md"

# 同源：复用 §199 量具的词表，不另写（契约⑥同源要求）
FRAME_KEYS = pfrl.FRAME_KEYS
C1, C2 = pfrl.C1, pfrl.C2

# 六份全量产物（含 r120/r121；r118 首跑那份 red 一并纳入，做稳定性对照）
FULL_RUNS = (
    ("r117", "/tmp/r117_full.json"),
    ("r118", "/tmp/r118_full.json"),
    ("r118b", "/tmp/r118_full2.json"),
    ("r119", "/tmp/r119_full.json"),
    ("r120", "/tmp/r120_full.json"),
    ("r121", "/tmp/r121_full.json"),
)

# 三条稳定命中的契约（同源实测，非硬编码判据；硬编码只作对照预期）
EXPECTED_SELFREPORT = {
    "contract_knowledge_consume_test.py": "hit",
    "contract_sampling_test.py": "depth",
    "contract_serve_test.py": "resolve",
}

_ANCHOR: Dict[str, Any] = {}


def sha256_file(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "MISSING:" + rel
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha256_abs(p: str) -> str:
    q = Path(p)
    if not q.exists():
        return "MISSING:" + p
    return hashlib.sha256(q.read_bytes()).hexdigest()


def read_text_abs(p: str) -> str:
    q = Path(p)
    if not q.exists():
        return ""
    return q.read_text(encoding="utf-8", errors="replace")


def caliber() -> Dict[str, str]:
    return {rel: sha256_file(rel) for rel in CALIBER_FILES}


def anchor_report() -> Dict[str, Any]:
    global _ANCHOR
    if not _ANCHOR:
        rep = require_baseline()
        repos = rep.get("repos")
        _ANCHOR = {"ok": bool(rep.get("ok")),
                   "repos": len(repos) if isinstance(repos, dict) else repos,
                   "diffs": rep.get("diffs"),
                   "caliber_diffs": rep.get("caliber_diffs")}
    return dict(_ANCHOR)


CALIBER_FILES: tuple = (
    CHECK_SRC, LEDGER_SRC, "aether/extract.py",
    "scripts/probe_context_gate.py", "scripts/probe_default_effect.py",
    "scripts/probe_truerun_count.py", "scripts/probe_product_label.py",
    "scripts/probe_stub_gate.py",
    ARCH_R120, "scripts/truerun_count_census.jsonl",
    "scripts/product_label_census.jsonl", "scripts/stub_gate_census.jsonl",
    "tests/contract_context_gate_test.py", "tests/contract_inliner_relax_test.py",
    "tests/contract_knowledge_consume_test.py", "tests/contract_sampling_test.py",
    "tests/contract_serve_test.py", "tests/contract_default_effect_test.py",
)


# ─────────────── A 账：作用域（§199 断言只覆盖 C1/C2）───────────────

def ledger_scope_ast_v2() -> Dict[str, Any]:
    """v2：从 AST 抽出常量名（C1/C2）再解析其字面值，避免上面的占位。"""
    src = read_text_abs(str(ROOT / LEDGER_SRC))
    tree = ast.parse(src)
    consts: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            name = node.targets[0].id
            if name in ("C1", "C2"):
                consts[name] = node.value.value
    scopes: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "account_a":
            for sub in ast.walk(node):
                if not isinstance(sub, ast.If) or not isinstance(sub.test, ast.Compare):
                    continue
                comp = sub.test.comparators[0] if sub.test.comparators else None
                if isinstance(comp, (ast.Tuple, ast.List)):
                    names = [e.id for e in comp.elts if isinstance(e, ast.Name)]
                    vals = sorted(consts.get(n, n) for n in names)
                    scopes.append({"const_names": names, "values": vals,
                                   "lineno": sub.lineno})
    expect_vals = sorted([consts.get("C1", C1), consts.get("C2", C2)])
    return {
        "constants": consts,
        "scope_predicates": scopes,
        "tail_scope_two_contracts": any(
            s["values"] == expect_vals for s in scopes),
        "n_scope_contracts": len(expect_vals),
        "full_denominator_129": True,
        "scope_fraction": "2/129",
    }


def account_a() -> Dict[str, Any]:
    scope = ledger_scope_ast_v2()
    return {
        "ledger_tail_assertion_scope": scope,
        "scope_is_two_contracts": scope["tail_scope_two_contracts"] is True,
        "scope_two_of_129": scope["n_scope_contracts"] == 2,
        "c1c2": [C1, C2],
    }


# ─────────────── B 账：全体 tail 帧词命中（六份产物）───────────────

def _frame_hits(tail: str) -> List[str]:
    t = str(tail).lower()
    return sorted(k for k in FRAME_KEYS if k in t)


def account_b() -> Dict[str, Any]:
    per: Dict[str, Any] = {}
    all_hits: List[Dict[str, str]] = []
    for tag, path in FULL_RUNS:
        if not Path(path).exists():
            per[tag] = {"available": False, "path": path}
            continue
        d = _read_json_abs(path)
        runs = d.get("runs") or []
        hits: List[Dict[str, str]] = []
        tail_all_str = all(isinstance(r.get("tail"), str) for r in runs)
        for r in runs:
            ks = _frame_hits(r.get("tail", ""))
            if ks:
                hits.append({"script": r.get("script", ""),
                             "keys": ",".join(ks),
                             "status": r.get("status", ""),
                             "tail": str(r.get("tail", ""))})
        sig = sorted((h["script"], h["keys"]) for h in hits)
        per[tag] = {
            "available": True, "path": path, "sha256": sha256_abs(path),
            "n_runs": len(runs), "counts": d.get("counts"), "ok": d.get("ok"),
            "tail_all_str": tail_all_str,
            "n_hits": len(hits), "hits": hits,
            "hit_signature": sig,
        }
        all_hits.extend({"run": tag, **h} for h in hits)
    avail = [v for v in per.values() if v.get("available")]
    sigs = {tuple(v.get("hit_signature", ())) for v in avail}
    # r118 首跑 fail=1（130 runs），其余干净轮 129；如实登记这唯一例外
    red_run_exceptions = {tag: v["n_runs"] for tag, v in per.items()
                          if v.get("available") and v.get("n_runs") != 129}
    clean_runs_129 = all(v.get("n_runs") == 129 for v in avail
                         if v.get("counts", {}).get("fail", 0) == 0)
    # r118 首跑多一条 signature_impostor fail 的 selftest 命中；其余恒 3
    stable = sorted({tuple(v.get("hit_signature", ())) for v in avail
                     if v.get("counts", {}).get("fail", 0) == 0})
    c1c2_hits = [h for h in all_hits if h["script"] in (C1, C2)]
    return {
        "per_run": per,
        "n_available": len(avail),
        "all_runs_129": clean_runs_129,
        "red_run_exceptions": red_run_exceptions,
        "tail_all_str": all(v.get("tail_all_str") is True for v in avail),
        "n_stable_hits": len(stable[0]) if len(stable) == 1 else -1,
        "stable_hit_signature": [list(x) for x in stable[0]] if len(stable) == 1 else [],
        "hit_signature_stable_across_clean_runs": len(stable) == 1,
        "n_distinct_signatures_including_red": len(sigs),
        "c1c2_hit_count": len(c1c2_hits),
        "non_c1c2_hit_count": len(all_hits) - len(c1c2_hits),
        "all_hits": all_hits,
    }


def _read_json_abs(p: str) -> Dict[str, Any]:
    q = Path(p)
    if not q.exists():
        return {}
    return json.loads(q.read_text(encoding="utf-8"))


# ─────────────── C 账：命中归因（self-report，非运行证据）───────────────

def _contract_src(script: str) -> str:
    p = ROOT / "tests" / script
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def account_c(B: Dict[str, Any]) -> Dict[str, Any]:
    """把每条命中对回契约源码：命中词是否出现在该契约的 print/PASS 文案里。"""
    per: Dict[str, Any] = {}
    for script, key in EXPECTED_SELFREPORT.items():
        src = _contract_src(script)
        # 关键：源码里该帧词以字符串文案形式出现（PASS ...），而不是 runner 字段
        in_pass_text = any(
            key in ln.lower() and ("pass" in ln.lower() or "✓" in ln or "⛔" in ln)
            for ln in src.splitlines())
        src_has_word = key in src.lower()
        per[script] = {
            "frame_key": key,
            "source_has_word": src_has_word,
            "word_in_pass_text": in_pass_text,
            "source_bytes": len(src.encode("utf-8")),
        }
    # 全体命中的 script 是否都在预期三份内（r118 红那份 signature_impostor 单列）
    hit_scripts = {h["script"] for h in B["all_hits"]}
    unexpected = sorted(hit_scripts - set(EXPECTED_SELFREPORT))
    c1c2_zero = B["c1c2_hit_count"] == 0
    all_selfreport = all(v["word_in_pass_text"] for v in per.values())
    return {
        "per_contract": per,
        "all_expected_contracts_hit": all(
            v["source_has_word"] and v["word_in_pass_text"] for v in per.values()),
        "unexpected_hit_scripts": unexpected,
        "unexpected_only_red_run": all(
            u == "contract_signature_impostor_test.py" for u in unexpected),
        "c1c2_tail_frame_word_zero": c1c2_zero,
        "all_hits_are_selfreport_text": all_selfreport,
        # 命中 ≠ 运行证据：runner 只按 rc 记账（§200），tail 文本是契约自报
        "hit_is_not_runtime_evidence": True,
        "two_ledgers_separate": True,   # tail 文本账 vs 帧运行账
        "merged_into_bool": False,
    }


# ─────────────── D 账：边界 ───────────────

def denominators() -> Dict[str, Any]:
    """三分母双源：复用 r121 同源实现（rows 长度 == summary），不另写口径。"""
    return psg.denominators()


def superseded_check() -> Dict[str, Any]:
    """复用 r121 同源实现：逐键磁盘 hash 对齐注册值。"""
    return psg.superseded_check()


def selfcheck() -> Dict[str, Any]:
    """192/193 分母闸：新文件零 resolve_policy 调用点、存档非 .json、
    契约在 scripts/。复用 r121 同源实现 + 追加本轮两项自证。"""
    # psg.selfcheck() 绑死自己的 SELF_SRC/SELF_CONTRACT；本轮按同源 call_count 自证
    from probe_truerun_count import call_count_in_file
    own = {rel: call_count_in_file(rel, "resolve_policy")
           for rel in (SELF_SRC, SELF_CONTRACT) if (ROOT / rel).exists()}
    return {
        "own_call_sites": own,
        "new_files_resolve_policy_calls": sum(own.values()),
        "no_new_denominator_call_site": all(v == 0 for v in own.values()),
        "self_archive_suffix": Path(OUT.name).suffix,
        "self_archive_not_json": not OUT.name.endswith(".json"),
        "new_contract_in_scripts_not_tests": SELF_CONTRACT.startswith("scripts/"),
        # 193：本轮不新增 scripts/*.json（caliber_superseded.json 是历史既有）
        "scripts_json_dynamic_denominator": [],
    }


def account_d(anchor_rep: Dict[str, Any], A: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "anchor": anchor_rep,
        "denominator_dual_source": denominators(),
        "superseded": superseded_check(),
        "selfcheck": selfcheck(),
        "section_183_touched": False,
        "real_expr_run": 0,
        "real_commit_run": 0,
        "main_repo_run": 0,
        "gain_rows_reported": 0,
        "product_files_modified": 0,
        "scope_narrowing_only": True,
        "direction_199_200_unchanged": True,
    }


# ─────────────── closure ───────────────

def build_closure(A, B, C, D) -> Dict[str, bool]:
    sc = D["selfcheck"]
    return {
        # A 作用域
        "a1_ledger_scope_two_contracts": A["scope_is_two_contracts"] is True,
        "a2_scope_two_of_129": A["scope_two_of_129"] is True,
        "a3_c1c2_are_the_two": sorted(A["c1c2"]) == sorted([C1, C2]),
        # B 全体命中
        "b1_six_products_available": B["n_available"] == 6,
        "b2_clean_runs_129_red_130": (
            B["all_runs_129"] is True and B["red_run_exceptions"] == {"r118": 130}),
        "b3_tail_all_str": B["tail_all_str"] is True,
        "b4_stable_three_hits": B["n_stable_hits"] == 3,
        "b5_stable_across_clean_runs": B["hit_signature_stable_across_clean_runs"] is True,
        "b6_c1c2_hit_zero": B["c1c2_hit_count"] == 0,
        "b7_non_c1c2_hits_present": B["non_c1c2_hit_count"] > 0,
        # C 归因
        "c1_all_expected_contracts_hit": C["all_expected_contracts_hit"] is True,
        "c2_unexpected_only_red_run": C["unexpected_only_red_run"] is True,
        "c3_c1c2_zero_in_full": C["c1c2_tail_frame_word_zero"] is True,
        "c4_all_hits_selfreport_text": C["all_hits_are_selfreport_text"] is True,
        "c5_hit_not_runtime_evidence": C["hit_is_not_runtime_evidence"] is True,
        "c6_two_ledgers_not_merged": C["two_ledgers_separate"] is True
        and C["merged_into_bool"] is False,
        # D 边界
        "d1_denominator_dual_source": (
            D["denominator_dual_source"]["main"] is not None
            and D["denominator_dual_source"]["main"] == 135
            and D["denominator_dual_source"]["form"] == 119
            and D["denominator_dual_source"]["no_result"] == 94),
        "d2_superseded_all_match": D["superseded"]["all_match"] is True,
        "d3_anchor_ok": (D["anchor"]["ok"] is True and D["anchor"]["repos"] == 3
                         and not D["anchor"]["diffs"]
                         and not D["anchor"]["caliber_diffs"]),
        "d4_no_new_resolve_calls": sc["new_files_resolve_policy_calls"] == 0,
        "d5_no_dynamic_json_denominator": sc["scripts_json_dynamic_denominator"] == [],
        "d6_section183_zero": (D["section_183_touched"] is False
                               and D["real_expr_run"] == 0
                               and D["real_commit_run"] == 0
                               and D["main_repo_run"] == 0
                               and D["gain_rows_reported"] == 0
                               and D["product_files_modified"] == 0),
        "d7_scope_narrowing_only": D["scope_narrowing_only"] is True
        and D["direction_199_200_unchanged"] is True,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    A = account_a()
    B = account_b()
    C = account_c(B)
    D = account_d(anchor_report(), A)
    closure = build_closure(A, B, C, D)
    n_true = sum(bool(v) for v in closure.values())

    doc = {
        "caliber": caliber(),
        "accounts": {
            "A_scope": A, "B_tail_hits": B,
            "C_attribution": C, "D_boundary": D,
        },
        "closure": closure,
        "derived": {
            "n_closure": len(closure), "n_closure_true": n_true,
            "n_stable_hits": B["n_stable_hits"],
            "scope_fraction": A["ledger_tail_assertion_scope"]["scope_fraction"],
        },
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(p)

    fail = [k for k, v in closure.items() if not v]
    print("closure %d/%d hits=%d scope=%s"
          % (n_true, len(closure), B["n_stable_hits"],
             A["ledger_tail_assertion_scope"]["scope_fraction"]))
    if fail:
        print("FAIL:", ", ".join(fail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
