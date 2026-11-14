#!/usr/bin/env python3
"""r112 / README 191：190 的「两口径」只切了 4 位 policy 中的 1 位（纯只读取证）。

═══ 起点：189 说「四条开关逐条有牙 4/4 / --strict 可逐字复算」═══

189（r110）把四条自限做成 `RelaxPolicy` 四个布尔位，用
6 策略 × 6 合成样本 = 36 行钉出 `switch_teeth 4/4`，并声明
「低层规则仍严格 ⇒ `--strict` 逐字回 `0000`，旧读数可复算」。
190（r111）把 177 主账 135 条逐条同源重放，报「两口径
verified 60→60 / counts_disc 变化 0 / ledger 名字全变 ≠ 新采纳」。

⛔ 本轮先查一件从未量过的事：**190 的「两口径」到底切了几位开关**。

便宜对照（/tmp/r112_cheap.py，纯只读）读数：

  · 四位在**产品源码**里各有读取点：
        cross_scope    -> merge_bindings
        first_rhs      -> bindings_for
        allow_unstable -> main / probe_inliner_relax.py::evaluate_case
        no_gold_gate   -> adopt_probe
  · 190 B 账调用链只有 `scoped_assertions` / `classify_probe` /
    `adopt_probe`，其中**只有 `adopt_probe` 接 policy** ⇒ 触达 1/4 位；
  · 另三位所需的整段 `test_src` / 绑定 `envs` / 两遍稳定性读数，
    在 177 主账 135 行里 **0/135 可得**（字段全部不存在）。

⇒ ⛔ 分寸：三位在**产品主流程里真的有读取点**，只是 190 这把
   **离线重放尺子**没触达；输入不可得 = 「这一轮测不了」，不是
   「没有差异」。这是**尺子的代表性边界**，不是产品洞（与 190 的
   「不是产品洞」同向），也不推翻 189 的语义。

═══ 四本账（⛔ 不合并成布尔）═══

  A 作用点  AST 同源抽 4 位字段 / 各自读取函数 / 190 调用链 /
            接 policy 的函数 / adopt_probe 内实读的字段 ⇒ 1/4；
            带 4/4 正例 + 0 反例，证明抽取器不是恒真也不是恒空
  B 可得性  135 行逐行查另三位所需输入（键名从产品函数签名 AST 派生）；
            按 k / verdict 分层；189 的 6 个合成 case 输入完备性
            （CASES 在量具源码里，存档只记 id）
  C 逐行    两口径 135 行逐行 outcome 对拍（补 190 只报聚合的粒度，
            按 r110「聚合数相同 ≠ 开关恒真」纪律）；同源对拍 190 B 账；
            4 类合成行注入证明「翻转探测器」不是死代码
  D 边界    aether/ 与 189/190 存档零回写、superseded 登记在位、
            三分母分开、尺子零沙箱自检、验锚 OK 3/3

⛔⛔ 本轮**只取证报告**：不改 `adopt_probe()` / `RelaxPolicy` /
  `probe_context_gate.py` / 任一旧存档。补一条真实主账上的
  四位开关读数需要整段 test_src ⇒ 属改判据语义（第 34 条），
  另一次需拍板，本轮不动。

跑法：python scripts/probe_policy_reach.py
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import inspect
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]          # ⛔ 第 99 条：根从 __file__ 推导
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from realrepo_baseline import require_baseline                  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # ⛔ NamedTuple / dataclass 的 sys.modules 坑
    spec.loader.exec_module(mod)
    return mod


CG = _load("pcg_r112", "scripts/probe_context_gate.py")
E = _load("aether_extract_r112", "aether/extract.py")

OUT = ROOT / "scripts" / "policy_reach_census.json"
ARCH_MAIN = ROOT / "scripts" / "extract_downstream.json"       # 177 主账 135
ARCH_190 = ROOT / "scripts" / "nogold_gap_census.json"         # 190 B 账
ARCH_189 = ROOT / "scripts" / "inliner_relax_census.json"      # 189 A 账 36
ARCH_FORM = ROOT / "scripts" / "form_shape_attribution.json"   # 179 form 119
ARCH_NORESULT = ROOT / "scripts" / "no_result_attribution.json"  # 178 94
CG_SRC = ROOT / "scripts" / "probe_context_gate.py"
INLINER_SRC = ROOT / "scripts" / "probe_inliner_relax.py"
NOGOLD_SRC = ROOT / "scripts" / "probe_nogold_gap.py"

#: ⛔ 新口径另存（v1）：含尺子自身、含量具 hash、含 189/190 存档 hash。
#:    旧存档零回写，跨口径读数不可比（eval-standard-anchoring 契约①）。
CALIBER_FILES: Tuple[str, ...] = (
    "aether/extract.py",
    "scripts/probe_context_gate.py",
    "scripts/probe_inliner_relax.py",
    "scripts/probe_nogold_gap.py",
    "scripts/probe_policy_reach.py",
    "scripts/inliner_relax_census.json",
    "scripts/nogold_gap_census.json",
    "scripts/extract_downstream.json",
    "scripts/form_shape_attribution.json",
    "scripts/no_result_attribution.json",
    "scripts/caliber_superseded.json",
    "scripts/realrepo_baseline.json",
)


def sha256_file(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def caliber() -> Dict[str, str]:
    return {rel: sha256_file(rel) for rel in CALIBER_FILES}


def parse(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


# ─────────────────────────── A 账：作用点 ───────────────────────────
def policy_fields_from_source() -> List[str]:
    """⛔ 同源：从 `class RelaxPolicy` 的 AnnAssign 抽四位字段名，不手写词表。"""
    for n in ast.walk(parse("scripts/probe_context_gate.py")):
        if isinstance(n, ast.ClassDef) and n.name == "RelaxPolicy":
            return [b.target.id for b in n.body
                    if isinstance(b, ast.AnnAssign)
                    and isinstance(b.target, ast.Name)]
    return []


def policy_reads(tree: ast.AST, fields: Tuple[str, ...]) -> Dict[str, List[str]]:
    """同源抽取器：抽 `policy.<field>` 读取所在函数名。

    ⛔ 同一个抽取器既跑产品源码、也跑正例/反例合成片段，
       这样「零命中」才不是「查询恒空」（第 133 条）。
    """
    out: Dict[str, List[str]] = {f: [] for f in fields}
    scope = "module"
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = n.name
            for sub in ast.walk(n):
                if (isinstance(sub, ast.Attribute) and sub.attr in out
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id == "policy"
                        and scope not in out[sub.attr]):
                    out[sub.attr].append(scope)
    return out


def account_a() -> Dict[str, Any]:
    fields = policy_fields_from_source()
    ft = tuple(fields)
    readers_cg = policy_reads(parse("scripts/probe_context_gate.py"), ft)
    readers_inl = policy_reads(parse("scripts/probe_inliner_relax.py"), ft)
    readers = {f: sorted(set(readers_cg[f] + readers_inl[f])) for f in fields}

    # 190 B 账 account_b() 调用链（AST 同源，不手写函数名）
    calls: List[str] = []
    for n in ast.walk(parse("scripts/probe_nogold_gap.py")):
        if isinstance(n, ast.FunctionDef) and n.name == "account_b":
            for sub in ast.walk(n):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and isinstance(sub.func.value, ast.Name)
                        and sub.func.value.id == "CG"):
                    if sub.func.attr not in calls:
                        calls.append(sub.func.attr)
    calls = sorted(calls)

    takes_policy = []
    for name in calls:
        f = getattr(CG, name, None)
        if f is not None and "policy" in inspect.signature(f).parameters:
            takes_policy.append(name)

    # adopt_probe 函数体内实读哪些位
    adopted_fields: List[str] = []
    for n in ast.walk(parse("scripts/probe_context_gate.py")):
        if isinstance(n, ast.FunctionDef) and n.name in takes_policy:
            for sub in ast.walk(n):
                if (isinstance(sub, ast.Attribute) and sub.attr in fields
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id == "policy"
                        and sub.attr not in adopted_fields):
                    adopted_fields.append(sub.attr)

    # ⛔ 正例 / 反例：同一抽取器喂合成源码，证明它能命中 4/4、也能返回 0
    pos_src = "\n".join(
        "def f_%s(policy):\n    return policy.%s\n" % (f, f) for f in fields)
    neg_src = "def g(policy):\n    return 1\n"
    pos = policy_reads(ast.parse(pos_src), ft)
    neg = policy_reads(ast.parse(neg_src), ft)
    pos_hit = sum(1 for f in fields if pos[f])
    neg_hit = sum(len(v) for v in neg.values())

    missing = [f for f in fields if f not in adopted_fields]
    return {
        "source_fields": "scripts/probe_context_gate.py::RelaxPolicy (AST)",
        "fields": fields,
        "n_fields": len(fields),
        "readers": readers,
        "n_fields_with_reader": sum(1 for f in fields if readers[f]),
        "b190_calls": calls,
        "b190_calls_taking_policy": takes_policy,
        "fields_read_by_b190": adopted_fields,
        "n_reachable": len(adopted_fields),
        "reach_ratio": "%d/%d" % (len(adopted_fields), len(fields)),
        "unreachable_fields": missing,
        "extractor_positive_hit": pos_hit,
        "extractor_positive_total": len(fields),
        "extractor_negative_hit": neg_hit,
        "boundary": "A 账纯 AST，零沙箱、零真实 expr、零真实 commit",
    }


# ─────────────────────────── B 账：输入可得性 ───────────────────────────
def required_input_keys() -> List[str]:
    """⛔ 同源：另三位所需输入键名从产品函数签名 / 量具 case 下标派生。

    `bindings_for(test_src, policy)` 要 test_src；
    `merge_bindings(envs, fn, policy)` 要 envs；
    `evaluate_case` 读 `case["unstable_runs"]` 要两遍稳定性读数。
    """
    keys: List[str] = []
    cg_tree = parse("scripts/probe_context_gate.py")
    for n in ast.walk(cg_tree):
        if isinstance(n, ast.FunctionDef) and n.name in ("bindings_for", "merge_bindings"):
            for a in n.args.args:
                if a.arg in ("test_src", "envs") and a.arg not in keys:
                    keys.append(a.arg)
    inl_tree = parse("scripts/probe_inliner_relax.py")
    for n in ast.walk(inl_tree):
        if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                and n.value.id == "case" and isinstance(n.slice, ast.Constant)
                and isinstance(n.slice.value, str)
                and "unstable" in n.slice.value
                and n.slice.value not in keys):
            keys.append(n.slice.value)
    return sorted(keys)


def cases_from_source() -> List[Dict[str, Any]]:
    """⛔ 同源：从量具源码的 `CASES` 赋值 literal_eval，不执行量具、不手写 case。"""
    tree = parse("scripts/probe_inliner_relax.py")
    for n in ast.walk(tree):
        if (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                and n.target.id == "CASES"):
            return list(ast.literal_eval(n.value))
    return []


def account_b(rows_a: List[Dict[str, Any]]) -> Dict[str, Any]:
    main = json.loads(ARCH_MAIN.read_text(encoding="utf-8"))
    rows = main["rows"]
    keys = required_input_keys()
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(rows):
        present = {k: (k in r) for k in keys}
        out.append({
            "i": i, "verdict": r.get("verdict"), "gate": r.get("gate"),
            "k": rows_a[i]["k"] if i < len(rows_a) else "",
            "present": present,
            "n_present": sum(present.values()),
        })
    by_k = Counter(r["k"] for r in out)
    by_verdict = Counter(str(r["verdict"]) for r in out)
    n_have_all = sum(r["n_present"] == len(keys) for r in out)

    cases = cases_from_source()
    case_keys = sorted({k for c in cases for k in c})
    n_case_src = sum("src" in c for c in cases)
    n_case_unstable = sum(c.get("unstable_runs") is not None for c in cases)
    d189 = json.loads(ARCH_189.read_text(encoding="utf-8"))
    archived_ids = d189["accounts"]["A_policy_matrix"].get("cases") or []

    return {
        "source": "scripts/extract_downstream.json (177 主账)",
        "required_input_keys": keys,
        "n_required_keys": len(keys),
        "rows": out,
        "n_rows": len(out),
        "n_rows_with_any_input": sum(r["n_present"] > 0 for r in out),
        "n_rows_with_all_inputs": n_have_all,
        "per_key_present": {k: sum(r["present"][k] for r in out) for k in keys},
        "by_k": dict(sorted(by_k.items())),
        "by_verdict": dict(sorted(by_verdict.items())),
        "by_k_sum": sum(by_k.values()),
        "by_verdict_sum": sum(by_verdict.values()),
        "cases_189": {
            "source": "scripts/probe_inliner_relax.py::CASES (AST literal_eval)",
            "n_cases": len(cases),
            "union_keys": case_keys,
            "n_with_src": n_case_src,
            "n_with_unstable_runs": n_case_unstable,
            "archived_case_ids": archived_ids,
            "n_archived_ids": len(archived_ids),
            "archive_has_src": 0,
            "note": "6 个合成 case 的完整 src 只在量具源码里，189 存档只记 id",
        },
        "boundary": "B 账只读存档字段 + AST，零沙箱；0/135 是输入不可得，不是『没有差异』",
    }


# ─────────────────────────── C 账：逐行 outcome 对拍 ───────────────────────────
def replay_rows() -> List[Dict[str, Any]]:
    """⛔ 与 190 B 账同源代码路径：`got` 只取存档已记录值，零沙箱。"""
    main = json.loads(ARCH_MAIN.read_text(encoding="utf-8"))
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(main["rows"]):
        stmt = r.get("stmt") or ""
        try:
            a = E.scoped_assertions(stmt)
        except SyntaxError:
            a = []
        act, exp, _fn = a[0] if a else ("", "", "")
        got = r.get("got_fixed")
        k = CG.classify_probe(act, exp, got) if a else "UNSPLITTABLE"
        unstable = (r.get("gate") == "unstable")
        disc = bool(r.get("truthy_f2p"))
        s = CG.adopt_probe(k, unstable, CG.STRICT, disc)
        x = CG.adopt_probe(k, unstable, CG.RELAXED, disc)
        out.append({
            "i": i, "k": k, "unstable": unstable, "disc": disc,
            "strict": {"ledger": s[0], "verified": s[1], "counts_disc": s[2]},
            "relaxed": {"ledger": x[0], "verified": x[1], "counts_disc": x[2]},
            "ledger_changed": s[0] != x[0],
            "verified_flip": s[1] != x[1],
            "counts_disc_flip": s[2] != x[2],
        })
    return out


def flip_detector_cases() -> List[Dict[str, Any]]:
    """⛔ 反证组：喂 4 类合成行，证明「逐行翻转探测器」不是死代码。"""
    seeds = [
        ("ok", False, False), ("mismatch", False, True),
        ("none", False, False), ("ok_nogold", True, True),
    ]
    out = []
    for k, unstable, disc in seeds:
        s = CG.adopt_probe(k, unstable, CG.STRICT, disc)
        x = CG.adopt_probe(k, unstable, CG.RELAXED, disc)
        flips = {
            "ledger": s[0] != x[0],
            "verified": s[1] != x[1],
            "counts_disc": s[2] != x[2],
        }
        out.append({
            "k": k, "unstable": unstable, "disc": disc,
            "strict": {"ledger": s[0], "verified": s[1], "counts_disc": s[2]},
            "relaxed": {"ledger": x[0], "verified": x[1], "counts_disc": x[2]},
            "flips": flips, "n_flips": sum(flips.values()),
        })
    return out


def account_c() -> Dict[str, Any]:
    rows = replay_rows()
    d190 = json.loads(ARCH_190.read_text(encoding="utf-8"))
    b190 = d190["accounts"]["B_main_replay"]["rows"]

    # 同源对拍：135 行 (k, strict_ledger, relaxed_ledger) 与 190 B 账逐行一致
    same = 0
    for r, o in zip(rows, b190):
        if (r["k"] == o["k"]
                and r["strict"]["ledger"] == o["strict_ledger"]
                and r["relaxed"]["ledger"] == o["relaxed_ledger"]):
            same += 1

    by_group: Dict[str, Dict[str, int]] = {}
    for r in rows:
        key = "%s|unstable=%s|disc=%s" % (r["k"], r["unstable"], r["disc"])
        g = by_group.setdefault(key, {"n": 0, "ledger_changed": 0,
                                     "verified_flip": 0, "counts_disc_flip": 0})
        g["n"] += 1
        g["ledger_changed"] += int(r["ledger_changed"])
        g["verified_flip"] += int(r["verified_flip"])
        g["counts_disc_flip"] += int(r["counts_disc_flip"])

    teeth = flip_detector_cases()
    return {
        "source": "177 主账 135 条同源重放（产品 scoped_assertions/classify_probe/adopt_probe）",
        "rows": rows,
        "n_rows": len(rows),
        "n_ledger_changed": sum(r["ledger_changed"] for r in rows),
        "n_verified_flip": sum(r["verified_flip"] for r in rows),
        "n_counts_disc_flip": sum(r["counts_disc_flip"] for r in rows),
        "by_group": dict(sorted(by_group.items())),
        "n_groups": len(by_group),
        "group_sum": sum(g["n"] for g in by_group.values()),
        "same_row_with_190": same,
        "n_190_rows": len(b190),
        "flip_detector_cases": teeth,
        "flip_detector_teeth": sum(t["n_flips"] > 0 for t in teeth),
        "flip_detector_total": len(teeth),
        "note": ("逐行 verified/counts_disc 翻转 0 只说明『这一位 + 这批 k 分布』"
                 "下构造必然；反证组证明探测器能检出翻转，⛔ 不外推到 4 位开关"),
        "boundary": "C 账零沙箱、零真实 expr、零真实 commit；got 只用存档已记录值",
    }


# ─────────────────────────── D 账：边界 ───────────────────────────
def self_sandbox_free() -> Dict[str, Any]:
    """⛔ 尺子自身 AST 自检：不硬编码 0 当证据（188 C 账做法）。"""
    tree = parse("scripts/probe_policy_reach.py")
    banned: List[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id in ("eval", "exec", "compile"):
            banned.append("call:" + n.func.id)
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            names = ([a.name for a in n.names] if isinstance(n, ast.Import)
                     else [((n.module or "") + "." + a.name) for a in n.names])
            for nm in names:
                if "environment" in nm or "subprocess" in nm:
                    banned.append("import:" + nm)
    return {"banned": banned, "n_banned": len(banned),
            "checked_own_source": True}


def account_d(anchor: Dict[str, Any]) -> Dict[str, Any]:
    sup = json.loads((ROOT / "scripts/caliber_superseded.json").read_text(encoding="utf-8"))
    sup_ok = 0
    for rel, want in (sup.get("archive_sha256_at_registration") or {}).items():
        if (ROOT / rel).exists() and sha256_file(rel) == want:
            sup_ok += 1
    d179 = json.loads(ARCH_FORM.read_text(encoding="utf-8"))
    d178 = json.loads(ARCH_NORESULT.read_text(encoding="utf-8"))
    n_form = len(d179["rows"])
    n_form_summary = d179["summary"]["n_rows"]
    n_noresult = len(d178["rows"])
    n_noresult_summary = d178["summary"]["n_rows"]
    n_main = len(json.loads(ARCH_MAIN.read_text(encoding="utf-8"))["rows"])
    free = self_sandbox_free()
    return {
        "policy_default": CG.resolve_policy(False).signature(),
        "policy_strict": CG.resolve_policy(True).signature(),
        "superseded_archives": sup.get("archives"),
        "superseded_sha_matches": sup_ok,
        "superseded_total": len(sup.get("archive_sha256_at_registration") or {}),
        "superseded_new_sha_on_disk": sha256_file(sup["file"]),
        "superseded_new_sha_registered": sup["new_sha256"],
        "aether_extract_sha": sha256_file("aether/extract.py"),
        "denominators": {"main_135": n_main, "form_119": n_form,
                         "no_result_94": n_noresult},
        "denominator_dual_source": {"form_rows": n_form,
                                    "form_summary": n_form_summary,
                                    "no_result_rows": n_noresult,
                                    "no_result_summary": n_noresult_summary},
        "denominators_kept_separate": len({n_main, n_form, n_noresult}) == 3,
        "n_forbidden_runtime_calls": free["n_banned"],
        "real_expr_runs": 0, "real_commit_runs": 0, "f2p_runs": 0,
        "gain_reported": 0,
        "sandbox_selfcheck": free,
        "anchor": anchor,
        "boundary": ("D 账只做 hash / 登记 / 分母 / 自检；不跑真实 expr、不跑真实 commit、"
                     "不报收益；189/190 存档与 aether/ 零回写"),
    }


# ─────────────────────────── 汇总 ───────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--no-anchor", action="store_true",
                    help="仅供同进程分层反证复用，跳过 3 仓库 census")
    args = ap.parse_args()

    A = account_a()
    C = account_c()
    if args.no_anchor:
        anchor_rep = {"skipped": True, "reason": "--no-anchor (分层反证复用)"}
    else:
        anchor = require_baseline()
        # ⛔ 缺失键不用默认值兜底（eval-standard-anchoring 契约①）
        anchor_rep = {
            "ok": bool(anchor.get("ok")),
            "repos": len(anchor.get("repos") or {}),
            "diffs": list(anchor.get("diffs") or []),
            "caliber_diffs": list(anchor.get("caliber_diffs") or []),
        }
    B = account_b(C["rows"])
    D = account_d(anchor_rep)

    fields = A["fields"]
    Bk = B["required_input_keys"]
    closure = {
        "a1_four_policy_fields": A["n_fields"] == 4 and len(set(fields)) == 4,
        "a2_all_fields_have_reader": A["n_fields_with_reader"] == 4,
        "a3_b190_calls_are_three": A["b190_calls"] == ["adopt_probe", "classify_probe", "scoped_assertions"],
        "a4_only_adopt_takes_policy": A["b190_calls_taking_policy"] == ["adopt_probe"],
        "a5_b190_reads_one_field": A["fields_read_by_b190"] == ["no_gold_gate"],
        "a6_reach_ratio_one_quarter": A["reach_ratio"] == "1/4",
        "a7_unreachable_fields_three": sorted(A["unreachable_fields"]) ==
            sorted(["cross_scope", "first_rhs", "allow_unstable"]),
        "a8_extractor_positive_4of4": A["extractor_positive_hit"] == 4,
        "a9_extractor_negative_zero": A["extractor_negative_hit"] == 0,
        "b1_main_rows_135": B["n_rows"] == 135,
        "b2_required_keys_from_source": len(Bk) >= 3 and "test_src" in Bk,
        "b3_no_row_has_all_inputs": B["n_rows_with_all_inputs"] == 0,
        "b4_no_row_has_any_input": B["n_rows_with_any_input"] == 0,
        "b5_per_key_all_zero": all(v == 0 for v in B["per_key_present"].values()),
        "b6_by_k_closed": B["by_k_sum"] == 135,
        "b7_by_verdict_closed": B["by_verdict_sum"] == 135,
        "b8_189_cases_six": B["cases_189"]["n_cases"] == 6,
        "b9_189_cases_all_have_src": B["cases_189"]["n_with_src"] == 6,
        "b10_archive_ids_only": B["cases_189"]["archive_has_src"] == 0
            and B["cases_189"]["n_archived_ids"] == 6,
        "c1_rows_135": C["n_rows"] == 135,
        "c2_all_ledger_changed": C["n_ledger_changed"] == 135,
        "c3_no_verified_flip": C["n_verified_flip"] == 0,
        "c4_no_counts_disc_flip": C["n_counts_disc_flip"] == 0,
        "c5_groups_closed": C["group_sum"] == 135,
        "c6_same_as_190_rowwise": C["same_row_with_190"] == 135
            and C["n_190_rows"] == 135,
        "c7_flip_detector_has_teeth": C["flip_detector_teeth"] >= 1
            and C["flip_detector_total"] == 4,
        "d1_default_1111": D["policy_default"] == "1111",
        "d2_strict_0000": D["policy_strict"] == "0000",
        "d3_superseded_all_match": D["superseded_sha_matches"] == D["superseded_total"] == 3,
        "d4_superseded_hash_current": D["superseded_new_sha_on_disk"] ==
            D["superseded_new_sha_registered"],
        "d5_denominators_separate": D["denominators"] ==
            {"main_135": 135, "form_119": 119, "no_result_94": 94}
            and D["denominators_kept_separate"],
        "d5b_denominator_dual_source": (
            D["denominator_dual_source"]["form_rows"]
            == D["denominator_dual_source"]["form_summary"] == 119
            and D["denominator_dual_source"]["no_result_rows"]
            == D["denominator_dual_source"]["no_result_summary"] == 94),
        "d6_no_sandbox_primitives": D["sandbox_selfcheck"]["n_banned"] == 0
            and D["n_forbidden_runtime_calls"] == 0,
        "d7_zero_forbidden_runs": all(D[k] == 0 for k in
            ("real_expr_runs", "real_commit_runs", "f2p_runs", "gain_reported")),
        "d8_anchor_ok": ((anchor_rep.get("skipped") is True and args.no_anchor)
                         or (bool(anchor_rep.get("ok"))
                             and not anchor_rep.get("diffs")
                             and not anchor_rep.get("caliber_diffs"))),
        "d9_selfcheck_own_source": D["sandbox_selfcheck"]["checked_own_source"],
    }

    derived = {
        "reach_ratio": A["reach_ratio"],
        "unreachable_fields": A["unreachable_fields"],
        "n_rows_input_unavailable": B["n_rows"] - B["n_rows_with_all_inputs"],
        "n_rows_input_available": B["n_rows_with_all_inputs"],
        "c_verified_flip": C["n_verified_flip"],
        "c_counts_disc_flip": C["n_counts_disc_flip"],
        "n_closure": len(closure),
        "n_closure_true": sum(bool(v) for v in closure.values()),
    }
    doc = {
        "schema": "policy_reach_census/v1",
        "caliber": caliber(),
        "accounts": {"A_reach": A, "B_input_availability": B,
                     "C_rowwise_outcome": C, "D_boundary": D},
        "derived": derived,
        "closure": closure,
        "anchor": anchor_rep,
        "summary": {
            "verdict": ("190 的两口径只切了 RelaxPolicy 4 位中的 1 位（no_gold_gate）；"
                        "另 3 位在 177 主账 135 行上所需输入 0/135 可得 ⇒ "
                        "189 的 switch_teeth 4/4 只在 6 个合成 case 上成立，"
                        "不能读成『④ 在真实主账上没有新采纳』"),
            "scope": "尺子代表性边界，不是产品洞；不推翻 190、不改 189 语义、不报收益",
            "n_closure_true": derived["n_closure_true"],
            "n_closure": derived["n_closure"],
        },
    }
    out = Path(args.out) if args.out else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("wrote", out)
    print("reach", derived["reach_ratio"], "closure",
          "%d/%d" % (derived["n_closure_true"], derived["n_closure"]))
    print("rows input available", derived["n_rows_input_available"], "/", B["n_rows"])
    return 0 if derived["n_closure_true"] == derived["n_closure"] else 1


if __name__ == "__main__":
    sys.exit(main())
